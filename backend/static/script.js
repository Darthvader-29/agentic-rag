// ── Token storage (sessionStorage so tokens clear on tab close) ────────────

function getAccess()  { return sessionStorage.getItem("access_token"); }
function getRefresh() { return sessionStorage.getItem("refresh_token"); }
function setTokens(pair) {
  sessionStorage.setItem("access_token",  pair.access_token);
  sessionStorage.setItem("refresh_token", pair.refresh_token);
}
function clearTokens() {
  sessionStorage.removeItem("access_token");
  sessionStorage.removeItem("refresh_token");
}

// ── Auth UI helpers ──────────────────────────────────────────────────────────

let _currentTab = "login";

function switchTab(tab) {
  _currentTab = tab;
  document.getElementById("auth-title").textContent     = tab === "login" ? "Sign in" : "Create account";
  document.getElementById("tab-login").classList.toggle("active",    tab === "login");
  document.getElementById("tab-register").classList.toggle("active", tab === "register");
  document.getElementById("register-fields").style.display = tab === "register" ? "block" : "none";
  document.getElementById("auth-submit-btn").textContent   = tab === "login" ? "Sign in" : "Register";
  document.getElementById("auth-error").textContent = "";
}

async function handleAuth(e) {
  e.preventDefault();
  const errEl = document.getElementById("auth-error");
  errEl.textContent = "";

  const email    = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;

  try {
    if (_currentTab === "register") {
      const username = document.getElementById("username").value.trim();
      const regResp = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, username, password }),
      });
      if (!regResp.ok) {
        const err = await regResp.json();
        throw new Error(err.detail || "Registration failed");
      }
    }

    // Login (also runs after successful registration)
    const loginResp = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!loginResp.ok) {
      const err = await loginResp.json();
      throw new Error(err.detail || "Login failed");
    }

    const tokens = await loginResp.json();
    setTokens(tokens);
    showApp(email);
  } catch (err) {
    errEl.textContent = err.message;
  }
}

function showApp(email) {
  document.getElementById("auth-overlay").style.display    = "none";
  document.getElementById("app-container").style.display   = "flex";
  document.getElementById("user-label").textContent        = email || "Signed in";
}

function showAuth() {
  document.getElementById("auth-overlay").style.display    = "flex";
  document.getElementById("app-container").style.display   = "none";
  clearTokens();
}

function logout() {
  clearTokens();
  showAuth();
}

// ── Try to refresh an expired access token ──────────────────────────────────

async function maybeRefresh() {
  const refresh = getRefresh();
  if (!refresh) return false;
  try {
    const resp = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!resp.ok) return false;
    setTokens(await resp.json());
    return true;
  } catch {
    return false;
  }
}

// ── Authenticated fetch helper ───────────────────────────────────────────────

async function authFetch(url, options = {}) {
  options.headers = options.headers || {};
  options.headers["Authorization"] = `Bearer ${getAccess()}`;

  let resp = await fetch(url, options);

  if (resp.status === 401) {
    // Try refresh once
    if (await maybeRefresh()) {
      options.headers["Authorization"] = `Bearer ${getAccess()}`;
      resp = await fetch(url, options);
    }
    if (resp.status === 401) {
      showAuth();
      throw new Error("Session expired — please sign in again");
    }
  }
  return resp;
}

// ── Session ID ───────────────────────────────────────────────────────────────

let SESSION_ID = crypto.randomUUID();
let uploadedFileKeys = [];

// ── Message helpers ──────────────────────────────────────────────────────────

const chatWindow = document.getElementById("chat-window");
const userInput  = document.getElementById("user-input");
const sendBtn    = document.getElementById("send-btn");
const uploadBtn  = document.getElementById("upload-btn");
const fileInput  = document.getElementById("file-input");
const webToggle  = document.getElementById("web-toggle");

function addMessage(text, role = "bot") {
  const div = document.createElement("div");
  div.className = `message ${role === "user" ? "user" : "bot"}`;
  div.textContent = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

// ── Chat ─────────────────────────────────────────────────────────────────────

async function sendMessage() {
  const text = userInput.value.trim();
  if (!text) return;
  addMessage(text, "user");
  userInput.value = "";

  try {
    const resp = await authFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        session_id: SESSION_ID,
        web_search_allowed: webToggle.checked,
      }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    if (data.session_id) SESSION_ID = data.session_id;
    addMessage(data.answer, "bot");
  } catch (err) {
    addMessage(`Error: ${err.message}`, "bot");
  }
}

sendBtn.addEventListener("click", sendMessage);
userInput.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });

// ── Upload ───────────────────────────────────────────────────────────────────

uploadBtn.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  addMessage(`Uploading ${file.name}…`, "bot");

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", SESSION_ID);

    const resp = await authFetch("/api/upload", { method: "POST", body: formData });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    if (data.session_id) SESSION_ID = data.session_id;
    if (data.s3_key) uploadedFileKeys.push(data.s3_key);
    addMessage(`${file.name} uploaded and ingestion started.`, "bot");
  } catch (err) {
    addMessage(`Upload failed: ${err.message}`, "bot");
  } finally {
    fileInput.value = "";
  }
});

// ── Cleanup on tab close ─────────────────────────────────────────────────────

window.addEventListener("beforeunload", () => {
  if (!SESSION_ID || !getAccess()) return;
  const blob = new Blob(
    [JSON.stringify({ session_id: SESSION_ID })],
    { type: "application/json" }
  );
  // sendBeacon doesn't support custom headers; best-effort cleanup
  navigator.sendBeacon("/api/cleanup", blob);
});

// ── Boot: show auth or app based on stored tokens ───────────────────────────

(async function boot() {
  if (getAccess()) {
    showApp("");  // email not persisted — just show the app
  } else {
    showAuth();
  }
})();
