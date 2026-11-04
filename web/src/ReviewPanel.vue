<script setup lang="ts">
import { reviewText } from './ratings'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from './api'
import { money } from './money'
import { netAmount } from './operations'
import { ReportGate, reportContext, reportStatus } from './reports'
import Icon from './Icon.vue'
import { sourceFor, sameSource } from './sources'
import type { LedgerStore } from './store'
import type { Account, AppSettings, Report, Scope, Source } from './types'
const props=defineProps<{account:Account;store:LedgerStore;scope:Scope;embedded?:boolean}>()
const emit=defineEmits<{close:[];restoreChanged:[];unauthorized:[]}>()
const scope=computed(()=>props.scope),reports=ref<Report[]>([]),selected=ref<Report>(),settings=ref<AppSettings>()
const loading=ref(false),busy=ref(false),detailLoading=ref(false),message=ref(''),settingsError=ref('')
const page=ref(1),pageSize=10
const reviewTab=ref<'weekly'|'reports'|'export'>('weekly')
const pages=computed(()=>Math.max(1,Math.ceil(reports.value.length/pageSize)))
const exportScope=ref<Scope|'all'>(props.account.family?'all':'private'),exportFormat=ref<'json'|'csv'>('json')
const gate=new ReportGate()
function source():Source{if(!props.account.source)throw new Error('请先联网核对来源');return props.account.source}
async function verify(captured:Source){await props.store.verifySource(captured.user_id,captured);if(!sameSource(captured,props.account.source))throw new Error('当前使用范围已变化')}
let epoch=0,listRequest=0,detailRequest=0,settingsRequest=0,busyToken=0
function startBusy(){busy.value=true;return ++busyToken}
function finishBusy(token:number){if(token===busyToken)busy.value=false}
const context=computed(()=>reportContext(props.account))
const settingsContext=computed(()=>JSON.stringify([props.account.user.id,props.account.restore_id??'',props.account.family?.id??null,props.account.membership_version,scope.value]))
let settingsDomain=settingsContext.value
const monthly=ref({enabled:false,time:'20:00'}),weekly=ref({enabled:false,weekday:6,time:'19:00'})
const hasUnsavedChanges=computed(()=>!!settings.value&&(monthly.value.enabled!==settings.value.monthly.enabled||monthly.value.time!==settings.value.monthly.time||weekly.value.enabled!==settings.value.weekly.enabled||weekly.value.weekday!==settings.value.weekly.weekday||weekly.value.time!==settings.value.weekly.time))
const isBusy=computed(()=>busy.value)
defineExpose({hasUnsavedChanges,isBusy})
function applySettings(value:AppSettings){
 const previous=settings.value
 // A fresh online baseline must not replace inputs kept through a network interruption.
 if(!previous||monthly.value.enabled===previous.monthly.enabled&&monthly.value.time===previous.monthly.time)monthly.value={enabled:value.monthly.enabled,time:value.monthly.time}
 if(!previous||weekly.value.enabled===previous.weekly.enabled&&weekly.value.weekday===previous.weekly.weekday&&weekly.value.time===previous.weekly.time)weekly.value={...value.weekly}
 settings.value=value
}
function errorText(error:unknown){
 if(error instanceof ApiError){if(error.code==='restore_changed'){emit('restoreChanged');return '服务端已恢复，正在同步新状态；本机草稿仍保留'}if(error.status===401||error.code==='account_changed'){emit('unauthorized');return '登录状态已变化，请恢复原账号'}if(error.status===404)return '报告已不可访问，请刷新列表'}
 return '暂时无法完成，请联网后重试'
}
async function loadSettings(){const current=epoch,request=++settingsRequest,book=scope.value,captured=source();settingsError.value='';try{const value=await api.settings(props.account.user.id,book,captured);await verify(captured);if(current===epoch&&request===settingsRequest&&book===scope.value)applySettings(value)}catch(error){if(current===epoch&&request===settingsRequest)settingsError.value=errorText(error)}}
async function loadReports(){
 const current=epoch,request=++listRequest,id=props.account.user.id,book=scope.value,captured=source()
 loading.value=true;detailLoading.value=false;message.value='';selected.value=undefined;gate.invalidate();detailRequest++
 try{const result=await api.reports(id,book,captured);await verify(captured);if(current!==epoch||request!==listRequest)return;reports.value=result.reports.filter(r=>r.scope===book&&(book==='shared'||r.owner_id===id)).sort((a,b)=>b.month.localeCompare(a.month));page.value=Math.min(page.value,pages.value)}
 catch(error){if(current===epoch&&request===listRequest){reports.value=[];message.value=errorText(error)}}
 finally{if(current===epoch&&request===listRequest)loading.value=false}
}
async function openReport(item:Report){
 const current=epoch,request=++detailRequest,id=props.account.user.id,token=gate.begin(context.value,item),captured=source()
 selected.value=undefined;detailLoading.value=true;message.value=''
 try{
  const result=await api.report(id,item.id,captured)
  // Recheck metadata after the potentially slow detail response. Revoked/stale content stays closed.
  const latest=await api.reports(id,item.scope,captured);await verify(captured)
  if(current!==epoch||request!==detailRequest)return
  const descriptor=latest.reports.find(r=>r.id===item.id)
  if(!descriptor||!gate.accepts(token,context.value,id,result)||descriptor.data_version!==result.data_version||descriptor.status!==result.status||descriptor.scope!==item.scope){message.value='报告内容或可见范围已变化，请刷新后重新打开';return}
  selected.value=result
 }catch(error){if(current===epoch&&request===detailRequest)message.value=errorText(error)}
 finally{if(current===epoch&&request===detailRequest)detailLoading.value=false}
}
async function saveSettings(part:'monthly'|'weekly'){
 if(busy.value)return;const current=epoch,token=startBusy(),captured=source();settingsError.value=''
 const submitted=part==='monthly'?{monthly:{...monthly.value}}:{weekly:{...weekly.value}}
 try{const value=await api.saveSettings(props.account.user.id,scope.value,submitted,captured);await verify(captured);if(current===epoch){
  // A partial save must not discard edits in the other form or edits made in flight.
  if(submitted.monthly&&monthly.value.enabled===submitted.monthly.enabled&&monthly.value.time===submitted.monthly.time)monthly.value={enabled:value.monthly.enabled,time:value.monthly.time}
  if(submitted.weekly&&weekly.value.enabled===submitted.weekly.enabled&&weekly.value.weekday===submitted.weekly.weekday&&weekly.value.time===submitted.weekly.time)weekly.value={...value.weekly}
  settings.value=value;settingsError.value='时间已保存（上海时间）'
 }}
 catch(error){if(current===epoch)settingsError.value=errorText(error)}finally{finishBusy(token)}
}
async function retryReport(item:Report){if(busy.value)return;const current=epoch,token=startBusy(),captured=source();message.value='';try{await api.retryReport(props.account.user.id,item.id,captured);await verify(captured);if(current===epoch){await loadReports();if(current===epoch)message.value='已重新排入生成待办'}}catch(error){if(current===epoch)message.value=errorText(error)}finally{finishBusy(token)}}
async function download(){
 if(busy.value)return;const current=epoch,id=props.account.user.id,format=exportFormat.value,book=exportScope.value,token=startBusy(),captured=book==='private'?sourceFor(props.account,{scope:'private',family_id:null}):source(),viewSource=source();settingsError.value=''
 try{const blob=await api.export(id,format,book,captured);await props.store.verifySource(id,captured);await verify(viewSource);if(current!==epoch)return;const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`同在-${book}-${new Date().toISOString().slice(0,10)}.${format}`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);settingsError.value='导出已交给浏览器下载；仅包含当前共同及本人私人范围'}catch(error){if(current===epoch)settingsError.value=errorText(error)}finally{finishBusy(token)}
}
function date(value:string|null){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未生成'}
function syncDate(value?:string|null){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未知'}
function payer(id:string){return props.account.users.find(u=>u.id===id)?.display_name??'付款人'}
function analysis(report:Report,id:string){return report.content?.items.find(i=>i.expense_id===id)?.analysis??''}
function invalidate(preserveSettings=false){epoch++;gate.invalidate();listRequest++;detailRequest++;settingsRequest++;selected.value=undefined;reports.value=[];detailLoading.value=false;loading.value=false;message.value='';settingsError.value='';if(!preserveSettings){settings.value=undefined;busyToken++;busy.value=false}}
watch([scope,context],()=>{const next=settingsContext.value;invalidate(next===settingsDomain);settingsDomain=next;page.value=1;if(!props.account.family)exportScope.value='private';void loadReports();void loadSettings()},{flush:'sync'})
function offline(){invalidate(true);message.value='报告仅在线读取，请联网后刷新'}
onMounted(()=>{void loadReports();void loadSettings();window.addEventListener('offline',offline)})
onUnmounted(()=>{invalidate();window.removeEventListener('offline',offline)})
</script>
<template>
 <div :class="embedded?'config-page':'modal-backdrop review-backdrop'" @click.self="!embedded&&emit('close')"><section :class="embedded?'config-panel review-card':'form-card review-card'" :role="embedded?'region':'dialog'" :aria-modal="embedded?undefined:true" aria-label="复盘与导出">
  <div class="form-title" :class="{'config-page-head':embedded}"><button v-if="embedded" class="config-back" @click="emit('close')"><Icon name="chevron"/>返回</button><div><p v-if="embedded" class="config-kicker">{{ scope==='shared'?(account.family?.name+' · 共同账'):'私人账 · 仅本人' }}</p><h2 tabindex="-1">复盘与导出</h2><p class="form-note">报告仅联网读取，私人报告仅本人可见。</p></div><button v-if="!embedded" class="icon-button" @click="emit('close')" aria-label="关闭复盘与导出"><Icon name="close"/></button></div>
  <nav class="review-sections" aria-label="复盘与导出任务"><button :aria-pressed="reviewTab==='weekly'" @click="reviewTab='weekly'">周补评</button><button :aria-pressed="reviewTab==='reports'" @click="reviewTab='reports'">历史复盘</button><button :aria-pressed="reviewTab==='export'" @click="reviewTab='export'">导出账本</button></nav>
  <section v-show="reviewTab==='reports'" class="review-task" aria-label="历史复盘">
  <p v-if="settings&&!settings.provider_configured" class="form-note">月度 AI 复盘暂未启用，现有报告仍可查看。</p>
  <details class="report-sync"><summary>核对成员与本机同步状态</summary><p v-for="user in account.users.length?account.users:[account.user]" :key="user.id">{{ user.display_name }}最近连接同步：{{ syncDate(user.last_synced_at) }}（上海时间）</p><p>本机待同步：{{ account.queue.length }} 项账务操作、{{ account.images.filter(image=>image.pending).length }} 项附件、{{ account.categoryQueue.length }} 项分类。</p><p>该时间不代表所有设备都已上传。复盘前请当前家庭成员确认各自设备没有待同步内容。</p></details>
  <nav class="review-tabs" aria-label="报告操作"><span class="form-note">{{ scope==='shared'?'共同月报':'私人月报' }}</span><button class="subtle" @click="loadReports" :disabled="loading">刷新报告</button></nav>
  <p v-if="message" class="notice" role="status">{{ message }}</p><p v-if="loading || detailLoading" class="form-note">{{ detailLoading?'读取在线报告…':'读取报告列表…' }}</p>
  <p v-if="!loading&&!reports.length" class="empty">暂无{{ scope==='shared'?'共同':'私人' }}月报</p>
  <article v-for="item in reports.slice((page-1)*pageSize,page*pageSize)" :key="item.id" class="report-item"><div><h3>{{ item.month }} · {{ scope==='shared'?'共同':'私人' }}复盘</h3><p>{{ reportStatus(item.status) }} · 净支出 ¥{{ money(item.summary.total_cents) }}</p><small>更新：{{ date(item.generated_at) }}</small></div><button class="subtle" @click="openReport(item)" :disabled="detailLoading">查看报告</button></article>
  <div v-if="pages>1" class="pagination"><button class="subtle" @click="page--" :disabled="page===1">上一页报告</button><span>{{ page }} / {{ pages }}</span><button class="subtle" @click="page++" :disabled="page===pages">下一页报告</button></div>
  <section v-if="selected" class="report-detail" aria-label="月报详情"><div class="form-title"><h3>{{ selected.month }}复盘 · {{ reportStatus(selected.status) }}</h3><button class="text-button" @click="selected=undefined;gate.invalidate();detailRequest++">收起报告</button></div><p class="form-note">数据版本：<span class="data-version">{{ selected.data_version }}</span><br>更新时间：{{ date(selected.generated_at) }}<br>模型：{{ selected.provider||'未生成' }} / {{ selected.model||'未生成' }}</p><p v-if="selected.error" class="field-error">{{ selected.error }}</p><p>净支出 ¥{{ money(selected.summary.total_cents) }} · {{ selected.summary.unrated_count }} 笔未评价</p><p v-for="amount in selected.summary.by_payer" :key="amount.user_id" class="form-note">{{ payer(amount.user_id) }}：¥{{ money(amount.amount_cents) }}</p><template v-if="selected.status==='ready'&&selected.content"><p class="report-prose">{{ selected.content.summary }}</p><article v-for="expense in selected.expenses??[]" :key="expense.id" class="report-expense"><h4>{{ expense.purpose }}</h4><p>原款 ¥{{ money(expense.amount_cents) }} · 退款 ¥{{ money(expense.amount_cents-netAmount(expense)) }} · 净额 ¥{{ money(netAmount(expense)) }}</p><p>{{ reviewText(expense.rating, expense.rating_reason) }}</p><p class="report-prose">{{ analysis(selected,expense.id) }}</p></article></template><p v-else class="form-note">当前正文不可用。过期报告需要按最新账目重新生成。</p><button v-if="['failed','unconfigured','stale'].includes(selected.status)" class="subtle" @click="retryReport(selected)" :disabled="busy">重新排入生成待办</button></section>
  <details v-if="settings" class="settings-disclosure review-settings"><summary>月报时间与启用状态</summary><p class="form-note">时间均为上海时间。月报在每月第一个星期日生成上月复盘；共同与私人时间分别保存。</p><form class="settings-group stack" @submit.prevent="saveSettings('monthly')"><label class="check-label"><input v-model="monthly.enabled" type="checkbox" :disabled="!settings?.provider_configured">启用{{ scope==='shared'?'共同':'私人' }}月报</label><label>{{ scope==='shared'?'共同':'私人' }}月报时间<input v-model="monthly.time" type="time" required></label><p class="form-note">{{ settings.monthly.updated_by?`最近修改：${payer(settings.monthly.updated_by)}`:'尚未选择启用时间；当前显示建议默认值。' }}</p><button class="subtle" :disabled="busy">保存月报时间</button></form></details>
  </section>
  <section v-show="reviewTab==='weekly'" class="review-task" aria-label="周补评提醒"><h3>每周回顾消费感受</h3><p class="form-note">在约定时间提醒你补记评价，仅调整本人的提醒。时间均为上海时间。</p><form v-if="settings" class="settings-group stack" @submit.prevent="saveSettings('weekly')"><label class="check-label"><input v-model="weekly.enabled" type="checkbox">启用我的周补评提醒</label><label>每周<select v-model="weekly.weekday"><option v-for="(name,n) in ['星期一','星期二','星期三','星期四','星期五','星期六','星期日']" :key="n" :value="n">{{ name }}</option></select></label><label>个人周提醒时间<input v-model="weekly.time" type="time" required></label><p class="form-note">只修改本人提醒；未启用时显示建议默认值。</p><button class="primary" :disabled="busy">保存个人提醒</button></form></section>
  <section v-show="reviewTab==='export'" class="review-task" aria-label="导出账本"><h3>导出当前账本</h3><p class="form-note">导出当前有效记录、退款净额和分类；图片只含信息，不含图片文件或修改历史。</p><div class="export-options"><label>导出范围<select v-model="exportScope"><option v-if="account.family" value="all">共同与本人私人</option><option v-if="account.family" value="shared">仅共同账</option><option value="private">仅本人私人账</option></select></label><label>文件格式<select v-model="exportFormat"><option value="json">JSON</option><option value="csv">CSV（Excel）</option></select></label></div><button class="primary" @click="download" :disabled="busy">导出并下载</button>  <details class="report-sync"><summary>核对成员与本机同步状态</summary><p v-for="user in account.users.length?account.users:[account.user]" :key="user.id">{{ user.display_name }}最近连接同步：{{ syncDate(user.last_synced_at) }}（上海时间）</p><p>本机待同步：{{ account.queue.length }} 项账务操作、{{ account.images.filter(image=>image.pending).length }} 项附件、{{ account.categoryQueue.length }} 项分类。</p><p>该时间不代表所有设备都已上传。复盘前请当前家庭成员确认各自设备没有待同步内容。</p></details>
</section>
  <p v-if="settings?.automation_paused" class="notice">服务端恢复后自动化已暂停，需管理员核对后恢复；修改时间不会解除暂停。</p>
  <p v-if="settingsError" class="notice" role="status">{{ settingsError }}</p>
 </section></div>
</template>
