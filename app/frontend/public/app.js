// Relay web client: sends the conversation to /api/chat and renders the
// streamed Server-Sent Events. No framework, no build step.
"use strict";

const $ = (id) => document.getElementById(id);
const els = {
  transcript: $("transcript"), empty: $("empty"), form: $("composer"), prompt: $("prompt"),
  send: $("send"), stop: $("stop"), status: $("status"), statusText: $("status-text"),
  env: $("env"), chain: $("model-chain"), version: $("version"), newChat: $("new-chat"),
  answers: $("u-answers"), tokens: $("u-tokens"), cost: $("u-cost"), ttft: $("u-ttft"),
};

const state = { messages: [], busy: false, controller: null, totals: { answers: 0, tokens: 0, cost: 0, ttft: [] } };

// ---------- markdown (small, safe: escapes HTML first) ----------
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function inline(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(src) {
  const out = [];
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```\s*([\w+-]*)/);
    if (fence) {
      const code = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++; // closing fence (may be missing while streaming)
      out.push(`<div class="codeblock"><button type="button" class="copy">Copy</button><pre><code${fence[1] ? ` data-lang="${escapeHtml(fence[1])}"` : ""}>${escapeHtml(code.join("\n"))}</code></pre></div>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)/);
    if (heading) { out.push(`<h${heading[1].length < 3 ? 3 : 4}>${inline(heading[2])}</h${heading[1].length < 3 ? 3 : 4}>`); i++; continue; }
    const listMatch = line.match(/^\s*([-*]|\d+\.)\s+/);
    if (listMatch) {
      const ordered = /\d/.test(listMatch[1]);
      const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""))}</li>`);
        i++;
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*]|\d+\.)\s+)/.test(lines[i])) para.push(lines[i++]);
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("");
}

// ---------- formatting ----------
const fmtInt = (n) => n.toLocaleString("en-GB");
const fmtCost = (usd) => (usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(3)}`);
const fmtSecs = (ms) => (ms == null ? "–" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

// ---------- rendering ----------
function addTurn(tplId) {
  els.empty.hidden = true;
  const node = $(tplId).content.firstElementChild.cloneNode(true);
  els.transcript.append(node);
  return node;
}

function scrollToEnd() {
  const t = els.transcript;
  if (t.scrollHeight - t.scrollTop - t.clientHeight < 160) t.scrollTop = t.scrollHeight;
}

function renderTelemetry(dl, d) {
  const cells = [
    ["Model", d.model],
    ["Tokens in / out", `${fmtInt(d.input_tokens)} / ${fmtInt(d.output_tokens)}`],
    ["Cost", fmtCost(d.cost_usd)],
    ["First token", fmtSecs(d.ttft_ms)],
    ["Total time", fmtSecs(d.latency_ms)],
  ];
  if (d.fallback_used) cells.push(["Fallback", "Used", "is-fallback"]);
  dl.replaceChildren(...cells.map(([k, v, cls]) => {
    const div = document.createElement("div");
    if (cls) div.className = cls;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    div.append(dt, dd);
    return div;
  }));
  dl.hidden = false;
}

function renderNotice(turn, kind, title, message, retry) {
  const box = document.createElement("div");
  box.className = `notice notice-${kind}`;
  box.innerHTML = `<div><strong></strong><p></p></div>`;
  box.querySelector("strong").textContent = title;
  box.querySelector("p").textContent = message;
  if (retry) {
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "btn btn-ghost"; btn.textContent = "Try again";
    btn.addEventListener("click", () => { turn.remove(); state.messages.pop(); retry(); });
    box.append(btn);
  }
  turn.append(box);
}

function updateTotals(d) {
  const t = state.totals;
  t.answers += 1; t.tokens += d.input_tokens + d.output_tokens; t.cost += d.cost_usd;
  if (d.ttft_ms != null) t.ttft.push(d.ttft_ms);
  els.answers.textContent = fmtInt(t.answers);
  els.tokens.textContent = fmtInt(t.tokens);
  els.cost.textContent = `$${t.cost.toFixed(4)}`;
  els.ttft.textContent = t.ttft.length ? fmtSecs(Math.round(t.ttft.reduce((a, b) => a + b, 0) / t.ttft.length)) : "–";
}

function setBusy(busy) {
  state.busy = busy;
  els.send.disabled = busy;
  els.stop.hidden = !busy;
  els.prompt.disabled = busy;
  if (!busy) els.prompt.focus();
}

// ---------- streaming ----------
async function* readSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message", data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

async function ask() {
  const turn = addTurn("tpl-answer");
  const body = turn.querySelector(".turn-body");
  turn.classList.add("streaming");
  body.innerHTML = "<p></p>";
  let text = "";
  setBusy(true);
  state.controller = new AbortController();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: state.messages }),
      signal: state.controller.signal,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      const msg = typeof detail.detail === "string" ? detail.detail : `The server returned ${res.status}.`;
      body.remove();
      if (res.status === 429) renderNotice(turn, "warn", "Slow down", `${msg} Try again in ${res.headers.get("Retry-After") || "a few"} seconds.`, ask);
      else renderNotice(turn, "error", "Request failed", msg, ask);
      return;
    }
    for await (const { event, data } of readSSE(res)) {
      if (event === "delta") {
        text += data.text;
        body.innerHTML = renderMarkdown(text) || "<p></p>";
        scrollToEnd();
      } else if (event === "done") {
        state.messages.push({ role: "assistant", content: text });
        renderTelemetry(turn.querySelector(".telemetry"), data);
        updateTotals(data);
      } else if (event === "error") {
        if (!text) body.remove();
        renderNotice(turn, "error", "No answer", data.message, ask);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      if (text) state.messages.push({ role: "assistant", content: text });
      renderNotice(turn, "warn", "Stopped", "You stopped this answer before it finished.");
    } else {
      body.remove();
      renderNotice(turn, "error", "Can't reach Relay", "Check that the backend is running, then try again.", ask);
    }
  } finally {
    turn.classList.remove("streaming");
    state.controller = null;
    setBusy(false);
    scrollToEnd();
  }
}

