chrome.runtime.sendMessage({ type: "PING" }, (res) => {
  console.log("Ping response:", res);
});
const DEBOUNCE_MS = 1000;
const MIN_CHARS = 8;
const EXACT_DUPLICATE_COOLDOWN_MS = 2000;
const EDITABLE_SELECTOR = 'textarea, [contenteditable="true"]';
const BASE_URL = "https://prompt-quality-analyzer.onrender.com";
const DEFAULT_API_BASE = BASE_URL;
const ANALYZE_URL = `${BASE_URL}/analyze`;
const FIXED_POPUP_TOP_PX = 20;
const FIXED_POPUP_RIGHT_PX = 20;

function getApiBase() {
  return new Promise((resolve) => {
    try {
      chrome.storage.sync.get({ apiBase: DEFAULT_API_BASE }, (r) => {
        const raw = r && r.apiBase != null ? String(r.apiBase).trim() : "";
        const base = raw || DEFAULT_API_BASE;
        resolve(base.replace(/\/$/, ""));
      });
    } catch (_) {
      resolve(DEFAULT_API_BASE);
    }
  });
}

let debounceTimer = null;
let analyzeRequestSeq = 0;
let lastSentNorm = "";
let lastSentAtMs = 0;

let activeFetchController = null;
let inFlightNorm = "";
let popupEl = null;
let dragState = null;
let listenersAttached = false;
let activeEditableEl = null;
let domObserver = null;
let textMutationObserver = null;
let observedEditableEl = null;
let forceRecheckIntervalId = null;
let popupLoadingState = false;
let popupLastMainHtml = "";
let popupLastDecisionClass = "";
const editableListenerTargets = new WeakSet();

function debugLog(message, details) {
  if (details === undefined) {
    console.debug("[PromptHelper]", message);
    return;
  }
  console.debug("[PromptHelper]", message, details);
}

function isEditableElement(el) {
  return !!el && el.isConnected && (el.tagName === "TEXTAREA" || el.isContentEditable);
}

function listEditableElements() {
  try {
    return Array.from(document.querySelectorAll(EDITABLE_SELECTOR));
  } catch (_) {
    return [];
  }
}

function isFocusedElement(el) {
  if (!isEditableElement(el)) return false;
  const active = document.activeElement;
  return el === active || (!!active && typeof el.contains === "function" && el.contains(active));
}

function getCurrentEditable(preferredTarget = null) {
  if (isFocusedElement(preferredTarget)) return preferredTarget;
  if (isFocusedElement(document.activeElement)) return document.activeElement;
  if (isFocusedElement(activeEditableEl)) return activeEditableEl;

  const focused = listEditableElements().find((el) => isFocusedElement(el));
  if (focused) return focused;
  return null;
}

function getInputText(el) {
  if (!el) return "";
  if (el.tagName === "TEXTAREA") return el.value || "";
  return (el.innerText || el.textContent || "").trim();
}

function getDisplayText(value, fallback = "N/A") {
  if (typeof value !== "string") return fallback;

  const trimmed = value.trim();
  return trimmed || fallback;
}

function getDisplayNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "N/A";
}

function normalizeApiResponse(data = {}) {
  const scores = data?.scores || {};
  const breakdown = data?.breakdown || {};
  const overallRaw = data?.score ?? ((Number(data?.ed_score ?? scores?.ed) + Number(data?.sq_score ?? scores?.sq)) / 2);
  const overall = Number.isFinite(Number(overallRaw)) ? Number(overallRaw) : 0;
  const clarityRaw = Number(breakdown?.clarity ?? 0);
  const structureRaw = Number(breakdown?.structure ?? 0);
  const actionabilityRaw = Number(breakdown?.actionability ?? 0);
  return {
    decision: getDisplayText(data?.decision).toUpperCase(),
    overall,
    overallText: getDisplayNumber(overall),
    ed: getDisplayNumber(data?.ed_score ?? scores?.ed),
    sq: getDisplayNumber(data?.sq_score ?? scores?.sq),
    clarity: getDisplayNumber(clarityRaw),
    structure: getDisplayNumber(structureRaw),
    actionability: getDisplayNumber(actionabilityRaw),
    clarityRaw: Number.isFinite(clarityRaw) ? clarityRaw : 0,
    structureRaw: Number.isFinite(structureRaw) ? structureRaw : 0,
    actionabilityRaw: Number.isFinite(actionabilityRaw) ? actionabilityRaw : 0,
    reason: getDisplayText(data?.reason),
    suggestion: getDisplayText(data?.suggestion),
  };
}

