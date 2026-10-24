/* Westlake Insurance Portal — service worker
 *
 * App-shell caching + offline fallback so the portal installs as a PWA and
 * keeps its chrome (CSS, logo, icons, fonts) available without a network.
 * Dynamic /api/* responses are NEVER cached — only same-origin static assets
 * and third-party CDN/font payloads used to render the shell.
 */
// TEMP-BYPASS pay-first: clear cached navigation HTML that may render an outdated flag state.
const CACHE = 'westlake-shell-v2';

const SHELL = [
  // TEMP-BYPASS pay-first: keep only versioned static assets in the offline shell cache.
  '/manifest.webmanifest',
  '/css/dashboard.css',
  '/css/images/favicon.png',
  '/css/images/icon-192.png',
  '/css/images/icon-512.png',
  '/css/images/maskable-512.png',
  '/css/images/westlake_logo.png',
];

const CDN_HOSTS = [
  'https://cdnjs.cloudflare.com',
  'https://fonts.googleapis.com',
  'https://fonts.gstatic.com',
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch { return; }

  // Never intercept API calls — they must always hit the server.
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) return;

  const isCDN = CDN_HOSTS.includes(url.origin);

  // Third-party CDN/font assets: cache-first.
  if (isCDN) {
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req)))
    );
    return;
  }

  // TEMP-BYPASS pay-first: never cache rendered HTML, which includes the server-side flag value.
  if (req.mode === 'navigate' && url.origin === self.location.origin) {
    event.respondWith(
      fetch(req).catch(() => new Response(
        '<!doctype html><title>Offline</title><p>This page is unavailable while offline. Reconnect and reload.</p>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
      ))
    );
    return;
  }

  // Other same-origin GETs: stale-while-revalidate.
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req).then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
  }
});