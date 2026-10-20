"use strict";

const paperChatRuntime = { paperId: null, data: null, pending: new Map(), drafts: new Map(), attachments: new Map(), request: 0,
  mutating: false, width: null, widthLoaded: false, resizeObserver: null };

function paperChatApplyWidth(width = null, persist = false) {
  const pane = $("#reader-paper-chat"), workspace = pane?.parentElement;
  if (!pane || !workspace) return;
  if (!paperChatRuntime.widthLoaded) {
    const saved = readBrowserSetting("chatWidth");
    paperChatRuntime.width = saved === null ? null : Number(saved);
    paperChatRuntime.widthLoaded = true;
  }
  if (getComputedStyle(workspace).flexDirection !== "row") { pane.style.removeProperty("--paper-chat-width"); return; }
  const total = workspace.getBoundingClientRect().width, rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const min = Math.min(19 * rem, total * .5), max = Math.max(min, total - Math.min(26 * rem, total * .45));
  const requested = width ?? paperChatRuntime.width;
  const actual = Math.max(min, Math.min(max, requested ?? pane.getBoundingClientRect().width));
  if (requested !== null) pane.style.setProperty("--paper-chat-width", `${actual}px`);
  const handle = $("#paper-chat-resize");
  if (handle) for (const [name, value] of [["min", min], ["max", max], ["now", actual]]) handle.setAttribute(`aria-value${name}`, String(Math.round(value)));
  if (width !== null) paperChatRuntime.width = actual;
  if (persist && !writeBrowserSetting("chatWidth", String(actual))) toast("对话宽度暂未保存，当前仍可调整", "error");
}

function paperChatResizeHandle() {
  const handle = el("div", { id: "paper-chat-resize", class: "paper-chat-resize", role: "separator", tabindex: "0",
    "aria-orientation": "vertical", "aria-label": "拖动调整论文对话宽度", "aria-controls": "reader-paper-chat" }, el("span", { "aria-hidden": "true" }));
  let dragging = false, startX = 0, startWidth = 0;
  handle.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault(); dragging = true; startX = event.clientX;
    startWidth = $("#reader-paper-chat").getBoundingClientRect().width;
    handle.setPointerCapture(event.pointerId); handle.classList.add("dragging"); handle.focus({ preventScroll: true });
  });
  handle.addEventListener("pointermove", event => { if (dragging) paperChatApplyWidth(startWidth + startX - event.clientX); });
  const finish = event => {
    if (!dragging) return;
    dragging = false; handle.classList.remove("dragging");
    if (handle.hasPointerCapture?.(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    paperChatApplyWidth(paperChatRuntime.width, true);
  };
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) handle.addEventListener(event, finish);
  handle.addEventListener("keydown", event => {
    const width = $("#reader-paper-chat").getBoundingClientRect().width, step = event.shiftKey ? 80 : 24;
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    paperChatApplyWidth(event.key === "Home" ? Number(handle.getAttribute("aria-valuemin")) : event.key === "End" ? Number(handle.getAttribute("aria-valuemax")) : width + (event.key === "ArrowLeft" ? step : -step), true);
  });
  return handle;
}

function paperChatReasoning(text, active = false) {
  return el("details", { class: "paper-chat-reasoning", open: active },
    el("summary", {}, active ? "思考过程" : "查看思考过程"), paperChatMarkdown(text));
}

function paperChatAttachment(remove = null) {
  return el("div", { class: "paper-chat-attachment" },
    el("span", { "aria-hidden": "true" }, "▤"), el("span", {}, "已引用选中文字"),
    remove ? el("button", { type: "button", "aria-label": "移除引用文本", onclick: remove }, "×") : null);
}

