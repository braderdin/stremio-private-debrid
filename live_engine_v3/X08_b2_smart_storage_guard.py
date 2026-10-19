#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - SMART B2 STORAGE PRE-FLIGHT GUARD (X08)
# FAIL  : X08_b2_smart_storage_guard.py
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X08_b2_smart_storage_guard.py
#
# KELEBIHAN & CIRI PINTAR (X08):
# 1. Zero False-Positive: Menghapuskan ralat 0.0 GB akibat timeout atau sambungan terputus.
# 2. Double-Check Fallback: Akaun yang gagal pada pusingan pertama akan disemak semula
#    secara automatik dengan tempoh masa tamat (timeout) yang lebih panjang.
# 3. Pengesahan Integriti Keras (Hard Validation): Akaun yang gagal tidak akan sekali-kali
#    dilabelkan sebagai "SIAP SEDIA" atau dipilih untuk muat naik.
# 4. Pelupusan Berperingkat Global LRU: Mengosongkan fail tertua sehingga baki ruang
#    sebenar mencukupi (>= 5.0 GB) sebelum muat turun dimulakan.
# ==============================================================================

import os
import sys
import json
import time
import argparse
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# ------------------------------------------------------------------------------
# 1. KONFIGURASI LALUAN SISTEM & IMPORT MODUL ASAL
# ------------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
V2_DIR = PROJECT_ROOT / "live_engine_v2"

for p in [CURRENT_DIR, PROJECT_ROOT, LIVE_ENGINE_DIR, V2_DIR]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _config = importlib.import_module("00_config")
    _redis_mod = importlib.import_module("X01_series_redis")
    _b2_mod = importlib.import_module("X02_series_b2storage")

    db = getattr(_redis_mod, "series_db")
    storage = getattr(_b2_mod, "series_storage")
except Exception as e:
    console.print(f"[bold red]❌ Ralat mengimport modul asas sistem: {e}[/bold red]")
    sys.exit(1)


# ------------------------------------------------------------------------------
# 2. PENGESANAN AKAUN B2 DINAMIK (SEHINGGA 50 AKAUN)
# ------------------------------------------------------------------------------
def load_dynamic_b2_accounts() -> List[Dict[str, Any]]:
    """
    Mengesan 50 akaun B2 secara dinamik daripada:
    1. GitHub Actions Secret (B2_ACCOUNTS_JSON)
    2. Fail persekitaran tempatan (.env.local)
    3. Konfigurasi lalai 00_config.py
    """
    raw_json = os.environ.get("B2_ACCOUNTS_JSON", "").strip() or os.environ.get("B2_MULTI_ACCOUNT_JSON", "").strip()

    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list) and parsed:
                parsed.sort(key=lambda x: x.get("index", 0))
                return parsed
        except Exception:
            pass

    # Cuba baca daripada fungsi 00_config jika ada
    if hasattr(_config, "get_b2_accounts"):
        cfg_accs = _config.get_b2_accounts()
        if cfg_accs:
            return cfg_accs

    return getattr(storage, "accounts", []) or getattr(_config, "B2_ACCOUNTS", [])


