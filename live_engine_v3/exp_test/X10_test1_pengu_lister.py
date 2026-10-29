#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - EXPERIMENTAL TEST (X10 PENGUPLAY HTTP LISTER)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/X10_test1_pengu_lister.py
# ==============================================================================

import re
import sys
import json
import hashlib
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote, unquote

from curl_cffi import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# URL Asas Addon PenguPlay
PENGU_AUTH_TOKEN = "GwjNukzuTU_1cw6E2zcTgrxhyTYqmBDFhcUFgmQmDr0"
PENGU_BASE_URL = f"https://pengu.uk/%7B%22auth_token%22%3A%22{PENGU_AUTH_TOKEN}%22%7D"

TEST_MOVIES = [
    {"title": "Sairat", "year": "2016", "imdb": "tt5312232", "lang": "Marathi"},
    {"title": "Vaazhai", "year": "2024", "imdb": "tt23782140", "lang": "Tamil"},
    {"title": "Pada", "year": "2022", "imdb": "tt10375094", "lang": "Malayalam"},
    {"title": "Viduthalai Part 2", "year": "2024", "imdb": "tt25434834", "lang": "Tamil"},
    {"title": "Jana Gana Mana", "year": "2022", "imdb": "tt13391710", "lang": "Malayalam"},
    {"title": "Visaranai", "year": "2015", "imdb": "tt4991384", "lang": "Tamil"},
    {"title": "Masaan", "year": "2015", "imdb": "tt4635372", "lang": "Hindi"},
    {"title": "Newton", "year": "2017", "imdb": "tt6484982", "lang": "Hindi"},
    {"title": "Uppena", "year": "2020", "imdb": "tt9633480", "lang": "Telugu"},
]


def detect_and_validate_quality(raw_text: str) -> Optional[str]:
    """Menapis kualiti rendah (360p, 480p) dan hanya meluluskan 720p, 1080p, 2K, 4K."""
    t = raw_text.lower()

    # Sekat resolusi rendah
    if any(q in t for q in ["360p", "480p", "sd"]):
        return None

    if any(q in t for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in t for q in ["1440p", "2k", "qhd"]):
        return "2K"
    if any(q in t for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in t for q in ["720p", "hd"]):
        return "720p"

    return None


def parse_pengu_size(title_str: str) -> int:
    """Mengekstrak anggaran saiz bait daripada teks PenguPlay."""
    m = re.search(r"([\d\.]+)\s*(GB|MB)", title_str, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).upper()
        if unit == "GB":
            return int(val * 1024**3)
        return int(val * 1024**2)
    return int(1.5 * 1024**3)  # Anggaran purata jika tiada saiz


def fetch_pengu_streams(imdb_id: str) -> List[Dict[str, Any]]:
    base_id = imdb_id.split(":")[0]
    stream_url = f"{PENGU_BASE_URL}/stream/movie/{base_id}.json"
    results = []

    try:
        resp = requests.get(
            stream_url,
            impersonate="chrome120",
            timeout=12,
            headers={"User-Agent": "Stremio/4.4.168"}
        )
        if resp.status_code != 200:
            console.print(f"[dim red]HTTP {resp.status_code} semasa mengambil data PenguPlay[/dim red]")
            return []

        data = resp.json()
        streams = data.get("streams", [])

        for s in streams:
            name_field = s.get("name", "")
            title_field = s.get("title", "")
            url = s.get("url", "")

            if not url:
                continue

            full_desc = f"{name_field} {title_field}"
            quality = detect_and_validate_quality(full_desc)
            if not quality:
                continue  # Singkirkan kualiti di bawah 720p

            # Jana pseudo-hash 40-hex daripada URL untuk keserasian skema
            pseudo_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()
            size_bytes = parse_pengu_size(title_field)

            # Tentukan jenis strim: Direct MP4 vs DASH / HLS
            stream_format = "MP4"
            if ".m3u8" in url or "hls" in full_desc.lower():
                stream_format = "HLS"
            elif ".mpd" in url or "dash" in full_desc.lower():
                stream_format = "DASH"

            results.append({
                "name": f"[{quality}] {title_field.replace(chr(10), ' • ')}",
                "info_hash": pseudo_hash,
                "seeders": 999,  # Nilai penunjuk direct HTTP (tiada isu seed)
                "size": size_bytes,
                "quality": quality,
                "source": "PenguPlay",
                "stream_type": "http",
                "stream_format": stream_format,
                "direct_url": url,
                "file_idx": 0,
            })
    except Exception as e:
        console.print(f"[bold red]Ralat capaian PenguPlay:[/bold red] {e}")

    return results


def test_single_movie(item: Dict[str, str]):
    console.print(Panel.fit(
        f"[bold yellow]🐧 UJIAN PENGIRIS HTTP PENGUPLAY: {item['title']} ({item['year']})[/bold yellow]\n"
        f"ID IMDb: [bold cyan]{item['imdb']}[/bold cyan] | Bahasa: [white]{item['lang']}[/white]\n"
        f"Syarat: [green]1080p, 720p, 2K[/green] ([red]360p & 480p Ditolak[/red])",
        border_style="yellow"
    ))

    streams = fetch_pengu_streams(item["imdb"])

    table = Table(
        title=f"📋 Senarai Strim HTTP Lulus: {item['title']} ({len(streams)} Ditemui)",
        border_style="green" if streams else "red"
    )
    table.add_column("No", justify="center", style="cyan", width=4)
    table.add_column("Format", justify="center", style="yellow", width=8)
    table.add_column("Kualiti", justify="center", style="magenta", width=8)
    table.add_column("Anggaran Saiz", style="white", width=12)
    table.add_column("Jenis Sambungan", justify="center", style="blue", width=14)
    table.add_column("URL (Potongan Ringkas)", style="dim")

    for idx, s in enumerate(streams, 1):
        sz = s["size"]
        sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
        table.add_row(
            str(idx),
            s["stream_format"],
            s["quality"],
            sz_str,
            "Direct HTTP",
            s["direct_url"][:60] + "..."
        )

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Ujian Pengikis Strim PenguPlay (X10)")
    parser.add_argument("--imdb", default="", help="Target IMDb ID")
    parser.add_argument("--title", default="", help="Tajuk filem")
    parser.add_argument("--test-all", action="store_true", help="Uji semua filem dalam senarai")
    args = parser.parse_args()

    if args.test_all:
        for it in TEST_MOVIES:
            test_single_movie(it)
            console.print("\n")
        return

    if args.imdb:
        test_single_movie({"title": args.title or args.imdb, "year": "", "imdb": args.imdb, "lang": "N/A"})
    else:
        # Lalai: Uji Sairat seperti dalam paparan tangkapan layar anda
        test_single_movie(TEST_MOVIES[0])


if __name__ == "__main__":
    main()