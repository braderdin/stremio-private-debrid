#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - RECONCILIATION & ORPHAN CLEANER (TEST ENGINE)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/X13_test_orphan_reconcile.py
# ==============================================================================

import os
import re
import sys
import json
import time
import argparse
import importlib
from pathlib import Path
from typing import Dict, Any, List, Set, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
LIVE_ENGINE_V3 = CURRENT_DIR.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
V2_DIR = PROJECT_ROOT / "live_engine_v2"

for p in [CURRENT_DIR, LIVE_ENGINE_V3, PROJECT_ROOT, LIVE_ENGINE_DIR, V2_DIR]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _config = importlib.import_module("00_config")
    _redis_mod = importlib.import_module("X01_series_redis")
    _b2_mod = importlib.import_module("X02_series_b2storage")

    db = getattr(_redis_mod, "series_db")
    storage = getattr(_b2_mod, "series_storage")
except Exception as e:
    console.print(f"[bold red]❌ Ralat import modul asas: {e}[/bold red]")
    sys.exit(1)


def load_dynamic_b2_accounts() -> List[Dict[str, Any]]:
    raw_json = os.environ.get("B2_ACCOUNTS_JSON", "").strip() or os.environ.get("B2_MULTI_ACCOUNT_JSON", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list) and parsed:
                parsed.sort(key=lambda x: x.get("index", 0))
                return parsed
        except Exception:
            pass

    if hasattr(_config, "get_b2_accounts"):
        cfg_accs = _config.get_b2_accounts()
        if cfg_accs:
            return cfg_accs

    return getattr(storage, "accounts", []) or getattr(_config, "B2_ACCOUNTS", [])


def fetch_b2_bucket_inventory(acc: Dict[str, Any], timeout_sec: float = 30.0) -> Dict[str, Any]:
    idx = acc["index"]
    b_name = acc.get("bucket_name", f"bucket-{idx}")
    key_id = acc.get("key_id", "")
    app_key = acc.get("app_key", "")
    bucket_id = acc.get("bucket_id", "")

    result = {
        "index": idx,
        "bucket_name": b_name,
        "bucket_id": bucket_id,
        "files": {},
        "api_url": "",
        "auth_token": "",
        "success": False,
        "error": ""
    }

    if not key_id or not app_key:
        result["error"] = "Kredensial tidak lengkap"
        return result

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_sec, connect=10.0)) as client:
            auth_resp = client.get(
                "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
                auth=(key_id, app_key)
            )
            if auth_resp.status_code != 200:
                result["error"] = f"Auth Gagal: HTTP {auth_resp.status_code}"
                return result

            auth_data = auth_resp.json()
            api_url = auth_data.get("apiUrl")
            auth_token = auth_data.get("authorizationToken")
            account_id = auth_data.get("accountId")

            result["api_url"] = api_url
            result["auth_token"] = auth_token

            if not bucket_id:
                b_resp = client.post(
                    f"{api_url}/b2api/v2/b2_list_buckets",
                    headers={"Authorization": auth_token},
                    json={"accountId": account_id, "bucketName": b_name}
                )
                if b_resp.status_code == 200:
                    for b in b_resp.json().get("buckets", []):
                        if b.get("bucketName") == b_name:
                            bucket_id = b.get("bucketId")
                            break

            if not bucket_id:
                result["error"] = "Bucket ID tidak ditemui"
                return result

            result["bucket_id"] = bucket_id

            files_map = {}
            next_file_name = None

            while True:
                payload = {"bucketId": bucket_id, "maxFileCount": 1000}
                if next_file_name:
                    payload["startFileName"] = next_file_name

                list_resp = client.post(
                    f"{api_url}/b2api/v2/b2_list_file_names",
                    headers={"Authorization": auth_token},
                    json=payload
                )
                if list_resp.status_code != 200:
                    result["error"] = f"List Ralat: HTTP {list_resp.status_code}"
                    return result

                list_data = list_resp.json()
                items = list_data.get("files", [])
                for f in items:
                    f_name = f.get("fileName", "").lstrip("/")
                    files_map[f_name] = {
                        "fileId": f.get("fileId"),
                        "size": f.get("contentLength", 0),
                        "uploadTimestamp": f.get("uploadTimestamp", 0)
                    }

                next_file_name = list_data.get("nextFileName")
                if not next_file_name or len(items) == 0:
                    break

            result["files"] = files_map
            result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


def scan_all_b2(accounts: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]]]:
    b2_account_info = {}
    b2_files_registry = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {executor.submit(fetch_b2_bucket_inventory, acc, 30.0): acc for acc in accounts}
        for fut in as_completed(future_map):
            res = fut.result()
            b_name = res["bucket_name"]
            b2_account_info[b_name] = res
            if res["success"]:
                for rel_path, meta in res["files"].items():
                    b2_files_registry[(b_name, rel_path)] = {
                        **meta,
                        "account_index": res["index"],
                        "bucket_name": b_name
                    }

    return b2_account_info, b2_files_registry


