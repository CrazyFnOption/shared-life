import {expect,test} from 'vitest'
import {ChangeSet} from '@codemirror/state'
import {bindTasks,bodyWithItems,mapTaskEdits,taskBody,taskLines} from '../src/memos/markdownChecklist'
test('GFM task markers exclude fenced code and ordinary lists; existing items enter once',()=>{
 const body='说明\n- 钥匙\n\n```md\n- [ ] 示例\n```\n- 普通文字',items=[{id:'key',text:'钥匙'},{id:'ticket',text:'车票'}]
 const projected=bodyWithItems(body,items)
 expect(projected.match(/钥匙/g)).toHaveLength(1);expect(projected).toContain('- [ ] 钥匙');expect(projected).toContain('- 普通文字');expect(bodyWithItems(projected,items)).toBe(projected)
 expect(taskLines(projected).map(item=>item.text)).toEqual(['钥匙','车票'])
})
test('CodeMirror changes preserve renamed IDs and duplicate insertion does not steal old checked identity',()=>{
 const body='- [x] 钥匙\n- [ ] 车票',tasks=bindTasks(body,[{id:'key',text:'钥匙'},{id:'ticket',text:'车票'}])
 const inserted='- [ ] 钥匙\n',change=ChangeSet.of({from:0,insert:inserted},body.length),next=mapTaskEdits(tasks,inserted+body,change)
 expect(next[1]!.id).toBe('key');expect(next[0]!.id).not.toBe('key')
 const renamed=ChangeSet.of({from:6,to:8,insert:'备用钥匙'},body.length),updated=mapTaskEdits(tasks,body.slice(0,6)+'备用钥匙'+body.slice(8),renamed)
 expect(updated[0]!.id).toBe('key');expect(updated[0]!.text).toBe('备用钥匙')
 const removed=ChangeSet.of({from:0,to:9},body.length),remaining=mapTaskEdits(tasks,body.slice(9),removed,tasks),undo=ChangeSet.of({from:0,insert:body.slice(0,9)},body.length-9)
 expect(mapTaskEdits(remaining,body,undo,tasks)[0]!.id).toBe('key')
})
test('body checks use the selected occurrence and frozen snapshots preserve old text/order',()=>{
 const items=[{id:'one',text:'钥匙'},{id:'two',text:'车票'}],body='介绍\n- [x] 钥匙\n- [x] 车票'
 expect(taskBody(body,items,items,new Set()).body).toBe('介绍\n- [ ] 钥匙\n- [ ] 车票')
 expect(taskBody(body,items,[{id:'two',text:'旧车票'},{id:'one',text:'旧钥匙'}],new Set(['two']),true).body).toBe('介绍\n- [x] 旧车票\n- [ ] 旧钥匙')
})