# ------------------------------------------------------------------------------
# 3. ENJIN IMBASAN B2 TEGAP DENGAN SOKONGAN DOUBLE-CHECK & TIMEOUT DINAMIK
# ------------------------------------------------------------------------------
def inspect_single_account_robust(acc: Dict[str, Any], timeout_sec: float = 25.0) -> Dict[str, Any]:
    """
    Menjalankan pengesahan terus ke API Backblaze B2.
    Jika gagal atau timeout, ia TIDAK AKAN memulangkan nilai 0 bait secara senyap.
    """
    idx = acc["index"]
    b_name = acc.get("bucket_name", f"bucket-{idx}")
    key_id = acc.get("key_id", "")
    app_key = acc.get("app_key", "")
    bucket_id = acc.get("bucket_id", "")

    result: Dict[str, Any] = {
        "index": idx,
        "bucket_name": b_name,
        "files_count": 0,
        "used_bytes": 0,
        "bucket_id": bucket_id,
        "success": False,
        "attempts": 1,
        "latency_ms": 0.0,
        "error": ""
    }

    if not key_id or not app_key:
        result["error"] = "Kredensial B2 tidak lengkap"
        return result

    start_t = time.perf_counter()
    timeout = httpx.Timeout(timeout_sec, connect=10.0)

    try:
        with httpx.Client(timeout=timeout) as client:
            # 1. Dapatkan Token Sesi B2
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

            # 2. Cari Bucket ID jika tiada
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
                result["error"] = "Bucket ID tidak ditemui di portal B2"
                return result

            result["bucket_id"] = bucket_id

            # 3. Kira fail & saiz sebenar
            file_count = 0
            used_bytes = 0
            next_file_name = None

            while True:
                payload: Dict[str, Any] = {"bucketId": bucket_id, "maxFileCount": 1000}
                if next_file_name:
                    payload["startFileName"] = next_file_name

                list_resp = client.post(
                    f"{api_url}/b2api/v2/b2_list_file_names",
                    headers={"Authorization": auth_token},
                    json=payload
                )

                if list_resp.status_code != 200:
                    result["error"] = f"List fail ralat: HTTP {list_resp.status_code}"
                    return result

                list_data = list_resp.json()
                files = list_data.get("files", [])
                for f in files:
                    file_count += 1
                    used_bytes += f.get("contentLength", 0)

                next_file_name = list_data.get("nextFileName")
                if not next_file_name or len(files) == 0:
                    break

            result["files_count"] = file_count
            result["used_bytes"] = used_bytes
            result["success"] = True

    except httpx.TimeoutException:
        result["error"] = "Timeout (API B2 lambat bertindak balas)"
    except Exception as e:
        result["error"] = str(e)
    finally:
        result["latency_ms"] = (time.perf_counter() - start_t) * 1000

    return result


