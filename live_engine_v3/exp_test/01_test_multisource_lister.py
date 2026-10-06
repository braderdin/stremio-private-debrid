#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - MULTI-SOURCE SEEDER LISTER (EKSPERIMEN)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/01_test_multisource_lister.py
#
# CIRI-CIRI:
# 1. 7 Punca Gabungan Selari:
#    - Torrentio
#    - MediaFusion (Spesialis Bollywood / Hindustan / Asia)
#    - KnightCrawler (DHT / Torrust)
#    - Comet (Aggregator)
#    - YTS Official REST API (Filem)
#    - Apibay / The Pirate Bay (Carian Dwi-Mod: Tajuk Cinemeta + ID)
#    - EZTV REST API (Siri TV)
# 2. Penapisan Saiz Siling:
#    - Filem: 500 MB - 6.0 GB
#    - Siri TV: 30 MB - 6.0 GB
# 3. Pengisihan Pintar Berkeutamaan (Golden Zone):
#    - Keutamaan #1: Saiz antara 500 MB hingga 3.0 GB disusun mengikut Seeder tertinggi.
#    - Keutamaan #2: Saiz selebihnya (3.0 GB - 6.0 GB) disusun mengikut Seeder tertinggi.
# 4. Had Maksimum: 40 Senarai Teratas.
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

# ------------------------------------------------------------------------------
# 1. KONFIGURASI LALUAN SISTEM
# ------------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
V3_DIR = CURRENT_DIR.parent
PROJECT_ROOT = V3_DIR.parent
V2_DIR = PROJECT_ROOT / "live_engine_v2"
V1_DIR = PROJECT_ROOT / "live_engine"

for p in [CURRENT_DIR, V3_DIR, V2_DIR, V1_DIR, PROJECT_ROOT]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

# Cuba import enjin Redis jika ada (Graceful Fallback)
try:
    _redis_client_mod = importlib.import_module("X01_series_redis")
    db_v2 = getattr(_redis_client_mod, "series_db", None)
except Exception:
    db_v2 = None


# ------------------------------------------------------------------------------
# 2. PEMBANTU RESOLUSI & METADATA
# ------------------------------------------------------------------------------
def detect_quality(name: str) -> str:
    """Mengesan kualiti resolusi daripada teks nama torrent."""
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


def fetch_cinemeta_meta(imdb_id: str, is_series: bool) -> Dict[str, str]:
    """
    Mengambil metadata rasmi (tajuk, tahun) dari Stremio Cinemeta.
    Menyokong 'series' dan 'movie'.
    """
    base_id = imdb_id.split(":")[0]
    endpoint_type = "series" if is_series else "movie"
    url = f"https://v3-cinemeta.strem.io/meta/{endpoint_type}/{base_id}.json"

    meta = {"title": base_id, "year": "", "clean_title": base_id}
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            raw_title = data.get("name", "")
            year = str(data.get("year", ""))
            if raw_title:
                clean = re.sub(r"[^\w\s]", " ", raw_title)
                clean = re.sub(r"\s+", " ", clean).strip()
                meta = {
                    "title": raw_title,
                    "year": year,
                    "clean_title": clean
                }
    except Exception:
        pass
    return meta


