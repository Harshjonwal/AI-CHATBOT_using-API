"use strict";

const $ = (sel, root = document) => root.querySelector(sel);

const els = {
  sidebar: $("#sidebar"),
  scrim: $("#scrim"),
  list: $("#conv-list"),
  newChat: $("#new-chat"),
  modelInfo: $("#model-info"),
  menuBtn: $("#menu-btn"),
  title: $("#chat-title"),
  usage: $("#usage"),
  exportBtn: $("#export-btn"),
  banner: $("#banner"),
  messages: $("#messages"),
  composer: $("#composer"),
  input: $("#input"),
  sendBtn: $("#send-btn"),
  effort: $("#effort"),
};

const state = {
  config: null,
  conversations: [],
  current: null, // the open conversation, or null for a new chat not yet saved
  streaming: null, // { controller } while a reply is streaming
};

const ICONS = {
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
  pencil: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>',
  globe: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/></svg>',
  persona: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
};

// ---------- Helpers ----------

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(await errorDetail(res));
  return res.status === 204 ? null : res.json();
}

async function errorDetail(res) {
  try {
    const { detail } = await res.json();
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  } catch {}
  return `${res.status} ${res.statusText}`;
}

const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
const totalIn = (u) => u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens;

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const hasMarkdown = typeof marked !== "undefined" && typeof DOMPurify !== "undefined";
if (hasMarkdown) {
  marked.setOptions({ gfm: true });
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}
if (typeof hljs !== "undefined") hljs.configure({ ignoreUnescapedHTML: true });

// Model output is untrusted: always sanitize the HTML that Markdown produces.
function renderMarkdown(el, text, final) {
  el.innerHTML = hasMarkdown
    ? DOMPurify.sanitize(marked.parse(text))
    : `<p style="white-space:pre-wrap">${escapeHtml(text)}</p>`;
  if (final) decorateCode(el); // highlighting on every streamed token would be wasted work
}

function decorateCode(root) {
  root.querySelectorAll("pre > code").forEach((code) => {
    if (typeof hljs !== "undefined") {
      try { hljs.highlightElement(code); } catch {}
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copy-code";
    btn.textContent = "Copy";
    btn.addEventListener("click", () => copyText(code.innerText, btn));
    code.parentElement.appendChild(btn);
  });
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    return;
  }
  const isIcon = btn.classList.contains("icon-btn");
  const old = btn.innerHTML;
  btn.innerHTML = isIcon ? ICONS.check : "Copied";
  setTimeout(() => (btn.innerHTML = old), 1500);
}

function showBanner(html) {
  els.banner.innerHTML = html;
  els.banner.hidden = false;
}
function hideBanner() {
  els.banner.hidden = true;
}

function isNearBottom() {
  const m = els.messages;
  return m.scrollHeight - m.scrollTop - m.clientHeight < 140;
}
function scrollToBottom() {
  els.messages.scrollTop = els.messages.scrollHeight;
}

function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 220)}px`;
}

// ---------- Sidebar ----------

function groupLabel(iso) {
  const d = new Date(iso);
  const now = new Date();
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff <= 0) return "Today";
  if (diff === 1) return "Yesterday";
  if (diff < 7) return "Previous 7 days";
  return "Older";
}

function renderList() {
  els.list.innerHTML = "";
  if (!state.conversations.length) {
    els.list.innerHTML = '<div class="conv-empty">No chats yet. Your conversations will appear here.</div>';
    return;
  }
  let lastGroup = null;
  for (const conv of state.conversations) {
    const group = groupLabel(conv.updated_at);
    if (group !== lastGroup) {
      const h = document.createElement("div");
      h.className = "conv-group";
      h.textContent = group;
      els.list.appendChild(h);
      lastGroup = group;
    }
    const item = document.createElement("div");
    item.className = "conv-item" + (state.current?.id === conv.id ? " active" : "");
    item.innerHTML = `
      <button class="conv-open" type="button"></button>
      <button class="icon-btn rename" type="button" title="Rename" aria-label="Rename chat">${ICONS.pencil}</button>
      <button class="icon-btn delete" type="button" title="Delete" aria-label="Delete chat">${ICONS.trash}</button>`;
    const open = item.querySelector(".conv-open");
    open.textContent = conv.title;
    open.title = conv.title;
    open.addEventListener("click", () => openConversation(conv.id));
    item.querySelector(".rename").addEventListener("click", () => startRename(item, conv));
    item.querySelector(".delete").addEventListener("click", () => deleteConversation(conv));
    els.list.appendChild(item);
  }
}

async function refreshList() {
  try {
    state.conversations = await api("GET", "/api/conversations");
  } catch (e) {
    showBanner(`Couldn't load chats: ${escapeHtml(e.message)}`);
  }
  renderList();
}

