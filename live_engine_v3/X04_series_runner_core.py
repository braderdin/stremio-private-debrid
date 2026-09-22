#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - SERIES & MOVIE RUNNER CORE ENGINE
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X04_series_runner_core.py
# CIRI:
# 1. Mengimport 100% Modul Asal: 00_config, X01_series_redis, X02_series_b2storage dari live_engine/
# 2. Sokongan Penuh Siri TV (tt...:S:E) & Filem Biasa (tt...)
# 3. aria2c Bersasar: Muat turun episod khusus daripada Season Pack (--select-file)
# 4. Pengecaman Episod Pintar jika terdapat pelbagai fail video di folder muat turun
# 5. Penamaan Bersih B2: media/{base_id}.S01E01.{hash[:8]}.{nama}.{ext}
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

# 1. Penyelarasan Laluan Import Modul Asal dari live_engine/
CURRENT_DIR = Path(__file__).resolve().parent
LIVE_ENGINE_DIR = CURRENT_DIR.parent / "live_engine"

for p in [CURRENT_DIR, LIVE_ENGINE_DIR]:
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

PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
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


def detect_resolution(title: str) -> str:
    """Mengesan resolusi video berdasarkan tajuk fail/torrent."""
    t = title.lower()
    if "2160" in t or "4k" in t:
        return "4K"
    if "1080" in t:
        return "1080p"
    if "720" in t:
        return "720p"
    if "480" in t:
        return "480p"
    if any(k in t for k in ["cam", "telesync", "hdcam", "ts"]):
        return "CAM"
    return "HD"


def is_matching_episode(filename: str, season: int, episode: int) -> bool:
    """Menyemak sama ada nama fail sepadan dengan episod yang diminta."""
    s_num = str(season)
    e_num = str(episode)
    patterns = [
        rf"(?i)\b[Ss]0*{s_num}[\s.\-_]*[Ee]0*{e_num}\b",
        rf"(?i)\b0*{s_num}x0*{e_num}\b",
    ]
    return any(re.search(pat, filename) for pat in patterns)


def sanitize_b2_path(
    imdb_id: str,
    info_hash: str,
    original_filename: str,
    is_series: bool = False,
    season: int = 1,
    episode: int = 1,
) -> Tuple[str, str]:
    """
    Menjana nama fail selamat dan laluan destinasi B2.
    Format Siri : media/tt0944947.S01E01.{hash[:8]}.{stem}.{ext}
    Format Filem: media/tt0078748.{hash[:8]}.{stem}.{ext}
    """
    p = Path(original_filename)
    ext = p.suffix.lower() if p.suffix else ".mp4"
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