def scan_all_redis() -> Dict[str, Dict[str, Any]]:
    redis_keys_registry = {}

    for shard in db.accounts:
        try:
            keys_raw = db.execute_command(shard, "KEYS", "stremio:*")
            if not keys_raw:
                continue

            keys_list = json.loads(keys_raw) if isinstance(keys_raw, str) and keys_raw.startswith("[") else keys_raw
            if not isinstance(keys_list, list):
                continue

            for k in keys_list:
                # Abaikan kunci carian sementara
                if k.startswith("stremio:list:") or k.startswith("stremio:http:list:"):
                    continue
                # Hanya proses metadata video yang siap ditonton
                if not (k.startswith("stremio:tt") or k.startswith("stremio:http:tt")):
                    continue

                raw_val = db.execute_command(shard, "GET", k)
                if not raw_val:
                    continue

                val = json.loads(raw_val) if isinstance(raw_val, str) else raw_val
                if isinstance(val, dict):
                    redis_keys_registry[k] = {
                        "shard": shard,
                        "data": val
                    }
        except Exception as e:
            console.print(f"[dim red]Ralat imbas shard Redis {shard.get('index')}: {e}[/dim red]")

    return redis_keys_registry


def run_orphan_reconciliation_test(dry_run: bool = True):
    console.print(Panel.fit(
        f"[bold cyan]🔍 UJIAN PENYELARASAN & PENGESANAN FAIL ORPHAN / YATIM (X13 TEST)[/bold cyan]\n"
        f"Mod Operasi : {'[bold yellow]DRY-RUN (Semak Sahaja - Tiada Fail Dipadam)[/bold yellow]' if dry_run else '[bold red]FIX (Padam & Kemaskini Aktif)[/bold red]'}\n"
        f"Liputan     : 4 Shard Upstash Redis vs 50 Baldi Backblaze B2",
        border_style="cyan"
    ))

    accounts = load_dynamic_b2_accounts()
    if not accounts:
        console.print("[bold red]❌ Tiada akaun B2 dikesan![/bold red]")
        return

    console.print("[cyan]⏳ Langkah 1/3: Mengimbas semua fail fizikal di 50 baldi Backblaze B2...[/cyan]")
    b2_acc_info, b2_files = scan_all_b2(accounts)
    console.print(f"   └ [green]✔ Ditemui {len(b2_files)} fail fizikal di seluruh B2.[/green]")

    console.print("[cyan]⏳ Langkah 2/3: Mengimbas semua metadata penstriman di 4 Shard Upstash Redis...[/cyan]")
    redis_registry = scan_all_redis()
    console.print(f"   └ [green]✔ Ditemui {len(redis_registry)} kunci video aktif di Redis.[/green]")

    console.print("[cyan]⏳ Langkah 3/3: Membuat perbandingan silang (Cross-Referencing)...[/cyan]")

    redis_ghosts = []
    active_redis_b2_pairs = set()

    # Semak Kes 1: Ada di Redis tapi Tiada di B2
    for k, item in redis_registry.items():
        shard = item["shard"]
        data = item["data"]

        streams = data.get("streams", [])
        if not streams and data.get("file_path"):
            streams = [data]

        for s in streams:
            b_name = s.get("b2_bucket", "")
            f_path = s.get("file_path", "").lstrip("/")

            if not b_name or not f_path:
                continue

            active_redis_b2_pairs.add((b_name, f_path))

            if (b_name, f_path) not in b2_files:
                redis_ghosts.append({
                    "key": k,
                    "shard": shard,
                    "title": s.get("title") or data.get("title", k),
                    "bucket": b_name,
                    "file_path": f_path,
                    "info_hash": s.get("info_hash", ""),
                    "size_bytes": s.get("size_bytes", 0)
                })

    # Semak Kes 2: Ada di B2 tapi Tiada di Redis
    b2_zombies = []
    for (b_name, f_path), f_meta in b2_files.items():
        if (b_name, f_path) not in active_redis_b2_pairs:
            b2_zombies.append({
                "bucket": b_name,
                "file_path": f_path,
                "size_bytes": f_meta["size"],
                "fileId": f_meta["fileId"],
                "account_index": f_meta["account_index"]
            })

    # TAMPILKAN JADUAL 1: REDIS GHOSTS
    if redis_ghosts:
        t_ghost = Table(
            title=f"👻 KES 1: REDIS GHOST / YATIM ({len(redis_ghosts)} Rekod Ada di Redis tapi Fail Tiada di B2)",
            border_style="red"
        )
        t_ghost.add_column("No", justify="center", style="cyan", width=4)
        t_ghost.add_column("Kunci Redis", style="yellow", width=28)
        t_ghost.add_column("Tajuk / Hash", style="white", width=30)
        t_ghost.add_column("Baldi", style="dim", width=24)
        t_ghost.add_column("Fail Hilang", style="red")

        for idx, g in enumerate(redis_ghosts, 1):
            t_ghost.add_row(
                str(idx),
                g["key"],
                f"{g['title'][:25]} [{g['info_hash'][:8]}]",
                g["bucket"],
                Path(g["file_path"]).name
            )
        console.print(t_ghost)
    else:
        console.print("[bold green]✅ TIADA REDIS GHOST: Semua kunci metadata di Redis mempunyai fail fizikal yang sah di B2![/bold green]")

    # TAMPILKAN JADUAL 2: B2 ZOMBIES
    if b2_zombies:
        t_zombie = Table(
            title=f"🧟 KES 2: B2 ZOMBIE ({len(b2_zombies)} Fail Wujud di B2 tapi Tiada Rekod di Redis)",
            border_style="yellow"
        )
        t_zombie.add_column("No", justify="center", style="cyan", width=4)
        t_zombie.add_column("Akaun", justify="center", style="cyan", width=8)
        t_zombie.add_column("Baldi B2", style="white", width=25)
        t_zombie.add_column("Saiz", justify="right", style="magenta", width=12)
        t_zombie.add_column("Nama Fail Zombie", style="yellow")

        total_zombie_size = sum(z["size_bytes"] for z in b2_zombies)
        for idx, z in enumerate(b2_zombies, 1):
            sz_gb = z["size_bytes"] / (1024**3)
            sz_str = f"{sz_gb:.2f} GB" if sz_gb >= 1.0 else f"{z['size_bytes'] / (1024**2):.1f} MB"
            t_zombie.add_row(
                str(idx),
                f"#{z['account_index']:03d}",
                z["bucket"],
                sz_str,
                Path(z["file_path"]).name
            )
        console.print(t_zombie)
        console.print(f"[bold yellow]💾 Jumlah Ruang Dibazirkan oleh B2 Zombie:[/bold yellow] [bold red]{total_zombie_size / (1024**3):.2f} GB[/bold red]")
    else:
        console.print("[bold green]✅ TIADA B2 ZOMBIE: Semua fail fizikal di 50 baldi B2 dipetakan dengan tepat ke Redis![/bold green]")

    # TINDAKAN JIKA BUKAN DRY-RUN (--fix)
    if not dry_run:
        console.print("\n[bold red]⚡ MELAKSANAKAN PEMBERSIHAN AKTIF (--fix)...[/bold red]")
        
        # 1. Bersihkan Redis Ghosts
        fixed_ghosts = 0
        for g in redis_ghosts:
            k = g["key"]
            shard = g["shard"]
            raw_v = db.execute_command(shard, "GET", k)
            if not raw_v:
                continue
            v_data = json.loads(raw_v) if isinstance(raw_v, str) else raw_v
            if isinstance(v_data, dict):
                st_list = v_data.get("streams", [])
                if st_list:
                    new_streams = [s for s in st_list if s.get("file_path", "").lstrip("/") != g["file_path"]]
                    if len(new_streams) == 0:
                        db.execute_command(shard, "DEL", k)
                    else:
                        v_data["streams"] = new_streams
                        first = new_streams[0]
                        v_data["file_path"] = first.get("file_path")
                        v_data["stream_url"] = first.get("stream_url")
                        v_data["b2_bucket"] = first.get("b2_bucket")
                        v_data["info_hash"] = first.get("info_hash")
                        db.execute_command(shard, "SET", k, json.dumps(v_data, ensure_ascii=False))
                else:
                    db.execute_command(shard, "DEL", k)
                fixed_ghosts += 1
        console.print(f"[bold green]✔ Selesai membersihkan {fixed_ghosts} rekod hantu di Redis.[/bold green]")

        # 2. Bersihkan B2 Zombies
        fixed_zombies = 0
        for z in b2_zombies:
            b_name = z["bucket"]
            b_info = b2_acc_info.get(b_name, {})
            api_url = b_info.get("api_url")
            auth_token = b_info.get("auth_token")
            file_id = z.get("fileId")
            file_name = z.get("file_path")
            if api_url and auth_token and file_id:
                try:
                    with httpx.Client(timeout=20.0) as client:
                        del_resp = client.post(
                            f"{api_url}/b2api/v2/b2_delete_file_version",
                            headers={"Authorization": auth_token},
                            json={"fileName": file_name, "fileId": file_id}
                        )
                        if del_resp.status_code == 200:
                            fixed_zombies += 1
                except Exception:
                    pass
        console.print(f"[bold green]✔ Selesai memadam {fixed_zombies} fail zombie fizikal di B2.[/bold green]")


def main():
    parser = argparse.ArgumentParser(description="Stremio V3 Redis & B2 Orphan Reconciler Test")
    parser.add_argument("--fix", action="store_true", help="Laksanakan pembersihan sebenar (Lalai: Dry-run)")
    args = parser.parse_args()

    run_orphan_reconciliation_test(dry_run=not args.fix)


if __name__ == "__main__":
    main()