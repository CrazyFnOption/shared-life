import {expect,test} from 'vitest'
import {browserVersion,pushProvider,endpointFingerprint} from '../src/deviceNotifications'

test('只提取Edge版本，不把Android型号或完整UA带入摘要',()=>{
 const ua='Mozilla/5.0 (Linux; Android 16; Xiaomi-PRIVATE-MODEL) AppleWebKit/537.36 Chrome/142.0.0.0 Mobile Safari/537.36 EdgA/142.0.3595.53'
 expect(browserVersion(ua)).toBe('Edge 142.0.3595.53');expect(browserVersion('private-unparsed-ua')).toBe('浏览器版本未识别')
})
test('端点信息只提供公共服务名和12位SHA，不暴露token',async()=>{
 const endpoint='https://fcm.googleapis.com/private-token';expect(pushProvider(endpoint)).toBe('FCM')
 expect(pushProvider('https://fcm.googleapis.com.evil.invalid/private-token')).toBe('Other')
 expect(await endpointFingerprint(endpoint)).toMatch(/^[a-f0-9]{12}$/)
 expect(await endpointFingerprint(endpoint)).not.toBe(await endpointFingerprint(endpoint+'-different'))
})
