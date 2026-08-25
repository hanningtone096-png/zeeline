const BACKEND_ORIGIN = 'https://api.zeelineinsurance.tech';
const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'content-length',
  'content-encoding',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

function requestBody(request) {
  if (['GET', 'HEAD'].includes(request.method)) return undefined;
  if (Buffer.isBuffer(request.body) || typeof request.body === 'string') return request.body;
  if (request.body === undefined || request.body === null) return undefined;
  return JSON.stringify(request.body);
}

function upstreamPath(request) {
  const incoming = new URL(request.url, 'https://proxy.invalid');
  // The `path` parameter selects a backend route under /api/. Sanitize it so a
  // caller cannot escape the /api/ prefix (e.g. `?path=../admin`) or inject an
  // extra query string (e.g. `?path=foo?bar=baz`): strip leading slashes, drop
  // any `..` segment, and forbid a literal '?' by stripping anything from the
  // first '?' or '#' onward. Multi-segment paths (dashboard/stats,
  // admin/dmvic/pending-confirmations, …) MUST be preserved in full.
  const rawPath = (incoming.searchParams.get('path') || '');
  incoming.searchParams.delete('path');
  const pathOnly = rawPath.split(/[?#]/)[0];
  const cleanSegments = pathOnly
    .split('/')
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && s !== '.' && s !== '..');
  const path = cleanSegments.join('/');
  const query = incoming.searchParams.toString();
  return `/api/${path}${query ? `?${query}` : ''}`;
}

module.exports = async (request, response) => {
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    const normalizedName = name.toLowerCase();
    if (value === undefined || HOP_BY_HOP_HEADERS.has(normalizedName) || normalizedName === 'host') continue;
    // Do not forward client-supplied forwarding headers — they are spoofable.
    // The proxy sets the authoritative values below from its own origin/client.
    if (normalizedName === 'x-forwarded-for' || normalizedName === 'x-forwarded-host'
        || normalizedName === 'x-forwarded-proto' || normalizedName === 'x-real-ip'
        || normalizedName === 'x-zeeline-public-host') {
      continue;
    }
    headers.set(name, Array.isArray(value) ? value.join(', ') : value);
  }

  // Set trusted forwarding headers ourselves rather than trusting the client.
  // The public host is the backend origin's own host (not the attacker-controlled
  // request Host), so any backend CSRF referrer check validates against the real
  // public host. The client IP comes from the connection socket so it cannot be
  // spoofed via a header; client-supplied x-forwarded-for is dropped above.
  const backendUrl = new URL(BACKEND_ORIGIN);
  headers.set('x-zeeline-public-host', backendUrl.host);
  headers.set('x-forwarded-host', backendUrl.host);
  headers.set('x-forwarded-proto', backendUrl.protocol.replace(':', ''));
  const clientIp = (request.socket && request.socket.remoteAddress) || '';
  if (clientIp) {
    headers.set('x-forwarded-for', clientIp);
    headers.set('x-real-ip', clientIp);
  }

  try {
    const upstream = await fetch(`${BACKEND_ORIGIN}${upstreamPath(request)}`, {
      method: request.method,
      headers,
      body: requestBody(request),
      redirect: 'manual',
    });

    for (const [name, value] of upstream.headers.entries()) {
      const normalizedName = name.toLowerCase();
      if (!HOP_BY_HOP_HEADERS.has(normalizedName) && normalizedName !== 'set-cookie') {
        response.setHeader(name, value);
      }
    }

    const cookies = typeof upstream.headers.getSetCookie === 'function'
      ? upstream.headers.getSetCookie()
      : upstream.headers.get('set-cookie');
    if (cookies && (Array.isArray(cookies) ? cookies.length : true)) {
      response.setHeader('set-cookie', cookies);
    }

    response.setHeader('cache-control', 'private, no-store, max-age=0, must-revalidate');
    response.status(upstream.status).send(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    response.status(502).json({ error: 'The insurance service is temporarily unavailable.' });
  }
};
