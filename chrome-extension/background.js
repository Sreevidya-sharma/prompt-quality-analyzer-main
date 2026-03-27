importScripts("user-id.js");

const BASE_URL = "https://prompt-quality-analyzer.onrender.com";
const API_URL = `${BASE_URL}/analyze`;
const API_TIMEOUT_MS = 30000;
const DEV_MODE = false;

function log(...args) {
  if (DEV_MODE) console.log(...args);
}
function error(...args) {
  if (DEV_MODE) console.error(...args);
}
function warn(...args) {
  if (DEV_MODE) console.warn(...args);
}

log("Background script loaded");

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  log("[BG] Message received:", msg);

  if (msg.type === "PING") {
    sendResponse({ ok: true });
    return;
  }

  if (msg.type === "ANALYZE") {
    const prompt = typeof msg.prompt === "string" ? msg.prompt.trim() : "";

    if (!prompt) {
      sendResponse(fallback("Empty prompt"));
      return;
    }

    log("Calling API:", API_URL);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

    (async () => {
      try {
        const userId = await getUserId();

        const res = await fetch(API_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-user-id": userId
          },
          body: JSON.stringify({ text: prompt }),
          signal: controller.signal
        });

        clearTimeout(timeoutId);

        log("Status:", res.status);

        const raw = await res.text();
        log("Raw response:", raw);

        let data = null;
        try {
          data = raw ? JSON.parse(raw) : null;
        } catch (e) {
          error("JSON parse error:", e);
          sendResponse(fallback("Invalid JSON from API"));
          return;
        }

        if (!res.ok || !data) {
          error("API error:", data || raw);
          sendResponse(fallback("API error"));
          return;
        }

        if (!data.decision) {
          warn("Missing decision → fixing");

          data = {
            decision: "review",
            score: data.score || 0,
            reason: data.error || "Invalid response from API",
            suggestion: data.suggestion || "Try rewriting your prompt",
            breakdown: data.breakdown || {
              clarity: 0,
              structure: 0,
              actionability: 0
            }
          };
        }

        log("Final data:", data);
        sendResponse(data);

      } catch (err) {
        clearTimeout(timeoutId);
        error("Fetch failed:", err);
        sendResponse(fallback("Network error"));
      }
    })();

    return true;
  }
});

function fallback(reason) {
  return {
    decision: "review",
    score: 0,
    reason: reason,
    suggestion: "Check your input or try again",
    breakdown: {
      clarity: 0,
      structure: 0,
      actionability: 0
    }
  };
}