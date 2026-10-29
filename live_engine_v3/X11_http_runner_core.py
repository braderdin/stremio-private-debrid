#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - DIRECT HTTP & DASH/HLS RUNNER CORE (X11)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X11_http_runner_core.py
#
# CIRI-CIRI UTAMA:
# 1. Dwi-Fungsi: Sokongan modul Pengesan (--detect) untuk Step 6d GitHub Actions.
# 2. Dwi-Enjin Muat Turun Pintar:
#    - MP4 Direct  -> aria2c HTTP Multi-Stream (16 threads).
#    - DASH / HLS  -> yt-dlp dengan 16 fragmen selari & cantuman MP4 automatik.
# 3. 100% Serasi dengan B2 Storage (X02_series_b2storage.py):
#    - Peruntukan Round-Robin 50 Baldi & Pemindahan Native Rclone.
# 4. 100% Serasi dengan Sharded Redis (X01_series_redis.py):
#    - Mendaftar URL Cloudflare B2 Proxy & penjejak garis masa LRU.
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
from urllib.parse import unquote
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ------------------------------------------------------------------------------
# 1. SETUP LALUAN & IMPORT MODUL ASAL (00_config, X01, X02)
# ------------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"

for p in [CURRENT_DIR, LIVE_ENGINE_DIR, PROJECT_ROOT]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _config = importlib.import_module("00_config")
    _redis_mod = importlib.import_module("X01_series_redis")
    _b2_mod = importlib.import_module("X02_series_b2storage")

    db = getattr(_redis_mod, "series_db")
    storage = getattr(_b2_mod, "series_storage")
    TEMP_DIR = getattr(_config, "TEMP_DIR", CURRENT_DIR / "temp")
except Exception as e:
    console.print(f"[bold red]❌ Ralat import modul asas dari live_engine: {e}[/bold red]")
    sys.exit(1)


