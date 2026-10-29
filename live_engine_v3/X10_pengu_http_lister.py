#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - DIRECT HTTP STREAM LISTER (OPTIMIZED)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X10_pengu_http_lister.py
#
# CIRI-CIRI PENAMBAHBAIKAN:
# 1. Pengekstrakan Saiz Tepat: Membaca daripada 'title', 'description', dan 'name'.
#    Menyokong semakan HTTP HEAD bagi pautan terus MP4. Sandaran 1.8GB jika tiada.
# 2. Penapis Resolusi Bersasar:
#    - DITOLAK: 4K (2160p/UHD), 480p, 360p, SD, CAM.
#    - DITERIMA: 720p, 1080p, 1440p (2K).
# 3. Had Siling Mutlak 7.0 GB: Menolak mana-mana fail melebihi 7.0 GB.
# 4. Pengecaman Provider Asal: 2Peckle, MovieBox, dsb.
# ==============================================================================

import re
import sys
import json
import hashlib
import argparse
import importlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import unquote

from curl_cffi import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# ------------------------------------------------------------------------------
# 1. SETUP LALUAN & IMPORT REDIS V3
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

PENGU_AUTH_TOKEN = "GwjNukzuTU_1cw6E2zcTgrxhyTYqmBDFhcUFgmQmDr0"
PENGU_BASE_URL = f"https://pengu.uk/%7B%22auth_token%22%3A%22{PENGU_AUTH_TOKEN}%22%7D"


# ------------------------------------------------------------------------------
# 2. PEMBANTU KUALITI, METADATA & FORMAT
# ------------------------------------------------------------------------------
def fetch_cinemeta_meta(imdb_id: str, is_series: bool = False) -> Dict[str, str]:
    """Mengambil tajuk rasmi dan tahun keluaran daripada Cinemeta Stremio."""
    base_id = imdb_id.split(":")[0]
    endpoint_type = "series" if is_series else "movie"
    url = f"https://v3-cinemeta.strem.io/meta/{endpoint_type}/{base_id}.json"
    meta = {"title": base_id, "year": ""}
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            name = data.get("name", "")
            year = str(data.get("year", ""))
            if name:
                meta = {"title": name, "year": year}
    except Exception:
        pass
    return meta


def detect_and_validate_quality(raw_text: str) -> Optional[str]:
    """
    Menapis resolusi mengikut kriteria:
    - TOLAK MUTLAK: 4K (2160p/UHD), 480p, 360p, SD, CAM
    - KEKALKAN: 1440p (2K), 1080p, 720p
    """
    t = raw_text.lower()

    # 1. Sekat kualiti yang tidak diingini
    bad_tokens = [
        "2160p", "4k", "uhd",
        "480p", "360p", "240p", "sd", "dvdrip", "xvid",
        "cam", "camrip", "hdcam", "telesync", "tsrip", "dvdscr"
    ]
    for bad in bad_tokens:
        if re.search(rf"\b{bad}\b", t) or (bad in ["4k", "uhd", "360p", "480p", "2160p"] and bad in t):
            return None

    # 2. Luluskan hanya 3 variasi saiz
    if any(q in t for q in ["1440p", "2k", "qhd"]):
        return "1440p"
    if any(q in t for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in t for q in ["720p", "hd"]):
        return "720p"

    return None


def parse_size_bytes(raw_text: str, stream_url: str = "") -> int:
    """
    Mengekstrak saiz fail sebenar daripada teks metadata.
    Jika tiada dalam teks dan pautan adalah fail terus, cuba semak HTTP HEAD.
    Sandaran 1.8 GB jika tiada sebarang data.
    """
    # 1. Cari nilai saiz dalam teks (GB / GiB / MB / MiB)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|GiB|MB|MiB)\b", raw_text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        unit = m.group(2).upper()
        if "GB" in unit:
            return int(val * 1024**3)
        return int(val * 1024**2)

    # 2. Semakan pantas HTTP HEAD untuk fail terus (bukan DASH/HLS)
    if stream_url and not any(ext in stream_url.lower() for ext in [".m3u8", ".mpd", "/dash/"]):
        try:
            h_resp = requests.head(stream_url, timeout=2.0, headers={"User-Agent": "Stremio/4.4.168"})
            cl = h_resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > 10 * 1024 * 1024:
                return int(cl)
        except Exception:
            pass

    # 3. Anggaran sandaran kecemasan
    return int(1.8 * 1024**3)


