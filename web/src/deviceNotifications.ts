// Diagnostics contain public browser/provider names and a short opaque fingerprint only.
export function browserVersion(ua:string):string {
 const edge=/\bEdg(?:A|iOS)?\/([\d.]+)/.exec(ua)
 if(edge)return `Edge ${edge[1]}`
 const chrome=/\b(?:Chrome|CriOS)\/([\d.]+)/.exec(ua)
 if(chrome)return `Chrome ${chrome[1]}`
 const firefox=/\b(?:Firefox|FxiOS)\/([\d.]+)/.exec(ua)
 if(firefox)return `Firefox ${firefox[1]}`
 const safari=/\bVersion\/([\d.]+).*Safari\//.exec(ua)
 return safari?`Safari ${safari[1]}`:'浏览器版本未识别'
}
export function pushProvider(endpoint:string):string {
 try{const host=new URL(endpoint).hostname;for(const [domain,name] of [['fcm.googleapis.com','FCM'],['push.services.mozilla.com','Mozilla'],['notify.windows.com','Windows'],['web.push.apple.com','Apple']])if(host===domain||host.endsWith('.'+domain))return name!}catch{}
 return 'Other'
}
export async function endpointFingerprint(endpoint:string):Promise<string> {
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(endpoint))
 return Array.from(new Uint8Array(hash),value=>value.toString(16).padStart(2,'0')).join('').slice(0,12)
}
export function workerVersion(worker:ServiceWorker):Promise<string> {
 return new Promise(resolve=>{const channel=new MessageChannel();const timer=setTimeout(()=>{channel.port1.close();resolve('版本待核对')},1200);channel.port1.onmessage=event=>{clearTimeout(timer);channel.port1.close();resolve(typeof event.data?.version==='string'&&/^\d+\.\d+\.\d+$/.test(event.data.version)?`v${event.data.version}`:'版本待核对')};try{worker.postMessage({type:'app-version'},[channel.port2])}catch{clearTimeout(timer);channel.port1.close();resolve('版本待核对')}})
}
