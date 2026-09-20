#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - PELAYAN MIKRO RENDER (RESOLVER & DISPATCHER)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/04_render_server.py
# ==============================================================================

import os
import sys
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional, List

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# 1. Pastikan Laluan Direktori Dimasukkan ke sys.path
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# 2. Pembolehubah Persekitaran Utama (Render & GitHub Actions)
ADDON_SECRET_TOKEN = os.getenv("ADDON_SECRET_TOKEN", "Harunosakura1122")
GH_PAT = os.getenv("GH_PAT", "")
GH_OWNER = os.getenv("GH_OWNER", "braderdin")
GH_REPO = os.getenv("GH_REPO", "stremio-private-debrid")
GH_WORKFLOW_FILE = os.getenv("GH_WORKFLOW_FILE", "00_download.yml")
REDIS_ACCOUNTS_JSON = os.getenv("REDIS_ACCOUNTS_JSON", "")

# 3. Model Permintaan Pydantic
class DispatchRequest(BaseModel):
    info_hash: str
    imdb_id: str
    file_idx: Optional[str] = "0"
    title: Optional[str] = "Unknown Title"
    secret_token: Optional[str] = None


# 4. Inisialisasi Aplikasi FastAPI
app = FastAPI(
    title="Stremio Private Debrid Dispatcher & Resolver",
    description="Pelayan mikro ringan 24/7 untuk keepalive ping, stream resolver & GitHub Actions dispatcher",
    version="1.1.0"
)

SERVER_START_TIME = time.time()


def get_redis_shards() -> list:
    """Membaca shard Upstash Redis daripada pembolehubah persekitaran."""
    if not REDIS_ACCOUNTS_JSON:
        return []
    try:
        data = json.loads(REDIS_ACCOUNTS_JSON)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def trigger_github_workflow(info_hash: str, imdb_id: str, file_idx: str, title: str) -> bool:
    """Menghantar arahan POST workflow_dispatch terus ke GitHub REST API."""
    if not GH_PAT:
        return False

    api_url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/actions/workflows/{GH_WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Render-Debrid-Dispatcher/1.1"
    }
    payload = {
        "ref": "main",
        "inputs": {
            "info_hash": info_hash.lower().strip(),
            "imdb_id": imdb_id.strip(),
            "file_idx": str(file_idx),
            "title": title.strip()
        }
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(api_url, headers=headers, json=payload)
            return resp.status_code == 204
        except Exception:
            return False


@app.get("/", tags=["Keepalive"])
async def root_ping():
    """
    Endpoint utama menerima Keepalive Ping dari Cloudflare Worker setiap 10 minit.
    Menghalang pelayan Render Free Tier daripada masuk ke mod tidur (sleep).
    """
    uptime_seconds = int(time.time() - SERVER_START_TIME)
    uptime_str = f"{uptime_seconds // 3600}j {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s"

    return {
        "status": "online",
        "service": "stremio-private-debrid-dispatcher",
        "uptime": uptime_str,
        "timestamp": int(time.time()),
        "message": "Render web service aktif dan bersedia menerima tugasan."
    }


@app.get("/health", tags=["Keepalive"])
async def health_check():
    """Endpoint pemantauan kesihatan uptime perkhidmatan."""
    return {"status": "healthy", "timestamp": int(time.time())}


@app.get("/api/resolve-streams", tags=["Resolver"])
async def resolve_streams(type: str = Query("movie"), id: str = Query(...)):
    """
    Menyelesaikan senarai torrent daripada Torrentio tanpa sekatan Cloudflare WAF.
    Panggilan dibuat terus dari pusat data Render di Singapura.
    """
    clean_id = id.replace(".json", "").strip()
    target_url = f"https://torrentio.strem.fun/stream/{type}/{clean_id}.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        try:
            resp = await client.get(target_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return {"success": True, "streams": data.get("streams", [])}
            return {"success": False, "streams": [], "status_code": resp.status_code}
        except Exception as e:
            return {"success": False, "streams": [], "error": str(e)}


@app.post("/api/dispatch", tags=["Dispatcher"])
async def handle_dispatch(
    body: DispatchRequest,
    x_addon_token: Optional[str] = Header(None, alias="X-Addon-Token")
):
    """
    Endpoint webhook untuk memicu alur kerja GitHub Actions secara luaran.
    """
    auth_candidate = body.secret_token or x_addon_token
    if auth_candidate != ADDON_SECRET_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token keselamatan tidak sah atau tidak disertakan."
        )

    if not body.info_hash or not body.imdb_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parameter info_hash dan imdb_id adalah wajib."
        )

    success = await trigger_github_workflow(
        info_hash=body.info_hash,
        imdb_id=body.imdb_id,
        file_idx=body.file_idx or "0",
        title=body.title or "Unknown"
    )

    if not success:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "success": False,
                "message": "Gagal mencetuskan alur kerja GitHub Actions. Sila semak GH_PAT dan nama repositori."
            }
        )

    return {
        "success": True,
        "message": "Isyarat muat turun berjaya dihantar ke GitHub Actions Runner.",
        "payload": {
            "info_hash": body.info_hash,
            "imdb_id": body.imdb_id,
            "file_idx": body.file_idx,
            "title": body.title
        }
    }


@app.get("/api/check-cache", tags=["Metadata"])
async def check_cache(imdb_id: str = Query(..., description="Stremio IMDb ID")):
    """
    Menyemak status ketersediaan video di pangkalan data Upstash Redis.
    """
    shards = get_redis_shards()
    if not shards:
        return {"status": "unconfigured", "message": "Konfigurasi REDIS_ACCOUNTS_JSON tiada pada Render."}

    match_num = "".join(filter(str.isdigit, imdb_id))
    shard_idx = int(match_num) % len(shards) if match_num else 0
    target_shard = shards[shard_idx]

    url = (target_shard.get("redis_rest_url") or target_shard.get("url") or "").rstrip("/")
    token = target_shard.get("redis_rest_token") or target_shard.get("token") or ""

    if not url or not token:
        return {"status": "error", "message": "Kredensial Upstash Shard tidak lengkap."}

    async with httpx.AsyncClient(timeout=6.0) as client:
        try:
            resp = await client.get(f"{url}/get/stremio:{imdb_id}", headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                result_raw = resp.json().get("result")
                if result_raw:
                    meta = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
                    return {
                        "cached": True,
                        "title": meta.get("title"),
                        "stream_url": meta.get("stream_url"),
                        "b2_bucket": meta.get("b2_bucket")
                    }
        except Exception as e:
            return {"cached": False, "error": str(e)}

    return {"cached": False, "message": "Fail belum ada dalam storan B2."}


# 5. Mod Pelaksanaan Setempat (WSL)
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"🚀 Memulakan pelayan Render tempatan di http://127.0.0.1:{port}")
    uvicorn.run("04_render_server:app", host="0.0.0.0", port=port, reload=True)