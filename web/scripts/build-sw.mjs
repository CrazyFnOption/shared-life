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
const { version } = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
const digest = createHash('sha256')
for (const asset of assets) digest.update(await readFile(join(root.pathname,asset)))
const cache = 'shared-expense-' + version + '-' + digest.digest('hex').slice(0,16)
await writeFile(new URL('sw.js',root), `const CACHE=${JSON.stringify(cache)};
const VERSION=${JSON.stringify(version)};
const ASSETS=${JSON.stringify(['/',...assets])};
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS.map(asset => new Request(asset, { cache: 'reload' })))).then(() => self.skipWaiting())));
// An old window may still need its hashed lazy editor. Unknown/old clients keep all old caches.
async function cleanOldAppCaches() {
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const current = await Promise.all(windows.map(client => new Promise(resolve => {
    const channel = new MessageChannel();
    const timeout = setTimeout(() => { channel.port1.close(); resolve(false); }, 1000);
    channel.port1.onmessage = event => { clearTimeout(timeout); channel.port1.close(); resolve(event.data?.version === VERSION); };
    client.postMessage({ type: 'app-version' }, [channel.port2]);
  })));
  if (current.every(Boolean)) await Promise.all((await caches.keys()).filter(key => {
    const built = /^shared-expense-(\\d+\\.\\d+\\.\\d+)-/.exec(key)?.[1];
    return key.startsWith('shared-expense-') && key !== CACHE && (!built || built.localeCompare(VERSION, undefined, { numeric: true }) < 0);
  }).map(key => caches.delete(key)));
}
self.addEventListener('activate', event => event.waitUntil(self.clients.claim().then(cleanOldAppCaches)));
self.addEventListener('message', event => {
  if (event.data?.type === 'app-version') event.ports[0]?.postMessage({ version: VERSION });
  if (event.data?.type === 'clean-old-app-caches') event.waitUntil(cleanOldAppCaches());
});
function notificationUrl(value) {
  if (typeof value !== 'string' || value.startsWith('//')) return '/';
  try { const url = new URL(value, self.location.origin); return url.origin === self.location.origin && url.pathname === '/' && !url.username && !url.password ? url.pathname + url.search + url.hash : '/'; } catch { return '/'; }
}
self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch {}
  const url = notificationUrl(payload.url);
  const kind = ['monthly_report','weekly_review','memo_reminder'].includes(payload.kind) ? payload.kind : 'general';
  const body = { monthly_report:'月度复盘已生成，请打开查看', weekly_review:'你有待补评价，请打开查看', memo_reminder:'你有备忘提醒，请打开查看', general:'你有新的提醒，请打开同在查看' }[kind];
  event.waitUntil(self.registration.showNotification('同在', { body, icon: '/icon.svg', data: { url }, tag: 'shared-expense-' + kind }));
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
  if (event.request.mode === 'navigate') {
    event.respondWith((async () => {
      try { const response = await fetch(event.request); if (response.ok) return response; } catch {}
      return (await (await caches.open(CACHE)).match('/')) || Response.error();
    })());
    return;
  }
  if (ASSETS.includes(url.pathname) || url.pathname.startsWith('/assets/')) event.respondWith((async () => (await (await caches.open(CACHE)).match(event.request)) || (await caches.match(event.request)) || fetch(event.request))());
});
`)
console.log(`Service Worker generated: ${assets.length} application resources, ${cache}`)
