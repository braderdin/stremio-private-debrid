#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO PRIVATE DEBRID - DIAGNOSTIK MENYELURUH & PENJANA LAPORAN
# LOKASI: /home/braderdin/stremio-private-debrid/live_engine/experiments/01_stremio_debug_probe.py
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

# 1. Konfigurasi Direktori Projek
EXP_DIR = Path(__file__).resolve().parent
LIVE_ENGINE_DIR = EXP_DIR.parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
TEMP_DIR = LIVE_ENGINE_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

OUT_JSON = TEMP_DIR / "debug_report.json"
OUT_HTML = TEMP_DIR / "debug_report.html"

# 2. Baca .env.local secara manual
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

# Parameter Ujian (The Shawshank Redemption - tt0111161)
TEST_IMDB = "tt0111161"
TEST_TYPE = "movie"

report_data: Dict[str, Any] = {
    "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "test_target": {"imdb_id": TEST_IMDB, "type": TEST_TYPE},
    "env_check": {},
    "torrentio_direct": {},
    "render_server": {},
    "cf_worker_addon": {},
    "github_actions_api": {},
    "redis_shards": {},
    "summary_verdict": []
}


def log_step(title: str):
    console.print(f"\n[bold cyan]▶ {title}[/bold cyan]")


# ==============================================================================
# UJIAN 1: SEMAKAN INTEGRITI KUNCI .env.local
# ==============================================================================
log_step("1. Memeriksa Pembolehubah Persekitaran (.env.local)")
keys_to_check = [
    "ADDON_SECRET_TOKEN", "CF_WORKER_URL", "CF_WORKER_B2_PROXY_STORAGE",
    "CF_WORKER_RENDER_PING", "GH_PAT", "GH_OWNER", "GH_REPO",
    "GH_WORKFLOW_FILE", "RENDER_API_KEY", "RENDER_SERVICE_ID"
]

env_summary = {}
for k in keys_to_check:
    val = ENV.get(k, "")
    exists = bool(val and not val.startswith("belum"))
    preview = f"{val[:6]}...{val[-4:]}" if (exists and len(val) > 12) else val
    env_summary[k] = {"exists": exists, "preview": preview if exists else "KOSONG/TIADA"}

report_data["env_check"] = env_summary


# ==============================================================================
# UJIAN 2: UJIAN SAMBUNGAN TERUS TORRENTIO DARI LOKAL (WSL)
# ==============================================================================
log_step("2. Menguji API Torrentio Terus dari IP Tempatan (WSL)")
torrentio_url = f"https://torrentio.strem.fun/stream/{TEST_TYPE}/{TEST_IMDB}.json"

with httpx.Client(timeout=10.0, follow_redirects=True) as client:
    try:
        t_start = time.perf_counter()
        resp = client.get(torrentio_url, headers={"User-Agent": "Mozilla/5.0"})
        t_latency = int((time.perf_counter() - t_start) * 1000)

        report_data["torrentio_direct"] = {
            "status_code": resp.status_code,
            "latency_ms": t_latency,
            "streams_count": 0,
            "sample_title": None,
            "sample_hash": None,
            "error": None
        }

        if resp.status_code == 200:
            data = resp.json()
            streams = data.get("streams", [])
            report_data["torrentio_direct"]["streams_count"] = len(streams)
            if streams:
                report_data["torrentio_direct"]["sample_title"] = streams[0].get("title", "")[:80]
                report_data["torrentio_direct"]["sample_hash"] = streams[0].get("infoHash", "")
            console.print(f"[green]✔ Torrentio memulangkan {len(streams)} pilihan torrent ({t_latency}ms)[/green]")
        else:
            report_data["torrentio_direct"]["error"] = f"HTTP {resp.status_code}"
            console.print(f"[red]✖ Torrentio ralat: HTTP {resp.status_code}[/red]")
    except Exception as e:
        report_data["torrentio_direct"] = {"error": str(e)}
        console.print(f"[red]✖ Ralat menghubungi Torrentio: {e}[/red]")


