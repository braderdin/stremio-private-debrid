#!/usr/bin/env python3
"""
Module: 03_test_stream_fallback.py
Path: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/03_test_stream_fallback.py

Penerangan:
Skrip ujian Proof-of-Concept (PoC) bagi menguji senibina agregasi strim pintar
(Smart Fallback Streaming Resolver) dengan pengendalian ralat automatik dan
paparan jadual terminal terperinci menggunakan pustaka rich.
"""

import time
import random
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

@dataclass
class StreamCandidate:
    provider: str
    resolution: str  # 4K, 1080p, 720p, 480p
    quality_score: int
    size_bytes: int
    url: str
    latency_ms: float
    status: str  # OK, Fallback, Backup Ready, Error
    codec: str
    container: str

    @property
    def size_str(self) -> str:
        if self.size_bytes >= 1024 ** 3:
            return f"{self.size_bytes / (1024 ** 3):.2f} GB"
        elif self.size_bytes >= 1024 ** 2:
            return f"{self.size_bytes / (1024 ** 2):.1f} MB"
        return f"{self.size_bytes / 1024:.1f} KB"

class BaseStreamProvider:
    def __init__(self, name: str, priority: int, timeout_sec: float = 3.0):
        self.name = name
        self.priority = priority
        self.timeout_sec = timeout_sec

    def fetch_streams(self, imdb_id: str, title: str, year: int) -> List[StreamCandidate]:
        raise NotImplementedError

class ProviderA(BaseStreamProvider):
    """Penyedia Utama (Primary) - Resolusi Tinggi, disimulasi dengan semakan beban"""
    def __init__(self):
        super().__init__(name="Provider-A (SuperStream)", priority=1, timeout_sec=2.5)

    def fetch_streams(self, imdb_id: str, title: str, year: int) -> List[StreamCandidate]:
        start = time.perf_counter()
        time.sleep(0.08)  # Simulasi latency rangkaian
        elapsed_ms = (time.perf_counter() - start) * 1000

        return [
            StreamCandidate(
                provider=self.name,
                resolution="4K",
                quality_score=2160,
                size_bytes=int(6.8 * 1024 ** 3),
                url=f"https://cdn-a.stream.internal/{imdb_id}/4k_main.mp4",
                latency_ms=elapsed_ms,
                status="OK",
                codec="HEVC (H.265)",
                container="MP4"
            ),
            StreamCandidate(
                provider=self.name,
                resolution="1080p",
                quality_score=1080,
                size_bytes=int(3.2 * 1024 ** 3),
                url=f"https://cdn-a.stream.internal/{imdb_id}/1080p_hevc.mp4",
                latency_ms=elapsed_ms,
                status="OK",
                codec="H.264",
                container="MP4"
            ),
            StreamCandidate(
                provider=self.name,
                resolution="720p",
                quality_score=720,
                size_bytes=int(1.4 * 1024 ** 3),
                url=f"https://cdn-a.stream.internal/{imdb_id}/720p_fast.mp4",
                latency_ms=elapsed_ms,
                status="OK",
                codec="H.264",
                container="MP4"
            )
        ]

class ProviderB(BaseStreamProvider):
    """Penyedia Sekunder (Fallback 1) - Pelayan Multi-CDN / HLS Adaptif"""
    def __init__(self):
        super().__init__(name="Provider-B (CloudHLS)", priority=2, timeout_sec=3.0)

    def fetch_streams(self, imdb_id: str, title: str, year: int) -> List[StreamCandidate]:
        start = time.perf_counter()
        time.sleep(0.05)
        elapsed_ms = (time.perf_counter() - start) * 1000

        return [
            StreamCandidate(
                provider=self.name,
                resolution="1080p",
                quality_score=1080,
                size_bytes=int(2.4 * 1024 ** 3),
                url=f"https://edge.hls-b.net/vod/{imdb_id}/master.m3u8",
                latency_ms=elapsed_ms,
                status="Fallback OK",
                codec="H.264",
                container="HLS (.m3u8)"
            ),
            StreamCandidate(
                provider=self.name,
                resolution="720p",
                quality_score=720,
                size_bytes=int(1.1 * 1024 ** 3),
                url=f"https://edge.hls-b.net/vod/{imdb_id}/720p.m3u8",
                latency_ms=elapsed_ms,
                status="Fallback OK",
                codec="H.264",
                container="HLS (.m3u8)"
            ),
            StreamCandidate(
                provider=self.name,
                resolution="480p",
                quality_score=480,
                size_bytes=int(580 * 1024 ** 2),
                url=f"https://edge.hls-b.net/vod/{imdb_id}/480p.m3u8",
                latency_ms=elapsed_ms,
                status="Fallback OK",
                codec="H.264",
                container="HLS (.m3u8)"
            )
        ]

