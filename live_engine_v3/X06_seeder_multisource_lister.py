#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - MULTI-SOURCE SEEDER LISTER (ENGINE SEBENAR)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X06_seeder_multisource_lister.py
#
# PEMBAIKAN TERKINI:
# 1. Tapis Seeder Minimum: Wajib >= 4 Seeds (Singkirkan seeder hantu 1-3 seeds).
# 2. Zon Emas Baharu: 1.0 GB - 4.5 GB disusun di atas mengikut Seeder tertinggi.
# 3. Had Saiz Siling Fail: Dinaikkan sehingga 7.5 GB.
# 4. Kuota Gabungan Pintar (Limit 60):
#    - Kekalkan 30 terbaik dari X03.
#    - Tambah sehingga 30 terbaik dari X06 (atau isi baki sehingga genap 60 jika X03 < 30).
# 5. Smart Merge & Title Sanity Filter (Sekat XXX, spam pelepasan palsu).
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
# 1. KONFIGURASI LALUAN & IMPORT MODUL REDIS V3
# ------------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
V2_DIR = PROJECT_ROOT / "live_engine_v2"
V1_DIR = PROJECT_ROOT / "live_engine"

for p in [CURRENT_DIR, V2_DIR, V1_DIR, PROJECT_ROOT]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _redis_client_mod = importlib.import_module("X01_series_redis")
    db_v2 = getattr(_redis_client_mod, "series_db")
except Exception as e:
    console.print(f"[bold red]❌ Gagal mengimport modul X01_series_redis: {e}[/bold red]")
    sys.exit(1)


# ------------------------------------------------------------------------------
# 2. PEMBANTU KUALITI, PEMBERSIHAN TAJUK & PENAPIS KETEPATAN
# ------------------------------------------------------------------------------
def detect_quality(name: str) -> str:
    """Mengesan kualiti resolusi video daripada nama teks torrent."""
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
    """Membersihkan simbol khas seperti '...' atau tanda seru untuk carian API."""
    t = re.sub(r"\.{2,}", " ", raw_title)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def fetch_cinemeta_meta(imdb_id: str, is_series: bool) -> Dict[str, str]:
    """Mengambil metadata rasmi (tajuk, tahun keluaran) daripada Stremio Cinemeta."""
    base_id = imdb_id.split(":")[0]
    endpoint_type = "series" if is_series else "movie"
    url = f"https://v3-cinemeta.strem.io/meta/{endpoint_type}/{base_id}.json"

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


def is_valid_title_match(media_title: str, torrent_name: str, is_series: bool, year: str = "") -> bool:
    """Menapis pelepasan palsu, spam dewasa, dan pelepasan yang salah tajuk."""
    n_lower = torrent_name.lower()

    # Sekat kandungan lucah / dewasa
    if re.search(r"(?i)\b(xxx|porn|erotica|hentai)\b", n_lower):
        return False

    if is_series:
        return True

    clean_target = clean_search_title(media_title).lower()
    target_tokens = [t for t in clean_target.split() if len(t) > 1 or t.isdigit()]

    if not target_tokens:
        return True

    norm_torrent = re.sub(r"[^\w\s]", " ", n_lower)
    norm_torrent = re.sub(r"\s+", " ", norm_torrent).strip()

    # Sokong kes tanpa jarak (cth: '3idiots')
    target_nospace = "".join(target_tokens)
    torrent_nospace = re.sub(r"[^\w]", "", n_lower)
    if target_nospace in torrent_nospace:
        return True

    torrent_words = set(norm_torrent.split())
    for token in target_tokens:
        if token not in torrent_words:
            if token.isdigit():
                return False
            if not any(token in w for w in torrent_words):
                return False

    return True


# ------------------------------------------------------------------------------
# 3. PENGHURAI STRIM UMUM (STREAM PARSER)
# ------------------------------------------------------------------------------
def parse_generic_stremio_stream(stream: Dict[str, Any], default_source: str = "Stremio") -> Optional[Dict[str, Any]]:
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
        seeds_match = re.search(r"[👤👥]\s*([\d,]+)", line) or re.search(r"(?:Seeds?|Seeders?):\s*([\d,]+)", line, re.IGNORECASE)
        if seeds_match:
            seeds = int(seeds_match.group(1).replace(",", ""))

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

        if any(icon in line for icon in ["⚙️", "🌐", "🏷"]):
            src_m = re.search(r"[⚙🌐🏷️]\s*([\w\+\.\-]+)", line)
            if src_m:
                source_site = src_m.group(1)

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
# 4. SALURAN PENGAMBIL DATA (SOURCES)
# ------------------------------------------------------------------------------

