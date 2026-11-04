/** Compress before the single IndexedDB commit; failures leave the draft intact. */
export async function prepareImage(file:File): Promise<Blob> {
  if (file.size > 20 * 1024 * 1024) throw new Error('原图超过20MB，请先缩小图片')
  if (file.type === 'image/svg+xml') throw new Error('请选择照片或截图，不支持SVG')
  const url=URL.createObjectURL(file)
  const image=new Image()
  try {
    image.src=url
    await image.decode().catch(()=>{throw new Error('这张图片无法读取，请转换成JPEG或PNG后重试')})
    if (image.naturalWidth * image.naturalHeight > 40_000_000) throw new Error('图片尺寸过大，请先缩小图片')
    const scale=Math.min(1,1600/Math.max(image.naturalWidth,image.naturalHeight))
    const canvas=document.createElement('canvas')
    canvas.width=Math.max(1,Math.round(image.naturalWidth*scale));canvas.height=Math.max(1,Math.round(image.naturalHeight*scale))
    const ctx=canvas.getContext('2d')
    if (!ctx) throw new Error('浏览器无法处理图片，请重试')
    ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0,canvas.width,canvas.height)
    const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('图片压缩失败，请重试')),'image/jpeg',.85))
    if (blob.size>2*1024*1024) throw new Error('压缩后图片仍超过2MB，请选较小图片')
    return blob
  } finally { URL.revokeObjectURL(url) }
}
