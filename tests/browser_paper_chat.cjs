const {chromium}=require(process.env.EZREAD_PLAYWRIGHT);
const path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<div class="reader-workspace" style="display:flex;width:100%"><aside id="reader-paper-chat" class="reader-paper-chat" style="width:100%;max-width:500px"></aside></div>');
 for(const file of ['style.css','preferences.css','reader.css','redesign.css','surfaces.css']) await page.addStyleTag({path:path.resolve('static',file)});
 await page.addScriptTag({content:`const $=s=>document.querySelector(s); const state={reader:{id:'demo'},papers:[]}; function readBrowserSetting(){return null} function writeBrowserSetting(){return true} function toast(){} function setReaderPage(page){window.lastPDFPage=page} function selectBlock(){window.blockSelected=true};`});
 for(const file of ['core/dom.js','paper-chat-markdown.js','paper-chat.js']) await page.addScriptTag({path:path.resolve('static',file)});
 await page.evaluate(()=>{paperChatRuntime.paperId='demo';paperChatRuntime.data={generation:1,current:{generation:1},generations:[{generation:1,model:'Test'}],messages:[{role:'assistant',kind:'answer',text:'论文依据见原文；网络补充见 [网络1]。',citations:[{type:'pdf',block_id:'pdf-page-4',page:4},{type:'web',title:'公开研究来源',url:'https://example.org/paper'}]}]};renderPaperChat()});
 await page.getByRole('button',{name:'原 PDF 第 4 页',exact:true}).click();
 assert.equal(await page.evaluate(()=>window.lastPDFPage),4);
 assert.equal(await page.evaluate(()=>Boolean(window.blockSelected)),false);
 assert.equal(await page.locator('#paper-chat-web-search').count(),0);
 await page.evaluate(()=>renderPaperChat());
 await page.evaluate(()=>{paperChatRuntime.data.thread_id='test-thread';renderPaperChat()});
 assert.equal(await page.locator('.paper-chat-original').count(),0);
 assert.equal(await page.locator('#paper-chat-open-codex').innerText(),'在 Codex 中打开 ↗');
 assert.equal(await page.locator('.paper-chat-citations a').getAttribute('href'),'https://example.org/paper');
 for(const width of [1100,390]){await page.setViewportSize({width,height:850});await page.evaluate(()=>renderPaperChat());assert.equal(await page.locator('#paper-chat-web-search').count(),0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('#paper-chat-input').evaluate(e=>e.getBoundingClientRect().width>200));}
 await page.evaluate(()=>{
   if (!crypto.randomUUID) crypto.randomUUID=()=>"test-request-1";
   window.fixture=JSON.parse(JSON.stringify(paperChatRuntime.data));
   window.renderLibrary=()=>{};
   window.api=async(url,options)=>{
     if(url.endsWith('/open')) { window.openedGeneration=options.body.generation;window.fixture.thread_id='new-test-thread';return {ok:true}; }
     if(url.endsWith('?generation=1')) return {...window.fixture,generation:1,thread_id:'test-thread'};
     if(url.endsWith('/stop')) { window.streamCallback({type:'completed',stopped:true,unsaved:true,answer:''});window.finishStream();return {stopping:true}; }
     if(url.endsWith('/new')) { window.fixture={generation:2,current:{generation:2},generations:[{generation:2,model:'Test'},{generation:1,model:'Test'}],messages:[],thread_id:null}; }
     return JSON.parse(JSON.stringify(window.fixture));
   };
   window.apiStream=async(url,body,callback)=>{
     window.lastRequest={url,body};window.streamCallback=callback;
     callback({type:'operation',request_id:body.request_id});
     callback({type:'usage',usage:{last:{totalTokens:25000},total:{totalTokens:900000},modelContextWindow:100000}});
     callback({type:'status',stage:'compacting',label:'正在压缩上下文 · 剩余时间暂无法预计'});
     await new Promise(resolve=>window.finishStream=resolve);
   };
 });
 await page.locator('#paper-chat-input').fill('Test question');
 await page.getByRole('button',{name:'发送',exact:true}).click();
 await page.getByRole('button',{name:'停止',exact:true}).waitFor();
 assert.match(await page.locator('#paper-chat-usage').innerText(),/25%/);
 assert.match(await page.locator('#paper-chat-status-label').innerText(),/正在压缩上下文/);
 assert.equal(await page.locator('#paper-chat-new').isDisabled(),true);
 await page.getByRole('button',{name:'停止',exact:true}).click();
 await page.getByRole('button',{name:'发送',exact:true}).waitFor();
 assert.equal(await page.locator('#paper-chat-input').inputValue(),'Test question');
 await page.locator('#paper-chat-compact').click();
 assert.match(await page.evaluate(()=>lastRequest.url),/compact$/);
 await page.evaluate(()=>{streamCallback({type:'completed',compacted:true});finishStream()});
 await page.getByRole('button',{name:'发送',exact:true}).waitFor();
 assert.equal(await page.locator('#paper-chat-input').inputValue(),'Test question');
 await page.locator('#paper-chat-new').click();
 await page.waitForFunction(()=>paperChatRuntime.data.generation===2&&!paperChatRuntime.mutating);
 assert.equal(await page.locator('#paper-chat-input').inputValue(),'');
 assert.equal(await page.locator('#paper-chat-compact').isDisabled(),true);
 assert.match(await page.locator('#paper-chat-usage').innerText(),/待上报/);
 assert.equal(await page.locator('[aria-label="论文对话版本"] option').count(),2);
 await page.locator('#paper-chat-open-codex').click();
 await page.waitForFunction(()=>!paperChatRuntime.mutating&&paperChatRuntime.data.thread_id==='new-test-thread');
 assert.equal(await page.evaluate(()=>openedGeneration),2);
 await page.getByRole('combobox',{name:'论文对话版本'}).selectOption('1');
 await page.waitForFunction(()=>paperChatRuntime.data.generation===1);
 assert.equal(await page.locator('#paper-chat-input').isEnabled(),true);
 assert.equal(await page.locator('#reader-paper-chat').evaluate(e=>[...e.childNodes].some(n=>n.nodeType===3&&n.textContent==='null')),false);
 await page.locator('#paper-chat-input').fill('Continue old conversation');
 await page.getByRole('button',{name:'发送',exact:true}).click();
 await page.getByRole('button',{name:'停止',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>lastRequest.body.generation),1);
 await page.getByRole('button',{name:'停止',exact:true}).click();
 await page.getByRole('button',{name:'发送',exact:true}).waitFor();
 await page.locator('#paper-chat-compact').click();
 await page.getByRole('button',{name:'停止',exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>lastRequest.body.generation),1);
 await page.evaluate(()=>{streamCallback({type:'completed',compacted:true});finishStream()});
 await page.getByRole('button',{name:'发送',exact:true}).waitFor();
 await page.screenshot({path:'work/paper-chat-web-preview.png'});
 assert.deepEqual(errors,[]);console.log('Browser QA passed: no search toggle, rerender, source link, 1100/390px, no page errors.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
