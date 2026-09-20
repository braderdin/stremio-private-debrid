#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - EKSPERIMEN AUDIT RESOLVER & SUMBER TORRENT
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/experiments/02_resolver_diagnostic.py
# ==============================================================================

import os
import sys
import json
import time
import html
from pathlib import Path
from typing import Dict, Any, List

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

EXP_DIR = Path(__file__).resolve().parent
LIVE_ENGINE_DIR = EXP_DIR.parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
TEMP_DIR = LIVE_ENGINE_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

OUT_JSON = TEMP_DIR / "resolver_debug.json"
OUT_HTML = TEMP_DIR / "resolver_debug.html"


def load_env_vars() -> Dict[str, str]:
    env_path = PROJECT_ROOT / ".env.local"
    env_vars = {}
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars


ENV = load_env_vars()
TEST_IMDB = "tt0111161"  # The Shawshank Redemption
TEST_TITLE = "The Shawshank Redemption"

report: Dict[str, Any] = {
    "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "target": {"imdb_id": TEST_IMDB, "title": TEST_TITLE},
    "render_raw_response": {},
    "apibay_tpb": {},
    "yts_api": {},
    "tmdb_api": {},
    "generated_stream_preview": [],
    "verdict": []
}

console.print(Panel.fit(
    "[bold cyan]🔬 EKSPERIMEN DIAGNOSTIK SUMBER TORRENT & RESOLVER[/bold cyan]\n"
    "[yellow]Mengaudit punca Render 0 streams & menguji alternatif ThePirateBay + YTS + TMDB[/yellow]",
    border_style="cyan"
))