def run_aria2c(
    magnet: str,
    out_dir: Path,
    file_idx: int = 0,
    is_series: bool = False,
    season: int = 1,
    episode: int = 1,
) -> Optional[Path]:
    """Melaksanakan enjin muat turun aria2c dengan sokongan pemilihan fail bersasar."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "--seed-time=0",
        "--summary-interval=5",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--bt-stop-timeout=2400",
        f"--dir={str(out_dir)}",
    ]

    # Jika indeks fail khusus diberikan (> 0), arahkan aria2c muat turun indeks tersebut sahaja
    if file_idx > 0:
        console.print(f"[cyan]🎯 aria2c memuat turun indeks fail bersasar: #{file_idx + 1}[/cyan]")
        cmd.append(f"--select-file={file_idx + 1}")

    cmd.append(magnet)

    console.print("[cyan]🚀 aria2c memulakan proses muat turun...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        console.print(f"[bold red]❌ aria2c gagal (Kod {res.returncode}):[/bold red]\n{res.stdout[-500:]}")
        return None

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts"}
    candidates = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in video_exts]
    if not candidates:
        console.print("[bold red]❌ Tiada fail video dikesan selepas proses aria2c.[/bold red]")
        return None

    # Jika siri TV dan terdapat lebih daripada 1 fail video, pilih fail yang sepadan dengan episod
    if is_series and len(candidates) > 1:
        matching_episodes = [p for p in candidates if is_matching_episode(p.name, season, episode)]
        if matching_episodes:
            matching_episodes.sort(key=lambda x: x.stat().st_size, reverse=True)
            console.print(f"[green]🎯 Ditemui fail video episod tepat:[/green] [bold white]{matching_episodes[0].name}[/bold white]")
            return matching_episodes[0]

    # Sandaran: Ambil fail video terbesar
    candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
    return candidates[0]


def execute_job(info_hash: str, raw_imdb_id: str, file_idx: int, raw_title: str):
    imdb_id = unquote(raw_imdb_id).strip()
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
        f"[bold cyan]⚡ V3 DEBRID RUNNER ENGINE: MEMPROSES TUGAS ({kind})[/bold cyan]\n"
        f"IMDb: {imdb_id} | Hash: {info_hash}\n"
        f"Idx: {file_idx} | Tajuk: {raw_title}"
        + (f" | S{season:02d}E{episode:02d}" if is_series else ""),
        border_style="cyan",
    ))

    # 1. Pengendalian Hash "AUTO" Melalui Modul 04_resolver dari live_engine/
    if info_hash.strip().upper() == "AUTO" or len(info_hash.strip()) != 40:
        try:
            resolver = importlib.import_module("04_resolver")
            resolved = resolver.resolve_fallback(base_id, raw_title)
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

    target_hash = info_hash.lower().strip()

    # 2. Semakan Pintar Berasaskan InfoHash (Hash-Aware Verification)
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
            f"[bold green]✨ Torrent dengan InfoHash ini sudah wujud dalam B2!\n"
            f"ID: {imdb_id} | Hash: {target_hash}\n"
            f"Proses muat turun tidak perlu diulang.[/bold green]",
            border_style="green",
        ))
        return

    # 3. Direktori Sementara & Pelaksanaan aria2c
    job_dir = TEMP_DIR / f"job_{target_hash[:10]}_{int(time.time())}"
    magnet_url = build_magnet(target_hash, raw_title)

    try:
        video_file = run_aria2c(
            magnet=magnet_url,
            out_dir=job_dir,
            file_idx=file_idx,
            is_series=is_series,
            season=season,
            episode=episode,
        )
        if not video_file:
            console.print("[bold red]❌ Muat turun aria2c gagal.[/bold red]")
            sys.exit(1)

        file_size = video_file.stat().st_size
        sha256_hash = calculate_sha256(video_file)

        # 4. Agihan Storan B2 (20 Akaun Round-Robin & LRU Eviction dari live_engine/X02_series_b2storage.py)
        b2_target = storage.allocate_storage_with_eviction(file_size, db)
        if not b2_target:
            console.print("[bold red]❌ Storan B2 tidak mencukupi untuk menampung fail.[/bold red]")
            sys.exit(1)

        # 5. Penapisan Nama Destinasi B2
        clean_filename, b2_path = sanitize_b2_path(
            imdb_id=imdb_id,
            info_hash=target_hash,
            original_filename=video_file.name,
            is_series=is_series,
            season=season,
            episode=episode,
        )
        console.print(f"[cyan]📁 Destinasi B2 Diselaraskan:[/cyan] [bold white]{b2_path}[/bold white]")

        # 6. Pemindahan ke Baldi B2
        upload_ok = storage.upload_file(video_file, b2_target, b2_path)
        if not upload_ok:
            console.print("[bold red]❌ Pemindahan ke B2 gagal. Proses dihentikan.[/bold red]")
            sys.exit(1)

        # 7. Pendaftaran Metadata ke Upstash Redis Shard yang Tepat
        resolution = detect_resolution(raw_title)
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
            f"[bold green]🎉 VIDEO BERJAYA DIMUAT NAIK KE B2 & METADATA DIKEMAS KINI KE REDIS![/bold green]\n"
            f"[cyan]Kunci Redis: stremio:{imdb_id}[/cyan]\n"
            f"[cyan]URL Proksi : {metadata.get('stream_url')}[/cyan]",
            border_style="green",
        ))

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            console.print("[dim]🧹 Direktori sementara dibersihkan.[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V3 Debrid Runner Core Engine")
    parser.add_argument("--hash", required=True, help="InfoHash torrent (40-hex)")
    parser.add_argument("--imdb", required=True, help="IMDb ID (cth: tt0944947:1:1 atau tt0078748)")
    parser.add_argument("--idx", default="0", help="Indeks fail di dalam torrent")
    parser.add_argument("--title", default="Video", help="Tajuk pelepasan video")
    args = parser.parse_args()

    execute_job(args.hash, args.imdb, int(args.idx or 0), args.title)