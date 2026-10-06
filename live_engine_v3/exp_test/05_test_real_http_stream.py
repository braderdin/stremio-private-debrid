#!/usr/bin/env python3
"""
Module: 05_test_real_http_stream.py
Path: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/05_test_real_http_stream.py

Penerangan:
Skrip ujian HTTP Stream Resolver SEBENAR (Zero Dummy).
Mengekstrak pautan video langsung daripada arkib media awam berasaskan kata kunci,
menyemak status sambungan HTTP 200/206, dan memaparkan URL sedia tonton.
"""

import sys
import time
import urllib.parse
import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

def format_bytes(size_bytes: int) -> str:
    if not size_bytes or size_bytes <= 0:
        return "N/A"
    gb = size_bytes / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.2f} GB"
    mb = size_bytes / (1024 ** 2)
    return f"{mb:.1f} MB"

def fetch_real_http_streams(query: str, max_results: int = 5):
    console.print(f"[bold cyan]🔍 Mencari strim HTTP langsung untuk:[/] [yellow]'{query}'[/yellow]...")
    
    # 1. Panggil API carian Arkib Awam (Archive.org Media API)
    search_url = (
        "https://archive.org/advancedsearch.php?"
        f"q={urllib.parse.quote(query)}+AND+mediatype:movies"
        "&fl[]=identifier,title,year"
        f"&rows={max_results}&page=1&output=json"
    )
    
    start_time = time.perf_counter()
    try:
        res = requests.get(search_url, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        console.print(f"[bold red]Ralat carian API:[/] {e}")
        return []

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        console.print("[yellow]Tiada rekod ditemui untuk kata kunci tersebut.[/yellow]")
        return []

    valid_streams = []

    # 2. Dapatkan senarai fail sebenar bagi setiap rekod
    for item in docs:
        identifier = item.get("identifier")
        title = item.get("title", identifier)
        meta_url = f"https://archive.org/metadata/{identifier}/files"

        try:
            meta_res = requests.get(meta_url, timeout=8)
            if not meta_res.status_code == 200:
                continue
            
            files = meta_res.json().get("result", [])
            for f in files:
                fname = f.get("name", "")
                fmt = f.get("format", "")

                # Pilih hanya fail video MP4 atau h.264 sebenar
                if fname.lower().endswith(".mp4") or "h.264" in fmt.lower():
                    direct_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(fname)}"
                    size = int(f.get("size", 0))

                    # 3. Lakukan semakan sambungan pantas (HTTP HEAD)
                    ping_start = time.perf_counter()
                    head_res = requests.head(direct_url, timeout=5, allow_redirects=True)
                    latency_ms = (time.perf_counter() - ping_start) * 1000

                    if head_res.status_code in [200, 206, 302]:
                        valid_streams.append({
                            "title": title,
                            "filename": fname,
                            "url": direct_url,
                            "size": size,
                            "latency": latency_ms,
                            "status": "ONLINE (200 OK)"
                        })
                        break  # Ambil fail terbaik bagi setiap entri
        except Exception:
            continue

    total_latency = (time.perf_counter() - start_time) * 1000
    console.print(f"[bold green]✔ Selesai dalam {total_latency:.2f} ms[/bold green]\n")
    return valid_streams

def display_table(streams):
    if not streams:
        return

    table = Table(
        title="🎬 Hasil Carian Strim HTTP Sebenar (Live Streams)",
        show_header=True,
        header_style="bold cyan",
        border_style="bright_blue"
    )

    table.add_column("No", justify="center", width=4)
    table.add_column("Tajuk / Media", style="bold white", width=28)
    table.add_column("Saiz Fail", justify="right", style="green", width=12)
    table.add_column("Latensi", justify="right", style="yellow", width=10)
    table.add_column("URL Strim Sebenar (Bukan Dummy)", style="magenta", width=55)

    for idx, s in enumerate(streams, 1):
        table.add_row(
            str(idx),
            s["title"][:26],
            format_bytes(s["size"]),
            f"{s['latency']:.1f} ms",
            s["url"]
        )

    console.print(table)
    console.print("\n[bold underline green]Pautan Ujian Sedia Dimainkan:[/bold underline green]")
    for idx, s in enumerate(streams, 1):
        console.print(f"[{idx}] {s['url']}")

if __name__ == "__main__":
    console.print(Panel.fit(
        "[bold green]Ujian Langsung: Pengekstrak Strim HTTP Sebenar[/bold green]\n"
        "[dim]Mengakses pelayan arkib video langsung tanpa sebarang data olok-olok...[/dim]",
        border_style="green"
    ))

    # Boleh tukar tajuk filem/video awam di sini (contoh: 'Night of the Living Dead' atau tajuk lain)
    query_target = "Night of the Living Dead"
    if len(sys.argv) > 1:
        query_target = " ".join(sys.argv[1:])

    results = fetch_real_http_streams(query_target)
    display_table(results)