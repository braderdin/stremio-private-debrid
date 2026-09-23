#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - SERIES & MOVIE SEEDER LISTER
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X03_series_seeder_lister.py
# CIRI:
# 1. Dwi-Enjin Pintar: Torrentio (Multi-line / Season Pack) + Apibay (Cinemeta Query).
# 2. Pengesahan Pustaka Guessit untuk memastikan episod tepat tanpa ralat regex.
# 3. Penapisan Saiz Siling: < 5.0 GB bagi Siri TV (30MB - 5GB), < 8.5 GB bagi Filem.
# 4. Tangkapan 'file_idx' daripada Season Pack untuk muat turun aria2c bersasar.
# 5. Simpan automatik ke Shard Upstash Redis yang sepadan (TTL: 24 Jam).
# ==============================================================================

import re
import sys
import argparse
import importlib
from urllib.parse import quote_plus, unquote
from pathlib import Path
from typing import Dict, Any, List, Optional

from curl_cffi import requests
from guessit import guessit
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# 1. Konfigurasi Laluan & Import Dinamik Modul Redis
CURRENT_DIR = Path(__file__).resolve().parent
V2_DIR = CURRENT_DIR.parent / "live_engine_v2"

# Utamakan folder v3, sokong fallback ke v2 jika modul db berada di v2
for p in [CURRENT_DIR, V2_DIR]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _redis_client_mod = importlib.import_module("X01_series_redis")
    db_v2 = getattr(_redis_client_mod, "series_db")
except Exception as e:
    console.print(f"[bold red]❌ Gagal mengimport modul X01_series_redis: {e}[/bold red]")
    sys.exit(1)


def detect_quality(name: str) -> str:
    """Mengesan resolusi video berdasarkan teks nama torrent."""
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    return "HD"


def fetch_series_title_from_cinemeta(imdb_id: str) -> str:
    """Mendapatkan tajuk rasmi siri melalui Stremio Cinemeta API."""
    base_id = imdb_id.split(":")[0]
    url = f"https://v3-cinemeta.strem.io/meta/series/{base_id}.json"
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            name = data.get("meta", {}).get("name", "")
            if name:
                return name
    except Exception:
        pass
    return base_id


