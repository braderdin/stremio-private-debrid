#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - SEEDER LISTER (02_seeder_lister.py)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/02_seeder_lister.py
# CIRI: DUAL ENGINE (APIBAY + TORRENTIO) + SOURCE TRACKER + REDIS AUTO-CACHE
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

# Pemuatan dinamik modul 01_redis_client (kalis ralat import nombor)
try:
    _redis_client_mod = importlib.import_module("01_redis_client")
    db_v2 = getattr(_redis_client_mod, "db_v2")
except Exception as e:
    console.print(f"[bold red]❌ Gagal mengimport 01_redis_client.py: {e}[/bold red]")
    sys.exit(1)


def detect_quality(name: str) -> str:
    """Mengesan resolusi video berdasarkan nama pelepasan torrent."""
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    return "HD"


def parse_torrentio_title(raw_title: str) -> Dict[str, Any]:
    """Mengekstrak kualiti, saiz, dan bilangan seeders daripada teks Torrentio."""
    lines = raw_title.split("\n")
    release_name = lines[0].strip()
    extra_info = lines[1] if len(lines) > 1 else ""

    seeds_match = re.search(r"👤\s*(\d+)", extra_info)
    seeds = int(seeds_match.group(1)) if seeds_match else 0

    size_match = re.search(r"💾\s*([\d\.]+)\s*(GB|MB)", extra_info, re.IGNORECASE)
    size_bytes = 0
    if size_match:
        val = float(size_match.group(1))
        unit = size_match.group(2).upper()
        size_bytes = int(val * (1024**3 if unit == "GB" else 1024**2))

    return {
        "name": release_name,
        "seeders": seeds,
        "size": size_bytes,
        "quality": detect_quality(release_name + " " + raw_title)
    }


def fetch_apibay_torrents(imdb_id: str) -> List[Dict[str, Any]]:
    """Menyaring torrent daripada Apibay (ThePirateBay) dan menandakan sumber Apibay."""
    url = f"https://apibay.org/q.php?q={imdb_id}"
    out = []
    for profile in ["safari15_5", "chrome120"]:
        try:
            resp = requests.get(
                url,
                impersonate=profile,
                timeout=10,
                headers={"Accept": "application/json", "Referer": "https://thepiratebay.org/"}
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and data[0].get("name") != "No results returned":
                    for item in data:
                        h = (item.get("info_hash") or "").strip().lower()
                        name = (item.get("name") or "").strip()
                        if len(h) == 40 and name:
                            out.append({
                                "name": name,
                                "info_hash": h,
                                "seeders": int(item.get("seeders", 0)),
                                "size": int(item.get("size", 0)),
                                "quality": detect_quality(name),
                                "source": "Apibay"
                            })
                    break
        except Exception:
            continue
    return out


def fetch_torrentio_torrents(imdb_id: str) -> List[Dict[str, Any]]:
    """Menyaring torrent daripada Torrentio Scraper API dan menandakan sumber Torrentio."""
    url = f"https://torrentio.strem.fun/stream/movie/{imdb_id}.json"
    out = []
    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        if resp.status_code == 200:
            streams = resp.json().get("streams", [])
            for s in streams:
                h = (s.get("infoHash") or "").strip().lower()
                if len(h) == 40:
                    parsed = parse_torrentio_title(s.get("title", ""))
                    out.append({
                        "name": parsed["name"],
                        "info_hash": h,
                        "seeders": parsed["seeders"],
                        "size": parsed["size"],
                        "quality": parsed["quality"],
                        "source": "Torrentio"
                    })
    except Exception:
        pass
    return out


def process_and_save_seeder_list(imdb_id: str, fallback_title: str = "") -> bool:
    base_id = imdb_id.split(":")[0]
    console.print(f"[bold cyan]🔍 V2 MULTI-LISTER: Mengimbas Apibay & Torrentio untuk {base_id}...[/bold cyan]")

    # 1. Kutip data daripada kedua-dua penyedia
    apibay_items = fetch_apibay_torrents(base_id)
    torrentio_items = fetch_torrentio_torrents(base_id)
    console.print(f"[cyan]📦 Ditemui:[/cyan] Apibay: {len(apibay_items)} | Torrentio: {len(torrentio_items)}")

    all_raw = apibay_items + torrentio_items
    if not all_raw:
        console.print("[bold red]❌ Tiada torrent ditemui dari mana-mana penyedia![/bold red]")
        return False

    # 2. Penapisan Saiz Selamat B2 & Nyah-Duplikasi InfoHash
    MIN_BYTES = 400 * 1024 * 1024          # 400 MB
    MAX_BYTES = 8.5 * 1024 * 1024 * 1024    # 8.5 GB (Kunci selamat baldi B2)

    unique_torrents: Dict[str, Dict[str, Any]] = {}
    for item in all_raw:
        h = item["info_hash"]
        seeds = item["seeders"]
        sz = item["size"]

        if seeds < 1 or sz < MIN_BYTES or sz > MAX_BYTES:
            continue

        # Pastikan medan source sentiasa ada
        src = item.get("source", "Apibay" if "Apibay" in item.get("source", "") else "Torrentio")
        item["source"] = src

        # Jika torrent wujud dari kedua-dua tempat, simpan rekod dengan seeder tertinggi
        if h not in unique_torrents or seeds > unique_torrents[h]["seeders"]:
            unique_torrents[h] = item

    if not unique_torrents:
        console.print("[bold red]❌ Semua torrent ditolak (0 seeder atau saiz di luar julat 400MB - 8.5GB)![/bold red]")
        return False

    # 3. Susun mengikut seeders terbanyak
    combined_list = list(unique_torrents.values())
    combined_list.sort(key=lambda x: x["seeders"], reverse=True)
    top_torrents = combined_list[:40]

    # 4. Simpan ke Redis V2 (TTL: 24 Jam)
    saved = db_v2.save_torrent_list(base_id, top_torrents, ttl_seconds=86400)

    if saved:
        table = Table(title=f"📋 Senarai Seeder Gabungan bagi {base_id} ({len(top_torrents)} pilihan)", border_style="green")
        table.add_column("No", justify="center", style="cyan")
        table.add_column("Sumber", justify="center", style="yellow")
        table.add_column("Kualiti", justify="center", style="magenta")
        table.add_column("Saiz", style="white")
        table.add_column("Seeders", justify="center", style="green")
        table.add_column("Tajuk Torrent", style="dim")

        for idx, t in enumerate(top_torrents[:30], 1):
            sz_str = f"{t['size'] / (1024*1024*1024):.2f} GB" if t['size'] >= 1024**3 else f"{t['size'] / (1024*1024):.1f} MB"
            src_display = "⚙️ Torrentio" if t.get("source") == "Torrentio" else "🏴‍☠️ Apibay"
            table.add_row(str(idx), src_display, t["quality"], sz_str, str(t["seeders"]), t["name"][:45])

        console.print(table)
        console.print(f"[bold green]✅ Berjaya menyimpan {len(top_torrents)} pilihan ke Redis Shard! (TTL: 24 Jam)[/bold green]")
        return True

    console.print("[bold red]❌ Gagal mengemas kini pangkalan data Redis![/bold red]")
    return False


def main():
    parser = argparse.ArgumentParser(description="V2 Multi-Provider Torrent Seeder Lister")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt1204977)")
    parser.add_argument("--title", default="", help="Tajuk sandaran filem")
    args = parser.parse_args()

    success = process_and_save_seeder_list(args.imdb, args.title)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()