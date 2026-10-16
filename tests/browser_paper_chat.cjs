const {chromium}=require(process.env.EZREAD_PLAYWRIGHT);
const path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<div class="reader-workspace" style="display:flex;width:100%"><aside id="reader-paper-chat" class="reader-paper-chat" style="width:100%;max-width:500px"></aside></div>');
 for(const file of ['style.css','preferences.css','reader.css','redesign.css','surfaces.css']) await page.addStyleTag({path:path.resolve('static',file)});
 await page.addScriptTag({content:`const $=s=>document.querySelector(s); const state={reader:{id:'demo'},papers:[]}; function readBrowserSetting(){return null} function writeBrowserSetting(){return true} function toast(){} function setReaderPage(){} function selectBlock(){};`});
 for(const file of ['core/dom.js','paper-chat-markdown.js','paper-chat.js']) await page.addScriptTag({path:path.resolve('static',file)});
 await page.evaluate(()=>{paperChatRuntime.paperId='demo';paperChatRuntime.data={generation:1,current:{generation:1},generations:[{generation:1,model:'Test'}],messages:[{role:'assistant',kind:'answer',text:'论文依据见原文；网络补充见 [网络1]。',citations:[{block_id:'p1-b1',page:1},{type:'web',title:'公开研究来源',url:'https://example.org/paper'}]}]};renderPaperChat()});
 assert.equal(await page.locator('#paper-chat-web-search').count(),0);
 await page.evaluate(()=>renderPaperChat());
 assert.equal(await page.locator('.paper-chat-citations a').getAttribute('href'),'https://example.org/paper');
 for(const width of [1100,390]){await page.setViewportSize({width,height:850});await page.evaluate(()=>renderPaperChat());assert.equal(await page.locator('#paper-chat-web-search').count(),0);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.ok(await page.locator('#paper-chat-input').evaluate(e=>e.getBoundingClientRect().width>200));}
 await page.screenshot({path:'work/paper-chat-web-preview.png'});
 assert.deepEqual(errors,[]);console.log('Browser QA passed: no search toggle, rerender, source link, 1100/390px, no page errors.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
