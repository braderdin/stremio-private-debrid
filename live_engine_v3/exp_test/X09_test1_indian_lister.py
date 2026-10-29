#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - EXPERIMENTAL TEST (X09 INDIAN LISTER V2)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/X09_test1_indian_lister.py
#
# CIRI-CIRI PENAMBAHBAIKAN:
# 1. 6 Punca P2P Bersepadu: Apibay, Knaben, Torrents-CSV, TorrentsDB, SolidTorrents, YTS.
# 2. Penyingkiran Mutlak 480p / SD: Hanya 1080p, 720p, 2K, dan 4K dibenarkan.
# 3. Enjin Transliterasi & Rumi: Memadankan 'Visaaranai'/'Visaranai', 'Part 2'/'Part II'.
# 4. Penepis Ketat Substring: Menyelesaikan isu 'Pada' tersalah padan 'Padayappa'.
# ==============================================================================

import re
import sys
import argparse
import importlib
from urllib.parse import quote_plus, unquote
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests
from guessit import guessit
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

CURRENT_DIR = Path(__file__).resolve().parent
V3_DIR = CURRENT_DIR.parent
PROJECT_ROOT = V3_DIR.parent
V2_DIR = PROJECT_ROOT / "live_engine_v2"
V1_DIR = PROJECT_ROOT / "live_engine"

for p in [CURRENT_DIR, V3_DIR, V2_DIR, V1_DIR, PROJECT_ROOT]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _redis_client_mod = importlib.import_module("X01_series_redis")
    db_v2 = getattr(_redis_client_mod, "series_db")
except Exception as e:
    console.print(f"[bold yellow]⚠️ Modul Redis tidak dimuatkan (Hanya untuk --dry-run): {e}[/bold yellow]")
    db_v2 = None

INDIAN_TEST_SUITE = [
    {"title": "Vaazhai", "year": "2024", "imdb": "tt23782140", "lang": "Tamil"},
    {"title": "Newton", "year": "2017", "imdb": "tt6484982", "lang": "Hindi"},
    {"title": "Pada", "year": "2022", "imdb": "tt10375094", "lang": "Malayalam"},
    {"title": "Viduthalai Part 2", "year": "2024", "imdb": "tt25434834", "lang": "Tamil"},
    {"title": "Jana Gana Mana", "year": "2022", "imdb": "tt13391710", "lang": "Malayalam"},
    {"title": "Visaranai", "year": "2015", "imdb": "tt4991384", "lang": "Tamil"},
    {"title": "Masaan", "year": "2015", "imdb": "tt4635372", "lang": "Hindi"},
    {"title": "Uppena", "year": "2020", "imdb": "tt9633480", "lang": "Telugu"},
]

# ------------------------------------------------------------------------------
# PENAPIS KUALITI MUTLAK (TIADA 480P / SD)
# ------------------------------------------------------------------------------
def detect_and_validate_quality(name: str) -> Optional[str]:
    n = name.lower()

    # Sekat mutlak 480p, SD, DVDRip, Cam, Telesync
    bad_patterns = [
        "480p", "sd", "dvdrip", "xvid", "cam", "camrip", "hdcam",
        "telesync", "tsrip", "hdtc", "dvdscr", "scr"
    ]
    for bad in bad_patterns:
        if re.search(rf"\b{bad}\b", n) or bad in n:
            return None

    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1440p", "2k", "qhd"]):
        return "2K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"

    # Jika pelepasan berkualiti WEB-DL/BluRay tanpa label nombor eksplisit
    if any(kw in n for kw in ["web-dl", "webrip", "bluray", "brrip", "hdrip"]):
        return "1080p"

    return None


