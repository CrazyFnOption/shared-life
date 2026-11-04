import { readdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { join } from 'node:path'
const root = new URL('../dist/',import.meta.url)
async function files(dir, prefix = '') {
  const result = []
  for (const entry of await readdir(dir,{withFileTypes:true})) {
    const relative = prefix + entry.name
    if (entry.isDirectory()) result.push(...await files(join(dir,entry.name),relative+'/'))
    else if (entry.name !== 'sw.js') result.push('/'+relative)
  }
  return result
}
const assets = await files(root.pathname)
const digest = createHash('sha256')
for (const asset of assets) digest.update(await readFile(join(root.pathname,asset)))
const cache = 'shared-expense-' + digest.digest('hex').slice(0,16)
await writeFile(new URL('sw.js',root), `const CACHE=${JSON.stringify(cache)};
const ASSETS=${JSON.stringify(['/',...assets])};
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS.map(asset => new Request(asset, { cache: 'reload' }))))));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('shared-expense-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim())));
function notificationUrl(value) {
  if (typeof value !== 'string' || value.startsWith('//')) return '/';
  try { const url = new URL(value, self.location.origin); return url.origin === self.location.origin && url.pathname === '/' && !url.username && !url.password ? url.pathname + url.search + url.hash : '/'; } catch { return '/'; }
}
self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch {}
  const url = notificationUrl(payload.url);
  event.waitUntil(self.registration.showNotification('同在', { body: '你有新的提醒，请打开同在查看', icon: '/icon.svg', data: { url }, tag: 'shared-expense-todo' }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = notificationUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async windows => {
    const current = windows.find(client => new URL(client.url).origin === self.location.origin);
    if (current) { current.postMessage({ type: 'notification-open', url }); return current.focus(); }
    return self.clients.openWindow(url);
  }));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (event.request.mode === 'navigate') { event.respondWith(caches.open(CACHE).then(cache => cache.match('/')).then(cached => cached || fetch(event.request))); return; }
  if (ASSETS.includes(url.pathname)) event.respondWith(caches.open(CACHE).then(cache => cache.match(event.request)).then(cached => cached || fetch(event.request)));
});
`)
console.log(`Service Worker generated: ${assets.length} application resources, ${cache}`)