function startRename(item, conv) {
  const input = document.createElement("input");
  input.className = "conv-rename";
  input.value = conv.title;
  input.maxLength = 100;
  item.querySelector(".conv-open").replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const title = input.value.trim();
    if (save && title && title !== conv.title) {
      try {
        const updated = await api("PATCH", `/api/conversations/${conv.id}`, { title });
        if (state.current?.id === conv.id) {
          state.current.title = updated.title;
          setTitle(updated.title);
        }
      } catch (e) {
        showBanner(escapeHtml(e.message));
      }
    }
    await refreshList();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

async function deleteConversation(conv) {
  if (state.streaming) return;
  if (!confirm(`Delete "${conv.title}"? This can't be undone.`)) return;
  try {
    await api("DELETE", `/api/conversations/${conv.id}`);
  } catch (e) {
    showBanner(escapeHtml(e.message));
    return;
  }
  if (state.current?.id === conv.id) newChat();
  await refreshList();
}

function openSidebar() { els.sidebar.classList.add("open"); }
function closeSidebar() { els.sidebar.classList.remove("open"); }

// ---------- Conversation view ----------

function setTitle(title) {
  els.title.textContent = title;
  document.title = state.current ? `${title} · ChatBot` : "ChatBot";
}

function renderUsage() {
  const u = state.current?.usage;
  if (!u || !u.output_tokens) {
    els.usage.textContent = "";
    return;
  }
  let label = `${fmt(totalIn(u))} in · ${fmt(u.output_tokens)} out`;
  if (u.web_search_requests) label += ` · ${u.web_search_requests} searches`;
  els.usage.textContent = label;
}

function guardStreaming() {
  if (!state.streaming) return false;
  showBanner("A reply is still streaming. Press Stop or wait for it to finish first.");
  return true;
}

function newChat() {
  if (guardStreaming()) return;
  state.current = null;
  history.replaceState(null, "", location.pathname);
  renderConversation();
  renderList();
  closeSidebar();
  els.input.focus();
}

async function openConversation(id) {
  if (guardStreaming()) return;
  closeSidebar();
  try {
    state.current = await api("GET", `/api/conversations/${id}`);
  } catch (e) {
    showBanner(`Couldn't open that chat: ${escapeHtml(e.message)}`);
    newChat();
    return;
  }
  history.replaceState(null, "", `#${id}`);
  hideBanner();
  renderConversation();
  renderList();
}

function renderConversation() {
  const conv = state.current;
  els.messages.innerHTML = "";
  setTitle(conv ? conv.title : "New chat");
  els.exportBtn.hidden = !conv;
  els.effort.value = conv?.effort ?? state.config.default_effort;
  renderUsage();
  if (!conv) {
    renderSetup();
    return;
  }
  renderChatInfo(conv);
  for (const m of conv.messages) {
    if (m.role === "user") addUserMessage(m.text);
    else addAssistantMessage().fill(m);
  }
  scrollToBottom();
}

function renderSetup() {
  const node = $("#setup-template").content.cloneNode(true);
  node.querySelectorAll(".suggestion").forEach((btn) =>
    btn.addEventListener("click", () => send(btn.textContent)),
  );
  els.messages.appendChild(node);
}

function renderChatInfo(conv) {
  const info = document.createElement("div");
  info.className = "chat-info";
  const persona = document.createElement("span");
  persona.className = "chip";
  persona.innerHTML = ICONS.persona;
  persona.append(conv.persona ? `Persona: ${conv.persona}` : "Default persona");
  persona.title = conv.persona || state.config.default_persona;
  const search = document.createElement("span");
  search.className = "chip";
  search.innerHTML = ICONS.globe;
  search.append(conv.web_search ? "Google Search on" : "Google Search off");
  info.append(persona, search);
  els.messages.appendChild(info);
}

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  el.appendChild(bubble);
  els.messages.appendChild(el);
  return el;
}

