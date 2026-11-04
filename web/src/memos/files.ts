import type { Source } from '../types'
import type { MemoAttachmentView } from './types'
import type { MemoBlob, MemoFileManifest } from './domain'
export async function blobHash(blob:Blob):Promise<string>{return[...new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()))].map(byte=>byte.toString(16).padStart(2,'0')).join('')}
export async function prepareMemoFiles(files:MemoAttachmentView[],source:Source,memoId:string):Promise<{manifest:MemoFileManifest[];blobs:MemoBlob[]}>{
 const manifest:MemoFileManifest[]=[],blobs:MemoBlob[]=[]
 for(const file of files){
  const kind=file.kind??'file',mime=file.mime_type||'application/octet-stream'
  if(file.size_bytes>20*1024*1024)throw new Error(`附件“${file.file_name}”超过20MB，请选择较小文件。`)
  if(kind==='photo'&&(!['image/jpeg','image/png','image/webp'].includes(mime)||file.size_bytes>2*1024*1024))throw new Error('照片支持 JPEG、PNG、WebP，单张不超过2MB。其他格式可通过“附件”保存。')
  if(file.blob&&file.blob.size!==file.size_bytes)throw new Error('文件字节发生变化，请重新选择。')
  const sha256=file.sha256??(file.blob?await blobHash(file.blob):'')
  if(!sha256)throw new Error('附件尚未加载，不能丢失原附件声明；请联网核对后重试。')
  const item:MemoFileManifest={id:file.id,kind,name:file.file_name,mime_type:mime,size_bytes:file.size_bytes,sha256}
  manifest.push(item)
  if(file.blob&&!file.sha256){const blob=file.blob.type===mime?file.blob:new Blob([await file.blob.arrayBuffer()],{type:mime});blobs.push({...item,memo_id:memoId,source:{...source},blob,pending:true})}
 }
 return{manifest,blobs}
}
export async function verifyMemoDownload(blob:Blob,manifest:MemoFileManifest):Promise<Blob>{if(blob.size!==manifest.size_bytes||await blobHash(blob)!==manifest.sha256)throw new Error('附件字节校验失败，未写入本机缓存。');return new Blob([await blob.arrayBuffer()],{type:manifest.mime_type})}