function unwrapAnalyzeResponse(raw) {
  if (!raw || typeof raw !== "object") return null;
  if (raw.result && typeof raw.result === "object") return raw.result;
  if (raw.data && typeof raw.data === "object") return raw.data;
  return raw;
}

function isUsableAnalyzeResponse(data) {
  if (!data || typeof data !== "object") {
    return { ok: false, reason: "response is null or not an object" };
  }
  if (typeof data.error === "string" && data.error.trim()) {
    return { ok: false, reason: `backend error: ${data.error}` };
  }
  if (typeof data.decision !== "string" || !data.decision.trim()) {
    return { ok: false, reason: "missing decision field" };
  }
  return { ok: true };
}

function decisionClass(decisionRaw) {
  const d = String(decisionRaw || "").toLowerCase();
  if (d === "accept") return "prompt-helper-decision-accept";
  if (d === "reject") return "prompt-helper-decision-reject";
  if (d === "review") return "prompt-helper-decision-review";
  return "prompt-helper-decision-error";
}

function normalizePrompt(text) {
  return String(text || "")
    .toLowerCase()
    .trim()
    .replace(/\s+/g, " ");
}

function currentFieldNorm() {
  const el = getCurrentEditable();
  return normalizePrompt(getInputText(el || {}));
}

function getActiveInput() {
  return getCurrentEditable();
}

function shouldSkipEvaluation(text) {
  if (text.length < MIN_CHARS) return true;
  const norm = normalizePrompt(text);
  if (!norm) return true;
  const now = Date.now();
  if (lastSentNorm && norm === lastSentNorm && (now - lastSentAtMs) < EXACT_DUPLICATE_COOLDOWN_MS) {
    return true;
  }
  return false;
}

function applyDefaultPopupPosition() {
  if (!popupEl) return;
  popupEl.style.transform = "none";
  popupEl.style.position = "fixed";
  popupEl.style.top = `${FIXED_POPUP_TOP_PX}px`;
  popupEl.style.right = `${FIXED_POPUP_RIGHT_PX}px`;
  popupEl.style.left = "auto";
  popupEl.style.bottom = "auto";
}

function clampPopupToViewport() {
  if (!popupEl) return;
  const rect = popupEl.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let left = rect.left;
  let top = rect.top;
  const pw = popupEl.offsetWidth || rect.width;
  const ph = popupEl.offsetHeight || rect.height;
  const maxLeft = Math.max(0, vw - pw);
  const maxTop = Math.max(0, vh - ph);
  left = Math.max(0, Math.min(left, maxLeft));
  top = Math.max(0, Math.min(top, maxTop));
  popupEl.style.transform = "none";
  popupEl.style.left = `${Math.round(left)}px`;
  popupEl.style.top = `${Math.round(top)}px`;
  popupEl.style.right = "auto";
  popupEl.style.bottom = "auto";
}

function applyPopupPosition() {
  if (!popupEl) return;
  applyDefaultPopupPosition();
}

function removePopup() {
  if (!popupEl) {
    const existing = document.getElementById("prompt-helper-box");
    if (!existing) return;
    popupEl = existing;
  }
  popupEl.classList.remove("prompt-helper-visible");
  endDragIfNeeded();
  setLoading(false);
  setMainHtml("");
  popupLastDecisionClass = "";
  popupEl.classList.remove(
    "prompt-helper-decision-accept",
    "prompt-helper-decision-reject",
    "prompt-helper-decision-review",
    "prompt-helper-decision-error",
  );
}

function endDragIfNeeded() {
  if (!dragState) return;
  dragState = null;
  document.body.classList.remove("prompt-helper-dragging");
  document.removeEventListener("mousemove", onDragMove, true);
  document.removeEventListener("mouseup", onDragEnd, true);
  if (popupEl) popupEl.classList.remove("prompt-helper-dragging");
}

function onDragMove(e) {
  if (!dragState || !popupEl) return;
  e.preventDefault();
  const dx = e.clientX - dragState.startX;
  const dy = e.clientY - dragState.startY;
  let left = dragState.origLeft + dx;
  let top = dragState.origTop + dy;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const pw = popupEl.offsetWidth;
  const ph = popupEl.offsetHeight;
  left = Math.max(0, Math.min(left, vw - pw));
  top = Math.max(0, Math.min(top, vh - ph));
  popupEl.style.transform = "none";
  popupEl.style.left = `${Math.round(left)}px`;
  popupEl.style.top = `${Math.round(top)}px`;
  popupEl.style.right = "auto";
  popupEl.style.bottom = "auto";
}

