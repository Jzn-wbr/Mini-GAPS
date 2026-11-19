const CACHE = 'mon-ecole-shell-v14';
const ASSETS = ['/', '/assets/app.css?v=14', '/assets/app.js?v=14', '/assets/icon-32.png', '/assets/icon-180.png', '/assets/icon-192.png', '/assets/icon-512.png', '/manifest.webmanifest'];
const ASSET_PATHS = new Set(ASSETS.map(path => new URL(path, self.location.origin).pathname));

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE)
    .then(cache => cache.addAll(ASSETS.map(path => new Request(path, {cache: 'reload'}))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const obsolete = (await caches.keys()).filter(key => key.startsWith('mon-ecole-shell-') && key !== CACHE);
    await Promise.all(obsolete.map(key => caches.delete(key)));
    await self.clients.claim();
    // Mettre aussi à jour les fenêtres exécutant l'ancien JS, qui n'écoutaient pas controllerchange.
    // Une première installation ne recharge pas la page d'association.
    if (obsolete.length) {
      const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
      for (const client of windows) {
        if (new URL(client.url).origin === self.location.origin) {
          // Ne pas attendre la navigation dans activate : elle doit pouvoir utiliser le worker activé.
          client.navigate(client.url).catch(() => {});
        }
      }
    }
  })());
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/') || url.pathname === '/healthz') return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/')));
  } else if (ASSET_PATHS.has(url.pathname)) {
    event.respondWith(caches.open(CACHE).then(cache => cache.match(event.request))
      .then(cached => cached || fetch(event.request)));
  }
});

self.addEventListener('push', event => {
  let message = {};
  try { message = event.data?.json() || {}; } catch {}
  // Chaque push affiche une notification visible, y compris en cas de contenu inattendu.
  event.waitUntil(self.registration.showNotification(message.title || 'Mini GAPS', {
    body: message.body || 'Une mise à jour est disponible.',
    tag: message.tag || 'school-update', icon: '/assets/icon-192.png',
    data: {view: 'notes'}
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(windows => {
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        client.postMessage({type: 'school-update', view: 'notes'});
        return client.focus();
      }
    }
    return self.clients.openWindow('/?view=notes');
  }));
});
