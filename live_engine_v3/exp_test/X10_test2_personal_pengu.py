#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - PERSONAL PENGUPLAY TEST ENGINE
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/X10_test2_personal_pengu.py
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

TEST_MOVIES = [
    {"title": "Kantara", "year": "2022", "imdb": "tt15327088", "lang": "Kannada"},
    {"title": "Masaan", "year": "2015", "imdb": "tt4635372", "lang": "Hindi"},
    {"title": "Vaazhai", "year": "2024", "imdb": "tt23782140", "lang": "Tamil"},
    {"title": "Sairat", "year": "2016", "imdb": "tt5312232", "lang": "Marathi"},
    {"title": "Jana Gana Mana", "year": "2022", "imdb": "tt13391710", "lang": "Malayalam"},
]


def detect_and_validate_quality(raw_text: str) -> Optional[str]:
    """Menolak 4K, 480p, dan 360p. Hanya luluskan 1440p (2K), 1080p, dan 720p."""
    t = raw_text.lower()

    bad_tokens = [
        "2160p", "4k", "uhd",
        "480p", "360p", "240p", "sd", "dvdrip", "xvid",
        "cam", "camrip", "hdcam", "telesync", "tsrip"
    ]
    for bad in bad_tokens:
        if re.search(rf"\b{bad}\b", t) or (bad in ["4k", "uhd", "360p", "480p", "2160p"] and bad in t):
            return None

    if any(q in t for q in ["1440p", "2k", "qhd"]):
        return "1440p"
    if any(q in t for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in t for q in ["720p", "hd"]):
        return "720p"

    return None


def parse_size_bytes(raw_text: str, stream_url: str = "") -> int:
    """Mengekstrak saiz fail sebenar daripada teks metadata atau semakan HEAD."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|GiB|MB|MiB)\b", raw_text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).upper()
        if "GB" in unit:
            return int(val * 1024**3)
        return int(val * 1024**2)

    if stream_url and not any(ext in stream_url.lower() for ext in [".m3u8", ".mpd", "/dash/"]):
        try:
            h_resp = requests.head(stream_url, timeout=2.0, headers={"User-Agent": "Stremio/4.4.168"})
            cl = h_resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > 10 * 1024 * 1024:
                return int(cl)
        except Exception:
            pass

    return int(1.8 * 1024**3)


def extract_provider_source(name_field: str, title_field: str) -> str:
    """Mengekstrak label penyedia asal (cth: HDHub4u, Cinejoy, 2Peckle, MovieBox)."""
    src_match = re.search(r"Source:\s*([\w\-]+)", title_field, re.IGNORECASE)
    if src_match:
        return src_match.group(1)

    lines = [l.strip() for l in name_field.split("\n") if l.strip()]
    if len(lines) >= 3:
        return lines[-1]
    return "MovieBox"


def test_personal_token(token: str, imdb_id: str, title: str):
    clean_token = token.strip().replace('"', '').replace("'", "")
    base_url = f"https://pengu.uk/%7B%22auth_token%22%3A%22{clean_token}%22%7D"
    endpoint = f"{base_url}/stream/movie/{imdb_id}.json"

    console.print(Panel.fit(
        f"[bold cyan]🔑 MENGUJI TOKEN PERIBADI PENGUPLAY[/bold cyan]\n"
        f"Token: [yellow]{clean_token[:10]}...{clean_token[-6:]}[/yellow]\n"
        f"Sasaran: [bold white]{title}[/bold white] ({imdb_id})\n"
        f"URL: [dim]{endpoint}[/dim]",
        border_style="cyan"
    ))

    try:
        resp = requests.get(
            endpoint,
            impersonate="chrome120",
            timeout=45,
            headers={"User-Agent": "Stremio/4.4.168"}
        )
        if resp.status_code != 200:
            console.print(f"[bold red]❌ Gagal menyambung ke PenguPlay (HTTP {resp.status_code})[/bold red]")
            return

        streams = resp.json().get("streams", [])
        if not streams:
            console.print("[bold yellow]⚠️ Tiada strim ditemui untuk filem ini dengan token tersebut.[/bold yellow]")
            return

        valid_streams = []
        for s in streams:
            url = s.get("url", "")
            name_f = s.get("name", "")
            title_f = s.get("title", "")
            desc_f = s.get("description", "")

            combined_desc = f"{name_f}\n{title_f}\n{desc_f}"
            quality = detect_and_validate_quality(combined_desc)
            if not quality:
                continue

            size_bytes = parse_size_bytes(combined_desc, stream_url=url)
            if size_bytes > int(7.0 * 1024**3):
                continue

            stream_fmt = "MP4"
            if ".m3u8" in url or "hls" in combined_desc.lower():
                stream_fmt = "HLS"
            elif ".mpd" in url or "dash" in combined_desc.lower():
                stream_fmt = "DASH"

            provider = extract_provider_source(name_f, combined_desc)
            valid_streams.append({
                "provider": provider,
                "format": stream_fmt,
                "quality": quality,
                "size": size_bytes,
                "url": url,
            })

        table = Table(
            title=f"📋 Hasil Token Peribadi: {title} ({len(valid_streams)} Strim Sah Melepasi Tapisan)",
            border_style="green"
        )
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Penyedia", justify="center", style="yellow", width=14)
        table.add_column("Format", justify="center", style="blue", width=8)
        table.add_column("Kualiti", justify="center", style="magenta", width=8)
        table.add_column("Saiz Sebenar", style="white", width=12)
        table.add_column("URL Potongan", style="dim")

        for idx, it in enumerate(valid_streams, 1):
            sz = it["size"]
            sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
            table.add_row(
                str(idx),
                it["provider"],
                it["format"],
                it["quality"],
                sz_str,
                it["url"][:60] + "..."
            )

        console.print(table)
        console.print("[bold green]✅ Token peribadi anda berfungsi dengan cemerlang![/bold green]")

    except Exception as e:
        console.print(f"[bold red]❌ Ralat komunikasi:[/bold red] {e}")


def main():
    parser = argparse.ArgumentParser(description="Ujian Token Peribadi PenguPlay")
    parser.add_argument("--token", required=True, help="Auth token daripada akaun PenguPlay anda")
    parser.add_argument("--imdb", default="tt15327088", help="Target IMDb ID (Lalai: Kantara)")
    parser.add_argument("--title", default="Kantara", help="Tajuk filem")
    args = parser.parse_args()

    test_personal_token(args.token, args.imdb, args.title)


if __name__ == "__main__":
    main()