function onDragEnd() {
  endDragIfNeeded();
}

function onHeaderMouseDown() {
  // Keep widget fixed to top-right for stable UX in ChatGPT.
}

function disconnectEditableTextObserver() {
  if (textMutationObserver) {
    try {
      textMutationObserver.disconnect();
    } catch (_) {}
  }
  textMutationObserver = null;
  observedEditableEl = null;
}

function observeEditableTextChanges(el) {
  if (!isEditableElement(el) || el.tagName === "TEXTAREA") {
    disconnectEditableTextObserver();
    return;
  }
  if (observedEditableEl === el && textMutationObserver) return;

  disconnectEditableTextObserver();
  observedEditableEl = el;
  let lastObservedText = getInputText(el);
  textMutationObserver = new MutationObserver(() => {
    if (!isFocusedElement(el)) return;
    const nextText = getInputText(el);
    if (nextText === lastObservedText) return;
    lastObservedText = nextText;
    debugLog("Contenteditable mutation detected", { length: nextText.length });
    handleTyping(el, "mutation");
  });
  textMutationObserver.observe(el, {
    childList: true,
    subtree: true,
    characterData: true,
  });
}

function setDecisionClass(decisionKey) {
  if (!popupEl) return;
  const nextClass = decisionClass(decisionKey);
  if (popupLastDecisionClass === nextClass) return;
  popupEl.classList.remove(
    "prompt-helper-decision-accept",
    "prompt-helper-decision-reject",
    "prompt-helper-decision-review",
    "prompt-helper-decision-error",
  );
  popupEl.classList.add(nextClass);
  popupLastDecisionClass = nextClass;
}

function setLoading(on) {
  if (!popupEl) return;
  if (popupLoadingState === !!on) return;
  popupLoadingState = !!on;
  const strip = popupEl.querySelector(".prompt-helper-loading");
  if (!strip) return;
  if (on) {
    strip.textContent = "Analyzing...";
    strip.classList.add("prompt-helper-loading-visible");
  } else {
    strip.textContent = "";
    strip.classList.remove("prompt-helper-loading-visible");
  }
}

function setMainHtml(html) {
  if (!popupEl) return;
  const next = String(html || "");
  if (popupLastMainHtml === next) return;
  const main = popupEl.querySelector(".prompt-helper-main");
  if (main) {
    main.innerHTML = next;
    popupLastMainHtml = next;
  }
}

function getOrCreatePopup() {
  let box = document.getElementById("prompt-helper-box");
  if (box) {
    if (box.parentNode !== document.body) {
      document.body.appendChild(box);
    }
    popupEl = box;
    return box;
  }

  const stray = document.querySelectorAll('[id="prompt-helper-box"]');
  stray.forEach((n) => n.remove());

  box = document.createElement("div");
  box.id = "prompt-helper-box";
  box.setAttribute("role", "status");
  box.innerHTML =
    '<div class="prompt-helper-header">' +
    '<span class="prompt-helper-title">Prompt quality</span>' +
    '<button type="button" class="prompt-helper-close" aria-label="Close">×</button>' +
    "</div>" +
    '<div class="prompt-helper-body">' +
    '<div class="prompt-helper-loading" aria-live="polite"></div>' +
    '<div class="prompt-helper-main"></div>' +
    "</div>";
  document.body.appendChild(box);
  popupEl = box;
  popupLoadingState = false;
  popupLastMainHtml = "";
  popupLastDecisionClass = "";

  const header = box.querySelector(".prompt-helper-header");
  header.addEventListener("mousedown", onHeaderMouseDown);
  box.querySelector(".prompt-helper-close").addEventListener("click", () => {
    removePopup();
  });
  applyPopupPosition();
  return box;
}

function showPopupContent(html, opts) {
  const visible = opts && opts.visible !== false;
  const box = getOrCreatePopup();
  setLoading(false);
  setMainHtml(html);
  applyPopupPosition();
  if (visible) {
    box.classList.add("prompt-helper-visible");
  }
}

