const {chromium} = require(process.env.EZREAD_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = process.env.EZREAD_TEST_ROOT, origin = process.env.EZREAD_TEST_URL, pid = '0123456789abcdef';
const edge = process.env.EZREAD_BROWSER || path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:edge});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage(), errors = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  const source = id => page.locator(`.reader-source-text[data-source-id="${id}"]`);
  const apiState = async () => (await page.request.get(origin + '/__test/state')).json();
  let firstOpen = true;
  async function open() {
    await page.goto(origin); await page.waitForSelector('.paper-card');
    await page.evaluate(id => openReader(id), pid); await source('one').waitFor();
    if(firstOpen) {
      assert.equal(await page.locator('#reader-text-layout').count(),0);
      assert.equal(await page.locator('.reader-source-page').count(),2);firstOpen=false;
    }
    await page.evaluate(() => {
      window.__nativeCopy = null;
      document.addEventListener('copy', e => { window.__nativeCopy = window.getSelection().toString(); e.preventDefault(); }, {once:true});
      Object.defineProperty(navigator, 'clipboard', {configurable:true,value:{writeText:async text => {window.__menuCopy=text;}}});
    });
  }
  async function select(id, quote, drag=false) {
    await page.evaluate(() => { readerCloseTextPopup(true); window.getSelection().removeAllRanges(); });
    await source(id).scrollIntoViewIfNeeded();
    const point = await source(id).evaluate((span, quote) => {
      const at = span.textContent.indexOf(quote); if (at < 0) throw Error('Missing quote: ' + quote);
      const range = document.createRange(); range.setStart(span.firstChild,at); range.setEnd(span.firstChild,at+quote.length);
      const start=range.cloneRange(), end=range.cloneRange(); start.collapse(true); end.collapse(false);
      const a=start.getBoundingClientRect(),b=end.getBoundingClientRect();
      window.__selectionNode=span.firstChild;
      return {start:{x:a.left+.25,y:a.top+a.height/2},end:{x:b.left-.25,y:b.top+b.height/2}, x:(a.left+b.left)/2,y:a.top+a.height/2};
    }, quote);
    if(drag) { await page.mouse.move(point.start.x,point.start.y);await page.mouse.down();await page.mouse.move(point.end.x,point.end.y,{steps:8});await page.mouse.up(); }
    else await source(id).evaluate((span,quote)=>{const at=span.textContent.indexOf(quote),range=document.createRange();range.setStart(span.firstChild,at);range.setEnd(span.firstChild,at+quote.length);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);},quote);
    return point;
  }
  async function menu(point) { await page.mouse.click(point.x,point.y,{button:'right'}); await page.getByRole('menu',{name:'所选文字操作'}).waitFor(); }
  async function command(name) { await page.getByRole('menuitem',{name,exact:true}).click(); }
  async function save(value) { await page.getByRole('textbox',{name:/所选文字批注|修订选中的文字/}).fill(value);await page.locator('.reader-text-editor').getByRole('button',{name:'保存',exact:true}).click();await page.waitForFunction(()=>readerTextUI.editor===null); }
  try {
    await open();
    const startup = await apiState(), initial = startup.doc;
    assert.equal(startup.preparations.length,1);
    assert.equal(startup.calls.length,0);
    await page.evaluate(() => prepareSelectionSession());
    assert.equal((await apiState()).preparations.length,1);
    assert.equal(await page.getByText(/后台连接测试|预热完成/).count(),0);
    checks.push('opening the main page starts silent background preparation once without showing test output');
    const point=await select('one','第一😀句',true);
    await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    assert.equal(await page.evaluate(()=>window.getSelection().toString()),'第一😀句');
    assert.equal(await page.evaluate(()=>document.querySelector('[data-source-id="one"]').firstChild===window.__selectionNode),true);
    await page.keyboard.press('Control+c');
    assert.equal(await page.evaluate(()=>window.__nativeCopy),'第一😀句');
    await menu(point); await command('复制');assert.equal(await page.evaluate(()=>window.__menuCopy),'第一😀句');
    assert.equal((await apiState()).calls.length,0);
    checks.push('native drag automatically opens the selection menu and Ctrl+C retains exact text');

    await menu(await select('one','第一😀句'));
    assert.equal(await page.getByRole('menuitem',{name:'重新翻译对应段落',exact:true}).count(),0);
    assert.equal(await page.getByRole('menuitem',{name:'定位原 PDF',exact:true}).count(),0);
    await command('提问');
    await page.locator('#paper-chat-input').waitFor();
    assert.equal(await page.locator('#paper-chat-input').inputValue(),'');
    assert.equal(await page.locator('.paper-chat-compose .paper-chat-attachment').innerText(),'▤\n已引用选中文字\n×');
    assert.equal(await page.locator('.paper-chat-compose').innerText().then(t=>t.includes('第一😀句')),false);
    await page.locator('#paper-chat-input').fill('我已有的问题');
    await page.evaluate(()=>closePaperChat());
    await menu(await select('one','第二句话'));
    await command('提问');
    await page.waitForFunction(()=>document.querySelector('#paper-chat-input')?.value==='我已有的问题');
    assert.equal((await apiState()).calls.length,0);
    await page.getByRole('button',{name:'移除引用文本'}).click();
    assert.equal(await page.locator('.paper-chat-compose .paper-chat-attachment').count(),0);
    assert.equal(await page.locator('#paper-chat-input').inputValue(),'我已有的问题');
    await page.locator('#paper-chat-input').fill('');
    await page.evaluate(()=>closePaperChat());
    checks.push('selection question opens chat, preserves draft, attaches hidden selection without sending, and removes retired menu actions');

    await select('one','第一');await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    await page.keyboard.press('Escape');await page.waitForTimeout(180);
    assert.equal(await page.getByRole('menu',{name:'所选文字操作'}).count(),0,'Escape must not reopen the same menu.');
    await page.evaluate(()=>window.getSelection().modify('extend','forward','character'));
    await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    await page.evaluate(()=>window.getSelection().removeAllRanges());
    await page.waitForFunction(()=>!document.querySelector('.reader-text-menu'));
    checks.push('keyboard-style selection extension opens the menu, Escape suppresses reopening and clearing selection dismisses it');
    await select('one','第一😀句');await page.getByRole('menu',{name:'所选文字操作'}).waitFor();

    await page.request.post(origin+'/__test/append');await page.evaluate(()=>refreshReader());
    assert.equal(await page.evaluate(()=>window.getSelection().toString()),'第一😀句');
    assert.equal(await page.evaluate(()=>document.querySelector('[data-source-id="one"]').firstChild===window.__selectionNode),true);
    await page.evaluate(()=>window.getSelection().removeAllRanges());await page.evaluate(()=>refreshReader());
    await source('one').filter({hasText:'后台更新'}).waitFor();
    checks.push('background update waits for selection to end');

    const cross = await page.evaluate(()=>{
      const one=document.querySelector('[data-source-id="one"]'),two=document.querySelector('[data-source-id="two"]');
      const range=document.createRange();range.setStart(one.firstChild,0);range.setEnd(two.firstChild,4);window.getSelection().removeAllRanges();window.getSelection().addRange(range);
      const rect=range.getClientRects()[0];return {x:rect.left+3,y:rect.top+rect.height/2};
    });
    assert.equal(await page.evaluate(()=>readerSelectionSnapshot().ranges.length),2);
    await menu(cross);await command('黄色高亮');
    await page.waitForFunction(()=>state.reader.text_annotations?.length===1);
    assert.equal((await apiState()).doc.text_annotations[0].ranges.length,2);
    assert.equal(await page.evaluate(()=>CSS.highlights.get('ezread-text-highlight').size),2);
    checks.push('cross-paragraph selection persists as one two-range highlight');

    const redPoint=await select('two','自然段');await menu(redPoint);await command('红色高亮');
    await page.waitForFunction(()=>state.reader.text_annotations?.some(a=>a.color==='red'));
    await open();
    assert.deepEqual((await apiState()).doc.text_annotations.map(a=>a.color),['yellow','red']);
    assert.equal(await page.evaluate(()=>CSS.highlights.get('ezread-text-highlight').size),2);
    assert.equal(await page.evaluate(()=>CSS.highlights.get('ezread-text-highlight-red').size),1);
    for (const theme of ['paper','sage','graphite']) {
      await page.evaluate(theme=>{state.settings.theme=theme;applyPreferences();},theme);
      assert.equal(await source('two').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-highlight-red)').backgroundColor),'rgb(246, 190, 190)');
      assert.equal(await source('one').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-highlight)').backgroundColor),'rgb(246, 231, 182)');
      assert.equal(await source('two').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-highlight-red)').color),'rgb(32, 43, 37)');
      assert.deepEqual((await apiState()).doc.text_annotations.map(a=>a.color),['yellow','red']);
    }
    await page.evaluate(()=>{state.settings.theme='paper';applyPreferences();});
    checks.push('annotation colors and legible text remain stable across all three themes without changing saved marks');
    await page.setViewportSize({width:1000,height:800});
    await menu(await select('two','自然段'));
    const highlightRow=page.getByRole('group',{name:'高亮',exact:true});
    assert.equal(await highlightRow.count(),1);
    assert.equal(await highlightRow.innerText(),'高亮');
    const swatches=highlightRow.getByRole('menuitem');
    assert.equal(await swatches.count(),2);
    const yellowBounds=await swatches.nth(0).boundingBox(),redBounds=await swatches.nth(1).boundingBox();
    assert.ok(Math.abs(yellowBounds.y-redBounds.y)<1&&redBounds.x>yellowBounds.x);
    assert.equal(await swatches.nth(0).innerText(),'');
    assert.equal(await swatches.nth(1).innerText(),'');
    const bounds=await page.getByRole('menu',{name:'所选文字操作'}).boundingBox();
    assert.ok(bounds.x>=0&&bounds.y>=0&&bounds.x+bounds.width<=1000&&bounds.y+bounds.height<=800);
    await page.screenshot({path:path.join(root,'work/reader-highlight-menu-preview.png')});
    await page.evaluate(()=>{readerCloseTextPopup();window.getSelection().removeAllRanges();});
    await page.screenshot({path:path.join(root,'work/reader-red-highlight-preview.png')});
    await page.setViewportSize({width:1440,height:1000});
    checks.push('one highlight row with clickable yellow/red swatches fits narrow menus and preserves colors on reopening');

    await page.evaluate(()=>window.getSelection().removeAllRanges());
    await page.getByRole('button',{name:'英文原文',exact:true}).click();
    const notePoint=await select('one','Another sentence');await menu(notePoint);await command('添加批注');await save('精准的文字批注');
    assert.equal((await apiState()).doc.text_annotations.filter(a=>a.kind==='note').length,1);
    await open();
    await page.locator('.translation-block[data-block-id="one"] .reader-annotation-rail').first().click();
    await page.getByText('精准的文字批注',{exact:true}).waitFor();
    await page.screenshot({path:path.join(root,'work/reader-text-note-preview.png')});
    assert.equal(await page.locator('.reader-annotation-markers').count(),0);
    assert.equal(await source('one').evaluate(span=>getComputedStyle(span,'::highlight(ezread-text-note)').textDecorationColor),'rgb(59, 130, 246)');
    checks.push('note survives reopening, has a blue underline and opens from its blue side rail');

    await select('one','Another sentence');await page.getByRole('menu',{name:'所选文字操作'}).waitFor();
    await command('添加批注');await save('重叠的另一条批注');
    await page.locator('.translation-block[data-block-id="one"] .reader-annotation-rail').click();
    await page.getByRole('button',{name:'重叠的另一条批注',exact:true}).click();
    await page.getByText('重叠的另一条批注',{exact:true}).waitFor();
    checks.push('overlapping notes share a rail and each remains accessible');

    await page.evaluate(()=>{readerCloseTextPopup();window.getSelection().removeAllRanges();});
    await source('four').scrollIntoViewIfNeeded();
    await page.evaluate(()=>{
      const one=document.querySelector('[data-source-id="four"]'),two=document.querySelector('[data-source-id="five"]');
      const range=document.createRange();range.setStart(one.firstChild,0);range.setEnd(two.firstChild,8);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);window.__longNoteNode=one.firstChild;
    });
    await page.getByRole('menu',{name:'所选文字操作'}).waitFor();await command('添加批注');await save('跨段和多行批注');
    const crossNote=(await apiState()).doc.text_annotations.find(a=>a.note==='跨段和多行批注');
    assert.equal(crossNote.ranges.length,2);
    assert.equal(await page.locator(`.reader-annotation-rail[data-annotation-ids="${crossNote.id}"]`).count(),2);
    const geometryMatches=async ()=>{ try { await page.waitForFunction(id=>{
      for(const painted of readerTextUI.painted.filter(v=>v.item.id===id)) {
        const span=painted.range.startContainer.parentElement.closest('.reader-source-text');
        const rail=span.closest('.translation-block').querySelector('.reader-annotation-rail');
        const rects=[...painted.range.getClientRects()].filter(r=>r.width&&r.height),box=rail.getBoundingClientRect();
        const top=Math.min(...rects.map(r=>r.top)),bottom=Math.max(...rects.map(r=>r.bottom));
        if(Math.abs(box.top-top)>1||Math.abs(box.bottom-bottom)>1||box.left<Math.max(...rects.map(r=>r.right)))return false;
      }
      return true;
    },crossNote.id,{timeout:3000}); } catch(error) {
      console.log(JSON.stringify(await page.evaluate(id=>readerTextUI.painted.filter(v=>v.item.id===id).map(painted=>{
        const span=painted.range.startContainer.parentElement.closest('.reader-source-text'),paragraph=span.closest('.translation-block');
        const rail=paragraph.querySelector('.reader-annotation-rail'),box=rail.getBoundingClientRect(),rects=[...painted.range.getClientRects()].filter(r=>r.width&&r.height);
        return {source:span.dataset.sourceId,rail:{top:box.top,bottom:box.bottom,left:box.left},expected:{top:Math.min(...rects.map(r=>r.top)),bottom:Math.max(...rects.map(r=>r.bottom)),right:Math.max(...rects.map(r=>r.right))},style:rail.getAttribute('style'),paragraph:paragraph.getBoundingClientRect().toJSON()};
      }),crossNote.id)));
      await page.screenshot({path:path.join(root,'work/reader-annotation-geometry-failure.png')});throw error;
    } };
    await geometryMatches();
    await page.setViewportSize({width:850,height:1000});await geometryMatches();
    await source('four').evaluate(span=>{span.closest('.translated-text').style.fontSize='26px';});await geometryMatches();
    assert.equal(await source('four').evaluate(span=>span.firstChild===window.__longNoteNode),true);
    await source('four').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(root,'work/reader-annotation-rails-preview.png')});
    await page.locator(`.translation-block[data-block-id="five"] .reader-annotation-rail`).click();
    await page.getByText('跨段和多行批注',{exact:true}).waitFor();
    await page.setViewportSize({width:1440,height:1000});
    await open();
    checks.push('one cross-paragraph note has per-paragraph rails covering actual selected lines after resize and font changes without replacing text');

    const editPoint=await select('one','First');await menu(editPoint);await command('修订原文');await save('Corrected');
    let current=(await apiState()).doc;
    assert.equal(current.blocks[0].text,initial.blocks[0].text);
    assert.equal(current.blocks[0].translation,initial.blocks[0].translation+' 后台更新。');
    assert.ok(current.blocks[0].text_override.startsWith('Corrected'));
    await page.getByText('原文已修改，译文待核对',{exact:true}).waitFor();
    assert.equal((await apiState()).calls.length,0);
    checks.push('source correction is reversible and retains original text and translation');

    await page.getByRole('button',{name:'译文',exact:true}).click();
    const chinesePoint=await select('one','😀');await menu(chinesePoint);await command('修订译文');await save('修正');
    current=(await apiState()).doc;assert.ok(current.blocks[0].translation.includes('第一修正句'));
    assert.equal(await page.evaluate(()=>state.reader.text_annotations.find(a=>a.kind==='highlight').status),'needs_review');
    await page.getByRole('button',{name:/待核对标记 1/}).waitFor();
    checks.push('translation revision changes only selected characters and flags changed anchors');

    await page.getByRole('button',{name:'英文原文',exact:true}).click();
    const wordPoint=await select('one','Corrected');await menu(wordPoint);await command('翻译单词');
    await page.waitForFunction(()=>document.querySelector('.reader-selected-translation-result')?.textContent.includes('corrected'));
    assert.equal((await apiState()).calls.length,0,'Dictionary lookup must not call the model.');
    checks.push('offline dictionary finds selected words without model calls');
    const translatePoint=await select('one','Corrected');await menu(translatePoint);await command('翻译句子');
    await page.getByText('模拟划线译文：Corrected',{exact:true}).waitFor();
    assert.equal((await apiState()).calls.length,1);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#reader-selection-mode-hint').innerText(),'选中文字即可显示操作选项');
    const autoPoint=await select('one','Corrected',true);
    assert.equal(await page.evaluate(()=>window.getSelection().toString()),'Corrected');
    assert.equal((await apiState()).calls.length,1,'Left mouse selection must not infer automatically.');
    await menu(autoPoint);await command('复制');
    assert.equal((await apiState()).calls.length,1,'Copying must not infer.');
    checks.push('automatic selection menu still requires an explicit dictionary or model choice and retains native text selection');

    await page.request.post(origin+'/__test/model-delay',{data:{enabled:true}});
    const slowStart = async () => {
      const count=(await apiState()).calls.length;
      const point=await select('one','Another sentence.');await menu(point);await command('翻译句子');
      await page.waitForFunction(async count=>(await (await fetch('/__test/state')).json()).calls.length>count,count);
    };
    const cancelled = async () => page.waitForFunction(async ()=>(await (await fetch('/__test/state')).json()).calls.at(-1)?.cancelled===true);
    await slowStart();
    await page.keyboard.press('Escape');
    await cancelled();
    checks.push('closing translation card cancels the actual backend request');
    await slowStart();
    await source('one').evaluate(span=>{const range=document.createRange();range.setStart(span.firstChild,0);range.setEnd(span.firstChild,9);const s=window.getSelection();s.removeAllRanges();s.addRange(range);});
    await cancelled();
    assert.equal(await page.locator('.reader-selected-translation').count(),0);
    const replacement=await select('one','Corrected');await menu(replacement);await command('翻译单词');
    await page.waitForFunction(()=>document.querySelector('.reader-selected-translation-result')?.textContent.includes('corrected'));
    await page.waitForTimeout(150);
    assert.match(await page.locator('.reader-selected-translation-result').innerText(),/corrected/);
    checks.push('selection change cancels old inference and old result cannot overwrite dictionary card');
    await slowStart();await page.evaluate(()=>closeReader());await cancelled();
    await page.evaluate(id=>openReader(id),pid);await source('one').waitFor();
    checks.push('closing and reopening reader cancels pending model translation');

    await page.request.post(origin+'/__test/model-delay',{data:{enabled:false}});
    await page.request.post(origin+'/__test/model-progress',{data:{mode:'success'}});
    await page.setViewportSize({width:550,height:700});
    await menu(await select('one','Another sentence.'));await command('翻译句子');
    const latest=page.locator('.reader-translation-current-status');
    await latest.getByText('网络不可用. 正在重新连接... 1/5',{exact:true}).waitFor();
    await page.getByText('模拟正在输出的部分译文',{exact:true}).waitFor();
    const progressBounds=await page.locator('.reader-selected-translation').boundingBox();
    assert(progressBounds.x>=0&&progressBounds.y>=0&&progressBounds.x+progressBounds.width<=551&&progressBounds.y+progressBounds.height<=701);
    await page.screenshot({path:path.join(root,'work/reader-sentence-progress-preview.png')});
    await page.waitForFunction(()=>readerTextUI.request===null);
    assert.equal(await page.locator('.reader-selected-translation-result').innerText(),'模拟划线译文：Another sentence.');
    assert.equal(await latest.innerText(),'翻译完成。');
    assert.equal(await page.locator('.reader-translation-progress-log').count(),0);
    assert.match(await page.locator('.reader-translation-progress-hint').innerText(),/用时/);
    assert.equal(await page.locator('.reader-selected-translation button').count(),0);
    checks.push('latest Chinese status and the current partial translation overwrite old output in a narrow window');

    await page.request.post(origin+'/__test/model-progress',{data:{mode:'failure'}});
    await menu(await select('one','Another sentence.'));await command('翻译句子');
    await page.getByText('无法连接 Codex 服务，请检查网络。',{exact:true}).first().waitFor();
    await page.waitForFunction(()=>readerTextUI.request===null);
    assert.equal(await latest.innerText(),'无法连接 Codex 服务，请检查网络。');
    assert.equal(await latest.count(),1);
    await page.screenshot({path:path.join(root,'work/reader-sentence-failure-preview.png')});
    checks.push('failure displays only the latest error without accumulating a conversation history');
    await page.request.post(origin+'/__test/model-progress',{data:{mode:'success'}});
    await menu(await select('one','Another sentence.'));await command('翻译句子');
    await latest.getByText('网络不可用. 正在重新连接... 1/5',{exact:true}).waitFor();
    await page.getByRole('button',{name:'取消翻译',exact:true}).click();await cancelled();
    assert.equal(await page.locator('.reader-selected-translation-result').innerText(),'已取消本次翻译。');
    assert.match(await latest.innerText(),/对话保留/);
    assert.equal(await page.getByRole('button',{name:'取消翻译',exact:true}).count(),0);
    await menu(await select('one','Corrected'));await command('翻译单词');
    await page.waitForFunction(()=>document.querySelector('.reader-selected-translation-result')?.textContent.includes('corrected'));
    await page.waitForTimeout(650);
    assert.equal(await page.locator('.reader-translation-progress').count(),0);
    assert.match(await page.locator('.reader-selected-translation-result').innerText(),/corrected/);
    checks.push('manual cancel interrupts the request and polling, retains the paper context and cannot overwrite a new dictionary result');
    await page.request.post(origin+'/__test/model-progress',{data:{mode:''}});

    for(const width of [850,550]) {
      await page.setViewportSize({width,height:900});
      const point=await select('one','Corrected');await menu(point);
      await page.getByRole('menuitem',{name:'翻译单词',exact:true}).waitFor();
      await page.getByRole('menuitem',{name:'翻译句子',exact:true}).waitFor();
      const rect=await page.getByRole('menu',{name:'所选文字操作'}).boundingBox();
      assert(rect.x>=0&&rect.y>=0&&rect.x+rect.width<=width+1&&rect.y+rect.height<=901);
      await page.screenshot({path:path.join(root,`work/reader-translation-menu-${width}.png`)});
    }
    checks.push('translation choices fit narrow windows at 850 and 550 pixels');
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(root,'work/reader-text-selection-preview.png')});
    await page.evaluate(() => {
      readerCloseTextPopup(true); window.getSelection().removeAllRanges();
      state.reader.blocks.find(b=>b.id==='four').text += ' A continuous passage remains on its original PDF page as the page grows.'.repeat(24);
      state.reader.blocks.find(b=>b.id==='five').page = 2;
      state.reader.blocks.push({id:'figure-caption',page:1,kind:'caption',text:'Fig. 1. Original diagram',translation:'图 1. 示意图',bbox:[.1,.92,.9,.96]});
      state.reader.reading_structure.sections.at(-1).entries.push({id:'figure-caption',role:'caption',source_ids:['figure-caption']});
      state.reader.figures = [{id:'fig-1',page:1,url:state.reader.pages[0].image_url,caption:'Fig. 1. Original diagram',bbox:[.1,.3,.9,.85]}];
      for (const [id, flags] of [['table-fixture',{is_table:true}],['formula-fixture',{is_formula:true}]]) {
        state.reader.blocks.push({id,page:1,kind:'paragraph',text:id,translation:id,image_url:state.reader.pages[0].image_url,...flags});
        state.reader.reading_structure.sections.at(-1).entries.push({id,role:'paragraph',source_ids:[id]});
      }
      renderTranslations();
    });
    const figure = page.locator('.reader-inline-figure');
    await figure.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => {const img=document.querySelector('.reader-inline-figure img');return img.complete&&img.naturalWidth>0;});
    assert(await figure.evaluate(n=>Boolean(n.compareDocumentPosition(document.querySelector('[data-block-id="figure-caption"]'))&Node.DOCUMENT_POSITION_FOLLOWING)));
    const imageSource = await figure.locator('img').getAttribute('src');
    await page.evaluate(() => { state.reader.blocks.find(b=>b.id==='figure-caption').translation='更新后的图注'; renderTranslations(); renderTranslations(); });
    assert.equal(await figure.count(),1);
    assert.equal(await figure.locator('img').getAttribute('src'),imageSource);
    for(const width of [1440,550]) {
      await page.setViewportSize({width,height:1000});
      await page.evaluate(() => changePreference({reader_font_size:18,reader_zoom:100}));
      await figure.scrollIntoViewIfNeeded();
      const fits = await figure.locator('img').evaluate(img=>img.getBoundingClientRect().width<=document.querySelector('#translation-scroll').clientWidth);
      assert(fits,'Original figure fits the translation pane.');
      const base = await figure.locator('img').boundingBox();
      const table = page.locator('.reader-table-image'), formula = page.locator('.reader-formula-image');
      await table.scrollIntoViewIfNeeded(); await formula.scrollIntoViewIfNeeded();
      await page.waitForFunction(()=>[...document.querySelectorAll('.reader-source-image img')].every(img=>img.complete&&img.naturalWidth));
      const tableBase = await table.boundingBox(), formulaBase = await formula.boundingBox();
      const parentWidth = img => img.evaluate(n=>n.parentElement.clientWidth);
      const figureParent=await parentWidth(figure.locator('img')),tableParent=await parentWidth(table),formulaParent=await parentWidth(formula);
      const spacing = () => source('two').evaluate(n=>{const p=getComputedStyle(n.closest('.translated-text')),b=getComputedStyle(n.closest('.translation-block'));return {line:parseFloat(p.lineHeight),margin:parseFloat(b.marginBottom),padding:parseFloat(b.paddingTop)};});
      const spacingBase = await spacing();
      // Dispatch the actual DOM wheel event on the image so bubbling and the
      // translation-pane handler both run; only the system browser zoom is excluded.
      async function zoom(deltaY, expected) {
        await figure.locator('img').dispatchEvent('wheel',{deltaY,ctrlKey:true,bubbles:true,cancelable:true});
        await page.waitForFunction(size=>state.settings.reader_zoom===size,expected);
        const box = await figure.locator('img').boundingBox();
        assert(Math.abs(box.width/base.width/(await parentWidth(figure.locator('img'))/figureParent)-expected/100)<.01,'Image width follows text zoom.');
        assert(Math.abs(box.height/box.width-base.height/base.width)<.01,'Image aspect ratio survives zoom.');
        assert(Math.abs((await table.boundingBox()).width/tableBase.width/(await parentWidth(table)/tableParent)-expected/100)<.01,'Tables follow zoom.');
        assert(Math.abs((await formula.boundingBox()).width/formulaBase.width/(await parentWidth(formula)/formulaParent)-expected/100)<.01,'Formula crops follow zoom.');
        const spaces = await spacing();
        for(const key of ['line','margin','padding']) assert(Math.abs(spaces[key]/spacingBase[key]-expected/100)<.01,`${key} follows text zoom.`);
        if(expected<100) for(const img of [figure.locator('img'),table,formula]) assert(await img.evaluate(n=>{const a=n.getBoundingClientRect(),b=n.parentElement.getBoundingClientRect();return Math.abs(a.left+a.width/2-b.left-b.width/2)<2;}),'Reduced image is centered.');
      }
      for(const expected of [90,80,70,60,50,40]) await zoom(120,expected);
      await zoom(120,40);
      for(const expected of [50,60,70,80,90,100,110]) await zoom(-120,expected);
      assert(await figure.evaluate(n=>n.scrollWidth>n.clientWidth),'Enlarged image remains accessible by horizontal scrolling.');
      assert(await figure.evaluate(n=>n.getBoundingClientRect().width<=document.querySelector('#translation-scroll').clientWidth),'Zoom does not widen the reading pane.');
      await zoom(120,100);
      await page.screenshot({path:path.join(root,`work/reader-inline-figure-${width}.png`)});
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.evaluate(()=>{document.querySelector('#translation-scroll').scrollTop=0;});
    const pane = page.locator('#translation-scroll');
    await pane.hover(); await page.mouse.wheel(0,80);
    await page.waitForFunction(()=>document.querySelector('#translation-scroll').scrollTop>0);
    await page.waitForTimeout(250);
    const moved=await pane.evaluate(n=>n.scrollTop);
    assert(moved<150,'A small wheel movement scrolls continuously rather than flipping a page.');
    assert.equal(await pane.evaluate(n=>n.scrollLeft),0);
    assert.equal(await page.locator('#reader-reading-footer').count(),0);
    checks.push('translation pane native wheel scroll stays continuous without virtual pages');
    const beforeHeight = await page.locator('#translation-scroll').evaluate(n=>n.clientHeight);
    await page.getByRole('button',{name:'折叠工具栏',exact:true}).click();
    assert(await page.locator('#translation-scroll').evaluate(n=>n.clientHeight)>beforeHeight+70);
    assert.equal(await page.locator('.reader-topbar').isVisible(),false);
    assert.equal(await page.locator('.translation-pane .pane-bar').isVisible(),false);
    await page.screenshot({path:path.join(root,'work/reader-collapsed-toolbar.png')});
    await page.getByRole('button',{name:'展开工具栏',exact:true}).click();
    await page.evaluate(()=>readerSetZoom(40));
    await page.waitForFunction(()=>document.querySelectorAll('.reader-page-section').length===2);
    const rects = await page.locator('.reader-page-section').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,height:r.height};}));
    assert(rects[1].y>=rects[0].y+rects[0].height,'PDF pages always remain in one continuous vertical stream.');
    for(const width of [550,1440]) {
      await page.setViewportSize({width,height:1000});
      assert(await page.locator('.original-pane').isVisible());
      assert(await page.locator('.translation-pane').isVisible());
      assert(await page.locator('#reader-content').evaluate(n=>n.scrollWidth<=n.clientWidth+1),'Comparison fits wide and narrow windows.');
    }
    await page.getByRole('button',{name:'折叠工具栏',exact:true}).click();
    await page.getByRole('button',{name:'展开工具栏',exact:true}).click();
    await page.evaluate(async()=>{readerSetTextZoom(40); readerSetDisplay({collapsed:true}); await flushPreferences(); await saveReadPage();});
    await page.evaluate(()=>{const id=state.reader.id;readerWriteLocal('display',id,{columns:2,collapsed:true});readerWriteLocal('position',id,{position:{...readerCapturePosition(),mode:'translation'},pending:true});});
    await page.reload(); await page.waitForSelector('.paper-card');
    assert.equal(await page.evaluate(()=>document.documentElement.style.getPropertyValue('--reader-image-width')),'40%');
    await page.evaluate(id=>openReader(id),pid);
    assert.equal(await page.locator('#reader-columns, [data-reader-mode], .reader-mode-switch').count(),0);
    assert.equal(await page.evaluate(()=>readerRuntime.chromeCollapsed),true);
    assert.equal(await page.evaluate(()=>state.zoom),40);
    assert(await page.locator('.original-pane').isVisible());
    assert(await page.locator('.translation-pane').isVisible());
    for(const name of ['中英对照','只看原文','只看译文','双列阅读','双页阅读']) assert.equal(await page.getByRole('button',{name,exact:true}).count(),0);
    await page.getByRole('button',{name:'展开工具栏',exact:true}).click();
    checks.push('40% zoom, source page layout, collapsing/restoring all top bars and saved display preferences');
    await page.setViewportSize({width:1440,height:1000});
    await page.evaluate(()=>{
      state.reader.blocks.find(b=>b.id==='five').page=2;
      for(const [id,bbox] of [['one',[.1,.1,.45,.2]],['two',[.1,.3,.45,.4]],['three',[.55,.1,.9,.2]],['four',[.55,.3,.9,.4]]]) state.reader.blocks.find(b=>b.id===id).bbox=bbox;
      state.reader.reading_structure.sections=[{id:'merged',role:'body',label:'Main text',entries:[{id:'merged-across-pages',role:'paragraph',source_ids:state.reader.blocks.map(b=>b.id)}]}];
      state.reader.figures=[1,2].map(page=>({id:`source-fig-${page}`,page,url:state.reader.pages[0].image_url,bbox:[.1,.85,.9,.95]}));
      renderTranslations();
    });
    await page.waitForSelector('.reader-source-page');
    const membership = await page.evaluate(()=>{
      const all=[...document.querySelectorAll('.reader-source-text')];
      return {count:all.length,unique:new Set(all.map(n=>n.dataset.sourceId)).size,
        valid:all.every(n=>Number(n.dataset.page)===Number(state.reader.blocks.find(b=>b.id===n.dataset.sourceId).page)
          && n.closest('.translation-block').dataset.sourcePages.split(' ').includes(n.dataset.page)),
        expected:state.reader.blocks.filter(b=>!b.is_formula||!b.image_url).length};
    });
    assert(membership.valid,'Merged paragraphs retain each source span’s page and navigation anchors.'); assert.equal(membership.count,membership.unique); assert.equal(membership.count,membership.expected);
    assert.equal(await page.locator('.reader-source-page').first().getAttribute('data-columns'),'2');
    assert(await page.locator('.reader-inline-figure').evaluateAll(nodes=>nodes.every(n=>n.dataset.page===n.closest('.reader-source-page').dataset.page)),'Figures stay on their exact source page.');
    const beforePageHeight=await page.locator('.reader-source-page').first().evaluate(n=>n.getBoundingClientRect().height);
    await page.evaluate(()=>{state.reader.blocks[0].translation='较长译文仍然属于原来这一页。'.repeat(1500);state.reader.blocks[0].text_override='A long original paragraph. '.repeat(1500);renderTranslations();});
    const afterPageHeight=await page.locator('.reader-source-page').first().evaluate(n=>n.getBoundingClientRect().height);
    assert(afterPageHeight>beforePageHeight+500,'Extra translation extends its source page vertically.');
    assert.equal(await page.locator('.reader-source-page').count(),2,'Long translation never creates another page.');
    assert.equal(await page.locator('#reader-reading-footer').count(),0);
    await page.evaluate(()=>{state.settings.reader_sync=true;});
    await page.waitForTimeout(300);
    // Exercise actual scroll events in both directions, including a page boundary.
    for(const side of ['translation-scroll','original-scroll']) {
      const samples=await page.evaluate(async side=>{
        const src=document.getElementById(side),dst=document.getElementById(side==='translation-scroll'?'original-scroll':'translation-scroll');
        const selector=side==='translation-scroll'?'.reader-source-page':'.reader-page-section';
        const page2=src.querySelectorAll(selector)[1];
        const boundary=page2.getBoundingClientRect().top-src.getBoundingClientRect().top+src.scrollTop;
        const max=src.scrollHeight-src.clientHeight;
        const positions=[0,40,80,...[boundary-20,boundary,boundary+20].filter(v=>v>80&&v<max),max];
        const results=[];
        for(const position of positions){
          readerRuntime.suppressLeftUntil=0;readerRuntime.suppressRightUntil=0;
          src.scrollTop=position;
          await new Promise(resolve=>setTimeout(resolve,70));
          results.push({source:src.scrollTop,target:dst.scrollTop});
        }
        return {results,maxTarget:dst.scrollHeight-dst.clientHeight};
      },side);
      assert(samples.results[1].target>samples.results[0].target,'Peer moves within the same page.');
      assert(samples.results[2].target>samples.results[1].target,'Every incremental scroll moves its peer.');
      for(let i=1;i<samples.results.length;i++) assert(samples.results[i].target>=samples.results[i-1].target,'Sync is monotonic across page boundaries.');
      assert(Math.abs(samples.results.at(-1).target-samples.maxTarget)<2,'Both panes reach the bottom.');
    }
    await page.evaluate(()=>{state.settings.reader_sync=false;});
    const independent=await page.evaluate(async()=>{
      const left=document.querySelector('#original-scroll'),right=document.querySelector('#translation-scroll'),before=left.scrollTop;
      readerRuntime.suppressRightUntil=0;right.scrollTop-=40;
      await new Promise(resolve=>setTimeout(resolve,80));return left.scrollTop===before;
    });
    assert(independent,'Disabling synchronization leaves the other pane untouched.');
    checks.push('bidirectional continuous page-progress sync, page boundaries, bottom alignment and disabled sync');
    await page.screenshot({path:path.join(root,'work/reader-source-page-layout.png')});
    checks.push('source layout defaults to one translated page per PDF page, keeps each block exactly once, and grows page height without pagination');
    checks.push('Ctrl+wheel scales original images proportionally in both directions, confines overflow and restores saved zoom');
    checks.push('original image loads before its caption, survives translation refresh without duplication, and fits wide/narrow windows');
    await page.evaluate(async()=>{await closeReader();document.querySelectorAll('dialog[open]').forEach(d=>d.close());document.querySelector('#library-view').style.minHeight='3500px';});
    await page.waitForTimeout(150);
    await page.evaluate(()=>window.scrollTo(0,300));
    const libraryBefore=await page.evaluate(()=>({y:scrollY,width:document.body.clientWidth}));
    assert(libraryBefore.y>0,'The background must be scrollable before opening a paper.');
    for(const kind of ['detail','reader']) {
      await page.evaluate(async({kind,pid})=>{if(kind==='detail')await openDetail(pid);else await openReader(pid);},{kind,pid});
      const locked=await page.evaluate(()=>({y:scrollY,width:document.body.clientWidth,overflow:getComputedStyle(document.documentElement).overflowY}));
      assert.equal(locked.overflow,'hidden','The outer scrollbar is hidden for every paper dialog.');
      assert.equal(locked.y,libraryBefore.y);assert.equal(locked.width,libraryBefore.width,'Locking does not shift library columns.');
      await page.mouse.move(1437,450);await page.mouse.wheel(0,500);await page.waitForTimeout(180);
      assert.equal(await page.evaluate(()=>scrollY),locked.y,'Wheeling the background edge cannot move the library.');
      if(kind==='detail') {
        await page.evaluate(()=>{document.querySelector('#detail-scroll').firstElementChild.style.minHeight='2500px';});
        await page.locator('#detail-scroll').hover();await page.mouse.wheel(0,200);
        await page.waitForFunction(()=>document.querySelector('#detail-scroll').scrollTop>0);
        assert.equal(await page.evaluate(()=>scrollY),locked.y,'Dialog content scrolls independently.');
        await page.evaluate(()=>openDialog('#edit-dialog'));
        await page.keyboard.press('Escape');
        await page.waitForFunction(()=>!document.querySelector('#edit-dialog').open);
        assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).overflowY),'hidden','Closing a nested dialog keeps its parent background locked.');
        await page.evaluate(()=>closeDialog('#detail-dialog'));
      } else await page.evaluate(()=>closeReader());
      await page.waitForFunction(()=>!document.querySelector('dialog[open]'));
      await page.waitForTimeout(150);
      assert.notEqual(await page.evaluate(()=>getComputedStyle(document.documentElement).overflowY),'hidden');
      assert.equal(await page.evaluate(()=>scrollY),libraryBefore.y,'Closing restores the original library position.');
    }
    await page.mouse.move(1000,600);await page.mouse.wheel(0,160);
    await page.waitForFunction(y=>scrollY>y,libraryBefore.y);
    checks.push('paper detail and reader hide background scrollbar, lock edge wheel, retain inner scrolling and library position, including nested Escape close');
    const quota={status:'fresh',updated_at:'2026-10-14T07:21:00Z',checked_at:'2026-10-14T07:22:00Z',buckets:[{id:'codex',name:'Codex',windows:[{window_minutes:300,used_percent:20},{window_minutes:10080,used_percent:5,remaining_percent:95,resets_at:1792485420}]}]};
    const requests=[];
    page.on('request',request=>requests.push(request.url()));
    await page.route('**/api/usage?refresh=1',route=>route.fulfill({json:quota}));
    await page.evaluate(quota=>{state.usage=quota;renderQueue();openDialog('#queue-dialog');},quota);
    const queue=page.locator('#queue-dialog');
    assert.equal(await queue.locator('#codex-status, #refresh-status').count(),0);
    assert.equal(await queue.getByRole('button',{name:'刷新',exact:true}).count(),1);
    assert.match(await queue.innerText(),/已用 5%/);assert.match(await queue.innerText(),/剩余 95%/);
    assert(!/账户共享|ChatGPT 订阅|最近检查|5 小时窗口/.test(await queue.innerText()));
    assert(await page.locator('#queue-list').innerText(),'Removing the connection panel does not stop task rendering.');
    await queue.getByRole('button',{name:'刷新',exact:true}).click();
    await page.waitForFunction(()=>!queueRefreshBusy&&!usageBusy);
    assert(requests.some(url=>url.endsWith('/api/usage?refresh=1')));
    assert(requests.some(url=>url.endsWith('/api/status')));
    assert(requests.some(url=>url.includes('/api/papers')),'The only refresh also updates tasks.');
    await page.screenshot({path:path.join(root,'work/queue-weekly-usage.png')});
    await page.evaluate(()=>{state.usage.status='stale';state.usage.error='offline';renderUsagePanels();});
    assert.match(await queue.innerText(),/刷新失败，当前为上次记录/);
    assert.match(await queue.innerText(),/剩余 95%/);
    await page.evaluate(()=>{state.usage.buckets[0].windows=state.usage.buckets[0].windows.filter(w=>w.window_minutes!==10080);state.usage.status='fresh';delete state.usage.error;renderUsagePanels();});
    assert.match(await queue.innerText(),/七天用量暂不可用/);
    assert.equal(await queue.getByRole('progressbar').count(),0);
    assert.match(await page.evaluate(()=>usagePanel(false).textContent),/账户共享 Codex 用量/);
    assert.match(await page.evaluate(()=>usagePanel(false).textContent),/5 小时窗口/);
    checks.push('compact weekly quota, one combined refresh, stale/missing seven-day states, intact task rendering and unchanged settings usage');
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(root,'work/reader-text-browser-verification.json'),JSON.stringify({checks,errors,realModelCalls:0,realLibraryWrites:0,mockedTranslationCalls:(await apiState()).calls.length},null,2));
    console.log(JSON.stringify({checks:checks.length,errors,realModelCalls:0,realLibraryWrites:0}));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
