#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - JAMBATAN INTEGRASI B2, REDIS & SQLITE
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X05_sync_bridge.py
# CIRI:
# 1. Mengimport 100% Modul Asal: 00_config, X01_series_redis, X02_series_b2storage.
# 2. Semakan Status Kewujudan Rentas Awan (Redis Shards & B2 Usage).
# 3. Penamaan Bersih B2 Mengikut Piawaian Sistem: media/{prefix}.{hash}.{name}.ext
# 4. Pelupusan LRU Automatik jika 20 Akaun B2 Penuh sebelum Muat Naik.
# 5. Penyegerakan Rekod Dua Hala ke SQLite Tempatan (X02_db_manager).
# ==============================================================================

import os
import re
import sys
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from rich.console import Console
from rich.panel import Panel

# 1. Penyelarasan Laluan Direktori Sistem Asal
SCRAPE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRAPE_DIR.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
LIVE_ENGINE_V3_DIR = PROJECT_ROOT / "live_engine_v3"

for p in [SCRAPE_DIR, PROJECT_ROOT, LIVE_ENGINE_DIR, LIVE_ENGINE_V3_DIR]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

import X00_scrape_config as cfg
from X02_db_manager import db_manager

# 2. Import Modul Singleton Asal dari live_engine_v3
try:
    from X01_series_redis import series_db
    from X02_series_b2storage import series_storage
except ImportError as e:
    console.print(f"[bold red]❌ Ralat import modul asal live_engine_v3: {e}[/bold red]")
    sys.exit(1)

console = Console()


def calculate_sha256(filepath: Path) -> str:
    """Mengira hash fail video menggunakan penimbal 4MB."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


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
    Format Siri : media/{base_id}.S01E01.{hash[:8]}.{stem}.{ext}
    Format Filem: media/{imdb_id}.{hash[:8]}.{stem}.{ext}
    """
    p = Path(original_filename)
    ext = p.suffix.lower() if p.suffix else ".mp4"
    stem = p.stem
    short_hash = (info_hash or "webscrape")[:8].lower()

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


