#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - RESOLUSI METADATA PINTAR (CINEMETA & TMDB)
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/01_meta_resolver.py
# ==============================================================================

import re
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import quote

import httpx
from rich.console import Console
from rich.table import Table

# Import tetapan laluan dan kunci API dari 00_scrape_config
SCRAPE_DIR = Path(__file__).resolve().parent
if str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

import X00_scrape_config as cfg

console = Console()


class MetaResolver:
    def __init__(self):
        self.timeout = httpx.Timeout(12.0, connect=6.0)
        self.headers = {"User-Agent": cfg.USER_AGENT_DESKTOP}

    def parse_imdb_id(self, raw_id: str) -> Dict[str, Any]:
        """Menganalisis format ID IMDb sama ada Filem (tt...) atau Siri (tt...:S:E)."""
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
        """Mendapatkan maklumat asas tajuk dan tahun dari Stremio Cinemeta API."""
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

                        if name:
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
        """Mengekstrak tajuk Inggeris, tajuk asal Cina/Asia, dan alternatif melalui TMDB."""
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

                # Ambil senarai tajuk alternatif bagi mengesan aksara Cina/Pinyin
                alt_titles = []
                if tmdb_id:
                    alt_url = f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/alternative_titles"
                    alt_res = client.get(alt_url, params=params)
                    if alt_res.status_code == 200:
                        alt_data = alt_res.json()
                        raw_alts = alt_data.get("titles" if tmdb_type == "movie" else "results", [])
                        for a in raw_alts:
                            t_val = (a.get("title") or "").strip()
                            if t_val and t_val.lower() != title.lower() and t_val not in alt_titles:
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
        """Menyatukan metadata Cinemeta dan TMDB serta menyusun calon kata carian."""
        parsed = self.parse_imdb_id(raw_imdb_id)
        base_imdb = parsed["base_imdb"]

        console.print(f"[cyan]🔍 Menyelesaikan metadata untuk: [bold]{raw_imdb_id}[/bold]...[/cyan]")

        tmdb_data = self.fetch_tmdb_metadata(base_imdb)
        cinemeta_data = self.fetch_cinemeta_metadata(base_imdb, parsed["media_type"])

        # Tentukan tajuk utama dan tahun
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

        if not title:
            # Sandaran terakhir jika kedua-dua API tiada jawapan
            title = base_imdb

        # Bina senarai susunan carian laman web mengikut keutamaan (Priority Search Queries)
        search_queries: List[str] = []

        def add_query(q: str):
            clean = re.sub(r"[:\-_/]", " ", q).strip()
            clean = re.sub(r"\s+", " ", clean)
            if clean and clean not in search_queries:
                search_queries.append(clean)

        # 1. Utamakan tajuk asal jika wujud (terutamanya tulisan Cina / tajuk tempatan Asia)
        if orig_title:
            add_query(orig_title)

        # 2. Tajuk rasmi antarabangsa
        if title:
            add_query(title)

        # 3. Tajuk-tajuk alternatif dari TMDB
        for alt in alt_titles:
            add_query(alt)

        return {
            "imdb_id": parsed["full_id"],
            "base_imdb": base_imdb,
            "is_series": parsed["is_series"],
            "season": parsed["season"],
            "episode": parsed["episode"],
            "media_type": parsed["media_type"],
            "title": title,
            "original_title": orig_title,
            "year": year,
            "search_queries": search_queries
        }


def main():
    parser = argparse.ArgumentParser(description="Penyelesai Metadata Cinemeta & TMDB")
    parser.add_argument("--imdb", default="tt0097576", help="IMDb ID untuk diuji (cth: tt0097576 atau tt0944947:1:1)")
    args = parser.parse_args()

    resolver = MetaResolver()
    result = resolver.resolve(args.imdb)

    table = Table(title="🎯 Hasil Resolusi Metadata", border_style="green")
    table.add_column("Parameter", style="cyan")
    table.add_column("Nilai Dikenal Pasti", style="white")

    table.add_row("IMDb ID", result["imdb_id"])
    table.add_row("Jenis Media", result["media_type"].upper())
    table.add_row("Tajuk Utama", result["title"])
    table.add_row("Tajuk Asal (Asia/Cina)", result["original_title"] or "-")
    table.add_row("Tahun", result["year"] or "-")
    table.add_row("Keutamaan Carian Web", " -> ".join(result["search_queries"][:3]))

    console.print(table)


if __name__ == "__main__":
    main()