function paperChatMessage(message) {
  if (message.kind === "reasoning") return paperChatReasoning(message.text);
  const node = el("div", { class: `paper-chat-message role-${message.role}` },
    message.kind === "summary" ? el("div", { class: "paper-chat-role" }, "研究速览") : null,
    el("div", { class: "paper-chat-text" }, message.role === "user" ? message.text : paperChatMarkdown(message.text)));
  const references = (message.citations || []).filter(c => c.type !== "selection");
  if (message.citations?.some(c => c.type === "selection")) node.append(paperChatAttachment());
  if (references.length) node.append(el("div", { class: "paper-chat-citations" },
    references.map((cite, index) => cite.type === "web" ? paperChatInline(`[网络${references.slice(0, index + 1).filter(c => c.type === "web").length}${cite.open_recorded === false ? "（待核验）" : ""}：${String(cite.title).replace(/[\[\]\n]/g, " ")}](${cite.url})`) : button(cite.type === "pdf" ? `原 PDF 第 ${cite.page} 页` : `第 ${cite.page} 页 · ${cite.block_id}`, () => {
      if (state.reader?.id !== paperChatRuntime.paperId) return;
      setReaderPage(cite.page, true); if (cite.type !== "pdf") selectBlock(cite.block_id, true);
    }, "secondary"))));
  return node;
}

function paperChatPending(pending) {
  const reasoning = paperChatReasoning(pending.reasoning || "", true);
  reasoning.id = "paper-chat-reasoning-live"; reasoning.hidden = !pending.reasoning;
  return el("div", { class: "paper-chat-live" },
    el("div", { class: "paper-chat-status", role: "status", "aria-live": "polite" },
      el("span", { class: "paper-chat-spinner", "aria-hidden": "true", hidden: pending.failed || pending.completed }),
      el("span", { id: "paper-chat-status-label" }, pending.label),
      el("span", { id: "paper-chat-elapsed", class: "paper-chat-elapsed", "aria-hidden": "true" })),
    reasoning, el("div", { id: "paper-chat-answer-live", class: "paper-chat-text", hidden: !pending.answer },
      paperChatMarkdown(pending.answer || "")));
}

function renderPaperChat() {
  const pane = $("#reader-paper-chat"), data = paperChatRuntime.data, id = paperChatRuntime.paperId;
  if (!pane || !data) return;
  const pending = paperChatRuntime.pending.get(id);
  const visiblePending = pending?.generation === data.generation ? pending : null;
  const busy = pending && !pending.failed && !pending.completed;
  const previous = $("#paper-chat-history");
  const scroll = previous?.scrollTop || 0, follow = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight < 60;
  const version = el("select", { "aria-label": "论文对话版本", onchange: event => void loadPaperChat(Number(event.target.value)).catch(error => toast(error.message, "error")) },
    data.generations.map(item => el("option", { value: item.generation }, `对话 ${item.generation} · ${item.model || "模型未记录"}`)));
  version.value = String(data.generation); version.disabled = Boolean(busy || paperChatRuntime.mutating);
  const history = el("div", { class: "paper-chat-history", id: "paper-chat-history", "aria-label": "论文对话消息" }, data.messages.map(paperChatMessage));
  if (visiblePending && !visiblePending.compact) history.append(paperChatMessage({ role: "user", text: visiblePending.question, citations: visiblePending.selectedText ? [{ type: "selection" }] : [] }), paperChatPending(visiblePending));
  if (visiblePending?.compact) history.append(paperChatPending(visiblePending));
  if (!data.messages.length && !visiblePending) history.append(el("p", { class: "paper-chat-empty" }, "围绕论文自由讨论，也可以提问或延伸探索"));
  const input = el("textarea", { id: "paper-chat-input", rows: "3", maxlength: "4000", placeholder: "向论文提问",
    value: paperChatRuntime.drafts.get(id) || "", disabled: busy, "aria-label": "向论文提问" });
  input.addEventListener("input", () => paperChatRuntime.drafts.set(id, input.value));
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void sendPaperChat(); }
  });
  const threadLink = button("在 Codex 中打开 ↗", () => void openPaperChatInCodex(), "secondary", "", { class: "paper-chat-native", id: "paper-chat-open-codex", disabled: Boolean(paperChatRuntime.mutating || (busy && !data.thread_id)), title: "在 Codex 中打开", "aria-label": "在 Codex 中打开" });
  pane.replaceChildren(paperChatResizeHandle(), el("div", { class: "paper-chat-heading" }, el("strong", {}, "论文对话"),
    el("div", { class: "paper-chat-context" }, version),
    button("新建对话", () => void newPaperChat(), "secondary", "", { disabled: Boolean(busy || paperChatRuntime.mutating), id: "paper-chat-new" }), threadLink,
    iconButton("close", "收起论文对话", () => closePaperChat())), history,
    el("div", { class: "paper-chat-compose" },
      paperChatRuntime.attachments.has(id) ? paperChatAttachment(() => {
        paperChatRuntime.attachments.delete(id); renderPaperChat(); $("#paper-chat-input")?.focus();
      }) : null, input,
      el("div", { class: "paper-chat-compose-footer" },
        el("div", { class: "paper-chat-usage-actions" },
          el("small", { id: "paper-chat-usage", title: "按 Codex 最近一次上报更新，不是累计消耗；未上报时不估算。" }, paperChatUsage(data.usage)),
          button("压缩", () => void sendPaperChat(true), "secondary", "", { id: "paper-chat-compact", disabled: Boolean(busy || !data.thread_id), title: "压缩模型上下文，保留聊天记录；可能损失细节" })),
        el("button", { type: "button", class: "button primary paper-chat-send", id: "paper-chat-send",
          disabled: Boolean(busy && (!pending.ready || pending.stopping)), "aria-label": busy ? "停止" : "发送", onclick: () => void (busy ? stopPaperChat() : sendPaperChat()) },
          busy ? "■" : icon("arrow")))));
  history.scrollTop = follow ? history.scrollHeight : scroll;
  paperChatApplyWidth();
  paperChatRuntime.resizeObserver?.disconnect();
  paperChatRuntime.resizeObserver = new ResizeObserver(() => paperChatApplyWidth());
  paperChatRuntime.resizeObserver.observe(pane.parentElement);
  updatePaperChatProgress(id);
}

