<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from './api'
import Icon from './Icon.vue'
import { accountSource, sourceFor } from './sources'
import type { Account, AppSettings, PushSubscriptionState } from './types'
import { browserVersion, pushProvider, endpointFingerprint, workerVersion } from './deviceNotifications'
import { version } from '../package.json'

const props=defineProps<{account:Account;embedded?:boolean}>()
const emit=defineEmits<{close:[];restoreChanged:[];unauthorized:[]}>()
type DeviceSettings=Pick<AppSettings,'automation_paused'|'push_available'|'vapid_public_key'|'push_subscription_state'|'notification_pending'|'notification_uncertain'>
const settings=ref<DeviceSettings>(),message=ref(''),busy=ref(false),loading=ref(false),online=ref(navigator.onLine)
const supported='Notification' in window&&'serviceWorker' in navigator&&'PushManager' in window
const permission=ref<NotificationPermission|null>('Notification' in window?Notification.permission:null)
const localSubscribed=ref<boolean|null>(null),localReadFailed=ref(false)
const registrationState=ref<PushSubscriptionState>(),bindingStatus=ref<'checking'|'unknown'|'offline'|'none'|'verified'>('checking')
const provider=ref('待核对'),fingerprint=ref('待核对'),swInfo=ref('待核对')
const browser=browserVersion(navigator.userAgent)
const registrationExpired=computed(()=>bindingStatus.value==='verified'&&registrationState.value?.registered&&!registrationState.value.active)
const serverLabel=computed(()=>bindingStatus.value==='checking'?'正在核对…':bindingStatus.value==='offline'?'离线，待核对':bindingStatus.value==='none'?'本机没有订阅':bindingStatus.value!=='verified'?'未知，待核对':!registrationState.value?.registered?'未登记':registrationState.value.active?'已登记 · 有效':'登记已失效')
const deviceSummary=computed(()=>`同在 v${version}\n浏览器：${browser}\n授权：${permissionLabel.value}\n浏览器订阅：${localLabel.value}\n服务端登记：${serverLabel.value}\nService Worker：${swInfo.value}\n推送服务：${provider.value}\n端点指纹：${fingerprint.value}\n系统通知展示：未验证`)
const permissionLabel=computed(()=>!supported?'不支持通知':permission.value==='granted'?'已允许':permission.value==='denied'?'已阻止':'未开启')
const localLabel=computed(()=>!supported?'不支持通知':localReadFailed.value?'暂不可读取':localSubscribed.value===null?'正在读取…':localSubscribed.value?'订阅存在':'没有订阅')
const context=computed(()=>JSON.stringify([props.account.user.id,props.account.restore_id??'']))
let epoch=0,busyToken=0,settingsRequest=0,localRequest=0
function invalidate(){epoch++;busyToken++;settingsRequest++;localRequest++;busy.value=false;loading.value=false;settings.value=undefined;message.value='';localSubscribed.value=null;localReadFailed.value=false;registrationState.value=undefined;bindingStatus.value='unknown';provider.value='待核对';fingerprint.value='待核对';swInfo.value='待核对'}
function errorText(error:unknown){
 if(error instanceof ApiError){
  if(error.code==='restore_changed'){emit('restoreChanged');return '服务端已恢复，请同步后重试；本机待同步内容保留'}
  if(error.status===401||error.code==='account_changed'){emit('unauthorized');return '登录状态已变化，请恢复原账号'}
  return '暂时无法读取或保存通知设置，请联网后重试'
 }
 return error instanceof Error?error.message:'通知设置未完成，请联网后重试'
}
async function loadSettings(){
 const current=epoch,request=++settingsRequest,id=props.account.user.id
 if(!online.value)return
 loading.value=true
 try{const value=await api.settings(id,'private',sourceFor(props.account,{scope:'private',family_id:null}));if(current!==epoch||request!==settingsRequest)return;settings.value={automation_paused:value.automation_paused,push_available:value.push_available,vapid_public_key:value.vapid_public_key,push_subscription_state:value.push_subscription_state,notification_pending:value.notification_pending,notification_uncertain:value.notification_uncertain}}
 catch(error){if(current===epoch&&request===settingsRequest)message.value=errorText(error)}
 finally{if(current===epoch&&request===settingsRequest)loading.value=false}
}
async function loadLocal(){
 permission.value='Notification' in window?Notification.permission:null
 const current=epoch,request=++localRequest,origin=accountSource(props.account)
 registrationState.value=undefined;bindingStatus.value=online.value?'checking':'offline'
 if(!supported){localSubscribed.value=false;bindingStatus.value='none';swInfo.value='不支持';return}
 let subscription:PushSubscription|null|undefined
 try{
  const registration=await navigator.serviceWorker.getRegistration()
  if(current!==epoch||request!==localRequest)return
  const worker=registration?.active??navigator.serviceWorker.controller
  swInfo.value=worker?worker.state:'尚未激活'
  if(worker)void workerVersion(worker).then(value=>{if(current===epoch&&request===localRequest)swInfo.value=`${worker.state} · ${value}`})
  subscription=await registration?.pushManager.getSubscription()
  if(current!==epoch||request!==localRequest)return
  localSubscribed.value=!!subscription;localReadFailed.value=false
 }catch{if(current===epoch&&request===localRequest){localSubscribed.value=null;localReadFailed.value=true;bindingStatus.value='unknown'};return}
 if(!subscription){bindingStatus.value='none';provider.value='无订阅';fingerprint.value='无订阅';return}
 provider.value=pushProvider(subscription.endpoint)
 try{const value=await endpointFingerprint(subscription.endpoint);if(current===epoch&&request===localRequest)fingerprint.value=value}catch{if(current===epoch&&request===localRequest)fingerprint.value='待核对'}
 if(current!==epoch||request!==localRequest||!online.value)return
 try{const value=await api.checkPushSubscription(origin,subscription.endpoint);if(current!==epoch||request!==localRequest)return;registrationState.value=value;provider.value=value.provider;fingerprint.value=value.endpoint_fingerprint;bindingStatus.value='verified'}
 catch(error){if(current===epoch&&request===localRequest){bindingStatus.value='unknown';message.value=errorText(error)}}
}
async function copyDeviceSummary(){
 try{await navigator.clipboard.writeText(deviceSummary.value);message.value='本机通知摘要已复制；未包含完整端点、密钥或账号身份。'}catch{message.value='暂时无法复制，请选中下方摘要文本复制。'}
}
function applicationKey(value:string):ArrayBuffer{const padded=value.replace(/-/g,'+').replace(/_/g,'/');const raw=atob(padded+'='.repeat((4-padded.length%4)%4));return Uint8Array.from(raw,c=>c.charCodeAt(0)).buffer}
async function enablePush(){
 if(busy.value||!online.value)return
 const current=epoch,id=props.account.user.id,token=++busyToken;busy.value=true;message.value=''
 try{
  if(!settings.value?.push_available||!settings.value.vapid_public_key)throw new Error('服务端尚未配置推送，仍可打开应用处理相关内容。')
  if(!supported)throw new Error('当前浏览器不支持通知，请从支持通知的浏览器或主屏幕应用打开。')
  const result=await Notification.requestPermission()
  if(current!==epoch)return
  permission.value=result
  if(result!=='granted')throw new Error('尚未获得通知授权，请在系统或浏览器设置中允许后重试。')
  const registration=await navigator.serviceWorker.getRegistration()
  if(current!==epoch)return
  if(!registration)throw new Error('离线页面尚未准备好，请联网重开后重试。')
  let subscription=await registration.pushManager.getSubscription()
  if(current!==epoch)return
  if(!subscription)subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:applicationKey(settings.value.vapid_public_key)})
  if(current!==epoch)return
  await api.subscribe(accountSource(props.account),subscription.toJSON())
  if(current!==epoch)return
  await Promise.all([loadSettings(),loadLocal()])
  if(current===epoch&&!message.value)message.value=registrationState.value?.active?'本机订阅已登记；实际显示仍取决于设备与系统设置。':'本机订阅存在，服务端登记待核对。'
 }catch(error){if(current===epoch)message.value=errorText(error)}finally{if(token===busyToken)busy.value=false}
}
async function disablePush(){
 if(busy.value||!online.value)return
 const current=epoch,id=props.account.user.id,token=++busyToken;busy.value=true;message.value=''
 try{
  const registration=await navigator.serviceWorker?.getRegistration()
  if(current!==epoch)return
  const subscription=await registration?.pushManager.getSubscription()
  if(current!==epoch)return
  if(subscription){await api.unsubscribe(accountSource(props.account),subscription.endpoint);if(current!==epoch)return;await subscription.unsubscribe();if(current!==epoch)return}
  await Promise.all([loadSettings(),loadLocal()])
  if(current===epoch&&!message.value)message.value='本设备通知已关闭；其他设备和应用内相关内容不受影响。'
 }catch(error){if(current===epoch)message.value=errorText(error)}finally{if(token===busyToken)busy.value=false}
}
function close(){invalidate();emit('close')}
function connectionChanged(){online.value=navigator.onLine;if(!online.value){invalidate();message.value='当前离线，服务端登记待核对；浏览器订阅仍可查看。'}else{message.value='';void loadSettings()}void loadLocal()}
watch(context,()=>{invalidate();void loadSettings();void loadLocal()},{flush:'sync'})
onMounted(()=>{void loadSettings();void loadLocal();window.addEventListener('online',connectionChanged);window.addEventListener('offline',connectionChanged)})
onUnmounted(()=>{invalidate();window.removeEventListener('online',connectionChanged);window.removeEventListener('offline',connectionChanged)})
</script>
<template>
 <div :class="embedded?'config-page':'modal-backdrop'" @click.self="!embedded&&close()" @keydown.esc="close"><section :class="embedded?'config-panel':'form-card device-card'" :role="embedded?'region':'dialog'" :aria-modal="embedded?undefined:true" aria-label="通知与设备">
  <div class="form-title" :class="{'config-page-head':embedded}"><button v-if="embedded" class="config-back" @click="close"><Icon name="chevron"/>返回</button><div><p v-if="embedded" class="config-kicker">配置 · 通知设置</p><h2 tabindex="-1">通知与设备</h2></div><button v-if="!embedded" class="icon-button" @click="close" aria-label="关闭通知与设备"><Icon name="close"/></button></div>
  <p v-if="message" class="notice" role="status" aria-live="polite">{{ message }}</p>
  <p v-if="loading" class="form-note" role="status">正在读取通知设置…</p>
  <section class="device-section" aria-label="本设备通知"><h3>本设备通知</h3><p class="form-note">备忘通知显示标题、提醒时间及简短正文摘要，锁屏上可能可见；月报和补评仍只显示固定提示，不包含账务明细。点击开启后才请求系统授权。</p>
   <dl class="device-status"><div><dt>浏览器授权</dt><dd>{{ permissionLabel }}</dd></div><div><dt>浏览器订阅</dt><dd>{{ localLabel }}</dd></div><div><dt>本机服务端登记</dt><dd>{{ serverLabel }}</dd></div></dl>
   <p v-if="!supported" class="form-note">请从支持通知的浏览器或主屏幕应用打开。</p>
   <p v-if="permission==='denied'" class="form-note">请在系统或浏览器设置中允许通知后重试。</p>
   <p v-if="settings?.push_available===false" class="form-note">服务端尚未配置推送，仍可打开应用处理相关内容。</p>
   <p v-if="registrationExpired" class="notice">本机登记已失效，请重新开启通知。</p>
   <div class="device-actions"><button class="primary" @click="enablePush" :disabled="busy||loading||!online||!supported||!settings?.push_available">{{ busy?'正在处理…':registrationExpired?'重新开启通知':'开启通知' }}</button><button class="subtle" @click="disablePush" :disabled="busy||!online||!supported">关闭本设备通知</button></div>
   <p class="form-note">关闭仅影响本设备，不会更改浏览器的授权设置。登记有效不代表手机已显示系统通知，实际送达仍取决于设备与系统设置。</p>
   <details class="settings-disclosure device-diagnostics"><summary>本机通知信息</summary><p class="form-note">可复制版本、授权和短指纹用于核对；不含完整端点、密钥、身份或提醒正文。</p><pre class="device-summary">{{ deviceSummary }}</pre><div class="action-row"><button class="subtle" @click="loadLocal" :disabled="busy">重新核对</button><button class="subtle" @click="copyDeviceSummary">复制摘要</button></div></details>
  </section>
  <section class="device-section" aria-label="服务端通知处理"><h3>服务端通知处理</h3><p v-if="settings" class="device-delivery">账号有 {{ settings.notification_pending??0 }} 项通知待处理或结果待确认<span v-if="settings.notification_uncertain">，其中 {{ settings.notification_uncertain }} 项提交结果待确认</span></p><p class="form-note">服务端处理或提交成功不代表手机已显示系统通知。仍可打开应用查看和处理相关内容。</p></section>
  <section v-if="settings?.automation_paused" class="device-section"><h3>自动化已暂停</h3><p class="notice">服务端恢复后自动化已暂停，需管理员核对后恢复。开启通知不会解除暂停。</p></section>
 </section></div>
</template>