function showResult(data) {
  const box = getOrCreatePopup();
  setLoading(false);
  const {
    decision,
    overallText,
    ed,
    sq,
    clarity,
    structure,
    actionability,
    clarityRaw,
    structureRaw,
    actionabilityRaw,
    reason,
    suggestion,
  } = normalizeApiResponse(data);
  setDecisionClass(decision);
  const isReject = String(decision || "").toLowerCase() === "reject";
  const toPct = (v) => `${Math.max(0, Math.min(100, Math.round(Number(v || 0) * 100)))}%`;
  const bar = (label, textValue, rawValue) =>
    '<div class="prompt-helper-breakdown-row">' +
      '<div class="prompt-helper-breakdown-top">' +
        `<span class="prompt-helper-breakdown-label">${escapeHtml(label)}</span>` +
        `<span class="prompt-helper-breakdown-value">${escapeHtml(textValue)}</span>` +
      "</div>" +
      '<div class="prompt-helper-bar-track">' +
        `<div class="prompt-helper-bar-fill" style="width:${toPct(rawValue)}"></div>` +
      "</div>" +
    "</div>";
  const bodyHtml =
    '<div class="prompt-helper-decision">' +
    escapeHtml(decision) +
    "</div>" +
    '<div class="prompt-helper-meta">Overall score: ' +
    escapeHtml(overallText) +
    " · ED: " +
    escapeHtml(ed) +
    " · SQ: " +
    escapeHtml(sq) +
    "</div>" +
    '<div class="prompt-helper-breakdown">' +
      bar("Clarity", clarity, clarityRaw) +
      bar("Structure", structure, structureRaw) +
      bar("Actionability", actionability, actionabilityRaw) +
    "</div>" +
    '<div class="prompt-helper-reason"><span class="prompt-helper-label">Reason</span> ' +
    escapeHtml(reason) +
    "</div>" +
    (isReject
      ? '<div class="prompt-helper-suggestion"><span class="prompt-helper-label">Improved Prompt Suggestion</span> ' +
        escapeHtml(suggestion) +
        "</div>"
      : '<div class="prompt-helper-suggestion"><span class="prompt-helper-label">Suggestion</span> ' +
        escapeHtml(suggestion) +
        "</div>");
  setMainHtml(bodyHtml);
  applyPopupPosition();
  box.classList.add("prompt-helper-visible");
  console.log("[PromptHelper] UI UPDATED", data);
}

function showLocalModeWithReason(reason, details) {
  debugLog("Falling back to local mode", {
    reason,
    details: details || null,
  });
  setDecisionClass("review");
  setLoading(false);
  setMainHtml(
    '<div class="prompt-helper-decision">' +
      escapeHtml("LOCAL MODE ONLY") +
      "</div>" +
    '<div class="prompt-helper-meta prompt-helper-api-msg">' +
      escapeHtml(reason || "Local mode only") +
      "</div>",
  );
  getOrCreatePopup().classList.add("prompt-helper-visible");
  applyPopupPosition();
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function abortActiveFetchIfDifferent(norm) {
  if (
    activeFetchController &&
    inFlightNorm &&
    inFlightNorm !== norm
  ) {
    try {
      activeFetchController.abort();
    } catch (_) {}
    activeFetchController = null;
    inFlightNorm = "";
  }
}

async function runAnalyze(text, opts = {}) {
  const force = !!opts.force;
  const source = opts.source || "typing";
  const norm = normalizePrompt(text);
  if (
    activeFetchController &&
    inFlightNorm === norm &&
    !force
  ) {
    debugLog("Skipped API call (same prompt already in flight)", {
      length: text.length,
    });
    return;
  }

  if (force && activeFetchController) {
    try {
      activeFetchController.abort();
    } catch (_) {}
    activeFetchController = null;
    inFlightNorm = "";
  }

  abortActiveFetchIfDifferent(norm);

  const c = new AbortController();
  activeFetchController = c;
  inFlightNorm = norm;

  const box = getOrCreatePopup();
  setLoading(true);
  applyPopupPosition();
  box.classList.add("prompt-helper-visible");

  try {
    const apiBase = await getApiBase();
    const url = apiBase === DEFAULT_API_BASE ? ANALYZE_URL : apiBase + "/analyze";
    const requestId = `req-${Date.now()}-${++analyzeRequestSeq}`;
    lastSentNorm = norm;
    lastSentAtMs = Date.now();
    console.log("[PromptHelper] SENDING:", text);
    debugLog("Outgoing message to background", {
      requestId,
      type: "ANALYZE",
      source,
      url,
      length: text.length,
      force,
    });
    const response = await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "ANALYZE", prompt: text, requestId }, (res) => {
        if (chrome.runtime.lastError) {
          resolve({
            __transport_error: chrome.runtime.lastError.message || "Runtime messaging error",
            __request_id: requestId,
          });
          return;
        }
        resolve(res || { __request_id: requestId });
      });
    });
    debugLog("Background response received", {
      requestId,
      response,
    });
    if (response && response.__transport_error) {
      throw new Error(response.__transport_error);
    }

    const data = unwrapAnalyzeResponse(response);
    debugLog("Resolved analyze payload", data);
    const validity = isUsableAnalyzeResponse(data);
    if (!validity.ok) {
      throw new Error(validity.reason);
    }

    if (activeFetchController !== c) return;
    activeFetchController = null;
    inFlightNorm = "";
    if (currentFieldNorm() !== norm) {
      debugLog("Discarded stale response (input changed)", { requestId });
      setLoading(false);
      return;
    }

    showResult(data);
  } catch (err) {
    if (err && err.name === "AbortError") {
      debugLog("Analysis request aborted due to newer input", {
        message: err.message || "abort",
      });
      return;
    }
    if (activeFetchController !== c) return;
    activeFetchController = null;
    inFlightNorm = "";
    if (currentFieldNorm() !== norm) {
      debugLog("Error ignored because input already changed", {
        message: err?.message || "unknown error",
      });
      setLoading(false);
      return;
    }
    debugLog("API call failed; switching to offline UI", {
      message: err?.message || "unknown error",
      rawError: err || null,
    });
    showLocalModeWithReason(err?.message || "Invalid API response");
  }
}

