#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V3 - ENJIN PANGKALAN DATA REDIS SHARDED
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v3/X01_series_redis.py
# CIRI:
# 1. CRC32 4-Shard Modulo Routing (Siri & Filem)
# 2. Multi-Stream Array & InfoHash Mapping
# 3. Penjejak Garis Masa LRU Rentas Shard
# 4. Pengurusan Cache Senarai Seeder (save_torrent_list)
# ==============================================================================

import zlib
import json
import time
import sys
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import httpx
from rich.console import Console

console = Console()

# Muat tetapan dari live_engine/00_config.py tanpa mengubah fail asal
CURRENT_DIR = Path(__file__).resolve().parent
LIVE_ENGINE_DIR = CURRENT_DIR.parent / "live_engine"

for p in [CURRENT_DIR, LIVE_ENGINE_DIR]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _config = importlib.import_module("00_config")
    REDIS_ACCOUNTS = getattr(_config, "REDIS_ACCOUNTS", [])
    CF_B2_PROXY = getattr(
        _config,
        "CF_WORKER_B2_PROXY_STORAGE",
        "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev"
    ).rstrip("/")
except Exception as e:
    console.print(f"[bold red]❌ Ralat memuatkan konfigurasi dari 00_config: {e}[/bold red]")
    REDIS_ACCOUNTS = []
    CF_B2_PROXY = "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev"


