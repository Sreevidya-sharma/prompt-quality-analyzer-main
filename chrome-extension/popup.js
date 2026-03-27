const API_BASE = "https://prompt-quality-analyzer.onrender.com";

function updateAuthUI() {
  const authForm = document.getElementById("auth-form");
  const userInfo = document.getElementById("user-info");
  const authStatus = document.getElementById("auth-status");
  if (!authForm || !userInfo) return;

  chrome.storage.local.get(["user"], (r) => {
    const u = r.user;
    if (u && u.email) {
      authForm.hidden = true;
      userInfo.hidden = false;
      userInfo.textContent = u.email;
      if (authStatus) authStatus.style.color = "#16a34a";
    } else {
      authForm.hidden = false;
      userInfo.textContent = "";
      userInfo.hidden = true;
      if (authStatus) authStatus.style.color = "#b91c1c";
    }
  });
}

function parseErrorResponse(res) {
  return res
    .text()
    .then((raw) => {
      if (!raw) return `HTTP ${res.status}`;
      try {
        const data = JSON.parse(raw);
        if (data && typeof data.detail === "string") return data.detail;
        return `HTTP ${res.status}: ${raw}`;
      } catch {
        return `HTTP ${res.status}: ${raw}`;
      }
    })
    .catch(() => `HTTP ${res.status}`);
}

function authFlow(path, email, password, authStatus) {
  fetch(API_BASE + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ email, password })
  })
    .then((res) => {
      if (!res.ok) {
        return parseErrorResponse(res).then((msg) => {
          throw new Error(msg);
        });
      }
      return res.json();
    })
    .then((data) => {
      chrome.storage.local.set({ user: data, email: data.email }, () => {
        updateAuthUI();
        authStatus.style.color = "#16a34a";
        authStatus.textContent =
          path === "/auth/register" ? "Registered and signed in." : "Signed in.";
        setTimeout(() => {
          authStatus.textContent = "";
        }, 2500);
      });
    })
    .catch((e) => {
      authStatus.style.color = "#b91c1c";
      authStatus.textContent = e.message || String(e);
    });
}

document.addEventListener("DOMContentLoaded", () => {
  const loginBtn = document.getElementById("login-btn");
  const registerBtn = document.getElementById("register-btn");
  const emailInput = document.getElementById("email");
  const passwordInput = document.getElementById("password");
  const authStatus = document.getElementById("auth-status");
  const openDashboard = document.getElementById("openDashboard");

  updateAuthUI();

  loginBtn.addEventListener("click", () => {
    authStatus.textContent = "";
    const email = String(emailInput.value || "").trim().toLowerCase();
    const password = String(passwordInput.value || "");
    if (!email || !password) {
      authStatus.style.color = "#b91c1c";
      authStatus.textContent = "Enter email and password.";
      return;
    }
    authFlow("/auth/login", email, password, authStatus);
  });

  registerBtn.addEventListener("click", () => {
    authStatus.textContent = "";
    const email = String(emailInput.value || "").trim().toLowerCase();
    const password = String(passwordInput.value || "");
    if (!email || !password) {
      authStatus.style.color = "#b91c1c";
      authStatus.textContent = "Enter email and password.";
      return;
    }
    authFlow("/auth/register", email, password, authStatus);
  });

  openDashboard.addEventListener("click", async () => {
    const userId = await getUserId();
    chrome.tabs.create({
      url: `${API_BASE}/dashboard?user_id=${encodeURIComponent(userId)}`
    });
  });
});