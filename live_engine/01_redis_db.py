#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - ENJIN METADATA REDIS (4 SHARDS & LRU TRACKER)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/01_redis_db.py
# ==============================================================================

import zlib
import json
import time
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# 1. Muat Turun Tetapan dari 00_config
try:
    _config = importlib.import_module("00_config")
    REDIS_ACCOUNTS = getattr(_config, "REDIS_ACCOUNTS", [])
    CF_B2_PROXY = getattr(
        _config,
        "CF_WORKER_B2_PROXY_STORAGE",
        "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev"
    ).rstrip("/")
except ImportError:
    console.print("[bold red]❌ Gagal mengimport 00_config.py![/bold red]")
    REDIS_ACCOUNTS = []
    CF_B2_PROXY = "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev"


class RedisShardedDB:
    def __init__(self, accounts: List[Dict[str, Any]], proxy_base: str):
        self.accounts = accounts
        self.total_shards = len(accounts)
        self.proxy_base = proxy_base.rstrip("/")
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def get_shard(self, key_identifier: str) -> Dict[str, Any]:
        """Menentukan shard sasaran menggunakan CRC32 modulo (agihan seimbang 25% setiap shard)."""
        if not self.accounts:
            raise RuntimeError("Tiada akaun Upstash Redis ditemui!")
        checksum = zlib.crc32(key_identifier.encode("utf-8")) & 0xFFFFFFFF
        return self.accounts[checksum % self.total_shards]

    def execute_command(self, shard: Dict[str, Any], command: str, *args) -> Optional[Any]:
        """Melaksanakan arahan Redis melalui Upstash REST API."""
        url = shard["url"].rstrip("/")
        headers = {
            "Authorization": f"Bearer {shard['token']}",
            "Content-Type": "application/json"
        }
        payload = [command] + [str(a) for a in args]

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    return resp.json().get("result")
                return None
        except Exception:
            return None

    def build_proxy_stream_url(self, bucket_name: str, file_path: str) -> str:
        """Membina URL penstriman rasmi menggunakan Cloudflare B2 Proxy."""
        clean_path = file_path.lstrip("/")
        return f"{self.proxy_base}/{bucket_name}/{clean_path}"

    def set_stream_metadata(self, imdb_id: str, data: Dict[str, Any]) -> bool:
        """
        Menyimpan rekod metadata lengkap penstriman dan mendaftarkan cap masa ke indeks LRU.
        """
        shard = self.get_shard(imdb_id)
        current_ts = int(time.time())

        # Pastikan URL penstriman sentiasa menggunakan Cloudflare B2 Proxy
        bucket = data.get("b2_bucket", "")
        f_path = data.get("file_path", "")
        data["stream_url"] = self.build_proxy_stream_url(bucket, f_path)
        data["created_at"] = data.get("created_at", current_ts)
        data["last_accessed"] = current_ts

        # Metadata format piawai
        payload_full = {
            "imdb_id": imdb_id,
            "title": data.get("title", ""),
            "year": data.get("year", ""),
            "rating_imdb": data.get("rating_imdb", ""),
            "resolution": data.get("resolution", "1080p"),
            "file_name": data.get("file_name", Path(f_path).name),
            "file_path": f_path,
            "size_bytes": data.get("size_bytes", 0),
            "b2_account_index": data.get("b2_account_index", 1),
            "b2_bucket": bucket,
            "stream_url": data["stream_url"],
            "info_hash": data.get("info_hash", "").lower(),
            "sha256": data.get("sha256", ""),
            "created_at": data["created_at"],
            "last_accessed": data["last_accessed"]
        }

        serialized = json.dumps(payload_full, ensure_ascii=False)
        res_set = self.execute_command(shard, "SET", f"stremio:{imdb_id}", serialized)

        # Simpan indeks carian torrent (infohash -> imdb_id)
        if payload_full["info_hash"]:
            t_shard = self.get_shard(payload_full["info_hash"])
            self.execute_command(t_shard, "SET", f"torrent:{payload_full['info_hash']}", imdb_id)

        # Daftarkan ke Sorted Set (ZSET) untuk penjejakan fail tertua (LRU)
        self.execute_command(shard, "ZADD", "timeline:lru", current_ts, imdb_id)

        return res_set == "OK"

    def get_stream_metadata(self, imdb_id: str) -> Optional[Dict[str, Any]]:
        """Membaca metadata penstriman dan mengemas kini cap masa akses terakhir."""
        shard = self.get_shard(imdb_id)
        raw = self.execute_command(shard, "GET", f"stremio:{imdb_id}")
        if not raw:
            return None

        try:
            data = json.loads(raw)
            # Kemas kini cap masa akses terkini di ZSET
            current_ts = int(time.time())
            self.execute_command(shard, "ZADD", "timeline:lru", current_ts, imdb_id)
            return data
        except json.JSONDecodeError:
            return None

    def get_torrent_cache(self, info_hash: str) -> Optional[str]:
        """Menyemak kehadiran torrent melalui infohash."""
        shard = self.get_shard(info_hash.lower())
        return self.execute_command(shard, "GET", f"torrent:{info_hash.lower()}")

    def delete_stream_metadata(self, imdb_id: str) -> bool:
        """Memadamkan metadata, kunci torrent, dan rekod LRU daripada shard berkaitan."""
        shard = self.get_shard(imdb_id)
        meta = self.get_stream_metadata(imdb_id)

        if meta and meta.get("info_hash"):
            t_shard = self.get_shard(meta["info_hash"])
            self.execute_command(t_shard, "DEL", f"torrent:{meta['info_hash']}")

        self.execute_command(shard, "ZREM", "timeline:lru", imdb_id)
        res = self.execute_command(shard, "DEL", f"stremio:{imdb_id}")
        return bool(res and res > 0)

    def get_oldest_stream_across_shards(self) -> Optional[Dict[str, Any]]:
        """
        Mengimbas kesemua 4 shard untuk mencari fail paling lama yang tidak ditonton (Oldest LRU Entry).
        Digunakan oleh 02_b2_storage semasa proses pelupusan storan penuh.
        """
        oldest_candidate: Optional[Tuple[str, float, Dict[str, Any]]] = None

        for shard in self.accounts:
            # Dapatkan 1 entri dengan skor timestamp terkecil daripada ZSET
            res = self.execute_command(shard, "ZRANGE", "timeline:lru", 0, 0, "WITHSCORES")
            if res and isinstance(res, list) and len(res) >= 2:
                candidate_id = str(res[0])
                candidate_ts = float(res[1])

                if oldest_candidate is None or candidate_ts < oldest_candidate[1]:
                    oldest_candidate = (candidate_id, candidate_ts, shard)

        if oldest_candidate:
            target_id = oldest_candidate[0]
            raw_meta = self.execute_command(oldest_candidate[2], "GET", f"stremio:{target_id}")
            if raw_meta:
                try:
                    return json.loads(raw_meta)
                except Exception:
                    pass
        return None


