#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - EKSPERIMEN PENGIKIS TORRENTIO API
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/experiments/02_test_torrentio.py
# CIRI: MENGAMBIL SENARAI TORRENTIO DENGAN INFOHASH & PARSING SEEDER / SAIZ
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


def parse_torrentio_title(raw_title: str) -> Dict[str, Any]:
    """
    Mengekstrak kualiti, saiz fail, dan bilangan seeders daripada teks title Torrentio.
    Format tipikal Torrentio:
    'Ouija.2014.1080p.BluRay.x264\n👤 45 💾 1.4 GB ⚙️ RARBG'
    """
    lines = raw_title.split("\n")
    release_name = lines[0].strip()
    extra_info = lines[1] if len(lines) > 1 else ""

    # 1. Ekstrak Seeders (ikon 👤)
    seeds_match = re.search(r"👤\s*(\d+)", extra_info)
    seeds = int(seeds_match.group(1)) if seeds_match else 0

    # 2. Ekstrak Saiz (ikon 💾)
    size_match = re.search(r"💾\s*([\d\.]+)\s*(GB|MB)", extra_info, re.IGNORECASE)
    size_bytes = 0
    size_str = "N/A"
    if size_match:
        val = float(size_match.group(1))
        unit = size_match.group(2).upper()
        size_str = f"{val} {unit}"
        size_bytes = int(val * (1024**3 if unit == "GB" else 1024**2))

    # 3. Kualiti
    q = "HD"
    low = (release_name + " " + raw_title).lower()
    if any(k in low for k in ["2160p", "4k"]):
        q = "4K"
    elif any(k in low for k in ["1080p", "fhd"]):
        q = "1080p"
    elif any(k in low for k in ["720p", "hd"]):
        q = "720p"

    return {
        "release_name": release_name,
        "seeders": seeds,
        "size_str": size_str,
        "size_bytes": size_bytes,
        "quality": q,
    }


def fetch_torrentio_streams(imdb_id: str) -> List[Dict[str, Any]]:
    """Menghubungi endpoint terbuka Torrentio Stremio Addon."""
    # Endpoint lalai Torrentio menyaring penyedia popular
    url = f"https://torrentio.strem.fun/stream/movie/{imdb_id}.json"
    console.print(f"[cyan]📡 Menghubungi Torrentio API: {url}...[/cyan]")

    start = time.time()
    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        duration = time.time() - start

        if resp.status_code == 200:
            streams = resp.json().get("streams", [])
            console.print(f"[green]✅ Berjaya menerima {len(streams)} strim dari Torrentio dalam masa {duration:.2f}s![/green]\n")

            parsed_list = []
            for s in streams:
                info_hash = (s.get("infoHash") or "").strip().lower()
                if not info_hash or len(info_hash) != 40:
                    continue

                meta = parse_torrentio_title(s.get("title", ""))
                parsed_list.append({
                    "name": meta["release_name"],
                    "info_hash": info_hash,
                    "seeders": meta["seeders"],
                    "size_str": meta["size_str"],
                    "size_bytes": meta["size_bytes"],
                    "quality": meta["quality"],
                    "source": s.get("name", "Torrentio").split("\n")[0]
                })
            return parsed_list
        else:
            console.print(f"[bold red]❌ Torrentio memulangkan HTTP {resp.status_code}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]❌ Ralat menghubungi Torrentio: {type(e).__name__} - {e}[/bold red]")

    return []


def main():
    console.print(Panel.fit(
        "[bold cyan]🧪 EKSPERIMEN PENGIKIS PINTAR TORRENTIO API[/bold cyan]\n"
        f"Sasaran: IMDb [yellow]{TEST_IMDB}[/yellow] (Ouija 2014)",
        border_style="cyan"
    ))

    items = fetch_torrentio_streams(TEST_IMDB)
    if not items:
        console.print("[bold red]Tiada data strim dapat diekstrak daripada Torrentio.[/bold red]")
        return

    # Susun mengikut seeders tertinggi
    items.sort(key=lambda x: x["seeders"], reverse=True)

    table = Table(title=f"🎬 Senarai Pilihan Torrentio untuk {TEST_IMDB} (Top 12)", border_style="green")
    table.add_column("No", justify="center", style="cyan")
    table.add_column("Sumber / Kualiti", style="magenta")
    table.add_column("Saiz", style="white")
    table.add_column("Seeders", justify="center", style="green")
    table.add_column("InfoHash (40 Hex)", style="dim")
    table.add_column("Nama Pelepasan", style="white")

    for idx, it in enumerate(items[:12], 1):
        table.add_row(
            str(idx),
            f"{it['source']} [{it['quality']}]",
            it["size_str"],
            str(it["seeders"]),
            it["info_hash"][:16] + "...",
            it["name"][:45]
        )

    console.print(table)


if __name__ == "__main__":
    main()