with httpx.Client(timeout=12.0, follow_redirects=True) as client:
    # -------------------------------------------------------------------------
    # 1. SEMAK JAWAPAN SEBENAR DARIPADA ENDPOINT RENDER
    # -------------------------------------------------------------------------
    render_url = f"{ENV.get('CF_WORKER_RENDER_PING', 'https://stremio-private-debrid.onrender.com').rstrip('/')}/api/resolve-streams?type=movie&id={TEST_IMDB}"
    console.print(f"\n[cyan]1. Menyemak Respons Mentah Render Resolver...[/cyan]")
    try:
        r_resp = client.get(render_url)
        r_data = r_resp.json() if r_resp.status_code == 200 else {"raw_text": r_resp.text[:200]}
        report["render_raw_response"] = {
            "http_status": r_resp.status_code,
            "json_payload": r_data
        }
        console.print(f"   Status HTTP: {r_resp.status_code} | Kandungan: {json.dumps(r_data)[:100]}...")
    except Exception as e:
        report["render_raw_response"] = {"error": str(e)}
        console.print(f"   [red]Ralat memanggil Render: {e}[/red]")

    # -------------------------------------------------------------------------
    # 2. UJI API THEPIRATEBAY RASMI (APIBAY.ORG)
    # -------------------------------------------------------------------------
    console.print(f"\n[cyan]2. Menguji Capaian ThePirateBay API (apibay.org)...[/cyan]")
    apibay_url = f"https://apibay.org/q.php?q={TEST_IMDB}"
    try:
        t_start = time.perf_counter()
        tpb_resp = client.get(apibay_url)
        t_elapsed = int((time.perf_counter() - t_start) * 1000)

        if tpb_resp.status_code == 200:
            tpb_items = tpb_resp.json()
            valid_tpb = [it for it in tpb_items if it.get("info_hash") and it.get("name") != "No results returned"]
            report["apibay_tpb"] = {
                "http_status": 200,
                "latency_ms": t_elapsed,
                "count": len(valid_tpb),
                "top_sample": valid_tpb[0] if valid_tpb else None
            }
            console.print(f"   [green]✔ TPB (apibay.org) berjaya memulangkan {len(valid_tpb)} torrent ({t_elapsed}ms)![/green]")
        else:
            report["apibay_tpb"] = {"http_status": tpb_resp.status_code, "count": 0}
            console.print(f"   [red]✖ TPB gagal: HTTP {tpb_resp.status_code}[/red]")
    except Exception as e:
        report["apibay_tpb"] = {"error": str(e)}
        console.print(f"   [red]✖ Ralat TPB: {e}[/red]")

    # -------------------------------------------------------------------------
    # 3. UJI API YTS.MX
    # -------------------------------------------------------------------------
    console.print(f"\n[cyan]3. Menguji Capaian YTS API (yts.mx)...[/cyan]")
    yts_url = f"https://yts.mx/api/v2/list_movies.json?query_term={TEST_IMDB}"
    try:
        y_start = time.perf_counter()
        yts_resp = client.get(yts_url)
        y_elapsed = int((time.perf_counter() - y_start) * 1000)

        if yts_resp.status_code == 200:
            y_data = yts_resp.json()
            movies = y_data.get("data", {}).get("movies", [])
            torrents = movies[0].get("torrents", []) if movies else []
            report["yts_api"] = {
                "http_status": 200,
                "latency_ms": y_elapsed,
                "count": len(torrents),
                "torrents": torrents
            }
            console.print(f"   [green]✔ YTS berjaya memulangkan {len(torrents)} torrent ({y_elapsed}ms)![/green]")
        else:
            report["yts_api"] = {"http_status": yts_resp.status_code, "count": 0}
            console.print(f"   [red]✖ YTS gagal: HTTP {yts_resp.status_code}[/red]")
    except Exception as e:
        report["yts_api"] = {"error": str(e)}
        console.print(f"   [red]✖ Ralat YTS: {e}[/red]")

    # -------------------------------------------------------------------------
    # 4. UJI API TMDB (DENGAN KREDENSIAL TEMPATAN)
    # -------------------------------------------------------------------------
    console.print(f"\n[cyan]4. Menguji Pengesahan TMDB API Kunci Tempatan...[/cyan]")
    tmdb_key = ENV.get("TMDB_API_KEY", "")
    if tmdb_key:
        tmdb_url = f"https://api.themoviedb.org/3/find/{TEST_IMDB}?api_key={tmdb_key}&external_source=imdb_id"
        try:
            m_resp = client.get(tmdb_url)
            if m_resp.status_code == 200:
                m_json = m_resp.json()
                results = m_json.get("movie_results", [])
                report["tmdb_api"] = {
                    "authenticated": True,
                    "movie_title": results[0].get("title") if results else None,
                    "release_date": results[0].get("release_date") if results else None
                }
                console.print(f"   [green]✔ TMDB Sah: {report['tmdb_api']['movie_title']} ({report['tmdb_api']['release_date']})[/green]")
            else:
                report["tmdb_api"] = {"authenticated": False, "status": m_resp.status_code}
                console.print(f"   [red]✖ TMDB gagal disahkan (HTTP {m_resp.status_code})[/red]")
        except Exception as e:
            report["tmdb_api"] = {"error": str(e)}
    else:
        report["tmdb_api"] = {"authenticated": False, "note": "Kunci TMDB tiada dalam .env.local"}
        console.print("   [yellow]⚠ TMDB_API_KEY tidak ditemui dalam .env.local[/yellow]")

    # -------------------------------------------------------------------------
    # 5. SIMULASI PENJANAAN BUTANG STRIM [📥 SEDUT KE B2]
    # -------------------------------------------------------------------------
    console.print(f"\n[cyan]5. Menjana Simulasi Pilihan Strim Berasaskan TPB + YTS...[/cyan]")
    simulated_streams = []

    # Ambil daripada TPB
    tpb_list = report.get("apibay_tpb", {}).get("top_sample")
    if tpb_list:
        sz_gb = f"{int(tpb_list.get('size', 0)) / (1024*1024*1024):.2f} GB"
        simulated_streams.append({
            "name": "[📥 Sedut ke B2] 1080p",
            "title": f"{tpb_list.get('name')}\n💾 {sz_gb} | 👤 {tpb_list.get('seeders')} Seeders [ThePirateBay]",
            "info_hash": tpb_list.get("info_hash")
        })

    # Ambil daripada YTS
    for y_tor in report.get("yts_api", {}).get("torrents", [])[:2]:
        simulated_streams.append({
            "name": f"[📥 Sedut ke B2] {y_tor.get('quality')}",
            "title": f"The Shawshank Redemption {y_tor.get('quality')} {y_tor.get('type')}\n💾 {y_tor.get('size')} | 👤 {y_tor.get('seeds')} Seeders [YTS]",
            "info_hash": y_tor.get("hash")
        })

    report["generated_stream_preview"] = simulated_streams
    console.print(f"   [green]✔ Berjaya membina {len(simulated_streams)} butang strim contoh untuk Stremio![/green]")

