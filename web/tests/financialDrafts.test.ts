import { describe, expect, it } from 'vitest'
import { financialDraftFingerprint as fingerprint } from '../src/financialDrafts'

describe('unsaved financial input boundary',()=>{
  it('does not treat an untouched new or prefilled edit form as changed',()=>{
    for(const draft of [{amount:'',purpose:'',time:'2026-11-04T11:37',rating:'3'},{amount:'123.45',purpose:'existing',time:'2026-11-04T11:37',rating:'4',rating_reason:'useful'}]){
      const initial=fingerprint(draft)
      expect(fingerprint({...draft})).toBe(initial)
      expect(fingerprint({...draft,amount:'987.65'})).not.toBe(initial)
    }
  })
  it('tracks minute, satisfaction reason and refund or request edits',()=>{
    const draft={amount:'123.45',purpose:'existing',time:'2026-11-04T11:37',rating:'3',rating_reason:'',reason:'',refund_amount:'',refund_time:'2026-11-04T11:37',refund_reason:''}
    for(const change of [{time:'2026-11-04T11:38'},{rating_reason:'reason'},{refund_amount:'1.00'},{refund_time:'2026-11-04T11:38'},{refund_reason:'return'},{reason:'request change'}])expect(fingerprint({...draft,...change})).not.toBe(fingerprint(draft))
    expect(fingerprint({...draft,amount:'999',purpose:'edit',...draft})).toBe(fingerprint(draft))
  })
  it('tracks added and removed photos without serializing Blob content or preview URL',()=>{
    const draft={amount:'',added:[] as object[],removed:[] as string[]}
    expect(fingerprint({...draft,added:[{id:'photo-a',blob:new Blob(['fictional']),url:'blob:first'}]})).not.toBe(fingerprint(draft))
    expect(fingerprint({...draft,removed:['photo-old']})).not.toBe(fingerprint(draft))
    expect(fingerprint({added:[{id:'same',url:'blob:first'}]})).toBe(fingerprint({added:[{id:'same',url:'blob:second'}]}))
  })
  it('retains an unconfirmed submitted request identity as unresolved input',()=>{
    const draft={purpose:'original',reason:''}
    expect(fingerprint({...draft,submitted:{id:'same-request',reason:'changed'}})).not.toBe(fingerprint(draft))
  })
  it('does not classify read-only source data and UI-only flags as unsaved input',()=>{
    const draft={amount:'12.00',rating:'3'}
    expect(fingerprint({...draft,id:'expense',version:2,original:{purpose:'old'},ratingTouched:true,existingReason:true})).toBe(fingerprint(draft))
  })
})
