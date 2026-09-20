#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - PENGURUS STORAN B2 (02_b2_storage.py)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/02_b2_storage.py
# CIRI: NATIVE B2 RCLONE ENGINE + ROUND-ROBIN 20 AKAUN + AUTOMATIK LRU EVICTION
# ==============================================================================

import os
import time
import subprocess
import importlib
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import httpx
from rich.console import Console
from rich.panel import Panel

console = Console()

# 1. Muat Turun Tetapan dari 00_config
try:
    _config = importlib.import_module("00_config")
    B2_ACCOUNTS = getattr(_config, "B2_ACCOUNTS", [])
    B2_MAX_BYTES = getattr(_config, "B2_MAX_BYTES_PER_ACCOUNT", int(9.5 * 1024 * 1024 * 1024))
    CF_B2_PROXY = getattr(
        _config,
        "CF_WORKER_B2_PROXY_STORAGE",
        "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev"
    ).rstrip("/")
except ImportError:
    console.print("[bold red]❌ Gagal mengimport 00_config.py![/bold red]")
    B2_ACCOUNTS = []
    B2_MAX_BYTES = int(9.5 * 1024 * 1024 * 1024)
    CF_B2_PROXY = "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev"


class B2StorageManager:
    def __init__(self, accounts: List[Dict[str, Any]], proxy_base: str):
        self.accounts = accounts
        self.proxy_base = proxy_base.rstrip("/")
        self.auth_cache: Dict[int, Dict[str, Any]] = {}
        self.timeout = httpx.Timeout(15.0, connect=8.0)
        self.current_rr_index = 0

    def authorize_account(self, acc_index: int) -> Optional[Dict[str, Any]]:
        """Mendapatkan token sesi B2 dengan cache memori 12 jam."""
        now = time.time()
        cached = self.auth_cache.get(acc_index)
        if cached and cached.get("expires_at", 0) > (now + 300):
            return cached

        acc = next((a for a in self.accounts if a["index"] == acc_index), None)
        if not acc:
            return None

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    "https://api.backblazeb2.com/b2api/v2/b2_authorize_account",
                    auth=(acc["key_id"], acc["app_key"])
                )
                if resp.status_code == 200:
                    data = resp.json()
                    auth_info = {
                        "token": data["authorizationToken"],
                        "api_url": data["apiUrl"],
                        "download_url": data["downloadUrl"],
                        "account_id": data["accountId"],
                        "expires_at": now + (12 * 3600)
                    }
                    self.auth_cache[acc_index] = auth_info
                    return auth_info
        except Exception:
            pass
        return None

    def get_bucket_usage(self, acc_index: int) -> Tuple[int, int, str]:
        """Mengambil bilangan fail, bait digunakan, dan Bucket ID terkini."""
        acc = next((a for a in self.accounts if a["index"] == acc_index), None)
        auth = self.authorize_account(acc_index)
        if not acc or not auth:
            return 0, 0, ""

        bucket_id = acc.get("bucket_id", "")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                if not bucket_id:
                    b_resp = client.post(
                        f"{auth['api_url']}/b2api/v2/b2_list_buckets",
                        headers={"Authorization": auth["token"]},
                        json={"accountId": auth["account_id"], "bucketName": acc["bucket_name"]}
                    )
                    if b_resp.status_code == 200:
                        b_list = b_resp.json().get("buckets", [])
                        if b_list:
                            bucket_id = b_list[0]["bucketId"]

                if not bucket_id:
                    return 0, 0, ""

                f_resp = client.post(
                    f"{auth['api_url']}/b2api/v2/b2_list_file_names",
                    headers={"Authorization": auth["token"]},
                    json={"bucketId": bucket_id, "maxFileCount": 1000}
                )
                if f_resp.status_code == 200:
                    files = f_resp.json().get("files", [])
                    total_bytes = sum(f.get("contentLength", 0) for f in files)
                    return len(files), total_bytes, bucket_id
        except Exception:
            pass
        return 0, 0, bucket_id

    def delete_file_from_b2(self, acc_index: int, file_path: str) -> bool:
        """Memadamkan versi fail fizikal daripada bucket B2."""
        auth = self.authorize_account(acc_index)
        _, _, bucket_id = self.get_bucket_usage(acc_index)
        if not auth or not bucket_id:
            return False

        clean_name = file_path.lstrip("/")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                list_resp = client.post(
                    f"{auth['api_url']}/b2api/v2/b2_list_file_names",
                    headers={"Authorization": auth["token"]},
                    json={"bucketId": bucket_id, "startFileName": clean_name, "maxFileCount": 1}
                )
                if list_resp.status_code != 200:
                    return False

                files = list_resp.json().get("files", [])
                target_file = next((f for f in files if f["fileName"] == clean_name), None)
                if not target_file:
                    return False

                del_resp = client.post(
                    f"{auth['api_url']}/b2api/v2/b2_delete_file_version",
                    headers={"Authorization": auth["token"]},
                    json={"fileName": target_file["fileName"], "fileId": target_file["fileId"]}
                )
                return del_resp.status_code == 200
        except Exception:
            return False

    def select_round_robin_target(self, required_bytes: int = 1500000000) -> Optional[Dict[str, Any]]:
        """Memilih akaun B2 seterusnya secara Round-Robin yang mempunyai baki ruang selamat (< 9.5GB)."""
        total_accounts = len(self.accounts)
        if total_accounts == 0:
            return None

        for _ in range(total_accounts):
            acc = self.accounts[self.current_rr_index]
            self.current_rr_index = (self.current_rr_index + 1) % total_accounts

            _, used_bytes, _ = self.get_bucket_usage(acc["index"])
            if (B2_MAX_BYTES - used_bytes) >= required_bytes:
                return acc

        return None

    def allocate_storage_with_eviction(self, required_bytes: int, redis_db_instance: Any) -> Optional[Dict[str, Any]]:
        """Memastikan ruang mencukupi. Melakukan pelupusan LRU sekiranya semua 20 akaun penuh."""
        chosen_acc = self.select_round_robin_target(required_bytes)
        if chosen_acc:
            return chosen_acc

        console.print("[bold yellow]⚠️ Semua 20 akaun B2 penuh! Memulakan pembersihan fail lama (LRU)...[/bold yellow]")

        for _ in range(15):
            oldest_meta = redis_db_instance.get_oldest_stream_across_shards()
            if not oldest_meta:
                console.print("[bold red]❌ Tiada fail lama untuk dilupuskan![/bold red]")
                return None

            imdb_id = oldest_meta.get("imdb_id")
            acc_idx = oldest_meta.get("b2_account_index")
            f_path = oldest_meta.get("file_path")

            console.print(f"[dim]🗑️ Memadam fail lama: {oldest_meta.get('title')} ({f_path}) dari B2 #{acc_idx}...[/dim]")
            self.delete_file_from_b2(acc_idx, f_path)
            redis_db_instance.delete_stream_metadata(imdb_id)

            chosen_acc = self.select_round_robin_target(required_bytes)
            if chosen_acc:
                console.print(f"[bold green]✅ Ruang berjaya dikosongkan pada Akaun #{chosen_acc['index']}![/bold green]")
                return chosen_acc

        return None

    def upload_file(self, local_file: Path, b2_account: Dict[str, Any], dest_path: str) -> bool:
        """Memindahkan fail ke B2 menggunakan Native Rclone tanpa konflik endpoint."""
        env = os.environ.copy()
        remote_name = "TEMP_B2_RUNNER"
        clean_path = dest_path.lstrip("/")

        # Konfigurasi B2 Native rasmi (tanpa parameter endpoint S3)
        env[f"RCLONE_CONFIG_{remote_name}_TYPE"] = "b2"
        env[f"RCLONE_CONFIG_{remote_name}_ACCOUNT"] = b2_account["key_id"]
        env[f"RCLONE_CONFIG_{remote_name}_KEY"] = b2_account["app_key"]

        target_remote = f"{remote_name}:{b2_account['bucket_name']}/{clean_path}"
        cmd = [
            "rclone", "copyto",
            str(local_file),
            target_remote,
            "--transfers=4",
            "--fast-list",
            "--stats=10s",
            "--stats-one-line"
        ]

        console.print(f"[cyan]☁️ Memindahkan fail ke B2: #{b2_account['index']} ({b2_account['bucket_name']})...[/cyan]")
        res = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        if res.returncode != 0:
            console.print(f"[bold red]❌ Rclone gagal memindahkan fail (Kod {res.returncode}):[/bold red]\n{res.stdout[-600:]}")
            return False

        console.print(f"[bold green]✅ Rclone berjaya memindahkan fail ke B2![/bold green]")
        return True


# Singleton Instance
storage = B2StorageManager(B2_ACCOUNTS, CF_B2_PROXY)