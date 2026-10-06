/**
 * ==============================================================================
 * PROJEK: STREMIO PRIVATE DEBRID V3 - SERIES & MOVIE SMART PICKER
 * WORKER: series-list-stremio-addon.retrogamerg405v.workers.dev
 * CIRI UTAMA:
 * 1. Mendukung Penuh Serial TV (tt...:S:E) & Film (tt...) tanpa bentrok.
 * 2. Penanganan Otomatis URL-Encoding Stremio (%3A -> :).
 * 3. Sharding Konsisten CRC32 berbasis Base IMDb ID (Semua episode 1 serial di Shard sama).
 * 4. Mendukung Parameter file_idx untuk unduhan episode tertentu dalam Season Pack.
 * 5. Multi-Stream B2 Display & Penyaringan Hash Aktif.
 * 6. Memanggil Workflow X03_series_seeder_list.yml & X04_series_download.yml.
 * ==============================================================================
 */

// Tabel CRC32 resmi (100% cocok dengan modul Python zlib.crc32 & 01_redis_db.py)
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

    // 1. Pemeriksaan Keamanan Token URL
    const requestToken = pathParts[0];
    const validToken = env.ADDON_SECRET_TOKEN || "Harunosakura1122";

    if (!requestToken || requestToken !== validToken) {
      return new Response(JSON.stringify({ error: "Akses tidak diizinkan" }), {
        status: 401,
        headers: corsHeaders,
      });
    }

    const route = pathParts[1];

    // 2. Endpoint Manifest (/manifest.json)
    if (route === "manifest.json" || pathParts.length === 1) {
      const manifest = {
        id: "org.braderdin.privatedebrid.series.v3",
        version: "3.0.0",
        name: "B2 Debrid V3 (Series & Movies)",
        description: "Smart Picker Multi-Stream B2 untuk Serial TV & Film",
        resources: ["stream"],
        types: ["movie", "series"],
        idPrefixes: ["tt"],
        catalogs: [],
      };
      return new Response(JSON.stringify(manifest), { headers: corsHeaders });
    }

    // 3. Endpoint Pemicu 1: Ambil Daftar Torrent (/trigger_list)
    if (route === "trigger_list") {
      const rawId = url.searchParams.get("id") || "";
      const imdbId = decodeURIComponent(rawId).trim();
      const title = url.searchParams.get("title") || imdbId;

      if (imdbId) {
        const accounts = getRedisAccounts(env);
        const shard = getTargetRedisShard(imdbId, accounts);
        const lockKey = `lock:list:${imdbId}`;

        // Kunci Atomik Redis 2 Menit (120 detik)
        const locked = await acquireRedisLock(shard, lockKey, 120);
        if (locked) {
          ctx.waitUntil(triggerGitHubListRunner(env, imdbId, title));
        }
      }

      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 4. Endpoint Pemicu 2: Unduh Torrent Pilihan (/trigger_download)
    if (route === "trigger_download") {
      const targetHash = (url.searchParams.get("hash") || "").toLowerCase().trim();
      const rawId = url.searchParams.get("id") || "";
      const imdbId = decodeURIComponent(rawId).trim();
      const fileIdx = url.searchParams.get("idx") || "0";
      const title = url.searchParams.get("title") || "video";

      if (targetHash && imdbId) {
        const accounts = getRedisAccounts(env);
        const shard = getTargetRedisShard(imdbId, accounts);
        const lockKey = `lock:download:${targetHash}:${imdbId}`;

        // Kunci Atomik Redis 2 Menit (120 detik)
        const locked = await acquireRedisLock(shard, lockKey, 120);
        if (locked) {
          ctx.waitUntil(triggerGitHubDownloadRunner(env, targetHash, imdbId, fileIdx, title));
        }
      }

      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 5. Endpoint Stream Resolver (/stream/{type}/{id}.json)
    if (route === "stream" && pathParts.length >= 4) {
      // Decode %3A kembali menjadi karakter titik dua (:)
      const rawId = decodeURIComponent(pathParts[3].replace(".json", "")).trim();
      const proxyBase = (env.CF_WORKER_B2_PROXY_STORAGE || "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev").replace(/\/+$/, "");
      const workerBase = url.origin;

      try {
        const accounts = getRedisAccounts(env);
        const targetShard = getTargetRedisShard(rawId, accounts);

        // Ambil data status siap B2 & senarai cache torrent secara serentak
        const [cachedMeta, cachedList] = await Promise.all([
          fetchRedisData(targetShard, `stremio:${rawId}`),
          fetchRedisData(targetShard, `stremio:list:${rawId}`)
        ]);

        const streamResults = [];
        const activeHashes = new Set();

        // BAGIAN A: Ekstrak semua versi siap tonton di B2 (Multi-Stream)
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
              title: `${s.title || rawId}${sizeTag}\n💾 Server: ${s.b2_bucket}\n⚡ Siap Ditonton Berkecepatan Tinggi`,
              url: streamUrl,
              behaviorHints: { bingeGroup: `b2-ready-${rawId}-${s.info_hash || 'def'}`, notWebReady: false }
            });
          }
        }

        // BAGIAN B: Tampilkan daftar torrent yang tersedia (Saring info_hash yang sudah ada di B2)
        if (Array.isArray(cachedList) && cachedList.length > 0) {
          for (const item of cachedList) {
            const itemHash = (item.info_hash || "").toLowerCase().trim();
            if (itemHash && activeHashes.has(itemHash)) continue;

            const cleanTitle = (item.name || "Torrent").replace(/[^\w\s\.\-]/g, "");
            const sizeText = formatBytes(item.size || 0);
            const seeds = item.seeders || 0;
            const q = item.quality || "HD";
            const fIdx = item.file_idx !== undefined ? item.file_idx : 0;

            const sourceTag = item.source ? `🌐 ${item.source}` : "🌐 Torrent";
            const downloadUrl = `${workerBase}/${requestToken}/trigger_download?hash=${item.info_hash}&id=${encodeURIComponent(rawId)}&idx=${fIdx}&title=${encodeURIComponent(cleanTitle)}`;

            streamResults.push({
              name: `[📥 Sedut B2] ${q}`,
              title: `${item.name}\n💾 ${sizeText}  |  👤 ${seeds} Seeds  |  ${sourceTag}\n⚡ Klik untuk Mengunduh ke B2`,
              url: downloadUrl,
              behaviorHints: { bingeGroup: `b2-v3-choice-${rawId}`, notWebReady: false }
            });
          }
        }

        // BAGIAN C: Tombol Navigasi (Refresh jika sudah ada data, atau Ambil Daftar jika kosong)
        if (streamResults.length > 0) {
          const refreshUrl = `${workerBase}/${requestToken}/trigger_list?id=${encodeURIComponent(rawId)}&title=${encodeURIComponent(rawId)}`;
          streamResults.push({
            name: `[🔄 Perbarui Daftar]`,
            title: `Segarkan pencarian seeder terbaru untuk ${rawId}`,
            url: refreshUrl,
            behaviorHints: { bingeGroup: `b2-v3-refresh-${rawId}`, notWebReady: false }
          });
        } else {
          const listTriggerUrl = `${workerBase}/${requestToken}/trigger_list?id=${encodeURIComponent(rawId)}&title=${encodeURIComponent(rawId)}`;
          streamResults.push({
            name: `[📋 Ambil Daftar Seeder]`,
            title: `Target: ${rawId}\n⚡ Klik untuk menjalankan Runner GitHub mencari torrent terbaik (< 5.0 GB)\n(Tunggu ~20 detik lalu buka kembali episode ini)`,
            url: listTriggerUrl,
            behaviorHints: { bingeGroup: `b2-v3-fetch-${rawId}`, notWebReady: false }
          });
        }

        return new Response(JSON.stringify({ streams: streamResults }), { headers: corsHeaders });

      } catch (err) {
        console.error("Kesalahan Stream Resolver V3:", err);
      }

      return new Response(JSON.stringify({ streams: [] }), { headers: corsHeaders });
    }

    return new Response(JSON.stringify({ error: "Not Found" }), { status: 404, headers: corsHeaders });
  },
};

/**
 * Pengarah Sharding Redis CRC32:
 * Selalu menggunakan Base IMDb ID (contoh: tt0944947) agar semua episode berada di shard yang sama.
 */
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

/**
 * Pemicu GitHub Actions 1: Runner Daftar Seeder (X03_series_seeder_list.yml)
 */
async function triggerGitHubListRunner(env, imdbId, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE_X3 || "X03_series_seeder_list.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "CF-Worker-V3-Series-List-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { imdb_id: imdbId, title: title },
      }),
    });
  } catch (e) {
    console.error("Kesalahan Trigger List Runner:", e);
  }
}

/**
 * Pemicu GitHub Actions 2: Runner Unduh Video (X04_series_download.yml)
 */
async function triggerGitHubDownloadRunner(env, hash, imdbId, fileIdx, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE_X4 || "X04_series_download.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "CF-Worker-V3-Series-Download-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          info_hash: hash,
          imdb_id: imdbId,
          file_idx: String(fileIdx || 0),
          title: title,
        },
      }),
    });
  } catch (e) {
    console.error("Kesalahan Trigger Download Runner:", e);
  }
}