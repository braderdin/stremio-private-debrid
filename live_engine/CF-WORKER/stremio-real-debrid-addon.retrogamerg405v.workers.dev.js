/**
 * Cloudflare Worker: Stremio Private Debrid Addon Engine
 * Mengendalikan Manifest, Torrentio Resolver, Sharding Modulo Redis, dan GitHub Action Dispatch.
 */

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

    // 1. Semakan Keselamatan Token
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
        version: "1.0.0",
        name: "B2 Private Debrid",
        description: "Private High-Speed Torrent Caching & Direct Streaming via B2",
        resources: ["stream"],
        types: ["movie", "series"],
        catalogs: [],
      };
      return new Response(JSON.stringify(manifest), { headers: corsHeaders });
    }

    // 3. Endpoint Trigger Muat Turun (/trigger)
    // Dipanggil apabila pengguna menekan link torrent yang belum ada di B2
    if (route === "trigger") {
      const targetHash = url.searchParams.get("hash");
      const targetId = url.searchParams.get("id");
      const targetTitle = url.searchParams.get("title") || "video";
      const fileIdx = url.searchParams.get("idx") || "0";

      if (targetHash && targetId) {
        // Cetuskan GitHub Actions di latar belakang
        ctx.waitUntil(triggerGitHubDownloader(env, targetHash, targetId, fileIdx, targetTitle));
      }

      // Alihkan pemain Stremio ke video makluman pendek agar tidak berlaku "Playback Error"
      // Menggunakan klip MP4 3 saat rasmi yang selamat dan pantas
      return Response.redirect("https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4", 302);
    }

    // 4. Endpoint Streams (/stream/{type}/{id}.json)
    if (route === "stream" && pathParts.length >= 4) {
      const type = pathParts[2];
      const rawId = pathParts[3].replace(".json", ""); // cth: tt0186945 atau tt0903747:1:1

      const proxyBase = (env.CF_WORKER_B2_PROXY_STORAGE || "").replace(/\/+$/, "");
      const workerBase = url.origin;

      try {
        const accounts = getRedisAccounts(env);
        const targetShard = getTargetRedisShard(rawId, accounts);

        // Semak jika video sudah siap disimpan di B2
        const cachedMeta = await fetchRedisData(targetShard, `stremio:${rawId}`);

        if (cachedMeta && cachedMeta.b2_bucket && cachedMeta.file_path) {
          // JIKA SUDAH SIAP: Berikan pautan video B2 laju
          const streamUrl = `${proxyBase}/${cachedMeta.b2_bucket}/${cachedMeta.file_path}`;
          return new Response(JSON.stringify({
            streams: [
              {
                name: `[⚡ B2 Fast Stream]`,
                title: `${cachedMeta.title || rawId}\n💾 Server: ${cachedMeta.b2_bucket}\n⚡ Status: Sedia Ditonton`,
                url: streamUrl,
              }
            ]
          }), { headers: corsHeaders });
        }

        // JIKA BELUM ADA: Ambil senarai torrent dari Torrentio API
        const torrentioUrl = `https://torrentio.strem.fun/stream/${type}/${rawId}.json`;
        const tResp = await fetch(torrentioUrl, {
          headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" }
        });

        if (!tResp.ok) {
          return new Response(JSON.stringify({ streams: [] }), { headers: corsHeaders });
        }

        const tData = await tResp.json();
        const incomingStreams = tData.streams || [];

        // Ubah senarai torrent kepada pautan pencetus muat turun
        const finalStreams = incomingStreams.map((stream) => {
          const hash = stream.infoHash;
          const fileIdx = stream.fileIdx !== undefined ? stream.fileIdx : 0;
          const titleText = stream.title || stream.name || "Torrent";

          // Format butang tindakan klik
          const triggerUrl = `${workerBase}/${requestToken}/trigger?hash=${hash}&id=${rawId}&idx=${fileIdx}&title=${encodeURIComponent(titleText.split("\n")[0])}`;

          return {
            name: `[📥 Sedut ke B2]`,
            title: `${titleText}\n⚡ Klik untuk muat turun ke storan awan B2`,
            url: triggerUrl,
          };
        });

        return new Response(JSON.stringify({ streams: finalStreams }), { headers: corsHeaders });

      } catch (err) {
        console.error("Stream Handler Error:", err);
      }

      return new Response(JSON.stringify({ streams: [] }), { headers: corsHeaders });
    }

    return new Response(JSON.stringify({ error: "Not Found" }), { status: 404, headers: corsHeaders });
  },
};

/**
 * Pengurusan Sharding 4 Akaun Upstash Redis
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
    } catch (e) {}
  }
  cachedRedisAccounts = accounts;
  return accounts;
}

/**
 * Kira shard sasaran berasaskan Modulo IMDb ID
 */
function getTargetRedisShard(imdbId, accounts) {
  if (!accounts || accounts.length === 0) return null;
  const match = (imdbId || "").match(/tt(\d+)/);
  let shardIdx = 0;

  if (match) {
    shardIdx = parseInt(match[1], 10) % accounts.length;
  } else {
    const sum = String(imdbId).split("").reduce((acc, c) => acc + c.charCodeAt(0), 0);
    shardIdx = sum % accounts.length;
  }
  return accounts[shardIdx];
}

/**
 * Pembantu membaca data dari Upstash REST API
 */
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
 * Menghantar arahan terus ke GitHub Actions Workflow
 */
async function triggerGitHubDownloader(env, hash, imdbId, fileIdx, title) {
  const ghOwner = env.GH_OWNER || "braderdin";
  const ghRepo = env.GH_REPO || "stremio-private-debrid";
  const workflowFile = env.GH_WORKFLOW_FILE || "download.yml";
  const ghPat = env.GH_PAT;

  if (!ghPat) return;

  const dispatchUrl = `https://api.github.com/repos/${ghOwner}/${ghRepo}/actions/workflows/${workflowFile}/dispatches`;

  try {
    await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghPat}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "Cloudflare-Worker-Debrid-Trigger",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          info_hash: hash,
          imdb_id: imdbId,
          file_idx: String(fileIdx),
          title: title,
        },
      }),
    });
  } catch (e) {
    console.error("GitHub Action Trigger Error:", e);
  }
}