import { ref } from 'vue'
import { version } from '../package.json'
import { sourceValid } from './sources'
import type { Account, Source } from './types'

export const appUpdateReady = ref(false)
export const appUpdateError = ref('')
export const appInputActive = ref(false)
const scopeKey = 'shared-life:update-source'
let registration: ServiceWorkerRegistration | undefined
let checking = false
let reloading = false
let canReload = () => false
let beforeReload = () => false

export function configureAppUpdates(allowed: () => boolean, prepare: () => boolean) {
  canReload = allowed
  beforeReload = prepare
}

export function rememberUpdateSource(source?: Source): boolean {
  try {
    if (source) sessionStorage.setItem(scopeKey, JSON.stringify(source))
    else sessionStorage.removeItem(scopeKey)
    return true
  } catch {
    appUpdateError.value = '无法保留当前范围，已暂停自动更新；当前内容仍保留。'
    return false
  }
}

export function takeUpdateSource(account: Account): Source | undefined {
  try {
    const value = sessionStorage.getItem(scopeKey)
    sessionStorage.removeItem(scopeKey)
    const source: Source | undefined = value ? JSON.parse(value) : undefined
    return source && sourceValid(account, source) ? source : undefined
  } catch { return undefined }
}

export function applyAppUpdate() {
  if (!appUpdateReady.value || reloading || !navigator.onLine || document.visibilityState !== 'visible' || !canReload()) return
  if (!beforeReload()) return
  reloading = true
  window.location.reload()
}

async function controllerChanged() {
  const worker = navigator.serviceWorker.controller
  if (!worker) return
  const channel = new MessageChannel()
  const timeout = window.setTimeout(() => channel.port1.close(), 2000)
  channel.port1.onmessage = event => {
    clearTimeout(timeout)
    channel.port1.close()
    // A network navigation can already load newer HTML before its worker activates.
    const installed = event.data?.version
    if (typeof installed === 'string' && installed.localeCompare(version, undefined, { numeric: true }) > 0) {
      appUpdateReady.value = true
      applyAppUpdate()
    }
  }
  worker.postMessage({ type: 'app-version' }, [channel.port2])
}

export async function checkAppUpdate() {
  if (!registration || checking || !navigator.onLine || document.visibilityState !== 'visible') return
  checking = true
  try {
    await registration.update()
    navigator.serviceWorker.controller?.postMessage({ type: 'clean-old-app-caches' })
  } catch { /* Keep the already installed offline version when update checks fail. */ }
  finally { checking = false }
  void controllerChanged()
  applyAppUpdate()
}

export function startAppUpdates() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  navigator.serviceWorker.addEventListener('message', event => {
    if (event.data?.type === 'app-version') event.ports[0]?.postMessage({ version })
  })
  navigator.serviceWorker.addEventListener('controllerchange', () => { void controllerChanged() })
  // IME text may not have reached v-model yet. Wait until the editable control loses focus.
  const inputFocused = () => {
    appInputActive.value = document.activeElement instanceof HTMLElement && document.activeElement.matches('input:not([readonly]):not([disabled]), textarea:not([readonly]):not([disabled]), [contenteditable="true"], [contenteditable=""]')
  }
  document.addEventListener('focusin', inputFocused)
  document.addEventListener('focusout', () => { queueMicrotask(() => { inputFocused(); applyAppUpdate() }) })
  inputFocused()
  window.addEventListener('online', () => { void checkAppUpdate() })
  document.addEventListener('visibilitychange', () => { void checkAppUpdate() })
  window.addEventListener('pageshow', () => { void checkAppUpdate() })
  void navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' }).then(value => {
    registration = value
    void checkAppUpdate()
  }).catch(() => { window.dispatchEvent(new CustomEvent('offline-cache-error')) })
}
