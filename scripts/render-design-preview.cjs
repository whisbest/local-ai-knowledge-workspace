const { chromium } = require('../frontend/node_modules/playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const output = path.join(root, '.reports', 'design-modern-ai');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge' });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
    const remote = [];
    page.on('request', request => {
      if (/^https?:/.test(request.url())) remote.push(request.url());
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(path.join(root, 'docs', 'design-modern-ai.html')).href);
    await page.getByRole('heading', { name: '让知识，随问随得。' }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.join(output, 'home.png'), fullPage: true });
    await page.getByRole('button', { name: '开始提问' }).click();
    await page.locator('#chat.active').waitFor();
    await page.getByRole('heading', { name: '参考来源 2' }).waitFor();
    await page.screenshot({ path: path.join(output, 'chat.png'), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const areas = await page.locator('.chat-grid > section').evaluateAll(elements => elements.map(element => {
      const bounds = element.getBoundingClientRect();
      return { left: bounds.left, right: bounds.right, width: bounds.width };
    }));
    assert.equal(areas.length, 3);
    for (const area of areas) assert.ok(area.left >= 200 && area.right <= 1280 && area.width > 150);
    await page.screenshot({ path: path.join(output, 'chat-1280.png'), fullPage: true });
    await page.getByRole('link', { name: '首页', exact: true }).click();
    await page.locator('#home.active').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(remote, []);
    assert.deepEqual(errors, []);
    console.log('Design preview verified: home/chat navigation, 1440/1280 layouts, local assets, no page errors.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