function addAssistantMessage() {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `
    <div class="avatar">${ICONS.spark}</div>
    <div class="msg-body">
      <details class="thinking" hidden><summary>Thought process</summary><div class="thinking-text"></div></details>
      <div class="searches" hidden></div>
      <div class="status" hidden><span class="dots"><i></i><i></i><i></i></span><span class="status-text"></span></div>
      <div class="md"></div>
      <div class="sources" hidden></div>
      <div class="search-suggestions" hidden></div>
      <div class="notice" hidden></div>
      <div class="msg-foot" hidden></div>
    </div>`;
  els.messages.appendChild(el);
  const q = (s) => el.querySelector(s);
  const md = q(".md");
  let text = "";
  let thinking = "";
  let pending = false;

  // Re-render Markdown at most once per frame while tokens stream in.
  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      if (!pending) return;
      pending = false;
      const stick = isNearBottom();
      renderMarkdown(md, text, false);
      if (stick) scrollToBottom();
    });
  }

  const view = {
    el,
    get text() { return text; },
    status(label) {
      const s = q(".status");
      s.hidden = label == null;
      if (label != null) q(".status-text").textContent = label;
    },
    appendText(t) {
      text += t;
      view.status(null);
      schedule();
    },
    appendThinking(t) {
      thinking += t;
      q(".thinking").hidden = false;
      q(".thinking-text").textContent = thinking;
    },
    addSearch(query) {
      const box = q(".searches");
      box.hidden = false;
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = ICONS.search;
      chip.append(query);
      box.appendChild(chip);
    },
    setSources(sources = []) {
      const box = q(".sources");
      box.innerHTML = "";
      box.hidden = !sources.length;
      if (!sources.length) return;
      const label = document.createElement("div");
      label.className = "sources-label";
      label.textContent = "Sources";
      box.appendChild(label);
      for (const s of sources) {
        const a = document.createElement("a");
        a.href = s.url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = s.title || s.url;
        a.title = s.url;
        box.appendChild(a);
      }
    },
    setSearchSuggestions(html) {
      // Google's terms require showing its Search Suggestions next to answers grounded in
      // Google Search. The HTML comes from Google, so it runs in a sandboxed frame,
      // isolated from this page, with links opening in a new tab.
      const box = q(".search-suggestions");
      box.innerHTML = "";
      box.hidden = !html;
      if (!html) return;
      const frame = document.createElement("iframe");
      frame.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
      frame.title = "Google Search suggestions";
      frame.srcdoc = `<base target="_blank">${html}`;
      box.appendChild(frame);
    },
    notice(kind, message, retry) {
      const n = q(".notice");
      n.hidden = false;
      n.className = `notice ${kind}`;
      n.textContent = message;
      if (retry) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "Retry";
        btn.addEventListener("click", retry);
        n.appendChild(btn);
      }
    },
    footer(meta = {}) {
      const foot = q(".msg-foot");
      foot.hidden = false;
      foot.innerHTML = "";
      if (text) {
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "icon-btn";
        copy.title = "Copy reply";
        copy.setAttribute("aria-label", "Copy reply");
        copy.innerHTML = ICONS.copy;
        copy.addEventListener("click", () => copyText(text, copy));
        foot.appendChild(copy);
      }
      if (meta.usage) {
        const u = meta.usage;
        let label = `${fmt(totalIn(u))} in · ${fmt(u.output_tokens)} out`;
        if (u.cache_read_input_tokens) label += ` · ${fmt(u.cache_read_input_tokens)} from cache`;
        foot.append(label);
      }
      const tag = (label) => {
        const t = document.createElement("span");
        t.className = "tag";
        t.textContent = label;
        foot.appendChild(t);
      };
      if (meta.effort) tag(`${meta.effort} thinking`);
      if (meta.stopped) tag("stopped");
      if (meta.finish_reason === "MAX_TOKENS") tag("cut off at max tokens");
    },
    finish() {
      pending = false;
      renderMarkdown(md, text, true);
      view.status(null);
    },
    fill(m) {
      text = m.text || "";
      renderMarkdown(md, text, true);
      if (m.thinking) view.appendThinking(m.thinking);
      (m.searches || []).forEach(view.addSearch);
      view.setSources(m.sources);
      view.setSearchSuggestions(m.search_html);
      view.footer(m.meta);
      return view;
    },
  };
  return view;
}

// ---------- Sending & streaming ----------

function setStreaming(on) {
  els.composer.classList.toggle("streaming", on);
  els.sendBtn.setAttribute("aria-label", on ? "Stop" : "Send");
  els.sendBtn.title = on ? "Stop generating" : "Send";
  els.effort.disabled = on;
}

// Parse a Server-Sent Events body: frames separated by a blank line, "data:" lines.
async function readEvents(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}

