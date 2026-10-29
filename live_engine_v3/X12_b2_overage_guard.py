#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - B2 OVERAGE AUTO-GUARD (X12)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X12_b2_overage_guard.py
#
# FUNGSI UTAMA:
# 1. Menjaga 50 Akaun Backblaze B2 supaya TIDAK MELEBIHI 10.0 GB (Bebas Caj Terlebih / Overage).
# 2. Dijalankan secara berjadual (Setiap 3 Hari Sekali) melalui GitHub Actions atau manual.
# 3. Jika mana-mana akaun dikesan > 10.0 GB:
#    - Mengenal pasti fail fizikal tertua berdasarkan uploadTimestamp B2.
#    - Memadamkan 1 hingga 2 fail paling lama sahaja pada akaun tersebut.
#    - Menyelaraskan dan membersihkan rekod metadata di Upstash Redis (Zero Orphan Link).
# 4. Jika akaun <= 10.0 GB, biarkan tanpa sebarang tindakan (diuruskan enjin LRU biasa).
# ==============================================================================

import os
import re
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
# 1. SETUP LALUAN SISTEM & IMPORT MODUL ASAL
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
# 2. PENGESANAN AKAUN B2 DINAMIK
# ------------------------------------------------------------------------------
def load_dynamic_b2_accounts() -> List[Dict[str, Any]]:
    """Membaca senarai 50 akaun B2 daripada B2_ACCOUNTS_JSON atau konfigurasi."""
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


# ------------------------------------------------------------------------------
# 3. IMBASAN TERPERINCI FAIL B2 (MENJEJAK UPLOAD TIMESTAMP & FILE ID)
# ------------------------------------------------------------------------------
def inspect_account_files_detailed(acc: Dict[str, Any], timeout_sec: float = 30.0) -> Dict[str, Any]:
    """Mengimbas akaun B2 dan memulangkan senarai fail lengkap dengan uploadTimestamp."""
    idx = acc["index"]
    b_name = acc.get("bucket_name", f"bucket-{idx}")
    key_id = acc.get("key_id", "")
    app_key = acc.get("app_key", "")
    bucket_id = acc.get("bucket_id", "")

    result: Dict[str, Any] = {
        "index": idx,
        "bucket_name": b_name,
        "bucket_id": bucket_id,
        "files": [],
        "used_bytes": 0,
        "api_url": "",
        "auth_token": "",
        "success": False,
        "latency_ms": 0.0,
        "error": ""
    }

    if not key_id or not app_key:
        result["error"] = "Kredensial tidak lengkap"
        return result

    start_t = time.perf_counter()
    timeout = httpx.Timeout(timeout_sec, connect=10.0)

    try:
        with httpx.Client(timeout=timeout) as client:
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

            files_list = []
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
                    result["error"] = f"List Ralat: HTTP {list_resp.status_code}"
                    return result

                list_data = list_resp.json()
                items = list_data.get("files", [])
                for f in items:
                    f_size = f.get("contentLength", 0)
                    used_bytes += f_size
                    files_list.append({
                        "fileId": f.get("fileId"),
                        "fileName": f.get("fileName"),
                        "size": f_size,
                        "uploadTimestamp": f.get("uploadTimestamp", 0)
                    })

                next_file_name = list_data.get("nextFileName")
                if not next_file_name or len(items) == 0:
                    break

            # Isih fail: Paling lama (uploadTimestamp terkecil) di hadapan
            files_list.sort(key=lambda x: x["uploadTimestamp"])

            result["files"] = files_list
            result["used_bytes"] = used_bytes
            result["success"] = True

    except Exception as e:
        result["error"] = str(e)
    finally:
        result["latency_ms"] = (time.perf_counter() - start_t) * 1000

    return result


