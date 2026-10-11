const {chromium} = require('../frontend/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const output = path.resolve(__dirname, '../.reports/modern-ai');
  fs.mkdirSync(output, {recursive: true});
  const browser = await chromium.launch({channel: 'msedge'});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const external = [];
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.protocol.startsWith('http') && url.hostname !== '127.0.0.1') {
        external.push(url.href);
        await route.abort();
      } else await route.continue();
    });
    for (const [name, url] of [['home', '/'], ['knowledge', '/knowledge'], ['processing', '/processing'], ['chat', '/chat'], ['settings', '/settings']]) {
      await page.goto('http://127.0.0.1:18765' + url);
      await page.getByText('本地服务运行中', {exact: true}).waitFor();
      await page.waitForLoadState('networkidle');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
    }
    await page.goto('http://127.0.0.1:18765');
    await page.getByRole('heading', {name: '让知识，随问随得。'}).waitFor();
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    console.log('Production interface checked: five pages, no horizontal overflow, no page errors, only local assets.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
