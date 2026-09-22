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