# ------------------------------------------------------------------------------
# 3. PENGHURAI STRIM UMUM (STREAM PARSER)
# ------------------------------------------------------------------------------
def parse_generic_stremio_stream(stream: Dict[str, Any], default_source: str = "Stremio") -> Optional[Dict[str, Any]]:
    """
    Menghurai objek strim berformat Stremio (Torrentio / MediaFusion / KnightCrawler / Comet).
    """
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
        # Ekstrak Seeders
        seeds_match = re.search(r"[👤👥]\s*([\d,]+)", line) or re.search(r"(?:Seeds?|Seeders?):\s*([\d,]+)", line, re.IGNORECASE)
        if seeds_match:
            seeds = int(seeds_match.group(1).replace(",", ""))

        # Ekstrak Saiz Fail
        size_match = re.search(r"[💾💿]\s*([\d\.]+)\s*(GB|MB|KB|GiB|MiB)", line, re.IGNORECASE) or \
                     re.search(r"(?:Size):\s*([\d\.]+)\s*(GB|MB|KB|GiB|MiB)", line, re.IGNORECASE)
        if size_match:
            val = float(size_match.group(1))
            unit = size_match.group(2).upper()
            if "GB" in unit:
                size_bytes = int(val * 1024**3)
            elif "MB" in unit:
                size_bytes = int(val * 1024**2)
            elif "KB" in unit:
                size_bytes = int(val * 1024)

        # Ekstrak Punca Spesifik
        if any(icon in line for icon in ["⚙️", "🌐", "🏷️"]):
            src_m = re.search(r"[⚙️🌐🏷️]\s*([\w\+\.\-]+)", line)
            if src_m:
                source_site = src_m.group(1)

    # Nama pelepasan fail
    if len(lines) >= 3:
        release_name = f"{lines[1]} [{lines[0]}]"
    else:
        release_name = lines[0] if lines else default_source

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
# 4. KOLEKSI PENGURUS PUNCA TORRENT (SOURCES)
# ------------------------------------------------------------------------------