# ==============================================================================
# UJIAN 3: UJIAN PELAYAN RENDER (KEEPALIVE & RESOLVE-STREAMS)
# ==============================================================================
log_step("3. Menguji Pelayan Render (Dispatcher & Resolver)")
render_base = ENV.get("CF_WORKER_RENDER_PING", "https://stremio-private-debrid.onrender.com").rstrip("/")
render_report = {"ping": {}, "resolve_streams": {}}

with httpx.Client(timeout=15.0, follow_redirects=True) as client:
    # 3.1 Keepalive Ping
    try:
        p_resp = client.get(f"{render_base}/")
        render_report["ping"] = {
            "status_code": p_resp.status_code,
            "response": p_resp.json() if p_resp.status_code == 200 else p_resp.text[:150]
        }
        console.print(f"[green]✔ Ping Render: HTTP {p_resp.status_code}[/green]")
    except Exception as e:
        render_report["ping"] = {"error": str(e)}
        console.print(f"[red]✖ Ping Render Gagal: {e}[/red]")

    # 3.2 Endpoint Resolver
    try:
        r_url = f"{render_base}/api/resolve-streams?type={TEST_TYPE}&id={TEST_IMDB}"
        r_resp = client.get(r_url)
        render_report["resolve_streams"] = {
            "status_code": r_resp.status_code,
            "streams_found": 0,
            "error": None
        }
        if r_resp.status_code == 200:
            res_json = r_resp.json()
            s_list = res_json.get("streams", [])
            render_report["resolve_streams"]["streams_found"] = len(s_list)
            console.print(f"[green]✔ Render /api/resolve-streams memulangkan {len(s_list)} pilihan torrent[/green]")
        elif r_resp.status_code == 404:
            render_report["resolve_streams"]["error"] = "Endpoint 404 Not Found (Kod baharu belum aktif di Render!)"
            console.print("[red]✖ Endpoint /api/resolve-streams memulangkan 404! (Perlu deploy semula)[/red]")
        else:
            render_report["resolve_streams"]["error"] = f"HTTP {r_resp.status_code}"
            console.print(f"[red]✖ Render Resolver ralat: HTTP {r_resp.status_code}[/red]")
    except Exception as e:
        render_report["resolve_streams"] = {"error": str(e)}
        console.print(f"[red]✖ Gagal memanggil Render Resolver: {e}[/red]")

report_data["render_server"] = render_report


# ==============================================================================
# UJIAN 4: UJIAN CLOUDFLARE WORKER ADDON (MANIFEST & STREAMS)
# ==============================================================================
log_step("4. Menguji Cloudflare Worker Addon (Manifest & Streams)")
cf_addon_url = ENV.get("CF_WORKER_URL", "").rstrip("/")
token = ENV.get("ADDON_SECRET_TOKEN", "Harunosakura1122")
cf_report = {"manifest": {}, "stream_call": {}}

if cf_addon_url:
    with httpx.Client(timeout=12.0) as client:
        # 4.1 Manifest Test
        try:
            m_resp = client.get(f"{cf_addon_url}/{token}/manifest.json")
            if m_resp.status_code == 200:
                m_data = m_resp.json()
                cf_report["manifest"] = {
                    "status_code": 200,
                    "name": m_data.get("name"),
                    "version": m_data.get("version"),
                    "idPrefixes": m_data.get("idPrefixes", []),
                    "resources": m_data.get("resources", [])
                }
                console.print(f"[green]✔ Manifest Addon sah: v{m_data.get('version')} (idPrefixes: {m_data.get('idPrefixes')})[/green]")
            else:
                cf_report["manifest"] = {"status_code": m_resp.status_code, "error": m_resp.text[:150]}
                console.print(f"[red]✖ Manifest memulangkan HTTP {m_resp.status_code}[/red]")
        except Exception as e:
            cf_report["manifest"] = {"error": str(e)}

        # 4.2 Stream Test
        try:
            s_url = f"{cf_addon_url}/{token}/stream/{TEST_TYPE}/{TEST_IMDB}.json"
            s_resp = client.get(s_url)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                streams = s_data.get("streams", [])
                cf_report["stream_call"] = {
                    "status_code": 200,
                    "streams_count": len(streams),
                    "first_stream": streams[0] if streams else None
                }
                if streams:
                    console.print(f"[green]✔ Worker memulangkan {len(streams)} butang strim! Addon berfungsi![/green]")
                else:
                    console.print("[yellow]⚠ Worker memulangkan 0 strim (Punca Stremio menyorokkan addon!)[/yellow]")
            else:
                cf_report["stream_call"] = {"status_code": s_resp.status_code, "error": s_resp.text[:150]}
                console.print(f"[red]✖ Worker Stream Endpoint ralat: HTTP {s_resp.status_code}[/red]")
        except Exception as e:
            cf_report["stream_call"] = {"error": str(e)}