class ProviderC(BaseStreamProvider):
    """Penyedia Tersier (Fallback 2 / Fail-Safe) - Cache Pantas Saiz Ringan"""
    def __init__(self):
        super().__init__(name="Provider-C (FastDirect)", priority=3, timeout_sec=1.5)

    def fetch_streams(self, imdb_id: str, title: str, year: int) -> List[StreamCandidate]:
        start = time.perf_counter()
        time.sleep(0.03)
        elapsed_ms = (time.perf_counter() - start) * 1000

        return [
            StreamCandidate(
                provider=self.name,
                resolution="1080p",
                quality_score=1080,
                size_bytes=int(1.9 * 1024 ** 3),
                url=f"https://direct-cache.c-node.xyz/stream/{imdb_id}.mp4",
                latency_ms=elapsed_ms,
                status="Backup Ready",
                codec="H.264",
                container="MP4"
            ),
            StreamCandidate(
                provider=self.name,
                resolution="720p",
                quality_score=720,
                size_bytes=int(920 * 1024 ** 2),
                url=f"https://direct-cache.c-node.xyz/stream/{imdb_id}_720.mp4",
                latency_ms=elapsed_ms,
                status="Backup Ready",
                codec="H.264",
                container="MP4"
            ),
            StreamCandidate(
                provider=self.name,
                resolution="480p",
                quality_score=480,
                size_bytes=int(420 * 1024 ** 2),
                url=f"https://direct-cache.c-node.xyz/stream/{imdb_id}_480.mp4",
                latency_ms=elapsed_ms,
                status="Backup Ready",
                codec="H.264",
                container="MP4"
            )
        ]

class StreamAggregator:
    def __init__(self, providers: Optional[List[BaseStreamProvider]] = None):
        self.providers = providers or [ProviderA(), ProviderB(), ProviderC()]
        # Susun mengikut keutamaan (priority menaik: 1 -> 2 -> 3)
        self.providers.sort(key=lambda p: p.priority)

    def aggregate_streams(self, imdb_id: str, title: str, year: int, simulate_failure_provider_a: bool = False) -> List[StreamCandidate]:
        all_candidates: List[StreamCandidate] = []
        provider_logs = []

        for provider in self.providers:
            try:
                # Ujian simulasi timeout/ralat pada Provider A
                if simulate_failure_provider_a and isinstance(provider, ProviderA):
                    raise TimeoutError(f"Pelayan {provider.name} tamat masa selepas {provider.timeout_sec}s")

                results = provider.fetch_streams(imdb_id=imdb_id, title=title, year=year)
                if results:
                    all_candidates.extend(results)
                    provider_logs.append((provider.name, "BERJAYA", len(results)))
                else:
                    provider_logs.append((provider.name, "KOSONG", 0))

            except Exception as e:
                provider_logs.append((provider.name, f"GAGAL: {str(e)}", 0))
                # Auto-fallback diteruskan ke provider seterusnya tanpa henti
                continue

        # Pengisihan: 1) Kualiti resolusi, 2) Saiz fail (bit-rate mantap), 3) Latency rendah
        all_candidates.sort(key=lambda s: (s.quality_score, s.size_bytes, -s.latency_ms), reverse=True)

        return all_candidates[:40]

def display_results_table(title: str, imdb_id: str, year: int, streams: List[StreamCandidate]):
    table = Table(
        title=f"🎬 Keputusan Stream Fallback — {title} ({year}) [{imdb_id}]",
        caption=f"Jumlah Strim Ditemui: {len(streams)} (Had Maksimum Paparan: 40)",
        show_header=True,
        header_style="bold cyan",
        border_style="bright_blue"
    )

    table.add_column("No", justify="center", style="dim", width=4)
    table.add_column("Provider", justify="left", style="bold white", width=26)
    table.add_column("Resolution", justify="center", width=12)
    table.add_column("Codec / Container", justify="center", style="magenta", width=18)
    table.add_column("File Size", justify="right", style="green", width=12)
    table.add_column("Latency", justify="right", style="yellow", width=12)
    table.add_column("Status", justify="center", width=16)

    res_color_map = {
        "4K": "[bold bright_red]4K UHD[/]",
        "1080p": "[bold bright_cyan]1080p FHD[/]",
        "720p": "[bold bright_yellow]720p HD[/]",
        "480p": "[dim white]480p SD[/]"
    }

    status_color_map = {
        "OK": "[bold green]ONLINE[/]",
        "Fallback OK": "[bold blue]FALLBACK[/]",
        "Backup Ready": "[dim cyan]BACKUP[/]",
        "Error": "[bold red]FAILED[/]"
    }

    for idx, item in enumerate(streams, start=1):
        res_display = res_color_map.get(item.resolution, item.resolution)
        status_display = status_color_map.get(item.status, item.status)

        table.add_row(
            str(idx),
            item.provider,
            res_display,
            f"{item.codec} ({item.container})",
            item.size_str,
            f"{item.latency_ms:.1f} ms",
            status_display
        )

    console.print(table)

if __name__ == "__main__":
    console.print(Panel.fit(
        "[bold green]Ujian PoC Smart Fallback Stream Resolver[/bold green]\n"
        "[dim]Memulakan simulasi agregasi berbilang penyedia dengan toleransi kegagalan...[/dim]",
        border_style="green"
    ))

    test_imdb = "tt0451787"
    test_title = "Kyon Ki"
    test_year = 2005

    aggregator = StreamAggregator()

    # Senario 1: Semua Provider Berfungsi Normal
    console.print("\n[bold underline yellow]>>> SENARIO 1: SEMUA PROVIDER NORMAL (A, B, C AKTIF)[/bold underline yellow]")
    streams_normal = aggregator.aggregate_streams(test_imdb, test_title, test_year, simulate_failure_provider_a=False)
    display_results_table(test_title, test_imdb, test_year, streams_normal)

    # Senario 2: Provider A Gagal / Timeout (Auto-fallback ke B & C)
    console.print("\n[bold underline red]>>> SENARIO 2: PROVIDER-A TIMEOUT / DOWN (AUTO-FALLBACK KE B & C)[/bold underline red]")
    streams_fallback = aggregator.aggregate_streams(test_imdb, test_title, test_year, simulate_failure_provider_a=True)
    display_results_table(test_title, test_imdb, test_year, streams_fallback)