# Singleton Instance
db = RedisShardedDB(REDIS_ACCOUNTS, CF_B2_PROXY)


if __name__ == "__main__":
    console.print(Panel.fit(
        "[bold cyan]🧪 UJIAN SISTEM METADATA REDIS & PROXY B2[/bold cyan]\n"
        f"[yellow]Pangkalan URL Proksi:[/yellow] [white]{CF_B2_PROXY}[/white]\n"
        "[white]Ciri: Modulo 4 Shards, LRU Timeline Indexing, Pautan Proksi Automatik[/white]",
        border_style="cyan"
    ))

    # Simulasi simpanan metadata
    sample_id = "tt0186945"
    sample_meta = {
        "title": "Big Momma's House",
        "year": "2000",
        "rating_imdb": "5.3",
        "resolution": "1080p",
        "file_name": "Big.Mommas.House.2000.1080p.BluRay.x264.mp4",
        "file_path": "movies/Big_Mommas_House_2000.mp4",
        "size_bytes": 1548576000,
        "b2_account_index": 1,
        "b2_bucket": "bucket-001-seedr-bot-din",
        "info_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a"
    }

    db.set_stream_metadata(sample_id, sample_meta)
    retrieved = db.get_stream_metadata(sample_id)

    table = Table(title="📦 Semakan Metadata Tersimpan di Redis", border_style="green")
    table.add_column("Parameter", style="cyan")
    table.add_column("Nilai", style="white")

    if retrieved:
        table.add_row("IMDb ID", retrieved.get("imdb_id", ""))
        table.add_row("Tajuk & Tahun", f"{retrieved.get('title')} ({retrieved.get('year')})")
        table.add_row("URL Proksi B2", f"[green]{retrieved.get('stream_url')}[/green]")
        table.add_row("Hash SHA256", str(retrieved.get("sha256"))[:24] + "...")
        table.add_row("InfoHash", str(retrieved.get("info_hash"))[:24] + "...")
        table.add_row("Cap Masa LRU", str(retrieved.get("last_accessed")))

    console.print(table)