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
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse

import yt_dlp
from playwright.sync_api import Page
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
from X02_db_manager import db_manager
from X03_browser_stealth import (
    launch_camoufox_session,
    human_delay,
    find_search_input,
    smart_human_type,
    select_dropdown_match,
    ensure_movie_watch_page,
    activate_hydrax_and_open_popup,
    resolve_cloudflare_turnstile
)
from X04_stream_capture import configure_resolution_and_sniff
from X05_sync_bridge import sync_bridge

console = Console()


def execute_step01(page: Page, target_url: str, search_query: str) -> Tuple[Page, Dict[str, str]]:
    """Melaksanakan Langkah 1: Navigasi, carian, pemilihan Hydrax, dan popup pemain."""
    console.print("\n[bold blue]=== [LANGKAH 1: NAVIGASI, HYDRAX & POPUP RESOLUSI] ===[/bold blue]")
    console.print(f"[*] Melayari laman sasaran: [underline]{target_url}[/underline]")
    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
    human_delay(2.5, 4.0)

    # Lepaskan sekatan Cloudflare Turnstile terlebih dahulu
    resolve_cloudflare_turnstile(page)

    input_box = find_search_input(page)
    if not input_box:
        raise RuntimeError(f"Gagal mengesan medan carian. URL: {page.url} | Tajuk: '{page.title()}'")

    console.print(f"[*] Mengisi kata kunci carian: [bold cyan]'{search_query}'[/bold cyan]")
    smart_human_type(input_box, page, search_query)

    new_pages = []
    def on_page_opened(p):
        new_pages.append(p)

    page.context.on("page", on_page_opened)
    try:
        select_dropdown_match(page, search_query)
    finally:
        page.wait_for_timeout(1500)
        try:
            page.context.remove_listener("page", on_page_opened)
        except Exception:
            pass

    active_page = new_pages[-1] if new_pages else page
    try:
        active_page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    active_page = ensure_movie_watch_page(active_page, search_query)
    player_page = activate_hydrax_and_open_popup(active_page)
    media_info = configure_resolution_and_sniff(player_page)

    if not media_info.get("stream_url"):
        console.print("[red][✕] Amaran: Tiada pautan strim media dikesan daripada trafik rangkaian.[/red]")

    return player_page, media_info


def download_stream_with_ytdlp(stream_info: Dict[str, Any], output_dir: Path, target_title: str) -> Optional[Path]:
    stream_url = stream_info.get("stream_url", "").strip()
    referer = stream_info.get("referer", "").strip()
    ua = stream_info.get("user_agent") or cfg.USER_AGENT_DESKTOP

    if not stream_url:
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


def execute_scrape_pipeline(imdb_id: str, clean_title: str, force: bool = False) -> bool:
    console.print(Panel.fit(
        f"[bold cyan]⚡ X06 WEB SCRAPE ENGINE: MEMULAKAN TUGASAN[/bold cyan]\n"
        f"IMDb Target  : [bold white]{imdb_id}[/bold white]\n"
        f"Tajuk Carian : [bold green]{clean_title}[/bold green]\n"
        f"Portal Web   : [cyan]{cfg.TARGET_WEB_URL}[/cyan]",
        border_style="cyan"
    ))

    # 1. Semakan Dedplikasi (SQLite & Status Aktif Redis Awan)
    if not force:
        if db_manager.is_completed(imdb_id) and sync_bridge.is_stream_active_in_cloud(imdb_id):
            console.print(Panel.fit(
                f"[bold green]✨ Media ini sudah siap dimuat naik ke B2 & aktif di Redis!\n"
                f"ID: {imdb_id} | Tajuk: {clean_title}\n"
                f"Tugasan dilangkau secara selamat.[/bold green]",
                border_style="green"
            ))
            return True

    # 2. Persediaan Direktori Sementara
    temp_job_dir = cfg.TEMP_DIR / f"job_{imdb_id.replace(':', '_')}_{int(time.time())}"
    temp_job_dir.mkdir(parents=True, exist_ok=True)

    try:
        with launch_camoufox_session() as (browser, context, page):
            active_player_page, media_info = execute_step01(
                page=page,
                target_url=cfg.TARGET_WEB_URL,
                search_query=clean_title
            )

            if not media_info.get("stream_url"):
                raise RuntimeError("Tiada pautan media sah berjaya dipintas.")

            safe_title = f"{imdb_id.replace(':', '_')}_{int(time.time())}"
            downloaded_video = download_stream_with_ytdlp(media_info, temp_job_dir, safe_title)
            if not downloaded_video:
                raise RuntimeError("Proses muat turun fail video melalui yt-dlp gagal.")

            meta_info = {
                "imdb_id": imdb_id,
                "base_imdb": imdb_id.split(":")[0],
                "title": clean_title,
                "is_series": ":" in imdb_id,
                "season": int(imdb_id.split(":")[1]) if ":" in imdb_id and len(imdb_id.split(":")) > 1 else 1,
                "episode": int(imdb_id.split(":")[2]) if ":" in imdb_id and len(imdb_id.split(":")) > 2 else 1,
            }

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
            "title": clean_title,
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
    parser.add_argument("--imdb", required=True, help="IMDb identifier")
    parser.add_argument("--title", required=True, help="Tajuk video bersama tahun (cth: Kaabil 2017)")
    parser.add_argument("--force", action="store_true", help="Paksa muat turun semula")
    args = parser.parse_args()

    success = execute_scrape_pipeline(args.imdb, args.title, args.force)
    sys.exit(0 if success else 1)