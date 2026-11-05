// Peer annotation pages (home and item).
//
// Item page: one item in A is "active" at a time. C / P / N answer it, J / K move,
// and clicking a part of B links it to the active item. Answers are kept as a draft
// in this browser until saved, so Back, a reload or a closed tab loses nothing.
// Ctrl+Enter (⌘↵) saves; a save with something missing scrolls to it instead.
// Ctrl+Z (⌘Z) goes back to the previous item, on the home page too.
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const mac = /Mac|iPhone|iPad/.test(navigator.platform);
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); return true; } catch { return false; } },
    remove(key) { try { localStorage.removeItem(key); } catch { /* private mode */ } },
  };
  const typing = (el) => el && (el.tagName === "TEXTAREA" || el.isContentEditable ||
    (el.tagName === "INPUT" && !["radio", "checkbox", "button", "submit", "hidden"].includes(el.type)));
  const modKey = (e) => (mac ? e.metaKey : e.ctrlKey) && !e.altKey;

  // Scroll only when the element is out of view, and keep it clear of the sticky bar.
  function reveal(el, block = "center") {
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.top >= 70 && r.bottom <= window.innerHeight - 110) return;
    el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block });
  }
  function replay(el, cls) {
    if (!el || calm) return;
    el.classList.remove(cls);
    void el.offsetWidth;   // restart the animation
    el.classList.add(cls);
    el.addEventListener("animationend", () => el.classList.remove(cls), { once: true });
  }

  $$(".ann-mod").forEach((k) => { k.textContent = mac ? "⌘" : "Ctrl"; });
  $$(".ann-kbd").forEach((k) => { if (mac) k.textContent = "⌘Z"; });

  // Saved / info notes fade away on their own; errors stay until clicked.
  $$(".flash-stack").forEach((stack) => {
    const dismiss = () => {
      if (calm) { stack.remove(); return; }
      stack.classList.add("is-leaving");
      stack.addEventListener("animationend", () => stack.remove(), { once: true });
    };
    stack.addEventListener("click", dismiss);
    if (!stack.querySelector(".flash-error")) setTimeout(dismiss, 3800);
  });

  // The patient panel: folding it holds for the rest of that patient's outputs only,
  // so a new patient always opens with their message showing.
  const context = $(".ann-context");
  if (context) {
    const key = `ann-context-closed:${context.dataset.run}`;
    if (store.get(key)) context.open = false;
    context.addEventListener("toggle", () => (context.open ? store.remove(key) : store.set(key, "1")));
  }

  const form = $("#ann-form");
  let leaveOk = () => true;

  // ---------- Back (Ctrl+Z) ----------
  const back = $("#ann-back");
  if (back) {
    back.addEventListener("click", (e) => { if (!leaveOk()) e.preventDefault(); });
    document.addEventListener("keydown", (e) => {
      if (e.key.toLowerCase() !== "z" || !modKey(e) || e.shiftKey || typing(document.activeElement)) return;
      e.preventDefault();
      if (leaveOk()) window.location.href = back.href;
    });
  }

  if (!form) return;
  const started = Date.now();
  let carried = 0;     // seconds spent on this item before a reload, from the draft
  let ready = false;   // no entrance animations for what was already answered on load

  const items = $$(".ann-item[data-item]");
  const units = $$(".ann-b-text .ann-unit");
  const panelB = $(".ann-side-b");
  const hint = $("#ann-b-hint");
  const bar = $("#ann-bar");
  const note = $("#ann-progress");
  const tally = $("#ann-tally");
  const segs = $$(".ann-map-seg");
  const steps = $$(".ann-map-step");
  const label = (item) => $('input[name^="item_"]:checked', item)?.value || "";
  const covers = (value) => value === "yes" || value === "partly";
  const boxes = (item) => $$(".ann-link input", item);
  const linkedTo = (i) => boxes(items[i]).filter((b) => b.checked).map((b) => b.value);
  const unitAt = (j) => units.find((u) => u.dataset.unit === String(j));
  const needsLink = (i) => covers(label(items[i])) && !!$(".ann-links", items[i]) && !linkedTo(i).length;

  // ---------- The active item ----------
  let active = -1;
  function setActive(i, { scroll = true } = {}) {
    if (i < 0 || i >= items.length) return;
    active = i;
    items.forEach((item, k) => item.classList.toggle("is-active", k === i));
    segs.forEach((seg, k) => seg.classList.toggle("is-active", k === i));
    if (scroll) reveal(items[i]);
    sync();
  }
  function advanceFrom(i, { scroll = true } = {}) {
    const next = items.findIndex((item, k) => k > i && !label(item));
    const target = next >= 0 ? next : items.findIndex((item) => !label(item));
    if (target >= 0) setActive(target, { scroll });
    return target;
  }
  function answer(i, value, { fromKey = false } = {}) {
    const radio = $(`input[name="item_${i}"][value="${value}"]`, items[i]);
    if (!radio) return;
    radio.checked = true;
    form.dispatchEvent(new Event("change", { bubbles: true }));
    replay($(".ann-num", items[i]), "is-pop");
    if (value === "no" && advanceFrom(i, { scroll: fromKey }) < 0 && fromKey) reveal($("#ann-step-similar"), "start");
  }
  items.forEach((item, i) => {
    item.addEventListener("pointerdown", () => { if (active !== i) setActive(i, { scroll: false }); });
    item.addEventListener("focusin", () => { if (active !== i) setActive(i, { scroll: false }); });
    // A mouse click on "Not covered" moves on to the next open item, without jumping the page.
    $$('input[name^="item_"]', item).forEach((radio) => radio.addEventListener("change", (e) => {
      if (!e.isTrusted) return;
      replay($(".ann-num", item), "is-pop");
      if (radio.value === "no") advanceFrom(i, { scroll: false });
    }));
  });

  // ---------- Linking: click a part of B ----------
  units.forEach((unit) => {
    unit.addEventListener("click", () => {
      if (active < 0) return;
      if (!covers(label(items[active]))) { replay(hint, "is-nudge"); return; }
      const box = boxes(items[active]).find((b) => b.value === unit.dataset.unit);
      if (!box) return;
      box.checked = !box.checked;
      form.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  // Point at an item and the parts of B it links to light up, and the other way round.
  const echo = (on, targets) => targets.forEach((el) => el && el.classList.toggle("is-echo", on));
  items.forEach((item, i) => {
    item.addEventListener("mouseenter", () => echo(true, linkedTo(i).map(unitAt)));
    item.addEventListener("mouseleave", () => echo(false, units));
  });
  units.forEach((unit) => {
    unit.addEventListener("mouseenter", () => echo(true, items.filter((_, i) => linkedTo(i).includes(unit.dataset.unit))));
    unit.addEventListener("mouseleave", () => echo(false, items));
  });
  $$(".ann-link").forEach((link) => {
    const j = link.dataset.unit;
    const text = $(".ann-unit-text", unitAt(j) || document)?.textContent || "";
    link.title = text;
    $("input", link)?.setAttribute("aria-label", `Part ${Number(j) + 1} of B: ${text}`);
    const light = (on) => {
      const unit = unitAt(j);
      if (!unit) return;
      unit.classList.toggle("is-hot", on);
      if (on && panelB && panelB.scrollHeight > panelB.clientHeight) {
        const top = unit.getBoundingClientRect().top - panelB.getBoundingClientRect().top;
        if (top < 60 || top > panelB.clientHeight - 40) {
          panelB.scrollTo({ top: panelB.scrollTop + top - 80, behavior: calm ? "auto" : "smooth" });
        }
      }
    };
    ["mouseenter", "focusin"].forEach((e) => link.addEventListener(e, () => light(true)));
    ["mouseleave", "focusout"].forEach((e) => link.addEventListener(e, () => light(false)));
  });

  // ---------- Keep everything in step with the answers ----------
  const branches = $$(".ann-branch");
  const other = $('input[name="other_reason"]');
  const otherBox = $('input[name="reasons"][value="other"]');
  const checked = (name) => $(`input[name="${name}"]:checked`, form)?.value || "";
  let matchedBefore = new Set();

  function sync() {
    const similar = checked("similar");
    branches.forEach((b) => { b.hidden = b.dataset.when !== similar; });
    if (other) other.closest(".ann-other").hidden = !(otherBox && otherBox.checked);

    const matchedBy = new Map();
    items.forEach((item, i) => {
      const value = label(item);
      const row = $(".ann-links", item);
      if (row) {
        const open = covers(value);
        row.classList.toggle("is-open", open);
        row.inert = !open;
        boxes(item).forEach((box) => {
          if (!open) box.checked = false;
          if (box.checked) matchedBy.set(box.value, [...(matchedBy.get(box.value) || []), i + 1]);
        });
      }
      ["yes", "partly", "no"].forEach((v) => {
        item.classList.toggle(`is-${v}`, value === v);
        segs[i]?.classList.toggle(`is-${v}`, value === v);
      });
      segs[i]?.classList.toggle("needs-link", needsLink(i));
    });

    const allAnswered = items.every(label);
    const activeLinks = active >= 0 ? linkedTo(active) : [];
    units.forEach((unit) => {
      const j = unit.dataset.unit;
      const from = matchedBy.get(j) || [];
      unit.classList.toggle("is-matched", from.length > 0);
      unit.classList.toggle("is-unmatched", allAnswered && !from.length);
      unit.classList.toggle("is-mine", activeLinks.includes(j));
      $(".ann-unit-links", unit).textContent = from.length ? "A" + from.join(", A") : "";
      if (ready && from.length && !matchedBefore.has(j)) replay($(".ann-unit-num", unit), "is-pop");
    });
    matchedBefore = new Set(matchedBy.keys());

    if (hint && panelB) {
      const value = active >= 0 ? label(items[active]) : "";
      panelB.classList.toggle("is-linking", covers(value));
      hint.textContent = covers(value)
        ? `Click the parts of B that say item ${active + 1}. Click again to undo.`
        : value === "no"
          ? `Item ${active + 1} is not covered, so there's nothing to link.`
          : "Mark an item in A as Covered or Partly, then click where B says it.";
    }

    steps.forEach((step) => {
      const done = step.dataset.step === "ann-step-similar" ? !!similar : !!checked("confidence");
      step.classList.toggle("is-done", done);
    });

    const picked = items.map(label).filter(Boolean);
    const yes = picked.filter((v) => v === "yes").length;
    const partly = picked.filter((v) => v === "partly").length;
    const total = items.length;
    if (note && !bar.classList.contains("has-problem")) {
      note.textContent = picked.length === total ? `All ${total} items answered` : `${picked.length} of ${total} items answered`;
      note.classList.toggle("complete", picked.length === total);
    }
    if (tally && picked.length) {
      const share = Math.round(((yes + partly / 2) / total) * 100);
      tally.hidden = false;
      tally.textContent = `Your item answers so far: ${yes} covered, ${partly} partly, ${picked.length - yes - partly} not covered` +
        (picked.length === total ? ` (about ${share}% of A)` : "") +
        (units.length ? `; ${matchedBy.size} of ${units.length} parts of B matched to A.` : ".");
    }
  }

  // ---------- Map in the bar: click to jump ----------
  segs.forEach((seg, i) => seg.addEventListener("click", () => {
    setActive(i, { scroll: false });
    reveal(items[i]);
    replay(items[i], "is-flash");
  }));
  steps.forEach((step) => step.addEventListener("click", () => {
    const section = document.getElementById(step.dataset.step);
    reveal(section, "center");
    replay(section, "is-flash");
  }));

  // ---------- Draft: answers survive Back, reloads and closed tabs ----------
  const draftKey = form.dataset.draftKey ? `ann-draft:${form.dataset.draftKey}` : "";
  const skip = ["csrfmiddlewaretoken", "task", "seconds"];
  let dirty = false;
  let kept = true;   // the latest answers are in the draft
  function snapshot() {
    const data = {};
    new FormData(form).forEach((value, name) => {
      if (!skip.includes(name)) (data[name] = data[name] || []).push(value);
    });
    return JSON.stringify({ data, seconds: carried + Math.round((Date.now() - started) / 1000) });
  }
  let timer = 0;
  function keepDraft() {
    if (!draftKey || !dirty) return;
    kept = false;
    clearTimeout(timer);
    timer = setTimeout(() => { kept = store.set(draftKey, snapshot()); }, 250);
  }
  function restoreDraft() {
    // A page sent back with errors already holds the latest answers.
    if (!draftKey || $(".has-error", form)) return false;
    let draft;
    try { draft = JSON.parse(store.get(draftKey) || "null"); } catch { draft = null; }
    if (!draft || !draft.data || !Object.keys(draft.data).length) return false;
    $$("input, textarea", form).forEach((el) => {
      if (!el.name || skip.includes(el.name) || el.type === "hidden") return;
      const values = draft.data[el.name] || [];
      if (el.type === "radio" || el.type === "checkbox") el.checked = values.includes(el.value);
      else el.value = values[0] || "";
    });
    carried = Number(draft.seconds) || 0;
    return true;
  }
  if (restoreDraft()) $("#ann-draft-note")?.removeAttribute("hidden");
  $("#ann-draft-discard")?.addEventListener("click", () => {
    store.remove(draftKey);
    dirty = false;
    window.location.reload();
  });
  leaveOk = () => !dirty || (kept && !!draftKey) || window.confirm("Leave this item? Your answers on it aren't saved.");
  window.addEventListener("pagehide", () => { if (dirty && draftKey) store.set(draftKey, snapshot()); });

  form.addEventListener("change", () => {
    dirty = true;
    bar.classList.remove("has-problem");
    sync();
    keepDraft();
  });
  form.addEventListener("input", () => { dirty = true; keepDraft(); });

  // ---------- Saving: check first, and show where anything is missing ----------
  function firstGap() {
    for (let i = 0; i < items.length; i++) {
      if (!label(items[i])) return { el: items[i], item: i, msg: `Item ${i + 1} in A needs an answer` };
      if (needsLink(i)) return { el: items[i], item: i, msg: `Show where B says item ${i + 1}: click it in B` };
    }
    const similar = checked("similar");
    if (!similar) return { el: $("#ann-step-similar"), msg: "Say whether A and B are similar overall" };
    if (similar === "yes" && !checked("coverage")) {
      return { el: $('.ann-branch[data-when="yes"]'), msg: "Choose roughly how much of A B covers" };
    }
    if (similar === "no") {
      if (!$('input[name="reasons"]:checked', form)) return { el: $('.ann-branch[data-when="no"]'), msg: "Choose at least one reason they differ" };
      if (otherBox?.checked && !other.value.trim()) return { el: other, msg: "Add a short note for “Other”", focus: true };
    }
    if (!checked("confidence")) return { el: $("#ann-step-confidence"), msg: "Say how confident you feel" };
    return null;
  }
  const saveButton = $(".ann-save", form);
  form.addEventListener("submit", (e) => {
    const gap = firstGap();
    if (gap) {
      e.preventDefault();
      if (gap.item !== undefined) setActive(gap.item, { scroll: false });
      reveal(gap.el, "center");
      replay(gap.el, "is-missing");
      if (gap.focus) gap.el.focus({ preventScroll: true });
      bar.classList.add("has-problem");
      note.textContent = gap.msg;
      note.classList.remove("complete");
      replay(note, "is-nudge");
      return;
    }
    const seconds = $('input[name="seconds"]', form);
    if (seconds) seconds.value = carried + Math.round((Date.now() - started) / 1000);
    clearTimeout(timer);
    store.remove(draftKey);
    dirty = false;
    bar.classList.add("is-saving");
    const button = e.submitter || saveButton;
    if (button) {
      button.classList.add("is-busy");
      ($("span", button) || button).textContent = "Saving…";
    }
  });

  // ---------- Keyboard ----------
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented) return;
    if (e.key === "Enter" && modKey(e)) {
      e.preventDefault();
      form.requestSubmit(saveButton || undefined);
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey || typing(document.activeElement)) return;
    const key = e.key.toLowerCase();
    const labels = { c: "yes", p: "partly", n: "no" };
    if (labels[key] && active >= 0) {
      e.preventDefault();
      answer(active, labels[key], { fromKey: true });
    } else if (key === "j" || key === "k") {
      e.preventDefault();
      setActive(Math.min(items.length - 1, Math.max(0, (active < 0 ? 0 : active) + (key === "j" ? 1 : -1))));
    }
  });

  // ---------- Start on the first item that still needs something ----------
  const firstOpen = items.findIndex((item, i) => !label(item) || needsLink(i));
  setActive(firstOpen >= 0 ? firstOpen : 0, { scroll: false });
  ready = true;
})();
