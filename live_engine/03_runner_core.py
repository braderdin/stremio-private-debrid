#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - RUNNER CORE ENGINE (ARIA2C + RCLONE + REDIS)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/03_runner_core.py
# ==============================================================================

import os
import sys
import time
import shutil
import hashlib
import argparse
import subprocess
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Pemuatan Modul Teras Modular (00_config, 01_redis_db, 02_b2_storage)
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    _config = importlib.import_module("00_config")
    _redis_mod = importlib.import_module("01_redis_db")
    _b2_mod = importlib.import_module("02_b2_storage")

    db = getattr(_redis_mod, "db")
    storage = getattr(_b2_mod, "storage")
    TEMP_DIR = getattr(_config, "TEMP_DIR", CURRENT_DIR / "temp")
except Exception as e:
    console.print(f"[bold red]❌ Gagal mengimport modul asas: {e}[/bold red]")
    sys.exit(1)

# Senarai Public Trackers Berprestasi Tinggi
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce"
]


def build_magnet_link(info_hash: str, name: str) -> str:
    """Membina URI Magnet lengkap berserta senarai pelayan tracker pantas."""
    tr_params = "&".join([f"tr={t}" for t in PUBLIC_TRACKERS])
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={name}&{tr_params}"


def calculate_sha256(filepath: Path) -> str:
    """Mengira hash SHA-256 fail video besar secara blok 4MB untuk jimat RAM."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def run_aria2c_download(magnet: str, out_dir: Path, file_idx: int) -> Optional[Path]:
    """Menjalankan aria2c CLI untuk menyedut fail video sasaran."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "--seed-time=0",
        "--summary-interval=5",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--bt-stop-timeout=600",
        f"--dir={str(out_dir)}",
    ]

    # Pilih fail spesifik sekiranya muat turun siri TV (file_idx bermula dari 1 di aria2c)
    if file_idx > 0:
        cmd.append(f"--select-file={file_idx + 1}")

    cmd.append(magnet)

    console.print(f"[cyan]🚀 Memulakan aria2c (Indeks Fail: {file_idx})...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if res.returncode != 0:
        console.print(f"[bold red]❌ aria2c tamat dengan ralat (Kod {res.returncode}):[/bold red]\n{res.stdout[-600:]}")
        return None

    # Cari fail video terbesar dalam folder muat turun
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".m4v"}
    candidates = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in video_exts]

    if not candidates:
        console.print("[bold red]❌ Tiada fail video dikesan selepas proses aria2c selesai![/bold red]")
        return None

    # Susun mengikut saiz fail terbesar
    candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
    return candidates[0]


def upload_via_rclone(local_file: Path, b2_account: Dict[str, Any], dest_path: str) -> bool:
    """Memindahkan fail ke B2 menggunakan konfigurasi dinamik rclone tanpa fail fizikal."""
    env = os.environ.copy()
    remote_name = "TEMP_B2_RUNNER"

    # Konfigurasi parameter rclone terus melalui Environment Variables
    env[f"RCLONE_CONFIG_{remote_name}_TYPE"] = "b2"
    env[f"RCLONE_CONFIG_{remote_name}_ACCOUNT"] = b2_account["key_id"]
    env[f"RCLONE_CONFIG_{remote_name}_KEY"] = b2_account["app_key"]
    env[f"RCLONE_CONFIG_{remote_name}_ENDPOINT"] = b2_account["endpoint"]

    target_remote = f"{remote_name}:{b2_account['bucket_name']}/{dest_path.lstrip('/')}"
    cmd = [
        "rclone", "copyto",
        str(local_file),
        target_remote,
        "--transfers=4",
        "--fast-list",
        "--stats=10s",
        "--stats-one-line"
    ]

    console.print(f"[cyan]☁️ Memindahkan fail ke B2 Bucket: #{b2_account['index']} ({b2_account['bucket_name']})...[/cyan]")
    res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if res.returncode != 0:
        console.print(f"[bold red]❌ Rclone gagal memindahkan fail:[/bold red]\n{res.stdout[-600:]}")
        return False
    return True