def clean_search_title(raw_title: str) -> str:
    t = re.sub(r"\.{2,}", " ", raw_title)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def generate_title_variations(media_title: str) -> List[str]:
    raw = clean_search_title(media_title).lower()
    variations = {raw}

    # Transliterasi vokal India: aa <-> a, ee <-> i
    if "aa" in raw:
        variations.add(raw.replace("aa", "a"))
    else:
        # Tambah variasi gandaan vokal
        variations.add(re.sub(r"a", "aa", raw, count=1))

    # Variasi Angka Rumi & Siri
    if "part 2" in raw or "part ii" in raw:
        variations.add(raw.replace("part 2", "part ii"))
        variations.add(raw.replace("part ii", "part 2"))
        variations.add(raw.replace("part 2", "part-2"))
        variations.add(raw.replace("part 2", "2"))
    if "part 1" in raw or "part i" in raw:
        variations.add(raw.replace("part 1", "part i"))
        variations.add(raw.replace("part i", "part 1"))
        variations.add(raw.replace("part 1", "part-1"))
        variations.add(raw.replace("part 1", "1"))

    return list(variations)


def fetch_cinemeta_meta(imdb_id: str) -> Dict[str, str]:
    base_id = imdb_id.split(":")[0]
    url = f"https://v3-cinemeta.strem.io/meta/movie/{base_id}.json"
    meta = {"title": base_id, "year": "", "clean_title": base_id}
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            raw_name = data.get("name", "")
            year = str(data.get("year", ""))
            if raw_name:
                meta = {
                    "title": raw_name,
                    "year": year,
                    "clean_title": clean_search_title(raw_name)
                }
    except Exception:
        pass
    return meta


def is_valid_title_match(media_title: str, torrent_name: str, year: str = "") -> bool:
    n_lower = torrent_name.lower()
    if re.search(r"(?i)\b(xxx|porn|erotica|hentai)\b", n_lower):
        return False

    torrent_words = set(re.findall(r"[a-z0-9]+", n_lower))
    variations = generate_title_variations(media_title)
    matched_any = False

    for var in variations:
        tokens = [t for t in var.split() if t]
        if not tokens:
            continue

        token_match = True
        for tok in tokens:
            if tok not in torrent_words:
                if tok == "2" and ("ii" in torrent_words or "part2" in n_lower or "part-2" in n_lower):
                    continue
                if tok == "1" and ("i" in torrent_words or "part1" in n_lower or "part-1" in n_lower):
                    continue
                token_match = False
                break

        if token_match:
            matched_any = True
            break

    if not matched_any:
        return False

    # Elak kekeliruan perkataan pendek (cth: 'pada' padan 'padayappa')
    if media_title.lower() == "pada" and any(bad in torrent_words for bad in ["padayappa", "padmavat", "padman"]):
        return False

    return True


# ------------------------------------------------------------------------------
# 6 PUNCA PENGAMBIL DATA P2P FILEM INDIA
# ------------------------------------------------------------------------------

