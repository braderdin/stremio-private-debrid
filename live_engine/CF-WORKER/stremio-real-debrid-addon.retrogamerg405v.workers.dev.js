/**
 * CLOUDFLARE WORKER: B2 PRIVATE DEBRID ADDON (v2.3.0)
 * INTEGRASI: DIRECT YTS BRIDGE + CRC32 REDIS SHARDS + GITHUB DISPATCH
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
  return (crc ^ (-1)) >>> 0;
}

let cachedRedis = null;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);

    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Content-Type": "application/json; charset=utf-8",
    };

    if (request.method === "OPTIONS") return new Response(null, { headers: cors });

    const token = parts[0];
    if (!token || token !== (env.ADDON_SECRET_TOKEN || "Harunosakura1122")) {
      return new Response(JSON.stringify({ error: "Akses tanpa kebenaran" }), { status: 401, headers: cors });
    }

    const route = parts[1];

    // 1. Manifest
    if (route === "manifest.json" || parts.length === 1) {
      return new Response(JSON.stringify({
        id: "org.braderdin.privatedebrid",
        version: "2.3.0",
        name: "B2 Private Debrid",
        description: "High-Speed Private Debrid via Backblaze B2 Multi-Storage",
        resources: ["stream", "catalog"],
        types: ["movie", "series"],
        idPrefixes: ["tt"],
        catalogs: [{ type: "movie", id: "b2_vault", name: "B2 Private Cloud" }],
      }), { headers: cors });
    }

    // 2. Katalog Filem Siap di B2
    if (route === "catalog" && parts.length >= 4) {
      const metas = await getReadyCatalog(env);
      return new Response(JSON.stringify({ metas }), { headers: cors });
    }

    // 3. Trigger Endpoint
    if (route === "trigger") {
      const targetHash = url.searchParams.get("hash") || "AUTO";
      const targetId = url.searchParams.get("id");
      const targetTitle = url.searchParams.get("title") || "Movie";

      if (targetId) {
        ctx.waitUntil(triggerGitHub(env, targetHash, targetId, targetTitle));
      }
      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 4. Stream Resolver
    if (route === "stream" && parts.length >= 4) {
      const rawId = parts[3].replace(".json", "");
      const proxyBase = (env.CF_WORKER_B2_PROXY_STORAGE || "").replace(/\/+$/, "");

      try {
        const shard = getRedisShard(rawId, env);
        const cached = await fetchRedis(shard, `stremio:${rawId}`);

        // Jika sudah siap di B2
        if (cached && cached.b2_bucket && cached.file_path) {
          const streamUrl = `${proxyBase}/${cached.b2_bucket}/${cached.file_path.replace(/^\/+/, "")}`;
          return new Response(JSON.stringify({
            streams: [{
              name: "[⚡ B2 Fast Stream]",
              title: `${cached.title || rawId}\n💾 Server: ${cached.b2_bucket}\n⚡ Status: Sedia Ditonton`,
              url: streamUrl,
            }]
          }), { headers: cors });
        }

        // Strategi B: Ambil pilihan kualiti rasmi dari YTS API
        const streams = await fetchDirectTorrents(rawId, url.origin, token);
        return new Response(JSON.stringify({ streams }), { headers: cors });
      } catch (e) {
        console.error("Stream Handler Error:", e);
      }
      return new Response(JSON.stringify({ streams: [] }), { headers: cors });
    }

    return new Response(JSON.stringify({ error: "Not Found" }), { status: 404, headers: cors });
  },
};

function getRedisShard(imdbId, env) {
  if (!cachedRedis) {
    try {
      cachedRedis = JSON.parse(env.REDIS_ACCOUNTS_JSON || "[]");
    } catch (e) {
      cachedRedis = [];
    }
  }
  if (!cachedRedis.length) return null;
  const idx = calculateCRC32(imdbId) % cachedRedis.length;
  const acc = cachedRedis[idx];
  return { url: (acc.redis_rest_url || acc.url).replace(/\/+$/, ""), token: acc.redis_rest_token || acc.token };
}

async function fetchRedis(shard, key) {
  if (!shard) return null;
  try {
    const res = await fetch(`${shard.url}/get/${key}`, { headers: { Authorization: `Bearer ${shard.token}` } });
    if (!res.ok) return null;
    const d = await res.json();
    return typeof d.result === "string" ? JSON.parse(d.result) : d.result;
  } catch (e) { return null; }
}

async function getReadyCatalog(env) {
  const shard = getRedisShard("tt0000000", env);
  if (!shard) return [];
  try {
    const res = await fetch(`${shard.url}/zrevrange/timeline:lru/0/49`, { headers: { Authorization: `Bearer ${shard.token}` } });
    const ids = (await res.json()).result || [];
    const out = [];
    for (const id of ids) {
      const m = await fetchRedis(shard, `stremio:${id}`);
      if (m && m.imdb_id) {
        out.push({
          id: m.imdb_id,
          type: "movie",
          name: m.title || m.imdb_id,
          poster: `https://images.metahub.space/poster/medium/${m.imdb_id}/img`,
        });
      }
    }
    return out;
  } catch (e) { return []; }
}

async function fetchDirectTorrents(imdbId, origin, token) {
  const baseId = imdbId.split(":")[0];
  const list = [];

  try {
    const ctrl = new AbortController();
    const timeoutId = setTimeout(() => ctrl.abort(), 2500); // 2.5s guard
    const resp = await fetch(`https://yts.mx/api/v2/list_movies.json?query_term=${baseId}`, { signal: ctrl.signal });
    clearTimeout(timeoutId);

    if (resp.ok) {
      const data = await resp.json();
      const movie = data?.data?.movies?.[0];
      if (movie && Array.isArray(movie.torrents)) {
        for (const t of movie.torrents) {
          const q = t.quality || "HD";
          const sz = t.size || "1.5 GB";
          const seeds = t.seeds || 10;
          const trUrl = `${origin}/${token}/trigger?hash=${t.hash}&id=${imdbId}&title=${encodeURIComponent(movie.title + " " + q)}`;

          list.push({
            name: `[📥 Sedut B2] ${q}`,
            title: `${movie.title} (${movie.year}) [${q}]\n💾 Saiz: ${sz}  |  👤 ${seeds} Seeders\n⚡ Klik untuk muat turun terus ke B2`,
            url: trUrl,
          });
        }
      }
    }
  } catch (e) {}

  // Sentiasa sediakan pilihan Auto Fallback di bawah
  list.push({
    name: `[📥 Minta Sedut B2 (Auto)]`,
    title: `IMDb: ${imdbId}\n⚡ Klik untuk arahkan Runner cari & sedut sumber terbaik secara pintar`,
    url: `${origin}/${token}/trigger?hash=AUTO&id=${imdbId}&title=${imdbId}`,
  });

  return list;
}

async function triggerGitHub(env, hash, imdbId, title) {
  const pat = env.GH_PAT;
  if (!pat) return;
  const owner = env.GH_OWNER || "braderdin";
  const repo = env.GH_REPO || "stremio-private-debrid";
  const workflow = env.GH_WORKFLOW_FILE || "00_download.yml";

  await fetch(`https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${pat}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "CF-Worker-Trigger",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: { info_hash: hash, imdb_id: imdbId, file_idx: "0", title: title },
    }),
  }).catch((e) => console.error(e));
}