function sendForAnalysis(text, source = "submit") {
  const raw = typeof text === "string" ? text : "";
  const trimmed = raw.trim();
  if (!trimmed || trimmed.length < 3) return;
  console.log("[FINAL SENT]", trimmed);
  runAnalyze(trimmed, { force: true, source });
}

function handleTyping(sourceEl = null, trigger = "unknown") {
  try {
    clearTimeout(debounceTimer);
    debounceTimer = null;

    const el = getCurrentEditable(sourceEl);
    if (!el) {
      removePopup();
      return;
    }
    activeEditableEl = el;
    const text = getInputText(el);
    const norm = normalizePrompt(text);
    observeEditableTextChanges(el);
    const textLength = text.length;
    console.log("[TRIGGER SOURCE]", trigger);
    console.log("[TEXT NOW]", text);
    console.log("[NORMALIZED]", norm);
    console.log("[PromptHelper] CURRENT TEXT:", text);
    console.log("[PromptHelper] LAST SENT:", lastSentNorm);
    debugLog("Input detected", { trigger, length: textLength });

    if (!text || text.trim().length === 0) {
      removePopup();
      return;
    }

    getOrCreatePopup().classList.add("prompt-helper-visible");

    if (textLength < MIN_CHARS) {
      debugLog("Input blocked by minimum character threshold", {
        length: textLength,
        minChars: MIN_CHARS,
      });
      setLoading(false);
      setDecisionClass("review");
      setMainHtml(
        '<span class="prompt-helper-pending">Keep typing — need more than ' +
          MIN_CHARS +
          " characters to analyze.</span>",
      );
      applyPopupPosition();
      return;
    }

    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      const el2 = getCurrentEditable(sourceEl);
      if (!el2) {
        removePopup();
        return;
      }
      activeEditableEl = el2;
      const text2 = getInputText(el2);
      const norm2 = normalizePrompt(text2);
      observeEditableTextChanges(el2);
      const text2Length = text2.length;
      console.log("[TRIGGER SOURCE]", trigger);
      console.log("[TEXT NOW]", text2);
      console.log("[NORMALIZED]", norm2);
      console.log("[PromptHelper] CURRENT TEXT:", text2);
      console.log("[PromptHelper] LAST SENT:", lastSentNorm);

      if (!text2 || text2.trim().length === 0) {
        removePopup();
        return;
      }

      getOrCreatePopup().classList.add("prompt-helper-visible");

      if (text2Length < MIN_CHARS) {
        debugLog("Input blocked by minimum character threshold", {
          length: text2Length,
          minChars: MIN_CHARS,
        });
        setLoading(false);
        setDecisionClass("review");
        setMainHtml(
          '<span class="prompt-helper-pending">Keep typing — need more than ' +
            MIN_CHARS +
            " characters to analyze.</span>",
        );
        applyPopupPosition();
        return;
      }

      if (shouldSkipEvaluation(text2)) {
        console.log("[PromptHelper] SKIPPED:", text2);
        setLoading(false);
        setMainHtml(
          '<span class="prompt-helper-pending">Unchanged duplicate (2s cooldown).</span>',
        );
        applyPopupPosition();
        return;
      }

      debugLog("Analysis triggered", { length: text2Length });
      runAnalyze(text2);
    }, DEBOUNCE_MS);
  } catch (_) {
  }
}

