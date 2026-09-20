/**
 * Cloudflare Worker: 24/7 Render Keepalive Ping Engine
 * Mengendalikan Cron Trigger automatik (setiap 10 minit) dan ujian manual HTTP GET.
 */

export default {
  // 1. Dijalankan secara automatik oleh Cron Trigger Cloudflare
  async scheduled(event, env, ctx) {
    ctx.waitUntil(performRenderPing(env, "CRON_SCHEDULED"));
  },

  // 2. Dijalankan apabila URL Worker dibuka melalui pelayar atau arahan curl
  async fetch(request, env, ctx) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
      "Content-Type": "application/json; charset=utf-8",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);

    // Endpoint semakan pantas / status
    if (url.pathname === "/" || url.pathname === "/ping") {
      const result = await performRenderPing(env, "HTTP_MANUAL");
      return new Response(JSON.stringify(result, null, 2), {
        status: result.success ? 200 : 502,
        headers: corsHeaders,
      });
    }

    return new Response(JSON.stringify({ error: "Endpoint tidak ditemui" }), {
      status: 404,
      headers: corsHeaders,
    });
  },
};

/**
 * Fungsi utama menghantar permintaan Keepalive GET ke Render
 */
async function performRenderPing(env, triggerType) {
  const targetUrl = (env.RENDER_URL || "https://stremio-private-debrid.onrender.com").replace(/\/+$/, "");
  const startTime = Date.now();

  try {
    const response = await fetch(`${targetUrl}/`, {
      method: "GET",
      headers: {
        "User-Agent": "Cloudflare-Cron-Ping-Engine/1.0",
        "Cache-Control": "no-cache",
      },
    });

    const elapsedMs = Date.now() - startTime;
    const isSuccess = response.status === 200;

    let payload = null;
    try {
      payload = await response.json();
    } catch (_) {
      payload = await response.text();
    }

    const logSummary = {
      success: isSuccess,
      trigger_by: triggerType,
      target_url: targetUrl,
      http_status: response.status,
      latency_ms: elapsedMs,
      timestamp_utc: new Date().toISOString(),
      render_response: payload,
    };

    console.log(`[Ping Report] ${triggerType} -> Status: ${response.status} (${elapsedMs}ms)`);
    return logSummary;

  } catch (error) {
    const errSummary = {
      success: false,
      trigger_by: triggerType,
      target_url: targetUrl,
      error_message: error.message,
      timestamp_utc: new Date().toISOString(),
    };

    console.error(`[Ping Error] ${triggerType} failed:`, error.message);
    return errSummary;
  }
}