def extract_provider_source(name_field: str, title_field: str) -> str:
    """Mengekstrak label penyedia asal (cth: 2Peckle, MovieBox, MoviesDrives)."""
    src_match = re.search(r"Source:\s*([\w\-]+)", title_field, re.IGNORECASE)
    if src_match:
        return src_match.group(1)

    lines = [l.strip() for l in name_field.split("\n") if l.strip()]
    if len(lines) >= 3:
        return lines[-1]
    return "MovieBox"


def fetch_pengu_streams(target_id: str, is_series: bool = False, display_title: str = "") -> List[Dict[str, Any]]:
    """Mengekstrak strim HTTP terus daripada PenguPlay dengan pengesahan saiz sebenar."""
    endpoint_type = "series" if is_series else "movie"
    url = f"{PENGU_BASE_URL}/stream/{endpoint_type}/{target_id}.json"
    results = []
    max_ceiling_bytes = int(7.0 * 1024 * 1024 * 1024)  # Had siling: 7.0 GB

    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=12,
            headers={"User-Agent": "Stremio/4.4.168"}
        )
        if resp.status_code != 200:
            return []

        streams = resp.json().get("streams", [])
        seen_urls = set()

        for s in streams:
            stream_url = s.get("url", "").strip()
            name_field = s.get("name", "")
            title_field = s.get("title", "")
            desc_field = s.get("description", "")

            if not stream_url or stream_url in seen_urls:
                continue

            # Gabungkan semua medan untuk pembacaan menyeluruh
            combined_desc = f"{name_field}\n{title_field}\n{desc_field}"
            quality = detect_and_validate_quality(combined_desc)
            if not quality:
                continue

            # Ekstrak saiz sebenar
            size_bytes = parse_size_bytes(combined_desc, stream_url=stream_url)

            # Tolak fail yang melebihi had siling 7.0 GB
            if size_bytes > max_ceiling_bytes:
                continue

            seen_urls.add(stream_url)

            # Pengesanan protokol penstriman
            stream_format = "MP4"
            if ".m3u8" in stream_url or "hls" in combined_desc.lower():
                stream_format = "HLS"
            elif ".mpd" in stream_url or "dash" in combined_desc.lower():
                stream_format = "DASH"

            provider = extract_provider_source(name_field, combined_desc)
            pseudo_hash = hashlib.sha1(stream_url.encode("utf-8")).hexdigest()

            movie_tag = display_title or target_id
            clean_name = f"{movie_tag} [{quality}] [{stream_format}] [{provider}] [PenguPlay]"

            results.append({
                "name": clean_name,
                "info_hash": pseudo_hash,
                "seeders": 999,
                "size": size_bytes,
                "quality": quality,
                "source": "PenguPlay",
                "stream_type": "http",
                "stream_format": stream_format,
                "direct_url": stream_url,
                "file_idx": 0,
            })
    except Exception as e:
        console.print(f"[dim red]Panggilan PenguPlay terhenti: {e}[/dim red]")

    return results


