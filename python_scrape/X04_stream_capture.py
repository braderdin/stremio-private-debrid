#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - KAWALAN PEMAIN, RESOLUSI & PINTASAN MEDIA
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X04_stream_capture.py
# ==============================================================================

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.sync_api import Page
from rich.console import Console

SCRAPE_DIR = Path(__file__).resolve().parent
if str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

import X00_scrape_config as cfg
from X03_browser_stealth import human_delay, smooth_scroll_down, smooth_scroll

console = Console()


class StreamCaptureManager:
    def __init__(self, wait_timeout: int = 40):
        self.wait_timeout = wait_timeout

    def activate_player_and_popup(self, page: Page) -> Page:
        console.print("\n[bold yellow][*] Mengesan bar pelayan di bawah video...[/bold yellow]")
        smooth_scroll_down(page, pixels=350)
        human_delay(1.5, 2.5)

        hydrax_locators = [
            page.locator("text=/^\\s*HYDRAX\\s*$/i"),
            page.get_by_text("HYDRAX", exact=True),
            page.locator("xpath=//*[normalize-space(text())='HYDRAX']"),
            page.locator("a:has-text('HYDRAX')"),
            page.locator("span:has-text('HYDRAX')"),
            page.locator("li:has-text('HYDRAX')")
        ]

        hydrax_btn = None
        start_find = time.time()
        while time.time() - start_find < 15:
            for loc in hydrax_locators:
                try:
                    first_item = loc.first
                    if first_item.is_visible(timeout=400):
                        hydrax_btn = first_item
                        break
                except Exception:
                    continue
            if hydrax_btn:
                break
            page.wait_for_timeout(500)

        if not hydrax_btn:
            for frame in page.frames:
                try:
                    loc = frame.locator("text=/HYDRAX/i").first
                    if loc.is_visible(timeout=400):
                        hydrax_btn = loc
                        break
                except Exception:
                    continue

        if hydrax_btn:
            console.print("[bold green][✓] Butang 'HYDRAX' ditemui! Melakukan klik santai...[/bold green]")
            hydrax_btn.scroll_into_view_if_needed()
            human_delay(1.0, 1.8)
            hydrax_btn.click()
        else:
            console.print("[red][✕] Butang 'HYDRAX' tidak ditemui pada bar pelayan![/red]")

        console.print("[cyan]⏳ Menunggu tepat 5 saat selepas memilih HYDRAX...[/cyan]")
        for sec in range(5, 0, -1):
            console.print(f"   [dim]{sec} saat lagi...[/dim]", end="\r")
            time.sleep(1.0)
        print()

        popup_locators = [
            page.locator("text=/^\\s*Popup\\s*$/i"),
            page.get_by_text("Popup", exact=False),
            page.locator("xpath=//*[normalize-space(text())='Popup']"),
            page.locator("a:has-text('Popup')"),
            page.locator("button:has-text('Popup')"),
            page.locator("span:has-text('Popup')")
        ]

        popup_btn = None
        for p_loc in popup_locators:
            try:
                first_item = p_loc.first
                if first_item.is_visible(timeout=500):
                    popup_btn = first_item
                    break
            except Exception:
                continue

        if not popup_btn:
            console.print("[yellow][!] Butang 'Popup' tidak ditemui; kekal pada laman semasa.[/yellow]")
            return page

        console.print("[bold green][✓] Butang 'Popup' ditemui! Mengklik untuk membuka pemain...[/bold green]")
        popup_btn.scroll_into_view_if_needed()
        human_delay(0.8, 1.5)

        target_popup_page = None
        try:
            with page.expect_popup(timeout=10000) as popup_info:
                popup_btn.click()
            target_popup_page = popup_info.value
        except Exception:
            current_pages = page.context.pages
            if len(current_pages) > 1:
                target_popup_page = current_pages[-1]

        if target_popup_page:
            console.print(f"[bold green][✓] Laman popup pemain dibuka: {target_popup_page.url[:70]}[/bold green]")
            try:
                target_popup_page.wait_for_load_state("domcontentloaded", timeout=15000)
                target_popup_page.bring_to_front()
            except Exception:
                pass
            human_delay(2.5, 4.0)
            return target_popup_page

        return page

    def capture_stream(self, player_page: Page) -> Dict[str, Any]:
        console.print("\n[bold yellow][*] Mengkonfigurasi resolusi pada pemain video popup...[/bold yellow]")

        try:
            user_agent_str = player_page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent_str = None

        intercepted = {
            "status": "failed",
            "stream_url": "",
            "referer": player_page.url,
            "user_agent": user_agent_str or cfg.USER_AGENT_DESKTOP,
            "quality": "unknown",
            "error": None
        }

        def on_ad_tab_opened(new_tab: Page):
            try:
                time.sleep(0.4)
                if new_tab != player_page:
                    console.print(f"[yellow][!] Mengesan dan menutup tab iklan bertindih: {new_tab.url[:55]}...[/yellow]")
                    new_tab.close()
                    player_page.bring_to_front()
            except Exception:
                pass

        player_page.context.on("page", on_ad_tab_opened)

        def is_media_target(url: str, content_type: str = "") -> bool:
            u = url.lower().split("?")[0]
            if any(bad in url.lower() for bad in [
                "youtube.com", "googlevideo.com", "ytimg.com", "doubleclick",
                "analytics", "google-analytics", "facebook.com", "favicon",
                "adsterra", "monetag", "histats", "popads", "clarity.ms"
            ]):
                return False

            if any(u.endswith(ext) for ext in [".m3u8", ".mp4", ".mpd", ".mkv", ".webm", ".m4s", ".ts"]):
                return True

            if any(kw in url.lower() for kw in [".m3u8", "/hls/", "master.m3u8", "playlist.m3u8", "manifest.mpd", "slug=", "abysscdn"]):
                if not any(u.endswith(no_ext) for no_ext in [".js", ".css", ".png", ".jpg", ".svg", ".json", ".html"]):
                    return True

            ct = content_type.lower()
            if any(t in ct for t in ["application/vnd.apple.mpegurl", "application/x-mpegurl", "video/mp4", "video/webm", "application/dash+xml"]):
                return True

            return False

        def process_media_candidate(url: str, headers: dict, ct: str = ""):
            if not is_media_target(url, ct):
                return

            quality = "unknown"
            if "1080" in url:
                quality = "1080p"
            elif "720" in url:
                quality = "720p"
            elif "480" in url:
                quality = "480p"

            if intercepted["quality"] == "1080p" and quality != "1080p":
                return

            intercepted["status"] = "success"
            intercepted["stream_url"] = url
            intercepted["quality"] = quality
            intercepted["referer"] = headers.get("referer", player_page.url)

            q_label = f" {quality}" if quality != "unknown" else ""
            console.print(f"[bold green][🎯 STRIM{q_label} DIPINTAS!] {url[:85]}...[/bold green]")

        def on_req(request):
            try:
                if request.resource_type == "media":
                    process_media_candidate(request.url, request.headers)
                elif is_media_target(request.url):
                    process_media_candidate(request.url, request.headers)
            except Exception:
                pass

        def on_res(response):
            try:
                ct = response.headers.get("content-type", "")
                if is_media_target(response.url, ct):
                    process_media_candidate(response.url, response.request.headers, ct)
            except Exception:
                pass

        player_page.on("request", on_req)
        player_page.on("response", on_res)

        console.print("[cyan][*] Memastikan video dimainkan (membersihkan lapisan iklan)...[/cyan]")

        def check_video_playing(target_page: Page) -> bool:
            for fr in [target_page] + target_page.frames:
                try:
                    is_active = fr.evaluate("() => { const v = document.querySelector('video'); return v && (!v.paused || v.currentTime > 0); }")
                    if is_active:
                        return True
                except Exception:
                    continue
            return False

        play_selectors = [
            'button[aria-label*="play" i]',
            '.vjs-big-play-button',
            '.jw-display-icon-display',
            'button.play-button',
            '.plyr__control--overlaid',
            'div[class*="play" i][role="button"]',
            'button.play'
        ]

        for attempt in range(1, 5):
            if check_video_playing(player_page) or intercepted["stream_url"]:
                console.print("[bold green][✓] Pemain video disahkan aktif dan berjalan.[/bold green]")
                break

            console.print(f"[cyan]    └─ Percubaan cetusan Play #{attempt}...[/cyan]")
            clicked = False

            for scope in [player_page] + player_page.frames:
                for p_sel in play_selectors:
                    try:
                        btn = scope.locator(p_sel).first
                        if btn.is_visible(timeout=400):
                            btn.click(force=True)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    break

            if not clicked:
                try:
                    player_page.mouse.click(640, 360)
                except Exception:
                    pass

            human_delay(1.5, 2.5)
            player_page.bring_to_front()

        # Semak src langsung pada elemen video
        for fr in [player_page] + player_page.frames:
            try:
                direct_src = fr.evaluate("() => { const v = document.querySelector('video'); if (v && v.src && !v.src.startsWith('blob:')) return v.src; if (v && v.currentSrc && !v.currentSrc.startsWith('blob:')) return v.currentSrc; const s = document.querySelector('video source'); if (s && s.src && !s.src.startsWith('blob:')) return s.src; return null; }")
                if direct_src and is_media_target(direct_src):
                    process_media_candidate(direct_src, {"referer": player_page.url})
                    break
            except Exception:
                pass

        time.sleep(2.5)
        if intercepted["quality"] != "1080p":
            console.print("[yellow][!] Memeriksa menu Gear untuk 1080p...[/yellow]")
            gear_selectors = [
                'button[aria-label*="setting" i]',
                'button[title*="setting" i]',
                'button[aria-label*="kualiti" i]',
                'button[aria-label*="quality" i]',
                '.jw-icon-settings',
                '.vjs-icon-cog',
                '.vjs-settings-button',
                'xpath=//*[normalize-space(text())="⚙"]'
            ]

            gear_found = False
            active_scope = player_page
            for scope in [player_page] + player_page.frames:
                for g_sel in gear_selectors:
                    try:
                        g_loc = scope.locator(g_sel).first
                        if g_loc.is_visible(timeout=400):
                            g_loc.click(force=True)
                            gear_found = True
                            active_scope = scope
                            human_delay(0.8, 1.5)
                            break
                    except Exception:
                        continue
                if gear_found:
                    break

            if gear_found:
                for res in ["1080p", "1080", "720p", "720"]:
                    try:
                        btn_res = active_scope.locator(f"xpath=//*[contains(normalize-space(text()), '{res}')]").first
                        if btn_res.is_visible(timeout=400):
                            console.print(f"[bold green][✓] Memilih resolusi '{res}' di menu gear.[/bold green]")
                            btn_res.click(force=True)
                            human_delay(1.5, 2.5)
                            break
                    except Exception:
                        continue

        console.print(f"[cyan][*] Menunggu penjejakan pautan strim media (sehingga {self.wait_timeout}s)...[/cyan]")
        start_sniff = time.time()
        while time.time() - start_sniff < self.wait_timeout:
            if intercepted["status"] == "success" and intercepted["stream_url"]:
                break
            player_page.wait_for_timeout(500)

        try:
            player_page.context.remove_listener("page", on_ad_tab_opened)
            player_page.remove_listener("request", on_req)
            player_page.remove_listener("response", on_res)
        except Exception:
            pass

        return intercepted


# Singleton Capture Manager
stream_capture = StreamCaptureManager()