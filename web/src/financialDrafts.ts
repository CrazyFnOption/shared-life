/** Only editable inputs and unreconciled submissions belong to a navigation draft.
 * Read-only details, IDs, source snapshots and UI expansion flags do not. */
export function financialDraftFingerprint(draft:object|undefined):string {
  if(!draft)return ''
  const data=draft as Record<string,unknown>
  const fields=['amount','purpose','time','location','category','rating','rating_reason','kind','reason','refund_id','refund_amount','refund_time','refund_reason','submitted']
  return JSON.stringify({
    fields:fields.map(key=>data[key]??null),
    photos:Array.isArray(data.added)?data.added.map(image=>image.id):[],
    removed:Array.isArray(data.removed)?data.removed:[],
  })
}