# Ringkasan Keputusan
if report["apibay_tpb"].get("count", 0) > 0 or report["yts_api"].get("count", 0) > 0:
    report["verdict"].append("Penyelesaian Sah: ThePirateBay (apibay.org) dan YTS memulangkan data tanpa halangan Cloudflare.")
    report["verdict"].append("Langkah seterusnya: Ubah Render resolver supaya menggunakan Apibay + YTS secara langsung.")

# Simpan debug JSON (<200KB)
json_str = json.dumps(report, indent=2, ensure_ascii=False)
with open(OUT_JSON, "w", encoding="utf-8") as f:
    f.write(json_str)

# Simpan debug HTML (<200KB)
html_str = f"""<!DOCTYPE html>
<html lang="ms">
<head>
  <meta charset="UTF-8"><title>Audit Alternatif Torrent</title>
  <style>
    body {{ font-family: sans-serif; background: #0b1120; color: #e2e8f0; padding: 20px; }}
    .box {{ background: #1e293b; padding: 16px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #334155; }}
    h2 {{ color: #38bdf8; margin-top: 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 8px 12px; border-bottom: 1px solid #334155; text-align: left; }}
    th {{ background: #0f172a; color: #94a3b8; }}
    pre {{ background: #0f172a; padding: 10px; border-radius: 6px; overflow-x: auto; color: #a5f3fc; }}
  </style>
</head>
<body>
  <h2>🔍 Laporan Audit Penyelesaian Resolver Alternatif</h2>
  <div class="box">
    <h3>1. Respons Sebenar Render Resolver Semasa</h3>
    <pre>{html.escape(json.dumps(report['render_raw_response'], indent=2))}</pre>
  </div>
  <div class="box">
    <h3>2. Ujian Sumber Alternatif Tanpa Sekatan Cloudflare</h3>
    <table>
      <tr><th>Sumber API</th><th>Status</th><th>Jumlah Torrent</th></tr>
      <tr><td>ThePirateBay (apibay.org)</td><td>HTTP {report['apibay_tpb'].get('http_status', 'N/A')}</td><td>{report['apibay_tpb'].get('count', 0)} fail</td></tr>
      <tr><td>YTS.mx Movies API</td><td>HTTP {report['yts_api'].get('http_status', 'N/A')}</td><td>{report['yts_api'].get('count', 0)} fail</td></tr>
      <tr><td>TMDB Pengesahan Tajuk</td><td>{'SAH' if report['tmdb_api'].get('authenticated') else 'GAGAL'}</td><td>{report['tmdb_api'].get('movie_title', 'N/A')}</td></tr>
    </table>
  </div>
  <div class="box">
    <h3>3. Contoh Butang Strim yang Boleh Dihasilkan untuk Stremio</h3>
    <pre>{html.escape(json.dumps(report['generated_stream_preview'], indent=2))}</pre>
  </div>
</body>
</html>"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html_str)

console.print(f"\n[bold green]✅ Fail laporan berjaya disimpan:[/bold green]")
console.print(f" • [yellow]{OUT_JSON}[/yellow] ({len(json_str.encode('utf-8'))/1024:.2f} KB)")
console.print(f" • [yellow]{OUT_HTML}[/yellow] ({len(html_str.encode('utf-8'))/1024:.2f} KB)\n")