# PUNCA 1: Torrentio Turbo (22 Tracker Antarabangsa + Sort Seeds)
def scrape_torrentio_turbo(target_id: str, is_series: bool) -> List[Dict[str, Any]]:
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
        if resp.status_code == 200:
            for s in resp.json().get("streams", []):
                p = parse_generic_stremio_stream(s, default_source="Torrentio")
                if p:
                    results.append(p)
    except Exception:
        pass
    return results


# PUNCA 2: Knaben Aggregator API (1337x, TGx, TPB, Rutracker, BitSearch, LimeTorrents)
def scrape_knaben_aggregator(
    clean_title: str,
    year: str,
    is_series: bool,
    season: int = 1,
    episode: int = 1
) -> List[Dict[str, Any]]:
    url = "https://api.knaben.org/v1"
    results = []

    queries = []
    if is_series:
        queries.append(f"{clean_title} S{season:02d}E{episode:02d}")
    else:
        if year:
            queries.append(f"{clean_title} {year}")
        queries.append(clean_title)

    seen_hashes = set()

    for q in queries:
        payload = {
            "query": q,
            "search_field": "title",
            "order_by": "seeders",
            "size": 50
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                timeout=12
            )
            if resp.status_code != 200:
                continue

            hits = resp.json().get("hits", [])
            for hit in hits:
                name = hit.get("title", "")
                bytes_sz = int(hit.get("bytes", 0))
                seeds = int(hit.get("seeders", 0))

                magnet = hit.get("magnetUrl", "")
                raw_hash = hit.get("hash", "")
                h = ""
                if raw_hash and len(raw_hash) == 40:
                    h = raw_hash.lower()
                elif magnet:
                    m_match = re.search(r"urn:btih:([a-fA-F0-9]{40})", magnet)
                    if m_match:
                        h = m_match.group(1).lower()

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
                    if re.search(r"(?i)\b[Ss]\d{1,2}[Ee]\d{1,2}\b", name):
                        continue
                    if not is_valid_title_match(clean_title, name, is_series=False, year=year):
                        continue

                seen_hashes.add(h)
                results.append({
                    "name": name,
                    "info_hash": h,
                    "seeders": seeds,
                    "size": bytes_sz,
                    "quality": detect_quality(name),
                    "source": "KnabenAgg",
                    "file_idx": 0,
                })
            if results:
                break
        except Exception:
            continue

    return results


# PUNCA 3: Apibay / The Pirate Bay (Carian Berbilang Kata Kunci)
def scrape_apibay_multi(
    imdb_id: str,
    clean_title: str,
    year: str,
    is_series: bool,
    season: int = 1,
    episode: int = 1,
) -> List[Dict[str, Any]]:
    results = []
    base_id = imdb_id.split(":")[0]

    search_queries = []
    if is_series:
        search_queries.append(f"{clean_title} S{season:02d}E{episode:02d}")
    else:
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
                    if re.search(r"(?i)\b[Ss]\d{1,2}[Ee]\d{1,2}\b", name):
                        continue
                    if not is_valid_title_match(clean_title, name, is_series=False, year=year):
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


# PUNCA 4: YTS Official REST API (Filem Sahaja)
def scrape_yts(imdb_id: str, clean_title: str, is_series: bool) -> List[Dict[str, Any]]:
    if is_series:
        return []
    base_id = imdb_id.split(":")[0]
    results = []

    for q in [base_id, clean_title]:
        if not q or re.match(r"^tt\d+$", q):
            continue
        url = f"https://yts.mx/api/v2/list_movies.json?query_term={quote_plus(q)}"
        try:
            resp = requests.get(url, impersonate="chrome120", timeout=8)
            if resp.status_code == 200:
                movies = resp.json().get("data", {}).get("movies", [])
                if movies:
                    for m in movies:
                        m_imdb = m.get("imdb_code", "")
                        if base_id and m_imdb and m_imdb != base_id:
                            continue
                        m_title = m.get("title", "")
                        m_year = m.get("year", "")
                        for t in m.get("torrents", []):
                            h = (t.get("hash") or "").strip().lower()
                            if len(h) == 40:
                                q_str = t.get("quality", "HD")
                                q_type = t.get("type", "").upper()
                                results.append({
                                    "name": f"{m_title} ({m_year}) [{q_str} {q_type}] [YTS.MX]",
                                    "info_hash": h,
                                    "seeders": int(t.get("seeds", 0)),
                                    "size": int(t.get("size_bytes", 0)),
                                    "quality": q_str,
                                    "source": "YTS",
                                    "file_idx": 0,
                                })
                    if results:
                        break
        except Exception:
            continue
    return results


