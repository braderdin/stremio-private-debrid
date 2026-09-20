#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - PELAYAN MIKRO RENDER (APIBAY RESOLVER 20-40)
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/04_render_server.py
# ==============================================================================

import os
import sys
import time
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

ADDON_SECRET_TOKEN = os.getenv("ADDON_SECRET_TOKEN", "Harunosakura1122")
GH_PAT = os.getenv("GH_PAT", "")
GH_OWNER = os.getenv("GH_OWNER", "braderdin")
GH_REPO = os.getenv("GH_REPO", "stremio-private-debrid")
GH_WORKFLOW_FILE = os.getenv("GH_WORKFLOW_FILE", "00_download.yml")
REDIS_ACCOUNTS_JSON = os.getenv("REDIS_ACCOUNTS_JSON", "")

app = FastAPI(title="Stremio Debrid Engine", version="2.0.0")
SERVER_START_TIME = time.time()

MIN_BYTES = 500 * 1024 * 1024       # Minima 500 MB
MAX_BYTES = int(9.0 * 1024**3)      # Maksima 9.0 GB
SWEET_MIN = 1 * 1024**3             # Keutamaan 1.0 GB
SWEET_MAX = 4 * 1024**3             # Keutamaan 4.0 GB


class DispatchRequest(BaseModel):
    info_hash: str
    imdb_id: str
    file_idx: Optional[str] = "0"
    title: Optional[str] = "Unknown Title"
    secret_token: Optional[str] = None


def detect_quality(name: str) -> str:
    n = name.lower()
    if any(q in n for q in ["2160p", "4k", "uhd"]):
        return "4K"
    if any(q in n for q in ["1440p", "2k", "qhd"]):
        return "2K"
    if any(q in n for q in ["1080p", "fhd"]):
        return "1080p"
    if any(q in n for q in ["720p", "hd"]):
        return "720p"
    return "HD"


def format_bytes(b: int) -> str:
    gb = b / (1024**3)
    return f"{gb:.2f} GB" if gb >= 1.0 else f"{b / (1024**2):.1f} MB"


def filter_and_rank_torrents(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = []
    for it in raw_items:
        h = it.get("info_hash", "").strip().lower()
        title = it.get("name", "").strip()
        if not h or len(h) != 40 or title == "No results returned":
            continue

        try:
            seeds = int(it.get("seeders", 0))
            leech = int(it.get("leechers", 0))
            sz = int(it.get("size", 0))
        except ValueError:
            continue

        if sz < MIN_BYTES or sz > MAX_BYTES or seeds < 1:
            continue

        # Beri keutamaan (multiplier) bagi saiz antara 1GB - 4GB
        score = float(seeds)
        if SWEET_MIN <= sz <= SWEET_MAX:
            score *= 1.8

        candidates.append({
            "infoHash": h,
            "name": title,
            "title": f"{title}\n💾 {format_bytes(sz)} | 👤 {seeds} Seeds | ⚙️ TPB Direct",
            "seeders": seeds,
            "leechers": leech,
            "size_bytes": sz,
            "quality": detect_quality(title),
            "score": score
        })

    # Susun mengikut markah keutamaan & ambil 35 pilihan terbaik
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:35]


@app.get("/", tags=["Keepalive"])
async def root_ping():
    uptime = int(time.time() - SERVER_START_TIME)
    return {
        "status": "online",
        "uptime": f"{uptime // 3600}j {(uptime % 3600) // 60}m {uptime % 60}s",
        "timestamp": int(time.time())
    }


@app.get("/health", tags=["Keepalive"])
async def health_check():
    return {"status": "healthy"}


@app.get("/api/resolve-streams", tags=["Resolver"])
async def resolve_streams(type: str = Query("movie"), id: str = Query(...)):
    clean_id = id.replace(".json", "").strip()
    apibay_url = f"https://apibay.org/q.php?q={clean_id}"

    async with httpx.AsyncClient(timeout=12.0) as client:
        try:
            resp = await client.get(apibay_url, headers={"User-Agent": "Render-Resolver/2.0"})
            if resp.status_code == 200:
                raw = resp.json()
                final_streams = filter_and_rank_torrents(raw)
                return {"success": True, "streams": final_streams, "count": len(final_streams)}
            return {"success": False, "streams": [], "status_code": resp.status_code}
        except Exception as e:
            return {"success": False, "streams": [], "error": str(e)}


@app.post("/api/dispatch", tags=["Dispatcher"])
async def handle_dispatch(body: DispatchRequest, x_token: Optional[str] = Header(None, alias="X-Addon-Token")):
    if (body.secret_token or x_token) != ADDON_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Token keselamatan tidak sah.")
    if not GH_PAT:
        raise HTTPException(status_code=500, detail="GH_PAT tiada pada pelayan Render.")

    url = f"https://api.github.com/repos/{GH_OWNER}/{GH_REPO}/actions/workflows/{GH_WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Render-Dispatcher/2.0"
    }
    payload = {
        "ref": "main",
        "inputs": {
            "info_hash": body.info_hash.lower().strip(),
            "imdb_id": body.imdb_id.strip(),
            "file_idx": str(body.file_idx or "0"),
            "title": body.title or "Unknown"
        }
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(url, headers=headers, json=payload)
            if res.status_code == 204:
                return {"success": True, "message": "Trigger berjaya dihantar ke GitHub Actions."}
            return JSONResponse(status_code=502, content={"success": False, "details": res.text})
        except Exception as e:
            return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.get("/api/check-cache", tags=["Metadata"])
async def check_cache(imdb_id: str = Query(...)):
    if not REDIS_ACCOUNTS_JSON:
        return {"cached": False}
    try:
        shards = json.loads(REDIS_ACCOUNTS_JSON)
        match = re.search(r"(\d+)", imdb_id)
        idx = int(match.group(1)) % len(shards) if match else 0
        target = shards[idx]
        url = (target.get("redis_rest_url") or target.get("url", "")).rstrip("/")
        tok = target.get("redis_rest_token") or target.get("token", "")

        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url}/get/stremio:{imdb_id}", headers={"Authorization": f"Bearer {tok}"})
            if r.status_code == 200:
                raw = r.json().get("result")
                if raw:
                    return {"cached": True, "data": json.loads(raw) if isinstance(raw, str) else raw}
    except Exception:
        pass
    return {"cached": False}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("04_render_server:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))