function updatePaperChatProgress(id) {
  const pending = paperChatRuntime.pending.get(id), data = paperChatRuntime.data;
  if (!pending || paperChatRuntime.paperId !== id || data?.generation !== pending.generation) return;
  const history = $("#paper-chat-history"), follow = history && history.scrollHeight - history.scrollTop - history.clientHeight < 60;
  const label = $("#paper-chat-status-label"), elapsed = $("#paper-chat-elapsed");
  if (label && label.textContent !== pending.label) label.textContent = pending.label;
  if (elapsed) elapsed.textContent = `${pending.stage === "compacting" ? "压缩已用 " : ""}${Math.floor((Date.now() - (pending.stage === "compacting" ? pending.stageStarted || pending.started : pending.started)) / 1000)} 秒`;
  const reasoning = $("#paper-chat-reasoning-live"), answer = $("#paper-chat-answer-live");
  if (reasoning && reasoning.dataset.text !== pending.reasoning) {
    reasoning.dataset.text = pending.reasoning; reasoning.hidden = !pending.reasoning;
    reasoning.lastElementChild.replaceWith(paperChatMarkdown(pending.reasoning));
  }
  if (answer && answer.dataset.text !== pending.answer) {
    answer.dataset.text = pending.answer; answer.hidden = !pending.answer;
    answer.replaceChildren(paperChatMarkdown(pending.answer));
  }
  if (follow) history.scrollTop = history.scrollHeight;
}

async function loadPaperChat(generation = null) {
  const id = state.reader?.id;
  if (!id) return;
  const request = ++paperChatRuntime.request;
  const data = await api(`/api/papers/${encodeURIComponent(id)}/ai${generation ? `?generation=${generation}` : ""}`);
  if (state.reader?.id !== id || request !== paperChatRuntime.request) return;
  paperChatRuntime.paperId = id; paperChatRuntime.data = data;
  const pending = paperChatRuntime.pending.get(id);
  if (pending?.completed && data.generation === pending.generation) paperChatRuntime.pending.delete(id);
  state.reader.paper_ai = data.current;
  const listed = state.papers.find(item => item.id === id);
  if (listed) listed.paper_ai = data.current;
  renderPaperChat();
}

