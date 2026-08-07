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
    if (value === undefined || HOP_BY_HOP_HEADERS.has(name.toLowerCase()) || name.toLowerCase() === 'host') continue;
    headers.set(name, Array.isArray(value) ? value.join(', ') : value);
  }
  headers.set('host', 'api.zeelineinsurance.tech');
  headers.set('x-forwarded-host', request.headers.host || 'zeelineinsurance.tech');
  headers.set('x-forwarded-proto', 'https');

  try {
    const upstream = await fetch(`${BACKEND_ORIGIN}${upstreamPath(request)}`, {
      method: request.method,
      headers,
      body: requestBody(request),
      redirect: 'manual',
    });

    for (const [name, value] of upstream.headers.entries()) {
      if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase()) && name.toLowerCase() !== 'set-cookie') {
        response.setHeader(name, value);
      }
    }
    const cookies = typeof upstream.headers.getSetCookie === 'function'
      ? upstream.headers.getSetCookie()
      : upstream.headers.get('set-cookie');
    if (cookies && (Array.isArray(cookies) ? cookies.length : true)) response.setHeader('set-cookie', cookies);
    response.setHeader('cache-control', 'private, no-store, max-age=0, must-revalidate');
    response.status(upstream.status).send(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    response.status(502).json({ error: 'The insurance service is temporarily unavailable.' });
  }
};
