#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - SEEDER LISTER ENGINE (02_seeder_lister.py)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/02_seeder_lister.py
# CIRI: FAST SCRAPE (SAFARI15_5 BYPASS) + SORT BY SEEDERS + AUTO-SAVE REDIS
# ==============================================================================

import re
import sys
import argparse
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, List

from curl_cffi import requests
from rich.console import Console
from rich.table import Table

console = Console()

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Pemuatan dinamik modul 01_redis_client (kalis ralat angka di hadapan nama fail)
try:
    _redis_client_mod = importlib.import_module("01_redis_client")
    db_v2 = getattr(_redis_client_mod, "db_v2")
except Exception as e:
    console.print(f"[bold red]❌ Gagal mengimport 01_redis_client.py: {e}[/bold red]")
    sys.exit(1)


def detect_quality(name: str) -> str:
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    return "HD"


def fetch_apibay_raw(query_param: str) -> List[Dict[str, Any]]:
    """Menghubungi apibay.org dengan perlindungan bypass TLS safari15_5."""
    url = f"https://apibay.org/q.php?q={query_param}"
    profiles = ["safari15_5", "chrome120", "chrome110"]

    for profile in profiles:
        try:
            resp = requests.get(
                url,
                impersonate=profile,
                timeout=12,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://thepiratebay.org/",
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0 and data[0].get("name") != "No results returned":
                    return data
        except Exception:
            continue
    return []


def get_movie_title_from_cinemeta(imdb_id: str) -> str:
    """Mendapatkan tajuk sandaran daripada Cinemeta sekiranya carian ID tiada hasil."""
    base_id = imdb_id.split(":")[0]
    try:
        url = f"https://v3-cinemeta.strem.io/meta/movie/{base_id}.json"
        res = requests.get(url, impersonate="chrome120", timeout=8)
        if res.status_code == 200:
            meta = res.json().get("meta", {})
            name = meta.get("name", "")
            year = str(meta.get("year", "")).split("–")[0].split("-")[0].strip()
            if name:
                return f"{name} {year}".strip()
    except Exception:
        pass
    return ""


def process_and_save_seeder_list(imdb_id: str, fallback_title: str = "") -> bool:
    console.print(f"[bold cyan]🔍 V2 LISTER: Memulakan pengekstrakan senarai torrent untuk {imdb_id}...[/bold cyan]")
    base_id = imdb_id.split(":")[0]

    # Peringkat 1: Carian tepat menggunakan IMDb ID
    raw_results = fetch_apibay_raw(base_id)

    # Peringkat 2: Carian sandaran teks jika ID kosong
    if not raw_results:
        search_title = get_movie_title_from_cinemeta(base_id) or fallback_title
        search_title = re.sub(r"[^\w\s]", " ", search_title).strip()
        if search_title:
            console.print(f"[yellow]🔄 ID kosong, mencuba carian kata kunci: '{search_title}'...[/yellow]")
            raw_results = fetch_apibay_raw(search_title)

    if not raw_results:
        console.print("[bold red]❌ Tiada torrent ditemui di Apibay untuk filem ini![/bold red]")
        return False

    valid_list = []
    MIN_BYTES = 400 * 1024 * 1024       # 400 MB
    MAX_BYTES = 8.5 * 1024 * 1024 * 1024  # 8.5 GB (maksimum selamat untuk B2)

    for item in raw_results:
        h = (item.get("info_hash") or "").strip().lower()
        name = (item.get("name") or "").strip()
        if not h or len(h) != 40 or name == "No results returned":
            continue

        try:
            seeds = int(item.get("seeders", 0))
            sz = int(item.get("size", 0))
        except ValueError:
            continue

        # Wajib sekurang-kurangnya 1 seeder dan saiz munasabah
        if seeds < 1 or sz < MIN_BYTES or sz > MAX_BYTES:
            continue

        valid_list.append({
            "name": name,
            "info_hash": h,
            "seeders": seeds,
            "size": sz,
            "quality": detect_quality(name),
        })

    if not valid_list:
        console.print("[bold red]❌ Semua torrent yang ditemui mempunyai 0 seeder atau saiz di luar julat![/bold red]")
        return False

    # Susun mengikut jumlah seeders tertinggi (Pilihan terbaik sentiasa di atas)
    valid_list.sort(key=lambda x: x["seeders"], reverse=True)
    top_torrents = valid_list[:35]

    # Simpan ke Redis V2 (TTL: 24 Jam)
    saved = db_v2.save_torrent_list(base_id, top_torrents, ttl_seconds=86400)

    if saved:
        table = Table(title=f"📋 Senarai Seeder Teratas bagi {base_id} ({len(top_torrents)} pilihan)", border_style="green")
        table.add_column("No", justify="center", style="cyan")
        table.add_column("Kualiti", style="magenta")
        table.add_column("Saiz", style="white")
        table.add_column("Seeders", style="green")
        table.add_column("Tajuk Torrent", style="dim")

        for idx, t in enumerate(top_torrents[:35], 1):
            sz_str = f"{t['size'] / (1024*1024*1024):.2f} GB" if t['size'] >= 1024**3 else f"{t['size'] / (1024*1024):.1f} MB"
            table.add_row(str(idx), t["quality"], sz_str, str(t["seeders"]), t["name"][:50])

        console.print(table)
        console.print(f"[bold green]✅ Berjaya menyimpan senarai ke Redis Shard! (TTL: 2 Jam)[/bold green]")
        return True

    console.print("[bold red]❌ Gagal menyimpan senarai ke Redis![/bold red]")
    return False


def main():
    parser = argparse.ArgumentParser(description="V2 Fast Torrent Seeder Lister")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt1204977)")
    parser.add_argument("--title", default="", help="Tajuk sandaran filem")
    args = parser.parse_args()

    success = process_and_save_seeder_list(args.imdb, args.title)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()