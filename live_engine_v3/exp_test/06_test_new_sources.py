#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - EKSPERIMEN PUNCA BAHARU (DIKEMAS KINI)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/06_test_new_sources.py
# ==============================================================================

import re
import sys
import time
import argparse
from urllib.parse import quote_plus, unquote
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


def detect_quality(name: str) -> str:
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    if any(q in n for q in ["480p", "sd", "dvdrip"]):
        return "480p"
    return "HD"


def clean_search_title(raw_title: str) -> str:
    t = re.sub(r"\.{2,}", " ", raw_title)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def fetch_cinemeta_info(imdb_id: str, is_series: bool) -> Dict[str, str]:
    base_id = imdb_id.split(":")[0]
    ep_type = "series" if is_series else "movie"
    url = f"https://v3-cinemeta.strem.io/meta/{ep_type}/{base_id}.json"
    meta = {"title": base_id, "year": "", "clean_title": base_id}
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            name = data.get("name", "")
            year = str(data.get("year", ""))
            if name:
                meta = {
                    "title": name,
                    "year": year,
                    "clean_title": clean_search_title(name),
                }
    except Exception as e:
        console.print(f"[dim red]Cinemeta API Error: {e}[/dim red]")
    return meta


def parse_generic_stream(stream: Dict[str, Any], default_source: str) -> Optional[Dict[str, Any]]:
    raw_title = stream.get("title", "")
    info_hash = (stream.get("infoHash") or "").strip().lower()
    file_idx = stream.get("fileIdx", 0)

    if len(info_hash) != 40 or not raw_title:
        return None

    lines = [l.strip() for l in raw_title.split("\n") if l.strip()]

    seeds = 0
    size_bytes = 0
    source_site = default_source

    for line in lines:
        seeds_match = re.search(r"[👤👥]\s*([\d,]+)", line) or re.search(
            r"(?:Seeds?|Seeders?):\s*([\d,]+)", line, re.IGNORECASE
        )
        if seeds_match:
            seeds = int(seeds_match.group(1).replace(",", ""))

        size_match = re.search(
            r"[💾💿]\s*([\d\.]+)\s*(GB|MB|KB|GiB|MiB)", line, re.IGNORECASE
        ) or re.search(
            r"(?:Size):\s*([\d\.]+)\s*(GB|MB|KB|GiB|MiB)", line, re.IGNORECASE
        )
        if size_match:
            val = float(size_match.group(1))
            unit = size_match.group(2).upper()
            if "GB" in unit:
                size_bytes = int(val * 1024**3)
            elif "MB" in unit:
                size_bytes = int(val * 1024**2)
            elif "KB" in unit:
                size_bytes = int(val * 1024)

        if any(icon in line for icon in ["⚙️", "🌐", "🏷"]):
            src_m = re.search(r"[⚙🌐🏷️]\s*([\w\+\.\-]+)", line)
            if src_m:
                source_site = src_m.group(1)

    release_name = lines[0] if lines else default_source
    if len(lines) >= 2 and not any(ic in lines[1] for ic in ["💾", "👤", "⚙️"]):
        release_name = f"{lines[1]} [{lines[0]}]"

    stream_name = stream.get("name", "")
    quality = detect_quality(f"{stream_name} {raw_title}")

    return {
        "name": release_name,
        "info_hash": info_hash,
        "seeders": seeds,
        "size": size_bytes,
        "quality": quality,
        "source": source_site,
        "file_idx": int(file_idx or 0),
    }


# ------------------------------------------------------------------------------
# UJIAN SUMBER-SUMBER BAHARU & DIPERBAIKI
# ------------------------------------------------------------------------------

# 1. YTS Langsung Berasaskan Kod IMDb (Membetulkan Isu X06)
def test_scrape_yts_direct(base_imdb_id: str, is_series: bool) -> List[Dict[str, Any]]:
    if is_series:
        return []
    url = f"https://yts.mx/api/v2/list_movies.json?query_term={quote_plus(base_imdb_id)}"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
            data = resp.json().get("data", {})
            for m in data.get("movies", []):
                m_title = m.get("title", "")
                m_year = m.get("year", "")
                for t in m.get("torrents", []):
                    h = (t.get("hash") or "").strip().lower()
                    if len(h) == 40:
                        q_str = t.get("quality", "HD")
                        results.append({
                            "name": f"{m_title} ({m_year}) [{q_str}] [YTS.MX]",
                            "info_hash": h,
                            "seeders": int(t.get("seeds", 0)),
                            "size": int(t.get("size_bytes", 0)),
                            "quality": q_str,
                            "source": "YTS",
                            "file_idx": 0,
                        })
    except Exception as e:
        console.print(f"[dim red]YTS Direct Error: {e}[/dim red]")
    return results


