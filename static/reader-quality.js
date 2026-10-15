"use strict";

async function openReaderQuality(section) {
  const pid = state.reader?.id;
  if (!pid) return;
  let paper = normalizeDetail(await api(`/api/papers/${pid}`));
  let ledger = paper.terminology || { revision: 0, entries: [] }, dirty = false, busy = false;
  if ($("#reader-quality-dialog")) return;
  const dialog = el("dialog", { id: "reader-quality-dialog", class: "form-dialog reader-quality-dialog" });
  const content = el("div", { class: "reader-quality-body" });
  const status = el("p", { class: "small muted", role: "status" });
  const actions = el("div", { class: "form-actions" });
  const close = () => {
    if (busy) { toast("请等待当前操作完成", "warn"); return; }
    if (dirty) { status.textContent = "有未保存修改，请完成校对或放弃修改并关闭。"; return; }
    dialog.close();
  };
  dialog.append(el("div", { class: "dialog-heading" }, el("h2", {}, section === "terminology" ? "全文术语表" : "校对图表与公式"), iconButton("close", "关闭", close)), status, content, actions);
  dialog.addEventListener("cancel", e => { e.preventDefault(); close(); });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  document.body.append(dialog);
  openDialog("#reader-quality-dialog");
  const request = async (action, extra = {}) => api(`/api/papers/${pid}/${section}`, { method: "POST", body: { action, ...extra } });
  const run = async fn => {
    if (busy) return;
    busy = true; dialog.setAttribute("aria-busy", "true");
    const controls = [...dialog.querySelectorAll("button,input,textarea,select")];
    controls.forEach(n => n.disabled = true);
    try { await fn(); } catch (error) { status.textContent = error.message || "操作失败，请重试。"; toast(status.textContent, "error"); }
    finally { busy = false; dialog.removeAttribute("aria-busy"); controls.forEach(n => n.disabled = false); }
  };
  function renderLedger() {
    content.replaceChildren();
    if (!ledger.entries.length) content.append(el("p", {}, "尚无术语。全文翻译前会自动建立，也可在这里建立或手动添加。"));
    ledger.entries.forEach((entry, index) => {
      const row = el("section", { class: "quality-entry" });
      for (const [key, label] of [["source", "英文术语"], ["target", "中文译名"], ["abbreviation", "缩写"], ["definition", "本文含义"]]) {
        const input = el("input", { value: entry[key] || "", "aria-label": label, maxlength: key === "definition" ? 600 : key === "abbreviation" ? 100 : 200,
          oninput: e => { entry[key] = e.target.value; dirty = true; } });
        row.append(field(label, input));
      }
      const uncertain = el("input", { type: "checkbox", checked: entry.uncertain, onchange: e => { entry.uncertain = e.target.checked; dirty = true; } });
      row.append(el("label", {}, uncertain, "术语待核对（不用于约束翻译）"));
      if (entry.source_note) row.append(el("p", { class: "small muted" }, entry.source_note));
      if (entry.block_id) row.append(button(`来源：第 ${entry.page} 页`, () => { if (dirty) { status.textContent = "请先保存术语，再定位来源。"; return; } dialog.close(); if (state.reader?.id === pid) selectBlock(entry.block_id, true); }));
      row.append(button("删除此术语", () => { ledger.entries.splice(index, 1); dirty = true; renderLedger(); }));
      content.append(row);
    });
    actions.replaceChildren(
      button("自动建立／补充", () => run(async () => { if (dirty) throw new Error("请先保存修改。"); status.textContent = "正在扫描全文并建立术语表…"; ledger = (await request("build")).terminology; renderLedger(); status.textContent = `已保存 ${ledger.entries.length} 个术语`; })),
      button("添加术语", () => { ledger.entries.push({ source: "", target: "", abbreviation: "", definition: "", block_id: "", uncertain: false }); dirty = true; renderLedger(); content.lastElementChild?.scrollIntoView({ block: "nearest" }); }),
      button("保存术语表", () => run(async () => { ledger = (await request("save", { revision: ledger.revision, entries: ledger.entries })).terminology; dirty = false; renderLedger(); status.textContent = "已保存；已有译文需通过检查后选择修正。"; }), "primary"),
      button("检查已有译文", () => run(async () => { if (dirty) throw new Error("请先保存术语表。"); status.textContent = "正在检查译名；只生成修改建议…"; renderPreview((await request("preview")).preview); })),
      button("放弃修改并关闭", () => { dirty = false; close(); }));
  }
  function renderPreview(preview) {
    content.replaceChildren();
    const selected = new Set();
    status.textContent = `发现 ${preview.items.length} 段建议修订，请逐项核对。`;
    for (const item of preview.items) {
      const checkbox = el("input", { type: "checkbox", onchange: e => { if (e.target.checked) selected.add(item.id); else selected.delete(item.id); } });
      const row = el("section", { class: "quality-entry" }, el("label", {}, checkbox, `第 ${item.page} 页 · 应用此项`, item.manual ? "（含手工修订）" : ""));
      row.append(el("p", { class: "small muted" }, item.source), el("p", {}, "修改前：", item.before), el("p", {}, "修改后：", item.after));
      content.append(row);
    }
    actions.replaceChildren(button("应用选中项", () => run(async () => {
      if (!selected.size) throw new Error("请先勾选要应用的项。");
      const result = await request("apply", { preview_id: preview.id, ids: [...selected] });
      updatePaper(result.paper);
      if (state.reader?.id === pid) await refreshReader();
      status.textContent = "已应用所选修改。"; content.replaceChildren(); actions.replaceChildren(button("关闭", close));
    }), "primary"), button("返回", () => { status.textContent = ""; renderLedger(); }));
  }
  function renderFormulaStart() {
    const additions = [], removals = new Set();
    let selection = null;
    const kind = el("select", { "aria-label": "区域类型" }, [["formula", "公式"], ["table", "表格"], ["figure", "插图"], ["algorithm", "算法"]].map(([value, text]) => el("option", { value }, text)));
    const existing = [...paper.blocks.filter(b => !b.formula_continuation && (b.is_formula || b.is_table || b.source_region_kind)), ...(paper.figures || [])];
    const regionName = b => b.is_formula ? "公式" : b.is_table ? "表格" : b.source_region_kind === "algorithm" ? "算法" : "插图";
    const views = paper.pages.map(p => {
      const img = el("img", { src: p.image_url, draggable: false, loading: "lazy", width: p.width, height: p.height, alt: `第 ${p.number} 页：拖动框选遗漏区域，点击已有区域取消识别` });
      const rectangle = el("div", { class: "quality-crop-selection", hidden: true });
      const canvas = el("div", { class: "quality-crop-canvas", dataset: { page: p.number } }, img, rectangle);
      const list = el("div", {class: "quality-page-actions"});
      const view = {number: Number(p.number), img, rectangle, canvas, list,
        node: el("section", {class: "quality-pdf-page", "aria-label": `原 PDF 第 ${p.number} 页`}, el("div", {class: "quality-page-label"}, `第 ${p.number} 页`), canvas, list)};
      let start = null;
      const point = e => { const r = img.getBoundingClientRect(); return [Math.max(0, Math.min(1, (e.clientX-r.left)/r.width)), Math.max(0, Math.min(1, (e.clientY-r.top)/r.height))]; };
      canvas.addEventListener("pointerdown", e => {
        if (busy || !img.naturalWidth || e.button !== 0) return;
        e.preventDefault(); views.forEach(v => v.rectangle.hidden = true);
        selection = null; start = point(e); canvas.setPointerCapture(e.pointerId);
      });
      const updateSelection = e => {
        if (!start) return;
        const end = point(e), box = [Math.min(start[0],end[0]), Math.min(start[1],end[1]), Math.max(start[0],end[0]), Math.max(start[1],end[1])];
        selection = {page: view.number, bbox: box};
        rectangle.hidden = false; position(rectangle, box);
      };
      canvas.addEventListener("pointermove", updateSelection);
      canvas.addEventListener("pointerup", e => { updateSelection(e); start = null; });
      canvas.addEventListener("pointercancel", () => { start = null; selection = null; rectangle.hidden = true; });
      return view;
    });
    function position(node, box) {
      Object.assign(node.style, {left: `${box[0]*100}%`, top: `${box[1]*100}%`, width: `${(box[2]-box[0])*100}%`, height: `${(box[3]-box[1])*100}%`});
    }
    function draw() {
      for (const {number, canvas, list} of views) {
        canvas.querySelectorAll(".quality-existing-region").forEach(n => n.remove());
        list.replaceChildren();
        existing.filter(b => Number(b.page) === number).forEach(b => {
          const toggle = () => { if (busy) return; if (removals.has(b.id)) removals.delete(b.id); else removals.add(b.id); dirty = Boolean(additions.length || removals.size); draw(); };
          const label = `${removals.has(b.id) ? "恢复识别" : "取消识别"}：${regionName(b)}`;
          list.append(button(label, toggle, "secondary"));
          if (!b.bbox || removals.has(b.id)) return;
          const overlay = button("", toggle, "quality-existing-region", "", { "aria-label": label });
          overlay.addEventListener("pointerdown", e => e.stopPropagation());
          position(overlay, b.bbox); canvas.append(overlay);
        });
        additions.forEach((a, index) => {
          if (a.page !== number) return;
          list.append(button(`撤回新增：第 ${a.page} 页 · ${({formula: "公式", table: "表格", figure: "插图", algorithm: "算法"})[a.kind]}`, () => { if (busy) return; additions.splice(index, 1); dirty = Boolean(additions.length || removals.size); draw(); }, "secondary"));
          const overlay = el("div", {class: "quality-existing-region"});
          overlay.style.pointerEvents = "none"; position(overlay, a.bbox); canvas.append(overlay);
        });
      }
    }
    status.textContent = "滚轮连续浏览整篇 PDF；点击绿色区域取消误识别，拖动框选遗漏区域后添加。完成后重新排版并翻译新增图表与算法；公式保留原图。";
    content.replaceChildren(...views.map(v => v.node));
    const captionList = el("details", {class: "quality-caption-ledger", open: true}, el("summary", {}, "按图号核对 caption（从 Figure 1 开始）"));
    const captionStates = {translated: "已识别 · 已翻译", pending: "已识别 · 待翻译／重试", missing: "未识别到图注", ambiguous: "图号重复 · 待核对"};
    if (!(paper.figure_captions || []).length) captionList.append(el("p", {}, "尚无图号记录。点击完成校对并翻译，将扫描整篇 PDF 的 Figure 图注。"));
    for (const entry of [...(paper.figure_captions || [])].sort((a,b) => a.number-b.number)) {
      const row = el("details", {}, el("summary", {}, `Figure ${entry.number}：${captionStates[entry.status] || "待核对"}${entry.page ? ` · 第 ${entry.page} 页` : ""}`));
      if (entry.text) row.append(el("p", {}, entry.text));
      if (entry.translation) row.append(el("p", {}, entry.translation));
      captionList.append(row);
    }
    content.prepend(captionList);
    actions.replaceChildren(kind, button("添加框选区域", () => run(async () => {
      if (!selection) throw new Error("请先在原文页上拖动框选。");
      const addition = {...selection, kind: kind.value};
      const box = addition.bbox;
      if (box[2] - box[0] < .004 || box[3] - box[1] < .004) throw new Error("框选区域过小，请拖动框选完整区域。");
      if (additions.some(a => a.page === addition.page && Math.min(a.bbox[2], box[2]) > Math.max(a.bbox[0], box[0]) && Math.min(a.bbox[3], box[3]) > Math.max(a.bbox[1], box[1]))) throw new Error("与已添加区域重叠，请调整框选或先撤回旧区域。");
      additions.push(addition); dirty = true; selection = null; views.forEach(v => v.rectangle.hidden = true); draw();
      status.textContent = `已添加 ${additions.length} 处；完成校对后统一保存。`;
    }), "secondary"), button("完成校对并翻译", () => run(async () => {
      if (dirty) {
        status.textContent = "正在保存校对并重新排版…";
        const saved = await request("correct", {revision: paper.region_revision, additions, removals: [...removals]});
        paper = normalizeDetail(saved.paper); updatePaper(saved.paper); dirty = false;
        additions.length = 0; removals.clear();
        if (state.reader?.id === pid) await refreshReader();
      }
      status.textContent = "正在按 Figure 图号扫描并补译全文 caption，随后补译图表与算法…";
      const result = await request("translate-missing");
      paper = normalizeDetail(result.paper); updatePaper(result.paper);
      if (state.reader?.id === pid) await refreshReader();
      renderFormulaStart();
      status.textContent = result.errors?.length ? `校对已保存，部分图号未识别或翻译未完成，请查看图号记录，可点击完成按钮重试：${result.errors.join("；")}` : `校对完成，已核对 ${paper.figure_captions?.length || 0} 个图号；caption 在阅读区直接显示，图内译文展开查看。`;
    }), "primary"), button("放弃修改并关闭", () => { dirty = false; close(); }));
    draw();
  }
  if (section === "terminology") renderLedger(); else renderFormulaStart();
}
