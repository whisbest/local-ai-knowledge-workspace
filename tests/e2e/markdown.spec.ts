import {test, expect} from '../../frontend/node_modules/@playwright/test';
import path from 'node:path';

test('assistant markdown renders readable headings lists emphasis code and citations', async ({page, request}) => {
  const chat = await (await request.post('/api/model-configs', {data: {
    purpose: 'chat', mode: 'api', endpoint: 'https://fixture.test/v1', model: 'markdown', key: 'test-key',
  }})).json();
  const embedding = await (await request.post('/api/model-configs', {data: {
    purpose: 'embedding', mode: 'api', endpoint: 'https://fixture.test/v1', model: 'markdown', key: 'test-key',
  }})).json();
  await (await request.post('/api/model-configs/' + chat.id + '/test')).json();
  await (await request.post('/api/model-configs/' + embedding.id + '/test')).json();
  await request.put('/api/settings', {data: {default_chat: chat.id, default_embedding: embedding.id}});
  const kb = await (await request.post('/api/knowledge-bases', {data: {name: 'Markdown 渲染'}})).json();
  const fs = await import('node:fs');
  await request.post('/api/documents/upload', {multipart: {
    kb_id: kb.id, request_key: 'markdown-upload', files: {name: 'policy.txt', mimeType: 'text/plain', buffer: fs.readFileSync(path.resolve('../tests/fixtures/policy-a.txt'))},
  }});
  for (let attempt = 0; attempt < 30; attempt++) {
    const docs = await (await request.get('/api/documents')).json();
    if (docs.items[0]?.state === 'ready') break;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  const conversation = await (await request.post('/api/conversations', {data: {}})).json();
  const generation = await (await request.post('/api/conversations/' + conversation.id + '/generations', {data: {
    question: '格式测试', request_key: 'markdown-e2e', model_id: chat.id,
  }})).json();
  await page.goto('/chat/' + conversation.id + '?generationId=' + generation.id);
  await expect(page.locator('.message.assistant')).toBeVisible();
  await expect(page.locator('.message.assistant .answer')).toBeVisible();
  await expect(page.locator('.answer h2')).toContainText('资料说明');
  await expect(page.locator('.answer ul li')).toHaveCount(2);
  await expect(page.locator('.answer code')).toContainText('RAG');
  await expect(page.locator('.citation')).toHaveCount(1);
});
