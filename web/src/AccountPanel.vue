<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from './api'
import Icon from './Icon.vue'
import type { Account } from './types'

const props=defineProps<{account:Account;embedded?:boolean}>()
const emit=defineEmits<{close:[];unauthorized:[];restoreChanged:[]}>()
const oldPassword=ref(''),newPassword=ref(''),confirmation=ref(''),message=ref('')
const busy=ref(false),online=ref(navigator.onLine)
let epoch=0
function clearPasswords(){oldPassword.value='';newPassword.value='';confirmation.value=''}
function reset(){epoch++;clearPasswords();message.value='';busy.value=false}
function close(){reset();emit('close')}
function connectionChanged(){online.value=navigator.onLine}
async function submit(){
 if(busy.value)return
 message.value=''
 if(!online.value){message.value='修改密码需要联网，请连接网络后重试';return}
 const length=Array.from(newPassword.value).length
 if(length<8||length>128){message.value='新密码需要8～128个字符';return}
 if(newPassword.value!==confirmation.value){message.value='两次新密码不一致';return}
 const current=epoch,id=props.account.user.id
 busy.value=true
 try{
  await api.changePassword(id,oldPassword.value,newPassword.value)
  if(current!==epoch)return
  clearPasswords();message.value='密码已修改。本设备保持登录，其他设备需用新密码重新登录；本机待同步内容保留。'
 }catch(error){
  if(current!==epoch)return
  if(error instanceof ApiError){
   if(error.status===401||error.code==='account_changed'){reset();emit('unauthorized');return}
   if(error.code==='restore_changed'){reset();emit('restoreChanged');message.value='服务端已恢复，请同步后重新输入密码；本机待同步内容保留';return}
   if(error.code==='incorrect_password'){message.value='旧密码不正确，请重新输入';return}
   if(error.code==='invalid_password_length'){message.value='新密码需要8～128个字符';return}
   message.value='修改未被接受，请核对后重试'
  }else message.value='网络未确认改密结果，可重试；若提示旧密码不正确，请用新密码重新登录核对。本机待同步内容保留。'
 }finally{if(current===epoch)busy.value=false}
}
watch(()=>`${props.account.user.id}:${props.account.restore_id??''}`,reset,{flush:'sync'})
onMounted(()=>{window.addEventListener('online',connectionChanged);window.addEventListener('offline',connectionChanged)})
onUnmounted(()=>{reset();window.removeEventListener('online',connectionChanged);window.removeEventListener('offline',connectionChanged)})
</script>
<template>
 <div :class="embedded?'config-page':'modal-backdrop'" @click.self="!embedded&&close()" @keydown.esc="close"><section :class="embedded?'config-panel':'form-card'" :role="embedded?'region':'dialog'" :aria-modal="embedded?undefined:true" aria-label="账号与密码">
  <div class="form-title" :class="{'config-page-head':embedded}"><button v-if="embedded" class="config-back" @click="close"><Icon name="chevron"/>返回</button><div><p v-if="embedded" class="config-kicker">配置</p><h2 tabindex="-1">账号与密码</h2></div><button v-if="!embedded" class="icon-button" @click="close" aria-label="关闭账号与密码"><Icon name="close"/></button></div>
  <p class="form-note">当前身份：{{ account.user.display_name }} · {{ account.username }}</p>
  <p class="form-note">修改后本设备保持登录，其他设备需重新登录。账本和本机待同步内容会保留。</p>
  <p v-if="!online" class="notice" role="status">当前离线，无法修改密码。联网后可提交。</p>
  <form class="stack account-password-form" @submit.prevent="submit">
   <label>旧密码<input v-model="oldPassword" name="old-password" type="password" autocomplete="current-password" :disabled="busy" required></label>
   <label>新密码（8～128个字符）<input v-model="newPassword" name="new-password" type="password" autocomplete="new-password" :disabled="busy" required></label>
   <label>确认新密码<input v-model="confirmation" name="confirm-password" type="password" autocomplete="new-password" :disabled="busy" required></label>
   <p v-if="message" class="notice" role="status">{{ message }}</p>
   <button class="primary" :disabled="busy||!online">{{ busy?'正在修改…':'修改密码' }}</button>
  </form>
 </section></div>
</template>