# ------------------------------------------------------------------------------
# 2. PEMBANTU FAIL & HASH
# ------------------------------------------------------------------------------
def calculate_sha256(filepath: Path) -> str:
    """Mengira nilai SHA-256 menggunakan penimbal 4MB."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def detect_resolution(title: str) -> str:
    t = title.lower()
    if any(q in t for q in ["2160", "4k", "uhd"]):
        return "4K"
    if any(q in t for q in ["1440", "2k", "qhd"]):
        return "2K"
    if any(q in t for q in ["1080", "fhd"]):
        return "1080p"
    if any(q in t for q in ["720", "hd"]):
        return "720p"
    return "HD"


def sanitize_b2_path(
    imdb_id: str,
    info_hash: str,
    original_filename: str,
    is_series: bool = False,
    season: int = 1,
    episode: int = 1,
) -> Tuple[str, str]:
    """Menjana nama fail seragam untuk B2: media/{base_id}.{hash[:8]}.{stem}.mp4"""
    p = Path(original_filename)
    ext = p.suffix.lower() if p.suffix else ".mp4"
    if ext not in [".mp4", ".mkv"]:
        ext = ".mp4"
    stem = p.stem
    short_hash = (info_hash or "00000000")[:8].lower()

    if is_series:
        base_id = imdb_id.split(":")[0]
        prefix = f"{base_id}.S{season:02d}E{episode:02d}"
    else:
        prefix = imdb_id

    raw_combined = f"{prefix}.{short_hash}.{stem}"
    clean_stem = re.sub(r"[^a-zA-Z0-9]+", ".", raw_combined).strip(".")
    clean_stem = re.sub(r"\.+", ".", clean_stem)

    clean_filename = f"{clean_stem}{ext}"
    b2_relative_path = f"media/{clean_filename}"
    return clean_filename, b2_relative_path


# ------------------------------------------------------------------------------
# 3. PENGESAN PINTAR (UNTUK STEP 6d GITHUB ACTIONS)
# ------------------------------------------------------------------------------
def detect_stream_type_in_redis(imdb_id: str, target_hash: str) -> str:
    """
    Menyemak metadata senarai di Redis untuk menentukan sama ada info_hash
    merupakan HTTP stream (PenguPlay) atau P2P Torrent biasa.
    """
    clean_hash = target_hash.lower().strip()
    torrent_list = db.get_torrent_list(imdb_id) or []

    for item in torrent_list:
        h = (item.get("info_hash") or "").lower().strip()
        if h == clean_hash:
            if item.get("stream_type") == "http" or "pengu.uk" in item.get("direct_url", ""):
                return "http"
            return "torrent"

    # Jika tiada dalam senarai atau hash AUTO, anggap sebagai torrent
    return "torrent"


# ------------------------------------------------------------------------------
# 4. ENJIN MUAT TURUN HTTP PINTAR (ARIA2C & YT-DLP)
# ------------------------------------------------------------------------------
def download_http_mp4(url: str, out_file: Path) -> bool:
    """Muat turun fail MP4 terus menggunakan aria2c dengan 16 sambungan selari."""
    out_dir = out_file.parent
    file_name = out_file.name

    cmd = [
        "aria2c",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--summary-interval=5",
        "--user-agent=Stremio/4.4.168",
        "--header=Accept: */*",
        f"--dir={str(out_dir)}",
        f"--out={file_name}",
        url
    ]

    console.print(f"[cyan]🚀 aria2c memulakan muat turun HTTP multi-stream (16 thread)...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if res.returncode == 0 and out_file.exists() and out_file.stat().st_size > 10 * 1024 * 1024:
        console.print("[bold green]✅ aria2c berjaya memuat turun fail video HTTP![/bold green]")
        return True

    console.print(f"[bold yellow]⚠️ aria2c gagal atau pelayan tidak menyokong multi-chunk. Beralih ke fallback yt-dlp...[/bold yellow]")
    return download_dash_or_hls(url, out_file)


def download_dash_or_hls(url: str, out_file: Path) -> bool:
    """Muat turun strim DASH (.mpd) atau HLS (.m3u8) menggunakan yt-dlp."""
    cmd = [
        "yt-dlp",
        "--concurrent-fragments", "16",
        "--user-agent", "Stremio/4.4.168",
        "-f", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "-o", str(out_file),
        url
    ]

    console.print(f"[cyan]🎬 yt-dlp memproses penstriman serpihan DASH/HLS secara selari...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if res.returncode == 0 and out_file.exists() and out_file.stat().st_size > 10 * 1024 * 1024:
        console.print("[bold green]✅ yt-dlp berjaya memuat turun dan mencantumkan video DASH/HLS![/bold green]")
        return True

    console.print(f"[bold red]❌ yt-dlp gagal memproses penstriman HTTP.[/bold red]")
    return False


# ------------------------------------------------------------------------------
# 5. PELAKSANAAN UTAMA (RUNNER CORE X11)
# ------------------------------------------------------------------------------
def execute_http_job(info_hash: str, raw_imdb_id: str, raw_title: str):
    imdb_id = unquote(raw_imdb_id).strip()
    target_hash = info_hash.lower().strip()
    is_series = ":" in imdb_id
    kind = "SIRI TV" if is_series else "FILEM"

    season = 1
    episode = 1
    if is_series:
        parts = imdb_id.split(":")
        base_id = parts[0]
        season = int(parts[1]) if len(parts) > 1 else 1
        episode = int(parts[2]) if len(parts) > 2 else 1
    else:
        base_id = imdb_id

    console.print(Panel.fit(
        f"[bold yellow]⚡ V3 HTTP DEBRID RUNNER ENGINE (X11): MEMPROSES TUGAS ({kind})[/bold yellow]\n"
        f"IMDb: {imdb_id} | Hash: {target_hash}\n"
        f"Tajuk: {raw_title}" + (f" | S{season:02d}E{episode:02d}" if is_series else ""),
        border_style="yellow",
    ))

    # 1. Cari Maklumat Pautan Terus daripada Cache Redis
    torrent_list = db.get_torrent_list(imdb_id) or []
    target_item = next((it for it in torrent_list if (it.get("info_hash") or "").lower().strip() == target_hash), None)

    if not target_item or not target_item.get("direct_url"):
        console.print(f"[bold red]❌ Maklumat direct_url bagi hash {target_hash} tidak ditemui di Redis![/bold red]")
        sys.exit(1)

    direct_url = target_item["direct_url"]
    stream_format = target_item.get("stream_format", "MP4").upper()
    media_name = target_item.get("name", raw_title)

    table = Table(title="🎯 Parameter Strim HTTP Terpilih", border_style="yellow")
    table.add_column("Parameter", style="cyan")
    table.add_column("Maklumat", style="white")
    table.add_row("Punca", target_item.get("source", "PenguPlay"))
    table.add_row("Format", stream_format)
    table.add_row("Kualiti", target_item.get("quality", "HD"))
    table.add_row("URL Sasaran", direct_url[:75] + "...")
    console.print(table)

    # 2. Semakan Hash-Aware: Jika Versi Ini Sudah Wujud di B2, Langkau Muat Turun
    cached_torrent = db.get_torrent_cache(target_hash)
    existing = db.get_stream_metadata(imdb_id)

    hash_exists_in_b2 = False
    if existing:
        if isinstance(existing, list):
            hash_exists_in_b2 = any(
                isinstance(item, dict) and item.get("info_hash", "").lower() == target_hash and item.get("file_path")
                for item in existing
            )
        elif isinstance(existing, dict):
            if "streams" in existing and isinstance(existing["streams"], list):
                hash_exists_in_b2 = any(
                    isinstance(item, dict) and item.get("info_hash", "").lower() == target_hash and item.get("file_path")
                    for item in existing["streams"]
                )
            elif existing.get("info_hash", "").lower() == target_hash and existing.get("file_path"):
                hash_exists_in_b2 = True

    if hash_exists_in_b2 or (cached_torrent and hash_exists_in_b2):
        console.print(Panel.fit(
            f"[bold green]✨ Strim HTTP dengan Hash ini sudah wujud dalam B2!\n"
            f"ID: {imdb_id} | Hash: {target_hash}\n"
            f"Proses muat turun tidak perlu diulang.[/bold green]",
            border_style="green",
        ))
        return

    # 3. Muat Turun Fail ke Folder Sementara
    job_dir = TEMP_DIR / f"job_http_{target_hash[:10]}_{int(time.time())}"
    job_dir.mkdir(parents=True, exist_ok=True)
    temp_video_file = job_dir / "video.mp4"

    try:
        download_success = False
        if stream_format == "MP4" and not any(ext in direct_url for ext in [".mpd", ".m3u8"]):
            download_success = download_http_mp4(direct_url, temp_video_file)
        else:
            download_success = download_dash_or_hls(direct_url, temp_video_file)

        if not download_success or not temp_video_file.exists():
            console.print("[bold red]❌ Muat turun fail video HTTP gagal.[/bold red]")
            sys.exit(1)

        file_size = temp_video_file.stat().st_size
        sha256_hash = calculate_sha256(temp_video_file)

        # 4. Agihan Storan B2 (50 Baldi Round-Robin & LRU Eviction dari X02)
        b2_target = storage.allocate_storage_with_eviction(file_size, db)
        if not b2_target:
            console.print("[bold red]❌ Storan B2 tidak mencukupi untuk menampung fail.[/bold red]")
            sys.exit(1)

        # 5. Penapisan Nama Destinasi B2
        clean_filename, b2_path = sanitize_b2_path(
            imdb_id=imdb_id,
            info_hash=target_hash,
            original_filename=f"{raw_title}.mp4",
            is_series=is_series,
            season=season,
            episode=episode,
        )
        console.print(f"[cyan]📁 Destinasi B2 Diselaraskan:[/cyan] [bold white]{b2_path}[/bold white]")

        # 6. Pemindahan ke Baldi B2 Menggunakan Native Rclone
        upload_ok = storage.upload_file(temp_video_file, b2_target, b2_path)
        if not upload_ok:
            console.print("[bold red]❌ Pemindahan ke B2 gagal. Proses dihentikan.[/bold red]")
            sys.exit(1)

        # 7. Pendaftaran Metadata ke Upstash Redis Shard yang Tepat
        resolution = detect_resolution(f"{media_name} {target_item.get('quality', '')}")
        metadata = {
            "title": raw_title,
            "resolution": resolution,
            "file_name": clean_filename,
            "file_path": b2_path,
            "size_bytes": file_size,
            "b2_account_index": b2_target["index"],
            "b2_bucket": b2_target["bucket_name"],
            "info_hash": target_hash,
            "sha256": sha256_hash,
            "created_at": int(time.time()),
        }
        db.set_stream_metadata(imdb_id, metadata)

        console.print(Panel(
            f"[bold green]🎉 VIDEO STRIM HTTP BERJAYA DIMUAT NAIK KE B2 & DIKEMAS KINI KE REDIS![/bold green]\n"
            f"[cyan]Kunci Redis: stremio:{imdb_id}[/cyan]\n"
            f"[cyan]URL Proksi : {metadata.get('stream_url')}[/cyan]",
            border_style="green",
        ))

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            console.print("[dim]🧹 Direktori sementara dibersihkan.[/dim]")


# ------------------------------------------------------------------------------
# 6. TITIK MASUK UTAMA
# ------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="V3 Direct HTTP & DASH/HLS Runner Engine (X11)")
    parser.add_argument("--hash", required=True, help="InfoHash atau Pseudo-Hash")
    parser.add_argument("--imdb", required=True, help="IMDb ID (cth: tt5312232 atau tt0944947:1:1)")
    parser.add_argument("--title", default="Video", help="Tajuk video atau nama pelepasan")
    parser.add_argument("--detect", action="store_true", help="Mod semakan jenis pautan untuk GitHub Actions")
    args = parser.parse_args()

    # Mod Pengesanan untuk Step 6d
    if args.detect:
        stype = detect_stream_type_in_redis(args.imdb, args.hash)
        console.print(f"[cyan]🔍 Pengesanan Jenis Pautan:[/cyan] [bold yellow]{stype.upper()}[/bold yellow]")
        # Hantar output format GitHub Actions jika dijalankan di Runner
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output and Path(github_output).exists():
            with open(github_output, "a") as f:
                f.write(f"stream_type={stype}\n")
        else:
            print(f"stream_type={stype}")
        sys.exit(0)

    # Mod Muat Turun untuk Step 7a
    execute_http_job(args.hash, args.imdb, args.title)


if __name__ == "__main__":
    main()