#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - MODUL MODULAR SMART RESOLVER (04_resolver.py)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/04_resolver.py
# CIRI: CURL-CFFI TLS IMPERSONATION (BYPASS CLOUDFLARE 403) + MULTI-STAGE APIBAY
# ==============================================================================

import re
import sys
from typing import Dict, Any, Optional, List

from curl_cffi import requests
from rich.console import Console

console = Console()


def detect_quality(name: str) -> str:
    """Mengesan kualiti pelepasan video daripada rentetan nama fail."""
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    return "HD"


def filter_and_rank_torrents(items: List[Dict[str, Any]], source_label: str) -> Optional[Dict[str, Any]]:
    """Menapis saiz (500MB - 9.0GB) dan mengutamakan Zon Emas (1.0GB - 4.0GB)."""
    MIN_BYTES = 500 * 1024 * 1024
    MAX_BYTES = 9.0 * 1024 * 1024 * 1024
    SWEET_MIN = 1.0 * 1024 * 1024 * 1024
    SWEET_MAX = 4.0 * 1024 * 1024 * 1024

    candidates = []
    for it in items:
        h = (it.get("info_hash") or it.get("hash") or "").strip().lower()
        title = (it.get("name") or it.get("title") or "").strip()
        if not h or len(h) != 40 or title == "No results returned":
            continue

        try:
            seeds = int(it.get("seeders") or it.get("seeds") or 0)
            sz = int(it.get("size") or it.get("size_bytes") or 0)
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
            "quality": detect_quality(title),
            "score": score,
            "source": source_label
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


def query_apibay(query_param: str, label: str) -> Optional[Dict[str, Any]]:
    """
    Menghubungi apibay.org menggunakan curl_cffi TLS impersonation
    untuk memintas sekatan Cloudflare Bot Management (HTTP 403).
    """
    url = f"https://apibay.org/q.php?q={query_param}"
    console.print(f"[cyan]📡 [{label}] Menghubungi apibay.org?q={query_param}...[/cyan]")

    # Penyamaran cap jari penyulitan pelayar sebenar secara bergilir
    browser_profiles = ["chrome120", "chrome110", "safari15_5"]

    for profile in browser_profiles:
        try:
            resp = requests.get(
                url,
                impersonate=profile,
                timeout=12,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://thepiratebay.org/",
                    "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="120", "Chromium";v="120"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                }
            )

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 0 and data[0].get("name") != "No results returned":
                        console.print(f"[green]✅ [{label}] Bypass Cloudflare berjaya ({profile})! {len(data)} entri diterima.[/green]")
                        return filter_and_rank_torrents(data, label)
                    else:
                        console.print(f"[dim]ℹ️ [{label}] Apibay memulangkan senarai kosong.[/dim]")
                        return None
                except Exception as je:
                    console.print(f"[yellow]⚠️ [{label}] Ralat huraian JSON: {je}[/yellow]")
                    return None

            elif resp.status_code == 403:
                console.print(f"[yellow]⚠️ [{label}] HTTP 403 dikesan dengan {profile}, mencuba profil lain...[/yellow]")
                continue
            else:
                console.print(f"[yellow]⚠️ [{label}] Status HTTP luar jangkaan: {resp.status_code}[/yellow]")

        except Exception as e:
            console.print(f"[dim]⚠️ [{label}] Ralat rangkaian ({profile}): {type(e).__name__} - {e}[/dim]")

    return None


def get_cinemeta_meta(imdb_id: str) -> tuple[str, str]:
    """Mengekstrak tajuk dan tahun rasmi daripada Cinemeta menggunakan curl-cffi."""
    base_id = imdb_id.split(":")[0]
    for mtype in ["movie", "series"]:
        url = f"https://v3-cinemeta.strem.io/meta/{mtype}/{base_id}.json"
        try:
            res = requests.get(url, impersonate="chrome120", timeout=8)
            if res.status_code == 200:
                meta = res.json().get("meta", {})
                name = meta.get("name", "")
                year = str(meta.get("year", "")).split("–")[0].split("-")[0].strip()
                if name:
                    return name, year
        except Exception:
            continue
    return "", ""


def query_yts_fallback(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Sandaran YTS API menggunakan curl-cffi."""
    base_id = imdb_id.split(":")[0]
    url = f"https://yts.mx/api/v2/list_movies.json?query_term={base_id}"
    console.print(f"[cyan]📡 [YTS API] Mencuba sandaran ke yts.mx untuk {base_id}...[/cyan]")
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            movies = resp.json().get("data", {}).get("movies", [])
            if movies and movies[0].get("torrents"):
                m = movies[0]
                torrents = m["torrents"]
                t = next((x for x in torrents if "1080p" in x.get("quality", "").lower()), torrents[0])
                sz = int(t.get("size_bytes", 0))
                return {
                    "name": f"{m.get('title')} ({m.get('year')}) [{t.get('quality', 'HD')}] [YTS]",
                    "info_hash": t["hash"].lower(),
                    "seeders": int(t.get("seeds", 10)),
                    "size": sz,
                    "quality": t.get("quality", "1080p"),
                    "source": "YTS Official API"
                }
    except Exception as e:
        console.print(f"[dim]ℹ️ [YTS API] Dikecualikan: {type(e).__name__}[/dim]")
    return None


def resolve_fallback(imdb_id: str, default_title: str) -> Optional[Dict[str, Any]]:
    """Enjin Resolver Berperingkat Pintar."""
    console.print(f"\n[bold cyan]🧠 ENJIN PINTAR: MEMULAKAN PROSES RESOLVE BAGI {imdb_id}[/bold cyan]")
    base_id = imdb_id.split(":")[0]

    # PERINGKAT 1: Carian Terus ID IMDb di Apibay (Paling Tepat & Berkesan)
    res = query_apibay(base_id, "Peringkat 1: Apibay ID")
    if res:
        return res

    # Ekstrak Metadata daripada Cinemeta
    meta_title, meta_year = get_cinemeta_meta(imdb_id)
    clean_title = meta_title or default_title
    clean_title = re.sub(r"[^\w\s]", " ", clean_title).strip()

    # PERINGKAT 2: Carian Apibay Tajuk + Tahun
    if clean_title and meta_year:
        search_query_1 = f"{clean_title} {meta_year}"
        res = query_apibay(search_query_1, "Peringkat 2: Apibay Tajuk+Tahun")
        if res:
            return res

    # PERINGKAT 3: Carian Apibay Tajuk Sahaja
    if clean_title:
        res = query_apibay(clean_title, "Peringkat 3: Apibay Tajuk Sahaja")
        if res:
            return res

    # PERINGKAT 4: Sandaran ke YTS API
    res = query_yts_fallback(imdb_id)
    if res:
        return res

    console.print("[bold red]❌ Kesemua 4 peringkat pintar telah dicuba dan tiada torrent ditemui.[/bold red]")
    return None