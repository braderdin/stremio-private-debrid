#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - ALAT PENYEGERAKAN RENDER API (CLI)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/05_render_sync.py
# ==============================================================================

import os
import sys
import time
import argparse
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Pemuatan Konfigurasi dari 00_config
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    _config = importlib.import_module("00_config")
    RENDER_API_KEY = getattr(_config, "RENDER_API_KEY", "")
    RENDER_SERVICE_ID = getattr(_config, "RENDER_SERVICE_ID", "")
except Exception as e:
    console.print(f"[bold red]❌ Ralat membaca konfigurasi: {e}[/bold red]")
    RENDER_API_KEY = ""
    RENDER_SERVICE_ID = ""

RENDER_BASE_URL = "https://api.render.com/v1"


def get_headers() -> Dict[str, str]:
    """Membina header pengesahan rasmi untuk Render REST API."""
    return {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }


def list_render_services() -> List[Dict[str, Any]]:
    """Mendapatkan senarai semua perkhidmatan yang didaftarkan di bawah akaun Render."""
    if not RENDER_API_KEY:
        console.print("[bold red]❌ RENDER_API_KEY tiada di dalam fail .env.local![/bold red]")
        return []

    url = f"{RENDER_BASE_URL}/services?limit=20"
    with httpx.Client(timeout=12.0) as client:
        try:
            resp = client.get(url, headers=get_headers())
            if resp.status_code == 200:
                data = resp.json()
                # Respons Render mengembalikan senarai objek berbalut: [{'service': {...}}, ...]
                return [item["service"] for item in data if "service" in item]
            else:
                console.print(f"[bold red]❌ Gagal membaca senarai perkhidmatan Render (HTTP {resp.status_code}): {resp.text}[/bold red]")
        except Exception as e:
            console.print(f"[bold red]❌ Ralat sambungan Render API: {e}[/bold red]")
    return []


def trigger_render_deploy(service_id: str, clear_cache: bool = False) -> bool:
    """Memicu proses muat naik / deploy binaan baharu secara terus dari terminal."""
    if not RENDER_API_KEY:
        console.print("[bold red]❌ RENDER_API_KEY tidak sah![/bold red]")
        return False

    url = f"{RENDER_BASE_URL}/services/{service_id}/deploys"
    payload = {"clearCache": "clear" if clear_cache else "do_not_clear"}

    with httpx.Client(timeout=15.0) as client:
        try:
            resp = client.post(url, headers=get_headers(), json=payload)
            if resp.status_code in [200, 201]:
                deploy_info = resp.json()
                deploy_id = deploy_info.get("id", "N/A")
                console.print(f"[bold green]🚀 Deploy baharu berjaya dipicu! (Deploy ID: {deploy_id})[/bold green]")
                return True
            else:
                console.print(f"[bold red]❌ Gagal memicu deploy (HTTP {resp.status_code}): {resp.text}[/bold red]")
        except Exception as e:
            console.print(f"[bold red]❌ Ralat ketika menghantar permintaan deploy: {e}[/bold red]")
    return False


def get_latest_deploy_status(service_id: str):
    """Menyemak status deploy terkini bagi perkhidmatan Render."""
    url = f"{RENDER_BASE_URL}/services/{service_id}/deploys?limit=1"
    with httpx.Client(timeout=12.0) as client:
        try:
            resp = client.get(url, headers=get_headers())
            if resp.status_code == 200:
                deploys = resp.json()
                if deploys:
                    dep = deploys[0]["deploy"]
                    status_text = dep.get("status", "unknown").upper()
                    color = "green" if status_text == "LIVE" else ("yellow" if "BUILD" in status_text or "PROGRESS" in status_text else "red")
                    
                    console.print(f"Status Deploy Terkini: [{color}]{status_text}[/{color}]")
                    console.print(f"Masa Mula: [dim]{dep.get('createdAt')}[/dim]")
                    console.print(f"Mesej Commit: {dep.get('commit', {}).get('message', 'Manual trigger')}")
        except Exception as e:
            console.print(f"[bold red]❌ Gagal menyemak status deploy: {e}[/bold red]")


def print_services_table(services: List[Dict[str, Any]]):
    """Memaparkan jadual perkhidmatan Render menggunakan Rich Console."""
    table = Table(title="🌐 Senarai Web Service Aktif di Render", border_style="cyan")
    table.add_column("No", justify="center", style="cyan")
    table.add_column("Nama Servis", style="white")
    table.add_column("Service ID (Salin ke .env.local)", style="yellow")
    table.add_column("Jenis", style="magenta")
    table.add_column("URL Servis", style="green")
    table.add_column("Kemas Kini", style="dim")

    for idx, s in enumerate(services, 1):
        srv_id = s.get("id", "")
        srv_name = s.get("name", "")
        srv_type = s.get("type", "")
        srv_url = s.get("serviceDetails", {}).get("url", "N/A")
        updated = s.get("updatedAt", "")[:19].replace("T", " ")

        table.add_row(str(idx), srv_name, srv_id, srv_type, srv_url, updated)

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="Render Management & Deployment CLI")
    parser.add_argument("--list", action="store_true", help="Papar semua perkhidmatan Render")
    parser.add_argument("--deploy", action="store_true", help="Picu deploy baharu untuk RENDER_SERVICE_ID")
    parser.add_argument("--clear-cache", action="store_true", help="Padam cache binaan semasa deploy")
    parser.add_argument("--status", action="store_true", help="Semak status deploy terkini")
    args = parser.parse_args()

    console.print(Panel.fit(
        "[bold cyan]🛠️ RENDER SERVICE MANAGER & SYNC ENGINE[/bold cyan]\n"
        "[yellow]Menguruskan Web Service Render terus dari Terminal WSL[/yellow]",
        border_style="cyan"
    ))

    # Jika tiada argumen diberikan, paparkan senarai servis secara lalai
    if not any([args.list, args.deploy, args.status]):
        args.list = True

    if args.list:
        services = list_render_services()
        if services:
            print_services_table(services)
            console.print("\n[bold green]💡 Petua:[/bold green] Salin [bold yellow]Service ID[/bold yellow] di atas ke dalam [bold cyan].env.local[/bold cyan] pada pembolehubah [bold cyan]RENDER_SERVICE_ID[/bold cyan].\n")

    target_id = RENDER_SERVICE_ID
    if args.deploy:
        if not target_id:
            console.print("[bold red]❌ RENDER_SERVICE_ID belum diisi dalam .env.local! Jalankan skrip ini dengan --list dahulu untuk menyalin ID servis.[/bold red]")
            return
        trigger_render_deploy(target_id, clear_cache=args.clear_cache)

    if args.status:
        if not target_id:
            console.print("[bold red]❌ Sila tetapkan RENDER_SERVICE_ID dalam .env.local terlebih dahulu.[/bold red]")
            return
        get_latest_deploy_status(target_id)


if __name__ == "__main__":
    main()