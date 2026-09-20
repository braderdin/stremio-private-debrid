#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - RUNNER CORE ENGINE (ARIA2C + RCLONE + REDIS)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/03_runner_core.py
# CIRI: 4-LAYER AUTO-RESOLVER + PUBLIC TRACKERS + SMART B2 STORAGE ALLOCATOR
# ==============================================================================

import os
import sys
import re
import time
import shutil
import hashlib
import argparse
import subprocess
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Pemuatan Modul Teras Modular (00_config, 01_redis_db, 02_b2_storage)
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    _config = importlib.import_module("00_config")
    _redis_mod = importlib.import_module("01_redis_db")
    _b2_mod = importlib.import_module("02_b2_storage")

    db = getattr(_redis_mod, "db")
    storage = getattr(_b2_mod, "storage")
    TEMP_DIR = getattr(_config, "TEMP_DIR", CURRENT_DIR / "temp")
except Exception as e:
    console.print(f"[bold red]❌ Gagal mengimport modul asas: {e}[/bold red]")
    sys.exit(1)

# Senarai Pelacak Awam (Public Trackers) Berprestasi Tinggi
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce"
]


# ==============================================================================
# BAHAGIAN A: ENJIN AUTO-RESOLVER & 4 LAPISAN FALLBACK
# ==============================================================================

def fetch_metadata(imdb_id: str) -> Tuple[str, str]:
    """Mendapatkan tajuk rasmi dan tahun keluaran melalui Cinemeta Stremio atau TMDB."""
    base_id = imdb_id.split(":")[0]
    
    # 1. Cinemeta API (Percuma, rasmi ekosistem Stremio)
    try:
        url = f"https://v3-cinemeta.strem.io/meta/movie/{base_id}.json"
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code == 200:
                meta = r.json().get("meta", {})
                name = meta.get("name")
                year = str(meta.get("year", ""))
                if name:
                    return name, year
    except Exception:
        pass

    # 2. TMDB Sandaran jika kunci persekitaran dibekalkan
    tmdb_key = getattr(_config, "TMDB_API_KEY", "") or os.getenv("TMDB_API_KEY", "")
    if tmdb_key:
        try:
            url = f"https://api.themoviedb.org/3/find/{base_id}?external_source=imdb_id&api_key={tmdb_key}"
            with httpx.Client(timeout=8.0) as client:
                r = client.get(url)
                if r.status_code == 200:
                    res = r.json().get("movie_results", [])
                    if res:
                        return res[0].get("title", ""), res[0].get("release_date", "")[:4]
        except Exception:
            pass

    return base_id, ""


def rank_and_pick_torrent(items: List[Dict[str, Any]], source_name: str) -> Optional[Dict[str, Any]]:
    """Menapis saiz (500MB - 9.0GB) dan mengira markah berasaskan Zon Emas (1.0GB - 4.0GB)."""
    MIN_BYTES = 500 * 1024 * 1024        # 500 MB
    MAX_BYTES = 9.0 * 1024 * 1024 * 1024  # 9.0 GB
    SWEET_MIN = 1.0 * 1024 * 1024 * 1024  # 1.0 GB
    SWEET_MAX = 4.0 * 1024 * 1024 * 1024  # 4.0 GB

    candidates = []
    for it in items:
        h = (it.get("info_hash") or it.get("hash") or "").strip().lower()
        title = (it.get("name") or it.get("title") or "").strip()
        if not h or len(h) != 40:
            continue

        try:
            seeds = int(it.get("seeders") or it.get("seeds") or 0)
            sz = int(it.get("size") or it.get("size_bytes") or 0)
        except ValueError:
            continue

        if seeds < 1 or sz < MIN_BYTES or sz > MAX_BYTES:
            continue

        # Pengganda markah zon emas
        score = seeds * 1.8 if (SWEET_MIN <= sz <= SWEET_MAX) else seeds
        candidates.append({
            "name": title,
            "info_hash": h,
            "seeders": seeds,
            "size": sz,
            "score": score,
            "source": source_name
        })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]


