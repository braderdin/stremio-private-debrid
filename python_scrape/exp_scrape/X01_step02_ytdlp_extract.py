import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse
import yt_dlp
from rich.console import Console

# Muat turun / inject ffmpeg & ffprobe binary ke PATH jika belum wujud
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

console = Console()

def download_media_1080p(media_info: Dict[str, str], download_folder: Path, custom_user_agent: Optional[str] = None) -> Dict[str, Any]:
    stream_url = media_info.get("stream_url", "").strip()
    referer = media_info.get("referer", "").strip()
    ua = custom_user_agent or media_info.get("user_agent", "").strip()

    result = {
        "status": "failed",
        "url": stream_url,
        "title": None,
        "file_path": None,
        "resolution": None,
        "error": None
    }

    console.print(f"\n[bold magenta]=== [LANGKAH 2: EKSTRAK & MUAT TURUN YT-DLP] ===[/bold magenta]")

    if not stream_url:
        err_msg = "Pautan strim kosong! Sila pastikan media (.m3u8/.mp4) berjaya dipintas dalam Langkah 1."
        console.print(f"[bold red][✕] {err_msg}[/bold red]")
        result["error"] = err_msg
        return result

    console.print(f"[*] Sasaran Pautan Strim: [underline]{stream_url[:90]}...[/underline]")
    
    download_folder.mkdir(parents=True, exist_ok=True)
    out_template = str(download_folder / "%(title).80s [%(id)s].%(ext)s")

    # Susunan kualiti: Utamakan 1080p, seterusnya kualiti terbaik yang wujud
    format_selection = (
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/bestvideo+bestaudio/best"
    )

    # Bina pengepala HTTP (Headers) lengkap untuk elak ralat 403 Forbidden
    headers = {
        "User-Agent": ua or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }
    if referer:
        headers["Referer"] = referer
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

    ydl_opts = {
        "format": format_selection,
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,  # Laju untuk serpihan HLS (.m3u8)
        "http_headers": headers,
        "nocheckcertificate": True,
        "windowsfilenames": True,
    }

    try:
        console.print("[cyan][*] Memproses strim video melalui yt-dlp...[/cyan]")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(stream_url, download=True)
            if info:
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]

                title = info.get("title", "Media_Video")
                height = info.get("height", "N/A")

                # Kenal pasti laluan fail sebenar hasil gabungan ffmpeg
                file_downloaded = None
                if "requested_downloads" in info and info["requested_downloads"]:
                    file_downloaded = (
                        info["requested_downloads"][0].get("filepath")
                        or info["requested_downloads"][0].get("_filename")
                    )

                if not file_downloaded or not os.path.exists(file_downloaded):
                    file_downloaded = ydl.prepare_filename(info)
                    if not os.path.exists(file_downloaded):
                        base, _ = os.path.splitext(file_downloaded)
                        for ext in [".mp4", ".mkv", ".webm"]:
                            if os.path.exists(f"{base}{ext}"):
                                file_downloaded = f"{base}{ext}"
                                break

                result["status"] = "success"
                result["title"] = title
                result["resolution"] = f"{height}p" if height != "N/A" else "Kualiti Terbaik"
                result["file_path"] = file_downloaded

                console.print(f"[bold green][✓] Selesai muat turun![/bold green]")
                console.print(f"    ├─ Tajuk     : {title}")
                console.print(f"    ├─ Kualiti   : {result['resolution']}")
                console.print(f"    └─ Simpan di : {file_downloaded}")

    except Exception as err:
        err_msg = str(err)
        result["error"] = err_msg
        console.print(f"[bold red][✕] Ralat yt-dlp: {err_msg}[/bold red]")

    return result