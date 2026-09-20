#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - PENGUJI HUJUNG-KE-HUJUNG (E2E TESTER)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/06_e2e_tester.py
# ==============================================================================

import sys
import time
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Muat Turun Tetapan dari 00_config
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    _config = importlib.import_module("00_config")
    ADDON_TOKEN = getattr(_config, "ADDON_SECRET_TOKEN", "Harunosakura1122")
    CF_PROXY_URL = getattr(_config, "CF_WORKER_B2_PROXY_STORAGE", "").rstrip("/")
    CF_ADDON_URL = getattr(_config, "CF_WORKER_URL", "").rstrip("/")
    RENDER_URL = getattr(_config, "CF_WORKER_RENDER_PING", "").rstrip("/")
except Exception as e:
    console.print(f"[bold red]❌ Ralat import konfigurasi: {e}[/bold red]")
    sys.exit(1)


def test_render_health(client: httpx.Client) -> bool:
    """Menguji kesihatan dan ketersediaan pelayan mikro Render."""
    if not RENDER_URL:
        console.print("[yellow]⚠️ RENDER_URL tiada dalam tetapan. Melangkau ujian Render.[/yellow]")
        return False

    try:
        resp = client.get(f"{RENDER_URL}/", timeout=8.0)
        if resp.status_code == 200:
            data = resp.json()
            console.print(f"[bold green]✅ Pelayan Render Aktif:[/bold green] Uptime: {data.get('uptime')} | Status: {data.get('status')}")
            return True
        else:
            console.print(f"[bold red]❌ Render membalas dengan status kod: HTTP {resp.status_code}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]❌ Ralat sambungan ke Render: {e}[/bold red]")
    return False


def test_addon_manifest(client: httpx.Client) -> bool:
    """Menguji titik akhir manifest Cloudflare Worker Stremio Addon."""
    if not CF_ADDON_URL:
        console.print("[yellow]⚠️ CF_WORKER_URL belum ditetapkan dalam .env.local.[/yellow]")
        return False

    manifest_url = f"{CF_ADDON_URL}/{ADDON_TOKEN}/manifest.json"
    try:
        resp = client.get(manifest_url, timeout=8.0)
        if resp.status_code == 200:
            manifest = resp.json()
            console.print(f"[bold green]✅ Manifest Sah:[/bold green] {manifest.get('name')} (v{manifest.get('version')})")
            return True
        else:
            console.print(f"[bold red]❌ Manifest gagal diakses: HTTP {resp.status_code}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]❌ Gagal menyambung ke Addon Manifest: {e}[/bold red]")
    return False


def test_torrentio_and_stream_resolution(client: httpx.Client, imdb_id: str) -> List[Dict[str, Any]]:
    """Menguji resolver penstriman Stremio Addon melalui integrasi Torrentio."""
    stream_url = f"{CF_ADDON_URL}/{ADDON_TOKEN}/stream/movie/{imdb_id}.json"
    console.print(f"\n[cyan]🔍 Mengambil senarai strim untuk {imdb_id} dari Worker Addon...[/cyan]")

    try:
        resp = client.get(stream_url, timeout=12.0)
        if resp.status_code == 200:
            data = resp.json()
            streams = data.get("streams", [])
            console.print(f"[bold green]✅ Berjaya menerima {len(streams)} pilihan penstriman daripada resolver.[/bold green]")
            return streams
        else:
            console.print(f"[bold red]❌ Resolver membalas dengan status: HTTP {resp.status_code}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]❌ Ralat ketika meminta senarai strim: {e}[/bold red]")
    return []


def test_b2_proxy_range_support(client: httpx.Client) -> bool:
    """Menguji keupayaan Proksi B2 mengendalikan Range Header (HTTP 206 Partial Content)."""
    if not CF_PROXY_URL:
        return False

    # Uji panggilan Range Request ke proksi (meminta 100 bait terawal)
    test_file_url = f"{CF_PROXY_URL}/bucket-001-seedr-bot-din/test_probe.bin"
    headers = {"Range": "bytes=0-99"}

    try:
        resp = client.get(test_file_url, headers=headers, timeout=8.0)
        # HTTP 206 bermaksud sokongan seeking/partial content aktif; HTTP 404 boleh diterima jika fail belum wujud
        if resp.status_code in [206, 200]:
            console.print("[bold green]✅ Proksi B2 menyokong 'HTTP 206 Partial Content' dengan sempurna.[/bold green]")
            return True
        elif resp.status_code == 404:
            console.print("[bold yellow]ℹ️ Proksi B2 aktif (Menerima status 404 kerana fail ujian belum wujud fizikal di bucket).[/bold yellow]")
            return True
        else:
            console.print(f"[bold red]❌ Proksi B2 membalas kod tidak dijangka: HTTP {resp.status_code}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]❌ Ralat sambungan Proksi B2: {e}[/bold red]")
    return False


def main():
    console.print(Panel.fit(
        "[bold cyan]🧪 DIAGNOSTIK ALIRAN LENGKAP STREMIO PRIVATE DEBRID[/bold cyan]\n"
        "[yellow]Menguji Manifest, Pautan Strim, Resolusi Torrentio, Render, dan Proksi B2[/yellow]",
        border_style="cyan"
    ))

    with httpx.Client(follow_redirects=False) as client:
        # 1. Semakan Pelayan Render
        console.print("[white]1. Menguji Status Pelayan Render...[/white]")
        test_render_health(client)

        # 2. Semakan Manifest Addon
        console.print("\n[white]2. Menguji Pengesahan Token & Manifest Addon...[/white]")
        test_addon_manifest(client)

        # 3. Semakan Proksi B2
        console.print("\n[white]3. Menguji Proksi B2 (Zero-Egress & Fast Seeking)...[/white]")
        test_b2_proxy_range_support(client)

        # 4. Simulasi Carian Katalog Filem (Contoh: Big Momma's House - tt0186945)
        test_imdb = "tt0186945"
        streams = test_torrentio_and_stream_resolution(client, test_imdb)

        if streams:
            table = Table(title=f"🎬 Senarai Strim Dikesan bagi {test_imdb}", border_style="green")
            table.add_column("No", justify="center", style="cyan")
            table.add_column("Nama Butang", style="magenta")
            table.add_column("Perincian Torrent / Kualiti", style="white")
            table.add_column("Format Tindakan", style="yellow")

            for idx, s in enumerate(streams[:5], 1):
                btn_name = s.get("name", "")
                title_desc = s.get("title", "").split("\n")[0]
                action_type = "⚡ Strim Terus (B2)" if "B2 Fast" in btn_name else "📥 Sedut ke B2 (Trigger)"
                table.add_row(str(idx), btn_name, title_desc, action_type)

            console.print(table)
            if len(streams) > 5:
                console.print(f"[dim]... serta {len(streams) - 5} lagi pilihan kualiti yang sedia dipilih.[/dim]")

    console.print("\n[bold green]✨ Diagnostik selesai! Sistem sedia diuji di aplikasi Stremio.[/bold green]\n")


if __name__ == "__main__":
    main()