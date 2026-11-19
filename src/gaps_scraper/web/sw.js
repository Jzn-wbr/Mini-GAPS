const CACHE = 'mon-ecole-shell-v11';
const ASSETS = ['/', '/assets/app.css', '/assets/app.js', '/assets/icon-32.png', '/assets/icon-180.png', '/assets/icon-192.png', '/assets/icon-512.png', '/manifest.webmanifest'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('mon-ecole-shell-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/') || url.pathname === '/healthz') return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/')));
  } else if (ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
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
