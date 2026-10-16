// LLM Health Assistant — small progressive enhancements (the app works without JS).
(function () {
  const form = document.getElementById("compare-form");

  // --- Tabs: Example Messages / Write Your Own ---------------------------
  const sourceInput = document.getElementById("source-input");
  const tabs = document.querySelectorAll(".tab[data-source]");
  function setSource(src) {
    if (!sourceInput) return;
    sourceInput.value = src;
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.source === src));
    document.querySelectorAll(".tab-panel").forEach((p) => (p.hidden = p.dataset.panel !== src));
  }
  tabs.forEach((t) => t.addEventListener("click", () => setSource(t.dataset.source)));
  if (sourceInput) setSource(sourceInput.value || "example");

  // --- Highlight the selected radio card ---------------------------------
  function syncSelected(groupName, cardSelector) {
    document.querySelectorAll(`input[name="${groupName}"]`).forEach((input) => {
      input.closest(cardSelector)?.classList.toggle("selected", input.checked);
    });
  }
  document.addEventListener("change", (e) => {
    if (e.target.name === "message") syncSelected("message", ".option");
    if (e.target.name === "task") syncSelected("task", ".task-option");
  });

  // --- The message list follows the task chosen in step 1 ---------------------
  // Only messages that can be scored for that task are offered. This is kept out
  // of the interface copy on purpose: the list simply shows what is selectable.
  const search = document.getElementById("example-search");
  const exampleList = document.getElementById("example-list");
  if (exampleList) {
    const options = Array.from(exampleList.querySelectorAll(".option"));
    const count = document.getElementById("search-count");
    const noMatches = document.getElementById("no-matches");
    const currentTask = () => document.querySelector('input[name="task"]:checked')?.value || "followup";
    const inThisDataset = (o) =>
      (currentTask() === "followup" ? o.dataset.gtFollowup : o.dataset.gtResponse) === "1";

    const apply = ({ taskChanged = false } = {}) => {
      const q = (search?.value || "").trim().toLowerCase();
      const eligible = options.filter(inThisDataset);
      let shown = 0;
      let selectionLost = false;

      options.forEach((o) => {
        const input = o.querySelector('input[name="message"]');
        const mine = inThisDataset(o);
        if (!mine && input?.checked) {         // its dataset no longer applies
          input.checked = false;
          o.classList.remove("selected");
          selectionLost = true;
        }
        const hit = mine && (!q || (o.dataset.search || "").includes(q));
        o.hidden = !hit;
        if (hit) shown++;
      });

      // Never leave step 2 with nothing chosen: fall back to the first message
      // of the task's dataset.
      if (selectionLost || !exampleList.querySelector('input[name="message"]:checked')) {
        const first = options.find((o) => inThisDataset(o) && !o.hidden) || eligible[0];
        const input = first?.querySelector('input[name="message"]');
        if (input) {
          input.checked = true;
          first.classList.add("selected");
          if (taskChanged) first.scrollIntoView({ block: "nearest" });
        }
      }

      if (count) count.textContent = q ? `${shown} shown` : "";
      if (noMatches) noMatches.hidden = shown > 0;
    };

    search?.addEventListener("input", () => apply());
    document.querySelectorAll('input[name="task"]').forEach((r) =>
      r.addEventListener("change", () => apply({ taskChanged: true })));
    apply();
  }

  // --- Rating form: show how many scores are still missing -------------------
  const progress = document.getElementById("rate-progress");
  if (progress) {
    const total = Number(progress.dataset.total || 0);
    const update = () => {
      const done = document.querySelectorAll('.scale input:checked').length;
      progress.textContent = done === total
        ? `All ${total} scores given — ready to save`
        : `${done} of ${total} scores given`;
      progress.classList.toggle("complete", done === total);
    };
    document.getElementById("rate-form")?.addEventListener("change", update);
    update();
  }

  // --- Loading overlay on submit ------------------------------------------
  if (form) {
    form.addEventListener("submit", () => {
      const loading = document.getElementById("loading");
      if (loading) loading.hidden = false;
    });
  }
  // Hide overlay if the user navigates back to a cached page.
  window.addEventListener("pageshow", () => {
    const loading = document.getElementById("loading");
    if (loading) loading.hidden = true;
  });

  // --- Side-by-side / Table view switch -----------------------------------
  document.querySelectorAll(".seg[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      document.querySelectorAll(".seg[data-view]").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll("[data-view-panel]").forEach((p) => (p.hidden = p.dataset.viewPanel !== view));
      const url = new URL(window.location);
      url.searchParams.set("view", view);
      history.replaceState(null, "", url);
    });
  });

  // --- Copy buttons --------------------------------------------------------
  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const el = document.getElementById(btn.dataset.copy);
      if (!el) return;
      try {
        await navigator.clipboard.writeText(el.innerText.trim());
        const original = btn.lastChild.textContent;
        btn.classList.add("copied");
        btn.lastChild.textContent = "Copied";
        setTimeout(() => { btn.classList.remove("copied"); btn.lastChild.textContent = original; }, 1500);
      } catch (_) { /* clipboard unavailable */ }
    });
  });
})();
