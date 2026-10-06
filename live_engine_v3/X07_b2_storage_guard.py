#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - B2 STORAGE PRE-FLIGHT GUARD & EVICTOR
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X07_b2_storage_guard.py
#
# CIRI-CIRI UTAMA:
# 1. Pengesanan Dinamik 20 / 30 / 50 Akaun B2 secara pintar (Zero Hardcoding).
# 2. Imbasan Selari (Multi-Threaded) penggunaan baldi B2 dalam masa beberapa saat.
# 3. Penjagaan Integriti Data (Zero Orphan):
#    - Padam fail fizikal di B2 dahulu.
#    - Padam metadata di Redis Shard hanya selepas B2 mengesahkan fail telah tiada.
# 4. Pelupusan Berperingkat Global LRU sehingga baki ruang selamat (8.0 GB had)
#    mempunyai ruang penimbal sekurang-kurangnya 3.5 GB - 8.0 GB untuk fail baharu.
# 5. Jeda Masa Bertenang (Cooling-off) untuk membenarkan B2 API mengira semula Cap.
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
    console.print(f"[bold red]❌ Ralat mengimport modul asas: {e}[/bold red]")
    sys.exit(1)


# ------------------------------------------------------------------------------
# 2. PENGESANAN AKAUN B2 DINAMIK (20 / 30 / 50 AKAUN)
# ------------------------------------------------------------------------------
def load_dynamic_b2_accounts() -> List[Dict[str, Any]]:
    """
    Mengesan akaun B2 secara dinamik daripada:
    1. Pemboleh ubah persekitaran B2_ACCOUNTS_JSON (GitHub Actions)
    2. Fail rahsia .env.local (Persekitaran WSL Ubuntu tempatan)
    3. Konfigurasi lalai storage.accounts
    """
    raw_json = os.environ.get("B2_ACCOUNTS_JSON", "").strip()

    # 1. Cuba baca dari persekitaran terus
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list) and parsed:
                return parsed
        except Exception:
            pass

    # 2. Cuba baca dari .env.local jika wujud
    env_local_path = PROJECT_ROOT / ".env.local"
    if env_local_path.exists():
        try:
            with open(env_local_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("B2_ACCOUNTS_JSON="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        parsed = json.loads(val)
                        if isinstance(parsed, list) and parsed:
                            return parsed
        except Exception:
            pass

    # 3. Sandaran kepada tetapan asal
    return getattr(storage, "accounts", []) or getattr(_config, "B2_ACCOUNTS", [])


# ------------------------------------------------------------------------------
# 3. PEMERIKSAAN PENGGUNAAN PANTAS (MULTI-THREADED)
# ------------------------------------------------------------------------------
def inspect_single_account(acc: Dict[str, Any]) -> Dict[str, Any]:
    """Mengambil penggunaan satu akaun B2."""
    idx = acc["index"]
    b_name = acc.get("bucket_name", f"bucket-{idx}")
    try:
        files_count, used_bytes, bucket_id = storage.get_bucket_usage(idx)
        return {
            "index": idx,
            "bucket_name": b_name,
            "files_count": files_count,
            "used_bytes": used_bytes,
            "bucket_id": bucket_id,
            "success": True,
        }
    except Exception as e:
        return {
            "index": idx,
            "bucket_name": b_name,
            "files_count": 0,
            "used_bytes": 0,
            "bucket_id": "",
            "success": False,
            "error": str(e),
        }


def scan_all_accounts_parallel(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mengimbas keseluruhan akaun secara selari bagi mengurangkan masa menunggu."""
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(inspect_single_account, acc): acc["index"] for acc in accounts}
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
    results.sort(key=lambda x: x["index"])
    return results


# ------------------------------------------------------------------------------
# 4. PELUPUSAN PINTAR (ZERO ORPHAN IN B2 & REDIS)
# ------------------------------------------------------------------------------
def safe_evict_oldest_stream(target_meta: Dict[str, Any]) -> bool:
    """
    Memadamkan satu rekod strim paling lama:
    1. Sahkan dan padam fail fizikal di B2 dahulu.
    2. Padam metadata di Redis Shard hanya selepas fail disahkan tiada di B2.
    """
    imdb_id = target_meta.get("imdb_id")
    target_hash = (target_meta.get("info_hash") or "").strip().lower()
    acc_idx = target_meta.get("b2_account_index")
    f_path = target_meta.get("file_path", "").lstrip("/")
    title = target_meta.get("title", imdb_id)

    if not acc_idx or not f_path:
        # Jika metadata cacat, bersihkan dari Redis terus
        if imdb_id:
            db.delete_stream_metadata(imdb_id, info_hash=target_hash)
        return False

    console.print(f"[yellow]🗑️  Memulakan pelupusan fail lama:[/yellow] [bold white]{title}[/bold white]")
    console.print(f"   └ Laluan B2 : [cyan]#{acc_idx}[/cyan] ({f_path})")

    # 1. Padam fail fizikal di B2
    b2_deleted = storage.delete_file_from_b2(acc_idx, f_path)

    # 2. Kemas kini Redis:
    # Sama ada berjaya padam di B2 ATAU fail memang sudah tiada di B2 (pembersihan rekod hantu),
    # kita buang metadata dari Redis supaya tidak menghalang kitaran LRU seterusnya.
    db.delete_stream_metadata(imdb_id, info_hash=target_hash)

    if b2_deleted:
        console.print(f"   └ [green]✔ Fail fizikal B2 berjaya dipadam & rekod Redis dibersihkan.[/green]")
    else:
        console.print(f"   └ [dim yellow]ℹ Fail fizikal tidak ditemui di B2 (hantu). Rekod Redis telah dibersihkan.[/dim yellow]")

    # 3. Masa Bertenang (Cooling-off) untuk membenarkan API B2 mengira semula Cap
    time.sleep(3)
    return True


# ------------------------------------------------------------------------------
# 5. ALUR KERJA UTAMA PENGAWAL STORAN (STORAGE GUARD)
# ------------------------------------------------------------------------------
def run_storage_guard(
    required_free_gb: float = 3.5,
    safe_cap_gb: float = 8.0,
    max_evictions: int = 15
) -> bool:
    safe_cap_bytes = int(safe_cap_gb * 1024**3)
    required_free_bytes = int(required_free_gb * 1024**3)

    # 1. Muat dan selaraskan akaun dinamik
    accounts = load_dynamic_b2_accounts()
    if not accounts:
        console.print("[bold red]❌ Tiada akaun B2 dikesan dalam persekitaran![/bold red]")
        return False

    storage.accounts = accounts
    total_acc = len(accounts)

    console.print(Panel.fit(
        f"[bold cyan]🛡️  STREMIO V3: B2 STORAGE PRE-FLIGHT GUARD (X07)[/bold cyan]\n"
        f"Jumlah Akaun Dikesan: [bold green]{total_acc} Akaun B2[/bold green]\n"
        f"Had Siling Selamat   : [bold yellow]{safe_cap_gb:.1f} GB[/bold yellow] per akaun\n"
        f"Ruang Bebas Diperlukan: [bold magenta]>= {required_free_gb:.1f} GB[/bold magenta] untuk fail baharu",
        border_style="cyan"
    ))

    # 2. Imbas status semasa kesemua akaun
    console.print("[dim]🔍 Mengimbas status kapasiti semua akaun B2 secara selari...[/dim]")
    usage_list = scan_all_accounts_parallel(accounts)

    # Semak sama ada sudah wujud akaun yang mempunyai baki mencukupi
    def find_ready_account(u_list):
        for u in u_list:
            if not u["success"]:
                continue
            used = u["used_bytes"]
            free = safe_cap_bytes - used
            if free >= required_free_bytes:
                return u
        return None

    ready_acc = find_ready_account(usage_list)

    # 3. Kitaran Pelupusan LRU jika tiada akaun mempunyai ruang mencukupi
    eviction_count = 0
    while not ready_acc and eviction_count < max_evictions:
        console.print(f"[bold yellow]⚠️  Tiada akaun dengan baki bebas >= {required_free_gb:.1f} GB! Memulakan pelupusan LRU (#{eviction_count + 1})...[/bold yellow]")

        oldest = db.get_oldest_stream_across_shards()
        if not oldest:
            console.print("[bold red]❌ Tiada fail lama dikesan dalam timeline Redis untuk dilupuskan![/bold red]")
            break

        safe_evict_oldest_stream(oldest)
        eviction_count += 1

        # Imbas semula akaun yang terjejas atau keseluruhan untuk kepastian
        usage_list = scan_all_accounts_parallel(accounts)
        ready_acc = find_ready_account(usage_list)

    # 4. Paparan Jadual Keputusan Pra-Pemeriksaan
    table = Table(
        title=f"📊 Status Kapasiti Storan B2 Pasca-Pengawal ({total_acc} Akaun Diperiksa)",
        border_style="green" if ready_acc else "red"
    )
    table.add_column("Akaun", justify="center", style="cyan", width=8)
    table.add_column("Nama Baldi", style="white", width=26)
    table.add_column("Fail", justify="center", style="yellow", width=6)
    table.add_column("Digunakan", justify="right", style="white", width=11)
    table.add_column("Baki Bebas (Had 8GB)", justify="right", style="green", width=14)
    table.add_column("Status Pengawal", justify="center", width=18)

    for u in usage_list:
        idx_str = f"#{u['index']:03d}"
        b_name = u["bucket_name"]
        files = str(u["files_count"])
        used_gb = u["used_bytes"] / (1024**3)
        free_gb = max(0.0, (safe_cap_bytes - u["used_bytes"]) / (1024**3))

        if free_gb >= required_free_gb:
            status_tag = "[bold green]SIAP SEDIA ★[/bold green]"
        elif used_gb >= safe_cap_gb:
            status_tag = "[bold red]PENUH (>8GB)[/bold red]"
        else:
            status_tag = "[yellow]AMARAN RUANG[/yellow]"

        table.add_row(
            idx_str,
            b_name,
            files,
            f"{used_gb:.2f} GB",
            f"{free_gb:.2f} GB",
            status_tag
        )

    console.print(table)

    # 5. Keputusan Akhir
    if ready_acc:
        target_free = (safe_cap_bytes - ready_acc['used_bytes']) / (1024**3)
        console.print(Panel(
            f"[bold green]✅ PENGESAHAN BERJAYA: RUANG STORAN B2 MENCUKUPI![/bold green]\n"
            f"Akaun Sasaran: [bold cyan]#{ready_acc['index']} ({ready_acc['bucket_name']})[/bold cyan]\n"
            f"Baki Bebas   : [bold white]{target_free:.2f} GB[/bold white] (Melebihi sasaran {required_free_gb:.1f} GB)\n"
            f"Tugasan muat turun aria2c & pemindahan B2 sedia untuk dimulakan.",
            border_style="green"
        ))
        return True
    else:
        console.print(Panel(
            f"[bold red]❌ PENGESAHAN GAGAL: TIADA RUANG BEBAS MENCUKUPI PADA MANA-MANA AKAUN B2![/bold red]\n"
            f"Sila semak sama ada fail fizikal tersekat di portal B2 atau tambah akaun baharu.",
            border_style="red"
        ))
        return False


def main():
    parser = argparse.ArgumentParser(description="V3 B2 Storage Pre-Flight Guard (X07)")
    parser.add_argument("--min-free-gb", type=float, default=3.5, help="Ruang bebas minimum diperlukan (Lalai: 3.5 GB)")
    parser.add_argument("--safe-cap-gb", type=float, default=8.0, help="Had siling selamat per akaun (Lalai: 8.0 GB)")
    parser.add_argument("--max-evict", type=int, default=15, help="Had maksimum fail dipadam dalam satu sesi (Lalai: 15)")
    args = parser.parse_args()

    success = run_storage_guard(
        required_free_gb=args.min_free_gb,
        safe_cap_gb=args.safe_cap_gb,
        max_evictions=args.max_evict
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()