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
from typing import Dict, Any, Optional

from rich.console import Console
from rich.panel import Panel

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
    tr = "&".join([f"tr={t}" for t in PUBLIC_TRACKERS])
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={name}&{tr}"


def calculate_sha256(filepath: Path) -> str:
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def run_aria2c(magnet: str, out_dir: Path, file_idx: int) -> Optional[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c", "--seed-time=0", "--summary-interval=5", "--max-connection-per-server=16",
        "--split=16", "--min-split-size=1M", "--file-allocation=none", "--bt-stop-timeout=600",
        f"--dir={str(out_dir)}"
    ]
    if file_idx > 0:
        cmd.append(f"--select-file={file_idx + 1}")
    cmd.append(magnet)

    console.print(f"[cyan]🚀 aria2c memulakan muat turun (Indeks Fail: {file_idx})...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        console.print(f"[bold red]❌ aria2c tamat dengan ralat:[/bold red]\n{res.stdout[-400:]}")
        return None

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".m4v"}
    candidates = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in video_exts]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
    return candidates[0]


def upload_rclone(local_file: Path, b2_acc: Dict[str, Any], dest_path: str) -> bool:
    env = os.environ.copy()
    r_name = "TEMP_B2_RUNNER"
    env[f"RCLONE_CONFIG_{r_name}_TYPE"] = "b2"
    env[f"RCLONE_CONFIG_{r_name}_ACCOUNT"] = b2_acc["key_id"]
    env[f"RCLONE_CONFIG_{r_name}_KEY"] = b2_acc["app_key"]
    env[f"RCLONE_CONFIG_{r_name}_ENDPOINT"] = b2_acc["endpoint"]

    target = f"{r_name}:{b2_acc['bucket_name']}/{dest_path.lstrip('/')}"
    cmd = ["rclone", "copyto", str(local_file), target, "--transfers=4", "--fast-list", "--stats=10s"]

    console.print(f"[cyan]☁️ Memindahkan ke B2 Bucket: #{b2_acc['index']} ({b2_acc['bucket_name']})...[/cyan]")
    res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return res.returncode == 0


def execute_job(info_hash: str, imdb_id: str, file_idx: int, raw_title: str):
    console.print(Panel.fit(
        f"[bold cyan]⚡ DEBRID RUNNER ENGINE: MEMPROSES TUGAS[/bold cyan]\n"
        f"IMDb: {imdb_id} | Hash: {info_hash}\nTajuk: {raw_title}",
        border_style="cyan"
    ))

    # 1. Semak Cache
    existing = db.get_stream_metadata(imdb_id)
    if existing and existing.get("b2_bucket") and existing.get("file_path"):
        console.print("[bold green]✨ Fail sudah siap di B2! Selesai.[/bold green]")
        return

    # 2. Pengendalian Hash AUTO (Strategi A Fallback)
    if info_hash.strip().upper() == "AUTO" or len(info_hash.strip()) != 40:
        try:
            resolver = importlib.import_module("04_resolver")
            resolved = resolver.resolve_fallback(imdb_id, raw_title)
            if not resolved:
                console.print("[bold red]❌ Tiada torrent ditemui via fallback resolver![/bold red]")
                return
            info_hash = resolved["info_hash"]
            raw_title = resolved["name"]
        except Exception as e:
            console.print(f"[bold red]❌ Gagal memanggil modul 04_resolver: {e}[/bold red]")
            return

    # 3. Muat Turun & Pindah B2
    job_dir = TEMP_DIR / f"job_{info_hash[:10]}_{int(time.time())}"
    magnet_url = build_magnet(info_hash, raw_title)

    try:
        video_file = run_aria2c(magnet_url, job_dir, file_idx)
        if not video_file:
            console.print("[bold red]❌ aria2c gagal menyedut fail.[/bold red]")
            return

        file_size = video_file.stat().st_size
        sha256_hash = calculate_sha256(video_file)

        b2_target = storage.allocate_storage_with_eviction(file_size, db)
        if not b2_target:
            console.print("[bold red]❌ Storan B2 tidak mencukupi.[/bold red]")
            return

        clean_name = f"{imdb_id.replace(':', '_')}_{video_file.name}"
        b2_path = f"media/{clean_name}"
        if not upload_rclone(video_file, b2_target, b2_path):
            console.print("[bold red]❌ Rclone gagal muat naik.[/bold red]")
            return

        metadata = {
            "title": raw_title,
            "resolution": "1080p" if "1080" in raw_title else "HD",
            "file_name": clean_name,
            "file_path": b2_path,
            "size_bytes": file_size,
            "b2_account_index": b2_target["index"],
            "b2_bucket": b2_target["bucket_name"],
            "info_hash": info_hash.lower(),
            "sha256": sha256_hash,
            "created_at": int(time.time()),
        }
        db.set_stream_metadata(imdb_id, metadata)
        console.print(Panel(f"[bold green]🎉 SELESAI DISIMPAN DI B2![/bold green]\n{metadata.get('stream_url')}", border_style="green"))

    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash", required=True)
    parser.add_argument("--imdb", required=True)
    parser.add_argument("--idx", default="0")
    parser.add_argument("--title", default="Video")
    args = parser.parse_args()

    execute_job(args.hash, args.imdb, int(args.idx or 0), args.title)