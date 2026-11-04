<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from './api'
import Icon from './Icon.vue'
import { sourceFor } from './sources'
import type { Account, AccountContext, FamilyInvite, Family, Source } from './types'

const props=defineProps<{account:Account;received:FamilyInvite[];sent:FamilyInvite[];loading:boolean;initialTab?:'received'|'sent';embedded?:boolean;openSequence?:number}>()
const emit=defineEmits<{close:[];changed:[AccountContext,{user_id:string;restore_id:string}];metadataChanged:[AccountContext,{user_id:string;restore_id:string}];unauthorized:[];restoreChanged:[];refresh:[]}>()
const name=ref(''),target=ref(''),successor=ref(''),busy=ref(false),message=ref('')
const managedFamilyId=ref(props.account.context?.families[0]?.id??'')
const family=computed(()=>props.account.context?.families.find(f=>f.id===managedFamilyId.value))
const defaultChoice=ref(props.account.context?.default_scope.family_id??'private')
function familySource(value:Family=family.value!):Source{if(!value)throw new Error('请先选择要管理的家庭');return sourceFor(props.account,{scope:'shared',family_id:value.id})}
async function saveDefault(){if(busy.value||!props.account.context)return;const origin=source(),version=epoch,input={request_id:crypto.randomUUID(),base_version:props.account.context.default_scope.version,scope:defaultChoice.value==='private'?'private' as const:'shared' as const,family_id:defaultChoice.value==='private'?null:defaultChoice.value};busy.value=true;message.value='';try{const result=await api.defaultScope(origin,input);if(current(origin,version)){emit('metadataChanged',result.context,origin);message.value='默认范围已保存，下次打开时使用；当前范围保持不变'}}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}}
const isBusy=computed(()=>busy.value)
defineExpose({isBusy})
const exitStep=ref<'choose'|'transfer'|'dissolve'>()
const inviteTab=ref<'received'|'sent'>(props.initialTab??(family.value?'sent':'received'))
const pendingReceived=computed(()=>props.received.filter(i=>i.status==='pending'))
const pendingSent=computed(()=>props.sent.filter(i=>i.status==='pending'))
const owner=computed(()=>!!family.value&&family.value.owner_id===props.account.user.id)
const successors=computed(()=>family.value?.members.filter(m=>m.id!==props.account.user.id)??[])
let requestId:string|undefined,inviteId:string|undefined,epoch=0
function source(){return{user_id:props.account.user.id,restore_id:props.account.restore_id??''}}
function current(origin:{user_id:string;restore_id:string},version:number){return version===epoch&&props.account.user.id===origin.user_id&&(props.account.restore_id??'')===origin.restore_id}
function fail(error:unknown){
 if(error instanceof ApiError){
  if(error.status===401||error.code==='account_changed'){emit('unauthorized');return '登录状态已变化，请恢复账号'}
  if(error.code==='restore_changed'){emit('restoreChanged');return '服务端已恢复，请同步后重试；本机待同步内容保留'}
  if(error.code==='owner_transfer_or_dissolve_required')return '请重新点击退出家庭，选择移交并退出或解散并退出'
  if(error.code==='family_changed'||error.status===409){emit('refresh');return '家庭或邀请状态已变化，正在刷新'}
  if(error.status===403){emit('refresh');return '当前没有此操作权限，正在刷新家庭状态'}
  if(error.status===404)return '账号、成员或邀请不存在，请核对'
  if(error.status===422)return '输入未被接受，请核对名称或账号名'
 }
 return error instanceof TypeError?'网络结果未确认，请刷新核对后重试':'操作未完成，请刷新后重试'
}
async function create(){if(busy.value)return;const value=name.value.trim();if(!value){message.value='请填写家庭名称';return}const id=requestId??=crypto.randomUUID(),origin=source(),version=epoch;busy.value=true;message.value='';try{const context=await api.createFamily(origin,value,id);if(!current(origin,version))return;requestId=undefined;emit('changed',context.context,origin)}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}}
async function invite(){if(busy.value)return;const value=target.value.trim();if(!value){message.value='请输入对方的准确账号名';return}const id=inviteId??=crypto.randomUUID(),origin=source(),version=epoch;busy.value=true;message.value='';try{await api.invite(origin.user_id,value,id,familySource());if(!current(origin,version))return;inviteId=undefined;target.value='';message.value='邀请已发送；对方接受后可查看家庭全部共同历史';emit('refresh')}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}}
async function decide(item:FamilyInvite,action:'accept'|'reject'|'revoke'){
 if(busy.value)return
 if(action==='accept'&&!confirm(`加入「${item.family_name}」后，你可以查看该家庭全部共同历史；家庭成员也能查看你加入后记入的共同账。你的私人账始终只有自己可见。确认加入？`))return
 if(action==='revoke'&&!confirm(`撤销发给 ${item.target_username} 的邀请？`))return
 const origin=source(),version=epoch;busy.value=true;message.value=''
 try{const result=action==='revoke'?await api.revokeInvite(origin.user_id,item.id,familySource(props.account.context?.families.find(f=>f.id===item.family_id)??(()=>{throw new Error('邀请的原家庭已不可访问，请刷新')})())):await api.decideInvite(origin,item.id,action);if(!current(origin,version))return;if(action==='accept'){emit('changed',(result as {context:AccountContext}).context,origin);return}message.value=action==='reject'?'已拒绝邀请':'已撤销邀请';emit('refresh')}
 catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}
}
async function remove(member:{id:string;display_name:string;relation_epoch?:number}){const chosen=family.value;if(!chosen||!owner.value||member.id===props.account.user.id||busy.value)return
 if(!confirm(`移出 ${member.display_name}？对方将立即失去这个家庭的全部共同访问；其私人账保留。`))return
 const origin=source(),version=epoch;busy.value=true;message.value=''
 try{const context=await api.removeMember(origin.user_id,chosen!.id,member.id,chosen!.revision!,member.relation_epoch!,familySource(chosen!));if(current(origin,version))emit('metadataChanged',context.context,origin)}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}
}
function openExit(){if(busy.value)return;if(owner.value){successor.value='';message.value='';exitStep.value='choose'}else void leave()}
function cancelExit(){if(busy.value)return;exitStep.value=undefined;successor.value='';message.value=''}
function close(){if(busy.value)return;if(exitStep.value)cancelExit();else {resetContext();emit('close')}}
async function transfer(){const chosen=family.value,member=successors.value.find(m=>m.id===successor.value);if(!chosen||!owner.value||!member||busy.value||exitStep.value!=='transfer')return
 const origin=source(),version=epoch;busy.value=true;message.value=''
 try{const context=await api.transferAndLeave(origin.user_id,chosen!.id,member.id,chosen!.revision!,member.relation_epoch!,familySource(chosen!));if(current(origin,version))emit('changed',context.context,origin)}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}
}
async function leave(){const chosen=family.value;if(!chosen||busy.value||owner.value)return
 if(!confirm(`退出「${chosen.name}」后，你将不能再查看这里的任何共同账，包括自己付款的记录、图片、历史、报告与导出；私人账保留。确认退出？`))return
 const origin=source(),version=epoch;busy.value=true;message.value=''
 try{const context=await api.leaveFamily(origin.user_id,chosen!.id,chosen!.revision!,familySource(chosen!));if(current(origin,version))emit('changed',context.context,origin)}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}
}
async function dissolve(){const chosen=family.value;if(!chosen||!owner.value||busy.value||exitStep.value!=='dissolve')return
 const origin=source(),version=epoch;busy.value=true;message.value=''
 try{const context=await api.dissolveFamily(origin.user_id,chosen!.id,chosen!.revision!,familySource(chosen!));if(current(origin,version))emit('changed',context.context,origin)}catch(error){if(current(origin,version))message.value=fail(error)}finally{if(current(origin,version))busy.value=false}
}
function status(item:FamilyInvite){return {pending:'待处理',accepted:'已接受',rejected:'已拒绝',revoked:'已撤销',expired:'已过期'}[item.status]??item.status}
function resetContext(){epoch++;busy.value=false;name.value='';target.value='';message.value='';requestId=undefined;inviteId=undefined;exitStep.value=undefined;successor.value=''}
watch([()=>props.account.user.id,()=>family.value?.id,()=>family.value?.owner_id,()=>family.value?.relation_epoch,()=>props.account.restore_id],()=>{resetContext();if(!family.value)inviteTab.value='received'},{flush:'sync'})
watch(()=>props.openSequence,()=>{if(!busy.value)inviteTab.value=props.initialTab??(family.value?'sent':'received')},{flush:'post'})
watch(()=>props.account.context?.families.map(f=>f.id).join('|'),()=>{if(!props.account.context?.families.some(f=>f.id===managedFamilyId.value))managedFamilyId.value=props.account.context?.families[0]?.id??''},{flush:'sync'})
watch(()=>[props.account.user.id,props.account.restore_id,props.account.context?.default_scope.version],()=>{defaultChoice.value=props.account.context?.default_scope.family_id??'private'},{flush:'sync'})
onUnmounted(resetContext)
</script>
<template>
 <div :class="embedded?'config-page':'modal-backdrop'" @click.self="!embedded&&close()" @keydown.esc="close"><section :class="embedded?'config-panel family-card':'form-card family-card'" :role="embedded?'region':'dialog'" :aria-modal="embedded?undefined:true" :aria-label="exitStep?'退出家庭':'家庭管理'">
  <div class="form-title" :class="{'config-page-head':embedded}"><button v-if="embedded" class="config-back" @click="close" :disabled="busy"><Icon name="chevron"/>返回</button><div><p v-if="embedded" class="config-kicker">配置 · 家庭管理</p><h2 tabindex="-1">{{ exitStep?'退出家庭':'家庭管理' }}</h2><p class="form-note">{{ family?`${family.name} · ${family.members.length} 位成员`:'当前只有私人账' }}</p></div><button v-if="!embedded" class="icon-button" @click="close" :disabled="busy" :aria-label="exitStep?'取消退出家庭':'关闭家庭管理'"><Icon name="close"/></button></div>
  <p v-if="message" class="notice" role="status">{{ message }}</p>
  <section v-if="!exitStep&&(account.context?.families.length??0)>0" class="family-section"><label>管理家庭<select v-model="managedFamilyId" aria-label="管理家庭" :disabled="busy"><option v-for="item in account.context?.families??[]" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><p class="form-note">这里只选择要管理的家庭，首页当前范围保持不变。</p></section>
  <section v-if="!exitStep" class="family-section"><h3>默认使用范围</h3><p class="form-note">下次打开应用时使用。保存默认不会切换当前范围。</p><form class="family-inline-form" @submit.prevent="saveDefault"><label>默认范围<select v-model="defaultChoice" aria-label="默认使用范围" :disabled="busy"><option value="private">私人</option><option v-for="item in account.context?.families??[]" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><button class="primary" :disabled="busy||!account.context">保存默认范围</button></form><p v-if="account.context?.default_invalidated_family_id" class="form-note">原默认家庭已失效，已回到私人范围。</p></section>
  <section v-if="exitStep && family" class="family-section family-exit-confirm">
   <template v-if="exitStep==='choose'">
    <p class="form-note">你是这个家庭的管理员，请选择退出方式。</p>
    <button v-if="successors.length" class="exit-choice" :disabled="busy" @click="exitStep='transfer'"><strong>移交并退出</strong><span>选择一位当前成员接任，确认后自动退出。</span><Icon name="chevron"/></button>
    <p v-else class="form-note">当前家庭只有你一位成员，没有可接任的成员。</p>
    <button class="exit-choice danger" :disabled="busy" @click="exitStep='dissolve'"><strong>解散并退出</strong><span>不移交管理员，解散整个家庭。</span><Icon name="chevron"/></button>
   </template>
   <template v-else-if="exitStep==='transfer'">
    <h3>移交并退出</h3>
    <p class="form-note">新管理员接任后，你将自动退出，不能再查看这个家庭的任何共同账，包括自己付款的记录、图片、历史、报告与导出。共同记录留在原家庭，私人账保留。</p>
    <form class="stack exit-transfer-form" @submit.prevent="transfer"><label>接任管理员<select v-model="successor" :disabled="busy" required><option value="" disabled>选择成员</option><option v-for="member in successors" :key="member.id" :value="member.id">{{ member.display_name }}（{{ member.username||member.id }}）</option></select></label><button class="primary" :disabled="busy||!successors.some(member=>member.id===successor)">{{ busy?'正在移交并退出…':'确认移交并退出' }}</button></form>
   </template>
   <template v-else>
    <h3>解散并退出</h3>
    <p class="form-note">解散「{{ family.name }}」后，全部成员将失去这个家庭的所有共同访问，包括本人付款的记录、图片、历史、报告与导出。</p>
    <p class="form-note">每个人的私人账保留。共同记录将封存留存，不能在本应用中恢复。此操作无法撤销。</p>
    <button class="subtle danger exit-confirm-button" :disabled="busy" @click="dissolve">{{ busy?'正在解散并退出…':'确认解散并退出' }}</button>
   </template>
   <div class="action-row exit-cancel"><button v-if="exitStep!=='choose'" class="subtle" :disabled="busy" @click="exitStep='choose';message=''">返回选择</button><button class="subtle" :disabled="busy" @click="cancelExit">取消</button></div>
  </section>
  <template v-else>

  <section v-if="family" class="family-section"><div class="family-section-title"><h3>成员</h3><small v-if="owner" class="owner-chip">你是管理员</small></div><ul class="family-members"><li v-for="member in family.members" :key="member.id"><span class="avatar">{{ member.display_name.slice(0,1) }}</span><span class="member-identity"><strong>{{ member.display_name }}</strong><small>{{ member.username||member.id }}</small></span><small v-if="member.id===family.owner_id" class="owner-chip">管理员</small><small v-if="member.id===account.user.id">你</small><button v-if="owner && member.id!==account.user.id" class="subtle danger member-remove" :disabled="busy" @click="remove(member)">移出</button></li></ul></section>
  <section class="family-section invitation-module" aria-label="邀请"><div class="family-section-title"><div><h3>邀请</h3><p class="form-note">已注册账号可按准确账号名邀请。</p></div><button class="text-button" :disabled="loading||busy" @click="emit('refresh')">{{ loading?'刷新中…':'刷新' }}</button></div>
   <form v-if="family" class="family-inline-form" @submit.prevent="invite"><label>邀请已注册成员<input v-model="target" autocomplete="off" placeholder="对方的准确账号名" required @input="inviteId=undefined"></label><button class="primary" :disabled="busy">发送</button></form>
   <p v-if="family" class="form-note invite-explain">接受后对方可查看这个家庭全部共同历史。</p>
   <div class="invite-tabs" role="tablist" aria-label="邀请列表"><button role="tab" :aria-selected="inviteTab==='received'" :class="{active:inviteTab==='received'}" @click="inviteTab='received'">收到 <span v-if="pendingReceived.length">{{ pendingReceived.length }}</span></button><button role="tab" :aria-selected="inviteTab==='sent'" :class="{active:inviteTab==='sent'}" @click="inviteTab='sent'">发出 <span v-if="pendingSent.length">{{ pendingSent.length }}</span></button></div>
   <template v-if="inviteTab==='received'"><p v-if="!received.length" class="form-note invite-empty">暂无收到的邀请</p><article v-for="item in received" :key="item.id" class="family-invite"><div><strong>{{ item.family_name }}</strong><small>{{ item.inviter_name }} 邀请 · {{ status(item) }}<template v-if="item.status==='pending'"> · 有效至 {{ new Date(item.expires_at).toLocaleDateString('zh-CN') }}</template></small><p v-if="item.status==='pending'" class="form-note">接受后可查看该家庭全部共同历史；私人账仍只有你可见。</p></div><div v-if="item.status==='pending'" class="action-row"><button class="subtle" :disabled="busy" @click="decide(item,'reject')">拒绝</button><button class="primary" :disabled="busy" @click="decide(item,'accept')">接受邀请</button></div></article></template>
   <template v-else><p v-if="!sent.length" class="form-note invite-empty">暂无发出的邀请</p><article v-for="item in sent" :key="item.id" class="family-invite"><div><strong>{{ item.target_username }}</strong><small>{{ item.family_name }} · {{ status(item) }}<template v-if="item.status==='pending'"> · 有效至 {{ new Date(item.expires_at).toLocaleDateString('zh-CN') }}</template></small></div><button v-if="item.status==='pending'" class="subtle" :disabled="busy" @click="decide(item,'revoke')">撤销</button></article></template>
  </section>
  <details class="family-section settings-disclosure" :open="!(account.context?.families.length)"><summary>创建家庭</summary><p class="form-note">创建后即可使用共同账，再按账号名邀请其他成员。</p><form class="family-inline-form" @submit.prevent="create"><label>家庭名称<input v-model="name" maxlength="40" placeholder="例如 我们的家" required @input="requestId=undefined"></label><button class="primary" :disabled="busy">创建家庭</button></form></details>
  <details v-if="family" class="family-section family-exit settings-disclosure"><summary>退出家庭</summary><p class="form-note">退出后不能查看这里的任何共同账，包括你付款的记录。私人账保留。</p><button class="subtle danger" :disabled="busy" @click="openExit">退出这个家庭</button></details>
  </template>
 </section></div>
</template>
