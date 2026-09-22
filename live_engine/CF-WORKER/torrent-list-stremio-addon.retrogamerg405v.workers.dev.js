/**
 * ==============================================================================
 * PROJEK: STREMIO PRIVATE DEBRID V2 - SMART TORRENT PICKER (MULTI-STREAM)
 * WORKER: torrent-list-stremio-addon.retrogamerg405v.workers.dev
 * CIRI: 
 * 1. Sokongan Penuh Multi-Stream: Papar semua kualiti sedia tonton di B2.
 * 2. Penapisan Hash Pintar: Halang torrent sedia ada muncul semula di butang sedut.
 * 3. Kunci Penyejuk Atomik Redis 2 Minit (120s) untuk menyekat pemicu berganda TV.
 * 4. Butang Kemas Kini Senarai (Refresh) sentiasa tersedia di bawah sekali.
 * ==============================================================================
 */

// Jadual CRC32 rasmi (100% sepadan dengan modul Python zlib.crc32 & 01_redis_db.py)
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

    // 1. Semakan Keselamatan Token URL
    const requestToken = pathParts[0];
    const validToken = env.ADDON_SECRET_TOKEN || "Harunosakura1122";

    if (!requestToken || requestToken !== validToken) {
      return new Response(JSON.stringify({ error: "Akses tanpa kebenaran" }), {
        status: 401,
        headers: corsHeaders,
      });
    }

    const route = pathParts[1];

    // 2. Endpoint Manifest (/manifest.json)
    if (route === "manifest.json" || pathParts.length === 1) {
      const manifest = {
        id: "org.braderdin.privatedebrid.v2",
        version: "2.2.0",
        name: "B2 Debrid V2 (Smart Picker)",
        description: "Pilih Kualiti & Seeder Terbanyak Sebelum Muat Turun ke B2",
        resources: ["stream"],
        types: ["movie", "series"],
        idPrefixes: ["tt"],
        catalogs: [],
      };
      return new Response(JSON.stringify(manifest), { headers: corsHeaders });
    }

    // 3. Endpoint Pemicu 1: Dapatkan Senarai Torrent (/trigger_list)
    if (route === "trigger_list") {
      const imdbId = url.searchParams.get("id");
      const title = url.searchParams.get("title") || imdbId;

      if (imdbId) {
        const accounts = getRedisAccounts(env);
        const shard = getTargetRedisShard(imdbId, accounts);
        const lockKey = `lock:list:${imdbId}`;

        // Kunci Atomik 2 Minit (120 saat)
        const locked = await acquireRedisLock(shard, lockKey, 120);
        if (locked) {
          ctx.waitUntil(triggerGitHubListRunner(env, imdbId, title));
        }
      }

      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 4. Endpoint Pemicu 2: Muat Turun Torrent Terpilih (/trigger_download)
    if (route === "trigger_download") {
      const targetHash = (url.searchParams.get("hash") || "").toLowerCase().trim();
      const imdbId = url.searchParams.get("id");
      const title = url.searchParams.get("title") || "video";

      if (targetHash && imdbId) {
        const accounts = getRedisAccounts(env);
        const shard = getTargetRedisShard(imdbId, accounts);
        const lockKey = `lock:download:${targetHash}`;

        // Kunci Atomik 2 Minit (120 saat)
        const locked = await acquireRedisLock(shard, lockKey, 120);
        if (locked) {
          ctx.waitUntil(triggerGitHubDownloadRunner(env, targetHash, imdbId, title));
        }
      }

      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 5. Endpoint Stream Resolver (/stream/{type}/{id}.json)
    if (route === "stream" && pathParts.length >= 4) {
      const rawId = pathParts[3].replace(".json", "");
      const proxyBase = (env.CF_WORKER_B2_PROXY_STORAGE || "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev").replace(/\/+$/, "");
      const workerBase = url.origin;

      try {
        const accounts = getRedisAccounts(env);
        const targetShard = getTargetRedisShard(rawId, accounts);

        // Ambil serentak: Status fail siap di B2 & Senarai torrent
        const [cachedMeta, cachedList] = await Promise.all([
          fetchRedisData(targetShard, `stremio:${rawId}`),
          fetchRedisData(targetShard, `stremio:list:${rawId}`)
        ]);

        const streamResults = [];
        const activeHashes = new Set();

        // BAHAGIAN A: Ekstrak semua versi siap di B2 (Multi-Stream)
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
              title: `${s.title || rawId}${sizeTag}\n💾 Server: ${s.b2_bucket}\n⚡ Sedia Ditonton Berkelajuan Tinggi`,
              url: streamUrl,
              behaviorHints: { bingeGroup: `b2-ready-${rawId}-${s.info_hash || 'def'}`, notWebReady: false }
            });
          }
        }

        // BAHAGIAN B: Jika senarai torrent wujud, paparkan pilihan kualiti lain (tapis hash sedia ada)
        if (Array.isArray(cachedList) && cachedList.length > 0) {
          for (const item of cachedList) {
            const itemHash = (item.info_hash || "").toLowerCase().trim();
            if (itemHash && activeHashes.has(itemHash)) continue; // Langkau jika versi ini sudah ada di B2

            const cleanTitle = (item.name || "Torrent").replace(/[^\w\s\.\-]/g, "");
            const sizeText = formatBytes(item.size || 0);
            const seeds = item.seeders || 0;
            const q = item.quality || "HD";

            const downloadUrl = `${workerBase}/${requestToken}/trigger_download?hash=${item.info_hash}&id=${rawId}&title=${encodeURIComponent(cleanTitle)}`;

            streamResults.push({
              name: `[📥 Sedut B2] ${q}`,
              title: `${item.name}\n💾 ${sizeText}  |  👤 ${seeds} Seeders  |  ⚡ Klik untuk Muat Turun`,
              url: downloadUrl,
              behaviorHints: { bingeGroup: `b2-v2-choice-${rawId}`, notWebReady: false }
            });
          }
        }

        // BAHAGIAN C: Butang navigasi (Refresh jika ada data, atau Dapatkan Senarai jika kosong)
        if (streamResults.length > 0) {
          const refreshUrl = `${workerBase}/${requestToken}/trigger_list?id=${rawId}&title=${rawId}`;
          streamResults.push({
            name: `[🔄 Kemas Kini Senarai]`,
            title: `Segarkan semula senarai torrent daripada pencarian baru (Apibay + Torrentio)`,
            url: refreshUrl,
            behaviorHints: { bingeGroup: `b2-v2-refresh-${rawId}`, notWebReady: false }
          });
        } else {
          const listTriggerUrl = `${workerBase}/${requestToken}/trigger_list?id=${rawId}&title=${rawId}`;
          streamResults.push({
            name: `[📋 Dapatkan Senarai Torrent]`,
            title: `IMDb: ${rawId}\n⚡ Klik untuk arahkan GitHub Runner ekstrak 20-35 senarai seeder tertinggi\n(Tunggu 20 saat kemudian buka semula skrin ini)`,
            url: listTriggerUrl,
            behaviorHints: { bingeGroup: `b2-v2-fetch-${rawId}`, notWebReady: false }
          });
        }

        return new Response(JSON.stringify({ streams: streamResults }), { headers: corsHeaders });

      } catch (err) {
        console.error("Ralat Penghala Stream V2:", err);
      }

      return new Response(JSON.stringify({ streams: [] }), { headers: corsHeaders });
    }

    return new Response(JSON.stringify({ error: "Not Found" }), { status: 404, headers: corsHeaders });
  },
};

/**
 * Sharding Upstash Redis CRC32 Modulo
 */
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

function getTargetRedisShard(keyIdentifier, accounts) {
  if (!accounts || accounts.length === 0) return null;
  const checksum = calculateCRC32(keyIdentifier);
  return accounts[checksum % accounts.length];
}

/**
 * Kunci Atomik Redis (SET key value EX seconds NX)
 */
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

/**
 * Pemicu GitHub Actions 1: Runner Senarai Pantas (02_movie_seeder_list.yml)
 */
async function triggerGitHubListRunner(env, imdbId, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE_V2 || "02_movie_seeder_list.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "CF-Worker-V2-List-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { imdb_id: imdbId, title: title },
      }),
    });
  } catch (e) {
    console.error("Ralat Trigger List Runner:", e);
  }
}

/**
 * Pemicu GitHub Actions 2: Runner Muat Turun Video (00_download.yml)
 */
async function triggerGitHubDownloadRunner(env, hash, imdbId, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE || "00_download.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "CF-Worker-V2-Download-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          info_hash: hash,
          imdb_id: imdbId,
          file_idx: "0",
          title: title,
        },
      }),
    });
  } catch (e) {
    console.error("Ralat Trigger Download Runner:", e);
  }
}