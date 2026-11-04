import { expect, test } from 'vitest'
import { ratingDraft, ratingReasonVisible, ratingText, reviewText } from '../src/ratings'
test('历史空评价显示无感，五档使用文字且空原因没有分隔符',()=>{
 expect([1,2,3,4,5].map(ratingText)).toEqual(['非常不满意','比较不满意','无感','比较满意','非常满意'])
 expect(ratingText(null)).toBe('无感');expect(reviewText(undefined,'')).toBe('无感')
 expect(reviewText(4,'  好用  ')).toBe('比较满意 · 好用')
})

test('新开默认为3且未触碰不展开，历史原因初始化直接展开',()=>{
 const fresh=ratingDraft();expect(fresh).toEqual({rating:'3',rating_reason:'',ratingTouched:false,existingReason:false});expect(ratingReasonVisible(fresh)).toBe(false)
 const old=ratingDraft(5,'旧原因应保留');expect(ratingReasonVisible(old)).toBe(true);expect(old.rating_reason).toBe('旧原因应保留')
})
test.each(['1','2','4','5'])('主动选%s展开且空原因保持合法；切3仅收起不清空',rating=>{
 const draft=ratingDraft();draft.rating=rating;draft.ratingTouched=true;expect(ratingReasonVisible(draft)).toBe(true);expect(draft.rating_reason).toBe('')
 draft.rating_reason='主动写下的原因';draft.rating='3';expect(ratingReasonVisible(draft)).toBe(false);expect(draft.rating_reason).toBe('主动写下的原因')
})
test('取消后重开重新初始化touched，历史原因值不因收起改变',()=>{
 const old=ratingDraft(4,'历史原因');old.ratingTouched=true;expect(ratingReasonVisible(old)).toBe(true)
 const reopened=ratingDraft(4,'历史原因');expect(reopened.ratingTouched).toBe(false);expect(ratingReasonVisible(reopened)).toBe(true);expect(reopened.rating_reason).toBe(old.rating_reason)
})

test('编辑旧原因清空文字仍保留输入框，历史无感也直接回显',()=>{
 const draft=ratingDraft(3,'旧原因');expect(ratingReasonVisible(draft)).toBe(true)
 draft.rating_reason='';expect(ratingReasonVisible(draft)).toBe(true)
 expect(ratingReasonVisible(ratingDraft(5,''))).toBe(false)
})

test('满意度五档筛选对所有记录者生效，旧null与undefined按无感且不修改原值',async()=>{
 const {matchesRating}=await import('../src/ratings')
 for(const filter of ['1','2','3','4','5'] as const){
  expect([1,2,3,4,5].filter(r=>matchesRating(r,filter))).toEqual([Number(filter)])
 }
 expect(matchesRating(null,'3')).toBe(true);expect(matchesRating(undefined,'3')).toBe(true)
 expect(matchesRating(null,'4')).toBe(false)
 expect([null,undefined,1,2,3,4,5].every(r=>matchesRating(r,''))).toBe(true)
})
