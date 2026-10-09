#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - ORKESTRASI UTAMA (RUNNER CORE)
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X06_scrape_runner.py
# ==============================================================================

import os
import sys
import time
import shutil
import argparse
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import yt_dlp
from rich.console import Console
from rich.panel import Panel

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

SCRAPE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRAPE_DIR.parent
for p in [SCRAPE_DIR, PROJECT_ROOT]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

import X00_scrape_config as cfg
from X01_meta_resolver import MetaResolver, is_imdb_code
from X02_db_manager import db_manager
from X03_browser_stealth import (
    BrowserStealthManager,
    human_delay,
    smooth_scroll_down,
    smart_human_type
)
from X04_stream_capture import stream_capture
from X05_sync_bridge import sync_bridge

console = Console()


def download_stream_with_ytdlp(stream_info: Dict[str, Any], output_dir: Path, target_title: str) -> Optional[Path]:
    stream_url = stream_info.get("stream_url", "").strip()
    referer = stream_info.get("referer", "").strip()
    ua = stream_info.get("user_agent") or cfg.USER_AGENT_DESKTOP

    if not stream_url:
        console.print("[bold red]❌ Tiada URL strim sah untuk dimuat turun.[/bold red]")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(output_dir / f"{target_title}.%(ext)s")

    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

    ydl_opts = {
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/bestvideo+bestaudio/best",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "http_headers": headers,
        "nocheckcertificate": True,
    }

    console.print(f"[cyan]🚀 Memulakan muat turun yt-dlp: [underline]{stream_url[:80]}...[/underline][/cyan]")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(stream_url, download=True)
            if not info:
                return None
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            downloaded_file = None
            if "requested_downloads" in info and info["requested_downloads"]:
                downloaded_file = (
                    info["requested_downloads"][0].get("filepath")
                    or info["requested_downloads"][0].get("_filename")
                )

            if not downloaded_file or not os.path.exists(downloaded_file):
                downloaded_file = ydl.prepare_filename(info)
                if not os.path.exists(downloaded_file):
                    base, _ = os.path.splitext(downloaded_file)
                    for ext in [".mp4", ".mkv", ".webm", ".ts"]:
                        if os.path.exists(f"{base}{ext}"):
                            downloaded_file = f"{base}{ext}"
                            break

            if downloaded_file and os.path.exists(downloaded_file):
                console.print(f"[bold green]✓ Fail video siap dimuat turun:[/bold green] {downloaded_file}")
                return Path(downloaded_file)

    except Exception as e:
        console.print(f"[bold red]❌ Ralat muat turun yt-dlp: {e}[/bold red]")

    return None


