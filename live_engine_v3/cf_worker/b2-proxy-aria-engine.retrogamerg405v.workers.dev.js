/**
 * Cloudflare Worker: B2 Multi-Account Video Streaming Proxy (V2 Turbo Streaming Engine)
 * Lokasi: CF_WORKER_B2_PROXY_STORAGE (b2-proxy-aria-engine.retrogamerg405v.workers.dev)
 * Ciri Utama:
 * 1. HTTP Range Request (206 Partial Content) untuk penstriman pantas tanpa sangkut di TV.
 * 2. Cloudflare Edge Caching untuk mengurangkan latensi pelayan Backblaze US-West ke Malaysia.
 * 3. Cache-Control agresif (immutable) untuk mengelakkan ExoPlayer memuat semula data sama.
 * 4. Token Auto-Refresh jika Backblaze memulangkan ralat 401 Unauthorized secara tiba-tiba.
 */

const authCache = {};
let parsedAccountsCache = null;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Range, Authorization, Content-Type, If-Range, If-Match",
  "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges, ETag",
};

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: corsHeaders });
    }

    const url = new URL(request.url);
    const pathSegments = url.pathname.split("/").filter(Boolean);

    // Format URL yang dijangka: /:bucket_identifier/:path_to_video.mp4
    if (pathSegments.length < 2) {
      return new Response("Format URL tidak sah. Gunakan: /:bucket_identifier/:file_path", {
        status: 400,
        headers: corsHeaders,
      });
    }

    const rawBucketIdentifier = pathSegments[0];
    const filePath = pathSegments.slice(1).join("/");

    // 1. Dapatkan maklumat akaun B2 daripada konfigurasi env
    const b2Account = getB2AccountConfig(env, rawBucketIdentifier);
    if (!b2Account) {
      return new Response(`Ralat: Baldi B2 '${rawBucketIdentifier}' tidak ditemui.`, {
        status: 404,
        headers: corsHeaders,
      });
    }

    try {
      // 2. Hubungi Backblaze B2 dengan sokongan Auto-Retry Token
      let auth = await getB2Authorization(b2Account.keyId, b2Account.appKey);
      let b2Response = await fetchB2File(auth, b2Account.bucketName, filePath, request);

      // Jika token luput di pelayan B2 (HTTP 401), kosongkan memori dan cuba semula sekali
      if (b2Response.status === 401) {
        delete authCache[b2Account.keyId];
        auth = await getB2Authorization(b2Account.keyId, b2Account.appKey);
        b2Response = await fetchB2File(auth, b2Account.bucketName, filePath, request);
      }

      // 3. Susun pengepala respons untuk penstriman optimum di ExoPlayer TV
      const responseHeaders = new Headers(b2Response.headers);
      
      // Tetapkan kawalan CORS dan sokongan Range
      for (const [key, value] of Object.entries(corsHeaders)) {
        responseHeaders.set(key, value);
      }
      responseHeaders.set("Accept-Ranges", "bytes");

      // Kunci Cache Edge untuk menghapuskan buffering blok video berulang
      responseHeaders.set("Cache-Control", "public, max-age=2592000, immutable");

      // Tentukan format video dengan tepat jika asalnya octet-stream
      const cType = responseHeaders.get("Content-Type") || "";
      if (!cType || cType.includes("octet-stream")) {
        if (filePath.toLowerCase().endsWith(".mkv")) {
          responseHeaders.set("Content-Type", "video/x-matroska");
        } else if (filePath.toLowerCase().endsWith(".mp4")) {
          responseHeaders.set("Content-Type", "video/mp4");
        }
      }

      // Alirkan (stream) terus tanpa memuatkan keseluruhan fail ke memori
      return new Response(b2Response.body, {
        status: b2Response.status, // Kekalkan 200 OK atau 206 Partial Content
        statusText: b2Response.statusText,
        headers: responseHeaders,
      });

    } catch (err) {
      return new Response(`Ralat Proksi B2: ${err.message}`, {
        status: 500,
        headers: corsHeaders,
      });
    }
  },
};

/**
 * Membuat subrequest ke Backblaze B2 dengan memajukan pengepala Range dan Edge Cache
 */
async function fetchB2File(auth, bucketName, filePath, incomingRequest) {
  const forwardHeaders = new Headers();
  forwardHeaders.set("Authorization", auth.token);

  // Majukan pengepala penstriman penting dari Stremio TV
  const headersToForward = ["Range", "If-Range", "If-Match", "If-None-Match"];
  for (const h of headersToForward) {
    const val = incomingRequest.headers.get(h);
    if (val) forwardHeaders.set(h, val);
  }

  const b2DownloadUrl = `${auth.downloadUrl}/file/${bucketName}/${encodeURI(filePath)}`;

  return await fetch(b2DownloadUrl, {
    method: incomingRequest.method,
    headers: forwardHeaders,
    cf: {
      cacheEverything: true,
      cacheTtl: 86400, // Simpan blok video aktif selama 24 jam di pelayan Cloudflare terdekat
    },
  });
}

/**
 * Menghurai akaun B2 daripada konfigurasi V1 dan V2
 */
function getB2AccountConfig(env, bucketIdentifier) {
  const target = (bucketIdentifier || "").toLowerCase().trim();

  if (!parsedAccountsCache) {
    let combined = [];
    const parsePart = (val) => {
      if (!val) return [];
      try {
        return typeof val === "string" ? JSON.parse(val) : val;
      } catch {
        return [];
      }
    };

    if (env.B2_ACCOUNTS_JSON_V1) combined = combined.concat(parsePart(env.B2_ACCOUNTS_JSON_V1));
    if (env.B2_ACCOUNTS_JSON_V2) combined = combined.concat(parsePart(env.B2_ACCOUNTS_JSON_V2));

    parsedAccountsCache = combined;
  }

  for (const acc of parsedAccountsCache || []) {
    const bName = (acc.bucket_name || "").toLowerCase();
    const idxStr = String(acc.index || "");

    if (bName === target || idxStr === target) {
      return {
        keyId: acc.key_id,
        appKey: acc.app_key,
        bucketName: acc.bucket_name,
      };
    }
  }
  return null;
}

/**
 * Autentikasi token sesi Backblaze B2 (Disimpan dalam memori Worker selama 12 jam)
 */
async function getB2Authorization(keyId, appKey) {
  const now = Date.now();
  if (authCache[keyId] && authCache[keyId].expiresAt > now) {
    return authCache[keyId];
  }

  const credentials = btoa(`${keyId}:${appKey}`);
  const authUrl = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account";

  const res = await fetch(authUrl, {
    headers: { Authorization: `Basic ${credentials}` },
  });

  if (!res.ok) {
    throw new Error(`Autorisasi B2 Gagal (HTTP ${res.status})`);
  }

  const data = await res.json();
  const authData = {
    token: data.authorizationToken,
    downloadUrl: data.downloadUrl,
    expiresAt: now + (12 * 60 * 60 * 1000),
  };

  authCache[keyId] = authData;
  return authData;
}