async function send(rawText) {
  const text = rawText.trim();
  if (!text || state.streaming) return;
  hideBanner();

  if (!state.current) {
    // First message of a new chat: save it with the setup choices, which are now locked.
    try {
      state.current = await api("POST", "/api/conversations", {
        persona: $("#persona")?.value ?? "",
        web_search: $("#web-search")?.checked ?? false,
        effort: els.effort.value,
      });
    } catch (e) {
      showBanner(`Couldn't start the chat: ${escapeHtml(e.message)}`);
      return;
    }
    history.replaceState(null, "", `#${state.current.id}`);
    renderConversation();
  }

  const conv = state.current;
  els.input.value = "";
  autosize();
  const userEl = addUserMessage(text);
  const view = addAssistantMessage();
  view.status("Thinking…");
  scrollToBottom();

  const controller = new AbortController();
  state.streaming = { controller };
  setStreaming(true);

  const retry = () => {
    userEl.remove();
    view.el.remove();
    send(text);
  };
  let ended = false;

  const onEvent = (ev) => {
    switch (ev.type) {
      case "start":
        conv.title = ev.title;
        setTitle(ev.title);
        break;
      case "thinking":
        view.appendThinking(ev.text);
        break;
      case "text":
        view.appendText(ev.text);
        break;
      case "search":
        view.addSearch(ev.query);
        break;
      case "done":
        ended = true;
        view.finish();
        view.setSources(ev.sources);
        view.setSearchSuggestions(ev.search_html);
        view.footer(ev);
        conv.usage = ev.conversation_usage;
        renderUsage();
        break;
      case "blocked":
        ended = true;
        view.finish();
        userEl.classList.add("unsaved");
        view.notice(
          "refusal",
          `Gemini's safety filters blocked this (${ev.reason}), so the message wasn't saved. Try rephrasing it.`,
        );
        break;
      case "error":
        ended = true;
        view.finish();
        userEl.classList.add("unsaved");
        view.notice("error", ev.message, retry);
        break;
    }
  };

  try {
    const res = await fetch(`/api/conversations/${conv.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text, effort: els.effort.value }),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    await readEvents(res.body, onEvent);
    if (!ended) throw new Error("The connection closed before the reply finished.");
  } catch (err) {
    view.finish();
    if (err.name === "AbortError") {
      // The server keeps whatever text was shown; with nothing shown, it drops the turn.
      if (view.text.trim()) view.footer({ stopped: true });
      else {
        userEl.classList.add("unsaved");
        view.notice("refusal", "Stopped before the assistant replied. This message wasn't saved.", retry);
      }
    } else if (!ended) {
      userEl.classList.add("unsaved");
      view.notice("error", err.message, retry);
    }
  } finally {
    state.streaming = null;
    setStreaming(false);
    refreshList();
  }
}

// ---------- Wiring ----------

els.composer.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.streaming) state.streaming.controller.abort();
  else send(els.input.value);
});

els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (!state.streaming) send(els.input.value);
  }
});
els.input.addEventListener("input", autosize);

els.effort.addEventListener("change", async () => {
  if (!state.current) return;
  try {
    const updated = await api("PATCH", `/api/conversations/${state.current.id}`, { effort: els.effort.value });
    state.current.effort = updated.effort;
  } catch (e) {
    showBanner(escapeHtml(e.message));
  }
});

els.newChat.addEventListener("click", newChat);
els.menuBtn.addEventListener("click", openSidebar);
els.scrim.addEventListener("click", closeSidebar);
els.banner.addEventListener("click", hideBanner);
els.exportBtn.addEventListener("click", () => {
  if (state.current) location.href = `/api/conversations/${state.current.id}/export`;
});

async function init() {
  try {
    state.config = await api("GET", "/api/config");
  } catch (e) {
    showBanner(`Can't reach the ChatBot server: ${escapeHtml(e.message)}`);
    return;
  }
  for (const level of state.config.efforts) {
    const opt = document.createElement("option");
    opt.value = level;
    opt.textContent = level;
    els.effort.appendChild(opt);
  }
  els.modelInfo.textContent = `Model: ${state.config.model}`;
  if (!state.config.has_api_key) {
    showBanner(
      "No <code>GEMINI_API_KEY</code> found. Get a free key at aistudio.google.com/apikey, " +
        "paste it into <code>.env</code>, then restart the server.",
    );
  }
  await refreshList();
  const id = location.hash.slice(1);
  if (id) await openConversation(id);
  else newChat();
}

init();
