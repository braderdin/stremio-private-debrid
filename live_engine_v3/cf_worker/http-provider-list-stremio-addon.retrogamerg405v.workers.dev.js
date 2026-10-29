/**
 * ==============================================================================
 * PROJEK: STREMIO HTTP FAST PROVIDER - DEDICATED ADDON
 * WORKER: http-provider-stremio-addon.retrogamerg405v.workers.dev
 * CIRI-CIRI:
 * 1. Khusus untuk Sumber Penstriman Terus HTTP (PenguPlay, MovieBox, HDHub4u, dsb).
 * 2. 100% Bebas daripada Rangkaian P2P Torrent (Antaramuka Bersih & Fokus).
 * 3. Berkongsi Storan B2 (50 Baldi) & Redis (4 Shard) yang sama dengan Addon V3.
 * 4. Memacu Workflow Khusus:
 *    - /trigger_list     -> X10_http_stream_list.yml
 *    - /trigger_download -> X11_http_download.yml
 * ==============================================================================
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function calculateCRC32(str) {
  let crc = 0 ^ (-1);
  for (let i = 0; i < str.length; i++) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ str.charCodeAt(i)) & 0xFF];
  }
  return ((crc ^ (-1)) >>> 0);
}

let cachedRedisAccounts = null;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const pathParts = url.pathname.split("/").filter(Boolean);

    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Content-Type": "application/json; charset=utf-8",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    // 1. Pemeriksaan Token Laluan
    const requestToken = pathParts[0];
    const validToken = env.ADDON_SECRET_TOKEN || "Harunosakura1122";

    if (!requestToken || requestToken !== validToken) {
      return new Response(JSON.stringify({ error: "Akses tidak dibenarkan" }), {
        status: 401,
        headers: corsHeaders,
      });
    }

    const route = pathParts[1];

    // 2. Endpoint Manifest (/manifest.json)
    if (route === "manifest.json" || pathParts.length === 1) {
      const manifest = {
        id: "org.braderdin.http.provider.v1",
        version: "1.0.0",
        name: "B2 HTTP Fast Provider (Direct Streams)",
        description: "Enjin Penstriman Terus HTTP & DASH/HLS Tanpa Bergantung Seeder",
        resources: ["stream"],
        types: ["movie", "series"],
        idPrefixes: ["tt"],
        catalogs: [],
      };
      return new Response(JSON.stringify(manifest), { headers: corsHeaders });
    }

    // 3. Endpoint Pemicu 1: Cari Strim HTTP (/trigger_list)
    if (route === "trigger_list") {
      const rawId = url.searchParams.get("id") || "";
      const imdbId = decodeURIComponent(rawId).trim();
      const title = url.searchParams.get("title") || imdbId;

      if (imdbId) {
        const accounts = getRedisAccounts(env);
        const shard = getTargetRedisShard(imdbId, accounts);
        const lockKey = `lock:http:list:${imdbId}`;

        const locked = await acquireRedisLock(shard, lockKey, 90);
        if (locked) {
          ctx.waitUntil(triggerGitHubHttpListRunner(env, imdbId, title));
        }
      }

      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 4. Endpoint Pemicu 2: Muat Turun Strim HTTP ke B2 (/trigger_download)
    if (route === "trigger_download") {
      const targetHash = (url.searchParams.get("hash") || "").toLowerCase().trim();
      const rawId = url.searchParams.get("id") || "";
      const imdbId = decodeURIComponent(rawId).trim();
      const title = url.searchParams.get("title") || "video";

      if (targetHash && imdbId) {
        const accounts = getRedisAccounts(env);
        const shard = getTargetRedisShard(imdbId, accounts);
        const lockKey = `lock:http:download:${targetHash}:${imdbId}`;

        const locked = await acquireRedisLock(shard, lockKey, 120);
        if (locked) {
          ctx.waitUntil(triggerGitHubHttpDownloadRunner(env, targetHash, imdbId, title));
        }
      }

      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 5. Endpoint Stream Resolver (/stream/{type}/{id}.json)
    if (route === "stream" && pathParts.length >= 4) {
      const rawId = decodeURIComponent(pathParts[3].replace(".json", "")).trim();
      const proxyBase = (env.CF_WORKER_B2_PROXY_STORAGE || "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev").replace(/\/+$/, "");
      const workerBase = url.origin;

      try {
        const accounts = getRedisAccounts(env);
        const targetShard = getTargetRedisShard(rawId, accounts);

        // Ambil status siap B2 & senarai cache HTTP daripada Redis
        const [cachedMeta, cachedList] = await Promise.all([
          fetchRedisData(targetShard, `stremio:${rawId}`),
          fetchRedisData(targetShard, `stremio:list:${rawId}`)
        ]);

        const streamResults = [];
        const activeHashes = new Set();

        // BAGIAN A: Strim Siap di B2 (Multi-Stream)
        let readyStreams = [];
        if (cachedMeta) {
          if (Array.isArray(cachedMeta.streams) && cachedMeta.streams.length > 0) {
            readyStreams = cachedMeta.streams;
          } else if (cachedMeta.b2_bucket && cachedMeta.file_path) {
            readyStreams = [cachedMeta];
          }
        }

        for (const s of readyStreams) {
          if (s.info_hash) {
            activeHashes.add(s.info_hash.toLowerCase().trim());
          }

          if (s.b2_bucket && s.file_path) {
            const streamUrl = `${proxyBase}/${s.b2_bucket}/${s.file_path.replace(/^\/+/, "")}`;
            const resTag = s.resolution ? ` [${s.resolution}]` : "";
            const sizeTag = s.size_bytes ? ` | 💾 ${formatBytes(s.size_bytes)}` : "";

            streamResults.push({
              name: `[⚡ B2 Fast Stream]${resTag}`,
              title: `${s.title || rawId}${sizeTag}\n💾 Server: ${s.b2_bucket}\n⚡ Siap Ditonton Terus Tanpa Buffering`,
              url: streamUrl,
              behaviorHints: { bingeGroup: `b2-ready-http-${rawId}-${s.info_hash || 'def'}`, notWebReady: false }
            });
          }
        }

        // BAGIAN B: Senarai Pilihan HTTP Terus (HANYA tapis entri dengan stream_type == 'http')
        if (Array.isArray(cachedList) && cachedList.length > 0) {
          for (const item of cachedList) {
            // Abaikan torrent P2P biasa di addon ini
            if (item.stream_type !== "http") continue;

            const itemHash = (item.info_hash || "").toLowerCase().trim();
            if (itemHash && activeHashes.has(itemHash)) continue;

            const cleanTitle = (item.name || "HTTP Stream").replace(/[^\w\s\.\-\[\]\(\)]/g, "");
            const sizeText = formatBytes(item.size || 0);
            const q = item.quality || "HD";
            const fmt = item.stream_format || "MP4";
            const src = item.source || "PenguPlay";

            const downloadUrl = `${workerBase}/${requestToken}/trigger_download?hash=${item.info_hash}&id=${encodeURIComponent(rawId)}&title=${encodeURIComponent(cleanTitle)}`;

            streamResults.push({
              name: `[📥 Sedut B2] ${q} (${fmt})`,
              title: `${item.name}\n💾 ${sizeText}  |  🚀 Direct HTTP  |  🌐 ${src}\n⚡ Klik untuk Mengunduh ke B2`,
              url: downloadUrl,
              behaviorHints: { bingeGroup: `b2-http-choice-${rawId}`, notWebReady: false }
            });
          }
        }

        // BAGIAN C: Butang Tindakan (Refresh atau Cari Strim HTTP)
        if (streamResults.length > 0) {
          const refreshUrl = `${workerBase}/${requestToken}/trigger_list?id=${encodeURIComponent(rawId)}&title=${encodeURIComponent(rawId)}`;
          streamResults.push({
            name: `[🔄 Perbarui Senarai]`,
            title: `Segarkan pencarian strim HTTP terkini untuk ${rawId}`,
            url: refreshUrl,
            behaviorHints: { bingeGroup: `b2-http-refresh-${rawId}`, notWebReady: false }
          });
        } else {
          const listTriggerUrl = `${workerBase}/${requestToken}/trigger_list?id=${encodeURIComponent(rawId)}&title=${encodeURIComponent(rawId)}`;
          streamResults.push({
            name: `[🌐 Ambil Strim HTTP]`,
            title: `Sasaran: ${rawId}\n⚡ Klik untuk menjalankan Runner mencari pautan HTTP terus (PenguPlay, HDHub4u, MovieBox)\n(Tunggu ~20 saat dan buka semula media ini)`,
            url: listTriggerUrl,
            behaviorHints: { bingeGroup: `b2-http-fetch-${rawId}`, notWebReady: false }
          });
        }

        return new Response(JSON.stringify({ streams: streamResults }), { headers: corsHeaders });

      } catch (err) {
        console.error("Kesalahan HTTP Stream Resolver:", err);
      }

      return new Response(JSON.stringify({ streams: [] }), { headers: corsHeaders });
    }

    return new Response(JSON.stringify({ error: "Not Found" }), { status: 404, headers: corsHeaders });
  },
};

function getTargetRedisShard(keyIdentifier, accounts) {
  if (!accounts || accounts.length === 0) return null;
  const baseId = keyIdentifier.split(":")[0];
  const checksum = calculateCRC32(baseId);
  return accounts[checksum % accounts.length];
}

function getRedisAccounts(env) {
  if (cachedRedisAccounts && cachedRedisAccounts.length > 0) return cachedRedisAccounts;
  let accounts = [];
  const rawJson = (env.REDIS_ACCOUNTS_JSON || "").trim();

  if (rawJson) {
    try {
      const parsed = JSON.parse(rawJson);
      if (Array.isArray(parsed)) {
        accounts = parsed.map((acc, idx) => ({
          index: acc.index || idx + 1,
          url: (acc.redis_rest_url || acc.url).trim().replace(/\/+$/, ""),
          token: (acc.redis_rest_token || acc.token).trim(),
        }));
        accounts.sort((a, b) => a.index - b.index);
      }
    } catch (e) {
      console.error("Gagal membaca REDIS_ACCOUNTS_JSON:", e);
    }
  }
  cachedRedisAccounts = accounts;
  return accounts;
}

async function acquireRedisLock(account, lockKey, ttlSeconds) {
  if (!account || !account.url || !account.token) return true;
  try {
    const res = await fetch(`${account.url}/`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${account.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(["SET", lockKey, "locked", "EX", ttlSeconds, "NX"]),
    });
    if (!res.ok) return true;
    const data = await res.json();
    return data && data.result === "OK";
  } catch (e) {
    return true;
  }
}

async function fetchRedisData(account, key) {
  if (!account || !account.url || !account.token) return null;
  try {
    const res = await fetch(`${account.url}/get/${key}`, {
      headers: { Authorization: `Bearer ${account.token}` },
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (data && data.result) {
      return typeof data.result === "string" ? JSON.parse(data.result) : data.result;
    }
  } catch (e) {}
  return null;
}

function formatBytes(bytes) {
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1.0) return `${gb.toFixed(2)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

async function triggerGitHubHttpListRunner(env, imdbId, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE_X10 || "X10_http_stream_list.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "CF-Worker-HTTP-List-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { imdb_id: imdbId, title: title },
      }),
    });
  } catch (e) {
    console.error("Kesalahan Trigger HTTP List Runner:", e);
  }
}

async function triggerGitHubHttpDownloadRunner(env, hash, imdbId, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE_X11 || "X11_http_download.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "CF-Worker-HTTP-Download-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          info_hash: hash,
          imdb_id: imdbId,
          title: title,
        },
      }),
    });
  } catch (e) {
    console.error("Kesalahan Trigger HTTP Download Runner:", e);
  }
}