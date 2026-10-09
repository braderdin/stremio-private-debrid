#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - PENGURUS PANGKALAN DATA SQLITE & SHARDING 25MB
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/02_db_manager.py
# CIRI:
# 1. Mod WAL & Synchronous Normal untuk operasi serentak tanpa 'database locked'.
# 2. Sharding Automatik: Menjana fail baru jika saiz fail aktif capai 25MB-35MB.
# 3. Semakan Dedplikasi Merentas Shard (O(1) imbasan terbalik dari fail terkini).
# 4. Pengendalian Status Kitar Hayat Media (completed, failed, stale, in_progress).
# ==============================================================================

import os
import sys
import time
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional, List
from rich.console import Console

# Penyelarasan modul konfigurasi
SCRAPE_DIR = Path(__file__).resolve().parent
if str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

import X00_scrape_config as cfg

console = Console()


class SQLiteShardedDBManager:
    def __init__(self):
        self.data_dir = cfg.DATA_DIR
        self.split_threshold = cfg.DB_SPLIT_THRESHOLD_BYTES
        self.base_name = cfg.DB_BASE_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema(self.get_active_db_path())

    def get_all_db_paths(self) -> List[Path]:
        """Menyenaraikan semua fail pecahan pangkalan data mengikut urutan nombor."""
        files = sorted(self.data_dir.glob(f"{self.base_name}_*.db"))
        if not files:
            default_path = self.data_dir / f"{self.base_name}_01.db"
            return [default_path]
        return files

    def get_active_db_path(self) -> Path:
        """
        Mengenal pasti fail SQLite untuk operasi tulis.
        Sekiranya fail terkini melebihi had ambang (25MB), jana fail siri baharu.
        """
        all_dbs = self.get_all_db_paths()
        latest_db = all_dbs[-1]

        if latest_db.exists():
            try:
                current_size = latest_db.stat().st_size
                if current_size >= self.split_threshold:
                    new_idx = len(all_dbs) + 1
                    new_db = self.data_dir / f"{self.base_name}_{new_idx:02d}.db"
                    console.print(
                        f"[yellow]⚠️ DB Semasa ({latest_db.name}) mencecah had {current_size / (1024*1024):.1f}MB. "
                        f"Membuka pecahan baharu: {new_db.name}[/yellow]"
                    )
                    self._ensure_schema(new_db)
                    return new_db
            except OSError:
                pass

        return latest_db

    def _get_connection(self, db_path: Path) -> sqlite3.Connection:
        """Membuka sambungan SQLite pantas dengan mod WAL."""
        conn = sqlite3.connect(str(db_path), timeout=20.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, db_path: Path):
        """Membina skema jadual media sekiranya pangkalan data baru dimulakan."""
        with self._get_connection(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraped_media (
                    imdb_id TEXT PRIMARY KEY,
                    base_imdb TEXT,
                    title TEXT,
                    year TEXT,
                    media_type TEXT,
                    season INTEGER DEFAULT 1,
                    episode INTEGER DEFAULT 1,
                    search_query TEXT,
                    stream_url TEXT,
                    info_hash TEXT,
                    b2_path TEXT,
                    b2_account_index INTEGER DEFAULT 1,
                    file_size INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'in_progress',
                    error_msg TEXT,
                    created_at INTEGER,
                    updated_at INTEGER
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON scraped_media(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_base_imdb ON scraped_media(base_imdb);")

    def get_media_record(self, imdb_id: str) -> Optional[Dict[str, Any]]:
        """
        Menyemak kewujudan rekod merentasi semua fail SQLite (dari yang terkini ke lama).
        Mengelakkan rekod bertindih walaupun data terbahagi kepada beberapa fail.
        """
        clean_id = imdb_id.strip()
        all_dbs = list(reversed(self.get_all_db_paths()))

        for db_file in all_dbs:
            if not db_file.exists():
                continue
            try:
                with self._get_connection(db_file) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM scraped_media WHERE imdb_id = ? LIMIT 1;", (clean_id,))
                    row = cur.fetchone()
                    if row:
                        data = dict(row)
                        data["_db_source"] = db_file.name
                        return data
            except sqlite3.Error:
                continue

        return None

    def is_completed(self, imdb_id: str) -> bool:
        """Menyemak status integriti fail media secara pantas."""
        record = self.get_media_record(imdb_id)
        if record and record.get("status") == "completed":
            return True
        return False

    def save_or_update_record(self, data: Dict[str, Any]) -> bool:
        """
        Menyimpan rekod baharu ke fail SQLite aktif atau mengemas kini fail asal jika wujud.
        """
        imdb_id = data["imdb_id"].strip()
        existing = self.get_media_record(imdb_id)
        current_ts = int(time.time())

        # Tentukan fail pangkalan data destinasi
        if existing and "_db_source" in existing:
            target_db = self.data_dir / existing["_db_source"]
        else:
            target_db = self.get_active_db_path()

        self._ensure_schema(target_db)

        fields = [
            "imdb_id", "base_imdb", "title", "year", "media_type",
            "season", "episode", "search_query", "stream_url",
            "info_hash", "b2_path", "b2_account_index", "file_size",
            "status", "error_msg", "created_at", "updated_at"
        ]

        payload = {
            "imdb_id": imdb_id,
            "base_imdb": data.get("base_imdb", imdb_id.split(":")[0]),
            "title": data.get("title", ""),
            "year": str(data.get("year", "")),
            "media_type": data.get("media_type", "movie"),
            "season": int(data.get("season", 1)),
            "episode": int(data.get("episode", 1)),
            "search_query": data.get("search_query", ""),
            "stream_url": data.get("stream_url", ""),
            "info_hash": data.get("info_hash", ""),
            "b2_path": data.get("b2_path", ""),
            "b2_account_index": int(data.get("b2_account_index", 1)),
            "file_size": int(data.get("file_size", 0)),
            "status": data.get("status", "in_progress"),
            "error_msg": data.get("error_msg", ""),
            "created_at": existing.get("created_at", current_ts) if existing else current_ts,
            "updated_at": current_ts
        }

        placeholders = ", ".join(["?"] * len(fields))
        columns = ", ".join(fields)
        values = [payload[f] for f in fields]

        try:
            with self._get_connection(target_db) as conn:
                conn.execute(
                    f"INSERT OR REPLACE INTO scraped_media ({columns}) VALUES ({placeholders});",
                    values
                )
            return True
        except sqlite3.Error as e:
            console.print(f"[bold red]❌ Ralat menulis rekod ke SQLite ({target_db.name}): {e}[/bold red]")
            return False

    def mark_as_stale(self, imdb_id: str, reason: str = "B2/Redis file evicted"):
        """Menandakan fail sebagai tidak sah sekiranya telah dilupuskan oleh sistem awan."""
        existing = self.get_media_record(imdb_id)
        if existing and "_db_source" in existing:
            target_db = self.data_dir / existing["_db_source"]
            try:
                with self._get_connection(target_db) as conn:
                    conn.execute("""
                        UPDATE scraped_media 
                        SET status = 'stale', error_msg = ?, updated_at = ?
                        WHERE imdb_id = ?;
                    """, (reason, int(time.time()), imdb_id.strip()))
                console.print(f"[dim yellow]ℹ️ Rekod {imdb_id} ditandakan sebagai 'stale' pada {target_db.name}.[/dim yellow]")
            except sqlite3.Error:
                pass


# Singleton DB Manager
db_manager = SQLiteShardedDBManager()


if __name__ == "__main__":
    console.print("[bold yellow]🧪 UJIAN PENGESAHAN MODUL 02_DB_MANAGER.PY[/bold yellow]")
    active_path = db_manager.get_active_db_path()
    console.print(f"📁 Fail DB Aktif    : [green]{active_path.name}[/green]")
    console.print(f"📦 Jumlah Fail DB   : {len(db_manager.get_all_db_paths())} fail")

    # Ujian Simpan Ringkas
    test_data = {
        "imdb_id": "tt0097576",
        "title": "The Miracle",
        "year": "1989",
        "media_type": "movie",
        "status": "in_progress",
        "search_query": "The Miracle 奇蹟"
    }
    db_manager.save_or_update_record(test_data)
    rec = db_manager.get_media_record("tt0097576")
    console.print(f"✅ Ujian Semakan Rekod : [cyan]{rec['title']} ({rec['status']}) dari {rec['_db_source']}[/cyan]")