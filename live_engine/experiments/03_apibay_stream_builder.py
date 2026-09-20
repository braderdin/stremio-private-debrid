#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - PEMBINA STRIM APIBAY (TPB) UNTUK STREMIO
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/experiments/03_apibay_stream_builder.py
# ==============================================================================

import os
import sys
import json
import time
import html
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Direktori Projek & Fail Output
EXP_DIR = Path(__file__).resolve().parent
LIVE_ENGINE_DIR = EXP_DIR.parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
TEMP_DIR = LIVE_ENGINE_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

OUT_JSON = TEMP_DIR / "stremio_apibay_streams.json"
OUT_HTML = TEMP_DIR / "stremio_apibay_streams.html"


def load_env_vars() -> Dict[str, str]:
    """Membaca konfigurasi .env.local untuk mendapatkan token dan domain."""
    env_path = PROJECT_ROOT / ".env.local"
    env_vars = {}
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars


ENV = load_env_vars()
ADDON_TOKEN = ENV.get("ADDON_SECRET_TOKEN", "Harunosakura1122")
CF_WORKER_URL = ENV.get("CF_WORKER_URL", "https://stremio-real-debrid-addon.retrogamerg405v.workers.dev").rstrip("/")

# Filem Ujian: The Shawshank Redemption (tt1204977)
TEST_IMDB = "tt1204977"
TEST_TYPE = "movie"


def detect_quality(name: str) -> str:
    """Mengesan kualiti video daripada nama pelepasan torrent."""
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    if any(q in n for q in ["480p", "dvdrip", "xvid"]):
        return "SD"
    return "HD"


def format_bytes(size_bytes: int) -> str:
    """Memformat saiz bait ke format GB / MB yang kemas."""
    gb = size_bytes / (1024 * 1024 * 1024)
    if gb >= 1.0:
        return f"{gb:.2f} GB"
    mb = size_bytes / (1024 * 1024)
    return f"{mb:.1f} MB"


def fetch_and_build_streams(imdb_id: str, media_type: str = "movie") -> Dict[str, Any]:
    """
    Mengambil data terus dari ThePirateBay API (apibay.org), menyusun mengikut seeders,
    dan memformatkannya menjadi senarai strim rasmi mengikut protokol Stremio v3.
    """
    apibay_url = f"https://apibay.org/q.php?q={imdb_id}"
    console.print(f"[cyan]📡 Menghubungi apibay.org untuk carian ID: {imdb_id}...[/cyan]")

    streams_payload = []
    raw_results = []

    with httpx.Client(timeout=10.0) as client:
        try:
            resp = client.get(apibay_url, headers={"User-Agent": "Stremio-Apibay-Builder/1.0"})
            if resp.status_code == 200:
                raw_results = resp.json()
        except Exception as e:
            console.print(f"[bold red]❌ Ralat semasa menghubungi apibay.org: {e}[/bold red]")
            return {"streams": []}

    # Tapis data yang tidak sah
    valid_torrents = []
    for item in raw_results:
        h = item.get("info_hash", "").strip().lower()
        title = item.get("name", "").strip()
        if not h or len(h) != 40 or title == "No results returned":
            continue

        try:
            seeds = int(item.get("seeders", 0))
            leech = int(item.get("leechers", 0))
            sz = int(item.get("size", 0))
        except ValueError:
            continue

        # Abaikan torrent yang langsung tiada seeder
        if seeds < 1:
            continue

        valid_torrents.append({
            "name": title,
            "info_hash": h,
            "seeders": seeds,
            "leechers": leech,
            "size": sz,
            "quality": detect_quality(title),
            "status": item.get("status", "")
        })

    # Susun mengikut kualiti & jumlah seeder tertinggi
    valid_torrents.sort(key=lambda x: (x["seeders"]), reverse=True)

    # Hadkan kepada 15 pilihan terbaik untuk paparan bersih di Stremio TV
    selected_torrents = valid_torrents[:15]

    for item in selected_torrents:
        clean_title = re.sub(r"[^\w\s\.\-]", "", item["name"])
        size_str = format_bytes(item["size"])
        q = item["quality"]

        # Pautan trigger yang akan dipanggil oleh Stremio apabila butang ditekan
        trigger_url = (
            f"{CF_WORKER_URL}/{ADDON_TOKEN}/trigger"
            f"?hash={item['info_hash']}"
            f"&id={imdb_id}"
            f"&idx=0"
            f"&title={httpx.URL('', params={'t': clean_title}).params['t']}"
        )

        stremio_stream = {
            "name": f"[📥 Sedut B2] {q}",
            "title": (
                f"{item['name']}\n"
                f"💾 {size_str}  |  👤 {item['seeders']} Seeders  |  ⚙️ TPB Direct\n"
                f"⚡ Klik untuk muat turun automatik ke B2 Storage"
            ),
            "url": trigger_url,
            "behaviorHints": {
                "bingeGroup": f"b2-debrid-{imdb_id}",
                "notWebReady": False
            }
        }
        streams_payload.append(stremio_stream)

    return {
        "streams": streams_payload,
        "total_found": len(valid_torrents),
        "total_returned": len(streams_payload),
        "target_imdb": imdb_id
    }


