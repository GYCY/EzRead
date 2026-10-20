"use strict";

const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = name => fs.readFileSync(path.join(__dirname, "../static", name), "utf8");

async function streamTests() {
  const encoder = new TextEncoder();
  function fixture(raw, contentType = "application/x-ndjson") {
    const bytes = encoder.encode(raw);
    const stream = new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); } });
    const context = vm.createContext({ TextDecoder, asText: String, fetch: async () => ({ ok: true, body: stream, headers: { get: () => contentType } }) });
    vm.runInContext(source("core/api.js"), context);
    return context;
  }
  const events = [];
  await fixture('{"type":"reasoning","text":"中文思考"}\n{"type":"answer","text":"回答"}\n{"type":"completed","answer":"回答"}').apiStream("/test", {}, e => events.push(e));
  assert.equal(events[0].text, "中文思考", "UTF-8 and NDJSON boundaries may split at any byte");
  assert.deepEqual(events.map(e => e.type), ["reasoning", "answer", "completed"]);
  await assert.rejects(fixture('{"type":"answer","text":"partial"}\n').apiStream("/test", {}, () => {}), /中断/);
  await assert.rejects(fixture('{"type":"error","error":"引用无效"}\n').apiStream("/test", {}, () => {}), /引用无效/);
  await assert.rejects(fixture("{}", "application/json").apiStream("/test", {}, () => {}), /版本不匹配/);
}

async function pendingTests() {
  const input = { value: "实验结果？" }, calls = [], toasts = [], timers = new Map();
  let resolve, reject, onEvent;
  const context = vm.createContext({
    state: { reader: { id: "paper-a" }, papers: [] },
    $: selector => selector === "#paper-chat-input" ? input : null,
    setInterval: fn => { timers.set(1, fn); return 1; }, clearInterval: id => timers.delete(id),
    crypto: require("node:crypto").webcrypto, Date, toast: text => toasts.push(text), renderLibrary() {},
    apiStream: (url, data, callback) => { calls.push({ url, data }); onEvent = callback; return new Promise((ok, bad) => { resolve = ok; reject = bad; }); },
  });
  vm.runInContext(source("paper-chat.js"), context);
  vm.runInContext(`paperChatRuntime.paperId='paper-a'; paperChatRuntime.data={generation:1,current:{generation:1}};
    renderPaperChat=()=>{}; updatePaperChatProgress=()=>{};
    loadPaperChat=async()=>{throw Error('history refresh failed')};`, context);
  vm.runInContext("paperChatRuntime.attachments.set('paper-a', '引用正文')", context);
  const run = context.sendPaperChat();
  assert.equal(calls[0].data.selected_text, "引用正文");
  assert.equal(calls.length, 1);
  assert.equal("web_search" in calls[0].data, false);
  const snapshot = () => vm.runInContext("paperChatRuntime.pending.get('paper-a')", context);
  assert.equal(snapshot().question, "实验结果？", "Question becomes visible before a network response");
  await context.sendPaperChat(); assert.equal(calls.length, 1, "Duplicate sends are suppressed");
  onEvent({ type: "reasoning", text: "核对原文" });
  onEvent({ type: "answer", text: "partial" });
  context.state.reader = { id: "paper-b" };
  reject(Error("network disconnected")); await run;
  assert.equal(vm.runInContext("paperChatRuntime.attachments.get('paper-a')", context), "引用正文");
  assert.equal(snapshot().failed, true); assert.equal(snapshot().answer, "partial");
  assert.equal(vm.runInContext("paperChatRuntime.drafts.get('paper-a')", context), "实验结果？");
  assert.equal(timers.size, 0);
  context.state.reader = { id: "paper-a" };
  const retry = context.sendPaperChat();
  assert.equal("web_search" in calls[1].data, false);
  onEvent({ type: "completed", answer: "saved answer" }); resolve(); await retry;
  assert.equal(snapshot().completed, true); assert.equal(snapshot().failed, false, "Saved success survives a history refresh error");
  assert.equal(vm.runInContext("paperChatRuntime.drafts.has('paper-a')", context), false, "Do not invite a duplicate retry of an already saved answer");
  assert.equal(timers.size, 0);
  assert.equal(toasts.length, 2);
}

async function selectionTests() {
  let opened = 0;
  const context = vm.createContext({ state: { reader: { id: "a" } }, $: () => null,
    readerCloseTextPopup() {}, window: { getSelection: () => ({ removeAllRanges() {} }) } });
  vm.runInContext(source("paper-chat.js"), context);
  context.openPaperChat = async () => { opened++; };
  await context.paperChatAskSelection({ paperId: "a", text: "First 😀\nsecond" });
  assert.equal(vm.runInContext("paperChatRuntime.attachments.get('a')", context), "First 😀\nsecond");
  await context.paperChatAskSelection({ paperId: "a", text: "More" });
  assert.equal(vm.runInContext("paperChatRuntime.attachments.get('a')", context), "More");
  await context.paperChatAskSelection({ paperId: "b", text: "Wrong paper" });
  assert.equal(opened, 2);
  await assert.rejects(context.paperChatAskSelection({ paperId: "a", text: "x".repeat(20001) }), /20000/);
  assert.equal(opened, 2);
  assert.equal(vm.runInContext("paperChatRuntime.attachments.get('a')", context), "More");
}

(async () => { await streamTests(); await pendingTests(); await selectionTests(); console.log("Paper chat: streaming UTF-8, terminal status, duplicate sends, failure recovery and saved-answer refresh passed."); })()
  .catch(error => { console.error(error); process.exitCode = 1; });