# PUNCA 1: Apibay Regional (The Pirate Bay - Rilis Komuniti India)
def scrape_apibay_regional(clean_title: str, year: str, lang: str = "") -> List[Dict[str, Any]]:
    queries = [clean_title]
    if year:
        queries.append(f"{clean_title} {year}")
    if lang:
        queries.append(f"{clean_title} {lang}")

    for v in generate_title_variations(clean_title):
        if v not in queries:
            queries.append(v)

    results = []
    seen = set()

    for q in queries:
        url = f"https://apibay.org/q.php?q={quote_plus(q)}"
        try:
            resp = requests.get(
                url,
                impersonate="chrome120",
                timeout=8,
                headers={"Accept": "application/json", "Referer": "https://thepiratebay.org/"}
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            if not (isinstance(data, list) and data and data[0].get("name") != "No results returned"):
                continue

            for it in data:
                h = (it.get("info_hash") or "").strip().lower()
                name = (it.get("name") or "").strip()
                seeds = int(it.get("seeders", 0))
                sz = int(it.get("size", 0))

                if len(h) != 40 or h in seen:
                    continue

                q_detected = detect_and_validate_quality(name)
                if not q_detected:
                    continue

                if not is_valid_title_match(clean_title, name, year):
                    continue

                seen.add(h)
                results.append({
                    "name": name,
                    "info_hash": h,
                    "seeders": seeds,
                    "size": sz,
                    "quality": q_detected,
                    "source": "ApibayReg",
                    "file_idx": 0,
                })
        except Exception:
            continue
    return results


# PUNCA 2: Torrents-CSV Global API (Pangkalan Data Terbuka 10M+ Torrent)
def scrape_torrents_csv(clean_title: str, year: str) -> List[Dict[str, Any]]:
    queries = [clean_title]
    if year:
        queries.append(f"{clean_title} {year}")

    results = []
    seen = set()

    for q in queries:
        url = f"https://torrents-csv.com/service/search?q={quote_plus(q)}&size=40"
        try:
            resp = requests.get(url, impersonate="chrome120", timeout=8)
            if resp.status_code != 200:
                continue

            torrents = resp.json().get("torrents", [])
            for it in torrents:
                h = (it.get("infohash") or "").strip().lower()
                name = it.get("name") or ""
                seeds = int(it.get("seeders", 0))
                sz = int(it.get("size_bytes", 0))

                if len(h) != 40 or h in seen:
                    continue

                q_detected = detect_and_validate_quality(name)
                if not q_detected:
                    continue

                if not is_valid_title_match(clean_title, name, year):
                    continue

                seen.add(h)
                results.append({
                    "name": name,
                    "info_hash": h,
                    "seeders": seeds,
                    "size": sz,
                    "quality": q_detected,
                    "source": "TorrentsCSV",
                    "file_idx": 0,
                })
        except Exception:
            continue
    return results


# PUNCA 3: TorrentsDB Addon API (Pengindeks 1TamilBlasters & 1337x)
def scrape_torrentsdb_stream(imdb_id: str, clean_title: str) -> List[Dict[str, Any]]:
    base_id = imdb_id.split(":")[0]
    url = f"https://torrentsdb.com/stream/movie/{base_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=9)
        if resp.status_code == 200:
            for s in resp.json().get("streams", []):
                h = (s.get("infoHash") or "").strip().lower()
                raw_title = s.get("title", "")
                if len(h) != 40 or not raw_title:
                    continue

                lines = [l.strip() for l in raw_title.split("\n") if l.strip()]
                seeds = 0
                sz_bytes = 0

                for line in lines:
                    sm = re.search(r"[👤👥]\s*([\d,]+)", line)
                    if sm:
                        seeds = int(sm.group(1).replace(",", ""))
                    zm = re.search(r"[💾💿]\s*([\d\.]+)\s*(GB|MB)", line, re.IGNORECASE)
                    if zm:
                        val = float(zm.group(1))
                        sz_bytes = int(val * 1024**3) if "GB" in zm.group(2).upper() else int(val * 1024**2)

                rel_name = lines[1] if len(lines) >= 2 else (lines[0] if lines else "TorrentsDB")
                q_detected = detect_and_validate_quality(f"{s.get('name','')} {raw_title}")
                if not q_detected:
                    continue

                results.append({
                    "name": rel_name,
                    "info_hash": h,
                    "seeders": seeds,
                    "size": sz_bytes,
                    "quality": q_detected,
                    "source": "TorrentsDB",
                    "file_idx": int(s.get("fileIdx", 0)),
                })
    except Exception:
        pass
    return results


# PUNCA 4: Knaben Aggregator API (Pengembangan Variasi Ejaan India)
def scrape_knaben_regional(clean_title: str, year: str) -> List[Dict[str, Any]]:
    url = "https://api.knaben.org/v1"
    results = []
    seen = set()

    queries = [clean_title]
    if year:
        queries.append(f"{clean_title} {year}")
    for v in generate_title_variations(clean_title):
        if v not in queries:
            queries.append(v)

    for q in queries:
        payload = {"query": q, "search_field": "title", "order_by": "seeders", "size": 30}
        try:
            resp = requests.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if resp.status_code != 200:
                continue

            for hit in resp.json().get("hits", []):
                name = hit.get("title", "")
                bytes_sz = int(hit.get("bytes", 0))
                seeds = int(hit.get("seeders", 0))
                raw_hash = hit.get("hash", "")
                magnet = hit.get("magnetUrl", "")
                h = ""
                if raw_hash and len(raw_hash) == 40:
                    h = raw_hash.lower()
                elif magnet:
                    m_m = re.search(r"urn:btih:([a-fA-F0-9]{40})", magnet)
                    if m_m:
                        h = m_m.group(1).lower()

                if len(h) != 40 or h in seen:
                    continue

                q_detected = detect_and_validate_quality(name)
                if not q_detected:
                    continue

                if not is_valid_title_match(clean_title, name, year):
                    continue

                seen.add(h)
                results.append({
                    "name": name,
                    "info_hash": h,
                    "seeders": seeds,
                    "size": bytes_sz,
                    "quality": q_detected,
                    "source": "KnabenReg",
                    "file_idx": 0,
                })
        except Exception:
            continue
    return results


# PUNCA 5: SolidTorrents API Dwi-Cermin (.to & .net)
def scrape_solidtorrents(clean_title: str, year: str) -> List[Dict[str, Any]]:
    results = []
    queries = [clean_title]
    if year:
        queries.append(f"{clean_title} {year}")

    seen = set()
    mirrors = ["https://solidtorrents.to", "https://solidtorrents.net"]

    for base in mirrors:
        for q in queries:
            url = f"{base}/api/v1/search?q={quote_plus(q)}&category=video&sort=seeders"
            try:
                resp = requests.get(url, impersonate="chrome120", timeout=8)
                if resp.status_code == 200:
                    for it in resp.json().get("results", []):
                        h = (it.get("infoHash") or "").strip().lower()
                        name = it.get("title") or ""
                        seeds = int(it.get("swarm", {}).get("seeders", 0))
                        sz = int(it.get("size", 0))

                        if len(h) != 40 or h in seen:
                            continue

                        q_detected = detect_and_validate_quality(name)
                        if not q_detected:
                            continue

                        if not is_valid_title_match(clean_title, name, year):
                            continue

                        seen.add(h)
                        results.append({
                            "name": name,
                            "info_hash": h,
                            "seeders": seeds,
                            "size": sz,
                            "quality": q_detected,
                            "source": "SolidTorrents",
                            "file_idx": 0,
                        })
                if results:
                    break
            except Exception:
                continue
        if results:
            break
    return results


# PUNCA 6: YTS Official REST API (Pelepasan Rasmi 1080p/720p)
def scrape_yts(clean_title: str, imdb_id: str) -> List[Dict[str, Any]]:
    base_id = imdb_id.split(":")[0]
    queries = [base_id, clean_title]
    results = []
    seen = set()

    for q in queries:
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={quote_plus(q)}"
        try:
            resp = requests.get(url, impersonate="chrome120", timeout=8)
            if resp.status_code == 200:
                movies = resp.json().get("data", {}).get("movies", [])
                for m in movies:
                    m_title = m.get("title", "")
                    m_year = m.get("year", "")
                    for t in m.get("torrents", []):
                        h = (t.get("hash") or "").strip().lower()
                        q_str = t.get("quality", "").upper()
                        if len(h) != 40 or h in seen:
                            continue
                        if q_str not in ["720P", "1080P", "2160P", "4K"]:
                            continue

                        seen.add(h)
                        results.append({
                            "name": f"{m_title} ({m_year}) [{q_str}] [YTS]",
                            "info_hash": h,
                            "seeders": int(t.get("seeds", 0)),
                            "size": int(t.get("size_bytes", 0)),
                            "quality": "4K" if "2160" in q_str else q_str,
                            "source": "YTS",
                            "file_idx": 0,
                        })
            if results:
                break
        except Exception:
            continue
    return results


# ------------------------------------------------------------------------------
# ENJIN UTAMA & ISIH KEUTAMAAN
# ------------------------------------------------------------------------------
def execute_indian_lister(
    imdb_id: str,
    fallback_title: str = "",
    fallback_lang: str = "",
    dry_run: bool = False,
    force_run: bool = False
) -> bool:
    base_id = imdb_id.split(":")[0]
    meta = fetch_cinemeta_meta(base_id)
    clean_title = meta.get("clean_title") or fallback_title or base_id
    year = meta.get("year", "")

    min_bytes = int(400 * 1024 * 1024)          # Minimum 400 MB
    max_bytes = int(8.5 * 1024 * 1024 * 1024)   # Maksimum 8.5 GB
    golden_min = int(1.0 * 1024 * 1024 * 1024)  # Zon Emas 1.0 GB
    golden_max = int(4.5 * 1024 * 1024 * 1024)  # Zon Emas 4.5 GB
    min_seeds = 2

    console.print(Panel.fit(
        f"[bold yellow]🇮🇳 ENJIN P2P SANDARAN FILEM INDIA (X09 V2)[/bold yellow]\n"
        f"IMDb: [bold cyan]{base_id}[/bold cyan] | Tajuk: [bold white]{clean_title}[/bold white] ({year or 'N/A'})\n"
        f"Kualiti Dibenarkan: [bold green]1080p, 720p, 2K, 4K[/bold green] ([red]480p/SD Disekat Mutlak[/red])\n"
        f"Keutamaan: [bold magenta]Zon Emas 1.0GB - 4.5GB & Seeder Tertinggi[/bold magenta]",
        border_style="yellow",
    ))

    # Semak Senarai Semasa di Redis
    existing_list = []
    if db_v2 and not dry_run:
        existing_list = db_v2.get_torrent_list(base_id) or []
        valid_existing = [
            t for t in existing_list
            if int(t.get("seeders", 0)) >= min_seeds and detect_and_validate_quality(t.get("name", "")) is not None
        ]

        if len(valid_existing) >= 10 and not force_run:
            console.print(f"[bold green]✅ Senarai mencukupi ({len(valid_existing)} torrent sah). X09 dilangkau.[/bold green]")
            return True

    raw_results = []
    stats = {}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(scrape_apibay_regional, clean_title, year, fallback_lang): "ApibayReg",
            ex.submit(scrape_torrents_csv, clean_title, year): "TorrentsCSV",
            ex.submit(scrape_torrentsdb_stream, base_id, clean_title): "TorrentsDB",
            ex.submit(scrape_knaben_regional, clean_title, year): "KnabenReg",
            ex.submit(scrape_solidtorrents, clean_title, year): "SolidTorrents",
            ex.submit(scrape_yts, clean_title, base_id): "YTS",
        }
        for f in as_completed(futures):
            src = futures[f]
            try:
                res = f.result() or []
                stats[src] = len(res)
                raw_results.extend(res)
            except Exception:
                stats[src] = 0

    console.print(f"[cyan]📊 Statistik Pengikis India:[/cyan] " + " | ".join([f"{k}: {v}" for k, v in stats.items()]))

    # Sistem Isih Keutamaan:
    # 1. Zon Emas (1.0GB - 4.5GB)
    # 2. Resolusi (1080p/2K diutamakan berbanding 720p)
    # 3. Bilangan Seeds
    def sort_key(it):
        sz = it.get("size", 0)
        s = it.get("seeders", 0)
        q = it.get("quality", "")
        is_gold = 1 if (golden_min <= sz <= golden_max) else 0
        q_weight = 3 if q in ["1080p", "2K"] else (2 if q == "720p" else 1)
        return (is_gold, q_weight, s)

    seen_hashes = {t["info_hash"] for t in existing_list}
    unique_candidates: Dict[str, Dict[str, Any]] = {}

    for t in raw_results:
        h = t["info_hash"]
        sz = t["size"]
        s = t["seeders"]

        if s < min_seeds or sz < min_bytes or sz > max_bytes:
            continue
        if h in seen_hashes:
            continue
        if h not in unique_candidates or s > unique_candidates[h]["seeders"]:
            unique_candidates[h] = t

    sorted_new = sorted(unique_candidates.values(), key=sort_key, reverse=True)
    new_to_add = sorted_new[:40]

    console.print(f"[bold cyan]➕ Bersedia menambah {len(new_to_add)} torrent sah (tiada 480p) ke senarai.[/bold cyan]")

    combined_list = existing_list + new_to_add
    combined_list.sort(key=sort_key, reverse=True)
    final_list = combined_list[:60]

    table = Table(
        title=f"📋 Hasil Enjin X09 untuk {clean_title} ({len(final_list)} Torrent Lulus)",
        border_style="yellow"
    )
    table.add_column("No", justify="center", style="cyan", width=4)
    table.add_column("Punca", justify="center", style="yellow", width=14)
    table.add_column("Kualiti", justify="center", style="magenta", width=8)
    table.add_column("Saiz", style="white", width=10)
    table.add_column("Seeds", justify="center", style="green", width=7)
    table.add_column("Nama Pelepasan / Fail", style="dim")

    for idx, t in enumerate(final_list, 1):
        sz = t["size"]
        sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
        table.add_row(
            str(idx),
            t.get("source", "IndiaP2P"),
            t.get("quality", "HD"),
            sz_str,
            str(t.get("seeders", 0)),
            t.get("name", "")[:60],
        )
    console.print(table)

    if dry_run:
        console.print("[dim italic]ℹ️ Mod Dry-Run aktif: Tiada data ditulis ke Redis.[/dim italic]")
        return True

    if db_v2 and final_list:
        saved = db_v2.save_torrent_list(base_id, final_list, ttl_seconds=86400)
        if saved:
            console.print(f"[bold green]✅ Berjaya mengemas kini {len(final_list)} seeder ke Redis bagi kunci 'stremio:list:{base_id}'![/bold green]")
            return True

    return False