def execute_scrape_pipeline(raw_imdb_id: str, raw_title: str = "", force: bool = False) -> bool:
    console.print(Panel.fit(
        f"[bold cyan]⚡ X06 WEB SCRAPE ENGINE: MEMULAKAN TUGASAN[/bold cyan]\n"
        f"IMDb Target : [bold white]{raw_imdb_id}[/bold white]\n"
        f"Tajuk Input : [bold white]{raw_title or 'Auto'}[/bold white]\n"
        f"Laman Web   : [cyan]{cfg.TARGET_WEB_URL}[/cyan]",
        border_style="cyan"
    ))

    # 1. Resolusi Metadata TMDB / Cinemeta
    resolver = MetaResolver()
    meta_info = resolver.resolve(raw_imdb_id)

    # Pastikan tajuk input BUKAN kod IMDb sebelum ditambah ke senarai carian
    if raw_title and not is_imdb_code(raw_title):
        clean_raw = raw_title.strip()
        if clean_raw not in meta_info["search_queries"]:
            meta_info["search_queries"].insert(0, clean_raw)

    if not meta_info["search_queries"]:
        console.print("[bold red]❌ Gagal mengekstrak sebarang tajuk teks yang sah bagi filem ini.[/bold red]")
        return False

    target_query = meta_info["search_queries"][0]
    imdb_id = meta_info["imdb_id"]
    display_title = meta_info["title"] or target_query

    console.print(f"[bold green]🎯 Sasaran Carian Utama Portal: '{target_query}'[/bold green]")

    # 2. Semakan Dedplikasi (SQLite & Status Aktif Redis Awan)
    if not force:
        if db_manager.is_completed(imdb_id) and sync_bridge.is_stream_active_in_cloud(imdb_id):
            console.print(Panel.fit(
                f"[bold green]✨ Media ini sudah siap dimuat naik ke B2 & aktif di Redis!\n"
                f"ID: {imdb_id} | Tajuk: {display_title}\n"
                f"Tugasan dilangkau secara selamat.[/bold green]",
                border_style="green"
            ))
            return True

    # 3. Lancarkan Sesi Pelayar Camoufox
    temp_job_dir = cfg.TEMP_DIR / f"job_{imdb_id.replace(':', '_')}_{int(time.time())}"
    temp_job_dir.mkdir(parents=True, exist_ok=True)

    try:
        with BrowserStealthManager.launch_session() as (browser, context, page):
            console.print(f"[*] Melayari portal sasaran: [underline]{cfg.TARGET_WEB_URL}[/underline]")
            try:
                page.goto(cfg.TARGET_WEB_URL, wait_until="domcontentloaded", timeout=45000)
                human_delay(2.5, 4.0)
            except Exception as e:
                console.print(f"[bold red]❌ Gagal memuatkan laman portal: {e}[/bold red]")
                return False

            # Diagnosis jika tersekat di Cloudflare
            BrowserStealthManager.handle_cloudflare_challenge(page, max_wait_sec=10)

            # Cari input carian menggunakan logik penuh X01_step01a_nav
            search_input = BrowserStealthManager.find_search_input(page, timeout_sec=20)
            if not search_input:
                console.print(f"[bold red]❌ Medan kotak carian tidak ditemui. URL Semasa: {page.url} | Tajuk Laman: '{page.title()}'[/bold red]")
                return False

            # Taip carian menggunakan ritme semulajadi
            console.print(f"[*] Mengisi kata kunci carian teks: [bold cyan]'{target_query}'[/bold cyan]")
            smart_human_type(search_input, page, target_query)

            # Pantau jika ada popup tab baru terbuka semasa submit
            new_pages = []
            def on_page_opened(p):
                new_pages.append(p)
            context.on("page", on_page_opened)

            try:
                BrowserStealthManager.select_dropdown_match(page, target_query)
            finally:
                page.wait_for_timeout(1500)
                try:
                    context.remove_listener("page", on_page_opened)
                except Exception:
                    pass

            active_page = new_pages[-1] if new_pages else page
            try:
                active_page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass

            # Pastikan berada di laman video sebenar
            active_page = BrowserStealthManager.ensure_movie_watch_page(active_page, target_query)

            # 4. Aktifkan Pemain Hydrax & Buka Popup
            player_page = stream_capture.activate_player_and_popup(active_page)

            # 5. Pintas Strim
            media_info = stream_capture.capture_stream(player_page)
            if media_info["status"] != "success" or not media_info["stream_url"]:
                raise RuntimeError("Tiada pautan manifes video (.m3u8/.mp4) sah berjaya dipintas.")

            # 6. Muat Turun Melalui yt-dlp
            safe_title = f"{imdb_id.replace(':', '_')}_{int(time.time())}"
            downloaded_video = download_stream_with_ytdlp(media_info, temp_job_dir, safe_title)
            if not downloaded_video:
                raise RuntimeError("Proses muat turun fail video melalui yt-dlp gagal.")

            # 7. Pemindahan ke B2 & Pendaftaran Metadata Redis / SQLite
            sync_res = sync_bridge.sync_local_video_to_b2(
                local_video_file=downloaded_video,
                meta_info=meta_info,
                resolution=media_info.get("quality", "1080p")
            )
            if not sync_res:
                raise RuntimeError("Penyegerakan B2 atau pendaftaran Upstash Redis gagal.")

            return True

    except Exception as err:
        console.print(f"[bold red]❌ Ralat pelaksanaan aliran: {err}[/bold red]")
        db_manager.save_or_update_record({
            "imdb_id": imdb_id,
            "title": display_title,
            "status": "failed",
            "error_msg": str(err)
        })
        return False

    finally:
        if temp_job_dir.exists():
            shutil.rmtree(temp_job_dir, ignore_errors=True)
            console.print("[dim]🧹 Ruang kerja sementara dibersihkan.[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="X06 Web Scrape Runner Core Engine")
    parser.add_argument("--imdb", required=True, help="IMDb identifier (cth: tt5776858)")
    parser.add_argument("--title", default="", help="Tajuk video daripada Stremio")
    parser.add_argument("--force", action="store_true", help="Paksa muat turun semula")
    args = parser.parse_args()

    success = execute_scrape_pipeline(args.imdb, args.title, args.force)
    sys.exit(0 if success else 1)