from typing import Tuple, Dict
from playwright.sync_api import Page
from rich.console import Console

# Eksport semula human_delay untuk kegunaan langsung X01_run_all_pipeline.py
from X01_step01a_nav import (
    human_delay,
    find_search_input,
    smart_human_type,
    select_dropdown_match,
    ensure_movie_watch_page,
    activate_hydrax_and_open_popup
)
from X01_step01b_sniff import configure_resolution_and_sniff

console = Console()


def execute_step01(page: Page, target_url: str, search_query: str) -> Tuple[Page, Dict[str, str]]:
    console.print("\n[bold blue]=== [LANGKAH 1: NAVIGASI, HYDRAX & POPUP RESOLUSI] ===[/bold blue]")
    console.print(f"[*] Melayari laman sasaran: [underline]{target_url}[/underline]")
    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
    human_delay(2.5, 4.0)

    # 1. Kotak carian dan penaipan
    input_box = find_search_input(page)
    if not input_box:
        raise RuntimeError("Gagal mengesan sebarang medan carian.")

    console.print(f"[*] Mengisi kata kunci carian: [bold cyan]'{search_query}'[/bold cyan]")
    smart_human_type(input_box, page, search_query)

    # 2. Pantau dropdown dan buat pemilihan
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

    # 3. Sahkan berada di laman tontonan video sebenar
    active_page = ensure_movie_watch_page(active_page, search_query)

    # 4. Pilih HYDRAX, tunggu 5 saat, dan buka popup
    player_page = activate_hydrax_and_open_popup(active_page)

    # 5. Ad-bypass, pilihan resolusi kualiti, dan pintasan strim rangkaian
    media_info = configure_resolution_and_sniff(player_page)

    if not media_info["stream_url"]:
        console.print("[red][✕] Amaran: Tiada pautan strim media (.m3u8/.mp4) dikesan daripada trafik rangkaian.[/red]")

    return player_page, media_info