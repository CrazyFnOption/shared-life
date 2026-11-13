<script setup lang="ts">
import { reviewText, ratingLabels } from './ratings'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from './api'
import { money } from './money'
import { netAmount } from './operations'
import { ReportGate, reportContext, reportStatus, reportCostNotice, reportDisplayError } from './reports'
import Icon from './Icon.vue'
import { sourceFor, sameSource } from './sources'
import type { LedgerStore } from './store'
import type { Account, AppSettings, Expense, Report, Scope, Source } from './types'
const props=defineProps<{account:Account;store:LedgerStore;scope:Scope;pane:'review'|'export';active:boolean;target?:{tab:'weekly'|'reports';reportId?:string;notice?:string}}>()
const emit=defineEmits<{restoreChanged:[];unauthorized:[];changed:[]}>()
const scope=computed(()=>props.scope),reports=ref<Report[]>([]),selected=ref<Report>(),settings=ref<AppSettings>()
const loading=ref(false),busy=ref(false),detailLoading=ref(false),message=ref(''),settingsError=ref('')
const page=ref(1),pageSize=10
const reviewTab=ref<'weekly'|'reports'>(props.target?.tab??'reports')
const unratedExpenses=computed(()=>props.account.local.filter(item=>!item.voided&&item.owner_id===props.account.user.id&&item.rating==null))
const ratingEditor=ref<{expense:Expense;rating:string;reason:string}>(),ratingError=ref(''),ratingForm=ref<HTMLFormElement>()
const costNotice=computed(()=>selected.value?reportCostNotice(selected.value):''),reportError=computed(()=>selected.value?reportDisplayError(selected.value):'')
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
const hasUnsavedChanges=computed(()=>!!ratingEditor.value||!!settings.value&&(monthly.value.enabled!==settings.value.monthly.enabled||monthly.value.time!==settings.value.monthly.time||weekly.value.enabled!==settings.value.weekly.enabled||weekly.value.weekday!==settings.value.weekly.weekday||weekly.value.time!==settings.value.weekly.time))
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
function lastMonth(){const [year,month]=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).format(new Date()).split('-').map(Number);return new Date(Date.UTC(year!,month!-2,1)).toISOString().slice(0,7)}
async function loadReports(desiredId=selected.value?.id){
 const current=epoch,request=++listRequest,id=props.account.user.id,book=scope.value,captured=source()
 loading.value=true;detailLoading.value=false;message.value='';selected.value=undefined;gate.invalidate();detailRequest++
 try{const result=await api.reports(id,book,captured);await verify(captured);if(current!==epoch||request!==listRequest)return;reports.value=result.reports.filter(r=>r.scope===book&&(book==='shared'||r.owner_id===id)).sort((a,b)=>b.month.localeCompare(a.month));page.value=Math.min(page.value,pages.value);const candidate=desiredId?reports.value.find(r=>r.id===desiredId):reports.value.find(r=>r.month===lastMonth())??reports.value[0];if(candidate)await openReport(candidate);else if(desiredId)message.value='这份报告当前不可访问，请从列表核对当前状态。'}
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
function editRating(expense:Expense){const current=props.account.local.find(item=>item.id===expense.id&&!item.voided);if(!current||current.owner_id!==props.account.user.id)return;ratingEditor.value={expense:{...current},rating:current.rating==null?'':String(current.rating),reason:current.rating_reason??''};ratingError.value='';void nextTick(()=>{ratingForm.value?.scrollIntoView({block:'nearest'});ratingForm.value?.querySelector('select')?.focus()})}
async function saveRating(){if(!ratingEditor.value||busy.value)return;const draft=ratingEditor.value,current=epoch,captured={...source()},token=startBusy();ratingError.value='';if(!draft.rating||![1,2,3,4,5].includes(Number(draft.rating))){ratingError.value='请选择你实际的满意度；未选择时不会写入评分。';finishBusy(token);return}try{await props.store.maintain(props.account.user.id,draft.expense,{kind:'review',data:{rating:Number(draft.rating),rating_reason:draft.reason.trim()}},captured.restore_id,captured);if(current!==epoch||!sameSource(captured,props.account.source))return;ratingEditor.value=undefined;if(selected.value)selected.value={...selected.value,status:'stale',content:null};message.value='评价已保存到本机，待同步后自动更新月报。';emit('changed')}catch(error){if(current===epoch)ratingError.value=error instanceof Error?error.message:'评价未保存，输入已保留。'}finally{finishBusy(token)}}
function shortStatus(item:Report){return item.status==='stale'?'待更新':item.status==='running'?'更新中':reportStatus(item.status)}
function date(value:string|null){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未生成'}
function syncDate(value?:string|null){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'未知'}
function payer(id:string){return props.account.users.find(u=>u.id===id)?.display_name??'付款人'}
function analysis(report:Report,id:string){return report.content?.items.find(i=>i.expense_id===id)?.analysis??''}
function invalidate(preserveSettings=false){epoch++;gate.invalidate();listRequest++;detailRequest++;settingsRequest++;selected.value=undefined;reports.value=[];detailLoading.value=false;loading.value=false;message.value='';settingsError.value='';if(!preserveSettings){ratingEditor.value=undefined;ratingError.value='';settings.value=undefined;busyToken++;busy.value=false}}
watch([scope,context],()=>{const next=settingsContext.value;invalidate(next===settingsDomain);settingsDomain=next;page.value=1;if(!props.account.family)exportScope.value='private';void loadReports();void loadSettings()},{flush:'sync'})
watch(()=>props.target,async target=>{if(!target)return;reviewTab.value=target.tab;await loadReports(target.reportId);if(target.notice&&!message.value)message.value=target.notice})
function offline(){invalidate(true);message.value='报告仅在线读取，请联网后刷新'}
let poll:ReturnType<typeof setInterval>|undefined
onMounted(()=>{poll=setInterval(()=>{if(props.active&&reviewTab.value==='reports'&&!ratingEditor.value&&!busy.value&&!loading.value&&!detailLoading.value&&selected.value&&['pending','running','stale'].includes(selected.value.status))void loadReports()},5000);void loadReports(props.target?.reportId).then(()=>{if(props.target?.notice&&!message.value)message.value=props.target.notice});void loadSettings();window.addEventListener('offline',offline)})
onUnmounted(()=>{clearInterval(poll);invalidate();window.removeEventListener('offline',offline)})
</script>
<template>
 <section class="finance-review" :aria-label="pane==='export'?'导出账本':'财务复盘'">
  <div v-show="pane==='review'" class="review-intro"><h2>财务复盘</h2><p class="form-note">回看每月支出，补记真实感受。私人报告仅本人可见。</p></div>
  <nav v-show="pane==='review'" class="review-sections" aria-label="复盘任务"><button :aria-pressed="reviewTab==='reports'" @click="reviewTab='reports'">月度复盘</button><button :aria-pressed="reviewTab==='weekly'" @click="reviewTab='weekly'">本人补评</button></nav>
  <form v-if="ratingEditor" v-show="pane==='review'" ref="ratingForm" aria-label="补记我的评价" class="monthly-rating stack" @submit.prevent="saveRating"><h3>{{ ratingEditor.expense.purpose }} · 我的评价</h3><label>我的满意度<select v-model="ratingEditor.rating" :disabled="busy"><option value="">选择评分</option><option v-for="(name,index) in ratingLabels" :key="index" :value="String(index+1)">{{ index+1 }} 分 · {{ name }}</option></select></label><label>一句原因（选填）<textarea v-model="ratingEditor.reason" rows="2" maxlength="500" :disabled="busy"></textarea></label><p v-if="ratingError" role="alert" class="field-error">{{ ratingError }}</p><div class="monthly-rating-actions"><button type="button" class="text-button" style="flex-shrink:0;white-space:nowrap" :disabled="busy" @click="ratingEditor=undefined">取消</button><button class="primary" :disabled="busy">{{ busy?'正在保存…':'保存评价' }}</button></div></form>
  <section v-show="pane==='review'&&reviewTab==='reports'" class="review-task monthly-review" aria-label="月度复盘">
   <p v-if="settings&&!settings.provider_configured" class="notice">月度 AI 尚未配置，账目和已同步金额仍可查看。</p>
   <div class="review-tabs"><span class="form-note">{{ scope==='shared'?'共同月报':'私人月报' }} · 自动复盘上个月</span><button class="text-button" @click="loadReports()" :disabled="loading||busy||!!ratingEditor">刷新</button></div>
   <p v-if="message" class="notice" role="status">{{ message }}</p><p v-if="loading||detailLoading" class="form-note" role="status">{{ detailLoading?'读取在线报告…':'读取报告列表…' }}</p>
   <p v-if="!loading&&!reports.length" class="empty">上月报告尚未生成。每月首个周日按约定时间自动复盘，无需手动生成。</p>
   <section v-if="selected" class="report-detail monthly-detail" aria-label="月报详情">
    <div class="monthly-heading"><h3>{{ selected.month }} {{ scope==='shared'?'共同':'私人' }}复盘</h3><span class="monthly-status" role="status">{{ shortStatus(selected) }}</span></div>
    <div class="monthly-total"><span class="form-note">{{ scope==='shared'?'共同':'私人' }}净支出</span><strong>¥{{ money(selected.summary.total_cents) }}</strong><div class="monthly-payers"><div v-for="amount in selected.summary.by_payer" :key="amount.user_id"><span class="form-note">{{ payer(amount.user_id) }}支付净额</span><b>¥{{ money(amount.amount_cents) }}</b></div></div><p class="form-note">已扣除退款 · 仅含已同步记录</p></div>
    <h3 class="monthly-section-title">这个月，钱花得怎样</h3><p v-if="reportError" class="field-error">{{ reportError }}</p><p v-if="costNotice" class="form-note" role="status">{{ costNotice }}</p>
    <p v-if="selected.status==='ready'&&selected.content" class="report-prose monthly-prose">{{ selected.content.summary }}</p><p v-else class="form-note">{{ selected.status==='unconfigured'?'AI 尚未配置，消费明细与已提交评价仍可查看。':selected.status==='failed'?'AI 分析暂不可用，金额与评价保留。':'分析正在等待更新，以下金额与明细来自最新已同步记录。' }}</p>
    <p v-if="selected.summary.unrated_count" class="monthly-unrated">{{ selected.summary.unrated_count }} 笔消费未评价，暂按一般情况分析。评分仍为空，不代表付款人提交了 3 分。</p>
    <h3 class="monthly-section-title">逐笔看看</h3><p class="form-note">展开查看付款人的评价与价值解释</p>
    <details v-for="expense in selected.expenses??[]" :key="expense.id" class="monthly-expense"><summary><span><strong>{{ expense.purpose }}</strong><small>{{ payer(expense.owner_id) }}支付 · {{ expense.rating==null?'未评价':expense.rating+' 分' }}</small></span><b>¥{{ money(netAmount(expense)) }} <Icon name="chevron"/></b></summary><div class="monthly-expense-body"><p class="form-note">原款 ¥{{ money(expense.amount_cents) }} · 已退款 ¥{{ money(expense.amount_cents-netAmount(expense)) }}</p><p>{{ expense.rating==null?'付款人尚未提交评分与原因':payer(expense.owner_id)+'的评价 · '+expense.rating+' 分' }}{{ expense.rating_reason?'：'+expense.rating_reason:'' }}</p><p v-if="selected.status==='ready'&&selected.content" class="report-prose">{{ analysis(selected,expense.id) }}</p><p v-else-if="expense.rating==null" class="form-note">未评价项按一般情况分析，实际感受留待付款人补充。</p><button v-if="expense.owner_id===account.user.id&&!ratingEditor" class="subtle" @click="editRating(expense)">{{ expense.rating==null?'补充我的评价':'调整我的评价' }}</button>

    </div></details>
    <p class="form-note monthly-report-meta">数据截止：{{ syncDate(account.synced_at) }}<br>最近更新：{{ date(selected.generated_at) }}<br>实际模型：{{ selected.model||'尚未使用' }}</p>
    <button v-if="['failed','unconfigured'].includes(selected.status)" class="text-button" @click="retryReport(selected)" :disabled="busy||!!ratingEditor">恢复后重新排入待办</button>
   </section>
   <details v-if="reports.length>1" class="monthly-history"><summary>查看其他月份</summary><article v-for="item in reports.slice((page-1)*pageSize,page*pageSize)" :key="item.id" class="report-item"><div><h3>{{ item.month }} · {{ shortStatus(item) }}</h3><p>净支出 ¥{{ money(item.summary.total_cents) }}</p></div><button class="subtle" @click="openReport(item)" :disabled="detailLoading||!!ratingEditor">查看</button></article><div v-if="pages>1" class="pagination"><button class="subtle" @click="page--" :disabled="page===1">上一页</button><span>{{ page }} / {{ pages }}</span><button class="subtle" @click="page++" :disabled="page===pages">下一页</button></div></details>
   <details class="report-sync"><summary>数据与同步范围</summary><p class="form-note">只使用当前{{ scope==='shared'?'家庭的共同':'本人的私人' }}记录。</p><p v-for="user in account.users.length?account.users:[account.user]" :key="user.id">{{ user.display_name }}最近连接同步：{{ syncDate(user.last_synced_at) }}（上海时间）</p><p>本机待同步：{{ account.queue.length }} 项账务操作、{{ account.images.filter(image=>image.pending).length }} 项附件、{{ account.categoryQueue.length }} 项分类。</p><p>该时间不代表所有设备都已上传，请各自核对尚未同步的记录。</p></details>
   <details v-if="settings" class="settings-disclosure review-settings"><summary>复盘设置</summary><p class="form-note">每月第一个周日，按上海时间复盘上个月；{{ scope==='shared'?'当前家庭共用同一个时间，成员修改会共同生效':'私人时间独立保存，不受家庭修改影响' }}。</p><form class="settings-group stack" @submit.prevent="saveSettings('monthly')"><label class="check-label"><input v-model="monthly.enabled" type="checkbox" :disabled="!settings?.provider_configured">启用自动月复盘</label><label>晚间生成时间<input v-model="monthly.time" type="time" required></label><p class="form-note">{{ settings.monthly.updated_by?`最近修改：${payer(settings.monthly.updated_by)}`:'尚未选择启用时间；当前显示建议默认值。' }}</p><button class="subtle" :disabled="busy">保存月报时间</button></form></details>
  </section>
  <section v-show="pane==='review'&&reviewTab==='weekly'" class="review-task" aria-label="周补评提醒"><h3>本人待补评价</h3><p class="form-note">仅列当前范围中由你支付、尚未评价的消费。查看不会写入评分。</p><div v-if="unratedExpenses.length" class="stack"><button v-for="expense in unratedExpenses" :key="expense.id" type="button" class="subtle" :disabled="busy" :aria-label="`补记评价：${expense.purpose}`" @click="editRating(expense)">{{ expense.purpose }} · 补记评价</button></div><p v-else class="empty">当前范围没有待补评价。</p><h3>每周回顾消费感受</h3><p class="form-note">在约定时间提醒你补记评价，仅调整本人的提醒。时间均为上海时间。</p><form v-if="settings" class="settings-group stack" @submit.prevent="saveSettings('weekly')"><label class="check-label"><input v-model="weekly.enabled" type="checkbox">启用我的周补评提醒</label><label>每周<select v-model="weekly.weekday"><option v-for="(name,n) in ['星期一','星期二','星期三','星期四','星期五','星期六','星期日']" :key="n" :value="n">{{ name }}</option></select></label><label>个人周提醒时间<input v-model="weekly.time" type="time" required></label><p class="form-note">只修改本人提醒；未启用时显示建议默认值。</p><button class="primary" :disabled="busy">保存个人提醒</button></form></section>
  <section v-show="pane==='export'" class="review-task" aria-label="导出账本"><h3>导出当前账本</h3><p class="form-note">导出当前有效记录、退款净额和分类；图片只含信息，不含图片文件或修改历史。</p><div class="export-options"><label>导出范围<select v-model="exportScope"><option v-if="account.family" value="all">共同与本人私人</option><option v-if="account.family" value="shared">仅共同账</option><option value="private">仅本人私人账</option></select></label><label>文件格式<select v-model="exportFormat"><option value="json">JSON</option><option value="csv">CSV（Excel）</option></select></label></div><button class="primary" @click="download" :disabled="busy">导出并下载</button>  <details class="report-sync"><summary>核对成员与本机同步状态</summary><p v-for="user in account.users.length?account.users:[account.user]" :key="user.id">{{ user.display_name }}最近连接同步：{{ syncDate(user.last_synced_at) }}（上海时间）</p><p>本机待同步：{{ account.queue.length }} 项账务操作、{{ account.images.filter(image=>image.pending).length }} 项附件、{{ account.categoryQueue.length }} 项分类。</p><p>该时间不代表所有设备都已上传。复盘前请当前家庭成员确认各自设备没有待同步内容。</p></details>
</section>
  <p v-if="settings?.automation_paused" class="notice">服务端恢复后自动化已暂停，需管理员核对后恢复；修改时间不会解除暂停。</p>
  <p v-if="settingsError" class="notice" role="status">{{ settingsError }}</p>
 </section>
</template>
