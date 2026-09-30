// Chat page: model menu, streaming replies, and a small safe Markdown renderer.
(function () {
  const form = document.getElementById("chat-form");
  if (!form) return;

  const thread = document.getElementById("chat-thread");
  const scroller = document.getElementById("chat-scroll");
  const welcome = document.getElementById("chat-welcome");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const stopBtn = document.getElementById("chat-stop");
  const convInput = document.getElementById("chat-conversation");
  const modelInput = document.getElementById("chat-model");
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  let controller = null;

  // --- Markdown ---------------------------------------------------------------
  // Everything is HTML-escaped first, then a few safe tags are added back,
  // so nothing a model writes can inject markup or script.
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ESC[c]);

  function inline(text) {
    // Split out `code` first so its contents stay literal.
    return text.split(/(`[^`\n]+`)/g).map((part, i) => {
      if (i % 2) return "<code>" + escapeHtml(part.slice(1, -1)) + "</code>";
      return escapeHtml(part)
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/__(.+?)__/g, "<strong>$1</strong>")
        .replace(/(^|[^\w*])\*(?!\s)([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>");
    }).join("");
  }

  const LIST = /^(\s*)([-*+•]|\d+[.)])\s+(.*)$/;
  const FENCE = /^\s*```/;
  const HEADING = /^\s*(#{1,6})\s+(.*)$/;
  const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
  const QUOTE = /^\s*>/;
  const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const isTableStart = (l, next) => l.includes("|") && next !== undefined && TABLE_SEP.test(next);
  const startsBlock = (l, next) =>
    FENCE.test(l) || HEADING.test(l) || RULE.test(l) || QUOTE.test(l) || LIST.test(l) || isTableStart(l, next);
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => inline(c.trim()));

  function dedent(lines) {
    const pad = Math.min(...lines.filter((l) => l.trim()).map((l) => l.match(/^\s*/)[0].length));
    return lines.map((l) => l.slice(pad)).join("\n");
  }

  function renderMarkdown(src) {
    const lines = src.replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      let m;
      if (!line.trim()) { i++; continue; }

      if (FENCE.test(line)) {
        const code = [];
        i++;
        while (i < lines.length && !FENCE.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push("<pre><code>" + escapeHtml(code.join("\n")) + "</code></pre>");
      } else if ((m = line.match(HEADING))) {
        const tag = m[1].length <= 2 ? "h3" : "h4";
        out.push(`<${tag}>${inline(m[2])}</${tag}>`);
        i++;
      } else if (RULE.test(line)) {
        out.push("<hr>");
        i++;
      } else if (isTableStart(line, lines[i + 1])) {
        const head = cells(line);
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(cells(lines[i++]));
        out.push("<table><thead><tr>" + head.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>" +
          rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      } else if (QUOTE.test(line)) {
        const quote = [];
        while (i < lines.length && QUOTE.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push("<blockquote>" + renderMarkdown(quote.join("\n")) + "</blockquote>");
      } else if ((m = line.match(LIST))) {
        const indent = m[1].length;
        const ordered = /\d/.test(m[2]);
        const items = [];
        while (i < lines.length) {
          const l = lines[i];
          const lm = l.match(LIST);
          if (lm && lm[1].length <= indent) {
            if (/\d/.test(lm[2]) !== ordered) break;
            items.push({ text: lm[3], sub: [] });
            i++;
          } else if (l.trim() && /^\s/.test(l)) {
            items[items.length - 1].sub.push(l); // nested list or wrapped text
            i++;
          } else if (!l.trim()) {
            // A blank line inside a list is fine if the list carries on after it.
            const next = lines[i + 1] || "";
            const nm = next.match(LIST);
            if ((nm && nm[1].length <= indent && /\d/.test(nm[2]) === ordered) || (next.trim() && /^\s/.test(next))) i++;
            else break;
          } else break;
        }
        const start = ordered ? parseInt(m[2], 10) : 1;
        const body = items.map((it) => "<li>" + inline(it.text) + (it.sub.length ? renderMarkdown(dedent(it.sub)) : "") + "</li>").join("");
        out.push(ordered ? `<ol${start !== 1 ? ` start="${start}"` : ""}>${body}</ol>` : `<ul>${body}</ul>`);
      } else {
        const para = [];
        while (i < lines.length && lines[i].trim() && !(para.length && startsBlock(lines[i], lines[i + 1]))) para.push(lines[i++]);
        out.push("<p>" + para.map(inline).join("<br>") + "</p>");
      }
    }
    return out.join("");
  }

  function renderInto(el, text) {
    el.dataset.raw = text;
    el.innerHTML = renderMarkdown(text);
    el.classList.add("rendered");
  }

  // Replies saved earlier arrive as plain escaped text; render them now.
  thread.querySelectorAll(".msg-bot .md").forEach((el) => renderInto(el, el.textContent.trim()));

  // --- Scrolling --------------------------------------------------------------
  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  const toBottom = () => { scroller.scrollTop = scroller.scrollHeight; };
  toBottom();

  // --- Model menu ---------------------------------------------------------------
  const pickerBtn = document.getElementById("model-picker-btn");
  const menu = document.getElementById("model-menu");
  const options = menu ? Array.from(menu.querySelectorAll("li")) : [];
  const STORE_KEY = "chat-model";

  function selectModel(li, remember = true) {
    options.forEach((o) => o.setAttribute("aria-selected", o === li ? "true" : "false"));
    modelInput.value = li.dataset.id;
    document.getElementById("model-picker-name").textContent = li.dataset.name;
    document.getElementById("model-picker-provider").textContent = li.dataset.provider;
    document.getElementById("model-picker-mark").replaceChildren(li.querySelector(".mark").cloneNode(true));
    if (remember) { try { localStorage.setItem(STORE_KEY, li.dataset.id); } catch (_) { /* storage off */ } }
  }
  function openMenu(open) {
    menu.hidden = !open;
    pickerBtn.setAttribute("aria-expanded", String(open));
    if (open) (options.find((o) => o.getAttribute("aria-selected") === "true") || options[0])?.focus();
  }
  if (pickerBtn && menu) {
    pickerBtn.addEventListener("click", () => openMenu(menu.hidden));
    options.forEach((li) => li.addEventListener("click", () => { selectModel(li); openMenu(false); pickerBtn.focus(); }));
    menu.addEventListener("keydown", (e) => {
      const idx = options.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); options[Math.min(idx + 1, options.length - 1)].focus(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); options[Math.max(idx - 1, 0)].focus(); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); document.activeElement.click(); }
      else if (e.key === "Escape") { openMenu(false); pickerBtn.focus(); }
    });
    document.addEventListener("click", (e) => { if (!e.target.closest("#model-picker")) openMenu(false); });
    // A new chat starts with the model picked last time; an existing chat keeps its own.
    if (!convInput.value) {
      let saved = null;
      try { saved = localStorage.getItem(STORE_KEY); } catch (_) { /* storage off */ }
      const li = options.find((o) => o.dataset.id === saved);
      if (li) selectModel(li, false);
    }
  }

  // --- Composer -------------------------------------------------------------------
  function grow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }
  input.addEventListener("input", grow);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  document.querySelectorAll("[data-suggest]").forEach((b) => b.addEventListener("click", () => {
    input.value = b.dataset.suggest;
    grow();
    input.focus();
  }));

  function setBusy(busy) {
    sendBtn.hidden = busy;
    stopBtn.hidden = !busy;
    if (!busy) input.focus();
  }
  stopBtn.addEventListener("click", () => controller?.abort());

  // --- Message bubbles ------------------------------------------------------------
  function addUserBubble(text) {
    const row = document.createElement("div");
    row.className = "msg msg-user";
    const bubble = document.createElement("div");
    bubble.className = "bubble md";
    bubble.textContent = text;
    row.append(bubble);
    thread.append(row);
  }

  function addBotBubble() {
    const selected = options.find((o) => o.getAttribute("aria-selected") === "true");
    const row = document.createElement("div");
    row.className = "msg msg-bot streaming";
    row.innerHTML =
      '<div class="msg-body"><div class="msg-meta"><strong></strong><span class="meta-rest"></span></div>' +
      '<div class="md rendered"><div class="typing" aria-label="Writing"><i></i><i></i><i></i></div></div>' +
      '<div class="msg-actions"></div></div>';
    if (selected) row.prepend(selected.querySelector(".mark").cloneNode(true));
    row.querySelector(".msg-meta strong").textContent = selected ? selected.dataset.name : "";
    thread.append(row);
    return row;
  }

  function actionButton(action, label, icon) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "msg-btn";
    b.dataset.action = action;
    b.innerHTML = `<svg class="ico"><use href="#i-${icon}"></use></svg>`;
    b.append(label);
    return b;
  }

  function showError(row, message) {
    row.classList.add("has-error");
    const box = document.createElement("div");
    box.className = "msg-error";
    box.innerHTML = '<svg class="ico"><use href="#i-alert"></use></svg><span></span>';
    box.querySelector("span").textContent = message;
    row.querySelector(".msg-actions").before(box);
  }

  function finish(row, { text, meta, error, stopped }) {
    row.classList.remove("streaming");
    const md = row.querySelector(".md");
    if (text) renderInto(md, text);
    else md.remove();
    const rest = [];
    if (meta?.latency_ms) rest.push((meta.latency_ms / 1000).toFixed(1) + " s");
    if (meta?.completion_tokens) rest.push(meta.completion_tokens + " tokens");
    if (meta?.truncated) rest.push("cut off by token limit");
    if (stopped) rest.push("stopped");
    row.querySelector(".meta-rest").textContent = rest.map((r) => " · " + r).join("");
    if (meta?.id) row.dataset.id = meta.id;
    if (error) showError(row, error);
    const actions = row.querySelector(".msg-actions");
    if (text) actions.append(actionButton("copy", "Copy", "copy"));
    actions.append(actionButton("regenerate", error || !text ? "Try again" : "Regenerate", "refresh"));
  }

  // Only the newest reply can be regenerated.
  const clearRegenerate = () => thread.querySelectorAll('[data-action="regenerate"]').forEach((b) => b.remove());

  // --- Streaming ------------------------------------------------------------------
  function parseEvent(block) {
    let event = "message";
    const data = [];
    block.split("\n").forEach((line) => {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    });
    try { return { event, data: JSON.parse(data.join("\n") || "{}") }; } catch (_) { return { event, data: {} }; }
  }

  function onStart(data) {
    convInput.value = data.conversation;
    form.dataset.regenerateUrl = form.dataset.regenerateTemplate.replace("/0/", `/${data.conversation}/`);
    if (data.created) {
      history.replaceState(null, "", data.url);
      document.title = data.title + " · Chat · LLM Health Assistant";
      document.getElementById("chat-list-empty")?.remove();
      const list = document.getElementById("chat-list");
      list.querySelectorAll("a.active").forEach((a) => a.classList.remove("active"));
      const a = document.createElement("a");
      a.href = data.url;
      a.className = "active";
      a.innerHTML = '<span class="chat-list-title"></span><small>just now</small>';
      a.querySelector(".chat-list-title").textContent = data.title;
      list.prepend(a);
    }
  }

  async function streamReply(url, body) {
    clearRegenerate();
    const row = addBotBubble();
    const md = row.querySelector(".md");
    let text = "";
    let ended = false;
    let frame = 0;
    const paint = () => {
      frame = 0;
      const stick = nearBottom();
      renderInto(md, text);
      if (stick) toBottom();
    };
    toBottom();
    controller = new AbortController();
    setBusy(true);
    try {
      const resp = await fetch(url, {
        method: "POST", body, signal: controller.signal, headers: { "X-CSRFToken": csrf },
      });
      if (!resp.ok) {
        let msg = `Request failed (HTTP ${resp.status})`;
        try { msg = (await resp.json()).error || msg; } catch (_) { /* not JSON */ }
        finish(row, { error: msg });
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const { event, data } = parseEvent(buffer.slice(0, cut));
          buffer = buffer.slice(cut + 2);
          if (event === "start") onStart(data);
          else if (event === "delta") {
            text += data.text || "";
            if (!frame) frame = requestAnimationFrame(paint);
          } else if (event === "done") {
            ended = true;
            finish(row, { text, meta: data });
          } else if (event === "error") {
            ended = true;
            finish(row, { text, error: data.error });
          }
        }
      }
      if (!ended) finish(row, { text, error: "The reply ended unexpectedly. Try again." });
    } catch (err) {
      if (frame) cancelAnimationFrame(frame);
      if (err.name === "AbortError") finish(row, { text, stopped: true, error: text ? "" : "Stopped before the model replied." });
      else finish(row, { text, error: "Connection lost: " + err.message });
    } finally {
      controller = null;
      setBusy(false);
      if (nearBottom()) toBottom();
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || controller) return;
    if (!modelInput.value) { alert("Pick a model first."); return; }
    const body = new FormData(form);
    body.set("message", text);
    thread.querySelectorAll("[data-orphan]").forEach((el) => el.remove());
    if (welcome) welcome.hidden = true;
    addUserBubble(text);
    input.value = "";
    grow();
    streamReply(form.dataset.sendUrl, body);
  });

  thread.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const row = btn.closest(".msg");
    if (btn.dataset.action === "copy") {
      const md = row.querySelector(".md");
      try {
        await navigator.clipboard.writeText(md.dataset.raw || md.innerText);
        btn.lastChild.textContent = "Copied";
        setTimeout(() => { btn.lastChild.textContent = "Copy"; }, 1500);
      } catch (_) { /* clipboard unavailable */ }
    } else if (btn.dataset.action === "regenerate" && !controller) {
      if (!modelInput.value || !form.dataset.regenerateUrl) return;
      row.remove();
      const body = new FormData();
      body.set("csrfmiddlewaretoken", csrf);
      body.set("model", modelInput.value);
      streamReply(form.dataset.regenerateUrl, body);
    }
  });

  input.focus();
})();
