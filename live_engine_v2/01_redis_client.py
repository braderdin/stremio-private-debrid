#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID V2 - REDIS CLIENT (01_redis_client.py)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine_v2/01_redis_client.py
# CIRI: CRC32 SHARDING 4 AKAUN + AUTO-EXPIRY (TTL 2 JAM) UNTUK SENARAI SEEDER
# ==============================================================================

import os
import zlib
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
import httpx
from dotenv import load_dotenv

# Muat turun pembolehubah .env.local jika wujud
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = PROJECT_ROOT / ".env.local"
if ENV_LOCAL.exists():
    load_dotenv(ENV_LOCAL)
else:
    load_dotenv()


class RedisShardClientV2:
    def __init__(self):
        self.accounts = self._load_accounts()
        self.total_shards = len(self.accounts)
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def _load_accounts(self) -> List[Dict[str, Any]]:
        raw_json = (os.getenv("REDIS_ACCOUNTS_JSON") or "").strip()
        accounts = []
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, list):
                    for idx, acc in enumerate(parsed, 1):
                        url = (acc.get("redis_rest_url") or acc.get("url") or "").rstrip("/")
                        token = acc.get("redis_rest_token") or acc.get("token") or ""
                        if url and token:
                            accounts.append({"index": acc.get("index", idx), "url": url, "token": token})
                    accounts.sort(key=lambda x: x["index"])
            except Exception:
                pass

        # Sandaran: Baca pembolehubah UPSTASH_REDIS_001 hingga 004
        if not accounts:
            for idx in range(1, 5):
                fmt = f"{idx:03d}"
                url = os.getenv(f"UPSTASH_REDIS_{fmt}_REST_URL")
                token = os.getenv(f"UPSTASH_REDIS_{fmt}_REST_TOKEN")
                if url and token:
                    accounts.append({"index": idx, "url": url.strip().rstrip("/"), "token": token.strip()})

        return accounts

    def get_shard(self, key_identifier: str) -> Dict[str, Any]:
        """Menentukan shard sasaran menggunakan formula CRC32 Modulo."""
        if not self.accounts:
            raise RuntimeError("Tiada akaun Upstash Redis ditemui!")
        checksum = zlib.crc32(key_identifier.encode("utf-8")) & 0xFFFFFFFF
        return self.accounts[checksum % self.total_shards]

    def execute_command(self, shard: Dict[str, Any], command: str, *args) -> Optional[Any]:
        """Menghantar arahan terus ke Upstash REST API."""
        url = shard["url"]
        headers = {"Authorization": f"Bearer {shard['token']}", "Content-Type": "application/json"}
        payload = [command] + [str(a) for a in args]

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    return resp.json().get("result")
        except Exception:
            pass
        return None

    def save_torrent_list(self, imdb_id: str, torrents: List[Dict[str, Any]], ttl_seconds: int = 7200) -> bool:
        """Menyimpan senarai torrent ke Redis dengan tempoh luput (lalai: 2 jam)."""
        shard = self.get_shard(imdb_id)
        serialized = json.dumps(torrents, ensure_ascii=False)
        # SET key value EX ttl
        res = self.execute_command(shard, "SET", f"stremio:list:{imdb_id}", serialized, "EX", ttl_seconds)
        return res == "OK"

    def get_torrent_list(self, imdb_id: str) -> Optional[List[Dict[str, Any]]]:
        """Membaca senarai torrent sedia ada."""
        shard = self.get_shard(imdb_id)
        raw = self.execute_command(shard, "GET", f"stremio:list:{imdb_id}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None


db_v2 = RedisShardClientV2()