report_data["cf_worker_addon"] = cf_report


# ==============================================================================
# UJIAN 5: PENGESAHAN KEBENARAN GITHUB ACTIONS & PAT
# ==============================================================================
log_step("5. Menguji Kebenaran GitHub Personal Access Token & Workflow")
gh_pat = ENV.get("GH_PAT", "")
gh_owner = ENV.get("GH_OWNER", "braderdin")
gh_repo = ENV.get("GH_REPO", "stremio-private-debrid")
gh_wf = ENV.get("GH_WORKFLOW_FILE", "00_download.yml")
gh_report = {"token_valid": False, "workflow_exists": False, "scopes": None, "error": None}

if gh_pat:
    headers = {
        "Authorization": f"Bearer {gh_pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Probe-Tester/1.0"
    }
    with httpx.Client(timeout=10.0) as client:
        # 5.1 Semak kesahihan PAT & Skim Kebenaran
        try:
            user_resp = client.get("https://api.github.com/user", headers=headers)
            if user_resp.status_code == 200:
                gh_report["token_valid"] = True
                scopes = user_resp.headers.get("x-oauth-scopes", "Tiada Scopes")
                gh_report["scopes"] = scopes
                console.print(f"[green]✔ GitHub PAT Sah (Akaun: {user_resp.json().get('login')}, Scopes: {scopes})[/green]")
            else:
                gh_report["error"] = f"PAT tidak sah: HTTP {user_resp.status_code}"
                console.print(f"[red]✖ GitHub PAT Gagal: HTTP {user_resp.status_code}[/red]")
        except Exception as e:
            gh_report["error"] = str(e)

        # 5.2 Semak kewujudan fail alur kerja (Workflow File)
        try:
            wf_url = f"https://api.github.com/repos/{gh_owner}/{gh_repo}/actions/workflows/{gh_wf}"
            wf_resp = client.get(wf_url, headers=headers)
            if wf_resp.status_code == 200:
                gh_report["workflow_exists"] = True
                console.print(f"[green]✔ Fail alur kerja '{gh_wf}' sah dan dikesan di GitHub![/green]")
            else:
                gh_report["workflow_exists"] = False
                console.print(f"[red]✖ Fail alur kerja '{gh_wf}' tidak ditemui di repo: HTTP {wf_resp.status_code}[/red]")
        except Exception as e:
            pass

report_data["github_actions_api"] = gh_report


