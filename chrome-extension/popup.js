/**
 * Minimal popup: load/save API base for content script fetch.
 */

const BASE_URL = "https://prompt-quality-analyzer.onrender.com";
const DEFAULT_API_BASE = BASE_URL;

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("apiBase");
  const btn = document.getElementById("saveApi");
  const saved = document.getElementById("saved");

  chrome.storage.sync.get({ apiBase: DEFAULT_API_BASE }, (r) => {
    input.value = (r.apiBase && String(r.apiBase).trim()) || DEFAULT_API_BASE;
  });

  btn.addEventListener("click", () => {
    let v = input.value.trim().replace(/\/$/, "");
    if (!v) v = DEFAULT_API_BASE;
    chrome.storage.sync.set({ apiBase: v }, () => {
      input.value = v;
      saved.textContent = "Saved.";
      setTimeout(() => {
        saved.textContent = "";
      }, 2000);
    });
  });
});