async function openPaperChat() {
  const pane = $("#reader-paper-chat");
  if (!pane || !state.reader) return;
  if (paperChatRuntime.paperId !== state.reader.id) paperChatRuntime.data = null;
  const notes = $("#reader-notes");
  if (notes && !notes.hidden) { notes.hidden = true; $("#reader-notes-toggle")?.setAttribute("aria-expanded", "false"); }
  pane.hidden = false; $("#reader-chat-toggle")?.setAttribute("aria-expanded", "true");
  await loadPaperChat(); $("#paper-chat-input")?.focus({ preventScroll: true });
}

async function paperChatAskSelection(selection) {
  const id = selection?.paperId, text = selection?.text;
  if (!id || state.reader?.id !== id || typeof text !== "string" || !text.trim()) return;
  const pending = paperChatRuntime.pending.get(id);
  if (pending && !pending.failed && !pending.completed) throw Error("请等待当前回答完成后再添加引用。");
  if (text.length > 20000) throw Error("引用文本超过 20000 字，请缩短选区后重试。");
  paperChatRuntime.attachments.set(id, text);
  readerCloseTextPopup(true);
  window.getSelection()?.removeAllRanges();
  await openPaperChat();
  if (state.reader?.id !== id || paperChatRuntime.paperId !== id) return;
  const input = $("#paper-chat-input");
  if (input && !input.disabled) {
    input.focus({ preventScroll: true });
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

function closePaperChat() {
  const pane = $("#reader-paper-chat");
  if (pane) pane.hidden = true;
  $("#reader-chat-toggle")?.setAttribute("aria-expanded", "false");
}

function togglePaperChat() {
  if ($("#reader-paper-chat")?.hidden) void openPaperChat().catch(error => toast(error.message, "error"));
  else closePaperChat();
}

async function openPaperChatInCodex() {
  const id = paperChatRuntime.paperId, generation = paperChatRuntime.data?.generation, target = $("#paper-chat-open-codex");
  if (!id || !generation || target?.disabled) return;
  paperChatRuntime.mutating = true;
  renderPaperChat();
  if (target) { target.disabled = true; target.textContent = "正在打开…"; }
  try {
    await api(`/api/papers/${encodeURIComponent(id)}/ai/open`, { method: "POST", body: { generation } });
    if (paperChatRuntime.paperId === id && state.reader?.id === id) await loadPaperChat(generation);
    toast("已请求 Codex 打开此对话");
  } catch (error) { toast(error.message, "error", 8000); }
  finally { paperChatRuntime.mutating = false; if (paperChatRuntime.paperId === id) renderPaperChat(); }
}

async function sendPaperChat(compact = false) {
  const input = $("#paper-chat-input"), id = state.reader?.id, data = paperChatRuntime.data;
  const question = input?.value.trim(), existing = paperChatRuntime.pending.get(id);
  if (!id || (!compact && !question) || !data || paperChatRuntime.mutating || (existing && !existing.failed && !existing.completed)) return;
  const selectedText = paperChatRuntime.attachments.get(id) || "";
  const pending = { compact, requestId: crypto.randomUUID(), question: compact ? "" : question, selectedText, generation: data.generation, label: "正在发送问题", started: Date.now(), reasoning: "", answer: "", completed: false, failed: false };
  paperChatRuntime.pending.set(id, pending); if (!compact) { paperChatRuntime.drafts.delete(id); paperChatRuntime.attachments.delete(id); }
  renderPaperChat();
  const timer = setInterval(() => updatePaperChatProgress(id), 1000);
  try {
    await apiStream(`/api/papers/${encodeURIComponent(id)}/ai/${compact ? "compact" : "chat-stream"}`, { question, selected_text: compact ? "" : selectedText, request_id: pending.requestId, generation: pending.generation }, event => {
      if (event.type === "operation") { pending.ready = true; renderPaperChat(); }
      else if (event.type === "usage" && paperChatRuntime.paperId === id && paperChatRuntime.data?.generation === pending.generation) {
        paperChatRuntime.data.usage = event.usage;
        const usage = $("#paper-chat-usage"); if (usage) usage.textContent = paperChatUsage(event.usage);
      }
      else if (event.type === "status") { if (event.stage === "compacting" && pending.stage !== "compacting") pending.stageStarted = Date.now(); pending.stage = event.stage; pending.label = event.label; }
      else if (event.type === "reasoning") { pending.reasoning = event.text; pending.label = "正在思考"; }
      else if (event.type === "answer") { pending.answer = event.text; pending.label = "正在生成回答"; }
      else if (event.type === "thread" && paperChatRuntime.paperId === id && paperChatRuntime.data?.generation === pending.generation) {
        paperChatRuntime.data.thread_id = event.thread_id; renderPaperChat();
      } else if (event.type === "completed") { pending.completed = true; pending.answer = event.answer; pending.label = event.stopped ? "已停止" : compact ? "上下文已压缩" : "回答已完成"; if (event.unsaved && !compact) { paperChatRuntime.drafts.set(id, question); if (selectedText) paperChatRuntime.attachments.set(id, selectedText); } }
      updatePaperChatProgress(id);
    });
    if (state.reader?.id === id) { await loadPaperChat(paperChatRuntime.data?.generation); renderLibrary(); }
  } catch (error) {
    if (!pending.completed) {
      pending.failed = true; pending.label = `回答未完成：${error.message}`;
      if (!compact) paperChatRuntime.drafts.set(id, question);
      if (!compact && selectedText) paperChatRuntime.attachments.set(id, selectedText);
    }
    toast(`论文对话：${error.message}`, "error", 8000);
  } finally {
    clearInterval(timer);
    if (state.reader?.id === id && paperChatRuntime.paperId === id) renderPaperChat();
  }
}


function paperChatUsage(usage) {
  const used = usage?.last?.totalTokens, limit = usage?.modelContextWindow;
  if (!Number.isFinite(used)) return "上下文：待上报";
  const count = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
  return Number.isFinite(limit) && limit > 0
    ? `上下文 ${Math.min(100, Math.round(used / limit * 100))}% · ${count.format(used)}/${count.format(limit)}`
    : `上下文 ${count.format(used)} tokens · 上限未知`;
}

async function stopPaperChat() {
  const id = paperChatRuntime.paperId, pending = paperChatRuntime.pending.get(id);
  if (!pending?.ready || pending.stopping || pending.completed || pending.failed) return;
  pending.stopping = true; pending.label = "正在停止…"; renderPaperChat();
  try { await api(`/api/papers/${encodeURIComponent(id)}/ai/stop`, { method: "POST", body: { request_id: pending.requestId } }); }
  catch (error) { pending.stopping = false; toast(error.message, "error"); renderPaperChat(); }
}

async function newPaperChat() {
  const id = paperChatRuntime.paperId, pending = paperChatRuntime.pending.get(id);
  if (!id || paperChatRuntime.mutating || (pending && !pending.completed && !pending.failed)) return;
  paperChatRuntime.mutating = true; renderPaperChat();
  try {
    await api(`/api/papers/${encodeURIComponent(id)}/ai/new`, { method: "POST", body: {} });
    if (state.reader?.id === id) {
      paperChatRuntime.pending.delete(id); paperChatRuntime.drafts.delete(id); paperChatRuntime.attachments.delete(id);
      await loadPaperChat(); $("#paper-chat-input")?.focus({ preventScroll: true });
    }
  } catch (error) { toast(error.message, "error"); }
  finally { paperChatRuntime.mutating = false; if (paperChatRuntime.paperId === id) renderPaperChat(); }
}
