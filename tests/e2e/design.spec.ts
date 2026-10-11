import {test, expect} from '../../frontend/node_modules/@playwright/test';

test('desktop pages keep controls in view and source panel can close at narrow widths', async ({page}) => {
  for (const width of [1440, 1280]) {
    await page.setViewportSize({width, height: 900});
    for (const route of ['/', '/knowledge', '/processing', '/chat', '/settings']) {
      await page.goto(route);
      await expect(page.getByRole('main')).toBeVisible();
      await expect(page.getByRole('navigation', {name: '主导航'})).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      const controls = await page.locator('main button:visible, main select:visible, main input:visible, main textarea:visible').evaluateAll(elements => elements.map(element => {
        const rect = element.getBoundingClientRect();
        return {left: rect.left, right: rect.right, width: rect.width};
      }));
      for (const rect of controls) {
        expect(rect.left).toBeGreaterThanOrEqual(200);
        expect(rect.right).toBeLessThanOrEqual(width);
        expect(rect.width).toBeGreaterThan(0);
      }
    }
    await page.goto('/chat');
    await expect(page.getByRole('region', {name: '检索来源面板'})).toBeVisible();
    await page.getByRole('button', {name: '关闭来源面板'}).click();
    await expect(page.getByRole('region', {name: '检索来源面板'})).toHaveCount(0);
    await page.getByRole('button', {name: '展开来源'}).click();
    await expect(page.getByRole('region', {name: '检索来源面板'})).toBeVisible();
    expect(await page.locator('.dialogue').evaluate(element => element.getBoundingClientRect().width)).toBeGreaterThanOrEqual(400);
  }
  await page.setViewportSize({width: 1100, height: 720});
  await page.goto('/chat');
  await expect(page.getByRole('region', {name: '检索来源面板'})).toBeVisible();
  expect(await page.locator('.source-panel').evaluate(element => getComputedStyle(element).position)).toBe('absolute');
  await page.getByRole('button', {name: '关闭来源面板'}).click();
  await expect(page.getByRole('button', {name: '发送问题'})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1100);
  await page.goto('/settings');
  await expect(page.getByRole('heading', {name: '默认模型', exact: true})).toBeVisible();
  await page.getByRole('button', {name: '数据与备份', exact: true}).click();
  await expect(page).toHaveURL(/section=data/);
  await expect(page.getByRole('heading', {name: '备份与覆盖恢复', exact: true})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', {name: '本机数据与空间', exact: true})).toBeVisible();
  await page.getByRole('button', {name: '使用偏好', exact: true}).click();
  await expect(page.getByRole('button', {name: '保存使用偏好', exact: true})).toBeVisible();
  await page.goto('/settings?section=models&purpose=embedding');
  await expect(page.getByLabel('用途', {exact: true})).toHaveValue('embedding');
  await expect(page.getByLabel('模型名称', {exact: true})).toHaveValue('qwen3-embedding:0.6b');
  const config = await (await page.request.post('/api/model-configs', {data: {
    purpose: 'chat', mode: 'api', endpoint: 'https://fixture.test/v1', model: 'deletable', key: 'test-key',
  }})).json();
  await page.goto('/settings?section=models');
  await expect(page.getByRole('button', {name: '删除配置'})).toBeVisible();
  const configRow = page.locator('.row').filter({hasText: 'deletable'});
  page.once('dialog', dialog => dialog.accept());
  await configRow.getByRole('button', {name: '删除配置'}).click();
  await page.reload();
  await expect(page.locator('.row').filter({hasText: 'deletable'})).toHaveCount(0);
  const remainingConfigs = await (await page.request.get('/api/model-configs')).json();
  expect(remainingConfigs.find((item: {id: string}) => item.id === config.id)).toBeUndefined();
});
