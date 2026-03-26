const BASE_URL = "https://prompt-quality-analyzer.onrender.com";
const API_URL = `${BASE_URL}/analyze`;
const API_TIMEOUT_MS = 30000;

console.log("🔥 Background script loaded");

function getUserId() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(["userId"], (res) => {
      if (res.userId) return resolve(res.userId);

      const newId = "user_" + crypto.randomUUID();
      chrome.storage.sync.set({ userId: newId }, () => {
        resolve(newId);
      });
    });
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("📩 [BG] Message received:", msg);

  if (msg.type === "PING") {
    sendResponse({ ok: true });
    return;
  }

  if (msg.type === "ANALYZE") {
    const prompt = typeof msg.prompt === "string" ? msg.prompt.trim() : "";

    if (!prompt) {
      console.warn("⚠️ Empty prompt");
      sendResponse(null);
      return;
    }

    console.log("🚀 CALLING API:", API_URL);
    console.log("🧠 PROMPT:", prompt);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_MS);

    (async () => {
      try {
        const userId = await getUserId();
        fetch(API_URL, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-user-id": userId,
          },
          body: JSON.stringify({ text: prompt }),
          signal: controller.signal,
        })
          .then(async (res) => {
            clearTimeout(timeoutId);

            console.log("📡 STATUS:", res.status);

            const raw = await res.text();
            console.log("📦 RAW RESPONSE:", raw);

            let data = null;
            try {
              data = raw ? JSON.parse(raw) : null;
            } catch (e) {
              console.error("❌ JSON PARSE ERROR:", e);
            }

            if (!res.ok) {
              console.error("❌ API ERROR:", data || raw);
              sendResponse(null);
              return;
            }

            console.log("✅ FINAL DATA:", data);
            sendResponse(data);
          })
          .catch((err) => {
            clearTimeout(timeoutId);
            console.error("❌ FETCH FAILED:", err);
            sendResponse(null);
          });
      } catch (err) {
        clearTimeout(timeoutId);
        console.error("❌ USER ID RESOLVE FAILED:", err);
        sendResponse(null);
      }
    })();

    return true; // REQUIRED for async
  }
});