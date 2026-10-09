#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PYTHON SCRAPE ENGINE - KAWALAN PEMAIN, RESOLUSI & PINTASAN MEDIA
# LOKASI: /home/braderdin/stremio-private-debrid/python_scrape/X04_stream_capture.py
# CIRI:
# 1. Pengesanan Bar Pelayan Video (Hydrax, Popup Launcher).
# 2. Pencetus Main Semula (Play Trigger) merentasi Halaman & Bingkai Iframe.
# 3. Interaksi Menu Gear Kualiti Video (1080p -> 720p Fallback).
# 4. Penapis Trafik Media Pintar (HLS .m3u8, MP4, DASH) & Header Referer.
# ==============================================================================

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

from playwright.sync_api import Page, Locator, Error
from rich.console import Console

# Penyelarasan modul konfigurasi dan utiliti tempatan
SCRAPE_DIR = Path(__file__).resolve().parent
if str(SCRAPE_DIR) not in sys.path:
    sys.path.insert(0, str(SCRAPE_DIR))

import X00_scrape_config as cfg
from X03_browser_stealth import human_delay, smooth_scroll

console = Console()


class StreamCaptureManager:
    def __init__(self, wait_timeout: int = 35):
        self.wait_timeout = wait_timeout

    def activate_player_and_popup(self, page: Page) -> Page:
        """
        Menatal halaman ke bawah, memilih pelayan HYDRAX,
        menunggu persediaan DOM, dan membuka tetingkap popup pemain.
        """
        console.print("[cyan][*] Mencari bar pelayan video di portal...[/cyan]")
        smooth_scroll(page, pixels=350)
        human_delay(1.5, 2.5)

        hydrax_selectors = [
            "text=/^\\s*HYDRAX\\s*$/i",
            "xpath=//*[normalize-space(text())='HYDRAX']",
            "a:has-text('HYDRAX')",
            "span:has-text('HYDRAX')",
            "li:has-text('HYDRAX')",
            "button:has-text('HYDRAX')"
        ]

        hydrax_btn = None
        for scope in [page] + page.frames:
            for h_sel in hydrax_selectors:
                try:
                    loc = scope.locator(h_sel).first
                    if loc.is_visible(timeout=300):
                        hydrax_btn = loc
                        break
                except Exception:
                    continue
            if hydrax_btn:
                break

        if hydrax_btn:
            console.print("[bold green][✓] Butang pelayan 'HYDRAX' ditemui! Mengklik...[/bold green]")
            hydrax_btn.scroll_into_view_if_needed()
            human_delay(0.6, 1.2)
            hydrax_btn.click(force=True)
            console.print("[dim]⏳ Menunggu 5 saat untuk skrip pemain Hydrax bersedia...[/dim]")
            time.sleep(5.0)
        else:
            console.print("[yellow][!] Butang 'HYDRAX' tidak ditemui secara langsung. Mengimbas butang Popup...[/yellow]")

        popup_selectors = [
            "text=/^\\s*Popup\\s*$/i",
            "xpath=//*[normalize-space(text())='Popup']",
            "a:has-text('Popup')",
            "button:has-text('Popup')",
            "span:has-text('Popup')"
        ]

        popup_btn = None
        for scope in [page] + page.frames:
            for p_sel in popup_selectors:
                try:
                    loc = scope.locator(p_sel).first
                    if loc.is_visible(timeout=300):
                        popup_btn = loc
                        break
                except Exception:
                    continue
            if popup_btn:
                break

        if not popup_btn:
            console.print("[yellow][!] Tiada butang Popup ditemui; kekal pada halaman semasa.[/yellow]")
            return page

        console.print("[bold green][✓] Membuka popup pemain video...[/bold green]")
        popup_btn.scroll_into_view_if_needed()
        human_delay(0.5, 1.0)

        target_popup_page = None
        try:
            with page.expect_popup(timeout=10000) as popup_info:
                popup_btn.click(force=True)
            target_popup_page = popup_info.value
        except Exception:
            current_pages = page.context.pages
            if len(current_pages) > 1:
                target_popup_page = current_pages[-1]

        if target_popup_page:
            try:
                target_popup_page.wait_for_load_state("domcontentloaded", timeout=15000)
                target_popup_page.bring_to_front()
            except Exception:
                pass
            human_delay(2.0, 3.5)
            return target_popup_page

        return page

    def trigger_video_play(self, player_page: Page):
        """Mencetuskan butang Play merentasi semua rangka elemen pemain."""
        play_selectors = [
            'button[aria-label*="play" i]',
            '.vjs-big-play-button',
            '.jw-display-icon-display',
            'button.play-button',
            '.plyr__control--overlaid',
            'button.play',
            '#play',
            'video'
        ]

        for attempt in range(1, 4):
            for scope in [player_page] + player_page.frames:
                for p_sel in play_selectors:
                    try:
                        btn = scope.locator(p_sel).first
                        if btn.is_visible(timeout=250):
                            btn.click(force=True)
                            time.sleep(0.4)
                            break
                    except Exception:
                        continue

            try:
                if not player_page.is_closed():
                    player_page.mouse.click(640, 360)
            except Exception:
                pass

            time.sleep(1.0)

    def select_resolution_quality(self, player_page: Page, target_quality: str = "1080p") -> str:
        """Mengakses menu gear pemain untuk memilih resolusi 1080p atau 720p."""
        gear_selectors = [
            'button[aria-label*="setting" i]',
            'button[title*="setting" i]',
            '.jw-icon-settings',
            '.vjs-settings-button',
            'xpath=//*[normalize-space(text())="⚙"]'
        ]

        gear_btn = None
        target_scope = player_page

        for scope in [player_page] + player_page.frames:
            for g_sel in gear_selectors:
                try:
                    loc = scope.locator(g_sel).first
                    if loc.is_visible(timeout=300):
                        gear_btn = loc
                        target_scope = scope
                        break
                except Exception:
                    continue
            if gear_btn:
                break

        chosen_quality = "unknown"
        if gear_btn and not player_page.is_closed():
            try:
                gear_btn.click(force=True)
                human_delay(0.6, 1.2)

                btn_1080 = target_scope.locator("xpath=//*[contains(normalize-space(text()), '1080')]").first
                if btn_1080.is_visible(timeout=350):
                    btn_1080.click(force=True)
                    console.print("[bold green][✓] Resolusi '1080p' berjaya dipilih![/bold green]")
                    chosen_quality = "1080p"
                else:
                    btn_720 = target_scope.locator("xpath=//*[contains(normalize-space(text()), '720')]").first
                    if btn_720.is_visible(timeout=350):
                        btn_720.click(force=True)
                        console.print("[yellow][i] 1080p tiada; beralih ke '720p'.[/yellow]")
                        chosen_quality = "720p"
            except Exception:
                pass

        return chosen_quality

    def capture_stream(self, player_page: Page) -> Dict[str, Any]:
        """Menjejak trafik rangkaian untuk mengekstrak pautan media sebenar."""
        popup_base = player_page.url.split("?")[0].rstrip("/").lower()
        intercepted = {
            "status": "failed",
            "stream_url": "",
            "referer": player_page.url,
            "user_agent": cfg.USER_AGENT_DESKTOP,
            "quality": "unknown",
            "error": None
        }

        def is_valid_media(url: str, content_type: str = "") -> bool:
            u_lower = url.lower()
            clean_url = u_lower.split("?")[0]
            ct = content_type.lower()

            if clean_url == popup_base:
                return False
            if "abyssplayer.com" in u_lower and not any(ext in clean_url for ext in [".m3u8", ".mp4", ".mpd"]):
                return False

            if any(bad in u_lower for bad in [
                "youtube.com", "googlevideo.com", "doubleclick", "analytics",
                "adsterra", "monetag", "histats", "popads", "clarity.ms"
            ]):
                return False

            if any(clean_url.endswith(ext) for ext in [".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".json", ".html"]):
                return False
            if any(bad_ct in ct for bad_ct in ["text/html", "text/css", "application/javascript"]):
                return False

            # Format manifes standard
            if any(clean_url.endswith(ext) for ext in [".m3u8", ".mp4", ".mpd", ".mkv"]):
                return True
            if any(kw in u_lower for kw in [".m3u8", "master.m3u8", "playlist.m3u8"]):
                return True
            if any(valid_ct in ct for valid_ct in [
                "application/vnd.apple.mpegurl",
                "application/x-mpegurl",
                "video/mp4",
                "application/dash+xml"
            ]):
                return True

            return False

        def process_traffic(url: str, headers: dict, ct: str = ""):
            if not is_valid_media(url, ct):
                return

            base = url.split("?")[0].lower()
            if base.endswith(".ts") or base.endswith(".m4s"):
                return

            detected_q = "unknown"
            if "1080" in url.lower():
                detected_q = "1080p"
            elif "720" in url.lower():
                detected_q = "720p"
            elif "master.m3u8" in url.lower() or "playlist.m3u8" in url.lower():
                detected_q = "adaptive_hls"

            if intercepted["quality"] == "1080p" and detected_q != "1080p":
                return

            intercepted["status"] = "success"
            intercepted["stream_url"] = url
            intercepted["quality"] = detected_q
            intercepted["referer"] = headers.get("referer", player_page.url)

        def on_req(req):
            try:
                if req.resource_type in ["media", "fetch", "xhr"]:
                    process_traffic(req.url, req.headers)
            except Exception:
                pass

        def on_res(res):
            try:
                process_traffic(res.url, res.request.headers, res.headers.get("content-type", ""))
            except Exception:
                pass

        player_page.on("request", on_req)
        player_page.on("response", on_res)

        try:
            self.trigger_video_play(player_page)
            chosen_q = self.select_resolution_quality(player_page)
            if chosen_q != "unknown":
                intercepted["quality"] = chosen_q

            console.print(f"[cyan][*] Menunggu penjejakan fail media (sehingga {self.wait_timeout}s)...[/cyan]")
            start_t = time.time()
            while time.time() - start_t < self.wait_timeout:
                if intercepted["status"] == "success" and intercepted["stream_url"]:
                    console.print(f"[bold green]🎯 Strim berjaya dikesan: {intercepted['stream_url'][:80]}...[/bold green]")
                    break
                if player_page.is_closed():
                    break
                time.sleep(0.5)

        finally:
            try:
                player_page.remove_listener("request", on_req)
                player_page.remove_listener("response", on_res)
            except Exception:
                pass

        return intercepted


# Singleton Capture Manager
stream_capture = StreamCaptureManager()