// Pages Advanced Mode: 所有路径先验证，API再转发到受保护的Python源站。
const SECURITY = {
  'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
  'X-Robots-Tag': 'noindex, nofollow',
  'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
};
function response(text, status, extra = {}) {
  return new Response(text, {status, headers: {...SECURITY, ...extra}});
}
async function equal(a, b) {
  const encoder = new TextEncoder();
  const [x, y] = await Promise.all([a, b].map(v => crypto.subtle.digest('SHA-256', encoder.encode(v))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let difference = 0;
  for (let i = 0; i < u.length; i++) difference |= u[i] ^ v[i];
  return difference === 0;
}
export async function handle(request, env, outbound = fetch) {
  if (!env.PREVIEW_USER || !env.PREVIEW_PASSWORD || env.PREVIEW_PASSWORD.length < 24 ||
      !env.ORIGIN_TOKEN || env.ORIGIN_TOKEN.length < 24 || !env.ORIGIN_URL) {
    return response('远程测试尚未配置，拒绝公开访问。', 503);
  }
  const header = request.headers.get('Authorization') || '';
  let decoded = '';
  if (header.length < 2048 && /^Basic /i.test(header)) {
    try { decoded = atob(header.slice(6)); } catch { decoded = ''; }
  }
  if (!await equal(decoded, env.PREVIEW_USER + ':' + env.PREVIEW_PASSWORD)) {
    return response('请使用本机部署终端提供的测试账号和密码。', 401,
      {'WWW-Authenticate': 'Basic realm="CIRP preview", charset="UTF-8"'});
  }
  const url = new URL(request.url);
  if (!['GET', 'HEAD', 'POST', 'PUT'].includes(request.method)) return response('方法不支持。', 405);
  if (!['GET', 'HEAD'].includes(request.method)) {
    if (request.headers.get('Origin') && request.headers.get('Origin') !== url.origin) return response('禁止跨站修改。', 403);
    if (request.headers.get('X-CIRP-Client') !== 'browser') return response('缺少客户端标记。', 403);
  }
  const length = request.headers.get('Content-Length');
  if (length && (!/^\d+$/.test(length) || Number(length) > 4 * 1024 * 1024 + 65536)) return response('请求过大。', 413);
  let upstream;
  if (url.pathname.startsWith('/api/')) {
    let base;
    try { base = new URL(env.ORIGIN_URL); } catch { return response('源站配置无效。', 503); }
    if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash || base.pathname !== '/') return response('源站地址必须是HTTPS根地址。', 503);
    // 用户URL只能提供路径，不能通过//等路径改写固定源站主机。
    base.pathname = url.pathname; base.search = url.search;
    const headers = new Headers(request.headers);
    for (const name of ['authorization', 'cookie', 'host', 'x-cirp-origin-token', 'x-forwarded-host', 'x-forwarded-proto', 'forwarded']) headers.delete(name);
    headers.set('X-CIRP-Origin-Token', env.ORIGIN_TOKEN);
    if (!['GET', 'HEAD'].includes(request.method)) headers.set('Origin', base.origin);
    try {
      upstream = await outbound(base.toString(), {
        method: request.method, headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
        redirect: 'manual'
      });
    } catch { return response('源站离线：请保持Python服务及Tunnel终端运行。', 502); }
    if (upstream.status >= 300 && upstream.status < 400) return response('源站异常重定向，已阻止。', 502);
  } else if (url.pathname === '/' || url.pathname.startsWith('/assets/')) {
    if (!['GET', 'HEAD'].includes(request.method)) return response('仅允许读取页面。', 405);
    upstream = await env.ASSETS.fetch(request);
  } else return response('未找到。', 404);
  const out = new Response(upstream.body, {status: upstream.status, statusText: upstream.statusText, headers: upstream.headers});
  for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
  out.headers.delete('Set-Cookie');
  out.headers.delete('Access-Control-Allow-Origin');
  return out;
}
export default {fetch: (request, env) => handle(request, env)};
