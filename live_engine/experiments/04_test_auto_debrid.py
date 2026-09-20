#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - UJIAN AUTO-RESOLVER (TMDB/CINEMETA + APIBAY)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/experiments/04_test_auto_debrid.py
# ==============================================================================

import os
import sys
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

EXP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXP_DIR.parent.parent
ENV_FILE = PROJECT_ROOT / ".env.local"

# 1. Baca Pembolehubah .env.local
env_vars = {}
if ENV_FILE.exists():
    with open(ENV_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")

TMDB_API_KEY = env_vars.get("TMDB_API_KEY", "")
TEST_IMDB = "tt0111161"  # The Shawshank Redemption


def get_metadata(imdb_id: str) -> Dict[str, str]:
    """Mengambil metadata rasmi menggunakan Cinemeta Stremio atau TMDB API."""
    meta = {"title": "Unknown Title", "year": "", "type": "movie"}
    
    # Percubaan 1: Cinemeta API rasmi Stremio (Percuma, pantas, format standard)
    cinemeta_url = f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(cinemeta_url)
            if resp.status_code == 200:
                data = resp.json().get("meta", {})
                if data.get("name"):
                    meta["title"] = data["name"]
                    meta["year"] = str(data.get("year", ""))
                    return meta
    except Exception:
        pass

    # Percubaan 2: TMDB API Sandaran (jika kunci dibekalkan)
    if TMDB_API_KEY:
        tmdb_url = f"https://api.themoviedb.org/3/find/{imdb_id}?external_source=imdb_id&api_key={TMDB_API_KEY}"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(tmdb_url)
                if resp.status_code == 200:
                    results = resp.json().get("movie_results", [])
                    if results:
                        meta["title"] = results[0].get("title", meta["title"])
                        meta["year"] = results[0].get("release_date", "")[:4]
                        return meta
        except Exception:
            pass

    return meta


def pick_best_torrent(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Mencari senarai torrent di apibay.org dan memilih 1 torrent terbaik secara automatik."""
    apibay_url = f"https://apibay.org/q.php?q={imdb_id}"
    
    with httpx.Client(timeout=10.0) as client:
        try:
            resp = client.get(apibay_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                return None
            torrents = resp.json()
        except Exception:
            return None

    if not isinstance(torrents, list) or len(torrents) == 0 or torrents[0].get("name") == "No results returned":
        return None

    MIN_BYTES = 500 * 1024 * 1024        # 500 MB
    MAX_BYTES = 9.0 * 1024 * 1024 * 1024  # 9.0 GB
    SWEET_MIN = 1.0 * 1024 * 1024 * 1024  # 1.0 GB
    SWEET_MAX = 4.0 * 1024 * 1024 * 1024  # 4.0 GB

    candidates = []
    for item in torrents:
        h = item.get("info_hash", "").strip().lower()
        title = item.get("name", "").strip()
        if not h or len(h) != 40:
            continue

        try:
            seeds = int(item.get("seeders", 0))
            sz = int(item.get("size", 0))
        except ValueError:
            continue

        if seeds < 1 or sz < MIN_BYTES or sz > MAX_BYTES:
            continue

        score = seeds * 1.8 if (SWEET_MIN <= sz <= SWEET_MAX) else seeds
        candidates.append({
            "name": title,
            "info_hash": h,
            "seeders": seeds,
            "size": sz,
            "score": score
        })

    if not candidates:
        return None

    # Susun mengikut skor tertinggi
    candidates.sort(key=lambda x: x["score"], reverse=True)
    best = candidates[0]

    # Bina Magnet Link Rasmi berserta Public Trackers pantas
    trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce"
    ]
    tr_str = "&".join([f"tr={t}" for t in trackers])
    best["magnet"] = f"magnet:?xt=urn:btih:{best['info_hash']}&dn={best['name']}&{tr_str}"
    return best


def main():
    console.print(Panel.fit(
        "[bold cyan]🧪 UJIAN SISTEM AUTO-RESOLVER DEBRID ENGINE[/bold cyan]\n"
        f"[yellow]Sasaran IMDb ID:[/yellow] [white]{TEST_IMDB}[/white]",
        border_style="cyan"
    ))

    # 1. Uji Resolusi Metadata
    meta = get_metadata(TEST_IMDB)
    console.print(f"🎬 [bold green]Metadata Dikesan:[/bold green] {meta['title']} ({meta['year']})")

    # 2. Uji Pemilihan Torrent Terbaik di Apibay
    best = pick_best_torrent(TEST_IMDB)
    if not best:
        console.print("[bold red]❌ Tiada torrent sesuai ditemui di apibay.org![/bold red]")
        return

    gb_size = best["size"] / (1024 * 1024 * 1024)
    table = Table(title="🎯 Hasil Pemilihan Torrent Automatik", border_style="green")
    table.add_column("Parameter", style="cyan")
    table.add_column("Perincian", style="white")

    table.add_row("Tajuk Torrent", best["name"])
    table.add_row("Saiz Fail", f"{gb_size:.2f} GB (Zon Sasaran Emas)")
    table.add_row("Seeders Aktif", str(best["seeders"]))
    table.add_row("InfoHash", best["info_hash"])
    table.add_row("Magnet Link", best["magnet"][:70] + "...")

    console.print(table)
    console.print("\n[bold green]✅ Logik berfungsi sepenuhnya untuk dipasang pada Runner GitHub Actions![/bold green]")


if __name__ == "__main__":
    main()