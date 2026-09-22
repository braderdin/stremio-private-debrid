#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - EXPERIMENT SEEDER LISTER (SIRI & FILEM)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/experiments/03_test_seeder_lister.py
# CIRI: TORRENTIO MULTI-LINE PARSER + APIBAY GUESSIT SEARCH + PENAPIS < 5GB
# ==============================================================================

import re
import sys
import argparse
from urllib.parse import quote_plus
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from curl_cffi import requests
from guessit import guessit
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


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


def fetch_series_title_from_cinemeta(imdb_id: str) -> str:
    """Mendapatkan tajuk siri rasmi melalui API Stremio Cinemeta."""
    base_id = imdb_id.split(":")[0]
    url = f"https://v3-cinemeta.strem.io/meta/series/{base_id}.json"
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            meta = data.get("meta", {})
            name = meta.get("name", "")
            if name:
                return name
    except Exception:
        pass
    return base_id


def parse_torrentio_stream(stream: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Mengekstrak data daripada objek stream Torrentio secara mendalam.
    Menyokong format 2 baris (fail tunggal) dan 3 baris (Season Pack).
    """
    raw_title = stream.get("title", "")
    info_hash = (stream.get("infoHash") or "").strip().lower()
    file_idx = stream.get("fileIdx", 0)

    if len(info_hash) != 40 or not raw_title:
        return None

    lines = [l.strip() for l in raw_title.split("\n") if l.strip()]

    # Ekstrak seeders dan saiz merentasi semua baris
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

    # Nama paparan: jika Season Pack (3 baris), paparkan nama fail episod di baris 2
    if len(lines) >= 3:
        release_name = f"{lines[1]} [{lines[0]}]"
    else:
        release_name = lines[0] if lines else "Torrent"

    # Kualiti dari tag nama atau tajuk
    stream_name = stream.get("name", "")
    quality = detect_quality(stream_name + " " + raw_title)

    return {
        "name": release_name,
        "info_hash": info_hash,
        "seeders": seeds,
        "size": size_bytes,
        "quality": quality,
        "source": f"⚙️ {source_site}",
        "file_idx": file_idx,
    }


def fetch_torrentio_torrents(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    """Mengambil senarai torrent dari Torrentio (Movie atau Series)."""
    endpoint_type = "series" if is_series else "movie"
    url = f"https://torrentio.strem.fun/stream/{endpoint_type}/{target_id}.json"

    console.print(f"[dim]Panggilan Torrentio: {url}[/dim]")
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
    target_episode: int = 1
) -> List[Dict[str, Any]]:
    """Menyaring torrent dari Apibay dengan pengesahan guessit untuk siri."""
    url = f"https://apibay.org/q.php?q={quote_plus(query_str)}"
    console.print(f"[dim]Panggilan Apibay: {url}[/dim]")
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

                        # Jika siri, sahkan menggunakan guessit
                        if is_series:
                            guess = guessit(name)
                            s_guess = guess.get("season")
                            e_guess = guess.get("episode")

                            # Periksa jika episod sepadan
                            ep_match = False
                            if isinstance(e_guess, list):
                                ep_match = target_episode in e_guess
                            elif e_guess is not None:
                                ep_match = (e_guess == target_episode)

                            # Jika guessit tidak menemui episod, gunakan semakan regex lembut
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
                            "source": "🏴‍☠️ Apibay",
                            "file_idx": 0,
                        })
                    break
        except Exception:
            continue
    return out


def run_test(target_id: str):
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

    # Tetapan Had Saiz
    # SIRI: Had Siling < 5.0 GB | FILEM: Had Siling 8.5 GB
    min_bytes = (100 if is_series else 400) * 1024 * 1024
    max_bytes = (5 * 1024 * 1024 * 1024) if is_series else int(8.5 * 1024 * 1024 * 1024)

    console.print(Panel.fit(
        f"[bold cyan]🧪 MENGUJI KUTIPAN SEEDER V3 ({kind})[/bold cyan]\n"
        f"ID: [bold yellow]{target_id}[/bold yellow] | Base IMDb: [bold white]{base_id}[/bold white]\n"
        f"Penapis Saiz: [green]{min_bytes // (1024*1024)} MB[/green] hingga [red]{max_bytes / (1024**3):.1f} GB[/red]"
        + (f" | Episod: S{season:02d}E{episode:02d}" if is_series else ""),
        border_style="cyan",
    ))

    # 1. Panggilan Scraper Torrentio
    torrentio_items = fetch_torrentio_torrents(target_id, is_series=is_series)

    # 2. Panggilan Scraper Apibay
    apibay_items = []
    if is_series:
        series_title = fetch_series_title_from_cinemeta(base_id)
        clean_title = re.sub(r"[^\w\s]", "", series_title).strip()
        search_query = f"{clean_title} S{season:02d}E{episode:02d}"
        console.print(f"[cyan]🎬 Tajuk Dikesan Cinemeta:[/cyan] [bold white]{series_title}[/bold white] -> Query: [yellow]{search_query}[/yellow]")
        apibay_items = fetch_apibay_torrents(search_query, is_series=True, target_season=season, target_episode=episode)
    else:
        apibay_items = fetch_apibay_torrents(base_id, is_series=False)

    console.print(f"[cyan]📦 Ditemui Asal:[/cyan] Apibay: {len(apibay_items)} | Torrentio: {len(torrentio_items)}")

    all_raw = apibay_items + torrentio_items
    if not all_raw:
        console.print(f"[bold red]❌ Tiada torrent ditemui untuk {target_id}![/bold red]\n")
        return

    # 3. Penapisan Saiz & Nyah-duplikasi InfoHash
    unique_torrents: Dict[str, Dict[str, Any]] = {}
    total_rejected_size = 0

    for item in all_raw:
        h = item["info_hash"]
        seeds = item["seeders"]
        sz = item["size"]

        if seeds < 1:
            continue

        # Tapis saiz di luar had yang ditetapkan
        if sz < min_bytes or sz > max_bytes:
            total_rejected_size += 1
            continue

        # Simpan rekod dengan bilangan seeds tertinggi
        if h not in unique_torrents or seeds > unique_torrents[h]["seeders"]:
            unique_torrents[h] = item

    console.print(f"[dim]Jumlah ditolak melebihi had {max_bytes / (1024**3):.1f}GB atau < {min_bytes // (1024*1024)}MB: {total_rejected_size} torrent[/dim]")

    if not unique_torrents:
        console.print(f"[bold red]❌ Tiada torrent melepasi penapis saiz < {max_bytes / (1024**3):.1f} GB![/bold red]\n")
        return

    # 4. Susun mengikut Seeds Terbanyak
    combined_list = list(unique_torrents.values())
    combined_list.sort(key=lambda x: x["seeders"], reverse=True)
    top_torrents = combined_list[:35]

    # 5. Paparan Jadual Terminal
    table = Table(
        title=f"📋 Senarai Seeder Terbaik ({len(top_torrents)} Torrent Sedia Sedut)",
        border_style="green",
    )
    table.add_column("No", justify="center", style="cyan", width=4)
    table.add_column("Sumber", justify="center", style="yellow", width=14)
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
    console.print(f"[bold green]✅ Ujian untuk {target_id} SELESAI DENGAN CEMERLANG![/bold green]\n")


def main():
    parser = argparse.ArgumentParser(description="Ujian V3 Scraper Filem & Siri")
    parser.add_argument("--id", default="", help="ID IMDb spesifik (cth: tt0944947:1:1)")
    args = parser.parse_args()

    if args.id:
        run_test(args.id)
    else:
        # Ujian lalai kedua-dua kategori
        run_test("tt0944947:1:1")


if __name__ == "__main__":
    main()