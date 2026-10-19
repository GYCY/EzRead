const {chromium}=require(process.env.EZREAD_PLAYWRIGHT);
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.EZREAD_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try {
 const page=await browser.newPage({viewport:{width:430,height:850}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://fixture.test/**',route=>{const file=path.resolve('.'+new URL(route.request().url()).pathname);return route.fulfill({path:file})});
 await page.setContent('<!doctype html><div id="out" style="width:390px"></div>');
 await page.addStyleTag({url:'https://fixture.test/static/vendor/katex/katex.min.css'});
 await page.addStyleTag({path:path.resolve('static/surfaces.css')});
 for(const file of ['core/dom.js','vendor/katex/katex.min.js','paper-chat-markdown.js']) await page.addScriptTag({path:path.resolve('static',file)});
 const render=async text=>{await page.evaluate(t=>document.querySelector('#out').replaceChildren(paperChatMarkdown(t)),text)};
 await render(String.raw`弹性模量 $E=\frac{\sigma}{\varepsilon}$，泊松比 \(\nu=-\frac{\varepsilon_t}{\varepsilon_a}\)。

\[
\boldsymbol{\sigma}=\mathbf{C}\boldsymbol{\varepsilon}
\]

$$E=2G(1+\nu)$$

- 张量 $a_{ij}$ 与 $b_{ij}$

| 参数 | 数值 |
| --- | --- |
| $E$ | $10^6$ |`);
 assert.equal(await page.locator('.katex').count(),8);
 await page.evaluate(()=>document.fonts.ready);
 assert.equal(await page.evaluate(()=>document.fonts.check('16px KaTeX_Main')),true);
 await page.screenshot({path:'work/chat-math-preview.png'});
 await render('`$x$`\n\n```latex\n\\[x\\]\n```');assert.equal(await page.locator('.katex').count(),0);
 await render(String.raw`尚未完成 \(\frac{E}{`);assert.equal(await page.locator('.katex').count(),0);
 await render(String.raw`完成 \(\frac{E}{2}\)`);assert.equal(await page.locator('.katex').count(),1);
 await render(String.raw`无效 $\notARealCommand{x}$`);assert.match(await page.locator('#out').innerText(),/notARealCommand/);
 await render(String.raw`$\href{javascript:alert(1)}{click}$ <img src=x onerror=alert(1)>`);
 assert.equal(await page.locator('#out a, #out img, #out script').count(),0);
 assert.deepEqual(errors,[]);console.log('Math browser QA passed: 4 delimiters, fractions, matrices, lists, tables, fonts, incomplete stream, invalid math, literal code and unsafe commands.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
