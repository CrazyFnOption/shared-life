import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
createApp(App).mount('#app')
if (import.meta.env.PROD && 'serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => { window.dispatchEvent(new CustomEvent('offline-cache-error')) })
