#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - MODUL KONFIGURASI PUSAT & PERSEKITARAN
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/00_scrape_config.py
# ==============================================================================

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console

console = Console()

# 1. Penyelarasan Laluan Folder Projek
SCRAPE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRAPE_DIR.parent
DATA_DIR = SCRAPE_DIR / "data"
TEMP_DIR = SCRAPE_DIR / "temp"
DOWNLOAD_DIR = PROJECT_ROOT / "exp_download"
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
LIVE_ENGINE_V3_DIR = PROJECT_ROOT / "live_engine_v3"

# Pastikan folder operasi wujud secara automatik
for folder in [DATA_DIR, TEMP_DIR, DOWNLOAD_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Tambah laluan import modul sistem utama
for p in [SCRAPE_DIR, PROJECT_ROOT, LIVE_ENGINE_DIR, LIVE_ENGINE_V3_DIR]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

# 2. Muat Turun Pembolehubah Persekitaran (.env.local atau Sistem)
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
if ENV_LOCAL_PATH.exists():
    load_dotenv(ENV_LOCAL_PATH)
else:
    load_dotenv()

# 3. Kunci API Metadata (TMDB & Cinemeta)
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_READ_TOKEN = os.getenv("TMDB_READ_TOKEN", "").strip()

# 4. Laman Sasaran & Parameter Operasi Web
# Pengguna boleh menetapkan pautan manual di sini atau melalui pembolehubah sistem
TARGET_WEB_URL = os.getenv("TARGET_WEB_URL", "https://tv.lk21official.us").strip()

# 5. Kawalan Pintar Enjin Pelayar (Camoufox Headless Toggle)
# Jika dikesan dalam GitHub Actions (CI=true), sentiasa paksa Headless = True
IS_CI = os.getenv("CI", "false").lower() in ("true", "1")
FORCE_HEADLESS_ENV = os.getenv("BROWSER_HEADLESS", "").lower()

if IS_CI:
    BROWSER_HEADLESS = True
elif FORCE_HEADLESS_ENV in ("true", "1"):
    BROWSER_HEADLESS = True
elif FORCE_HEADLESS_ENV in ("false", "0"):
    BROWSER_HEADLESS = False
else:
    # Default untuk ujian komputer tempatan (Linux Mint): Paparan visual aktif (GUI ON)
    BROWSER_HEADLESS = False

USER_AGENT_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
)

# 6. Had & Tetapan Sharding SQLite Pintar
DB_BASE_NAME = "scrape_media"
DB_SPLIT_THRESHOLD_BYTES = int(os.getenv("DB_SPLIT_THRESHOLD_MB", "25")) * 1024 * 1024  # Had: 25 MB


def get_active_db_path() -> Path:
    """Mencari atau mencipta fail SQLite aktif mengikut had saiz peruntukan."""
    existing_dbs = sorted(DATA_DIR.glob(f"{DB_BASE_NAME}_*.db"))
    if not existing_dbs:
        return DATA_DIR / f"{DB_BASE_NAME}_01.db"

    latest_db = existing_dbs[-1]
    try:
        if latest_db.stat().st_size >= DB_SPLIT_THRESHOLD_BYTES:
            next_idx = len(existing_dbs) + 1
            new_db_path = DATA_DIR / f"{DB_BASE_NAME}_{next_idx:02d}.db"
            return new_db_path
    except OSError:
        pass

    return latest_db


if __name__ == "__main__":
    console.print("[bold yellow]🧪 PENGESAHAN MODUL 00_SCRAPE_CONFIG[/bold yellow]")
    console.print(f"📁 Folder Scrape     : [cyan]{SCRAPE_DIR}[/cyan]")
    console.print(f"📁 Folder Data (.db) : [cyan]{DATA_DIR}[/cyan]")
    console.print(f"📁 Folder Download   : [cyan]{DOWNLOAD_DIR}[/cyan]")
    console.print(f"🌐 Mod Pelayar       : [{'bold red]Headless (Latar Belakang)' if BROWSER_HEADLESS else 'bold green]GUI Aktif (Tetingkap Terbuka)'}[/]")
    console.print(f"🔑 TMDB Token/Key    : [{'green]Tersedia[/green]' if (TMDB_API_KEY or TMDB_READ_TOKEN) else '[yellow]Tiada (Guna Cinemeta Sahaja)[/yellow]'}")
    console.print(f"🗄️ Fail DB Semasa    : [green]{get_active_db_path().name}[/green]")