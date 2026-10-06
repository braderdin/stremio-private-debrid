#!/usr/bin/env python3
"""
Module: 04_test_async_stremio_resolver.py
Path: /home/braderdin/stremio-private-debrid/live_engine_v3/exp_test/04_test_async_stremio_resolver.py

Penerangan:
Skrip ujian Async Multi-Provider Stremio Resolver. Menguji resolusi serentak
(concurrent fetching), validasi HTTP stream, dan pembentukan output mengikut
spesifikasi Stremio Addon Protocol v1.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

@dataclass
class ResolvedStream:
    provider_name: str
    source_type: str  # Direct B2/R2, Debrid-HTTP, Web-HLS, Direct-MP4
    resolution: str   # 4K, 1080p, 720p, 480p
    quality_score: int
    size_bytes: int
    stream_url: str
    latency_ms: float
    headers: Dict[str, str] = field(default_factory=dict)
    is_live: bool = True

    @property
    def size_str(self) -> str:
        if self.size_bytes >= 1024 ** 3:
            return f"{self.size_bytes / (1024 ** 3):.2f} GB"
        elif self.size_bytes >= 1024 ** 2:
            return f"{self.size_bytes / (1024 ** 2):.1f} MB"
        return f"{self.size_bytes / 1024:.1f} KB"

    def to_stremio_format(self) -> Dict[str, Any]:
        """Menukar ke format rasmi Stremio Stream Object"""
        title_lines = [
            f"🎬 {self.resolution} | 💾 {self.size_str}",
            f"⚡ {self.source_type} ({self.latency_ms:.0f}ms)"
        ]
        
        stream_obj: Dict[str, Any] = {
            "name": f"[{self.provider_name}]\n{self.resolution}",
            "title": "\n".join(title_lines),
            "url": self.stream_url,
            "behaviorHints": {
                "notWebReady": False
            }
        }
        
        if self.headers:
            stream_obj["behaviorHints"]["proxyHeaders"] = {
                "request": self.headers
            }
            
        return stream_obj

# ==========================================
# ASYNC PROVIDER IMPLEMENTATION
# ==========================================

class BaseAsyncProvider:
    def __init__(self, name: str, source_type: str, timeout_sec: float = 2.0):
        self.name = name
        self.source_type = source_type
        self.timeout_sec = timeout_sec

    async def fetch(self, imdb_id: str) -> List[ResolvedStream]:
        raise NotImplementedError

class CloudStorageProvider(BaseAsyncProvider):
    """Kategori 1: Direct Storage HTTP (B2 / Cloudflare R2 / WebDAV)"""
    def __init__(self):
        super().__init__("CloudStore-B2", "Cloud-Direct", timeout_sec=1.5)

    async def fetch(self, imdb_id: str) -> List[ResolvedStream]:
        start = time.perf_counter()
        await asyncio.sleep(0.04)  # Simulasi latency rangkaian pantas
        latency = (time.perf_counter() - start) * 1000

        return [
            ResolvedStream(
                provider_name=self.name,
                source_type=self.source_type,
                resolution="1080p",
                quality_score=1080,
                size_bytes=int(4.1 * 1024 ** 3),
                stream_url=f"https://f002.backblazeb2.com/file/my-bucket/{imdb_id}/1080p.mp4",
                latency_ms=latency
            )
        ]

class DebridHTTPProvider(BaseAsyncProvider):
    """Kategori 2: Debrid Cache Unrestricted Direct Links"""
    def __init__(self):
        super().__init__("DebridLink-Fast", "Debrid-Cache", timeout_sec=2.0)

    async def fetch(self, imdb_id: str) -> List[ResolvedStream]:
        start = time.perf_counter()
        await asyncio.sleep(0.07)
        latency = (time.perf_counter() - start) * 1000

        return [
            ResolvedStream(
                provider_name=self.name,
                source_type=self.source_type,
                resolution="4K",
                quality_score=2160,
                size_bytes=int(12.4 * 1024 ** 3),
                stream_url=f"https://debrid-cdn.net/dl/{imdb_id}_remux_4k.mkv",
                latency_ms=latency
            ),
            ResolvedStream(
                provider_name=self.name,
                source_type=self.source_type,
                resolution="1080p",
                quality_score=1080,
                size_bytes=int(2.8 * 1024 ** 3),
                stream_url=f"https://debrid-cdn.net/dl/{imdb_id}_1080p.mp4",
                latency_ms=latency
            )
        ]

class AdaptiveHLSProvider(BaseAsyncProvider):
    """Kategori 3: Multi-bitrate HLS Streaming (.m3u8)"""
    def __init__(self):
        super().__init__("MediaHLS-CDN", "HLS-Adaptive", timeout_sec=2.0)

    async def fetch(self, imdb_id: str) -> List[ResolvedStream]:
        start = time.perf_counter()
        await asyncio.sleep(0.09)
        latency = (time.perf_counter() - start) * 1000

        return [
            ResolvedStream(
                provider_name=self.name,
                source_type=self.source_type,
                resolution="1080p",
                quality_score=1080,
                size_bytes=int(2.1 * 1024 ** 3),
                stream_url=f"https://edge-stream.network/hls/{imdb_id}/master.m3u8",
                latency_ms=latency
            ),
            ResolvedStream(
                provider_name=self.name,
                source_type=self.source_type,
                resolution="720p",
                quality_score=720,
                size_bytes=int(1.2 * 1024 ** 3),
                stream_url=f"https://edge-stream.network/hls/{imdb_id}/720p.m3u8",
                latency_ms=latency
            )
        ]

class WebDirectResolver(BaseAsyncProvider):
    """Kategori 4: Web Extractor dengan Custom Proxy Headers"""
    def __init__(self):
        super().__init__("WebExtractor-V4", "Web-Direct", timeout_sec=2.5)

    async def fetch(self, imdb_id: str) -> List[ResolvedStream]:
        start = time.perf_counter()
        await asyncio.sleep(0.12)
        latency = (time.perf_counter() - start) * 1000

        return [
            ResolvedStream(
                provider_name=self.name,
                source_type=self.source_type,
                resolution="720p",
                quality_score=720,
                size_bytes=int(950 * 1024 ** 2),
                stream_url=f"https://storage-node-direct.com/video/{imdb_id}.mp4",
                latency_ms=latency,
                headers={"Referer": "https://stream-hub.io", "User-Agent": "Mozilla/5.0"}
            )
        ]

# ==========================================
# ASYNC AGGREGATOR ENGINE
# ==========================================

class AsyncStremioEngine:
    def __init__(self):
        self.providers: List[BaseAsyncProvider] = [
            CloudStorageProvider(),
            DebridHTTPProvider(),
            AdaptiveHLSProvider(),
            WebDirectResolver()
        ]

    async def resolve_all(self, imdb_id: str) -> List[ResolvedStream]:
        tasks = [p.fetch(imdb_id) for p in self.providers]
        
        # Jalankan semua pembekal serentak (concurrent non-blocking)
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        
        resolved: List[ResolvedStream] = []
        for res in results_nested:
            if isinstance(res, list):
                resolved.extend(res)
            elif isinstance(res, Exception):
                console.print(f"[bold red]Provider error caught:[/] {res}")

        # Susun: Resolusi tertinggi dahulu, diikuti saiz fail terbesar
        resolved.sort(key=lambda s: (s.quality_score, s.size_bytes), reverse=True)
        return resolved[:40]

def print_rich_dashboard(streams: List[ResolvedStream]):
    table = Table(
        title="⚡ Ujian Asynchronous Stremio Multi-Source Resolver",
        caption=f"Jumlah Strim Sah: {len(streams)}",
        header_style="bold cyan",
        border_style="bright_blue"
    )

    table.add_column("No", justify="center", width=4)
    table.add_column("Provider", style="bold white", width=18)
    table.add_column("Type", style="magenta", width=15)
    table.add_column("Resolution", justify="center", width=12)
    table.add_column("File Size", justify="right", style="green", width=12)
    table.add_column("Latency", justify="right", style="yellow", width=10)
    table.add_column("Has Headers", justify="center", width=12)

    res_color = {
        "4K": "[bold red]4K UHD[/]",
        "1080p": "[bold cyan]1080p FHD[/]",
        "720p": "[bold yellow]720p HD[/]"
    }

    for idx, s in enumerate(streams, 1):
        table.add_row(
            str(idx),
            s.provider_name,
            s.source_type,
            res_color.get(s.resolution, s.resolution),
            s.size_str,
            f"{s.latency_ms:.1f}ms",
            "[green]YES[/]" if s.headers else "[dim]NO[/]"
        )

    console.print(table)

async def main():
    console.print(Panel.fit(
        "[bold green]Enjin Async Resolver Stremio Dimulakan[/bold green]\n"
        "[dim]Memanggil 4 kategori sumber serentak tanpa blocking...[/dim]",
        border_style="green"
    ))

    engine = AsyncStremioEngine()
    start_total = time.perf_counter()
    streams = await engine.resolve_all("tt0451787")
    total_time = (time.perf_counter() - start_total) * 1000

    print_rich_dashboard(streams)
    console.print(f"[bold green]Masa Keseluruhan Resolusi (Semua Sumber):[/bold green] [yellow]{total_time:.2f} ms[/yellow]\n")

    # Paparan contoh JSON Stremio sebenar untuk 2 item teratas
    stremio_payload = {"streams": [s.to_stremio_format() for s in streams[:2]]}
    json_str = json.dumps(stremio_payload, indent=2)
    
    console.print(Panel(
        Syntax(json_str, "json", theme="monokai", line_numbers=True),
        title="Contoh Output Stremio Protocol v1 (/stream/movie/tt0451787.json)",
        border_style="cyan"
    ))

if __name__ == "__main__":
    asyncio.run(main())