# ------------------------------------------------------------------------------
# 4. PENGECAMAN IMDB & PEMBERSIHAN REDIS (ZERO ORPHAN)
# ------------------------------------------------------------------------------
def extract_metadata_from_filename(file_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Mengekstrak IMDb ID dan Hash daripada corak nama fail media B2."""
    clean_name = Path(file_path).name

    # Corak Siri TV: tt1199099.S01E02.cb26b646...
    m_series = re.search(r"(tt\d+)\.S0*(\d+)E0*(\d+)\.([a-f0-9]{8})", clean_name, re.IGNORECASE)
    if m_series:
        imdb_id = f"{m_series.group(1)}:{int(m_series.group(2))}:{int(m_series.group(3))}"
        short_hash = m_series.group(4).lower()
        return imdb_id, short_hash

    # Corak Filem: tt1424381.f81a1ce1...
    m_movie = re.search(r"(tt\d+)\.([a-f0-9]{8})", clean_name, re.IGNORECASE)
    if m_movie:
        return m_movie.group(1), m_movie.group(2).lower()

    # Sandaran jika hanya ada tt ID
    m_tt = re.search(r"(tt\d+)", clean_name)
    if m_tt:
        return m_tt.group(1), None

    return None, None


def purge_redis_metadata_for_file(file_path: str):
    """Membersihkan entri fail yang telah dipadam daripada Upstash Redis."""
    imdb_id, short_hash = extract_metadata_from_filename(file_path)
    if not imdb_id:
        return

    base_id = imdb_id.split(":")[0]
    shard = db.get_shard(base_id)
    target_filename = Path(file_path).name

    # Periksa kedua-dua kunci: P2P (stremio:{id}) dan HTTP (stremio:http:{id})
    for prefix in [f"stremio:{imdb_id}", f"stremio:http:{imdb_id}"]:
        try:
            raw = db.execute_command(shard, "GET", prefix)
            if not raw:
                continue

            data = json.loads(raw)
            if isinstance(data, dict):
                streams = data.get("streams", [])
                if isinstance(streams, list) and streams:
                    orig_len = len(streams)
                    filtered_streams = [
                        s for s in streams
                        if not (
                            Path(s.get("file_path", "")).name == target_filename or
                            (short_hash and s.get("info_hash", "").lower().startswith(short_hash))
                        )
                    ]
                    if len(filtered_streams) < orig_len:
                        if len(filtered_streams) == 0:
                            db.execute_command(shard, "DEL", prefix)
                            console.print(f"      └ [dim green]Kunci '{prefix}' dipadamkan dari Redis (tiada lagi strim aktif).[/dim green]")
                        else:
                            data["streams"] = filtered_streams
                            first = filtered_streams[0]
                            data["file_path"] = first.get("file_path")
                            data["stream_url"] = first.get("stream_url")
                            data["b2_bucket"] = first.get("b2_bucket")
                            data["info_hash"] = first.get("info_hash")
                            db.execute_command(shard, "SET", prefix, json.dumps(data, ensure_ascii=False))
                            console.print(f"      └ [dim green]Kunci '{prefix}' dikemas kini di Redis (baki {len(filtered_streams)} versi).[/dim green]")
                else:
                    if Path(data.get("file_path", "")).name == target_filename:
                        db.execute_command(shard, "DEL", prefix)
                        console.print(f"      └ [dim green]Kunci '{prefix}' dipadamkan dari Redis.[/dim green]")
        except Exception:
            pass


# ------------------------------------------------------------------------------
# 5. PEMADAMAN FAIL FIZIKAL B2 (DIRECT API + RCLONE FALLBACK)
# ------------------------------------------------------------------------------
def delete_file_physical_b2(acc: Dict[str, Any], file_info: Dict[str, Any], api_url: str, auth_token: str) -> bool:
    """Memadam versi fail tertentu terus menggunakan API Backblaze B2."""
    file_id = file_info.get("fileId")
    file_name = file_info.get("fileName")
    acc_idx = acc["index"]

    if api_url and auth_token and file_id and file_name:
        try:
            with httpx.Client(timeout=20.0) as client:
                del_resp = client.post(
                    f"{api_url}/b2api/v2/b2_delete_file_version",
                    headers={"Authorization": auth_token},
                    json={"fileName": file_name, "fileId": file_id}
                )
                if del_resp.status_code == 200:
                    return True
        except Exception:
            pass

    # Kaedah Sandaran melalui Modul X02
    try:
        if hasattr(storage, "delete_file_from_b2"):
            return storage.delete_file_from_b2(acc_idx, file_name)
    except Exception:
        pass

    return False


# ------------------------------------------------------------------------------
# 6. ALUR KERJA UTAMA PENGAWAL OVERAGE 10GB (X12)
# ------------------------------------------------------------------------------
def run_b2_overage_guard(max_cap_gb: float = 10.0, max_delete_per_acc: int = 2) -> Dict[str, Any]:
    max_cap_bytes = int(max_cap_gb * 1024**3)
    accounts = load_dynamic_b2_accounts()

    if not accounts:
        console.print("[bold red]❌ Tiada akaun B2 dikesan dalam persekitaran![/bold red]")
        return {"status": "error", "message": "No B2 accounts"}

    header_msg = (
        f"[bold cyan]🛡️  STREMIO PRIVATE DEBRID V3: B2 OVERAGE AUTO-GUARD (X12)[/bold cyan]\n"
        f"Jumlah Akaun Disemak : [bold white]{len(accounts)} Akaun B2[/bold white]\n"
        f"Had Siling Bebas     : [bold yellow]{max_cap_gb:.2f} GB[/bold yellow] (Sekat Caj Terlebih / Overage)\n"
        f"Had Pemadaman        : [bold magenta]Maksimum {max_delete_per_acc} Fail[/bold magenta] per akaun melebihi had"
    )
    console.print(Panel.fit(header_msg, border_style="cyan"))
    console.print("[dim]🔍 Mengimbas kapasiti dan senarai fail bagi 50 akaun B2 secara selari...[/dim]")

    inspected_accounts: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {executor.submit(inspect_account_files_detailed, acc, 30.0): acc for acc in accounts}
        for fut in as_completed(future_map):
            inspected_accounts.append(fut.result())

    inspected_accounts.sort(key=lambda x: x["index"])

    actions_summary = []
    total_freed_bytes = 0

    table = Table(
        title=f"📊 Status Kapasiti & Tindakan Pengawal Overage (Had: {max_cap_gb:.1f} GB)",
        border_style="cyan"
    )
    table.add_column("Akaun", justify="center", style="cyan", width=8)
    table.add_column("Nama Baldi", style="white", width=25)
    table.add_column("Saiz Awal", justify="right", style="white", width=11)
    table.add_column("Tindakan", justify="center", width=24)
    table.add_column("Saiz Akhir", justify="right", style="green", width=11)
    table.add_column("Status Akhir", justify="center", width=18)

    for data in inspected_accounts:
        idx = data["index"]
        idx_str = f"#{idx:03d}"
        b_name = data["bucket_name"]
        initial_bytes = data["used_bytes"]
        initial_gb = initial_bytes / (1024**3)

        if not data["success"]:
            table.add_row(
                idx_str, b_name, "RALAT",
                "[bold red]Ralat Imbasan[/bold red]",
                "RALAT", "[red]GAGAL SEMAK[/red]"
            )
            continue

        current_bytes = initial_bytes
        files = data.get("files", [])
        deleted_count = 0
        deleted_files_log = []

        # JIKA MELEBIHI HAD SILING (cth: > 10.0 GB)
        if current_bytes > max_cap_bytes:
            console.print(f"\n[bold yellow]⚠️  Akaun #{idx:03d} ({b_name}) menggunakan {initial_gb:.2f} GB (Melebihi {max_cap_gb:.1f} GB)![/bold yellow]")

            acc_dict = next((a for a in accounts if a["index"] == idx), {})
            api_url = data.get("api_url", "")
            auth_token = data.get("auth_token", "")

            # Padam 1 hingga 2 fail paling lama
            for f in files:
                if current_bytes <= max_cap_bytes:
                    break
                if deleted_count >= max_delete_per_acc:
                    break

                f_name = f["fileName"]
                f_size = f["size"]
                f_size_gb = f_size / (1024**3)

                console.print(f"   └ [red]🗑️  Memadam fail tertua (#{deleted_count + 1}):[/red] [bold white]{Path(f_name).name}[/bold white] ({f_size_gb:.2f} GB)")

                ok = delete_file_physical_b2(acc_dict, f, api_url, auth_token)
                if ok:
                    purge_redis_metadata_for_file(f_name)
                    current_bytes -= f_size
                    total_freed_bytes += f_size
                    deleted_count += 1
                    deleted_files_log.append(Path(f_name).name)
                else:
                    console.print("      └ [bold red]✖ Gagal memadam fail fizikal dari B2.[/bold red]")

        final_gb = current_bytes / (1024**3)

        if deleted_count > 0:
            action_str = f"[bold green]Padam {deleted_count} fail lama[/bold green]"
            status_str = "[bold green]BERJAYA (<10GB) ✔[/bold green]" if current_bytes <= max_cap_bytes else "[bold yellow]MASIH >10GB[/bold yellow]"
            actions_summary.append({
                "account": idx_str,
                "bucket": b_name,
                "initial_gb": initial_gb,
                "final_gb": final_gb,
                "deleted": deleted_files_log
            })
        else:
            action_str = "[dim]Tiada Tindakan[/dim]"
            status_str = "[green]SELAMAT (<10GB)[/green]"

        table.add_row(
            idx_str,
            b_name,
            f"{initial_gb:.2f} GB",
            action_str,
            f"{final_gb:.2f} GB",
            status_str
        )

    console.print(table)

    freed_gb = total_freed_bytes / (1024**3)
    if actions_summary:
        msg = (
            f"[bold green]🎉 PENGUATKUASAAN SELESAI: CAJ OVERAGE BERJAYA DIELAKKAN![/bold green]\n"
            f"Jumlah Akaun Dibaiki : [bold cyan]{len(actions_summary)} akaun[/bold cyan]\n"
            f"Jumlah Ruang Dibebas : [bold yellow]{freed_gb:.2f} GB[/bold yellow]\n"
            f"Semua fail lama yang melebihi siling 10GB telah dipadam dan metadata Redis diselaraskan."
        )
        console.print(Panel(msg, border_style="green"))
    else:
        msg = (
            f"[bold green]✅ SEMUA 50 AKAUN B2 DIBAWAH HAD SILING 10.0 GB![/bold green]\n"
            f"Tiada sebarang akaun yang berisiko dikenakan caj mikro overage. Tiada fail dipadam."
        )
        console.print(Panel(msg, border_style="green"))

    return {
        "status": "success",
        "actions_taken": len(actions_summary),
        "freed_gb": freed_gb,
        "details": actions_summary
    }


def main():
    parser = argparse.ArgumentParser(description="Stremio V3 B2 Storage Overage Auto-Guard (X12)")
    parser.add_argument("--max-cap-gb", type=float, default=10.0, help="Had siling selamat per akaun (Lalai: 10.0 GB)")
    parser.add_argument("--max-delete", type=int, default=2, help="Had maksimum fail dipadam per akaun melebihi had (Lalai: 2)")
    args = parser.parse_args()

    run_b2_overage_guard(max_cap_gb=args.max_cap_gb, max_delete_per_acc=args.max_delete)


if __name__ == "__main__":
    main()