class SeriesRedisShardedDB:
    def __init__(self, accounts: List[Dict[str, Any]], proxy_base: str):
        self.accounts = accounts
        self.total_shards = len(accounts)
        self.proxy_base = proxy_base.rstrip("/")
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def get_shard(self, key_identifier: str) -> Dict[str, Any]:
        """Menentukan shard sasaran menggunakan formula CRC32 modulo."""
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
        """Membina URL penstriman rasmi melalui Cloudflare B2 Proxy."""
        clean_path = file_path.lstrip("/")
        return f"{self.proxy_base}/{bucket_name}/{clean_path}"

    # --------------------------------------------------------------------------
    # PENGURUSAN SENARAI SEEDER (LIST CACHE)
    # --------------------------------------------------------------------------
    def save_torrent_list(self, imdb_id: str, torrent_list: List[Dict[str, Any]], ttl_seconds: int = 86400) -> bool:
        """Menyimpan senarai torrent sedia sedut ke Redis Shard (TTL: 24 Jam)."""
        shard = self.get_shard(imdb_id)
        serialized = json.dumps(torrent_list, ensure_ascii=False)
        res = self.execute_command(shard, "SET", f"stremio:list:{imdb_id}", serialized, "EX", ttl_seconds)
        return res == "OK"

    def get_torrent_list(self, imdb_id: str) -> Optional[List[Dict[str, Any]]]:
        """Mengambil senarai torrent sedia sedut yang dicache."""
        shard = self.get_shard(imdb_id)
        raw = self.execute_command(shard, "GET", f"stremio:list:{imdb_id}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    # --------------------------------------------------------------------------
    # PENGURUSAN METADATA STRIM SIAP B2 (MULTI-STREAM)
    # --------------------------------------------------------------------------
    def set_stream_metadata(self, imdb_id: str, data: Dict[str, Any]) -> bool:
        """Menyimpan atau menggabungkan kualiti baharu ke dalam senarai streams."""
        shard = self.get_shard(imdb_id)
        current_ts = int(time.time())
        target_hash = (data.get("info_hash") or "").lower().strip()

        bucket = data.get("b2_bucket", "")
        f_path = data.get("file_path", "").lstrip("/")
        stream_url = self.build_proxy_stream_url(bucket, f_path)

        new_stream_entry = {
            "title": data.get("title", ""),
            "resolution": data.get("resolution", "1080p"),
            "file_name": data.get("file_name", Path(f_path).name),
            "file_path": f_path,
            "size_bytes": int(data.get("size_bytes", 0)),
            "b2_account_index": int(data.get("b2_account_index", 1)),
            "b2_bucket": bucket,
            "stream_url": stream_url,
            "info_hash": target_hash,
            "sha256": data.get("sha256", ""),
            "created_at": int(data.get("created_at", current_ts)),
            "last_accessed": current_ts,
        }

        existing_streams: List[Dict[str, Any]] = []
        raw = self.execute_command(shard, "GET", f"stremio:{imdb_id}")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    if "streams" in parsed and isinstance(parsed["streams"], list):
                        existing_streams = parsed["streams"]
                    elif parsed.get("file_path"):
                        existing_streams = [parsed]
                elif isinstance(parsed, list):
                    existing_streams = parsed
            except Exception:
                existing_streams = []

        # Keluarkan versi yang sama jika info_hash bertindih
        merged_streams = [
            s for s in existing_streams 
            if isinstance(s, dict) and s.get("info_hash", "").lower() != target_hash
        ]
        merged_streams.insert(0, new_stream_entry)

        payload_full = {
            "imdb_id": imdb_id,
            "title": new_stream_entry["title"],
            "resolution": new_stream_entry["resolution"],
            "file_name": new_stream_entry["file_name"],
            "file_path": new_stream_entry["file_path"],
            "size_bytes": new_stream_entry["size_bytes"],
            "b2_account_index": new_stream_entry["b2_account_index"],
            "b2_bucket": new_stream_entry["b2_bucket"],
            "stream_url": new_stream_entry["stream_url"],
            "info_hash": target_hash,
            "sha256": new_stream_entry["sha256"],
            "created_at": new_stream_entry["created_at"],
            "last_accessed": current_ts,
            "streams": merged_streams
        }

        serialized = json.dumps(payload_full, ensure_ascii=False)
        res_set = self.execute_command(shard, "SET", f"stremio:{imdb_id}", serialized)

        if target_hash:
            t_shard = self.get_shard(target_hash)
            self.execute_command(t_shard, "SET", f"torrent:{target_hash}", imdb_id)

        self.execute_command(shard, "ZADD", "timeline:lru", current_ts, imdb_id)
        return res_set == "OK"

    def get_stream_metadata(self, imdb_id: str) -> Optional[Dict[str, Any]]:
        """Membaca metadata strim siap dan mengemas kini skor capaian LRU."""
        shard = self.get_shard(imdb_id)
        raw = self.execute_command(shard, "GET", f"stremio:{imdb_id}")
        if not raw:
            return None

        try:
            data = json.loads(raw)
            current_ts = int(time.time())
            self.execute_command(shard, "ZADD", "timeline:lru", current_ts, imdb_id)
            return data
        except json.JSONDecodeError:
            return None

    def get_torrent_cache(self, info_hash: str) -> Optional[str]:
        """Menyemak IMDb ID pemilik torrent melalui InfoHash."""
        clean_hash = (info_hash or "").lower().strip()
        shard = self.get_shard(clean_hash)
        return self.execute_command(shard, "GET", f"torrent:{clean_hash}")

    def delete_stream_metadata(self, imdb_id: str, info_hash: Optional[str] = None) -> bool:
        """
        Memadam rekod metadata. Jika info_hash dibekalkan, hanya versi tersebut dibuang.
        Jika tiada versi berbaki, keseluruhan kunci stremio dan LRU akan dibersihkan.
        """
        shard = self.get_shard(imdb_id)
        meta = self.get_stream_metadata(imdb_id)
        if not meta:
            return False

        target_hash = (info_hash or "").lower().strip()

        if target_hash and "streams" in meta and isinstance(meta["streams"], list):
            t_shard = self.get_shard(target_hash)
            self.execute_command(t_shard, "DEL", f"torrent:{target_hash}")

            remaining = [s for s in meta["streams"] if s.get("info_hash", "").lower() != target_hash]

            if remaining:
                top = remaining[0]
                meta.update({
                    "title": top.get("title", meta.get("title")),
                    "resolution": top.get("resolution", meta.get("resolution")),
                    "file_name": top.get("file_name", meta.get("file_name")),
                    "file_path": top.get("file_path", meta.get("file_path")),
                    "size_bytes": top.get("size_bytes", meta.get("size_bytes")),
                    "b2_account_index": top.get("b2_account_index", meta.get("b2_account_index")),
                    "b2_bucket": top.get("b2_bucket", meta.get("b2_bucket")),
                    "stream_url": top.get("stream_url", meta.get("stream_url")),
                    "info_hash": top.get("info_hash", meta.get("info_hash")),
                    "sha256": top.get("sha256", meta.get("sha256")),
                    "streams": remaining
                })
                self.execute_command(shard, "SET", f"stremio:{imdb_id}", json.dumps(meta, ensure_ascii=False))
                return True

        # Padam keseluruhan jika tiada versi berbaki
        all_hashes = [meta.get("info_hash")] if meta.get("info_hash") else []
        if "streams" in meta and isinstance(meta["streams"], list):
            for s in meta["streams"]:
                if s.get("info_hash"):
                    all_hashes.append(s["info_hash"])

        for h in set(all_hashes):
            if h:
                t_shard = self.get_shard(h)
                self.execute_command(t_shard, "DEL", f"torrent:{h.lower()}")

        self.execute_command(shard, "ZREM", "timeline:lru", imdb_id)
        res = self.execute_command(shard, "DEL", f"stremio:{imdb_id}")
        return bool(res and res > 0)

    def get_oldest_stream_across_shards(self) -> Optional[Dict[str, Any]]:
        """Mencari fail yang paling lama tidak diakses merentasi 4 shard."""
        oldest_candidate: Optional[Tuple[str, float, Dict[str, Any]]] = None

        for shard in self.accounts:
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
                    data = json.loads(raw_meta)
                    streams = data.get("streams", [])
                    if isinstance(streams, list) and streams:
                        sorted_streams = sorted(streams, key=lambda s: s.get("created_at", 0))
                        oldest_stream = sorted_streams[0].copy()
                        oldest_stream["imdb_id"] = target_id
                        return oldest_stream

                    data["imdb_id"] = target_id
                    return data
                except Exception:
                    pass
        return None


# Singleton Instance V3
series_db = SeriesRedisShardedDB(REDIS_ACCOUNTS, CF_B2_PROXY)