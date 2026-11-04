import { expect, test } from 'vitest'
import { parseMoney } from '../src/money'
test('人民币十进制输入准确转换为分', () => {
  expect(parseMoney('0.29')).toBe(29)
  expect(parseMoney('9999999.99')).toBe(999999999)
  expect(parseMoney('12.3')).toBe(1230)
  for (const invalid of ['0', '-1', '1.001', '1e2', '10000000', '', 'NaN']) expect(() => parseMoney(invalid)).toThrow()
})