# 2. Torrentio Turbo (Semua Provider Diaktifkan)
def test_scrape_torrentio_full(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    ep_type = "series" if is_series else "movie"
    providers_str = (
        "providers=yts,eztv,rarbg,1337x,thepiratebay,kickasstorrents,"
        "torrentgalaxy,magnetdl,horriblesubs,nyaasi,tokyotosho,anidex,"
        "rutor,rutracker,comando,bludv,torrent9,ilcorsaronero,mejortorrent,"
        "wolfmax4k,cinecalidad,besttorrents|sort=seeders"
    )
    url = f"https://torrentio.strem.fun/{providers_str}/stream/{ep_type}/{target_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=12)
        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
            for s in resp.json().get("streams", []):
                p = parse_generic_stream(s, default_source="Torrentio")
                if p:
                    results.append(p)
    except Exception as e:
        console.print(f"[dim red]Torrentio Error: {e}[/dim red]")
    return results


# 3. Addon Tersuai (Uji URL Addon TorrentsDB / MediaFusion dari Stremio Anda)
def test_scrape_custom_addon(custom_base_url: str, target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    if not custom_base_url:
        return []
    ep_type = "series" if is_series else "movie"
    base = custom_base_url.rstrip("/")
    if base.endswith("/manifest.json"):
        base = base[:-14]

    url = f"{base}/stream/{ep_type}/{target_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=12)
        if resp.status_code == 200:
            ctype = resp.headers.get("content-type", "")
            if "application/json" not in ctype:
                console.print(f"[yellow]⚠️️ Custom Addon memulangkan bukan-JSON (Content-Type: {ctype}).[/yellow]")
                return []
            for s in resp.json().get("streams", []):
                p = parse_generic_stream(s, default_source="CustomAddon")
                if p:
                    results.append(p)
        else:
            console.print(f"[yellow]⚠️ Custom Addon HTTP Status: {resp.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[dim red]Custom Addon Error: {e}[/dim red]")
    return results


def run_diagnostic_test(raw_imdb_id: str, custom_addon_url: str = ""):
    target_id = unquote(raw_imdb_id).strip()
    is_series = ":" in target_id
    kind = "SIRI TV" if is_series else "FILEM"
    base_id = target_id.split(":")[0]

    console.print(Panel.fit(
        f"[bold yellow]🧪 DIAGNOSTIK ENJIN BAHARU: UJIAN SUMBER DIPERBAIKI[/bold yellow]\n"
        f"Target ID: [cyan]{target_id}[/cyan] | Jenis: [magenta]{kind}[/magenta]",
        border_style="yellow",
    ))

    meta = fetch_cinemeta_info(base_id, is_series)
    console.print(f"🎬 [bold white]{meta['title']}[/bold white] ({meta['year'] or 'N/A'})")

    all_results: Dict[str, List[Dict[str, Any]]] = {}
    timings: Dict[str, float] = {}

    tasks = {
        "YTS_Direct": lambda: test_scrape_yts_direct(base_id, is_series),
        "Torrentio_Turbo": lambda: test_scrape_torrentio_full(target_id, is_series),
    }

    if custom_addon_url:
        tasks["Custom_Addon"] = lambda: test_scrape_custom_addon(custom_addon_url, target_id, is_series)

    start_total = time.time()
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {executor.submit(func): (name, time.time()) for name, func in tasks.items()}
        for future in as_completed(future_map):
            name, t0 = future_map[future]
            try:
                res = future.result()
                timings[name] = time.time() - t0
                all_results[name] = res
            except Exception as e:
                timings[name] = time.time() - t0
                all_results[name] = []
                console.print(f"[red]❌ Ralat ketika memproses {name}: {e}[/red]")

    console.print(f"\n[green]⏱️ Masa Selesai Keseluruhan: {time.time() - start_total:.2f} saat[/green]\n")

    summary_table = Table(title="📊 Ringkasan Penemuan Sumber", border_style="cyan")
    summary_table.add_column("Nama Sumber", style="yellow")
    summary_table.add_column("Status", justify="center")
    summary_table.add_column("Jumlah Ditemui", justify="center", style="bold green")
    summary_table.add_column("Masa Respons", justify="center", style="cyan")

    for name in tasks.keys():
        items = all_results.get(name, [])
        status = "[bold green]BERJAYA[/bold green]" if items else "[bold yellow]TIADA HASIL[/bold yellow]"
        summary_table.add_row(name, status, str(len(items)), f"{timings.get(name, 0.0):.2f}s")

    console.print(summary_table)

    for name, items in all_results.items():
        if not items:
            continue

        detail_table = Table(title=f"🔎 Contoh Data Diperoleh: {name}", border_style="dim")
        detail_table.add_column("No", width=4, justify="center")
        detail_table.add_column("Punca", width=16, style="yellow")
        detail_table.add_column("Kualiti", width=8, justify="center")
        detail_table.add_column("Saiz", width=10)
        detail_table.add_column("Seeds", width=7, justify="center", style="bold green")
        detail_table.add_column("Nama Fail / Info Hash", style="dim")

        sorted_items = sorted(items, key=lambda x: x["seeders"], reverse=True)
        for idx, it in enumerate(sorted_items[:6], 1):
            sz = it["size"]
            sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
            detail_table.add_row(
                str(idx),
                str(it.get("source", "N/A")),
                it.get("quality", "HD"),
                sz_str,
                str(it.get("seeders", 0)),
                f"{it.get('name', '')[:45]}\n[cyan]{it['info_hash']}[/cyan]",
            )
        console.print(detail_table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Diagnostic Torrent Sources")
    parser.add_argument("--imdb", required=True, help="IMDb ID (cth: tt15748830)")
    parser.add_argument("--custom_addon", default="", help="Pautan addon Stremio (cth: TorrentsDB link)")
    args = parser.parse_args()

    run_diagnostic_test(args.imdb, args.custom_addon)