# PUNCA 5: EZTV REST API (Siri TV Sahaja)
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
# 5. ENJIN UTAMA: PENGURUSAN DATA & SMART MERGE KUOTA KE REDIS
# ------------------------------------------------------------------------------
def process_and_save_multisource_list(raw_imdb_id: str, fallback_title: str = "") -> bool:
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

    # 1. Ambil Metadata Cinemeta & Tapis IMDb ID daripada fallback_title
    meta = fetch_cinemeta_meta(base_id, is_series)

    clean_fallback = fallback_title.strip()
    if clean_fallback and re.match(r"^tt\d+", clean_fallback):
        clean_fallback = ""

    if meta.get("clean_title") and not re.match(r"^tt\d+", meta["clean_title"]):
        media_title = meta["clean_title"]
    else:
        media_title = clean_fallback or base_id

    year = meta.get("year", "")

    # 2. Tetapan Had Saiz & Had Seeder Baharu
    min_bytes = (30 if is_series else 500) * 1024 * 1024
    max_bytes = int(7.5 * 1024 * 1024 * 1024)           # Had siling: 7.5 GB
    golden_min_bytes = int(1.0 * 1024 * 1024 * 1024)    # Zon Emas: 1.0 GB
    golden_max_bytes = int(4.5 * 1024 * 1024 * 1024)    # Zon Emas: 4.5 GB
    min_seeds = 4                                       # Tapis: Wajib >= 4 Seeds

    console.print(Panel.fit(
        f"[bold cyan]🚀 V3 MULTI-SOURCE SEEDER ENGINE (X06 - KUOTA 60): {kind}[/bold cyan]\n"
        f"ID Sasaran: [bold yellow]{target_id}[/bold yellow] | Base IMDb: [bold white]{base_id}[/bold white]\n"
        f"Tajuk Cinemeta: [bold green]{meta['title']}[/bold green] -> Bersih: [yellow]{media_title}[/yellow] ({year or 'N/A'})\n"
        f"Julat Saiz Dibenarkan: [green]{min_bytes // (1024*1024)} MB[/green] - [red]7.5 GB[/red]\n"
        f"Zon Emas Pilihan: [bold magenta]1.0 GB - 4.5 GB (Seeder Tertinggi Diutamakan)[/bold magenta]\n"
        f"Penapis Seeder Minimum: [bold red]>= 4 Seeds Sahaja[/bold red]"
        + (f" | Episod: S{season:02d}E{episode:02d}" if is_series else ""),
        border_style="cyan",
    ))

    # 3. Panggilan Pengikis X06 Selari (Multi-Threaded)
    all_raw_x06: List[Dict[str, Any]] = []
    source_stats: Dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(scrape_torrentio_turbo, target_id, is_series): "TorrentioTurbo",
            executor.submit(scrape_knaben_aggregator, media_title, year, is_series, season, episode): "KnabenAgg",
            executor.submit(scrape_apibay_multi, base_id, media_title, year, is_series, season, episode): "ApibayMulti",
            executor.submit(scrape_yts, base_id, media_title, is_series): "YTS",
            executor.submit(scrape_eztv, base_id, is_series, season, episode): "EZTV",
        }

        for future in as_completed(futures):
            src_name = futures[future]
            try:
                items = future.result() or []
                source_stats[src_name] = len(items)
                all_raw_x06.extend(items)
            except Exception:
                source_stats[src_name] = 0

    console.print(
        f"[cyan]📊 Statistik Asal X06 Diperoleh:[/cyan] "
        + " | ".join([f"[yellow]{k}: {v}[/yellow]" for k, v in source_stats.items()])
    )

    # 4. Ambil Senarai Sedia Ada daripada X03 di Redis
    raw_x03 = db_v2.get_torrent_list(target_id) or []

    # Fungsi Pengisihan Keutamaan: Zon Emas (1.0GB - 4.5GB) dahulu, kemudian bilangan Seeds
    def priority_sort_key(item: Dict[str, Any]):
        sz = item.get("size", 0)
        seeds = item.get("seeders", 0)
        is_golden = (golden_min_bytes <= sz <= golden_max_bytes)
        return (1 if is_golden else 0, seeds)

    # 5. Tapis Senarai Asal X03 (Wajib seeds >= 4 dan saiz <= 7.5GB)
    valid_x03: Dict[str, Dict[str, Any]] = {}
    for item in raw_x03:
        h = item.get("info_hash", "").lower().strip()
        seeds = int(item.get("seeders", 0))
        sz = int(item.get("size", 0))

        if len(h) != 40 or seeds < min_seeds:
            continue
        if sz < min_bytes or sz > max_bytes:
            continue

        if h not in valid_x03 or seeds > valid_x03[h]["seeders"]:
            valid_x03[h] = item

    sorted_x03 = sorted(valid_x03.values(), key=priority_sort_key, reverse=True)
    # Kekalkan maksimum 30 senarai seeder terbanyak dari X03
    x03_selected = sorted_x03[:30]
    console.print(f"[dim green]📥 Diambil {len(x03_selected)} torrent sah (>= 4 seeds) dari X03 (Had Asal 30).[/dim green]")

    # 6. Tapis & Nyah-duplikasi Senarai X06 Baharu
    valid_x06: Dict[str, Dict[str, Any]] = {}
    for item in all_raw_x06:
        h = item.get("info_hash", "").lower().strip()
        seeds = int(item.get("seeders", 0))
        sz = int(item.get("size", 0))

        if len(h) != 40 or seeds < min_seeds:
            continue
        if sz < min_bytes or sz > max_bytes:
            continue

        if h not in valid_x06 or seeds > valid_x06[h]["seeders"]:
            valid_x06[h] = item

    sorted_x06 = sorted(valid_x06.values(), key=priority_sort_key, reverse=True)

    # 7. Penggabungan Berkuota (Smart Quota Merge ke Limit 60)
    x03_map = {it["info_hash"]: it for it in x03_selected}

    # Jika X06 mempunyai rekod hash sama dengan seeder lebih tinggi, kemas kini rekod X03
    for it in sorted_x06:
        h = it["info_hash"]
        if h in x03_map and it["seeders"] > x03_map[h]["seeders"]:
            x03_map[h] = it

    final_x03_list = list(x03_map.values())
    seen_hashes = set(x03_map.keys())

    # Kira baki kekosongan untuk diisi oleh X06 sehingga had maksimum 60
    remaining_slots = max(0, 60 - len(final_x03_list))
    x06_added = []
    for it in sorted_x06:
        h = it["info_hash"]
        if h not in seen_hashes:
            x06_added.append(it)
            seen_hashes.add(h)
            if len(x06_added) >= remaining_slots:
                break

    console.print(f"[dim cyan]➕ X06 menambah {len(x06_added)} torrent baharu (Baki kuota: {remaining_slots}).[/dim cyan]")

    combined_list = final_x03_list + x06_added
    combined_list.sort(key=priority_sort_key, reverse=True)

    # Hadkan kepada 60 torrent teratas
    top_torrents = combined_list[:60]

    if not top_torrents:
        console.print(f"[bold red]❌ Tiada torrent melepasi tapisan (tiada torrent dengan >= 4 seeds atau dalam had 7.5 GB)![/bold red]")
        return False

    # 8. Simpan Senarai Lengkap ke Redis Sharded V3 (TTL 24 Jam)
    saved = db_v2.save_torrent_list(target_id, top_torrents, ttl_seconds=86400)

    if saved:
        table = Table(
            title=f"📋 Senarai Seeder Multi-Source Gabungan: {target_id} ({len(top_torrents)} Torrent Lulus / Dihadkan ke 60)",
            border_style="green",
        )
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Punca", justify="center", style="yellow", width=14)
        table.add_column("Kualiti", justify="center", style="magenta", width=8)
        table.add_column("Saiz", style="white", width=11)
        table.add_column("Seeds", justify="center", style="green", width=7)
        table.add_column("Keutamaan", justify="center", style="blue", width=14)
        table.add_column("Nama Pelepasan / Fail", style="dim")

        for idx, t in enumerate(top_torrents, 1):
            sz = t["size"]
            sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
            is_golden = (golden_min_bytes <= sz <= golden_max_bytes)
            tier_tag = "[bold green]1.0G-4.5G ★[/bold green]" if is_golden else "[dim]Lain-lain[/dim]"

            table.add_row(
                str(idx),
                t.get("source", "Torrent"),
                t.get("quality", "HD"),
                sz_str,
                str(t.get("seeders", 0)),
                tier_tag,
                t.get("name", "")[:55],
            )

        console.print(table)
        console.print(f"[bold green]✅ Berjaya menggabungkan dan menyimpan {len(top_torrents)} torrent (semua >= 4 seeds) ke Upstash Redis bagi kunci 'stremio:list:{target_id}'![/bold green]")
        return True

    console.print("[bold red]❌ Gagal mengemas kini data ke Upstash Redis![/bold red]")
    return False


def main():
    parser = argparse.ArgumentParser(description="V3 Multi-Source Seeder Lister Engine (X06)")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt0944947:1:1 atau tt0451787)")
    parser.add_argument("--title", default="", help="Tajuk pilihan/sandaran media")
    args = parser.parse_args()

    success = process_and_save_multisource_list(args.imdb, args.title)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()