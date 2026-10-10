import {test,expect} from '../../frontend/node_modules/@playwright/test';
import path from 'node:path';
test('import → indexing → chat → sources → refresh → deleted-source snapshot',async({page,request})=>{
 for(const purpose of ['chat','embedding']){const config=await (await request.post('/api/model-configs',{data:{purpose,mode:'api',endpoint:'https://fixture.test/v1',model:'fixture',key:'test-key'}})).json();expect((await request.post('/api/model-configs/'+config.id+'/test')).ok()).toBeTruthy();await request.put('/api/settings',{data:{['default_'+purpose]:config.id}})}
 await page.goto('/knowledge');await page.getByLabel('新知识库名称').fill('浏览器样本');await page.getByRole('button',{name:'新建知识库'}).click();await expect(page.getByLabel('知识库筛选')).not.toHaveValue('');
 await page.getByLabel('上传资料').setInputFiles(path.resolve('../tests/fixtures/policy-a.txt'));await page.getByRole('button',{name:'批量上传'}).click();await expect(page.locator('.tag').filter({hasText:'可检索'})).toBeVisible();
 const doc=(await (await request.get('/api/documents')).json()).items[0];
 await page.goto('/chat');await page.getByLabel('问题',{exact:true}).fill('退款期限？');await page.getByRole('button',{name:'发送问题'}).click();await expect(page.getByText('资料说明：退款期限七天。',{exact:false})).toBeVisible();await expect(page.getByText('· completed',{exact:false})).toBeVisible();
 const before=(await (await request.get('/api/test-calls')).json()).calls;await page.reload();await expect(page.getByText('资料说明：退款期限七天。',{exact:false})).toBeVisible();expect((await (await request.get('/api/test-calls')).json()).calls).toBe(before);
 await page.locator('.citation').click();await expect(page.locator('.source-panel pre')).toContainText('七天');
 await request.delete('/api/documents/'+doc.id);await page.reload();await expect(page.locator('.source-panel')).toContainText('原来源已删除');await expect(page.locator('.source-panel pre')).toContainText('七天');
});
test('PDF.js renders a local page and page navigation',async({page,request})=>{
 const kb=await (await request.post('/api/knowledge-bases',{data:{name:'PDF页码'}})).json();
 const fs=await import('node:fs');const result=await (await request.post('/api/documents/upload',{multipart:{kb_id:kb.id,request_key:'pdf-browser',files:{name:'two-pages.pdf',mimeType:'application/pdf',buffer:fs.readFileSync(path.resolve('../tests/fixtures/two-pages.pdf'))}}})).json();
 await page.goto('/documents/'+result[0].document_id+'?sourcePage=2');await expect(page.getByLabel('PDF 第 2 页')).toBeVisible();await expect(page.getByText('第 2 / 2 页',{exact:true})).toBeVisible();await page.getByRole('button',{name:'上一页',exact:true}).click();await expect(page.getByLabel('PDF 第 1 页')).toBeVisible();
});
test('homepage forwards exactly one question with selected scope and stats link',async({page,request})=>{
 const kbs=await (await request.get('/api/knowledge-bases')).json();const kb=kbs.find((r:any)=>r.name==='PDF页码');
 await page.goto('/');await page.getByLabel('首页知识库').selectOption(kb.id);await page.getByLabel('首页问题').fill('首页只提交一次的问题');const before=(await (await request.get('/api/test-calls')).json()).calls;await page.getByRole('button',{name:'开始提问'}).click();await expect(page).toHaveURL(/\/chat\//);await expect(page.locator('.message.user')).toHaveCount(1);await expect(page.locator('.message.user')).toContainText('首页只提交一次的问题');await expect(page.getByLabel('指定资料')).toHaveValues([kb.id]);await expect(page.getByText('· completed',{exact:false})).toBeVisible();expect((await (await request.get('/api/test-calls')).json()).calls).toBe(before+1);
 await page.reload();await expect(page.locator('.message.user')).toHaveCount(1);expect((await (await request.get('/api/test-calls')).json()).calls).toBe(before+1);
 await page.goto('/');await page.locator('.stat').filter({hasText:'可检索'}).click();await expect(page).toHaveURL(/status=ready/);await expect(page.getByLabel('文档状态筛选')).toHaveValue('ready');
});
