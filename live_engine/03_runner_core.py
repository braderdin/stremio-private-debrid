#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - RUNNER CORE ENGINE (03_runner_core.py)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/03_runner_core.py
# CIRI: SEAMLESS AUTO-RESOLVE + ARIA2C + B2 STORAGE UPLOADER + REDIS METADATA
# ==============================================================================

import os
import re
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
    console.print(f"[bold red]❌ Ralat import modul asas: {e}[/bold red]")
    sys.exit(1)

PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce"
]


def build_magnet(info_hash: str, name: str) -> str:
    """Membina pautan magnet dengan pelacak awam pantas."""
    tr = "&".join([f"tr={t}" for t in PUBLIC_TRACKERS])
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={name}&{tr}"


def calculate_sha256(filepath: Path) -> str:
    """Mengira hash fail video menggunakan penimbal 4MB."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def sanitize_b2_path(imdb_id: str, original_filename: str) -> Tuple[str, str]:
    """
    Menukarkan semua ruang kosong dan simbol pelik menjadi titik '.'
    supaya tiada ralat URL pada proxy dan pemain Stremio.
    """
    p = Path(original_filename)
    ext = p.suffix.lower() if p.suffix else ".mp4"
    stem = p.stem

    # Bersihkan aksara: hanya abjad & nombor dibenarkan, yang lain jadi '.'
    raw_combined = f"{imdb_id}.{stem}"
    clean_stem = re.sub(r"[^a-zA-Z0-9]+", ".", raw_combined).strip(".")
    clean_stem = re.sub(r"\.+", ".", clean_stem)

    clean_filename = f"{clean_stem}{ext}"
    b2_relative_path = f"media/{clean_filename}"
    return clean_filename, b2_relative_path


def run_aria2c(magnet: str, out_dir: Path, file_idx: int) -> Optional[Path]:
    """Melaksanakan enjin muat turun aria2c."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c", "--seed-time=0", "--summary-interval=5", "--max-connection-per-server=16",
        "--split=16", "--min-split-size=1M", "--file-allocation=none", "--bt-stop-timeout=2400",
        f"--dir={str(out_dir)}"
    ]
    if file_idx > 0:
        cmd.append(f"--select-file={file_idx + 1}")
    cmd.append(magnet)

    console.print(f"[cyan]🚀 aria2c memulakan proses muat turun...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        console.print(f"[bold red]❌ aria2c gagal (Kod {res.returncode}):[/bold red]\n{res.stdout[-500:]}")
        return None

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".m4v"}
    candidates = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in video_exts]
    if not candidates:
        console.print("[bold red]❌ Tiada fail video dikesan selepas proses aria2c.[/bold red]")
        return None

    candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
    return candidates[0]


def execute_job(info_hash: str, imdb_id: str, file_idx: int, raw_title: str):
    console.print(Panel.fit(
        f"[bold cyan]⚡ DEBRID RUNNER ENGINE: MEMPROSES TUGAS[/bold cyan]\n"
        f"IMDb: {imdb_id} | Hash: {info_hash}\nTajuk: {raw_title}",
        border_style="cyan"
    ))

    # 1. Semakan Cache Redis Sedia Ada
    existing = db.get_stream_metadata(imdb_id)
    if existing and existing.get("b2_bucket") and existing.get("file_path"):
        console.print("[bold green]✨ Fail ini sudah wujud dalam pangkalan data B2! Selesai.[/bold green]")
        return

    # 2. Pengendalian Hash "AUTO" Melalui Modul 04_resolver
    if info_hash.strip().upper() == "AUTO" or len(info_hash.strip()) != 40:
        try:
            resolver = importlib.import_module("04_resolver")
            resolved = resolver.resolve_fallback(imdb_id, raw_title)
            if not resolved:
                console.print("[bold red]❌ Gagal mendapatkan pautan torrent sah. Proses dihentikan.[/bold red]")
                sys.exit(1)

            info_hash = resolved["info_hash"]
            raw_title = resolved["name"]

            table = Table(title="🎯 Torrent Terpilih untuk Muat Turun", border_style="green")
            table.add_column("Parameter", style="cyan")
            table.add_column("Maklumat", style="white")
            table.add_row("Punca", resolved.get("source", "Apibay"))
            table.add_row("Tajuk", raw_title)
            table.add_row("InfoHash", info_hash)
            table.add_row("Saiz Fail", f"{resolved['size'] / (1024*1024):.2f} MB")
            table.add_row("Seeders", str(resolved["seeders"]))
            console.print(table)

        except Exception as e:
            console.print(f"[bold red]❌ Ralat semasa memanggil 04_resolver: {e}[/bold red]")
            sys.exit(1)

    # 3. Direktori Sementara & Pelaksanaan aria2c
    job_dir = TEMP_DIR / f"job_{info_hash[:10]}_{int(time.time())}"
    magnet_url = build_magnet(info_hash, raw_title)

    try:
        video_file = run_aria2c(magnet_url, job_dir, file_idx)
        if not video_file:
            console.print("[bold red]❌ Muat turun aria2c gagal.[/bold red]")
            sys.exit(1)

        file_size = video_file.stat().st_size
        sha256_hash = calculate_sha256(video_file)

        # 4. Agihan Storan B2 (20 Akaun Round-Robin)
        b2_target = storage.allocate_storage_with_eviction(file_size, db)
        if not b2_target:
            console.print("[bold red]❌ Storan B2 tidak mencukupi untuk menampung fail.[/bold red]")
            sys.exit(1)

        # 5. Penapisan Nama Fail Bebas Ralat Simbol / Space
        clean_filename, b2_path = sanitize_b2_path(imdb_id, video_file.name)
        console.print(f"[cyan]📁 Nama Destinasi B2 Diselaraskan:[/cyan] [bold white]{b2_path}[/bold white]")

        # 6. Pemindahan ke B2 Melalui Storage Manager
        upload_ok = storage.upload_file(video_file, b2_target, b2_path)
        if not upload_ok:
            console.print("[bold red]❌ Pemindahan ke B2 gagal. Proses dihentikan.[/bold red]")
            sys.exit(1)

        # 7. Pendaftaran Metadata ke Upstash Redis
        metadata = {
            "title": raw_title,
            "resolution": "1080p" if "1080" in raw_title else "HD",
            "file_name": clean_filename,
            "file_path": b2_path,
            "size_bytes": file_size,
            "b2_account_index": b2_target["index"],
            "b2_bucket": b2_target["bucket_name"],
            "info_hash": info_hash.lower(),
            "sha256": sha256_hash,
            "created_at": int(time.time()),
        }
        db.set_stream_metadata(imdb_id, metadata)
        console.print(Panel(
            f"[bold green]🎉 VIDEO BERJAYA DIMUAT NAIK KE B2 & DIKEMAS KINI KE REDIS![/bold green]\n"
            f"[cyan]URL Proksi: {metadata.get('stream_url')}[/cyan]",
            border_style="green"
        ))

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            console.print("[dim]🧹 Direktori sementara dibersihkan.[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash", required=True)
    parser.add_argument("--imdb", required=True)
    parser.add_argument("--idx", default="0")
    parser.add_argument("--title", default="Video")
    args = parser.parse_args()

    execute_job(args.hash, args.imdb, int(args.idx or 0), args.title)