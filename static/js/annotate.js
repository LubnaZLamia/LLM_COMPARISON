// Peer annotation item page: show the follow-up that matches the yes/no answer,
// count answered items, and time how long the annotator spent.
(function () {
  const form = document.getElementById("ann-form");
  if (!form) return;
  const started = Date.now();

  const branches = form.querySelectorAll(".ann-branch");
  const similar = () => form.querySelector('input[name="similar"]:checked')?.value || "";
  function showBranch() {
    const value = similar();
    branches.forEach((b) => { b.hidden = b.dataset.when !== value; });
  }

  const other = form.querySelector('input[name="other_reason"]');
  const otherBox = form.querySelector('input[name="reasons"][value="other"]');
  function showOther() {
    if (other) other.closest(".ann-other").hidden = !(otherBox && otherBox.checked);
  }

  // How many items are answered, and what the item answers add up to: a nudge
  // towards an overall answer that agrees with the per-item ones.
  const progress = document.getElementById("ann-progress");
  const tally = document.getElementById("ann-tally");
  const total = Number(progress?.dataset.total || 0);
  function count() {
    const picked = Array.from(form.querySelectorAll('input[name^="item_"]:checked')).map((i) => i.value);
    const yes = picked.filter((v) => v === "yes").length;
    const partly = picked.filter((v) => v === "partly").length;
    const no = picked.length - yes - partly;
    if (progress) {
      progress.textContent = picked.length === total
        ? `All ${total} items answered`
        : `${picked.length} of ${total} items answered`;
      progress.classList.toggle("complete", picked.length === total);
    }
    if (tally && picked.length) {
      const share = Math.round(((yes + partly / 2) / total) * 100);
      tally.hidden = false;
      tally.textContent = `Your item answers so far: ${yes} covered, ${partly} partly, ${no} not covered` +
        (picked.length === total ? ` (about ${share}% of A).` : ".");
    }
  }

  form.addEventListener("change", () => { showBranch(); showOther(); count(); });
  form.addEventListener("submit", () => {
    const seconds = form.querySelector('input[name="seconds"]');
    if (seconds) seconds.value = Math.round((Date.now() - started) / 1000);
  });
  showBranch();
  showOther();
  count();
})();
