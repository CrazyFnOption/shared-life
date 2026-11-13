import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { Account, Source } from '../src/types'

const source:Source={protocol_version:2,user_id:'fictional-user',restore_id:'fictional-restore',scope:'shared',family_id:'temporary-family',relation_epoch:3}
const account={user:{id:source.user_id},restore_id:source.restore_id,context:{families:[{id:source.family_id,relation_epoch:3}],default_scope:{scope:'private',family_id:null}}} as Account
beforeEach(()=>{
 vi.resetModules()
 const values=new Map<string,string>()
 vi.stubGlobal('sessionStorage',{setItem:(k:string,v:string)=>values.set(k,v),getItem:(k:string)=>values.get(k)??null,removeItem:(k:string)=>values.delete(k)})
})
afterEach(()=>vi.unstubAllGlobals())

test('update restores temporary scope once, and rejects changed identity, restore domain or membership',async()=>{
 const {rememberUpdateSource,takeUpdateSource}=await import('../src/appUpdates')
 expect(rememberUpdateSource(source)).toBe(true)
 expect(takeUpdateSource(account)).toEqual(source)
 expect(takeUpdateSource(account)).toBeUndefined()
 for(const invalid of [{...source,user_id:'another-user'},{...source,restore_id:'new-restore'},{...source,relation_epoch:4}]){
  rememberUpdateSource(invalid)
  expect(takeUpdateSource(account)).toBeUndefined()
 }
})

test('a pending update rechecks live safety before one reload and leaves offline input alone',async()=>{
 const updates=await import('../src/appUpdates')
 const reload=vi.fn(),prepare=vi.fn(()=>true)
 vi.stubGlobal('window',{location:{reload}})
 const network={onLine:false};vi.stubGlobal('navigator',network)
 vi.stubGlobal('document',{visibilityState:'visible'})
 let safe=false;updates.configureAppUpdates(()=>safe,prepare);updates.appUpdateReady.value=true
 updates.applyAppUpdate();network.onLine=true;updates.applyAppUpdate()
 expect(prepare).not.toHaveBeenCalled();expect(reload).not.toHaveBeenCalled()
 safe=true;updates.applyAppUpdate();updates.applyAppUpdate()
 expect(prepare).toHaveBeenCalledTimes(1);expect(reload).toHaveBeenCalledTimes(1)
})

test('storage denial blocks context preparation instead of silently losing the selected scope',async()=>{
 const {rememberUpdateSource,appUpdateError}=await import('../src/appUpdates')
 vi.stubGlobal('sessionStorage',{setItem:()=>{throw new Error('denied')}})
 expect(rememberUpdateSource(source)).toBe(false)
 expect(appUpdateError.value).toContain('保留当前范围')
})
