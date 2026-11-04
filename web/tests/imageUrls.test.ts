import { expect, test, vi } from 'vitest'
import { ImageUrls } from '../src/imageUrls'
import type { LocalImage } from '../src/types'

function image(id='photo',bytes='fictional photo'):LocalImage {
  const blob=new Blob([bytes],{type:'image/jpeg'})
  return {id,expense_id:'expense',mime_type:blob.type,size_bytes:blob.size,blob,pending:'upload'}
}
async function contents(url:string){return (await fetch(url)).text()}

test('displayed image remains readable across cloned IDB reads and upload confirmation',async()=>{
  const cache=new ImageUrls(),original=image()
  try {
    const url=(await cache.update('a','restore-1',[original]))!.photo!
    for(let n=0;n<8;n++) {
      const next=structuredClone(original)
      delete next.pending
      expect((await cache.update('a','restore-1',[next]))!.photo).toBe(url)
      expect(await contents(url)).toBe('fictional photo')
    }
  } finally {cache.clear()}
})

test('removed and locally deleted images release URLs while restore-review images remain readable',async()=>{
  const cache=new ImageUrls(),first=image(),second=image('second')
  try {
    const before=(await cache.update('a','restore-1',[first,second]))!
    const after=(await cache.update('a','restore-1',[{...second,pending:'delete',restore_review:true}]))!
    expect(after.photo).toBeUndefined()
    await expect(contents(before.photo!)).rejects.toThrow()
    expect(await contents(after.second!)).toBe('fictional photo')
    await cache.update('a','restore-1',[{...second,pending:'delete'}])
    await expect(contents(after.second!)).rejects.toThrow()
  } finally {cache.clear()}
})

test('account changes, restore generations, locking and disposal revoke the previous displayed bytes',async()=>{
  const cache=new ImageUrls()
  let previous=(await cache.update('a','restore-1',[image()]))!.photo!
  for(const [owner,restore,bytes] of [['b','restore-1','other account'],['b','restore-2','restored bytes']] as const) {
    const next=(await cache.update(owner,restore,[image('photo',bytes)]))!.photo!
    expect(next).not.toBe(previous)
    await expect(contents(previous)).rejects.toThrow()
    expect(await contents(next)).toBe(bytes)
    previous=next
  }
  await cache.update(undefined,undefined,[image()])
  await expect(contents(previous)).rejects.toThrow()
  const reopened=(await cache.update('b','restore-2',[image()]))!.photo!
  cache.clear()
  await expect(contents(reopened)).rejects.toThrow()
})


test.each(['lock','withdraw','account','restore','dispose'])('late byte copies cannot publish after %s',async(change)=>{
  const cache=new ImageUrls(),photo=image()
  let release!:(bytes:ArrayBuffer)=>void
  const bytes=await photo.blob!.arrayBuffer()
  const held=new Promise<ArrayBuffer>(resolve=>{release=resolve})
  vi.spyOn(photo.blob!,'arrayBuffer').mockReturnValue(held)
  const pending=cache.update('a','restore-1',[photo])
  if(change==='dispose')cache.clear()
  else if(change==='withdraw')await cache.update('a','restore-1',[])
  else if(change==='account')await cache.update('b','restore-1',[])
  else if(change==='restore')await cache.update('a','restore-2',[])
  else await cache.update(undefined,undefined,[])
  release(bytes)
  expect(await pending).toBeUndefined()
  expect(cache.current()).toEqual({})
  cache.clear()
})

test('preview bytes no longer depend on the IDB Blob after its first read',async()=>{
  const cache=new ImageUrls(),photo=image()
  try {
    const read=vi.spyOn(photo.blob!,'arrayBuffer')
    const url=(await cache.update('a','restore-1',[photo]))!.photo!
    read.mockRejectedValue(new Error('IDB file backing became unavailable'))
    expect((await cache.update('a','restore-1',[photo]))!.photo).toBe(url)
    expect(await contents(url)).toBe('fictional photo')
    expect(read).toHaveBeenCalledTimes(1)
  } finally {cache.clear()}
})