def main():
    parser = argparse.ArgumentParser(description="Ujian Enjin P2P Sandaran Filem India (X09 V2)")
    parser.add_argument("--imdb", default="", help="Target IMDb ID (cth: tt23782140)")
    parser.add_argument("--title", default="", help="Tajuk filem")
    parser.add_argument("--lang", default="", help="Bahasa asal filem (Tamil, Telugu, dll)")
    parser.add_argument("--dry-run", action="store_true", help="Uji tanpa menulis ke Redis")
    parser.add_argument("--force", action="store_true", help="Paksa laksana")
    parser.add_argument("--test-all", action="store_true", help="Uji kesemua 8 filem senarai India berturut-turut")
    args = parser.parse_args()

    if args.test_all:
        console.print("[bold green]🎬 MEMULAKAN UJIAN BATCH UNTUK 8 FILEM INDIA SASARAN (VERSI 2)[/bold green]")
        for item in INDIAN_TEST_SUITE:
            console.print(f"\n[bold magenta]════════════════════════════════════════════════════════════[/bold magenta]")
            console.print(f"Menguji: [bold white]{item['title']} ({item['year']})[/bold white] - {item['imdb']} [{item['lang']}]")
            execute_indian_lister(item["imdb"], item["title"], fallback_lang=item["lang"], dry_run=True, force_run=True)
        sys.exit(0)

    if not args.imdb:
        console.print("[bold red]❌ Sila nyatakan --imdb <ttXXXXXXX> atau gunakan --test-all[/bold red]")
        sys.exit(1)

    execute_indian_lister(args.imdb, args.title, fallback_lang=args.lang, dry_run=args.dry_run, force_run=args.force)


if __name__ == "__main__":
    main()