function onResize() {
  if (popupEl) applyPopupPosition();
}

function onVisibilityChange() {
  if (document.hidden) {
    disconnectEditableTextObserver();
    removePopup();
    return;
  }
  const el = getCurrentEditable();
  if (!el) return;
  const text = getInputText(el);
  if (!text) return;
  handleTyping(el, "visibilitychange");
}

function onGlobalInput(e) {
  const target = e && e.target ? e.target : null;
  if (!(target instanceof Element)) return;
  const el = target.closest('[contenteditable="true"], textarea');
  if (!el) return;
  const text = getInputText(el);
  console.log("[FORCE INPUT EVENT]", text);
  activeEditableEl = el;
  handleTyping(el, "input");
}

function onGlobalKeydown(e) {
  if (!e || e.key !== "Enter" || e.shiftKey) return;
  const el = getCurrentEditable() || document.querySelector('[contenteditable="true"], textarea');
  if (!el) return;
  const text = getInputText(el);
  console.log("[SUBMIT DETECTED]", text);
  sendForAnalysis(text, "submit_enter");
}

function onGlobalClick(e) {
  const target = e && e.target ? e.target : null;
  if (!(target instanceof Element)) return;
  const btn = target.closest('button[data-testid="send-button"]');
  if (!btn) return;
  const el = getCurrentEditable() || document.querySelector('[contenteditable="true"], textarea');
  if (!el) return;
  const text = getInputText(el);
  console.log("[CLICK SUBMIT]", text);
  sendForAnalysis(text, "submit_click");
}

function startForceRecheckInterval() {
  if (forceRecheckIntervalId) return;
  forceRecheckIntervalId = setInterval(() => {
    if (document.hidden) return;
    const el = getActiveInput();
    if (!el) return;
    const text = getInputText(el);
    if (!text || text.trim().length === 0) return;
    handleTyping(el, "interval");
  }, 2000);
}

function attachElementListeners(el) {
  if (!isEditableElement(el)) return;
  if (editableListenerTargets.has(el)) return;
  editableListenerTargets.add(el);
  debugLog("Editable input mounted", {
    tag: el.tagName ? String(el.tagName).toLowerCase() : "unknown",
  });
  el.addEventListener("input", () => {
    if (!isFocusedElement(el)) return;
    activeEditableEl = el;
    handleTyping(el, "input");
  }, true);
  el.addEventListener("keyup", () => {
    if (!isFocusedElement(el)) return;
    activeEditableEl = el;
    handleTyping(el, "keyup");
  }, true);
  el.addEventListener("focus", () => {
    if (!isFocusedElement(el)) return;
    activeEditableEl = el;
    observeEditableTextChanges(el);
    handleTyping(el, "focus");
  }, true);
}

function attachListenersToCurrentInputs() {
  const inputs = listEditableElements();
  for (const el of inputs) {
    attachElementListeners(el);
  }
}

function observeEditableInputs() {
  if (domObserver) return;
  domObserver = new MutationObserver((mutations) => {
    let foundNewEditable = false;
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        if (!(node instanceof Element)) continue;
        if (isEditableElement(node)) {
          attachElementListeners(node);
          foundNewEditable = true;
        }
        const nested = node.querySelectorAll(EDITABLE_SELECTOR);
        if (nested.length > 0) {
          foundNewEditable = true;
          nested.forEach((el) => attachElementListeners(el));
        }
      }
    }
    if (foundNewEditable) {
      debugLog("Dynamic input update detected");
    }
  });
  domObserver.observe(document.documentElement || document.body, {
    childList: true,
    subtree: true,
  });
}

function attachListeners() {
  if (listenersAttached) return;
  listenersAttached = true;
  attachListenersToCurrentInputs();
  observeEditableInputs();
  window.addEventListener("resize", onResize, true);
  document.addEventListener("visibilitychange", onVisibilityChange, true);
  document.addEventListener("input", onGlobalInput, true);
  document.addEventListener("keydown", onGlobalKeydown, true);
  document.addEventListener("click", onGlobalClick, true);
  startForceRecheckInterval();
  debugLog("Content script listeners attached");
}

function init() {
  attachListeners();
}

try {
  init();
} catch (_) {
}