def execute_job(info_hash: str, imdb_id: str, file_idx: int, raw_title: str):
    """Aliran lengkap proses semakan, muat turun, pengiraan, muat naik dan pendaftaran Redis."""
    console.print(Panel.fit(
        f"[bold cyan]⚡ DEBRID RUNNER ENGINE: MEMPROSES TUGAS[/bold cyan]\n"
        f"[yellow]IMDb ID:[/yellow] {imdb_id} | [yellow]Hash:[/yellow] {info_hash}\n"
        f"[white]Tajuk Fail:[/white] {raw_title}",
        border_style="cyan"
    ))

    # 1. Semakan Cache Redis Sedia Ada
    existing_meta = db.get_stream_metadata(imdb_id)
    if existing_meta and existing_meta.get("b2_bucket"):
        console.print("[bold green]✨ Fail ini sudah wujud dalam pangkalan data! Melangkau proses sedutan.[/bold green]")
        return

    # 2. Persediaan Direktori Kerja Sementara
    job_dir = TEMP_DIR / f"job_{info_hash[:10]}_{int(time.time())}"
    magnet_url = build_magnet_link(info_hash, raw_title)

    try:
        # 3. Muat Turun Torrent melalui aria2c
        downloaded_file = run_aria2c_download(magnet_url, job_dir, file_idx)
        if not downloaded_file:
            console.print("[bold red]❌ Muat turun dibatalkan kerana ralat aria2c.[/bold red]")
            return

        file_size = downloaded_file.stat().st_size
        console.print(f"[green]✅ Fail siap disedut: {downloaded_file.name} ({file_size / (1024*1024):.2f} MB)[/green]")

        # 4. Pengiraan SHA-256
        console.print("[cyan]🔍 Mengira cap jari digital SHA-256...[/cyan]")
        sha256_hash = calculate_sha256(downloaded_file)

        # 5. Pemilihan Akaun B2 (Round-Robin dengan Pelupusan LRU jika Penuh)
        b2_target = storage.allocate_storage_with_eviction(file_size, db)
        if not b2_target:
            console.print("[bold red]❌ Gagal memperuntukkan storan B2 yang sah![/bold red]")
            return

        # 6. Pemindahan Fail ke Storan B2
        clean_name = f"{imdb_id.replace(':', '_')}_{downloaded_file.name}"
        b2_rel_path = f"media/{clean_name}"
        upload_ok = upload_via_rclone(downloaded_file, b2_target, b2_rel_path)

        if not upload_ok:
            console.print("[bold red]❌ Pemindahan ke B2 gagal. Proses dihentikan.[/bold red]")
            return

        # 7. Penyelarasan & Pendaftaran Metadata ke Upstash Redis
        metadata = {
            "title": raw_title,
            "resolution": "1080p" if "1080" in raw_title else ("720p" if "720" in raw_title else "HD"),
            "file_name": clean_name,
            "file_path": b2_rel_path,
            "size_bytes": file_size,
            "b2_account_index": b2_target["index"],
            "b2_bucket": b2_target["bucket_name"],
            "info_hash": info_hash.lower(),
            "sha256": sha256_hash,
            "created_at": int(time.time()),
        }

        saved = db.set_stream_metadata(imdb_id, metadata)
        if saved:
            console.print(Panel(
                f"[bold green]🎉 PROSES SELESAI DENGAN JAYANYA![/bold green]\n\n"
                f"[yellow]Pautan Penstriman B2 Proksi:[/yellow]\n"
                f"[cyan]{metadata.get('stream_url')}[/cyan]",
                border_style="green"
            ))
        else:
            console.print("[bold red]⚠️ Fail berjaya dimuat naik ke B2 tetapi gagal dikemas kini ke Redis![/bold red]")

    finally:
        # 8. Pembersihan Cakera Runner
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            console.print("[dim]🧹 Direktori sementara berjaya dipadam.[/dim]")


def main():
    parser = argparse.ArgumentParser(description="Stremio Private Debrid Task Runner")
    parser.add_argument("--hash", required=True, help="Torrent InfoHash")
    parser.add_argument("--imdb", required=True, help="IMDb ID atau Siri ID")
    parser.add_argument("--idx", default="0", help="Indeks fail torrent")
    parser.add_argument("--title", default="Video", help="Nama fail tajuk")
    args = parser.parse_args()

    try:
        f_idx = int(args.idx)
    except ValueError:
        f_idx = 0

    execute_job(args.hash, args.imdb, f_idx, args.title)


if __name__ == "__main__":
    main()