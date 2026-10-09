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

# Muat turun ffmpeg binari jika ada
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
from X01_meta_resolver import MetaResolver
from X02_db_manager import db_manager
from X03_browser_stealth import BrowserStealthManager, human_delay, smooth_scroll
from X04_stream_capture import stream_capture
from X05_sync_bridge import sync_bridge

console = Console()


def download_stream_with_ytdlp(stream_info: Dict[str, Any], output_dir: Path, target_title: str) -> Optional[Path]:
    """Memuat turun strim video (.m3u8/.mp4) menggunakan yt-dlp secara stabil."""
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
    if raw_title and raw_title not in meta_info["search_queries"]:
        meta_info["search_queries"].insert(0, raw_title.strip())

    imdb_id = meta_info["imdb_id"]
    display_title = meta_info["title"] or raw_title or imdb_id

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
            # Melayari dan mengisi carian
            search_ok = BrowserStealthManager.navigate_and_search(
                page=page,
                site_url=cfg.TARGET_WEB_URL,
                query_list=meta_info["search_queries"]
            )
            if not search_ok:
                raise RuntimeError("Gagal memulakan navigasi carian pada portal sasaran.")

            # Cari dan buka pautan kad filem/drama
            human_delay(2.0, 3.5)
            query_parts = [p.lower() for p in meta_info["search_queries"][0].split() if len(p) > 2]
            target_link = None

            for a_el in page.locator("a[href]").all():
                try:
                    if not a_el.is_visible():
                        continue
                    href = (a_el.get_attribute("href") or "").lower()
                    text = (a_el.inner_text() or "").lower()
                    if any(bad in href for bad in ["/genre/", "/year/", "/country/", "/tag/"]):
                        continue
                    if any(q in href or q in text for q in query_parts):
                        target_link = a_el
                        break
                except Exception:
                    continue

            if target_link:
                console.print("[green][✓] Membuka halaman video sasaran...[/green]")
                target_link.scroll_into_view_if_needed()
                human_delay(0.6, 1.2)
                target_link.click()
                human_delay(3.5, 5.0)

            # 4. Aktifkan Pemain & Pintas Strim
            player_page = stream_capture.activate_player_and_popup(page)
            media_info = stream_capture.capture_stream(player_page)

            if media_info["status"] != "success" or not media_info["stream_url"]:
                raise RuntimeError("Tiada pautan manifes video (.m3u8/.mp4) sah berjaya dipintas.")

            # 5. Muat Turun Melalui yt-dlp
            safe_title = f"{imdb_id.replace(':', '_')}_{int(time.time())}"
            downloaded_video = download_stream_with_ytdlp(media_info, temp_job_dir, safe_title)
            if not downloaded_video:
                raise RuntimeError("Proses muat turun fail video melalui yt-dlp gagal.")

            # 6. Pemindahan ke B2 & Pendaftaran Metadata Redis / SQLite
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
    parser.add_argument("--imdb", required=True, help="IMDb identifier (cth: tt0097576 atau tt0944947:1:1)")
    parser.add_argument("--title", default="", help="Tajuk video daripada Stremio")
    parser.add_argument("--force", action="store_true", help="Paksa muat turun semula")
    args = parser.parse_args()

    success = execute_scrape_pipeline(args.imdb, args.title, args.force)
    sys.exit(0 if success else 1)