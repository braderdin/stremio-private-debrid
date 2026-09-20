/**
 * Cloudflare Worker: B2 Multi-Account Video Streaming Proxy
 * Sokongan HTTP Range Request (206 Partial Content) & B2 Bandwidth Alliance.
 */

const authCache = {};
let parsedAccountsCache = null;

export default {
  async fetch(request, env, ctx) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
      "Access-Control-Allow-Headers": "Range, Authorization, Content-Type",
      "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: corsHeaders });
    }

    const url = new URL(request.url);
    const pathSegments = url.pathname.split("/").filter(Boolean);

    // Format yang dijangka: /:bucket_name_or_index/:filepath
    if (pathSegments.length < 2) {
      return new Response("Format URL salah. Format: /:bucket_identifier/:path_to_video.mp4", {
        status: 400,
        headers: corsHeaders,
      });
    }

    const rawBucketIdentifier = pathSegments[0];
    const filePath = pathSegments.slice(1).join("/");

    // 1. Padankan akaun B2 daripada V1 dan V2
    const b2Account = getB2AccountConfig(env, rawBucketIdentifier);
    if (!b2Account) {
      return new Response(`Bucket '${rawBucketIdentifier}' tidak wujud dalam konfigurasi.`, {
        status: 404,
        headers: corsHeaders,
      });
    }

    try {
      // 2. Dapatkan token sesi rasmi B2
      const auth = await getB2Authorization(b2Account.keyId, b2Account.appKey);

      // 3. Salin headers daripada Stremio (terutamanya header Range)
      const forwardHeaders = new Headers();
      forwardHeaders.set("Authorization", auth.token);

      const rangeHeader = request.headers.get("Range");
      if (rangeHeader) {
        forwardHeaders.set("Range", rangeHeader);
      }

      // 4. Buat panggilan terus ke fail B2
      const b2DownloadUrl = `${auth.downloadUrl}/file/${b2Account.bucketName}/${encodeURI(filePath)}`;
      const b2Response = await fetch(b2DownloadUrl, {
        method: request.method,
        headers: forwardHeaders,
      });

      // 5. Susun respons video untuk Stremio
      const responseHeaders = new Headers(b2Response.headers);
      responseHeaders.set("Access-Control-Allow-Origin", "*");
      responseHeaders.set("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges");
      responseHeaders.set("Accept-Ranges", "bytes");

      // Tentukan Content-Type video jika tiada
      if (!responseHeaders.get("Content-Type") || responseHeaders.get("Content-Type") === "application/octet-stream") {
        if (filePath.endsWith(".mkv")) {
          responseHeaders.set("Content-Type", "video/x-matroska");
        } else {
          responseHeaders.set("Content-Type", "video/mp4");
        }
      }

      return new Response(b2Response.body, {
        status: b2Response.status, // 200 OK atau 206 Partial Content
        statusText: b2Response.statusText,
        headers: responseHeaders,
      });

    } catch (err) {
      return new Response(`Ralat Streaming Proxy: ${err.message}`, {
        status: 500,
        headers: corsHeaders,
      });
    }
  },
};

/**
 * Menggabungkan dan membaca akaun daripada V1 dan V2 (Had 5KB Cloudflare)
 */
function getB2AccountConfig(env, bucketIdentifier) {
  const target = (bucketIdentifier || "").toLowerCase().trim();

  if (!parsedAccountsCache) {
    let combined = [];
    const parsePart = (val) => {
      if (!val) return [];
      try {
        return typeof val === "string" ? JSON.parse(val) : val;
      } catch (e) {
        return [];
      }
    };

    if (env.B2_ACCOUNTS_JSON_V1) combined = combined.concat(parsePart(env.B2_ACCOUNTS_JSON_V1));
    if (env.B2_ACCOUNTS_JSON_V2) combined = combined.concat(parsePart(env.B2_ACCOUNTS_JSON_V2));

    parsedAccountsCache = combined;
  }

  const accounts = parsedAccountsCache || [];

  for (const acc of accounts) {
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
 * Autentikasi sesi B2 dengan memori cache 12 jam
 */
async function getB2Authorization(keyId, appKey) {
  const cacheKey = keyId;
  const now = Date.now();

  if (authCache[cacheKey] && authCache[cacheKey].expiresAt > now) {
    return authCache[cacheKey];
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

  authCache[cacheKey] = authData;
  return authData;
}