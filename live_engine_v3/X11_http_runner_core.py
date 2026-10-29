#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO HTTP FAST PROVIDER - DEDICATED HTTP RUNNER CORE (X11 ISOLATED)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X11_http_runner_core.py
# KUNCI REDIS: stremio:http:{imdb_id} (100% Terasing daripada Addon P2P)
# ==============================================================================

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

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
  console.print(f"[bold red]❌ Ralat import modul asas: {e}[/bold red]")
  sys.exit(1)


# ------------------------------------------------------------------------------
# 2. PEMBANTU FAIL & HASH
# ------------------------------------------------------------------------------
def calculate_sha256(filepath: Path) -> str:
  """Mengira nilai SHA-256 fail video menggunakan penimbal 4MB."""
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
# 3. ENJIN MUAT TURUN HTTP PINTAR (ARIA2C & YT-DLP)
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
      url,
  ]

  console.print(
      "[cyan]🚀 aria2c memulakan muat turun HTTP multi-stream (16"
      " thread)...[/cyan]"
  )
  res = subprocess.run(
      cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
  )

  if (
      res.returncode == 0
      and out_file.exists()
      and out_file.stat().st_size > 10 * 1024 * 1024
  ):
    console.print(
        "[bold green]✅ aria2c berjaya memuat turun fail video HTTP![/bold"
        " green]"
    )
    return True

  console.print(
      "[bold yellow]⚠️ aria2c gagal atau pelayan memerlukan penstriman"
      " serpihan. Beralih ke yt-dlp...[/bold yellow]"
  )
  return download_dash_or_hls(url, out_file)


def download_dash_or_hls(url: str, out_file: Path) -> bool:
  """Muat turun strim DASH (.mpd) atau HLS (.m3u8) menggunakan yt-dlp."""
  cmd = [
      "yt-dlp",
      "--concurrent-fragments",
      "16",
      "--user-agent",
      "Stremio/4.4.168",
      "-f",
      "bestvideo+bestaudio/best",
      "--merge-output-format",
      "mp4",
      "-o",
      str(out_file),
      url,
  ]

  console.print(
      "[cyan]🎬 yt-dlp memproses penstriman serpihan DASH/HLS secara"
      " selari...[/cyan]"
  )
  res = subprocess.run(
      cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
  )

  if (
      res.returncode == 0
      and out_file.exists()
      and out_file.stat().st_size > 10 * 1024 * 1024
  ):
    console.print(
        "[bold green]✅ yt-dlp berjaya memuat turun dan mencantumkan video"
        " DASH/HLS![/bold green]"
    )
    return True

  console.print(
      "[bold red]❌ yt-dlp gagal memproses penstriman HTTP.[/bold red]"
  )
  return False


