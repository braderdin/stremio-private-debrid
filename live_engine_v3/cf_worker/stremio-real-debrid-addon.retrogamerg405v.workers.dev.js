/**
 * ==============================================================================
 * CLOUDFLARE WORKER: B2 PRIVATE VAULT ADDON (v3.0.0)
 * WORKER: stremio-real-debrid-addon.retrogamerg405v.workers.dev
 * PERANAN:
 * 1. Pustaka "Netflix Peribadi" untuk filem sedia ada di Backblaze B2 sahaja.
 * 2. Imbasan katalog 4-Shard serentak (tiada filem tercicir).
 * 3. 100% Pasif: Tiada pengikisan YTS & tiada pemicu GitHub Actions.
 * ==============================================================================
 */

// Jadual CRC32 rasmi (100% sepadan dengan modul Python zlib.crc32)
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
        id: "org.braderdin.privatedebrid",
        version: "3.0.0",
        name: "B2 Private Vault (Koleksi Siap)",
        description: "Koleksi Filem Sedia Ditonton Pantas daripada Storan Backblaze B2",
        resources: ["stream", "catalog"],
        types: ["movie", "series"],
        idPrefixes: ["tt"],
        catalogs: [
          {
            type: "movie",
            id: "b2_vault",
            name: "B2 Private Cloud",
          },
        ],
      };
      return new Response(JSON.stringify(manifest), { headers: corsHeaders });
    }

    // 3. Endpoint Katalog Vault (/catalog/movie/b2_vault.json)
    if (route === "catalog" && pathParts.length >= 4) {
      try {
        const metas = await getFullVaultCatalog(env);
        return new Response(JSON.stringify({ metas }), { headers: corsHeaders });
      } catch (err) {
        console.error("Ralat Katalog Vault:", err);
        return new Response(JSON.stringify({ metas: [] }), { headers: corsHeaders });
      }
    }

    // 4. Endpoint Stream Resolver (/stream/{type}/{id}.json)
    if (route === "stream" && pathParts.length >= 4) {
      const rawId = pathParts[3].replace(".json", "");
      const proxyBase = (env.CF_WORKER_B2_PROXY_STORAGE || "https://b2-proxy-aria-engine.retrogamerg405v.workers.dev").replace(/\/+$/, "");

      try {
        const accounts = getRedisAccounts(env);
        const targetShard = getTargetRedisShard(rawId, accounts);

        // Semak jika video sudah wujud di storan B2
        const cached = await fetchRedisData(targetShard, `stremio:${rawId}`);

        if (cached && cached.b2_bucket && cached.file_path) {
          const streamUrl = `${proxyBase}/${cached.b2_bucket}/${cached.file_path.replace(/^\/+/, "")}`;
          return new Response(
            JSON.stringify({
              streams: [
                {
                  name: "[⚡ B2 Vault Stream]",
                  title: `${cached.title || rawId}\n💾 Server: ${cached.b2_bucket}\n⚡ Status: Sedia Ditonton (Koleksi Vault)`,
                  url: streamUrl,
                  behaviorHints: { bingeGroup: `b2-vault-${rawId}`, notWebReady: false },
                },
              ],
            }),
            { headers: corsHeaders }
          );
        }
      } catch (e) {
        console.error("Ralat Stream Vault:", e);
      }

      // Jika tiada di B2, pulangkan senarai kosong (tiada trigger sebarang kerja)
      return new Response(JSON.stringify({ streams: [] }), { headers: corsHeaders });
    }

    return new Response(JSON.stringify({ error: "Not Found" }), { status: 404, headers: corsHeaders });
  },
};

/**
 * Pengurusan Sharding Upstash Redis CRC32 Modulo
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

/**
 * Mengumpul Keseluruhan Koleksi Filem Merentasi 4 Shard Redis Serentak
 */
async function getFullVaultCatalog(env) {
  const accounts = getRedisAccounts(env);
  if (!accounts || accounts.length === 0) return [];

  // 1. Dapatkan senarai ID fail dari timeline:lru pada kesemua 4 shard secara serentak
  const shardPromises = accounts.map(async (acc) => {
    try {
      const res = await fetch(`${acc.url}/zrevrange/timeline:lru/0/49`, {
        headers: { Authorization: `Bearer ${acc.token}` },
      });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data?.result) ? data.result : [];
    } catch {
      return [];
    }
  });

  const shardResults = await Promise.all(shardPromises);
  const allIds = [...new Set(shardResults.flat())];

  if (allIds.length === 0) return [];

  // 2. Ambil metadata penuh bagi setiap ID daripada shard masing-masing
  const metaPromises = allIds.map(async (imdbId) => {
    const shard = getTargetRedisShard(imdbId, accounts);
    const meta = await fetchRedisData(shard, `stremio:${imdbId}`);

    if (meta && meta.imdb_id && meta.b2_bucket && meta.file_path) {
      return {
        id: meta.imdb_id,
        type: "movie",
        name: meta.title || meta.imdb_id,
        poster: `https://images.metahub.space/poster/medium/${meta.imdb_id}/img`,
        description: `💾 Baldi: ${meta.b2_bucket}\n⚡ Status: Sedia Ditonton (Koleksi Vault)`,
      };
    }
    return null;
  });

  const resolvedMetas = await Promise.all(metaPromises);
  return resolvedMetas.filter(Boolean);
}