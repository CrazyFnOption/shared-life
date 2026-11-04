export function parseMoney(input: string): number {
  const text = input.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('请输入大于零、最多两位小数的金额')
  const [yuan = '', fraction = ''] = text.split('.')
  const cents = Number(yuan) * 100 + Number(fraction.padEnd(2, '0'))
  if (!Number.isSafeInteger(cents) || cents < 1 || cents > 999999999) throw new Error('金额应在 0.01～9,999,999.99 元之间')
  return cents
}
export function money(cents: number): string {
  return `${Math.floor(cents / 100).toLocaleString('zh-CN')}.${String(cents % 100).padStart(2, '0')}`
}
export function moneyInput(cents: number): string { return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}` }
