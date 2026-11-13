import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import { startAppUpdates } from './appUpdates'
createApp(App).mount('#app')
startAppUpdates()