class SyncBridgeManager:
    @staticmethod
    def is_stream_active_in_cloud(imdb_id: str, target_hash: str = "") -> bool:
        """
        Menyemak sama ada metadata video masih aktif di Redis.
        Jika data telah dilupuskan (evicted), tanda status SQLite sebagai 'stale'.
        """
        existing = series_db.get_stream_metadata(imdb_id)
        if not existing:
            if db_manager.is_completed(imdb_id):
                db_manager.mark_as_stale(imdb_id, reason="Hilang dari Redis LRU")
            return False

        if target_hash:
            clean_hash = target_hash.lower().strip()
            streams = existing.get("streams", []) if isinstance(existing, dict) else []
            has_hash = any(
                isinstance(s, dict) and s.get("info_hash", "").lower() == clean_hash
                for s in streams
            )
            if not has_hash:
                return False

        return True

    @staticmethod
    def sync_local_video_to_b2(
        local_video_file: Path,
        meta_info: Dict[str, Any],
        resolution: str = "1080p",
        custom_hash: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Memindahkan fail video tempatan ke Backblaze B2 dan menyegerakkan
        metadata rasmi ke Upstash Redis Shards serta SQLite tempatan.
        """
        if not local_video_file.exists():
            console.print(f"[bold red]❌ Fail video fizikal tidak wujud: {local_video_file}[/bold red]")
            return None

        imdb_id = meta_info["imdb_id"]
        is_series = meta_info.get("is_series", False)
        season = meta_info.get("season", 1)
        episode = meta_info.get("episode", 1)
        title = meta_info.get("title", local_video_file.stem)

        file_size = local_video_file.stat().st_size
        sha256_val = calculate_sha256(local_video_file)
        target_hash = (custom_hash or sha256_val[:40]).lower().strip()

        console.print(Panel.fit(
            f"[bold cyan]☁️ MEMULAKAN PENYEGERAKAN VIDEO KE B2 & REDIS[/bold cyan]\n"
            f"ID IMDb   : [white]{imdb_id}[/white]\n"
            f"Tajuk     : [white]{title}[/white]\n"
            f"Saiz Fail : [green]{file_size / (1024*1024):.2f} MB[/green]\n"
            f"Resolusi  : [cyan]{resolution}[/cyan]",
            border_style="cyan"
        ))

        # 1. Peruntukan Ruang B2 dengan Pelupusan LRU jika 20 Akaun Penuh
        b2_target = series_storage.allocate_storage_with_eviction(file_size, series_db)
        if not b2_target:
            console.print("[bold red]❌ Gagal memperuntukkan ruang B2 (semua akaun penuh & LRU gagal).[/bold red]")
            return None

        # 2. Penyeragaman Nama Destinasi B2
        clean_filename, b2_path = sanitize_b2_path(
            imdb_id=imdb_id,
            info_hash=target_hash,
            original_filename=local_video_file.name,
            is_series=is_series,
            season=season,
            episode=episode
        )

        # 3. Pemindahan Fizikal Menggunakan Native Rclone B2
        upload_ok = series_storage.upload_file(local_video_file, b2_target, b2_path)
        if not upload_ok:
            console.print("[bold red]❌ Pemindahan Rclone ke B2 gagal.[/bold red]")
            return None

        # 4. Pendaftaran Metadata ke Shard Upstash Redis
        metadata_payload = {
            "title": title,
            "resolution": resolution,
            "file_name": clean_filename,
            "file_path": b2_path,
            "size_bytes": file_size,
            "b2_account_index": b2_target["index"],
            "b2_bucket": b2_target["bucket_name"],
            "info_hash": target_hash,
            "sha256": sha256_val,
            "created_at": int(time.time()),
        }

        redis_ok = series_db.set_stream_metadata(imdb_id, metadata_payload)
        if not redis_ok:
            console.print("[bold red]❌ Pendaftaran metadata ke Upstash Redis gagal.[/bold red]")

        # 5. Kemas Kini Rekod ke SQLite Tempatan
        stream_url = series_db.build_proxy_stream_url(b2_target["bucket_name"], b2_path)
        db_record = {
            "imdb_id": imdb_id,
            "base_imdb": meta_info.get("base_imdb", imdb_id.split(":")[0]),
            "title": title,
            "year": str(meta_info.get("year", "")),
            "media_type": meta_info.get("media_type", "movie"),
            "season": season,
            "episode": episode,
            "stream_url": stream_url,
            "info_hash": target_hash,
            "b2_path": b2_path,
            "b2_account_index": b2_target["index"],
            "file_size": file_size,
            "status": "completed",
            "error_msg": None
        }
        db_manager.save_or_update_record(db_record)

        console.print(Panel(
            f"[bold green]🎉 PENYEGERAKAN BERJAYA SEPENUHNYA![/bold green]\n"
            f"[cyan]Kunci Redis  : stremio:{imdb_id}[/cyan]\n"
            f"[cyan]URL Proksi   : {stream_url}[/cyan]\n"
            f"[cyan]Destinasi B2 : {b2_path}[/cyan]",
            border_style="green"
        ))

        return {
            "status": "success",
            "stream_url": stream_url,
            "b2_path": b2_path,
            "info_hash": target_hash
        }


# Singleton Sync Bridge
sync_bridge = SyncBridgeManager()


if __name__ == "__main__":
    console.print("[bold yellow]🧪 UJIAN PENGESAHAN MODUL X05_SYNC_BRIDGE.PY[/bold yellow]")
    test_id = "tt0097576"
    status_active = sync_bridge.is_stream_active_in_cloud(test_id)
    console.print(f"Semakan Status Awan ({test_id}): [{'green]Aktif di Redis[/green]' if status_active else '[yellow]Tiada di Redis[/yellow]'}")