def parse_torrentio_stream(stream: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Mengekstrak maklumat daripada baris respons Torrentio.
    Menyokong keluaran Single Episode (2 baris) dan Season Pack (3 baris).
    """
    raw_title = stream.get("title", "")
    info_hash = (stream.get("infoHash") or "").strip().lower()
    file_idx = stream.get("fileIdx", 0)

    if len(info_hash) != 40 or not raw_title:
        return None

    lines = [l.strip() for l in raw_title.split("\n") if l.strip()]

    seeds = 0
    size_bytes = 0
    source_site = "Torrentio"

    for line in lines:
        seeds_match = re.search(r"👤\s*([\d,]+)", line)
        if seeds_match:
            seeds = int(seeds_match.group(1).replace(",", ""))

        size_match = re.search(r"💾\s*([\d\.]+)\s*(GB|MB|KB)", line, re.IGNORECASE)
        if size_match:
            val = float(size_match.group(1))
            unit = size_match.group(2).upper()
            if unit == "GB":
                size_bytes = int(val * 1024**3)
            elif unit == "MB":
                size_bytes = int(val * 1024**2)
            elif unit == "KB":
                size_bytes = int(val * 1024)

        if "⚙️" in line or "🌐" in line:
            src_m = re.search(r"[⚙️🌐]\s*([\w\+\-]+)", line)
            if src_m:
                source_site = src_m.group(1)

    # Jika Season Pack (>= 3 baris), gunakan nama fail episod di baris ke-2
    if len(lines) >= 3:
        release_name = f"{lines[1]} [{lines[0]}]"
    else:
        release_name = lines[0] if lines else "Torrent"

    stream_name = stream.get("name", "")
    quality = detect_quality(stream_name + " " + raw_title)

    return {
        "name": release_name,
        "info_hash": info_hash,
        "seeders": seeds,
        "size": size_bytes,
        "quality": quality,
        "source": source_site,
        "file_idx": int(file_idx or 0),
    }


def fetch_torrentio_torrents(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    """Mengambil torrent daripada Torrentio mengikut kategori."""
    endpoint_type = "series" if is_series else "movie"
    url = f"https://torrentio.strem.fun/stream/{endpoint_type}/{target_id}.json"

    console.print(f"[dim]📡 Panggilan Torrentio: {url}[/dim]")
    out = []
    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        if resp.status_code == 200:
            streams = resp.json().get("streams", [])
            for s in streams:
                parsed = parse_torrentio_stream(s)
                if parsed:
                    out.append(parsed)
    except Exception as e:
        console.print(f"[bold red]Ralat Torrentio:[/bold red] {e}")
    return out


def fetch_apibay_torrents(
    query_str: str,
    is_series: bool = False,
    target_season: int = 1,
    target_episode: int = 1,
) -> List[Dict[str, Any]]:
    """Menyaring torrent daripada Apibay dengan pengesahan pintar guessit."""
    url = f"https://apibay.org/q.php?q={quote_plus(query_str)}"
    console.print(f"[dim]🏴‍☠️ Panggilan Apibay: {url}[/dim]")
    out = []

    for profile in ["safari15_5", "chrome120"]:
        try:
            resp = requests.get(
                url,
                impersonate=profile,
                timeout=10,
                headers={"Accept": "application/json", "Referer": "https://thepiratebay.org/"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and data[0].get("name") != "No results returned":
                    for item in data:
                        h = (item.get("info_hash") or "").strip().lower()
                        name = (item.get("name") or "").strip()
                        seeds = int(item.get("seeders", 0))
                        size = int(item.get("size", 0))

                        if len(h) != 40 or not name:
                            continue

                        # Sahkan episod menggunakan guessit bagi siri TV
                        if is_series:
                            guess = guessit(name)
                            s_guess = guess.get("season")
                            e_guess = guess.get("episode")

                            ep_match = False
                            if isinstance(e_guess, list):
                                ep_match = target_episode in e_guess
                            elif e_guess is not None:
                                ep_match = (e_guess == target_episode)

                            # Sandaran regex lembut jika guessit terlepas pandang
                            if not ep_match:
                                s_pat = rf"(?i)\b[Ss]0*{target_season}[\s.\-_]*[Ee]0*{target_episode}\b"
                                if not re.search(s_pat, name):
                                    continue

                        out.append({
                            "name": name,
                            "info_hash": h,
                            "seeders": seeds,
                            "size": size,
                            "quality": detect_quality(name),
                            "source": "Apibay",
                            "file_idx": 0,
                        })
                    break
        except Exception:
            continue
    return out


def process_and_save_seeder_list(raw_imdb_id: str, fallback_title: str = "") -> bool:
    # Nyahkod simbol URL jika dihantar sebagai %3A
    target_id = unquote(raw_imdb_id).strip()
    is_series = ":" in target_id
    kind = "SIRI TV" if is_series else "FILEM"

    season = 1
    episode = 1
    if is_series:
        parts = target_id.split(":")
        base_id = parts[0]
        season = int(parts[1]) if len(parts) > 1 else 1
        episode = int(parts[2]) if len(parts) > 2 else 1
    else:
        base_id = target_id

    # Tetapan Had Saiz: Siri (< 5.0 GB) | Filem (< 8.5 GB)
    min_bytes = (30 if is_series else 400) * 1024 * 1024
    max_bytes = (5 * 1024 * 1024 * 1024) if is_series else int(8.5 * 1024 * 1024 * 1024)

    console.print(Panel.fit(
        f"[bold cyan]🚀 V3 SEEDER LISTER ENGINE: {kind}[/bold cyan]\n"
        f"ID Sasaran: [bold yellow]{target_id}[/bold yellow] | Base IMDb: [bold white]{base_id}[/bold white]\n"
        f"Julat Saiz: [green]{min_bytes // (1024*1024)} MB[/green] - [red]{max_bytes / (1024**3):.1f} GB[/red]"
        + (f" | Episod: S{season:02d}E{episode:02d}" if is_series else ""),
        border_style="cyan",
    ))

    # 1. Panggilan Scraper Torrentio
    torrentio_items = fetch_torrentio_torrents(target_id, is_series=is_series)

    # 2. Panggilan Scraper Apibay
    if is_series:
        series_title = fallback_title or fetch_series_title_from_cinemeta(base_id)
        clean_title = re.sub(r"[^\w\s]", "", series_title).strip()
        search_query = f"{clean_title} S{season:02d}E{episode:02d}"
        console.print(f"[cyan]🎬 Tajuk Dikesan Cinemeta:[/cyan] [bold white]{series_title}[/bold white] -> Query: [yellow]{search_query}[/yellow]")
        apibay_items = fetch_apibay_torrents(search_query, is_series=True, target_season=season, target_episode=episode)
    else:
        apibay_items = fetch_apibay_torrents(base_id, is_series=False)

    console.print(f"[cyan]📦 Ditemui Asal:[/cyan] Apibay: {len(apibay_items)} | Torrentio: {len(torrentio_items)}")

    all_raw = apibay_items + torrentio_items
    if not all_raw:
        console.print(f"[bold red]❌ Tiada torrent ditemui dari kedua-dua punca untuk {target_id}![/bold red]")
        return False

    # 3. Penapisan Saiz & Nyah-duplikasi InfoHash (Kekalkan seeds tertinggi)
    unique_torrents: Dict[str, Dict[str, Any]] = {}
    total_rejected = 0

    for item in all_raw:
        h = item["info_hash"]
        seeds = item["seeders"]
        sz = item["size"]

        if seeds < 1:
            continue

        if sz < min_bytes or sz > max_bytes:
            total_rejected += 1
            continue

        if h not in unique_torrents or seeds > unique_torrents[h]["seeders"]:
            unique_torrents[h] = item

    if not unique_torrents:
        console.print(f"[bold red]❌ Semua torrent ditolak (melebihi {max_bytes / (1024**3):.1f} GB atau di bawah {min_bytes // (1024*1024)} MB)![/bold red]")
        return False

    # 4. Susun mengikut Seeds Terbanyak (Ambil 35 teratas)
    combined_list = list(unique_torrents.values())
    combined_list.sort(key=lambda x: x["seeders"], reverse=True)
    top_torrents = combined_list[:35]

    # 5. Simpan ke Redis V3 (Kunci: stremio:list:{target_id}, TTL: 24 Jam)
    saved = db_v2.save_torrent_list(target_id, top_torrents, ttl_seconds=86400)

    if saved:
        table = Table(
            title=f"📋 Senarai Seeder V3 Disimpan ke Redis: {target_id} ({len(top_torrents)} Torrent Lulus)",
            border_style="green",
        )
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Punca", justify="center", style="yellow", width=14)
        table.add_column("Kualiti", justify="center", style="magenta", width=8)
        table.add_column("Saiz", style="white", width=10)
        table.add_column("Seeds", justify="center", style="green", width=8)
        table.add_column("Idx", justify="center", style="blue", width=5)
        table.add_column("Nama Pelepasan / Fail", style="dim")

        for idx, t in enumerate(top_torrents, 1):
            sz_str = f"{t['size'] / (1024**3):.2f} GB" if t['size'] >= 1024**3 else f"{t['size'] / (1024**2):.1f} MB"
            table.add_row(
                str(idx),
                t["source"],
                t["quality"],
                sz_str,
                str(t["seeders"]),
                str(t.get("file_idx", 0)),
                t["name"][:55],
            )

        console.print(table)
        console.print(f"[bold green]✅ Berjaya menyimpan metadata senarai ke Upstash Redis Shard bagi kunci 'stremio:list:{target_id}'![/bold green]")
        return True

    console.print("[bold red]❌ Gagal mengemas kini data ke Upstash Redis![/bold red]")
    return False


def main():
    parser = argparse.ArgumentParser(description="V3 Multi-Provider Seeder Lister (Movie & Series)")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt0944947:1:1 atau tt0078748)")
    parser.add_argument("--title", default="", help="Tajuk pilihan/sandaran media")
    args = parser.parse_args()

    success = process_and_save_seeder_list(args.imdb, args.title)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()