# ------------------------------------------------------------------------------
# 3. ENJIN PENGURUSAN DATA & INTEGRASI REDIS
# ------------------------------------------------------------------------------
def process_pengu_http_fallback(raw_imdb_id: str, fallback_title: str = "") -> bool:
    target_id = unquote(raw_imdb_id).strip()
    is_series = ":" in target_id
    base_id = target_id.split(":")[0] if is_series else target_id

    # 1. Semak Senarai Semasa di Redis
    existing_list = db_v2.get_torrent_list(target_id) or []
    valid_existing = [
        t for t in existing_list
        if int(t.get("seeders", 0)) >= 2 or t.get("stream_type") == "http"
    ]

    # Aktifkan jika pilihan < 40
    if len(valid_existing) >= 40:
        console.print(
            f"[bold green]✅ Senarai mencukupi ({len(valid_existing)} pilihan). "
            f"X10 PenguPlay dilangkau tanpa sebarang perubahan.[/bold green]"
        )
        return True

    # 2. Dapatkan Tajuk Rasmi
    meta = fetch_cinemeta_meta(base_id, is_series=is_series)
    movie_name = meta.get("title")
    if not movie_name or movie_name.startswith("tt"):
        movie_name = fallback_title if fallback_title and not fallback_title.startswith("tt") else base_id

    year = meta.get("year", "")
    display_title = f"{movie_name} ({year})" if year and year not in movie_name else movie_name

    console.print(Panel.fit(
        f"[bold yellow]🐧 AKTIVASI ENJIN STRIM TERUS HTTP PENGUPLAY (X10 OPTIMIZED)[/bold yellow]\n"
        f"Tajuk Filem: [bold white]{display_title}[/bold white] | ID: [bold cyan]{target_id}[/bold cyan]\n"
        f"Kualiti Dibenarkan: [bold green]1080p, 720p, 1440p (2K)[/bold green] ([red]4K, 480p, 360p Ditolak[/red])\n"
        f"Had Siling Saiz : [bold red]Maksimum 7.0 GB[/bold red]",
        border_style="yellow",
    ))

    # 3. Ambil Strim Terus daripada PenguPlay
    http_streams = fetch_pengu_streams(target_id, is_series=is_series, display_title=display_title)
    console.print(f"[cyan]📦 Ditemui daripada PenguPlay:[/cyan] {len(http_streams)} strim sah")

    if not http_streams:
        console.print("[dim yellow]⚠️ Tiada strim HTTP tambahan ditemui di PenguPlay.[/dim yellow]")
        return True

    # 4. Hadkan Tambahan Maksimum 40
    seen_hashes = {t["info_hash"] for t in existing_list}
    new_to_add = []

    for s in http_streams:
        if s["info_hash"] not in seen_hashes:
            new_to_add.append(s)
            seen_hashes.add(s["info_hash"])
            if len(new_to_add) >= 40:
                break

    console.print(f"[bold cyan]➕ Bersedia menambah {len(new_to_add)} strim HTTP unik ke Redis.[/bold cyan]")

    # 5. Isih Keutamaan: HTTP (999 Seeds) & Kualiti (1080p/1440p) dahulu
    def sort_key(it):
        s_type = 1 if it.get("stream_type") == "http" else 0
        sz = it.get("size", 0)
        s = it.get("seeders", 0)
        q = it.get("quality", "")
        q_weight = 3 if q in ["1080p", "1440p", "2K"] else (2 if q == "720p" else 1)
        return (s_type, q_weight, s, sz)

    combined_list = existing_list + new_to_add
    combined_list.sort(key=sort_key, reverse=True)
    final_list = combined_list[:60]

    # 6. Simpan ke Redis Sharded V3
    saved = db_v2.save_torrent_list(target_id, final_list, ttl_seconds=86400)
    if saved:
        table = Table(
            title=f"📋 Senarai Seeder & HTTP Dikemas Kini: {display_title} ({len(final_list)} Pilihan)",
            border_style="yellow"
        )
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Punca", justify="center", style="yellow", width=12)
        table.add_column("Jenis", justify="center", style="blue", width=8)
        table.add_column("Kualiti", justify="center", style="magenta", width=8)
        table.add_column("Saiz", style="white", width=10)
        table.add_column("Seeds", justify="center", style="green", width=7)
        table.add_column("Nama Pelepasan / Fail", style="dim")

        for idx, t in enumerate(final_list, 1):
            sz = t["size"]
            sz_str = f"{sz / (1024**3):.2f} GB" if sz >= 1024**3 else f"{sz / (1024**2):.1f} MB"
            stype = t.get("stream_format") or ("P2P" if t.get("source") != "PenguPlay" else "HTTP")
            table.add_row(
                str(idx),
                t.get("source", "Torrent"),
                stype,
                t.get("quality", "HD"),
                sz_str,
                str(t.get("seeders", 0)),
                t.get("name", "")[:60],
            )
        console.print(table)
        console.print(f"[bold green]✅ Berjaya mengemas kini {len(final_list)} pilihan ke Redis bagi kunci 'stremio:list:{target_id}'![/bold green]")
        return True

    return False


def main():
    parser = argparse.ArgumentParser(description="V3 Direct HTTP Stream Fallback Lister (X10 Optimized)")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt15327088)")
    parser.add_argument("--title", default="", help="Tajuk pilihan/sandaran media")
    args = parser.parse_args()

    process_pengu_http_fallback(args.imdb, args.title)


if __name__ == "__main__":
    main()