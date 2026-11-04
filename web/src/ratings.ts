export const ratingLabels = ['非常不满意', '比较不满意', '无感', '比较满意', '非常满意'] as const
export function ratingText(rating: number | null | undefined): string {
  return ratingLabels[(rating ?? 3) - 1] ?? '无感'
}
export function reviewText(rating: number | null | undefined, reason?: string): string {
  return ratingText(rating) + (reason?.trim() ? ` · ${reason.trim()}` : '')
}

export interface RatingDraft { rating:string; rating_reason:string; ratingTouched:boolean; existingReason:boolean }
export function ratingDraft(rating?:number|null,reason=''):RatingDraft {
  return {rating:String(rating??3),rating_reason:reason,ratingTouched:false,existingReason:!!reason.trim()}
}
export function ratingReasonVisible(draft:RatingDraft):boolean {
  return draft.existingReason || draft.ratingTouched && draft.rating!=='3'
}

export type RatingFilter = ''|'1'|'2'|'3'|'4'|'5'
export function matchesRating(rating:number|null|undefined,filter:RatingFilter):boolean {
  return !filter || (rating??3)===Number(filter)
}