def main():
    console.print(Panel.fit(
        "[bold cyan]🧪 EKSPERIMEN PEMBINA STRIM APIBAY (TPB) UNTUK STREMIO[/bold cyan]\n"
        "[yellow]Membina Butang [📥 Sedut B2] Tanpa Perlu Sandaran Scraping Tambahan[/yellow]",
        border_style="cyan"
    ))

    result_data = fetch_and_build_streams(TEST_IMDB, TEST_TYPE)
    streams = result_data.get("streams", [])

    if not streams:
        console.print("[bold red]❌ Tiada strim dapat dijana untuk IMDb ini![/bold red]")
        return

    # Paparkan Jadual Hasil di Terminal
    table = Table(title=f"🎬 Pilihan Strim Dijana bagi {TEST_IMDB} ({len(streams)} pilihan)", border_style="green")
    table.add_column("No", justify="center", style="cyan")
    table.add_column("Label Butang", style="magenta")
    table.add_column("Kualiti / Saiz / Seeder", style="white")
    table.add_column("Pautan Tindakan (Trigger URL)", style="dim")

    for idx, s in enumerate(streams[:8], 1):
        first_line = s["title"].split("\n")[0][:45]
        stats_line = s["title"].split("\n")[1] if len(s["title"].split("\n")) > 1 else ""
        table.add_row(str(idx), s["name"], f"{first_line}\n{stats_line}", s["url"][:60] + "...")

    console.print(table)

    # 1. Simpan fail JSON (<200KB)
    json_output = json.dumps(result_data, indent=2, ensure_ascii=False)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        f.write(json_output)

    # 2. Simpan fail HTML Visual (<200KB)
    html_items = ""
    for s in streams:
        html_items += f"""
        <div style="background: #1e293b; padding: 14px; margin-bottom: 12px; border-radius: 8px; border-left: 4px solid #38bdf8;">
            <div style="font-size: 16px; font-weight: bold; color: #38bdf8;">{html.escape(s['name'])}</div>
            <pre style="margin: 6px 0; color: #cbd5e1; white-space: pre-wrap;">{html.escape(s['title'])}</pre>
            <div style="font-size: 11px; color: #64748b; word-break: break-all;">Pautan Trigger: {html.escape(s['url'])}</div>
        </div>
        """

    html_page = f"""<!DOCTYPE html>
    <html lang="ms">
    <head><meta charset="UTF-8"><title>Simulasi Butang Stremio</title></head>
    <body style="background: #0f172a; color: #f8fafc; font-family: sans-serif; padding: 24px;">
        <h2>📺 Simulasi Paparan Senarai Strim Stremio</h2>
        <p>Sasaran IMDb: <strong>{TEST_IMDB}</strong> | Jumlah Pilihan Dijana: <strong>{len(streams)}</strong></p>
        <div style="max-width: 850px;">{html_items}</div>
    </body>
    </html>"""

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_page)

    console.print(f"\n[bold green]✅ Fail laporan berjaya dijana:[/bold green]")
    console.print(f" • [yellow]{OUT_JSON}[/yellow] ({len(json_output.encode('utf-8'))/1024:.2f} KB)")
    console.print(f" • [yellow]{OUT_HTML}[/yellow] ({len(html_page.encode('utf-8'))/1024:.2f} KB)\n")


if __name__ == "__main__":
    main()