#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - EKSPERIMEN PENYEDIA MULTI-TORRENT
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/experiments/01_multi_provider_tester.py
# CIRI: MENGUJI KELAJUAN, SEEDERS & RESPON APIBAY, YTS, SOLIDTORRENTS, EZTV
# ==============================================================================

import re
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

from curl_cffi import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

TEST_IMDB = "tt1204977"  # Ouija (2014)
TEST_TITLE = "Ouija"
TEST_YEAR = "2014"


def test_apibay(imdb_id: str) -> Dict[str, Any]:
    """Ujian 1: Apibay (ThePirateBay) menggunakan IMDb ID."""
    url = f"https://apibay.org/q.php?q={imdb_id}"
    start = time.time()
    try:
        r = requests.get(url, impersonate="safari15_5", timeout=10)
        dur = time.time() - start
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data and data[0].get("name") != "No results returned":
                valid = [x for x in data if int(x.get("seeders", 0)) > 0]
                max_seeds = max([int(x.get("seeders", 0)) for x in valid], default=0)
                return {"provider": "Apibay (TPB)", "status": "OK", "count": len(valid), "max_seeds": max_seeds, "time": f"{dur:.2f}s"}
    except Exception as e:
        return {"provider": "Apibay (TPB)", "status": f"Error: {type(e).__name__}", "count": 0, "max_seeds": 0, "time": "-"}
    return {"provider": "Apibay (TPB)", "status": "Tiada Hasil", "count": 0, "max_seeds": 0, "time": "-"}


def test_yts(imdb_id: str) -> Dict[str, Any]:
    """Ujian 2: YTS (YIFY) Official API menggunakan IMDb ID."""
    url = f"https://yts.mx/api/v2/list_movies.json?query_term={imdb_id}"
    start = time.time()
    try:
        r = requests.get(url, impersonate="chrome120", timeout=10)
        dur = time.time() - start
        if r.status_code == 200:
            movies = r.json().get("data", {}).get("movies", [])
            if movies and movies[0].get("torrents"):
                torrents = movies[0]["torrents"]
                max_seeds = max([int(x.get("seeds", 0)) for x in torrents], default=0)
                return {"provider": "YTS (YIFY)", "status": "OK", "count": len(torrents), "max_seeds": max_seeds, "time": f"{dur:.2f}s"}
    except Exception as e:
        return {"provider": "YTS (YIFY)", "status": f"Error: {type(e).__name__}", "count": 0, "max_seeds": 0, "time": "-"}
    return {"provider": "YTS (YIFY)", "status": "Tiada Hasil", "count": 0, "max_seeds": 0, "time": "-"}


def test_solidtorrents(title: str, year: str) -> Dict[str, Any]:
    """Ujian 3: SolidTorrents Public API menggunakan Carian Tajuk + Tahun."""
    query = f"{title} {year}".strip()
    url = f"https://solidtorrents.to/api/v1/search?q={query}&category=video"
    start = time.time()
    try:
        r = requests.get(url, impersonate="chrome120", timeout=10)
        dur = time.time() - start
        if r.status_code == 200:
            results = r.json().get("results", [])
            valid = [x for x in results if int(x.get("swarm", {}).get("seeders", 0)) > 0]
            max_seeds = max([int(x.get("swarm", {}).get("seeders", 0)) for x in valid], default=0)
            return {"provider": "SolidTorrents", "status": "OK", "count": len(valid), "max_seeds": max_seeds, "time": f"{dur:.2f}s"}
    except Exception as e:
        return {"provider": "SolidTorrents", "status": f"Error: {type(e).__name__}", "count": 0, "max_seeds": 0, "time": "-"}
    return {"provider": "SolidTorrents", "status": "Tiada Hasil", "count": 0, "max_seeds": 0, "time": "-"}


def test_eztv(imdb_id: str) -> Dict[str, Any]:
    """Ujian 4: EZTV API (Siri TV) menggunakan digit nombor IMDb."""
    num_only = re.sub(r"[^\d]", "", imdb_id)
    url = f"https://eztv.re/api/get-torrents?imdb_id={num_only}&limit=30"
    start = time.time()
    try:
        r = requests.get(url, impersonate="chrome120", timeout=10)
        dur = time.time() - start
        if r.status_code == 200:
            torrents = r.json().get("torrents", [])
            if torrents:
                max_seeds = max([int(x.get("seeds", 0)) for x in torrents], default=0)
                return {"provider": "EZTV (Series)", "status": "OK", "count": len(torrents), "max_seeds": max_seeds, "time": f"{dur:.2f}s"}
    except Exception as e:
        return {"provider": "EZTV (Series)", "status": f"Error: {type(e).__name__}", "count": 0, "max_seeds": 0, "time": "-"}
    return {"provider": "EZTV (Series)", "status": "Tiada Hasil", "count": 0, "max_seeds": 0, "time": "-"}


def main():
    console.print(Panel.fit(
        "[bold cyan]🧪 UJIAN PERBANDINGAN SUMBER ALTERNATIF TORRENT (FALLBACK TESTER)[/bold cyan]\n"
        f"Sasaran Ujian: [yellow]{TEST_TITLE} ({TEST_YEAR})[/yellow] | IMDb ID: [white]{TEST_IMDB}[/white]",
        border_style="cyan"
    ))

    providers_results = [
        test_apibay(TEST_IMDB),
        test_yts(TEST_IMDB),
        test_solidtorrents(TEST_TITLE, TEST_YEAR),
        test_eztv(TEST_IMDB),
    ]

    table = Table(title="📊 Hasil Imbasan Pelbagai Penyedia Torrent", border_style="green")
    table.add_column("Penyedia (Provider)", style="cyan")
    table.add_column("Status Sambungan", style="magenta")
    table.add_column("Jumlah Torrent Sah", justify="center", style="white")
    table.add_column("Seeders Tertinggi", justify="center", style="green")
    table.add_column("Masa Respons", justify="right", style="dim")

    for p in providers_results:
        status_color = "green" if p["status"] == "OK" else "yellow"
        table.add_row(
            p["provider"],
            f"[{status_color}]{p['status']}[/{status_color}]",
            str(p["count"]),
            str(p["max_seeds"]),
            p["time"]
        )

    console.print(table)


if __name__ == "__main__":
    main()