function submit(text) {
  const content = text.trim();
  if (!content || state.busy) return;
  // A stopped or failed answer leaves the last message from the user: drop it so roles alternate.
  if (state.messages.length && state.messages.at(-1).role === "user") state.messages.pop();
  state.messages.push({ role: "user", content });
  addTurn("tpl-user").querySelector(".turn-body").textContent = content;
  els.prompt.value = "";
  autosize();
  els.transcript.scrollTop = els.transcript.scrollHeight;
  ask();
}

// ---------- status ----------
async function loadInfo() {
  try {
    const res = await fetch("/api/info");
    if (!res.ok) throw new Error(res.status);
    const info = await res.json();
    els.status.dataset.state = info.mock_llm ? "mock" : "live";
    els.statusText.textContent = info.mock_llm ? "Mock mode, answers are canned" : `Live on ${info.models[0]}`;
    els.chain.replaceChildren(...info.models.map((m) => Object.assign(document.createElement("li"), { textContent: m })));
    els.env.textContent = `${info.environment} environment, up to ${info.max_tokens} tokens per answer`;
    els.version.textContent = `Version ${info.version}`;
  } catch {
    els.status.dataset.state = "down";
    els.statusText.textContent = "Backend unreachable";
    els.chain.replaceChildren(Object.assign(document.createElement("li"), { textContent: "Unavailable", className: "muted" }));
  }
}

// ---------- events ----------
function autosize() {
  els.prompt.style.height = "auto";
  els.prompt.style.height = `${Math.min(els.prompt.scrollHeight, 220)}px`;
}

els.form.addEventListener("submit", (e) => { e.preventDefault(); submit(els.prompt.value); });
els.prompt.addEventListener("input", autosize);
els.prompt.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(els.prompt.value); }
});
els.stop.addEventListener("click", () => state.controller?.abort());
els.newChat.addEventListener("click", () => {
  state.controller?.abort();
  state.messages = [];
  els.transcript.querySelectorAll(".turn").forEach((n) => n.remove());
  els.empty.hidden = false;
  els.prompt.focus();
});
$("starters").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (btn) submit(btn.textContent);
});
els.transcript.addEventListener("click", async (e) => {
  const btn = e.target.closest(".copy");
  if (!btn) return;
  await navigator.clipboard.writeText(btn.nextElementSibling.textContent);
  btn.textContent = "Copied";
  setTimeout(() => { btn.textContent = "Copy"; }, 1500);
});

loadInfo();
els.prompt.focus();