def scan_all_accounts_smart(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Imbasan dua fasa:
    - Fasa 1: Imbasan selari (Multi-threaded) dengan timeout 25s.
    - Fasa 2 (Fallback Double-Check): Imbas semula akaun yang gagal/timeout dengan timeout 40s.
    """
    results: Dict[int, Dict[str, Any]] = {}

    # FASA 1: IMBASAN SELARI
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_acc = {executor.submit(inspect_single_account_robust, acc, 25.0): acc for acc in accounts}
        for future in as_completed(future_to_acc):
            res = future.result()
            results[res["index"]] = res

    # FASA 2: DOUBLE-CHECK UNTUK AKAUN YANG MENGALAMI RALAT / TIMEOUT
    failed_indices = [idx for idx, r in results.items() if not r["success"]]

    if failed_indices:
        console.print(f"[bold yellow]⚠️  Dikesan {len(failed_indices)} akaun mengalami ralat latensi. Melaksanakan pengesahan semula (Double-Check Pass)...[/bold yellow]")
        for idx in failed_indices:
            acc = next((a for a in accounts if a["index"] == idx), None)
            if acc:
                retry_res = inspect_single_account_robust(acc, timeout_sec=40.0)
                retry_res["attempts"] = 2
                if retry_res["success"]:
                    console.print(f"   └ [green]✔ Akaun #{idx:03d} berjaya disahkan selepas percubaan kedua.[/green]")
                    results[idx] = retry_res
                else:
                    console.print(f"   └ [red]✖ Akaun #{idx:03d} kekal gagal: {retry_res['error']}[/red]")
                    results[idx] = retry_res

    sorted_results = [results[acc["index"]] for acc in accounts if acc["index"] in results]
    return sorted_results


# ------------------------------------------------------------------------------
# 4. PELUPUSAN PINTAR BERASASKAN LRU (ZERO ORPHAN)
# ------------------------------------------------------------------------------
def safe_evict_oldest_stream(target_meta: Dict[str, Any]) -> bool:
    """Memadamkan fail fizikal tertua di B2 dan membersihkan metadata di Redis."""
    imdb_id = target_meta.get("imdb_id")
    target_hash = (target_meta.get("info_hash") or "").strip().lower()
    acc_idx = target_meta.get("b2_account_index")
    f_path = target_meta.get("file_path", "").lstrip("/")
    title = target_meta.get("title", imdb_id)

    if not acc_idx or not f_path:
        if imdb_id:
            db.delete_stream_metadata(imdb_id, info_hash=target_hash)
        return False

    console.print(f"[yellow]🗑️  Memulakan pelupusan fail fizikal tertua:[/yellow] [bold white]{title}[/bold white]")
    console.print(f"   └ Sasaran B2 : [cyan]#{acc_idx}[/cyan] ({f_path})")

    b2_deleted = storage.delete_file_from_b2(acc_idx, f_path)
    db.delete_stream_metadata(imdb_id, info_hash=target_hash)

    if b2_deleted:
        console.print("   └ [green]✔ Fail fizikal B2 berjaya dipadam & rekod Redis diselaraskan.[/green]")
    else:
        console.print("   └ [dim yellow]ℹ Fail tiada di B2 fizikal (rekod hantu dibersihkan dari Redis).[/dim yellow]")

    time.sleep(3)
    return True


# ------------------------------------------------------------------------------
# 5. ALUR KERJA UTAMA PENGAWAL STORAN (X08 SMART GUARD)
# ------------------------------------------------------------------------------
def run_smart_storage_guard(
    required_free_gb: float = 5.0,
    safe_cap_gb: float = 9.0,
    max_evictions: int = 15
) -> bool:
    safe_cap_bytes = int(safe_cap_gb * 1024**3)
    required_free_bytes = int(required_free_gb * 1024**3)

    accounts = load_dynamic_b2_accounts()
    if not accounts:
        console.print("[bold red]❌ Tiada akaun B2 dikesan dalam persekitaran (B2_ACCOUNTS_JSON kosong)![/bold red]")
        return False

    storage.accounts = accounts
    total_acc = len(accounts)

    console.print(Panel.fit(
        f"[bold cyan]🛡️  STREMIO V3: SMART B2 STORAGE PRE-FLIGHT GUARD (X08)[/bold cyan]\n"
        f"Jumlah Akaun Disemak   : [bold green]{total_acc} Akaun B2[/bold green]\n"
        f"Had Siling Selamat     : [bold yellow]{safe_cap_gb:.1f} GB[/bold yellow] per akaun\n"
        f"Ruang Bebas Diperlukan : [bold magenta]>= {required_free_gb:.1f} GB[/bold magenta] (Bebas Ralat 0.0GB)",
        border_style="cyan"
    ))

    console.print("[dim]🔍 Menjalankan imbasan kesihatan & kapasiti 50 akaun B2...[/dim]")
    usage_list = scan_all_accounts_smart(accounts)

    def find_verified_ready_account(u_list):
        for u in u_list:
            # SYARAT KETAT: Mesti berjaya disahkan dan mempunyai bucket_id sah
            if not u.get("success") or not u.get("bucket_id"):
                continue

            used = u["used_bytes"]
            free = safe_cap_bytes - used
            if free >= required_free_bytes:
                return u
        return None

    ready_acc = find_verified_ready_account(usage_list)

    # KITARAN PELUPUSAN LRU JIKA TIADA RUANG MENCUKUPI
    eviction_count = 0
    while not ready_acc and eviction_count < max_evictions:
        console.print(f"[bold yellow]⚠️  Tiada akaun dengan ruang sah >= {required_free_gb:.1f} GB! Memulakan pelupusan LRU (#{eviction_count + 1})...[/bold yellow]")

        oldest = db.get_oldest_stream_across_shards()
        if not oldest:
            console.print("[bold red]❌ Tiada fail lama dikesan dalam garis masa Redis untuk dilupuskan![/bold red]")
            break

        safe_evict_oldest_stream(oldest)
        eviction_count += 1

        # Imbas semula akaun untuk mengemas kini kapasiti terkini
        usage_list = scan_all_accounts_smart(accounts)
        ready_acc = find_verified_ready_account(usage_list)

    # PAPARAN JADUAL STATUS PASCA-PENGAWAL
    table = Table(
        title=f"📊 Status Kapasiti Storan B2 Pasca-Pengawal Pintar ({total_acc} Akaun Diperiksa)",
        border_style="green" if ready_acc else "red"
    )
    table.add_column("Akaun", justify="center", style="cyan", width=8)
    table.add_column("Nama Baldi", style="white", width=25)
    table.add_column("Fail", justify="center", style="yellow", width=8)
    table.add_column("Digunakan", justify="right", style="white", width=11)
    table.add_column(f"Baki (Had {safe_cap_gb:.0f}GB)", justify="right", style="green", width=14)
    table.add_column("Latensi", justify="right", style="dim", width=10)
    table.add_column("Status Pengawal", justify="center", width=22)

    for u in usage_list:
        idx_str = f"#{u['index']:03d}"
        b_name = u["bucket_name"]

        if not u.get("success") or not u.get("bucket_id"):
            files_str = "N/A"
            used_str = "RALAT"
            free_str = "0.00 GB"
            lat_str = f"{u['latency_ms']:.0f} ms"
            status_tag = "[bold red]RALAT RANGKAIAN ⚠️[/bold red]"
        else:
            files_str = f"{u['files_count']} fail"
            used_gb = u["used_bytes"] / (1024**3)
            free_gb = max(0.0, (safe_cap_bytes - u["used_bytes"]) / (1024**3))
            used_str = f"{used_gb:.2f} GB"
            free_str = f"{free_gb:.2f} GB"
            lat_str = f"{u['latency_ms']:.0f} ms"

            if free_gb >= required_free_gb:
                status_tag = "[bold green]SIAP SEDIA ★[/bold green]"
            elif used_gb >= safe_cap_gb:
                status_tag = f"[bold red]PENUH (>{safe_cap_gb:.0f}GB)[/bold red]"
            else:
                status_tag = "[yellow]AMARAN RUANG[/yellow]"

        table.add_row(
            idx_str,
            b_name,
            files_str,
            used_str,
            free_str,
            lat_str,
            status_tag
        )

    console.print(table)

    if ready_acc:
        target_free = (safe_cap_bytes - ready_acc["used_bytes"]) / (1024**3)
        console.print(Panel(
            f"[bold green]✅ PENGESAHAN BERJAYA: RUANG STORAN B2 DISAHKAN WUJUD SECARA FIZIKAL![/bold green]\n"
            f"Akaun Sasaran: [bold cyan]#{ready_acc['index']} ({ready_acc['bucket_name']})[/bold cyan]\n"
            f"Baki Sah     : [bold white]{target_free:.2f} GB[/bold white] (Melebihi syarat minimum {required_free_gb:.1f} GB)\n"
            f"Tugasan muat turun aria2c & pemindahan B2 sedia untuk dimulakan.",
            border_style="green"
        ))
        return True
    else:
        console.print(Panel(
            f"[bold red]❌ PENGESAHAN GAGAL: TIADA RUANG BEBAS DISAHKAN MENCUKUPI PADA 50 AKAUN B2![/bold red]\n"
            f"Semua akaun mungkin penuh atau mengalami ralat sambungan berpanjangan.",
            border_style="red"
        ))
        return False


def main():
    parser = argparse.ArgumentParser(description="V3 Smart B2 Storage Guard & Evictor (X08)")
    parser.add_argument("--min-free-gb", type=float, default=5.0, help="Ruang bebas minimum diperlukan (Lalai: 5.0 GB)")
    parser.add_argument("--safe-cap-gb", type=float, default=9.0, help="Had siling selamat per akaun (Lalai: 9.0 GB)")
    parser.add_argument("--max-evict", type=int, default=15, help="Had maksimum fail dipadam dalam satu sesi (Lalai: 15)")
    args = parser.parse_args()

    success = run_smart_storage_guard(
        required_free_gb=args.min_free_gb,
        safe_cap_gb=args.safe_cap_gb,
        max_evictions=args.max_evict
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()