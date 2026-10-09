#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - RESOLUSI METADATA PINTAR (CINEMETA & TMDB)
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X01_meta_resolver.py
# ==============================================================================

import re
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from rich.console import Console

SCRAPE_DIR = Path(__file__).resolve().parent
if str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

import X00_scrape_config as cfg

console = Console()


def is_imdb_code(text: str) -> bool:
    """Menyemak sama ada teks ialah kod IMDb (cth: tt5776858 atau tt0816692:1:1)."""
    clean = text.strip()
    return bool(re.match(r"^tt\d+(:.+)?$", clean, re.IGNORECASE))


class MetaResolver:
    def __init__(self):
        self.timeout = httpx.Timeout(12.0, connect=6.0)
        self.headers = {"User-Agent": cfg.USER_AGENT_DESKTOP}

    def parse_imdb_id(self, raw_id: str) -> Dict[str, Any]:
        clean_id = raw_id.strip()
        is_series = ":" in clean_id

        base_id = clean_id
        season = 1
        episode = 1

        if is_series:
            parts = clean_id.split(":")
            base_id = parts[0]
            try:
                season = int(parts[1]) if len(parts) > 1 else 1
                episode = int(parts[2]) if len(parts) > 2 else 1
            except ValueError:
                season, episode = 1, 1

        return {
            "full_id": clean_id,
            "base_imdb": base_id,
            "is_series": is_series,
            "season": season,
            "episode": episode,
            "media_type": "series" if is_series else "movie"
        }

    def fetch_cinemeta_metadata(self, base_imdb: str, media_type: str) -> Optional[Dict[str, Any]]:
        types_to_try = [media_type, "movie" if media_type == "series" else "series"]
        for m_type in types_to_try:
            url = f"https://v3-cinemeta.strem.io/meta/{m_type}/{base_imdb}.json"
            try:
                with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        meta = resp.json().get("meta", {})
                        name = meta.get("name", "").strip()
                        raw_year = str(meta.get("year", "")).strip()
                        year = raw_year.split("–")[0].split("-")[0].strip()
                        if name and not is_imdb_code(name):
                            return {
                                "title": name,
                                "year": year,
                                "type": m_type,
                                "source": "Cinemeta"
                            }
            except Exception:
                continue
        return None

    def fetch_tmdb_metadata(self, base_imdb: str) -> Optional[Dict[str, Any]]:
        if not (cfg.TMDB_READ_TOKEN or cfg.TMDB_API_KEY):
            return None

        find_url = f"https://api.themoviedb.org/3/find/{base_imdb}?external_source=imdb_id"
        headers = dict(self.headers)
        if cfg.TMDB_READ_TOKEN:
            headers["Authorization"] = f"Bearer {cfg.TMDB_READ_TOKEN}"
        params = {}
        if not cfg.TMDB_READ_TOKEN and cfg.TMDB_API_KEY:
            params["api_key"] = cfg.TMDB_API_KEY

        try:
            with httpx.Client(timeout=self.timeout, headers=headers) as client:
                res = client.get(find_url, params=params)
                if res.status_code != 200:
                    return None

                data = res.json()
                movie_results = data.get("movie_results", [])
                tv_results = data.get("tv_results", [])

                target = None
                tmdb_type = "movie"
                if movie_results:
                    target = movie_results[0]
                    tmdb_type = "movie"
                elif tv_results:
                    target = tv_results[0]
                    tmdb_type = "tv"

                if not target:
                    return None

                tmdb_id = target.get("id")
                title = target.get("title") or target.get("name") or ""
                orig_title = target.get("original_title") or target.get("original_name") or ""
                date_str = target.get("release_date") or target.get("first_air_date") or ""
                year = date_str.split("-")[0].strip() if date_str else ""

                alt_titles = []
                if tmdb_id:
                    alt_url = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/alternative_titles"
                    alt_res = client.get(alt_url, params=params)
                    if alt_res.status_code == 200:
                        alt_data = alt_res.json()
                        raw_alts = alt_data.get("titles" if tmdb_type == "movie" else "results", [])
                        for a in raw_alts:
                            t_val = (a.get("title") or "").strip()
                            if t_val and t_val.lower() != title.lower() and not is_imdb_code(t_val):
                                if t_val not in alt_titles:
                                    alt_titles.append(t_val)

                return {
                    "title": title,
                    "original_title": orig_title,
                    "year": year,
                    "alt_titles": alt_titles[:5],
                    "source": "TMDB"
                }
        except Exception:
            return None

    def resolve(self, raw_imdb_id: str) -> Dict[str, Any]:
        parsed = self.parse_imdb_id(raw_imdb_id)
        base_imdb = parsed["base_imdb"]

        console.print(f"[cyan]🔍 Menyelesaikan metadata untuk: [bold]{raw_imdb_id}[/bold]...[/cyan]")

        tmdb_data = self.fetch_tmdb_metadata(base_imdb)
        cinemeta_data = self.fetch_cinemeta_metadata(base_imdb, parsed["media_type"])

        title = ""
        orig_title = ""
        year = ""
        alt_titles = []

        if tmdb_data:
            title = tmdb_data.get("title", "")
            orig_title = tmdb_data.get("original_title", "")
            year = tmdb_data.get("year", "")
            alt_titles = tmdb_data.get("alt_titles", [])

        if cinemeta_data:
            if not title:
                title = cinemeta_data.get("title", "")
            if not year:
                year = cinemeta_data.get("year", "")

        # Susun calon kata carian mengikut keutamaan (WAJIB teks tajuk sebenar, HARAM kod IMDb)
        search_queries: List[str] = []

        def add_query(q: str):
            clean = re.sub(r"[:\-_/]", " ", q).strip()
            clean = re.sub(r"\s+", " ", clean)
            if clean and not is_imdb_code(clean) and clean not in search_queries:
                search_queries.append(clean)

        # 1. Utamakan tajuk asal (terutamanya tulisan Cina / tajuk rasmi Asia)
        if orig_title:
            add_query(orig_title)

        # 2. Tajuk antarabangsa Inggeris
        if title:
            add_query(title)

        # 3. Tajuk alternatif TMDB
        for alt in alt_titles:
            add_query(alt)

        console.print(f"[green]✓ Tajuk Dikenal Pasti: [bold]{title}[/bold] (Asal: {orig_title or '-'}) | Calon: {search_queries}[/green]")

        return {
            "imdb_id": parsed["full_id"],
            "base_imdb": base_imdb,
            "is_series": parsed["is_series"],
            "season": parsed["season"],
            "episode": parsed["episode"],
            "media_type": parsed["media_type"],
            "title": title or base_imdb,
            "original_title": orig_title,
            "year": year,
            "search_queries": search_queries
        }