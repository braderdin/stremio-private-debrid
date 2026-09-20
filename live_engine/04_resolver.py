#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - MODUL MODULAR RESOLVER SANDARAN (STRATEGI A)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/04_resolver.py
# ==============================================================================

import re
from typing import Dict, Any, Optional
import httpx
from rich.console import Console

console = Console()


def get_metadata(imdb_id: str) -> tuple[str, str]:
    base_id = imdb_id.split(":")[0]
    try:
        url = f"https://v3-cinemeta.strem.io/meta/movie/{base_id}.json"
        with httpx.Client(timeout=6.0) as client:
            r = client.get(url)
            if r.status_code == 200:
                meta = r.json().get("meta", {})
                return meta.get("name", base_id), str(meta.get("year", ""))
    except Exception:
        pass
    return base_id, ""


def query_yts(imdb_id: str) -> Optional[Dict[str, Any]]:
    base_id = imdb_id.split(":")[0]
    try:
        with httpx.Client(timeout=8.0) as client:
            r = client.get(f"https://yts.mx/api/v2/list_movies.json?query_term={base_id}")
            if r.status_code == 200:
                movies = r.json().get("data", {}).get("movies", [])
                if movies and movies[0].get("torrents"):
                    torrents = movies[0]["torrents"]
                    t = next((x for x in torrents if "1080p" in x.get("quality", "").lower()), torrents[0])
                    return {
                        "name": f"{movies[0]['title']} ({movies[0].get('year')}) [{t.get('quality')}] [YTS]",
                        "info_hash": t["hash"].lower(),
                        "size": int(t.get("size_bytes", 0)),
                        "seeders": int(t.get("seeds", 10)),
                    }
    except Exception as e:
        console.print(f"[dim]YTS sandaran gagal: {e}[/dim]")
    return None


def query_apibay(query_term: str) -> Optional[Dict[str, Any]]:
    try:
        clean = re.sub(r"[^\w\s]", " ", query_term).strip()
        with httpx.Client(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(f"https://apibay.org/q.php?q={clean}")
            if r.status_code == 200:
                items = r.json()
                if isinstance(items, list) and items and items[0].get("name") != "No results returned":
                    valid = []
                    for it in items:
                        seeds = int(it.get("seeders", 0))
                        sz = int(it.get("size", 0))
                        h = it.get("info_hash", "").strip()
                        if seeds >= 1 and len(h) == 40 and (500 * 1024 * 1024 <= sz <= 9 * 1024 * 1024 * 1024):
                            valid.append({"name": it["name"], "info_hash": h.lower(), "size": sz, "seeders": seeds})
                    if valid:
                        valid.sort(key=lambda x: x["seeders"], reverse=True)
                        return valid[0]
    except Exception as e:
        console.print(f"[dim]Apibay sandaran gagal: {e}[/dim]")
    return None


def resolve_fallback(imdb_id: str, default_title: str) -> Optional[Dict[str, Any]]:
    console.print(f"[cyan]🔍 Menjalankan Resolver Sandaran untuk: {imdb_id}...[/cyan]")
    title, year = get_metadata(imdb_id)
    search_title = f"{title} {year}".strip() if year else title

    # Sandaran 1: YTS
    res = query_yts(imdb_id)
    if res:
        console.print(f"[green]✅ Ditemui via YTS Sandaran: {res['name']}[/green]")
        return res

    # Sandaran 2: Apibay Tajuk
    res = query_apibay(search_title)
    if res:
        console.print(f"[green]✅ Ditemui via Apibay Sandaran: {res['name']}[/green]")
        return res

    return None