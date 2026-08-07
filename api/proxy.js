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
  const path = incoming.searchParams.get('path') || '';
  incoming.searchParams.delete('path');
  const query = incoming.searchParams.toString();
  return `/api/${path}${query ? `?${query}` : ''}`;
}

module.exports = async (request, response) => {
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    const normalizedName = name.toLowerCase();
    if (value === undefined || HOP_BY_HOP_HEADERS.has(normalizedName) || normalizedName === 'host') continue;
    headers.set(name, Array.isArray(value) ? value.join(', ') : value);
  }

  // Flask-WTF checks the HTTPS referrer against request.host. Keep the public
  // host while the TLS connection itself is made to the private API origin.
  const publicHost = request.headers.host || 'zeelineinsurance.tech';
  headers.set('host', publicHost);
  headers.set('x-forwarded-host', publicHost);
  headers.set('x-forwarded-proto', 'https');

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
    response.setHeader('x-zeeline-api-relay', '1');
    response.status(upstream.status).send(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    response.status(502).json({ error: 'The insurance service is temporarily unavailable.' });
  }
};