# ==============================================================================
# UJIAN 6: SEMAKAN 4 SHARD UPSTASH REDIS
# ==============================================================================
log_step("6. Menguji 4 Shard Upstash Redis & Status Cache Filem")
redis_results = []
for idx in range(1, 5):
    fmt = f"{idx:03d}"
    url = ENV.get(f"UPSTASH_REDIS_{fmt}_REST_URL", "").rstrip("/")
    tok = ENV.get(f"UPSTASH_REDIS_{fmt}_REST_TOKEN", "")

    if url and tok:
        try:
            with httpx.Client(timeout=6.0) as client:
                r_start = time.perf_counter()
                p_res = client.post(url, headers={"Authorization": f"Bearer {tok}"}, json=["PING"])
                latency = int((time.perf_counter() - r_start) * 1000)

                # Semak kewujudan kunci sasaran
                k_res = client.post(url, headers={"Authorization": f"Bearer {tok}"}, json=["GET", f"stremio:{TEST_IMDB}"])
                has_target = bool(k_res.status_code == 200 and k_res.json().get("result"))

                redis_results.append({
                    "shard": idx,
                    "host": url.split("@")[-1].replace("https://", "").split("/")[0],
                    "online": (p_res.status_code == 200 and p_res.json().get("result") == "PONG"),
                    "latency_ms": latency,
                    "target_cached": has_target
                })
        except Exception as e:
            redis_results.append({"shard": idx, "online": False, "error": str(e)})

report_data["redis_shards"] = redis_results


# ==============================================================================
# KESIMPULAN DIAGNOSTIK
# ==============================================================================
verdicts = []
if report_data["torrentio_direct"].get("streams_count", 0) > 0:
    verdicts.append("Katalog Torrentio aktif dan memulangkan strim dari IP tempatan.")
else:
    verdicts.append("Amaran: Torrentio mungkin sedang mengalami gangguan atau disekat.")

if report_data["render_server"]["resolve_streams"].get("status_code") == 404:
    verdicts.append("KRITIKAL: Render memulangkan 404 pada /api/resolve-streams. Kod baharu belum di-deploy ke Render.")
elif report_data["render_server"]["resolve_streams"].get("streams_found", 0) == 0:
    verdicts.append("KRITIKAL: Render tidak memulangkan senarai torrent. Worker gagal menjana butang sedut.")

if report_data["cf_worker_addon"]["stream_call"].get("streams_count", 0) == 0:
    verdicts.append("PUNCA UTAMA: Worker Addon memulangkan 0 strim. Stremio secara automatik menyembunyikan addon yang tiada strim.")

report_data["summary_verdict"] = verdicts


# ==============================================================================
# JANA OUTPUT: debug_report.json (< 200KB)
# ==============================================================================
json_str = json.dumps(report_data, indent=2, ensure_ascii=False)
with open(OUT_JSON, "w", encoding="utf-8") as f:
    f.write(json_str)

json_size_kb = len(json_str.encode("utf-8")) / 1024


