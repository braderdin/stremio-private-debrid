import time
from typing import Dict
from playwright.sync_api import Page
from rich.console import Console

from X01_step01a_nav import human_delay

console = Console()


def configure_resolution_and_sniff(player_page: Page, wait_timeout: int = 45) -> Dict[str, str]:
    console.print("\n[bold yellow][*] Mengkonfigurasi pemain video popup & menjejak pautan media...[/bold yellow]")

    try:
        user_agent_str = player_page.evaluate("() => navigator.userAgent")
    except Exception:
        user_agent_str = None

    popup_main_url = player_page.url.split("?")[0].rstrip("/")

    intercepted = {
        "stream_url": "",
        "referer": player_page.url,
        "user_agent": user_agent_str or "",
        "quality": "unknown"
    }

    def on_ad_tab_opened(new_tab: Page):
        try:
            time.sleep(0.4)
            if new_tab != player_page:
                console.print(f"[yellow][!] Menutup tab iklan bertindih: {new_tab.url[:55]}...[/yellow]")
                new_tab.close()
                player_page.bring_to_front()
        except Exception:
            pass

    player_page.context.on("page", on_ad_tab_opened)

    def is_valid_media(url: str, content_type: str = "") -> bool:
        u_lower = url.lower()
        base_clean = u_lower.split("?")[0]
        ct = content_type.lower()

        # 1. MUTLAK: Haramkan URL laman web popup itu sendiri
        if base_clean == popup_main_url.lower():
            return False
        if "abyssplayer.com" in u_lower and not any(ext in base_clean for ext in [".m3u8", ".mp4", ".mpd"]):
            return False

        # 2. Tolak penjejak iklan dan fail statik laman web
        if any(bad in u_lower for bad in [
            "youtube.com", "googlevideo.com", "ytimg.com", "doubleclick",
            "analytics", "google-analytics", "facebook.com", "favicon",
            "adsterra", "monetag", "histats", "popads", "clarity.ms",
            "googleads", "pagead"
        ]):
            return False

        if any(base_clean.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".json", ".html", ".htm"]):
            return False
        if any(bad_ct in ct for bad_ct in ["text/html", "text/css", "application/javascript", "image/"]):
            return False

        # 3. Pautan HLS/DASH/MP4 tulen
        if any(base_clean.endswith(ext) for ext in [".m3u8", ".mp4", ".mpd", ".mkv", ".webm"]):
            return True

        if any(kw in u_lower for kw in [".m3u8", "master.m3u8", "playlist.m3u8", "manifest.mpd"]):
            return True

        # 4. Sahkan melalui Content-Type video
        if any(valid_ct in ct for valid_ct in [
            "application/vnd.apple.mpegurl",
            "application/x-mpegurl",
            "video/mp4",
            "video/webm",
            "application/dash+xml"
        ]):
            return True

        return False

    def process_candidate(url: str, headers: dict, ct: str = ""):
        if not is_valid_media(url, ct):
            return

        # Abaikan serpihan segmen mikro (.ts / .m4s)
        base_url = url.split("?")[0].lower()
        if base_url.endswith(".ts") or base_url.endswith(".m4s"):
            return

        quality = "unknown"
        if "1080" in url.lower():
            quality = "1080p"
        elif "720" in url.lower():
            quality = "720p"
        elif "480" in url.lower():
            quality = "480p"

        # Kekalkan resolusi terbaik jika 1080p sudah ditemui
        if intercepted["quality"] == "1080p" and quality != "1080p":
            return

        intercepted["stream_url"] = url
        intercepted["quality"] = quality
        intercepted["referer"] = headers.get("referer", player_page.url)

        q_label = f" ({quality})" if quality != "unknown" else ""
        console.print(f"[bold green][🎯 STRIM SAH DIPINTAS!{q_label}][/bold green]")
        console.print(f"    └─ URL: [underline]{url[:110]}...[/underline]")

    def on_req(request):
        try:
            if request.resource_type in ["media", "fetch", "xhr"]:
                process_candidate(request.url, request.headers)
        except Exception:
            pass

    def on_res(response):
        try:
            ct = response.headers.get("content-type", "")
            process_candidate(response.url, response.request.headers, ct)
        except Exception:
            pass

    player_page.on("request", on_req)
    player_page.on("response", on_res)

    console.print("[cyan][*] Memastikan video dimainkan (membersihkan lapisan iklan)...[/cyan]")

    play_selectors = [
        'button[aria-label*="play" i]',
        '.vjs-big-play-button',
        '.jw-display-icon-display',
        'button.play-button',
        '.plyr__control--overlaid',
        'div[class*="play" i][role="button"]',
        'button.play',
        '#play',
        'video'
    ]

    # Cetus Play pada laman popup dan semua frame dalaman
    for attempt in range(1, 6):
        if intercepted["stream_url"]:
            break

        console.print(f"[cyan]    └─ Menekan Play pada popup (Percubaan #{attempt})...[/cyan]")
        for scope in [player_page] + player_page.frames:
            for p_sel in play_selectors:
                try:
                    btn = scope.locator(p_sel).first
                    if btn.is_visible(timeout=300):
                        btn.click(force=True)
                        time.sleep(0.5)
                        break
                except Exception:
                    continue

        try:
            player_page.mouse.click(640, 360)
        except Exception:
            pass

        human_delay(1.5, 2.5)
        player_page.bring_to_front()

    console.print("[cyan][*] Menunggu penjejakan fail strim sebenar (.m3u8/.mp4)...[/cyan]")
    start_wait = time.time()
    while time.time() - start_wait < wait_timeout:
        if intercepted["stream_url"]:
            console.print("[bold green][✓] Pautan media sebenar berjaya dikesan![/bold green]")
            break
        player_page.wait_for_timeout(500)

    try:
        player_page.context.remove_listener("page", on_ad_tab_opened)
        player_page.remove_listener("request", on_req)
        player_page.remove_listener("response", on_res)
    except Exception:
        pass

    return intercepted