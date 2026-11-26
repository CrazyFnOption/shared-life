// Run against the built candidate and fictional ledger from run_browser.py.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { webkit } = require('playwright');

async function boxes(controls, context) {
  return Promise.all(controls.map(async ([name, locator]) => {
    await locator.waitFor({ state: 'visible' });
    const box = await locator.boundingBox();
    assert.ok(box, `${context}: ${name} is not visible`);
    return { name, ...box };
  }));
}

function assertAligned(fields, context, sameRow = true) {
  const detail = fields.map(({ name, y, height }) => `${name} y=${y}, height=${height}px`).join('; ');
  const heights = fields.map(field => field.height);
  assert.ok(Math.min(...heights) >= 44 && Math.max(...heights) - Math.min(...heights) <= 1,
    `${context}: controls must be equally tall and at least 44px (${detail})`);
  if (sameRow) {
    const tops = fields.map(field => field.y);
    assert.ok(Math.max(...tops) - Math.min(...tops) <= 1, `${context}: controls must align (${detail})`);
  }
}

test('WebKit aligns memo reminder fields at desktop and narrow widths', { timeout: 30000 }, async () => {
  const origin = new URL(process.env.E2E_ORIGIN);
  assert.equal(origin.hostname, '127.0.0.1', 'Run with run_browser.py on a fictional local service');
  const browser = await webkit.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, serviceWorkers: 'block' });
    page.setDefaultTimeout(5000);
    await page.goto(origin.href);
    await page.getByRole('textbox', { name: '账号名', exact: true }).fill('demo-a');
    await page.getByRole('textbox', { name: '密码', exact: true }).fill('demo-only-2026');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('button', { name: '＋ 新建备忘', exact: true }).click();
    await page.getByText('添加提醒', { exact: true }).click();
    const reminder = page.getByRole('region', { name: '提醒设置' });
    const dateTime = [
      ['date', reminder.getByLabel(/^日期/)],
      ['hour', reminder.getByRole('combobox', { name: '提醒时间小时', exact: true })],
      ['minute', reminder.getByRole('combobox', { name: '提醒时间分钟', exact: true })],
    ];

    assertAligned(await boxes(dateTime, 'desktop date/time'), 'desktop date/time');
    await reminder.getByRole('button', { name: '按时长提醒', exact: true }).click();
    const duration = [
      ['duration', reminder.getByRole('spinbutton', { name: '保存后', exact: true })],
      ['unit', reminder.getByRole('combobox', { name: '单位', exact: true })],
    ];
    assertAligned(await boxes(duration, 'desktop duration'), 'desktop duration');
    await reminder.getByRole('button', { name: '选日期和时间', exact: true }).click();

    await page.setViewportSize({ width: 320, height: 844 });
    const mobile = await boxes(dateTime, 'narrow date/time');
    assertAligned(mobile, 'narrow date/time', false);
    assertAligned(mobile.slice(1), 'narrow hour/minute');
    assert.ok(mobile[0].y + mobile[0].height <= mobile[1].y, 'narrow date should sit above time');
    assert.ok(mobile[1].x + mobile[1].width <= mobile[2].x, 'narrow hour and minute must not overlap');

    await reminder.getByRole('combobox', { name: '重复', exact: true }).selectOption('weekly');
    const weekly = [
      ['hour', reminder.getByRole('combobox', { name: '提醒时间1小时', exact: true })],
      ['minute', reminder.getByRole('combobox', { name: '提醒时间1分钟', exact: true })],
    ];
    assertAligned(await boxes(weekly, 'narrow weekly time'), 'narrow weekly time');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      'narrow reminder must not overflow horizontally');
  } finally {
    await browser.close();
  }
});