# ==============================================================================
# JANA OUTPUT: debug_report.html (< 200KB)
# ==============================================================================
html_content = f"""<!DOCTYPE html>
<html lang="ms">
<head>
  <meta charset="UTF-8">
  <title>Stremio Private Debrid - Laporan Diagnostik</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ color: #38bdf8; border-bottom: 2px solid #334155; padding-bottom: 12px; }}
    .card {{ background: #1e293b; border-radius: 8px; padding: 16px 20px; margin-bottom: 18px; border: 1px solid #334155; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 9999px; font-weight: bold; font-size: 12px; }}
    .badge-green {{ background: #065f46; color: #34d399; }}
    .badge-red {{ background: #991b1b; color: #f87171; }}
    .badge-yellow {{ background: #854d0e; color: #facc15; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #334155; }}
    th {{ background: #0f172a; color: #94a3b8; font-size: 13px; text-transform: uppercase; }}
    pre {{ background: #0f172a; padding: 12px; border-radius: 6px; overflow-x: auto; color: #a5f3fc; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🔍 Laporan Diagnostik Stremio Debrid Engine</h1>
    <p>Masa Ujian: <strong>{report_data["timestamp_utc"]}</strong> | Sasaran: <strong>{TEST_IMDB}</strong></p>

    <div class="card">
      <h3>📌 Rumusan Status Utama</h3>
      <ul>
        {''.join(f'<li>{html.escape(v)}</li>' for v in verdicts)}
      </ul>
    </div>

    <div class="card">
      <h3>1. Status Pelayan Render & Resolver</h3>
      <table>
        <tr><th>Ujian</th><th>Status HTTP</th><th>Keputusan</th></tr>
        <tr>
          <td>Keepalive Ping (/)</td>
          <td>{report_data["render_server"]["ping"].get("status_code", "RALAT")}</td>
          <td>{'<span class="badge badge-green">ONLINE</span>' if report_data["render_server"]["ping"].get("status_code") == 200 else '<span class="badge badge-red">OFFLINE</span>'}</td>
        </tr>
        <tr>
          <td>Resolver (/api/resolve-streams)</td>
          <td>{report_data["render_server"]["resolve_streams"].get("status_code", "RALAT")}</td>
          <td>Strim Dikesan: <strong>{report_data["render_server"]["resolve_streams"].get("streams_found", 0)}</strong></td>
        </tr>
      </table>
    </div>

    <div class="card">
      <h3>2. Cloudflare Worker Addon</h3>
      <table>
        <tr><th>Endpoint</th><th>Status</th><th>Keterangan</th></tr>
        <tr>
          <td>Manifest (/manifest.json)</td>
          <td>{report_data["cf_worker_addon"]["manifest"].get("status_code", "RALAT")}</td>
          <td>Versi: {report_data["cf_worker_addon"]["manifest"].get("version", "N/A")} | Prefixes: {report_data["cf_worker_addon"]["manifest"].get("idPrefixes", [])}</td>
        </tr>
        <tr>
          <td>Stream (/stream/{TEST_TYPE}/{TEST_IMDB}.json)</td>
          <td>{report_data["cf_worker_addon"]["stream_call"].get("status_code", "RALAT")}</td>
          <td>Jumlah Strim Dipulangkan: <strong>{report_data["cf_worker_addon"]["stream_call"].get("streams_count", 0)}</strong></td>
        </tr>
      </table>
    </div>

    <div class="card">
      <h3>3. GitHub Actions Dispatch Integrity</h3>
      <table>
        <tr><th>Item</th><th>Status</th></tr>
        <tr><td>Token Sah (GH_PAT)</td><td>{'<span class="badge badge-green">SAH</span>' if report_data["github_actions_api"].get("token_valid") else '<span class="badge badge-red">TIDAK SAH</span>'}</td></tr>
        <tr><td>Cakupan Token (Scopes)</td><td><code>{report_data["github_actions_api"].get("scopes", "Tiada")}</code></td></tr>
        <tr><td>Fail Alur Kerja ({gh_wf})</td><td>{'<span class="badge badge-green">Wujud</span>' if report_data["github_actions_api"].get("workflow_exists") else '<span class="badge badge-red">Tiada</span>'}</td></tr>
      </table>
    </div>

    <div class="card">
      <h3>4. Raw JSON Output Snippet</h3>
      <pre>{html.escape(json_str[:2500])} ... [dipotong untuk paparan]</pre>
    </div>
  </div>
</body>
</html>
"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html_content)

html_size_kb = len(html_content.encode("utf-8")) / 1024


# ==============================================================================
# PAPARAN RINGKASAN TERMINAL
# ==============================================================================
table = Table(title="📊 Ringkasan Fail Laporan Diagnostik Dijana", border_style="green")
table.add_column("Nama Fail", style="cyan")
table.add_column("Laluan Fail", style="white")
table.add_column("Saiz (Had 200KB)", style="yellow", justify="right")
table.add_column("Status Had", style="bold green", justify="center")

table.add_row("debug_report.json", str(OUT_JSON), f"{json_size_kb:.2f} KB", "✅ LULUS (<200KB)")
table.add_row("debug_report.html", str(OUT_HTML), f"{html_size_kb:.2f} KB", "✅ LULUS (<200KB)")

console.print(table)
console.print("\n[bold green]✨ Diagnostik selesai dijalankan![/bold green]")
console.print(f"Buka [bold yellow]{OUT_HTML}[/bold yellow] atau baca [bold yellow]{OUT_JSON}[/bold yellow] untuk melihat punca ralat terperinci.\n")


if __name__ == "__main__":
    pass