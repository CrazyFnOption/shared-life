import type { LocalImage } from './types'

// IDB can return file-backed Blobs whose object URLs WebKit invalidates when the
// account is written again. Display an independent byte copy and keep its URL
// until the immutable attachment leaves this account/restore generation.
export class ImageUrls {
  private owner?: string
  private restore?: string
  private urls: Record<string,string> = {}
  private generation=0

  current():Record<string,string>{return {...this.urls}}

  async update(owner:string|undefined,restore:string|undefined,images:LocalImage[]):Promise<Record<string,string>|undefined> {
    if(owner!==this.owner || restore!==this.restore)this.clear()
    this.owner=owner;this.restore=restore
    const generation=++this.generation
    const visible=owner?images.filter(image=>image.blob && (image.pending!=='delete' || image.restore_review)):[]
    const ids=new Set(visible.map(image=>image.id))
    for(const [id,url] of Object.entries(this.urls))if(!ids.has(id)){URL.revokeObjectURL(url);delete this.urls[id]}
    for(const image of visible)if(!this.urls[image.id]) {
      let bytes:ArrayBuffer
      try{bytes=await image.blob!.arrayBuffer()}
      catch(error){if(generation!==this.generation)return;throw error}
      if(generation!==this.generation)return
      this.urls[image.id]=URL.createObjectURL(new Blob([bytes],{type:image.blob!.type}))
    }
    return this.current()
  }

  clear():void {
    this.generation++
    Object.values(this.urls).forEach(URL.revokeObjectURL)
    this.urls={}
    this.owner=undefined;this.restore=undefined
  }
}