# ------------------------------------------------------------------------------
# 4. PELAKSANAAN UTAMA (RUNNER CORE X11 TERASING)
# ------------------------------------------------------------------------------
def execute_http_job(info_hash: str, raw_imdb_id: str, raw_title: str):
  imdb_id = unquote(raw_imdb_id).strip()
  target_hash = info_hash.lower().strip()
  is_series = ":" in imdb_id
  kind = "SIRI TV" if is_series else "FILEM"
  base_id = imdb_id.split(":")[0] if is_series else imdb_id

  season = 1
  episode = 1
  if is_series:
    parts = imdb_id.split(":")
    season = int(parts[1]) if len(parts) > 1 else 1
    episode = int(parts[2]) if len(parts) > 2 else 1

  console.print(
      Panel.fit(
          "[bold yellow]⚡ ENJIN RUNNER HTTP KHAS (X11): MEMPROSES TUGAS"
          f" ({kind})[/bold yellow]\nIMDb: {imdb_id} | Hash:"
          f" {target_hash}\nTajuk: {raw_title}"
          + (f" | S{season:02d}E{episode:02d}" if is_series else ""),
          border_style="yellow",
      )
  )

  # 1. Baca maklumat langsung daripada KUNCI TERASING: 'stremio:http:list:{imdb_id}'
  shard = db.get_shard(base_id)
  raw_list = db.execute_command(shard, "GET", f"stremio:http:list:{imdb_id}")
  torrent_list = []
  if raw_list:
    try:
      torrent_list = json.loads(raw_list)
    except Exception:
      torrent_list = []

  target_item = next(
      (
          it
          for it in torrent_list
          if (it.get("info_hash") or "").lower().strip() == target_hash
      ),
      None,
  )
  if not target_item or not target_item.get("direct_url"):
    console.print(
        f"[bold red]❌ Maklumat direct_url bagi hash {target_hash} tidak"
        f" ditemui di 'stremio:http:list:{imdb_id}'![/bold red]"
    )
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

  # 2. Semak sama ada fail sudah siap di B2 (KUNCI TERASING: 'stremio:http:{imdb_id}')
  raw_meta = db.execute_command(shard, "GET", f"stremio:http:{imdb_id}")
  existing = None
  if raw_meta:
    try:
      existing = json.loads(raw_meta)
    except Exception:
      existing = None

  hash_exists_in_b2 = False
  if existing:
    if isinstance(existing, dict):
      if "streams" in existing and isinstance(existing["streams"], list):
        hash_exists_in_b2 = any(
            isinstance(item, dict)
            and item.get("info_hash", "").lower() == target_hash
            and item.get("file_path")
            for item in existing["streams"]
        )
      elif (
          existing.get("info_hash", "").lower() == target_hash
          and existing.get("file_path")
      ):
        hash_exists_in_b2 = True

  if hash_exists_in_b2:
    console.print(
        Panel.fit(
            "✨ Strim HTTP dengan Hash ini sudah wujud dalam B2!\nID:"
            f" {imdb_id} | Hash: {target_hash}\nProses muat turun tidak perlu"
            " diulang.",
            border_style="green",
        )
    )
    return

  # 3. Muat Turun Fail ke Folder Sementara
  job_dir = TEMP_DIR / f"job_http_{target_hash[:10]}_{int(time.time())}"
  job_dir.mkdir(parents=True, exist_ok=True)
  temp_video_file = job_dir / "video.mp4"

  try:
    download_success = False
    if stream_format == "MP4" and not any(
        ext in direct_url.lower() for ext in [".mpd", ".m3u8", "/dash/"]
    ):
      download_success = download_http_mp4(direct_url, temp_video_file)
    else:
      download_success = download_dash_or_hls(direct_url, temp_video_file)

    if not download_success or not temp_video_file.exists():
      console.print(
          "[bold red]❌ Muat turun fail video HTTP gagal.[/bold red]"
      )
      sys.exit(1)

    file_size = temp_video_file.stat().st_size
    sha256_hash = calculate_sha256(temp_video_file)

    # 4. Agihan Storan B2 (50 Baldi Round-Robin & LRU Eviction dari X02)
    b2_target = storage.allocate_storage_with_eviction(file_size, db)
    if not b2_target:
      console.print(
          "[bold red]❌ Storan B2 tidak mencukupi untuk menampung fail.[/bold"
          " red]"
      )
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
    console.print(
        f"[cyan]📁 Destinasi B2 Diselaraskan:[/cyan] [bold white]{b2_path}[/bold"
        " white]"
    )

    # 6. Pemindahan ke Baldi B2 Menggunakan Native Rclone
    upload_ok = storage.upload_file(temp_video_file, b2_target, b2_path)
    if not upload_ok:
      console.print(
          "[bold red]❌ Pemindahan ke B2 gagal. Proses dihentikan.[/bold red]"
      )
      sys.exit(1)

    # 7. Pendaftaran Metadata ke KUNCI TERASING: 'stremio:http:{imdb_id}'
    proxy_stream_url = db.build_proxy_stream_url(
        b2_target["bucket_name"], b2_path
    )
    resolution = detect_resolution(
        f"{media_name} {target_item.get('quality', '')}"
    )
    current_ts = int(time.time())

    new_stream_entry = {
        "title": raw_title,
        "resolution": resolution,
        "file_name": clean_filename,
        "file_path": b2_path,
        "size_bytes": file_size,
        "b2_account_index": b2_target["index"],
        "b2_bucket": b2_target["bucket_name"],
        "stream_url": proxy_stream_url,
        "info_hash": target_hash,
        "sha256": sha256_hash,
        "created_at": current_ts,
        "last_accessed": current_ts,
    }

    existing_streams = []
    if existing:
      if isinstance(existing, dict):
        if "streams" in existing and isinstance(existing["streams"], list):
          existing_streams = existing["streams"]
        elif existing.get("file_path"):
          existing_streams = [existing]
      elif isinstance(existing, list):
        existing_streams = existing

    merged_streams = [
        s
        for s in existing_streams
        if isinstance(s, dict) and s.get("info_hash", "").lower() != target_hash
    ]
    merged_streams.insert(0, new_stream_entry)

    payload_full = {
        "imdb_id": imdb_id,
        "title": raw_title,
        "resolution": resolution,
        "file_name": clean_filename,
        "file_path": b2_path,
        "size_bytes": file_size,
        "b2_account_index": b2_target["index"],
        "b2_bucket": b2_target["bucket_name"],
        "stream_url": proxy_stream_url,
        "info_hash": target_hash,
        "sha256": sha256_hash,
        "created_at": current_ts,
        "last_accessed": current_ts,
        "streams": merged_streams,
    }

    serialized = json.dumps(payload_full, ensure_ascii=False)
    db.execute_command(shard, "SET", f"stremio:http:{imdb_id}", serialized)

    console.print(
        Panel(
            "[bold green]🎉 VIDEO STRIM HTTP BERJAYA DIMUAT NAIK KE B2 &"
            " DIKEMAS KINI KE REDIS![/bold green]\n[cyan]Kunci Redis Terasing:"
            f" stremio:http:{imdb_id}[/cyan]\n[cyan]URL Proksi          :"
            f" {proxy_stream_url}[/cyan]",
            border_style="green",
        )
    )

  finally:
    if job_dir.exists():
      shutil.rmtree(job_dir, ignore_errors=True)
      console.print("[dim]🧹 Direktori sementara dibersihkan.[/dim]")


# ------------------------------------------------------------------------------
# 5. TITIK MASUK UTAMA
# ------------------------------------------------------------------------------
def main():
  parser = argparse.ArgumentParser(
      description="Dedicated HTTP Runner Engine (X11 Isolated)"
  )
  parser.add_argument("--hash", required=True, help="Pseudo-Hash")
  parser.add_argument(
      "--imdb",
      required=True,
      help="IMDb ID (cth: tt10687070 atau tt0944947:1:1)",
  )
  parser.add_argument(
      "--title", default="Video", help="Tajuk video atau nama pelepasan"
  )
  parser.add_argument(
      "--detect",
      action="store_true",
      help="Kekalkan keserasian jika dipanggil dengan --detect",
  )
  args = parser.parse_args()

  execute_http_job(args.hash, args.imdb, args.title)


if __name__ == "__main__":
  main()