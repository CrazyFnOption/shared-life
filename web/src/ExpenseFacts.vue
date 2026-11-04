<script setup lang="ts">
import { reviewText } from './ratings'
import type { Expense } from './types'
import { money } from './money'
import { netAmount } from './operations'
defineProps<{expense:Expense}>()
function time(iso:string){return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(iso))}
</script>
<template>
  <div class="expense-facts">
    <p>{{ expense.purpose }}</p>
    <p>原款 ¥{{ money(expense.amount_cents) }} · 净额 ¥{{ money(netAmount(expense)) }}</p>
    <p>{{ expense.scope==='shared'?'共同账':'私人账' }} · {{ expense.voided?'已作废':'有效' }}</p>
    <p>{{ time(expense.occurred_at) }}（上海时间）</p>
    <p>{{ expense.category||'未分类' }} · {{ expense.location||'未填写地点' }}</p>
    <p>{{ reviewText(expense.rating, expense.rating_reason) }}</p>
    <p v-for="refund in expense.refunds" :key="refund.id">退款 ¥{{ money(refund.amount_cents) }} · {{ time(refund.occurred_at) }} · {{ refund.reason||'未填写原因' }}{{ refund.voided?' · 已作废':'' }}</p>
  </div>
</template>