def try_layer_1_apibay(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Lapisan 1: ThePirateBay Direct API menggunakan ID IMDb."""
    base_id = imdb_id.split(":")[0]
    url = f"https://apibay.org/q.php?q={base_id}"
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(url)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data and data[0].get("name") != "No results returned":
                    return rank_and_pick_torrent(data, "Lapisan 1 (Apibay ID)")
    except Exception as e:
        console.print(f"[dim]Lapisan 1 (Apibay ID) langkau: {e}[/dim]")
    return None


def try_layer_2_yts(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Lapisan 2: YTS (YIFY) Official REST API."""
    base_id = imdb_id.split(":")[0]
    url = f"https://yts.mx/api/v2/list_movies.json?query_term={base_id}"
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(url)
            if r.status_code == 200:
                data = r.json().get("data", {})
                movies = data.get("movies", [])
                if movies:
                    movie = movies[0]
                    torrents = movie.get("torrents", [])
                    # Beri keutamaan kualiti 1080p
                    target_t = next((t for t in torrents if "1080p" in t.get("quality", "").lower()), None)
                    if not target_t and torrents:
                        target_t = torrents[0]

                    if target_t and target_t.get("hash"):
                        sz = int(target_t.get("size_bytes", 0))
                        return {
                            "name": f"{movie.get('title', base_id)} ({movie.get('year', '')}) [{target_t.get('quality', 'HD')}] [YTS]",
                            "info_hash": target_t["hash"].lower(),
                            "seeders": int(target_t.get("seeds", 50)),
                            "size": sz,
                            "source": "Lapisan 2 (YTS Official API)"
                        }
    except Exception as e:
        console.print(f"[dim]Lapisan 2 (YTS) langkau: {e}[/dim]")
    return None


def try_layer_3_eztv(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Lapisan 3: EZTV API (Khusus Siri TV & rancangan berformat nombor)."""
    num_only = re.sub(r"[^\d]", "", imdb_id.split(":")[0])
    if not num_only:
        return None
    url = f"https://eztv.re/api/get-torrents?imdb_id={num_only}&limit=30"
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(url)
            if r.status_code == 200:
                torrents = r.json().get("torrents", [])
                if torrents:
                    best = max(torrents, key=lambda x: int(x.get("seeds", 0)))
                    h = best.get("hash")
                    if h:
                        return {
                            "name": best.get("title", "Siri Video"),
                            "info_hash": h.lower(),
                            "seeders": int(best.get("seeds", 1)),
                            "size": int(best.get("size_bytes", 0)),
                            "source": "Lapisan 3 (EZTV API)"
                        }
    except Exception as e:
        console.print(f"[dim]Lapisan 3 (EZTV) langkau: {e}[/dim]")
    return None


def try_layer_4_apibay_title(title: str, year: str) -> Optional[Dict[str, Any]]:
    """Lapisan 4: ThePirateBay Direct API menggunakan Carian Kata Kunci Tajuk & Tahun."""
    clean_t = re.sub(r"[^\w\s]", " ", title).strip()
    query = f"{clean_t} {year}".strip() if year else clean_t
    url = f"https://apibay.org/q.php?q={httpx.URL('', params={'q': query}).params['q']}"
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(url)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data and data[0].get("name") != "No results returned":
                    return rank_and_pick_torrent(data, "Lapisan 4 (Apibay Tajuk)")
    except Exception as e:
        console.print(f"[dim]Lapisan 4 (Apibay Tajuk) langkau: {e}[/dim]")
    return None


def resolve_torrent_4_layers(imdb_id: str, default_title: str) -> Optional[Dict[str, Any]]:
    """Melaksanakan carian berantai 4 lapisan sehingga menemui torrent terbaik."""
    title, year = fetch_metadata(imdb_id)
    display_title = f"{title} ({year})" if year else title
    console.print(f"[cyan]🎬 Metadata Dikesan:[/cyan] [bold white]{display_title}[/bold white]")

    # 1. Uji Lapisan 1
    console.print("[cyan]⏳ Menghubungi Lapisan 1 (Apibay ID)...[/cyan]")
    res = try_layer_1_apibay(imdb_id)
    if res:
        return res

    # 2. Uji Lapisan 2
    console.print("[cyan]⏳ Beralih ke Lapisan 2 (YTS Official API)...[/cyan]")
    res = try_layer_2_yts(imdb_id)
    if res:
        return res

    # 3. Uji Lapisan 3
    console.print("[cyan]⏳ Beralih ke Lapisan 3 (EZTV API)...[/cyan]")
    res = try_layer_3_eztv(imdb_id)
    if res:
        return res

    # 4. Uji Lapisan 4
    console.print(f"[cyan]⏳ Beralih ke Lapisan 4 (Apibay Kata Kunci: '{display_title}')...[/cyan]")
    res = try_layer_4_apibay_title(title, year)
    if res:
        return res

    return None


# ==============================================================================
# BAHAGIAN B: FUNGSI MUAT TURUN, B2 & REDIS
# ==============================================================================

def build_magnet_link(info_hash: str, name: str) -> str:
    """Membina URI Magnet lengkap berserta pelacak pantas."""
    tr_params = "&".join([f"tr={t}" for t in PUBLIC_TRACKERS])
    return f"magnet:?xt=urn:btih:{info_hash.lower()}&dn={name}&{tr_params}"


def calculate_sha256(filepath: Path) -> str:
    """Mengira hash SHA-256 fail video besar secara blok 4MB."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def run_aria2c_download(magnet: str, out_dir: Path, file_idx: int) -> Optional[Path]:
    """Menjalankan enjin aria2c CLI untuk menyedut fail video sasaran."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "--seed-time=0",
        "--summary-interval=5",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--bt-stop-timeout=600",
        f"--dir={str(out_dir)}",
    ]

    if file_idx > 0:
        cmd.append(f"--select-file={file_idx + 1}")

    cmd.append(magnet)

    console.print(f"[cyan]🚀 Memulakan aria2c (Indeks Fail: {file_idx})...[/cyan]")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if res.returncode != 0:
        console.print(f"[bold red]❌ aria2c tamat dengan ralat (Kod {res.returncode}):[/bold red]\n{res.stdout[-600:]}")
        return None

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".m4v"}
    candidates = [p for p in out_dir.rglob("*") if p.is_file() and p.suffix.lower() in video_exts]

    if not candidates:
        console.print("[bold red]❌ Tiada fail video dikesan selepas muat turun aria2c![/bold red]")
        return None

    candidates.sort(key=lambda x: x.stat().st_size, reverse=True)
    return candidates[0]


def upload_via_rclone(local_file: Path, b2_account: Dict[str, Any], dest_path: str) -> bool:
    """Memindahkan fail ke B2 menggunakan konfigurasi dinamik rclone."""
    env = os.environ.copy()
    remote_name = "TEMP_B2_RUNNER"

    env[f"RCLONE_CONFIG_{remote_name}_TYPE"] = "b2"
    env[f"RCLONE_CONFIG_{remote_name}_ACCOUNT"] = b2_account["key_id"]
    env[f"RCLONE_CONFIG_{remote_name}_KEY"] = b2_account["app_key"]
    env[f"RCLONE_CONFIG_{remote_name}_ENDPOINT"] = b2_account["endpoint"]

    target_remote = f"{remote_name}:{b2_account['bucket_name']}/{dest_path.lstrip('/')}"
    cmd = [
        "rclone", "copyto",
        str(local_file),
        target_remote,
        "--transfers=4",
        "--fast-list",
        "--stats=10s",
        "--stats-one-line"
    ]

    console.print(f"[cyan]☁️ Memindahkan fail ke B2 Bucket: #{b2_account['index']} ({b2_account['bucket_name']})...[/cyan]")
    res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if res.returncode != 0:
        console.print(f"[bold red]❌ Rclone gagal memindahkan fail:[/bold red]\n{res.stdout[-600:]}")
        return False
    return True


def execute_job(info_hash: str, imdb_id: str, file_idx: int, raw_title: str):
    """Aliran lengkap: Semak Cache -> Auto-Resolve -> aria2c -> B2 -> Redis."""
    console.print(Panel.fit(
        f"[bold cyan]⚡ DEBRID RUNNER ENGINE: MEMPROSES TUGAS[/bold cyan]\n"
        f"[yellow]IMDb ID:[/yellow] {imdb_id} | [yellow]Hash Awal:[/yellow] {info_hash}\n"
        f"[white]Tajuk Permintaan:[/white] {raw_title}",
        border_style="cyan"
    ))

    # 1. Semakan Awal Cache di Redis
    existing_meta = db.get_stream_metadata(imdb_id)
    if existing_meta and existing_meta.get("b2_bucket") and existing_meta.get("file_path"):
        console.print("[bold green]✨ Fail ini sudah wujud dalam pangkalan data B2! Melangkau muat turun.[/bold green]")
        return

    # 2. Enjin Auto-Resolver sekiranya hash bernilai 'AUTO' atau tidak sah
    if info_hash.strip().upper() == "AUTO" or len(info_hash.strip()) != 40:
        console.print(f"[bold yellow]🔍 Hash 'AUTO' dikesan. Mengaktifkan 4 Lapisan Fallback untuk: {imdb_id}...[/bold yellow]")
        resolved = resolve_torrent_4_layers(imdb_id, raw_title)
        if not resolved:
            console.print("[bold red]❌ Gagal mencari torrent sah selepas mencuba kesemua 4 lapisan fallback![/bold red]")
            return

        info_hash = resolved["info_hash"]
        raw_title = resolved["name"]
        sz_mb = resolved["size"] / (1024 * 1024)
        console.print(Panel(
            f"[bold green]✅ TORRENT BERJAYA DITEMUI![/bold green]\n"
            f"• Sumber : [yellow]{resolved['source']}[/yellow]\n"
            f"• Tajuk  : [white]{raw_title}[/white]\n"
            f"• Hash   : [cyan]{info_hash}[/cyan]\n"
            f"• Saiz   : [white]{sz_mb:.1f} MB[/white] | Seeders: [green]{resolved['seeders']}[/green]",
            border_style="green"
        ))

    # 3. Persediaan Direktori Sementara
    job_dir = TEMP_DIR / f"job_{info_hash[:10]}_{int(time.time())}"
    magnet_url = build_magnet_link(info_hash, raw_title)

    try:
        # 4. Muat Turun Torrent melalui aria2c
        downloaded_file = run_aria2c_download(magnet_url, job_dir, file_idx)
        if not downloaded_file:
            console.print("[bold red]❌ Muat turun dibatalkan kerana ralat pada peringkat aria2c.[/bold red]")
            return

        file_size = downloaded_file.stat().st_size
        console.print(f"[green]✅ Fail siap disedut: {downloaded_file.name} ({file_size / (1024*1024):.2f} MB)[/green]")

        # 5. Pengiraan SHA-256
        console.print("[cyan]🔍 Mengira cap jari digital SHA-256...[/cyan]")
        sha256_hash = calculate_sha256(downloaded_file)

        # 6. Pemilihan Akaun B2 (Round-Robin 20 Akaun dengan Pelupusan LRU jika Penuh)
        b2_target = storage.allocate_storage_with_eviction(file_size, db)
        if not b2_target:
            console.print("[bold red]❌ Gagal memperuntukkan storan B2 yang sah![/bold red]")
            return

        # 7. Pemindahan Fail ke Storan B2
        clean_name = f"{imdb_id.replace(':', '_')}_{downloaded_file.name}"
        b2_rel_path = f"media/{clean_name}"
        upload_ok = upload_via_rclone(downloaded_file, b2_target, b2_rel_path)

        if not upload_ok:
            console.print("[bold red]❌ Pemindahan ke B2 gagal. Operasi dihentikan.[/bold red]")
            return

        # 8. Pendaftaran Metadata ke Upstash Redis
        metadata = {
            "title": raw_title,
            "resolution": "1080p" if "1080" in raw_title else ("720p" if "720" in raw_title else "HD"),
            "file_name": clean_name,
            "file_path": b2_rel_path,
            "size_bytes": file_size,
            "b2_account_index": b2_target["index"],
            "b2_bucket": b2_target["bucket_name"],
            "info_hash": info_hash.lower(),
            "sha256": sha256_hash,
            "created_at": int(time.time()),
        }

        saved = db.set_stream_metadata(imdb_id, metadata)
        if saved:
            console.print(Panel(
                f"[bold green]🎉 KESELURUHAN PROSES DEBRID BERJAYA![/bold green]\n\n"
                f"[yellow]Pautan Penstriman B2 Proksi:[/yellow]\n"
                f"[cyan]{metadata.get('stream_url')}[/cyan]",
                border_style="green"
            ))
        else:
            console.print("[bold red]⚠️ Video dimuat naik ke B2 tetapi gagal dikemas kini ke Redis![/bold red]")

    finally:
        # 9. Pembersihan Cakera Runner
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            console.print("[dim]🧹 Direktori sementara berjaya dibersihkan.[/dim]")


def main():
    parser = argparse.ArgumentParser(description="Stremio Private Debrid Task Runner")
    parser.add_argument("--hash", required=True, help="Torrent InfoHash atau 'AUTO'")
    parser.add_argument("--imdb", required=True, help="IMDb ID atau Siri ID")
    parser.add_argument("--idx", default="0", help="Indeks fail torrent")
    parser.add_argument("--title", default="Video", help="Nama fail tajuk")
    args = parser.parse_args()

    try:
        f_idx = int(args.idx)
    except ValueError:
        f_idx = 0

    execute_job(args.hash, args.imdb, f_idx, args.title)


if __name__ == "__main__":
    main()