# PUNCA 1: Torrentio
def scrape_torrentio(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    ep_type = "series" if is_series else "movie"
    url = f"https://torrentio.strem.fun/stream/{ep_type}/{target_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=9)
        if resp.status_code == 200:
            for s in resp.json().get("streams", []):
                p = parse_generic_stremio_stream(s, default_source="Torrentio")
                if p:
                    results.append(p)
    except Exception:
        pass
    return results


# PUNCA 2: MediaFusion (Kekuatan Utama Kandungan Bollywood / Desi / Asia)
def scrape_mediafusion(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    ep_type = "series" if is_series else "movie"
    url = f"https://mediafusion.elfhosted.com/stream/{ep_type}/{target_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=10)
        if resp.status_code == 200:
            for s in resp.json().get("streams", []):
                p = parse_generic_stremio_stream(s, default_source="MediaFusion")
                if p:
                    results.append(p)
    except Exception:
        pass
    return results


# PUNCA 3: KnightCrawler (Penyerap Torrust DHT & 1337x)
def scrape_knightcrawler(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    ep_type = "series" if is_series else "movie"
    url = f"https://knightcrawler.elfhosted.com/stream/{ep_type}/{target_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=10)
        if resp.status_code == 200:
            for s in resp.json().get("streams", []):
                p = parse_generic_stremio_stream(s, default_source="KnightCrawler")
                if p:
                    results.append(p)
    except Exception:
        pass
    return results


# PUNCA 4: Comet Aggregator
def scrape_comet(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
    ep_type = "series" if is_series else "movie"
    url = f"https://comet.elfhosted.com/stream/{ep_type}/{target_id}.json"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=10)
        if resp.status_code == 200:
            for s in resp.json().get("streams", []):
                p = parse_generic_stremio_stream(s, default_source="Comet")
                if p:
                    results.append(p)
    except Exception:
        pass
    return results


# PUNCA 5: YTS Official API (Filem Sahaja)
def scrape_yts(imdb_id: str, clean_title: str, is_series: bool) -> List[Dict[str, Any]]:
    if is_series:
        return []
    base_id = imdb_id.split(":")[0]
    results = []

    # Cubaan 1: Cari guna IMDb ID
    queries = [base_id]
    if clean_title and clean_title != base_id:
        queries.append(clean_title)

    for q in queries:
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={quote_plus(q)}"
        try:
            resp = requests.get(url, impersonate="chrome120", timeout=8)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                movies = data.get("movies", [])
                if movies:
                    for m in movies:
                        # Pastikan sepadan jika guna carian teks tajuk
                        m_imdb = m.get("imdb_code", "")
                        if base_id and m_imdb and m_imdb != base_id:
                            continue

                        m_title = m.get("title", "")
                        m_year = m.get("year", "")
                        for t in m.get("torrents", []):
                            h = (t.get("hash") or "").strip().lower()
                            if len(h) != 40:
                                continue
                            quality = t.get("quality", "HD")
                            q_type = t.get("type", "")
                            release_name = f"{m_title} ({m_year}) [{quality} {q_type.upper()}] [YTS.MX]"
                            results.append({
                                "name": release_name,
                                "info_hash": h,
                                "seeders": int(t.get("seeds", 0)),
                                "size": int(t.get("size_bytes", 0)),
                                "quality": quality,
                                "source": "YTS",
                                "file_idx": 0,
                            })
                    if results:
                        break
        except Exception:
            continue
    return results


# PUNCA 6: Apibay / The Pirate Bay (Dipertingkat dengan Resolusi Tajuk Pintar)
def scrape_apibay(
    imdb_id: str,
    clean_title: str,
    year: str,
    is_series: bool,
    season: int = 1,
    episode: int = 1,
) -> List[Dict[str, Any]]:
    results = []
    base_id = imdb_id.split(":")[0]

    # Bina senarai carian pintar
    search_queries = []
    if is_series:
        clean = clean_title or base_id
        search_queries.append(f"{clean} S{season:02d}E{episode:02d}")
    else:
        # Filem: Cari Tajuk + Tahun dahulu, kemudian Tajuk, kemudian IMDb ID
        if clean_title and clean_title != base_id:
            if year:
                search_queries.append(f"{clean_title} {year}")
            search_queries.append(clean_title)
        search_queries.append(base_id)

    seen_hashes = set()

    for q in search_queries:
        url = f"https://apibay.org/q.php?q={quote_plus(q)}"
        try:
            resp = requests.get(
                url,
                impersonate="chrome120",
                timeout=9,
                headers={"Accept": "application/json", "Referer": "https://thepiratebay.org/"},
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            if not (isinstance(data, list) and data and data[0].get("name") != "No results returned"):
                continue

            for item in data:
                h = (item.get("info_hash") or "").strip().lower()
                name = (item.get("name") or "").strip()
                seeds = int(item.get("seeders", 0))
                size = int(item.get("size", 0))

                if len(h) != 40 or not name or h in seen_hashes:
                    continue

                if is_series:
                    guess = guessit(name)
                    e_guess = guess.get("episode")
                    ep_match = False
                    if isinstance(e_guess, list):
                        ep_match = episode in e_guess
                    elif e_guess is not None:
                        ep_match = (e_guess == episode)

                    if not ep_match:
                        s_pat = rf"(?i)\b[Ss]0*{season}[\s.\-_]*[Ee]0*{episode}\b"
                        if not re.search(s_pat, name):
                            continue
                else:
                    # Tapis siri TV tersasar jika mencari filem
                    if re.search(r"(?i)\b[Ss]\d{1,2}[Ee]\d{1,2}\b", name):
                        continue

                seen_hashes.add(h)
                results.append({
                    "name": name,
                    "info_hash": h,
                    "seeders": seeds,
                    "size": size,
                    "quality": detect_quality(name),
                    "source": "Apibay",
                    "file_idx": 0,
                })
        except Exception:
            continue

    return results


# PUNCA 7: EZTV REST API (Siri TV Sahaja)
def scrape_eztv(imdb_id: str, is_series: bool, season: int = 1, episode: int = 1) -> List[Dict[str, Any]]:
    if not is_series:
        return []
    base_id = imdb_id.split(":")[0]
    numeric_id = re.sub(r"^[^\d]*", "", base_id)
    if not numeric_id:
        return []

    url = f"https://eztv.re/api/get-torrents?imdb_id={numeric_id}"
    results = []
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=9)
        if resp.status_code == 200:
            for item in resp.json().get("torrents", []):
                h = (item.get("hash") or "").strip().lower()
                s_num = int(item.get("season", 0))
                e_num = int(item.get("episode", 0))

                if len(h) != 40:
                    continue

                if s_num == season and e_num == episode:
                    name = item.get("filename") or item.get("title") or "EZTV Series"
                    results.append({
                        "name": name,
                        "info_hash": h,
                        "seeders": int(item.get("seeds", 0)),
                        "size": int(item.get("size_bytes", 0)),
                        "quality": detect_quality(name),
                        "source": "EZTV",
                        "file_idx": 0,
                    })
    except Exception:
        pass
    return results


# ------------------------------------------------------------------------------
# 5. ENJIN GABUNGAN & PENAPISAN PINTAR
# ------------------------------------------------------------------------------
def execute_multisource_pipeline(raw_imdb_id: str, fallback_title: str = "") -> bool:
    target_id = unquote(raw_imdb_id).strip()
    is_series = ":" in target_id
    kind = "SIRI TV" if is_series else "FILEM"

    season = 1
    episode = 1
    if is_series:
        parts = target_id.split(":")
        base_id = parts[0]
        season = int(parts[1]) if len(parts) > 1 else 1
        episode = int(parts[2]) if len(parts) > 2 else 1
    else:
        base_id = target_id

    # 1. Dapatkan Metadata Rasmi dari Cinemeta
    meta = fetch_cinemeta_meta(base_id, is_series)
    media_title = fallback_title or meta["clean_title"]
    year = meta.get("year", "")

    # 2. Tetapan Had Saiz
    # Filem: 500 MB - 6.0 GB | Siri: 30 MB - 6.0 GB
    min_bytes = (30 if is_series else 500) * 1024 * 1024
    max_bytes = 6 * 1024 * 1024 * 1024

    # Had Golden Zone (500 MB - 3.0 GB)
    golden_min_bytes = 500 * 1024 * 1024
    golden_max_bytes = 3 * 1024 * 1024 * 1024

    console.print(Panel.fit(
        f"[bold cyan]🌟 V3 MULTI-SOURCE TEST ENGINE: {kind}[/bold cyan]\n"
        f"ID Sasaran: [bold yellow]{target_id}[/bold yellow] | Base IMDb: [bold white]{base_id}[/bold white]\n"
        f"Tajuk Cinemeta: [bold green]{meta['title']}[/bold green] (Tahun: {year or 'N/A'})\n"
        f"Julat Saiz Dibenarkan: [green]{min_bytes // (1024*1024)} MB[/green] - [red]{max_bytes / (1024**3):.1f} GB[/red]\n"
        f"Zon Pilihan Utama: [bold magenta]500 MB - 3.0 GB (Seeder Tertinggi Diutamakan)[/bold magenta]"
        + (f" | Episod: S{season:02d}E{episode:02d}" if is_series else ""),
        border_style="cyan",
    ))

    # 3. Laksana Panggilan Selari Rentas Punca (Multi-Threaded)
    all_raw: List[Dict[str, Any]] = []
    source_stats: Dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = {
            executor.submit(scrape_torrentio, target_id, is_series): "Torrentio",
            executor.submit(scrape_mediafusion, target_id, is_series): "MediaFusion",
            executor.submit(scrape_knightcrawler, target_id, is_series): "KnightCrawler",
            executor.submit(scrape_comet, target_id, is_series): "Comet",
            executor.submit(scrape_yts, base_id, media_title, is_series): "YTS",
            executor.submit(scrape_apibay, base_id, media_title, year, is_series, season, episode): "Apibay",
            executor.submit(scrape_eztv, base_id, is_series, season, episode): "EZTV",
        }

        for future in as_completed(futures):
            src_name = futures[future]
            try:
                items = future.result() or []
                source_stats[src_name] = len(items)
                all_raw.extend(items)
            except Exception as e:
                source_stats[src_name] = 0

    console.print(
        f"[cyan]📊 Statistik Asal Diperoleh:[/cyan] "
        + " | ".join([f"[yellow]{k}: {v}[/yellow]" for k, v in source_stats.items()])
    )

    if not all_raw:
        console.print(f"[bold red]❌ Tiada torrent ditemui dari sebarang punca bagi {target_id}![/bold red]")
        return False

    # 4. Penapisan Saiz & Nyah-duplikasi InfoHash (Kekalkan seeds tertinggi)
    unique_torrents: Dict[str, Dict[str, Any]] = {}
    total_rejected = 0

    for item in all_raw:
        h = item["info_hash"]
        seeds = item["seeders"]
        sz = item["size"]

        # Abaikan pautan mati (0 seeders)
        if seeds < 1:
            continue

        # Penapisan Saiz Siling: 500MB/30MB - 6.0GB
        if sz < min_bytes or sz > max_bytes:
            total_rejected += 1
            continue

        if h not in unique_torrents:
            unique_torrents[h] = item
        else:
            # Jika hash sama dari punca lain, pilih maklumat dengan seeder lebih tinggi
            if seeds > unique_torrents[h]["seeders"]:
                unique_torrents[h] = item

    if not unique_torrents:
        console.print(
            f"[bold red]❌ Semua torrent ({len(all_raw)}) ditolak "
            f"(saiz di luar {min_bytes // (1024*1024)} MB - {max_bytes / (1024**3):.1f} GB atau 0 seeders)![/bold red]"
        )
        return False

    # 5. Logik Pengisihan Berkeutamaan (Golden Zone 500MB - 3GB)
    def priority_sort_key(item: Dict[str, Any]):
        sz = item.get("size", 0)
        seeds = item.get("seeders", 0)
        is_golden = (golden_min_bytes <= sz <= golden_max_bytes)
        # Nilai 1 (Golden Zone) berada di atas nilai 0, diikuti dengan jumlah seeder
        return (1 if is_golden else 0, seeds)

    combined_list = list(unique_torrents.values())
    combined_list.sort(key=priority_sort_key, reverse=True)

    # Hadkan kepada 40 senarai teratas
    top_torrents = combined_list[:40]

    # 6. Paparan Jadual Keputusan
    table = Table(
        title=f"📋 Senarai Seeder Multi-Source: {target_id} ({len(top_torrents)} Torrent Lulus / Dihadkan ke 40)",
        border_style="green",
    )
    table.add_column("No", justify="center", style="cyan", width=4)
    table.add_column("Punca", justify="center", style="yellow", width=14)
    table.add_column("Kualiti", justify="center", style="magenta", width=8)
    table.add_column("Saiz", style="white", width=11)
    table.add_column("Seeds", justify="center", style="green", width=7)
    table.add_column("Keutamaan", justify="center", style="blue", width=11)
    table.add_column("Nama Pelepasan / Fail", style="dim")

    for idx, t in enumerate(top_torrents, 1):
        sz = t["size"]
        sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
        is_golden = (golden_min_bytes <= sz <= golden_max_bytes)
        tier_tag = "[bold green]500M-3G ★[/bold green]" if is_golden else "[dim]3G-6G[/dim]"

        table.add_row(
            str(idx),
            t["source"],
            t["quality"],
            sz_str,
            str(t["seeders"]),
            tier_tag,
            t["name"][:55],
        )

    console.print(table)
    console.print(f"[dim]Tolak: {total_rejected} torrent (luar saiz) | Bersih Unik: {len(unique_torrents)}[/dim]")

    # 7. Simpan ke Redis V3 (Jika modul tersedia)
    if db_v2 and getattr(db_v2, "accounts", None):
        try:
            saved = db_v2.save_torrent_list(target_id, top_torrents, ttl_seconds=86400)
            if saved:
                console.print(f"[bold green]✅ Berjaya menyimpan 40 senarai seeder ke Upstash Redis bagi kunci 'stremio:list:{target_id}'![/bold green]")
                return True
            else:
                console.print("[bold red]❌ Gagal menyimpan rekod ke Upstash Redis.[/bold red]")
        except Exception as e:
            console.print(f"[bold red]❌ Ralat komunikasi Redis: {e}[/bold red]")
    else:
        console.print("[dim yellow]ℹ️ Mod Ujian Tempatan: Melangkau simpanan Redis (Data sah dan sedia disimpan).[/dim yellow]")
        return True

    return False


def main():
    parser = argparse.ArgumentParser(description="V3 Multi-Source Seeder Lister Experiment")
    parser.add_argument("--imdb", default="tt0451787", help="Target IMDb ID (Lalai: tt0451787 untuk Kyon Ki...)")
    parser.add_argument("--title", default="", help="Tajuk sandaran filem/siri")
    args = parser.parse_args()

    success = execute_multisource_pipeline(args.imdb, args.title)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()