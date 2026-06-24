const app = document.querySelector("#app");
const toastHost = document.querySelector("#toast");

const state = {
  token: localStorage.getItem("wuyou.token") || "",
  locale: localStorage.getItem("wuyou.locale") || "zh-CN",
  theme: localStorage.getItem("wuyou.theme") || "light",
  dict: {},
  view: "inbox",
  user: null,
  messages: [],
  accounts: [],
  tags: [],
  selectedMessage: null,
  unread: 0,
  folderRole: "all",
};

const calendarState = {
  currentDate: new Date(),
  viewMode: "month",   // month / week / day
  events: [],
};

const contactsState = { contacts: [], searchQuery: "" };
const tasksState = { tasks: [], viewMode: "kanban", filterStatus: null };
const notesState = { notes: [], viewMode: "grid", filterCategory: null };

const views = [
  ["inbox", "nav.inbox", "收件箱", "Inbox"],
  ["unread", "nav.unread", "未读汇总", "Unread"],
  ["compose", "nav.compose", "写邮件", "Compose"],
  ["accounts", "nav.accounts", "邮箱账户", "Accounts"],
  ["calendar", "nav.calendar", "日历", "Calendar"],
  ["contacts", "nav.contacts", "通讯录", "Contacts"],
  ["tasks", "nav.tasks", "任务", "Tasks"],
  ["notes", "nav.notes", "便签", "Notes"],
  ["plugins", "nav.plugins", "插件社区", "Plugins"],
  ["settings", "nav.settings", "设置", "Settings"],
  ["about", "nav.about", "关于", "About"],
];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function t(key, fallback = key) {
  return state.dict[key] || fallback;
}

async function loadLocale(locale) {
  state.locale = locale || state.locale;
  // 1. 尝试静态文件
  let response = await fetch(`/static/locales/${state.locale}.json`);
  if (!response.ok) {
    // 2. 尝试 API（用户上传的语言包）
    try {
      const data = await api(`/api/locales/${state.locale}`);
      state.dict = data.messages || {};
      document.documentElement.lang = state.locale;
      localStorage.setItem("wuyou.locale", state.locale);
      return;
    } catch {
      // 3. 回退到 zh-CN
      state.locale = "zh-CN";
      localStorage.setItem("wuyou.locale", state.locale);
      response = await fetch("/static/locales/zh-CN.json");
    }
  }
  const data = await response.json();
  state.dict = data.messages || {};
  document.documentElement.lang = state.locale;
  localStorage.setItem("wuyou.locale", state.locale);
}

async function applyTheme() {
  if (state.theme === "light" || state.theme === "dark") {
    document.documentElement.dataset.theme = state.theme;
    localStorage.setItem("wuyou.theme", state.theme);
    return;
  }
  // 自定义主题：从 API 加载 JSON 并注入 CSS 变量
  try {
    const themeData = await api(`/api/themes/${state.theme}`);
    const variables = themeData.variables || {};
    for (const [key, value] of Object.entries(variables)) {
      document.documentElement.style.setProperty(key, value);
    }
    delete document.documentElement.dataset.theme;
    localStorage.setItem("wuyou.theme", state.theme);
  } catch (error) {
    toast(error.message, "error");
    state.theme = "light";
    applyTheme();
  }
}

function toast(message, type = "info") {
  const node = document.createElement("div");
  node.className = `toast ${type}`;
  node.textContent = message;
  toastHost.appendChild(node);
  setTimeout(() => node.remove(), 3600);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    localStorage.removeItem("wuyou.token");
    state.token = "";
    renderAuth();
    throw new Error("请重新登录。");
  }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(data.detail || data.message || data || "请求失败。");
  }
  return data;
}

async function init() {
  await applyTheme();
  await loadLocale(state.locale);
  if (!state.token) {
    renderAuth();
    return;
  }
  try {
    state.user = await api("/api/auth/me");
    await loadCommon();
    renderShell();
    await route("inbox");
  } catch (error) {
    toast(error.message, "error");
    renderAuth();
  }
}

function renderAuth(mode = "register") {
  app.className = "";
  app.innerHTML = `
    <div class="auth-page">
      <div class="auth-card">
        <h1>📮 WuYou</h1>
        <p class="slogan">你的邮件，都在坞里</p>
        <div id="auth-form-area">
          ${authFields(mode)}
        </div>
        <p style="margin-top:16px;text-align:center">
          <a href="javascript:void(0)" id="switch-auth">${mode === "register" ? "已有账号？登录" : "还没有账号？注册"}</a>
        </p>
      </div>
    </div>
  `;

  document.querySelector("#switch-auth").addEventListener("click", (e) => {
    e.preventDefault();
    renderAuth(mode === "register" ? "login" : "register");
  });

  // Tab 切换
  document.querySelectorAll("[data-auth]").forEach((button) => {
    button.addEventListener("click", () => renderAuth(button.dataset.auth));
  });

  document.querySelector("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const payload =
        mode === "login"
          ? { identifier: form.get("identifier"), password: form.get("password") }
          : {
              username: form.get("username") || null,
              email: form.get("email") || null,
              phone: form.get("phone") || null,
              password: form.get("password"),
            };
      const result = await api(`/api/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        body: payload,
      });
      state.token = result.token;
      state.user = result.user;
      localStorage.setItem("wuyou.token", result.token);
      await loadCommon();
      renderShell();
      await route("inbox");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  const sendCodeBtn = document.querySelector("#send-code-btn");
  if (sendCodeBtn) {
    sendCodeBtn.addEventListener("click", async function () {
      const email = document.querySelector("#reg-email")?.value?.trim() || "";
      const phone = document.querySelector("#reg-phone")?.value?.trim() || "";
      if (!email && !phone) {
        toast("请先输入邮箱或手机号", "error");
        return;
      }
      const target = email ? { email } : { phone };
      try {
        const response = await fetch("/api/auth/verification-code", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(target),
        });
        if (response.status === 429) {
          const data = await response.json();
          const secs = parseInt(data.detail) || 60;
          _startCodeCountdown(this, secs);
          return;
        }
        if (response.status === 503) {
          toast(t("auth.smtpNotConfigured", "系统发件邮箱未配置"), "error");
          return;
        }
        if (!response.ok) {
          const data = await response.json();
          throw new Error(data.detail || "发送失败");
        }
        toast(email ? t("auth.codeSentEmail", "验证码已发送至邮箱") : t("auth.codeSentSms", "验证码已发送至手机"));
        _startCodeCountdown(this, 60);
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
}

function authFields(mode) {
  if (mode === "register") {
    return `
      <form class="auth-form" id="auth-form">
        <div class="auth-tabs" style="justify-content:center">
          <button type="button" data-auth="register" class="active">注册</button>
          <button type="button" data-auth="login">登录</button>
        </div>
        <div class="field"><label>${t("auth.username", "用户名")}</label><input name="username" autocomplete="username" /></div>
        <div class="field"><label>${t("auth.email", "邮箱")}</label><input name="email" type="email" autocomplete="email" id="reg-email" /></div>
        <div class="field"><label>${t("auth.phone", "手机号")}</label><input name="phone" autocomplete="tel" id="reg-phone" /></div>
        <div class="field">
          <label>验证码</label>
          <div style="display:flex;gap:8px">
            <input name="veri_code" placeholder="请输入验证码" style="flex:1" />
            <button type="button" class="btn" id="send-code-btn">发送验证码</button>
          </div>
        </div>
        <div class="field"><label>${t("auth.password", "密码")}</label><input name="password" type="password" required minlength="8" autocomplete="new-password" /></div>
        <button class="btn primary" type="submit" style="width:100%">${t("auth.register", "注册")}</button>
      </form>
    `;
  }
  return `
    <form class="auth-form" id="auth-form">
      <div class="auth-tabs" style="justify-content:center">
        <button type="button" data-auth="register">注册</button>
        <button type="button" data-auth="login" class="active">登录</button>
      </div>
      <div class="field"><label>${t("auth.identifier", "用户名 / 邮箱 / 手机号")}</label><input name="identifier" required autocomplete="username" /></div>
      <div class="field"><label>${t("auth.password", "密码")}</label><input name="password" type="password" required autocomplete="current-password" /></div>
      <button class="btn primary" type="submit" style="width:100%">${t("auth.login", "登录")}</button>
    </form>
  `;
}

let _countdownTimer = null;

function _startCodeCountdown(button, seconds) {
  clearInterval(_countdownTimer);
  button.disabled = true;
  let remaining = seconds;
  button.textContent = `${remaining}s`;
  _countdownTimer = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      clearInterval(_countdownTimer);
      button.disabled = false;
      button.textContent = "\u53D1\u9001\u9A8C\u8BC1\u7801";
    } else {
      button.textContent = `${remaining}s`;
    }
  }, 1000);
}

async function loadCommon() {
  const [accounts, tags, unread] = await Promise.all([
    api("/api/accounts"),
    api("/api/mail/tags"),
    api("/api/mail/unread"),
  ]);
  state.accounts = accounts;
  state.tags = tags;
  state.unread = unread.unread;
}

function renderShell() {
  app.className = "";
  app.innerHTML = `
    <div class="app-shell">
      <header class="topbar">
        <div class="brand" style="cursor:pointer" onclick="route('inbox')">📮 WuYou</div>
        <div class="top-actions">
          <select id="locale-select" title="${t("top.language", "语言")}">
            <option value="zh-CN">简体中文</option>
            <option value="zh-TW">繁體中文</option>
            <option value="en-US">English</option>
          </select>
          <button class="btn" id="theme-toggle" title="${t("top.theme", "主题")}">${state.theme === "dark" ? "☀️" : "🌙"}</button>
          <div class="user-menu" style="position:relative">
            <button class="btn avatar-btn" id="user-avatar" title="${esc(state.user?.username||'用户')}">${(state.user?.username||'U')[0].toUpperCase()}</button>
            <div class="dropdown" id="user-dropdown" style="display:none;position:absolute;right:0;top:40px;background:var(--surface);border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.15);padding:8px 0;min-width:140px;z-index:100">
              <a href="javascript:route('settings')" style="display:block;padding:8px 16px;color:var(--text);text-decoration:none">${t("nav.settings","设置")}</a>
              <a href="javascript:route('about')" style="display:block;padding:8px 16px;color:var(--text);text-decoration:none">${t("nav.about","关于")}</a>
              <a href="javascript:doLogout()" style="display:block;padding:8px 16px;color:#e53e3e;text-decoration:none">${t("auth.logout","退出登录")}</a>
            </div>
          </div>
        </div>
      </header>
      <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
          <button class="btn sidebar-collapse-btn" id="sidebar-collapse" title="折叠侧栏">☰</button>
        </div>
        ${views
          .map(
            ([id, key, fallback, icon]) => `
              <button class="nav-button ${state.view === id ? "active" : ""}" data-view="${id}">
                <span class="nav-icon">${icon}</span><span class="nav-label">${t(key, fallback)}</span>${id === "unread" && state.unread ? `<span class="count">${state.unread}</span>` : ""}
              </button>
            `,
          )
          .join("")}
      </aside>
      <main class="workspace" id="workspace"></main>
    </div>
  `;
  document.querySelector("#locale-select").value = state.locale;
  document.querySelector("#locale-select").addEventListener("change", async (event) => {
    state.locale = event.target.value;
    localStorage.setItem("wuyou.locale", state.locale);
    await loadLocale();
    renderShell();
    await route(state.view);
  });
  document.querySelector("#theme-toggle").addEventListener("click", async () => {
    state.theme = state.theme === "dark" ? "light" : "dark";
    await applyTheme();
    renderShell();
    route(state.view);
  });
  document.querySelector("#user-avatar").addEventListener("click", () => {
    const dropdown = document.querySelector("#user-dropdown");
    dropdown.style.display = dropdown.style.display === "none" ? "block" : "none";
  });
  // 点击页面其他区域关闭下拉菜单
  document.addEventListener("click", (e) => {
    const menu = document.querySelector(".user-menu");
    if (menu && !menu.contains(e.target)) {
      const dropdown = document.querySelector("#user-dropdown");
      if (dropdown) dropdown.style.display = "none";
    }
  });
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => route(button.dataset.view)));

  // ── 侧栏折叠按钮 ──
  const sidebarCollapse = document.querySelector("#sidebar-collapse");
  if (sidebarCollapse) {
    sidebarCollapse.addEventListener("click", () => {
      const sidebar = document.querySelector("#sidebar");
      const shell = document.querySelector(".app-shell");
      const isCollapsed = sidebar.classList.toggle("collapsed");
      if (isCollapsed) {
        sidebar.style.width = "48px";
        shell.style.gridTemplateColumns = "48px 1fr";
        sidebar.querySelectorAll(".nav-label, .count").forEach(el => el.style.display = "none");
      } else {
        const savedWidth = localStorage.getItem("wuyou.sidebarWidth") || "180px";
        sidebar.style.width = savedWidth;
        shell.style.gridTemplateColumns = savedWidth + " 1fr";
        sidebar.querySelectorAll(".nav-label, .count").forEach(el => el.style.display = "");
      }
    });
  }

  // 用户菜单 toggle
  document.querySelector("#user-avatar")?.addEventListener("click", () => {
    const dd = document.querySelector("#user-dropdown");
    if (dd) dd.style.display = dd.style.display === "none" ? "block" : "none";
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".user-menu")) {
      const dd = document.querySelector("#user-dropdown");
      if (dd) dd.style.display = "none";
    }
  });

  // ── 分栏拖拽调整 ──
  const sidebar = document.querySelector("#sidebar");
  if (sidebar) {
    const handle = document.createElement("div");
    handle.className = "sidebar-resize-handle";
    handle.style.cssText = "width:4px;cursor:col-resize;background:var(--border);position:absolute;right:0;top:0;bottom:0;z-index:5";
    handle.style.display = "none";
    sidebar.style.position = "relative";
    sidebar.appendChild(handle);

    sidebar.addEventListener("mouseenter", () => {
      if (!sidebar.classList.contains("collapsed")) handle.style.display = "block";
    });
    sidebar.addEventListener("mouseleave", () => { handle.style.display = "none"; });

    let dragging = false, startX = 0, startW = 0;
    handle.addEventListener("mousedown", (e) => {
      dragging = true;
      startX = e.clientX;
      startW = sidebar.offsetWidth;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    });
    document.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const w = Math.max(160, Math.min(400, startW + e.clientX - startX));
      sidebar.style.width = w + "px";
      const shell = document.querySelector(".app-shell");
      if (shell) shell.style.gridTemplateColumns = w + "px 1fr";
    });
    document.addEventListener("mouseup", () => {
      if (dragging) {
        dragging = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        localStorage.setItem("wuyou.sidebarWidth", sidebar.style.width);
      }
    });
  }
}

async function doLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {}
  localStorage.removeItem("wuyou.token");
  state.token = "";
  renderAuth();
}

async function route(view) {
  state.view = view;
  document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  if (view === "inbox" || view === "unread") return renderInbox(view === "unread" ? "unread" : "all");
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace";
  if (view === "compose") return renderCompose();
  if (view === "accounts") return renderAccounts();
  if (view === "calendar") { renderCalendar(); return; }
  if (view === "contacts") { renderContacts(); return; }
  if (view === "tasks") { renderTasks(); return; }
  if (view === "notes") { renderNotes(); return; }
  if (view === "plugins") return renderPlugins();
  if (view === "settings") return renderSettings();
  return renderAbout();
}

async function renderInbox(status = "all", query = "", folderRole = null) {
  if (folderRole !== null) state.folderRole = folderRole;
  const role = state.folderRole;
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace inbox-split";
  workspace.innerHTML = `
    <div class="mail-layout">
      <section class="list-pane">
        <div class="toolbar">
          <input id="mail-search" placeholder="${t("mail.search", "搜索邮件标题、正文或发件人")}" value="${esc(query)}" />
          <button class="btn" id="sync-all">${t("mail.sync", "同步")}</button>
          <button class="btn" id="show-sync-jobs">&#128260; ${t("sync.jobs", "同步任务")}</button>
        </div>
        <div class="folder-tabs">
          ${["all","inbox","sent","trash","archive","junk"].map((r) => {
            const labels = { all: "\u5168\u90E8", inbox: "\u6536\u4EF6\u7BB1", sent: "\u5DF2\u53D1\u9001", trash: "\u5783\u573E\u7BB1", archive: "\u5F52\u6863", junk: "\u5783\u573E\u90AE\u4EF6" };
            return `<button class="folder-tab ${role === r ? "active" : ""}" data-folder="${r}">${labels[r]}</button>`;
          }).join("")}
        </div>
        <div id="mail-list"><div class="empty-state">${t("common.loading", "加载中...")}</div></div>
      </section>
    </div>
    <section class="reader-pane" id="reader"><div class="reader-empty">${t("mail.pick", "选择一封邮件阅读")}</div></section>
  `;
  document.querySelector("#mail-search").addEventListener("keydown", (event) => {
    if (event.key === "Enter") renderInbox(status, event.currentTarget.value.trim(), role);
  });
  document.querySelector("#sync-all").addEventListener("click", syncAll);
  document.querySelector("#show-sync-jobs").addEventListener("click", showSyncJobsModal);
  document.querySelectorAll(".folder-tab").forEach((btn) => {
    btn.addEventListener("click", () => renderInbox(status, query, btn.dataset.folder));
  });
  try {
    const params = new URLSearchParams({ status, q: query, folder_role: role });
    state.messages = await api(`/api/mail/inbox?${params.toString()}`);
    renderMessageList();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderMessageList() {
  const list = document.querySelector("#mail-list");
  if (!state.messages.length) {
    list.innerHTML = `<div class="empty-state">${t("mail.empty", "暂无邮件")}</div>`;
    return;
  }
  list.innerHTML = state.messages
    .map(
      (message) => `
        <article class="mail-row ${message.unread ? "unread" : ""}" data-message="${message.id}">
          <div>
            <div class="mail-sender">${esc(message.sender || t("mail.unknown", "未知发件人"))}</div>
            <div class="mail-subject">${esc(message.subject)}</div>
            <div class="mail-snippet">${esc(message.snippet)}</div>
            <div class="tag-line">${message.tags.map((tag) => `<span class="tag" style="background:${esc(tag.color)}">${esc(tag.name)}</span>`).join("")}</div>
          </div>
          <time class="mail-date">${esc(String(message.received_at).slice(0, 16))}</time>
        </article>
      `,
    )
    .join("");
  document.querySelectorAll("[data-message]").forEach((row) => row.addEventListener("click", () => openMessage(Number(row.dataset.message))));
}

async function openMessage(id) {
  const message = await api(`/api/mail/messages/${id}`);
  state.selectedMessage = message;
  await api(`/api/mail/messages/${id}/read?unread=false`, { method: "POST" });
  state.messages = state.messages.map((item) => (item.id === id ? { ...item, unread: false } : item));
  renderMessageList();
  renderReader(message);
}

function renderReader(message) {
  const htmlAllowed = message.remote_content_allowed && message.body_html;
  const reader = document.querySelector("#reader");
  reader.innerHTML = `
    <div class="reader-head">
      <h2>${esc(message.subject)}</h2>
      <div class="muted">${esc(message.sender)} · ${esc(message.received_at)}</div>
      <div class="toolbar">
        <button class="btn" id="translate-mail">${t("mail.translate", "翻译")}</button>
        ${
          message.body_html && !message.remote_content_allowed
            ? `<button class="btn" id="allow-remote">${t("mail.loadRemote", "加载远程内容")}</button>`
            : ""
        }
      </div>
    </div>
    <div class="reader-body">
      ${
        message.body_html && !message.remote_content_allowed
          ? `<div class="remote-warning"><span>${t("mail.remoteBlocked", "远程图片和追踪内容已默认阻止。")}</span></div>`
          : ""
      }
      ${htmlAllowed ? `<iframe sandbox="" srcdoc="${esc(message.body_html)}"></iframe>` : `<pre>${esc(message.body_text || message.snippet)}</pre>`}
      ${message.attachments.length ? `<h3>${t("mail.attachments", "附件")}</h3>${message.attachments.map((item) => `<p>${esc(item.filename)} · ${esc(item.content_type)} · ${item.size || 0} bytes</p>`).join("")}` : ""}
    </div>
  `;
  const allowRemote = document.querySelector("#allow-remote");
  if (allowRemote) {
    allowRemote.addEventListener("click", async () => {
      await api(`/api/mail/messages/${message.id}/remote-content?allowed=true`, { method: "POST" });
      const updated = await api(`/api/mail/messages/${message.id}`);
      renderReader(updated);
    });
  }
  document.querySelector("#translate-mail").addEventListener("click", async () => {
    try {
      const result = await api("/api/translate", {
        method: "POST",
        body: { text: message.body_text || message.snippet, source_lang: "en", target_lang: state.locale, provider: "auto" },
      });
      reader.querySelector(".reader-body").insertAdjacentHTML(
        "afterbegin",
        `<div class="item-card"><h3>${t("mail.translation", "翻译结果")}</h3><pre>${esc(result.translated_text)}</pre></div>`,
      );
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

function pollJobStatus(jobId, onUpdate) {
  let attempts = 0;
  const maxAttempts = 60;
  const timer = setInterval(async () => {
    attempts++;
    try {
      const job = await api(`/api/sync/jobs/${jobId}`);
      if (job.status === "success" || job.status === "failed" || job.status === "completed") {
        clearInterval(timer);
        onUpdate(job);
        return;
      }
      if (attempts >= maxAttempts) {
        clearInterval(timer);
        onUpdate(null);
        return;
      }
    } catch {
      if (attempts >= maxAttempts) {
        clearInterval(timer);
        onUpdate(null);
      }
    }
  }, 3000);
}

async function syncAll() {
  if (!state.accounts.length) {
    toast(t("accounts.needFirst", "请先添加邮箱账户。"), "error");
    return;
  }
  for (const account of state.accounts) {
    const toastId = toast(`${account.display_name}: ${t("mail.syncing", "正在同步...")}`, "info");
    try {
      const result = await api(`/api/accounts/${account.id}/sync`, { method: "POST" });
      if (result.job_id) {
        const updateToast = (msg) => {
          const nodes = toastHost.querySelectorAll(".toast");
          const last = nodes[nodes.length - 1];
          if (last) last.textContent = msg;
        };
        updateToast(`${account.display_name}: ${t("mail.syncing", "正在同步...")}`);
        pollJobStatus(result.job_id, (job) => {
          if (!job) {
            updateToast(`${account.display_name}: \u540C\u6B65\u8D85\u65F6\uFF0C\u8BF7\u624B\u52A8\u5237\u65B0\u3002`);
            return;
          }
          if (job.status === "success" || job.status === "completed") {
            const stats = job.stats_json ? (typeof job.stats_json === "string" ? JSON.parse(job.stats_json) : job.stats_json) : {};
            const newCount = stats.new_messages || 0;
            updateToast(`${account.display_name}: \u540C\u6B65\u5B8C\u6210\uFF08\u65B0\u589E${newCount}\u5C01\uFF09`);
            loadCommon().then(() => {
              renderShell();
              route(state.view);
            });
          } else if (job.status === "failed") {
            updateToast(`${account.display_name}: \u540C\u6B65\u5931\u8D25\uFF1A${job.error || "\u672A\u77E5\u9519\u8BEF"}`);
          }
        });
      } else {
        toast(`${account.display_name}: ${result.message}`);
      }
    } catch (error) {
      toast(`${account.display_name}: ${error.message}`, "error");
    }
  }
}

async function renderCalendar() {
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace";

  const year = calendarState.currentDate.getFullYear();
  const month = calendarState.currentDate.getMonth();

  const fromDate = `${year}-${String(month + 1).padStart(2, "0")}-01`;
  const lastDay = new Date(year, month + 1, 0).getDate();
  const toDate = `${year}-${String(month + 1).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`;

  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("nav.calendar", "日历")}</h2></div>
      <div class="toolbar">
        <button class="btn" id="cal-prev">← ${t("calendar.prev", "上一月")}</button>
        <span class="cal-title">${year}年${month + 1}月</span>
        <button class="btn" id="cal-next">${t("calendar.next", "下一月")} →</button>
        <button class="btn primary" id="cal-today">${t("calendar.today", "今天")}</button>
        <select id="cal-view-mode">
          <option value="month" ${calendarState.viewMode === "month" ? "selected" : ""}>${t("calendar.month", "月")}</option>
          <option value="week" ${calendarState.viewMode === "week" ? "selected" : ""}>${t("calendar.week", "周")}</option>
          <option value="day" ${calendarState.viewMode === "day" ? "selected" : ""}>${t("calendar.day", "日")}</option>
        </select>
        <button class="btn primary" id="cal-new-event">+ ${t("calendar.newEvent", "新建事件")}</button>
      </div>
      <div id="cal-grid" class="cal-grid-wrapper">${t("calendar.loading", "加载日历...")}</div>
    </section>
  `;

  document.querySelector("#cal-prev").addEventListener("click", () => {
    calendarState.currentDate.setMonth(calendarState.currentDate.getMonth() - 1);
    renderCalendar();
  });
  document.querySelector("#cal-next").addEventListener("click", () => {
    calendarState.currentDate.setMonth(calendarState.currentDate.getMonth() + 1);
    renderCalendar();
  });
  document.querySelector("#cal-today").addEventListener("click", () => {
    calendarState.currentDate = new Date();
    renderCalendar();
  });
  document.querySelector("#cal-view-mode").addEventListener("change", (e) => {
    calendarState.viewMode = e.target.value;
    renderCalendar();
  });
  document.querySelector("#cal-new-event").addEventListener("click", () => showEventModal());

  try {
    const data = await api(`/api/items?kind=calendar_event&from_date=${fromDate}&to_date=${toDate}`);
    calendarState.events = data.items || [];
  } catch (error) {
    toast(error.message, "error");
    calendarState.events = [];
  }

  if (calendarState.viewMode === "month") {
    renderMonthGrid(year, month);
  } else if (calendarState.viewMode === "week") {
    renderWeekGrid(year, month);
  } else {
    renderDayGrid(year, month);
  }
}

function renderMonthGrid(year, month) {
  const grid = document.querySelector("#cal-grid");
  const today = new Date();

  const eventMap = {};
  calendarState.events.forEach((ev) => {
    const meta = ev.meta_json || {};
    const startDate = meta.start_at || "";
    const dateKey = String(startDate).slice(0, 10);
    if (!eventMap[dateKey]) eventMap[dateKey] = [];
    eventMap[dateKey].push(ev);
  });

  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrevMonth = new Date(year, month, 0).getDate();
  const startOffset = firstDay === 0 ? 6 : firstDay - 1;

  let html = '<div class="cal-weekdays">';
  ["一", "二", "三", "四", "五", "六", "日"].forEach((d) => {
    html += `<div class="cal-weekday">${d}</div>`;
  });
  html += "</div>";

  const totalCells = Math.ceil((startOffset + daysInMonth) / 7) * 7;
  let day = 1;

  for (let i = 0; i < totalCells; i++) {
    if (i % 7 === 0) html += '<div class="cal-week">';

    if (i < startOffset) {
      const prevDay = daysInPrevMonth - startOffset + i + 1;
      html += `<div class="cal-day other-month"><span class="cal-day-num">${prevDay}</span></div>`;
    } else if (day > daysInMonth) {
      const nextDay = day - daysInMonth;
      day++;
      html += `<div class="cal-day other-month"><span class="cal-day-num">${nextDay}</span></div>`;
    } else {
      const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      const isToday = today.getFullYear() === year && today.getMonth() === month && today.getDate() === day;
      const events = eventMap[dateStr] || [];

      let dotsHtml = "";
      if (events.length > 0) {
        const showEvents = events.slice(0, 3);
        dotsHtml = showEvents
          .map((ev) => `<span class="cal-dot" data-event-id="${ev.id}" style="background:${esc((ev.meta_json || {}).color || "#4A90D9")}" title="${esc(ev.title || "")}"></span>`)
          .join("");
        if (events.length > 3) {
          dotsHtml += `<span class="cal-dot-more">+${events.length - 3}</span>`;
        }
      }

      html += `<div class="cal-day${isToday ? " today" : ""}" data-date="${dateStr}">
        <span class="cal-day-num">${day}</span>
        <div class="cal-dots">${dotsHtml}</div>
      </div>`;
      day++;
    }

    if ((i + 1) % 7 === 0 || i === totalCells - 1) html += "</div>";
  }

  if (!calendarState.events.length) {
    html += `<div class="empty-state" style="margin-top:12px">${t("calendar.empty", "暂无事件")}</div>`;
  }

  grid.innerHTML = html;

  grid.querySelectorAll(".cal-day:not(.other-month)").forEach((cell) => {
    cell.addEventListener("click", (e) => {
      if (e.target.classList.contains("cal-dot")) return;
      showEventModal(cell.dataset.date);
    });
  });

  grid.querySelectorAll(".cal-dot").forEach((dot) => {
    dot.addEventListener("click", (e) => {
      e.stopPropagation();
      const eventId = Number(dot.dataset.eventId);
      const ev = calendarState.events.find((item) => item.id === eventId);
      if (ev) showEventModal(null, ev);
    });
  });
}

function renderWeekGrid(year, month) {
  const grid = document.querySelector("#cal-grid");
  const today = new Date();

  const startOfWeek = new Date(calendarState.currentDate);
  const dayOfWeek = startOfWeek.getDay();
  const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
  startOfWeek.setDate(startOfWeek.getDate() + mondayOffset);

  const eventMap = {};
  calendarState.events.forEach((ev) => {
    const meta = ev.meta_json || {};
    const startDate = meta.start_at || "";
    const dateKey = String(startDate).slice(0, 10);
    if (!eventMap[dateKey]) eventMap[dateKey] = [];
    eventMap[dateKey].push(ev);
  });

  let html = '<div class="cal-weekdays">';
  ["一", "二", "三", "四", "五", "六", "日"].forEach((d, i) => {
    const date = new Date(startOfWeek);
    date.setDate(date.getDate() + i);
    const dateStr = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const isToday = date.toDateString() === today.toDateString();
    html += `<div class="cal-weekday${isToday ? " today" : ""}">${d} ${date.getDate()}日</div>`;
  });
  html += "</div><div class='cal-week cal-week-view'>";

  for (let i = 0; i < 7; i++) {
    const date = new Date(startOfWeek);
    date.setDate(date.getDate() + i);
    const dateStr = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    const isToday = date.toDateString() === today.toDateString();
    const events = eventMap[dateStr] || [];

    let eventsHtml = events
      .map((ev) => `<div class="cal-event-card" data-event-id="${ev.id}" style="border-left:3px solid ${esc((ev.meta_json || {}).color || "#4A90D9")}">
        <span class="cal-event-title">${esc(ev.title)}</span>
      </div>`)
      .join("");

    html += `<div class="cal-week-col${isToday ? " today" : ""}" data-date="${dateStr}">
      <div class="cal-week-col-inner">${eventsHtml || '<span class="cal-night-text"></span>'}</div>
    </div>`;
  }

  html += "</div>";
  grid.innerHTML = html;

  grid.querySelectorAll(".cal-week-col").forEach((col) => {
    col.addEventListener("click", () => showEventModal(col.dataset.date));
  });
  grid.querySelectorAll(".cal-event-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      e.stopPropagation();
      const eventId = Number(card.dataset.eventId);
      const ev = calendarState.events.find((item) => item.id === eventId);
      if (ev) showEventModal(null, ev);
    });
  });
}

function renderDayGrid(year, month) {
  const grid = document.querySelector("#cal-grid");
  const today = new Date();

  const date = new Date(calendarState.currentDate);
  const dateStr = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  const isToday = date.toDateString() === today.toDateString();

  const dayEvents = calendarState.events.filter((ev) => {
    const meta = ev.meta_json || {};
    const startDate = meta.start_at || "";
    return String(startDate).slice(0, 10) === dateStr;
  });

  dayEvents.sort((a, b) => {
    const aStart = ((a.meta_json || {}).start_at || "");
    const bStart = ((b.meta_json || {}).start_at || "");
    return String(aStart).localeCompare(String(bStart));
  });

  let html = `<div class="cal-day-header${isToday ? " today" : ""}">
    <h3>${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${["日", "一", "二", "三", "四", "五", "六"][date.getDay()]}</h3>
  </div>`;

  if (!dayEvents.length) {
    html += `<div class="empty-state">${t("calendar.empty", "暂无事件")}</div>`;
  } else {
    html += '<div class="cal-day-events">';
    dayEvents.forEach((ev) => {
      const emeta = ev.meta_json || {};
      const startTime = emeta.start_at ? String(emeta.start_at).slice(11, 16) : "";
      const endTime = emeta.end_at ? String(emeta.end_at).slice(11, 16) : "";
      const timeStr = emeta.all_day ? "全天" : `${startTime}${endTime ? " - " + endTime : ""}`;
      html += `<div class="cal-event-card" data-event-id="${ev.id}" style="border-left:3px solid ${esc(emeta.color || "#4A90D9")}">
        <div class="cal-event-time">${esc(timeStr)}</div>
        <div class="cal-event-title">${esc(ev.title)}</div>
        ${emeta.location ? `<div class="cal-event-loc">${esc(emeta.location)}</div>` : ""}
      </div>`;
    });
    html += "</div>";
  }

  grid.innerHTML = html;

  grid.querySelectorAll(".cal-event-card").forEach((card) => {
    card.addEventListener("click", () => {
      const eventId = Number(card.dataset.eventId);
      const ev = calendarState.events.find((item) => item.id === eventId);
      if (ev) showEventModal(null, ev);
    });
  });
}

function showEventModal(dateStr, existingEvent) {
  const oldOverlay = document.querySelector("#cal-modal-overlay");
  if (oldOverlay) oldOverlay.remove();

  const isEdit = !!existingEvent;
  const evMeta = isEdit ? (existingEvent.meta_json || {}) : {};
  const evTitle = isEdit ? existingEvent.title || "" : "";
  const evStart = isEdit ? (evMeta.start_at ? String(evMeta.start_at).slice(0, 16) : "") : dateStr ? (dateStr + "T09:00") : new Date().toISOString().slice(0, 16);
  const evEnd = isEdit ? (evMeta.end_at ? String(evMeta.end_at).slice(0, 16) : "") : "";
  const evAllDay = isEdit ? !!evMeta.all_day : false;
  const evLocation = isEdit ? evMeta.location || "" : "";
  const evColor = isEdit ? evMeta.color || "#4A90D9" : "#4A90D9";
  const eventId = isEdit ? existingEvent.id : null;

  const colors = ["#4A90D9", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6"];

  const overlay = document.createElement("div");
  overlay.id = "cal-modal-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal cal-modal">
      <h3>${isEdit ? t("calendar.eventTitle", "编辑事件") : t("calendar.newEvent", "新建事件")}</h3>
      <div class="form-grid">
        <div class="field wide">
          <label>${t("calendar.eventTitle", "标题")}</label>
          <input id="cal-ev-title" value="${esc(evTitle)}" />
        </div>
        <div class="field">
          <label>${t("calendar.eventStart", "开始")}</label>
          <input id="cal-ev-start" type="datetime-local" value="${esc(evStart)}" />
        </div>
        <div class="field">
          <label>${t("calendar.eventEnd", "结束")}</label>
          <input id="cal-ev-end" type="datetime-local" value="${esc(evEnd)}" />
        </div>
        <div class="field" style="display:flex;align-items:center;gap:8px">
          <label style="margin:0">${t("calendar.eventAllDay", "全天")}</label>
          <input id="cal-ev-allday" type="checkbox" ${evAllDay ? "checked" : ""} style="width:auto" />
        </div>
        <div class="field">
          <label>${t("calendar.eventLocation", "地点")}</label>
          <input id="cal-ev-location" value="${esc(evLocation)}" />
        </div>
        <div class="field wide">
          <label>${t("calendar.eventColor", "颜色")}</label>
          <div class="color-picker">
            ${colors.map((c) => `<span class="color-swatch ${c === evColor ? "active" : ""}" data-color="${c}" style="background:${c}"></span>`).join("")}
          </div>
          <input type="hidden" id="cal-ev-color" value="${esc(evColor)}" />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn primary" id="cal-ev-save">${t("calendar.eventSave", "保存")}</button>
        ${isEdit ? `<button class="btn danger" id="cal-ev-delete">${t("calendar.eventDelete", "删除")}</button>` : ""}
        <button class="btn" id="cal-ev-cancel">取消</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  overlay.querySelectorAll(".color-swatch").forEach((swatch) => {
    swatch.addEventListener("click", () => {
      overlay.querySelectorAll(".color-swatch").forEach((s) => s.classList.remove("active"));
      swatch.classList.add("active");
      overlay.querySelector("#cal-ev-color").value = swatch.dataset.color;
    });
  });

  overlay.querySelector("#cal-ev-cancel").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.remove();
  });

  overlay.querySelector("#cal-ev-save").addEventListener("click", async () => {
    const startVal = overlay.querySelector("#cal-ev-start").value;
    const endVal = overlay.querySelector("#cal-ev-end").value;
    const payload = {
      kind: "calendar_event",
      title: overlay.querySelector("#cal-ev-title").value,
      meta_json: {
        start_at: startVal || null,
        end_at: endVal || null,
        all_day: overlay.querySelector("#cal-ev-allday").checked,
        location: overlay.querySelector("#cal-ev-location").value || null,
        color: overlay.querySelector("#cal-ev-color").value,
      },
    };
    try {
      if (isEdit) {
        await api(`/api/items/${eventId}`, { method: "PUT", body: payload });
      } else {
        await api("/api/items", { method: "POST", body: payload });
      }
      overlay.remove();
      renderCalendar();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  if (isEdit) {
    overlay.querySelector("#cal-ev-delete").addEventListener("click", async () => {
      if (!confirm("确定要删除该事件吗？")) return;
      try {
        await api(`/api/items/${eventId}`, { method: "DELETE" });
        overlay.remove();
        renderCalendar();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
}

function renderCompose() {
  const workspace = document.querySelector("#workspace");
  // 恢复草稿（如果存在）
  const draft = JSON.parse(localStorage.getItem("wuyou.draft") || "{}");
  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("compose.title", "写邮件")}</h2></div>
      <form class="panel item-card compose" id="compose-form">
        <div class="form-grid">
          <div class="field wide"><label>${t("compose.from", "发件邮箱")}</label><select name="mailbox_id" required>${state.accounts.map((account) => `<option value="${account.id}" ${draft.mailbox_id == account.id ? "selected" : ""}>${esc(account.display_name)} · ${esc(account.email_address)}</option>`).join("")}</select></div>
          <div class="field wide"><label>${t("compose.to", "收件人，多个地址用英文逗号分隔")}</label><input name="recipients" required value="${esc(draft.recipients || "")}" /></div>
          <div class="field wide"><label>${t("compose.subject", "主题")}</label><input name="subject" required value="${esc(draft.subject || "")}" /></div>
          <div class="field"><label>${t("compose.format", "格式")}</label><select name="format"><option value="text" ${draft.format === "text" ? "selected" : ""}>Text</option><option value="markdown" ${!draft.format || draft.format === "markdown" ? "selected" : ""}>Markdown</option><option value="html" ${draft.format === "html" ? "selected" : ""}>HTML</option></select></div>
          <div class="field"><label>${t("compose.encryption", "加密策略")}</label><select name="encryption_mode"><option value="auto">Auto TLS</option><option value="tls_only">TLS Only</option><option value="pgp">PGP</option></select></div>
          <div class="field wide">
            <label>${t("compose.body", "正文")}</label>
            <div class="format-toolbar" style="margin-bottom:8px;display:flex;gap:4px">
              <button type="button" class="btn" data-format="bold" title="加粗"><b>B</b></button>
              <button type="button" class="btn" data-format="italic" title="斜体"><i>I</i></button>
              <button type="button" class="btn" data-format="list" title="无序列表">•</button>
              <button type="button" class="btn" data-format="link" title="插入链接">🔗</button>
            </div>
            <textarea name="body" id="compose-body">${esc(draft.body || "")}</textarea>
          </div>
        </div>
        <div class="btn-row" style="justify-content:flex-start">
          <button class="btn primary" type="submit" id="compose-send">${t("compose.send", "发送")}</button>
          <button class="btn" type="button" id="compose-draft">${t("compose.saveDraft", "保存草稿")}</button>
          <button class="btn" type="button" id="compose-cancel">${t("compose.cancel", "取消")}</button>
        </div>
      </form>
    </section>
  `;

  // ── 格式工具栏：在光标位置插入 Markdown 标记 ──
  const bodyTextarea = document.querySelector("#compose-body");
  document.querySelectorAll(".format-toolbar [data-format]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const fmt = btn.dataset.format;
      const ta = bodyTextarea;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const selected = ta.value.substring(start, end);
      let before = "", after = "";
      if (fmt === "bold") { before = "**"; after = "**"; }
      else if (fmt === "italic") { before = "*"; after = "*"; }
      else if (fmt === "list") { before = "\n- "; after = ""; }
      else if (fmt === "link") { before = "["; after = `](${selected || "url"})`; }
      ta.setRangeText(before + selected + after, start, end, "select");
      ta.focus();
    });
  });

  // ── 保存草稿 ──
  document.querySelector("#compose-draft").addEventListener("click", () => {
    const form = new FormData(document.querySelector("#compose-form"));
    const draftData = {
      mailbox_id: form.get("mailbox_id"),
      recipients: form.get("recipients"),
      subject: form.get("subject"),
      body: form.get("body"),
      format: form.get("format"),
    };
    localStorage.setItem("wuyou.draft", JSON.stringify(draftData));
    toast(t("compose.draftSaved", "草稿已保存。"));
  });

  // ── 取消 ──
  document.querySelector("#compose-cancel").addEventListener("click", () => {
    if (confirm(t("compose.cancelConfirm", "确定要取消吗？未保存的内容将会丢失。"))) {
      localStorage.removeItem("wuyou.draft");
      route("inbox");
    }
  });

  // ── 发送（带 loading 状态） ──
  document.querySelector("#compose-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sendBtn = document.querySelector("#compose-send");
    sendBtn.disabled = true;
    sendBtn.textContent = t("compose.sending", "发送中...");
    try {
      const payload = {
        mailbox_id: Number(form.get("mailbox_id")),
        recipients: String(form.get("recipients")).split(",").map((item) => item.trim()).filter(Boolean),
        subject: form.get("subject"),
        body: form.get("body"),
        format: form.get("format"),
        encryption_mode: form.get("encryption_mode"),
      };
      const result = await api("/api/mail/send", { method: "POST", body: payload });
      toast(result.message);
      localStorage.removeItem("wuyou.draft");
      event.currentTarget.reset();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = t("compose.send", "发送");
    }
  });
}

async function renderAccounts() {
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace";
  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("accounts.title", "邮箱账户")}</h2></div>
      <div class="grid" id="accounts-grid">
        <div class="empty-state">加载中...</div>
      </div>
      <form class="panel item-card" id="account-form" style="margin-top:14px">
        <h3>${t("accounts.add", "添加邮箱账户")}</h3>
        <div class="form-grid">
          <div class="field"><label>${t("accounts.display", "显示名称")}</label><input name="display_name" required /></div>
          <div class="field"><label>${t("accounts.email", "邮箱地址")}</label><input name="email_address" type="email" required /></div>
          <div class="field"><label>${t("accounts.auth", "登录方式")}</label><select name="auth_type"><option value="app_password">授权码</option><option value="password">密码</option><option value="key">密钥</option><option value="oauth2">OAuth2</option><option value="sms_code">手机验证码</option></select></div>
          <div id="oauth-provider-row" style="display:none">
            <label>${t("auth.oauthProvider", "选择服务商")}</label>
            <select id="oauth-provider"><option value="">请选择</option></select>
            <button class="btn primary" id="oauth-connect-btn" type="button">${t("auth.oauthConnect", "连接")}</button>
          </div>
          <div class="field"><label>${t("accounts.secret", "密码 / 授权码 / 密钥")}</label><input name="secret" type="password" required /></div>
          <div class="field"><label>IMAP Host</label><input name="imap_host" placeholder="${t("accounts.auto", "留空自动匹配")}" /></div>
          <div class="field"><label>SMTP Host</label><input name="smtp_host" placeholder="${t("accounts.auto", "留空自动匹配")}" /></div>
        </div>
        <button class="btn primary" type="submit">${t("accounts.save", "保存账户")}</button>
      </form>
      <article class="item-card" style="margin-top:14px">
        <h3>${t("accounts.tbImport", "导入 Thunderbird 数据")}</h3>
        <p class="muted">${t("accounts.tbPathHint", "输入 Thunderbird profile 路径，例如：%APPDATA%/Thunderbird/Profiles/xxxx.default")}</p>
        <div class="field"><input id="tb-profile-path" placeholder="C:/Users/.../Profiles/xxxx.default" /></div>
        <button class="btn" id="tb-import-btn">${t("accounts.tbImportBtn", "开始导入")}</button>
        <p id="tb-import-result" class="muted" style="display:none"></p>
      </article>
    </section>
  `;

  // 异步加载每个账户的同步状态
  if (state.accounts.length > 0) {
    const grid = document.querySelector("#accounts-grid");
    const accountsWithJobs = await Promise.all(
      state.accounts.map(async (acct) => {
        let lastJob = null;
        try {
          const jobs = await api(`/api/sync/jobs?mailbox_id=${acct.id}&limit=1`);
          lastJob = (jobs && jobs.length > 0) ? jobs[0] : null;
        } catch {}
        return { account: acct, lastJob };
      }),
    );
    grid.innerHTML = accountsWithJobs
      .map(({ account: acct, lastJob }) => {
        let statusText = "未知";
        let statusColor = "var(--muted)";
        let lastSync = "从未";
        let errorMsg = "";
        if (lastJob) {
          if (lastJob.status === "success") { statusText = "在线"; statusColor = "var(--green)"; }
          else if (lastJob.status === "failed" || lastJob.status === "canceled") { statusText = "错误"; statusColor = "var(--red)"; }
          else if (lastJob.status === "running") { statusText = "同步中..."; statusColor = "var(--yellow)"; }
          else if (lastJob.status === "queued") { statusText = "等待中"; statusColor = "var(--yellow)"; }
          else { statusText = "离线"; statusColor = "var(--muted)"; }
          if (lastJob.finished_at) lastSync = new Date(lastJob.finished_at).toLocaleString();
          if (lastJob.error) errorMsg = `<div style="color:var(--red);font-size:11px;margin-top:4px">${esc(lastJob.error)}</div>`;
        }
        return `
          <article class="item-card">
            <h3>${esc(acct.display_name)}</h3>
            <p>${esc(acct.email_address)}</p>
            <p class="muted">${esc(acct.provider)} · IMAP ${esc(acct.imap_host)} · SMTP ${esc(acct.smtp_host)}</p>
            <div class="account-status" style="margin-top:8px;font-size:12px">
              <span style="color:${statusColor}">&bull; ${statusText}</span>
              <span class="muted"> | 上次同步: ${lastSync}</span>
              <button class="btn" data-sync-acct="${acct.id}" style="font-size:11px;padding:2px 8px;margin-left:8px">${t("mail.sync", "同步")}</button>
              ${lastJob && lastJob.status === "running" ? '<span style="margin-left:6px;display:inline-block;width:12px;height:12px;border:2px solid var(--muted);border-top-color:var(--primary);border-radius:50%;animation:loading 0.8s linear infinite"></span>' : ""}
            </div>
            ${errorMsg}
          </article>
        `;
      })
      .join("");
  } else {
    document.querySelector("#accounts-grid").innerHTML = `<div class="empty-state">${t("accounts.empty", "还没有邮箱账户。")}</div>`;
  }

  document.querySelectorAll("[data-sync-acct]").forEach((button) =>
    button.addEventListener("click", async () => {
      const btn = button;
      btn.disabled = true;
      btn.textContent = "同步中...";
      try {
        await api("/api/sync/jobs", {
          method: "POST",
          body: { mailbox_id: parseInt(btn.dataset.syncAcct) },
        });
        toast("已加入同步队列");
        setTimeout(() => renderAccounts(), 1500);
      } catch (error) {
        toast(error.message, "error");
        btn.disabled = false;
        btn.textContent = t("mail.sync", "同步");
      }
    }),
  );
  document.querySelector("#account-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api("/api/accounts", {
        method: "POST",
        body: {
          display_name: form.get("display_name"),
          email_address: form.get("email_address"),
          auth_type: form.get("auth_type"),
          secret: form.get("secret"),
          imap_host: form.get("imap_host") || null,
          smtp_host: form.get("smtp_host") || null,
        },
      });
      await loadCommon();
      renderShell();
      route("accounts");
      toast(t("accounts.saved", "邮箱账户已保存。"));
    } catch (error) {
      toast(error.message, "error");
    }
  });

  // OAuth2: auth_type change handler
  const authTypeSelect = document.querySelector('#account-form select[name="auth_type"]');
  if (authTypeSelect) {
    authTypeSelect.addEventListener("change", async (e) => {
      const row = document.querySelector("#oauth-provider-row");
      if (!row) return;
      if (e.target.value === "oauth2") {
        row.style.display = "block";
        try {
          const providers = await api("/api/auth/oauth/providers");
          const sel = document.querySelector("#oauth-provider");
          sel.innerHTML = '<option value="">请选择</option>' +
            (providers.providers || []).map((p) => `<option value="${esc(p.id || p.name)}">${esc(p.name)}</option>`).join("");
        } catch (err) {
          toast(err.message, "error");
        }
      } else {
        row.style.display = "none";
      }
    });
  }

  // OAuth2: connect button
  const oauthConnectBtn = document.querySelector("#oauth-connect-btn");
  if (oauthConnectBtn) {
    oauthConnectBtn.addEventListener("click", async () => {
      const provider = document.querySelector("#oauth-provider")?.value;
      if (!provider) { toast("请选择服务商", "error"); return; }
      try {
        const result = await api(`/api/auth/oauth/authorize?provider=${encodeURIComponent(provider)}&redirect_to=`);
        window.location.href = result.auth_url;
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  // Thunderbird import
  const tbImportBtn = document.querySelector("#tb-import-btn");
  if (tbImportBtn) {
    tbImportBtn.addEventListener("click", async () => {
      const profilePath = document.querySelector("#tb-profile-path")?.value?.trim();
      if (!profilePath) { toast("请输入 Thunderbird profile 路径", "error"); return; }
      try {
        const result = await api("/api/accounts/thunderbird/import", {
          method: "POST",
          body: { profile_path: profilePath },
        });
        const resultEl = document.querySelector("#tb-import-result");
        if (resultEl) {
          resultEl.style.display = "block";
          resultEl.textContent = JSON.stringify(result, null, 2);
        }
        toast(t("accounts.tbImportSuccess", "Thunderbird 导入完成"));
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }
}

async function renderPlugins() {
  const workspace = document.querySelector("#workspace");
  workspace.innerHTML = `<section class="page-pane"><div class="empty-state">${t("common.loading", "加载中...")}</div></section>`;
  try {
    const [catalog, installed] = await Promise.all([api("/api/plugins/catalog"), api("/api/plugins/installed")]);
    const plugins = catalog.catalog.plugins || [];
    workspace.innerHTML = `
      <section class="page-pane">
        <div class="page-header"><h2>${t("plugins.title", "插件社区")}</h2><span class="muted">${esc(catalog.catalog.name || "")}</span></div>
        <div class="toolbar"><input id="remote-source" placeholder="${t("plugins.remote", "输入在线插件社区 index.json 地址")}" /><button class="btn" id="load-remote">${t("plugins.load", "加载在线社区")}</button></div>
        <div class="grid">
          ${plugins
            .map(
              (plugin) => `
                <article class="item-card">
                  <h3>${esc(plugin.name)}</h3>
                  <p>${esc(plugin.description)}</p>
                  <p class="muted">${esc(plugin.category)} · ${esc(plugin.version)} · ${esc(plugin.license)}</p>
                  <button class="btn primary" data-install='${esc(JSON.stringify(plugin))}'>${t("plugins.install", "安装")}</button>
                </article>
              `,
            )
            .join("")}
        </div>
        <h3>${t("plugins.installed", "已安装")}</h3>
        <div class="grid">${installed.installed.map((item) => {
          const isEnabled = item.enabled !== 0;
          const uninstallJson = esc(JSON.stringify({ plugin_id: item.plugin_id, name: item.name }));
          const shareJson = esc(JSON.stringify({ plugin_id: item.plugin_id, name: item.name, version: item.version, category: item.category, type: item.type }));
          return `<div class="item-card"><b>${esc(item.name)}</b><p class="muted">${esc(item.version)} · ${esc(item.category)}</p><p>${t("plugins.status", "状态")}：${isEnabled ? t("plugins.enabledLabel", "已启用") : t("plugins.disabledLabel", "已停用")}</p><div class="btn-row">${isEnabled ? `<button class="btn btn-sm" data-disable="${esc(item.plugin_id)}">${t("plugins.disable", "停用")}</button>` : `<button class="btn btn-sm primary" data-enable="${esc(item.plugin_id)}">${t("plugins.enable", "启用")}</button>`}<button class="btn btn-sm" data-uninstall='${uninstallJson}'>${t("plugins.uninstall", "卸载")}</button><button class="btn btn-sm" data-share='${shareJson}'>${t("plugins.share", "分享")}</button></div></div>`;
        }).join("") || `<div class="empty-state">${t("plugins.none", "暂无已安装插件")}</div>`}</div>
      </section>
    `;
    document.querySelectorAll("[data-install]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          const manifest = JSON.parse(button.dataset.install);
          const result = await api("/api/plugins/install", { method: "POST", body: { manifest } });
          toast(result.message);
          renderPlugins();
        } catch (error) {
          toast(error.message, "error");
        }
      }),
    );
    document.querySelector("#load-remote").addEventListener("click", async () => {
      const source = document.querySelector("#remote-source").value.trim();
      if (!source) return;
      try {
        const remote = await api(`/api/plugins/catalog?source_url=${encodeURIComponent(source)}`);
        toast(`${remote.catalog.name || "Remote"}: ${remote.catalog.plugins.length} plugins`);
      } catch (error) {
        toast(error.message, "error");
      }
    });

    // ── Enable plugin ──
    document.querySelectorAll("[data-enable]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          const pluginId = button.dataset.enable;
          await api(`/api/plugins/${encodeURIComponent(pluginId)}/enable`, { method: "POST" });
          toast("插件已启用。");
          renderPlugins();
        } catch (error) {
          toast(error.message, "error");
        }
      }),
    );

    // ── Disable plugin ──
    document.querySelectorAll("[data-disable]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          const pluginId = button.dataset.disable;
          await api(`/api/plugins/${encodeURIComponent(pluginId)}/disable`, { method: "POST" });
          toast("插件已停用。");
          renderPlugins();
        } catch (error) {
          toast(error.message, "error");
        }
      }),
    );

    // ── Uninstall plugin ──
    document.querySelectorAll("[data-uninstall]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          const info = JSON.parse(button.dataset.uninstall);
          if (!confirm(`确定要卸载 "${info.name}" 吗？此操作不可撤销。`)) return;
          await api(`/api/plugins/${encodeURIComponent(info.plugin_id)}`, { method: "DELETE" });
          toast("插件已卸载。");
          renderPlugins();
        } catch (error) {
          toast(error.message, "error");
        }
      }),
    );

    // ── Share plugin ──
    document.querySelectorAll("[data-share]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          const data = JSON.parse(button.dataset.share);
          await api("/api/share", {
            method: "POST",
            body: {
              type: "extension",
              item_id: data.plugin_id,
              manifest: {
                name: data.name,
                version: data.version,
                category: data.category,
                type: data.type,
              },
            },
          });
          toast("分享已提交，等待审核。");
        } catch (error) {
          toast(error.message, "error");
        }
      }),
    );
  } catch (error) {
    toast(error.message, "error");
  }
}

async function renderSettings() {
  const workspace = document.querySelector("#workspace");
  const data = await api("/api/settings");
  const settings = data.settings || {};
  // 获取当前值
  const theme = state.theme;
  const locale = state.locale;
  const telemetry = settings["telemetry_enabled"] === true || settings["telemetry_enabled"] === "true" || settings["telemetry_enabled"] === 1;
  const remoteUrl = settings["remote_sync_endpoint"] || "";

  workspace.innerHTML = `<section class="page-pane">
    <h2>${t("nav.settings","设置")}</h2>
    <div class="settings-form">
      <div class="form-group">
        <label>${t("settings.theme","主题")}</label>
        <select id="set-theme">
          <option value="light" ${theme==="light"?"selected":""}>日间模式</option>
          <option value="dark" ${theme==="dark"?"selected":""}>夜间模式</option>
        </select>
      </div>
      <div class="form-group">
        <label>${t("settings.language","界面语言")}</label>
        <select id="set-locale">
          <option value="zh-CN" ${locale==="zh-CN"?"selected":""}>简体中文</option>
          <option value="en-US" ${locale==="en-US"?"selected":""}>English</option>
          <option value="zh-TW" ${locale==="zh-TW"?"selected":""}>繁體中文</option>
        </select>
      </div>
      <div class="form-group">
        <label>${t("settings.telemetry","匿名使用数据")}</label>
        <input type="checkbox" id="set-telemetry" ${telemetry?"checked":""}>
        <small>${t("settings.telemetryHelp","帮助改进产品，不收集隐私")}</small>
      </div>
      <div class="form-group">
        <label>${t("settings.remoteSyncEndpoint","远程同步地址")}</label>
        <input type="text" id="set-remote-url" value="${esc(remoteUrl)}" placeholder="http://..." style="width:100%">
      </div>
      <div class="form-group">
        <label>${t("settings.changePassword","修改密码")}</label>
        <input type="password" id="set-old-pw" placeholder="原密码">
        <input type="password" id="set-new-pw" placeholder="新密码 (至少8位)">
        <button class="btn primary" id="btn-change-pw">${t("settings.save","保存")}</button>
      </div>
      <div class="form-group">
        <label>${t("settings.changeEmail","修改邮箱")}</label>
        <input type="email" id="set-new-email" placeholder="新邮箱">
        <button class="btn primary" id="btn-send-email-code">发送验证码</button>
        <input type="text" id="set-email-code" placeholder="验证码" maxlength="6">
        <button class="btn" id="btn-confirm-email">确认修改</button>
      </div>
      <div class="btn-row">
        <button class="btn primary" id="btn-save-settings">${t("settings.saveAll","保存设置")}</button>
      </div>
    </div>
  </section>`;

  // 绑定事件
  document.getElementById("btn-save-settings").onclick = async () => {
    // 保存 theme
    const newTheme = document.getElementById("set-theme").value;
    if (newTheme !== state.theme) {
      state.theme = newTheme;
      localStorage.setItem("wuyou.theme", state.theme);
      applyTheme();
    }
    // 保存 locale
    const newLocale = document.getElementById("set-locale").value;
    if (newLocale !== state.locale) {
      state.locale = newLocale;
      localStorage.setItem("wuyou.locale", state.locale);
      await loadLocale(state.locale);
      // 重新渲染 shell
      renderShell();
    }
    // 保存 telemetry
    const tel = document.getElementById("set-telemetry").checked;
    await api("/api/settings", { method: "PUT", body: {key:"telemetry_enabled", value: tel} });
    // 保存 remote url
    const rurl = document.getElementById("set-remote-url").value.trim();
    if (rurl) await api("/api/settings", { method: "PUT", body: {key:"remote_sync_endpoint", value: rurl} });
    toast(t("settings.saved","设置已保存"), "ok");
  };

  document.getElementById("btn-change-pw").onclick = async () => {
    const old = document.getElementById("set-old-pw").value;
    const news = document.getElementById("set-new-pw").value;
    if (!old || !news) return toast("请填写原密码和新密码", "error");
    if (news.length < 8) return toast("新密码至少8位", "error");
    const r = await api("/api/auth/change-password", { method: "PUT", body: {old_password: old, new_password: news} });
    if (r.message) toast(r.message, "ok");
    document.getElementById("set-old-pw").value = "";
    document.getElementById("set-new-pw").value = "";
  };

  document.getElementById("btn-send-email-code").onclick = async () => {
    const email = document.getElementById("set-new-email").value.trim();
    if (!email || !email.includes("@")) return toast("请输入有效邮箱", "error");
    const r = await api("/api/auth/verification-code", { method: "POST", body: {target_type:"email", target:email, purpose:"change_contact"} });
    if (r.message) toast(r.message, "ok");
  };

  document.getElementById("btn-confirm-email").onclick = async () => {
    const email = document.getElementById("set-new-email").value.trim();
    const code = document.getElementById("set-email-code").value.trim();
    if (!email || !code) return toast("请填写邮箱和验证码", "error");
    const r = await api("/api/auth/change-contact", { method: "PUT", body: {target_type:"email", target:email, code:code} });
    if (r.message) toast(r.message, "ok");
  };
}

async function renderAbout() {
  const workspace = document.querySelector("#workspace");
  const about = await api("/api/settings/about");
  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("about.title", "关于 WuYou")}</h2></div>
      <div class="item-card">
        <h3>${esc(about.name)}</h3>
        <p class="slogan">${t("about.slogan", "你的邮件，都在坞里")}</p>
        <p>${esc(about.positioning)}</p>
        <p>${esc(about.core)}</p>
        <p class="muted">${t("about.sloganEn", "WuYou. One emailbox. All yours.")}</p>
        <p class="muted">License: ${esc(about.license)}</p>
        <p class="muted"><button class="btn" id="show-changelog">${t("about.changelog", "更新日志")}</button></p>
      </div>
      <div class="item-card donate-card">
        <div class="donate-qr">
          <img src="/static/img/alipay-qr.svg" alt="支付宝打赏码" width="200" height="200" style="border-radius:8px;" />
        </div>
        <p class="donate-text">${t("about.donate", "喜欢 WuYou？请我喝杯咖啡")}</p>
      </div>
    </section>
  `;

  document.getElementById("show-changelog").onclick = () => {
    const modal = document.createElement("div");
    modal.className = "modal-overlay";
    modal.innerHTML = `<div class="modal" style="max-width:700px;max-height:80vh;overflow-y:auto">
      <h3>更新日志</h3>
      <div class="changelog-content">
        <h4>v1.0.1 (2026-06-23)</h4>
        <p>🔒 安全加固 + 国际化完善</p>
        <ul style="text-align:left"><li>5项安全漏洞修复（认证保护、路径穿越防护、越权同步修复、SQL注入消除）</li><li>zh-CN/en-US/zh-TW 三语统一补齐至 211 键</li><li>新增 sync.*/compose.* 等 19 个翻译 key</li></ul>
        <h4>v1.0.0 (2026-06-23)</h4>
        <p>首个正式版本，28项核心功能全部就绪。</p>
        <ul style="text-align:left"><li>多邮箱统一管理 (12家服务商自动匹配)</li><li>PGP端到端加密</li><li>日历/通讯录/任务/便签</li><li>CalDAV/CardDAV/Google/MS Graph同步</li><li>OAuth2一键登录</li><li>Thunderbird全量数据迁移</li><li>插件社区+主题/语言包</li><li>响应式布局 (PC/平板/手机)</li><li>热更新</li><li>远程设备同步</li></ul>
      </div>
      <button class="btn close-modal-btn">关闭</button>
    </div>`;
    document.body.appendChild(modal);
    modal.querySelector(".close-modal-btn").addEventListener("click", (e) => { e.stopPropagation(); modal.remove(); });
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });
  };
}

// ── 通讯录 ──
async function renderContacts() {
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace";

  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("nav.contacts", "通讯录")}</h2></div>
      <div class="toolbar">
        <input id="contact-search" placeholder="${t("contacts.search", "搜索联系人...")}" value="${esc(contactsState.searchQuery)}" />
        <button class="btn primary" id="contact-new">+ ${t("contacts.new", "新建联系人")}</button>
      </div>
      <div id="contact-list"><div class="empty-state">${t("contacts.loading", "加载通讯录...")}</div></div>
    </section>
  `;

  document.querySelector("#contact-search").addEventListener("input", (e) => {
    contactsState.searchQuery = e.target.value;
    loadContacts();
  });
  document.querySelector("#contact-new").addEventListener("click", () => showContactModal());

  await loadContacts();
}

async function loadContacts() {
  const list = document.querySelector("#contact-list");
  try {
    const params = new URLSearchParams({ kind: "contact" });
    if (contactsState.searchQuery) params.set("q", contactsState.searchQuery);
    const data = await api(`/api/items?${params.toString()}`);
    contactsState.contacts = data.items || [];
  } catch (error) {
    toast(error.message, "error");
    contactsState.contacts = [];
  }
  if (!contactsState.contacts.length) {
    list.innerHTML = `<div class="empty-state">${t("contacts.empty", "暂无联系人")}</div>`;
    return;
  }
  list.innerHTML = contactsState.contacts
    .map((c) => {
      const meta = c.meta_json || {};
      const firstName = esc(meta.first_name || c.title || "");
      const lastName = esc(meta.last_name || "");
      const initial = (firstName || "?").charAt(0).toUpperCase();
      return `<article class="item-card contact-card" data-contact-id="${c.id}">
        <div class="contact-avatar">${initial}</div>
        <div class="contact-info">
          <h3>${firstName}${lastName ? " " + lastName : ""}</h3>
          ${meta.email ? `<p><span class="muted">${esc(meta.email_type || "Work")}:</span> ${esc(meta.email)}</p>` : ""}
          ${meta.phone ? `<p><span class="muted">${esc(meta.phone_type || "Mobile")}:</span> ${esc(meta.phone)}</p>` : ""}
          ${meta.organization ? `<p>${esc(meta.organization)}${meta.job_title ? " · " + esc(meta.job_title) : ""}</p>` : ""}
        </div>
        <div class="contact-actions">
          ${meta.email ? `<button class="btn" data-send-email="${esc(meta.email)}">${t("contacts.sendEmail", "发邮件")}</button>` : ""}
        </div>
      </article>`;
    })
    .join("");

  list.querySelectorAll(".contact-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("[data-send-email]")) return;
      const id = Number(card.dataset.contactId);
      const contact = contactsState.contacts.find((c) => c.id === id);
      if (contact) showContactModal(contact);
    });
  });
  list.querySelectorAll("[data-send-email]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const email = btn.dataset.sendEmail;
      state.view = "compose";
      document.querySelectorAll(".nav-button").forEach((b) => b.classList.toggle("active", b.dataset.view === "compose"));
      renderComposeWithTo(email);
    });
  });
}

function renderComposeWithTo(toEmail) {
  const workspace = document.querySelector("#workspace");
  // 恢复草稿（如果存在）
  const draft = JSON.parse(localStorage.getItem("wuyou.draft") || "{}");
  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("compose.title", "写邮件")}</h2></div>
      <form class="panel item-card compose" id="compose-form">
        <div class="form-grid">
          <div class="field wide"><label>${t("compose.from", "发件邮箱")}</label><select name="mailbox_id" required>${state.accounts.map((account) => `<option value="${account.id}" ${draft.mailbox_id == account.id ? "selected" : ""}>${esc(account.display_name)} · ${esc(account.email_address)}</option>`).join("")}</select></div>
          <div class="field wide"><label>${t("compose.to", "收件人，多个地址用英文逗号分隔")}</label><input name="recipients" required value="${esc(toEmail || draft.recipients || "")}" /></div>
          <div class="field wide"><label>${t("compose.subject", "主题")}</label><input name="subject" required value="${esc(draft.subject || "")}" /></div>
          <div class="field"><label>${t("compose.format", "格式")}</label><select name="format"><option value="text" ${draft.format === "text" ? "selected" : ""}>Text</option><option value="markdown" ${!draft.format || draft.format === "markdown" ? "selected" : ""}>Markdown</option><option value="html" ${draft.format === "html" ? "selected" : ""}>HTML</option></select></div>
          <div class="field"><label>${t("compose.encryption", "加密策略")}</label><select name="encryption_mode"><option value="auto">Auto TLS</option><option value="tls_only">TLS Only</option><option value="pgp">PGP</option></select></div>
          <div class="field wide">
            <label>${t("compose.body", "正文")}</label>
            <div class="format-toolbar" style="margin-bottom:8px;display:flex;gap:4px">
              <button type="button" class="btn" data-format="bold" title="加粗"><b>B</b></button>
              <button type="button" class="btn" data-format="italic" title="斜体"><i>I</i></button>
              <button type="button" class="btn" data-format="list" title="无序列表">•</button>
              <button type="button" class="btn" data-format="link" title="插入链接">🔗</button>
            </div>
            <textarea name="body" id="compose-body">${esc(draft.body || "")}</textarea>
          </div>
        </div>
        <div class="btn-row" style="justify-content:flex-start">
          <button class="btn primary" type="submit" id="compose-send">${t("compose.send", "发送")}</button>
          <button class="btn" type="button" id="compose-draft">${t("compose.saveDraft", "保存草稿")}</button>
          <button class="btn" type="button" id="compose-cancel">${t("compose.cancel", "取消")}</button>
        </div>
      </form>
    </section>
  `;

  // ── 格式工具栏 ──
  const bodyTextarea = document.querySelector("#compose-body");
  document.querySelectorAll(".format-toolbar [data-format]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const fmt = btn.dataset.format;
      const ta = bodyTextarea;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const selected = ta.value.substring(start, end);
      let before = "", after = "";
      if (fmt === "bold") { before = "**"; after = "**"; }
      else if (fmt === "italic") { before = "*"; after = "*"; }
      else if (fmt === "list") { before = "\n- "; after = ""; }
      else if (fmt === "link") { before = "["; after = `](${selected || "url"})`; }
      ta.setRangeText(before + selected + after, start, end, "select");
      ta.focus();
    });
  });

  // ── 保存草稿 ──
  document.querySelector("#compose-draft").addEventListener("click", () => {
    const form = new FormData(document.querySelector("#compose-form"));
    const draftData = {
      mailbox_id: form.get("mailbox_id"),
      recipients: form.get("recipients"),
      subject: form.get("subject"),
      body: form.get("body"),
      format: form.get("format"),
    };
    localStorage.setItem("wuyou.draft", JSON.stringify(draftData));
    toast(t("compose.draftSaved", "草稿已保存。"));
  });

  // ── 取消 ──
  document.querySelector("#compose-cancel").addEventListener("click", () => {
    if (confirm(t("compose.cancelConfirm", "确定要取消吗？未保存的内容将会丢失。"))) {
      localStorage.removeItem("wuyou.draft");
      route("inbox");
    }
  });

  // ── 发送 ──
  document.querySelector("#compose-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sendBtn = document.querySelector("#compose-send");
    sendBtn.disabled = true;
    sendBtn.textContent = t("compose.sending", "发送中...");
    try {
      const payload = {
        mailbox_id: Number(form.get("mailbox_id")),
        recipients: String(form.get("recipients")).split(",").map((item) => item.trim()).filter(Boolean),
        subject: form.get("subject"),
        body: form.get("body"),
        format: form.get("format"),
        encryption_mode: form.get("encryption_mode"),
      };
      const result = await api("/api/mail/send", { method: "POST", body: payload });
      toast(result.message);
      localStorage.removeItem("wuyou.draft");
      event.currentTarget.reset();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = t("compose.send", "发送");
    }
  });
}

function showContactModal(existing) {
  const oldOverlay = document.querySelector("#contact-modal-overlay");
  if (oldOverlay) oldOverlay.remove();

  const isEdit = !!existing;
  const meta = existing ? existing.meta_json || {} : {};
  const contactId = isEdit ? existing.id : null;

  const emailTypes = ["Work", "Home", "Other"];
  const phoneTypes = ["Mobile", "Work", "Home", "Other"];

  const overlay = document.createElement("div");
  overlay.id = "contact-modal-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal contact-modal">
      <h3>${isEdit ? esc(existing.title || "") : t("contacts.new", "新建联系人")}</h3>
      <div class="form-grid">
        <div class="field"><label>${t("contacts.firstName", "名")}</label><input id="contact-first" value="${esc(meta.first_name || "")}" /></div>
        <div class="field"><label>${t("contacts.lastName", "姓")}</label><input id="contact-last" value="${esc(meta.last_name || "")}" /></div>
        <div class="field"><label>${t("contacts.emailType", "邮箱类型")}</label><select id="contact-email-type">${emailTypes.map((t) => `<option value="${t}" ${meta.email_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="field"><label>${t("contacts.email", "邮箱")}</label><input id="contact-email" type="email" value="${esc(meta.email || "")}" /></div>
        <div class="field"><label>${t("contacts.phoneType", "电话类型")}</label><select id="contact-phone-type">${phoneTypes.map((t) => `<option value="${t}" ${meta.phone_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="field"><label>${t("contacts.phone", "电话")}</label><input id="contact-phone" value="${esc(meta.phone || "")}" /></div>
        <div class="field"><label>${t("contacts.organization", "公司")}</label><input id="contact-org" value="${esc(meta.organization || "")}" /></div>
        <div class="field"><label>${t("contacts.jobTitle", "职位")}</label><input id="contact-job" value="${esc(meta.job_title || "")}" /></div>
        <div class="field"><label>${t("contacts.birthday", "生日")}</label><input id="contact-birthday" type="date" value="${esc(meta.birthday || "")}" /></div>
      </div>
      <div class="btn-row">
        <button class="btn primary" id="contact-save">${t("contacts.save", "保存联系人")}</button>
        ${isEdit ? `<button class="btn danger" id="contact-delete">${t("contacts.delete", "删除")}</button>` : ""}
        <button class="btn" id="contact-cancel">取消</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  overlay.querySelector("#contact-cancel").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

  overlay.querySelector("#contact-save").addEventListener("click", async () => {
    const firstName = overlay.querySelector("#contact-first").value.trim();
    const lastName = overlay.querySelector("#contact-last").value.trim();
    const title = [firstName, lastName].filter(Boolean).join(" ");

    const payload = {
      kind: "contact",
      title: title || "未命名联系人",
      meta_json: {
        first_name: firstName,
        last_name: lastName,
        email: overlay.querySelector("#contact-email").value.trim(),
        email_type: overlay.querySelector("#contact-email-type").value,
        phone: overlay.querySelector("#contact-phone").value.trim(),
        phone_type: overlay.querySelector("#contact-phone-type").value,
        organization: overlay.querySelector("#contact-org").value.trim(),
        job_title: overlay.querySelector("#contact-job").value.trim(),
        birthday: overlay.querySelector("#contact-birthday").value || null,
      },
    };
    try {
      if (isEdit) {
        await api(`/api/items/${contactId}`, { method: "PUT", body: payload });
      } else {
        await api("/api/items", { method: "POST", body: payload });
      }
      overlay.remove();
      loadContacts();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  if (isEdit) {
    overlay.querySelector("#contact-delete").addEventListener("click", async () => {
      if (!confirm("确定要删除该联系人吗？")) return;
      try {
        await api(`/api/items/${contactId}`, { method: "DELETE" });
        overlay.remove();
        loadContacts();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
}

// ── 任务 ──
async function renderTasks() {
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace";

  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("nav.tasks", "任务")}</h2></div>
      <div class="toolbar">
        <button class="btn ${tasksState.viewMode === "kanban" ? "primary" : ""}" id="task-view-kanban">${t("tasks.kanban", "看板")}</button>
        <button class="btn ${tasksState.viewMode === "list" ? "primary" : ""}" id="task-view-list">${t("tasks.list", "列表")}</button>
        <input id="task-quick-add" placeholder="${t("tasks.newPlaceholder", "输入新任务，按回车添加...")}" />
      </div>
      <div id="task-container"><div class="empty-state">${t("tasks.loading", "加载任务...")}</div></div>
    </section>
  `;

  document.querySelector("#task-view-kanban").addEventListener("click", () => {
    tasksState.viewMode = "kanban";
    renderTaskContent();
  });
  document.querySelector("#task-view-list").addEventListener("click", () => {
    tasksState.viewMode = "list";
    renderTaskContent();
  });

  document.querySelector("#task-quick-add").addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const title = e.target.value.trim();
    if (!title) return;
    try {
      await api("/api/items", {
        method: "POST",
        body: { kind: "task", title, meta_json: { status: "todo", priority: 5 } },
      });
      e.target.value = "";
      await loadTasks();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  await loadTasks();
}

async function loadTasks() {
  try {
    const params = new URLSearchParams({ kind: "task" });
    if (tasksState.filterStatus) params.set("status", tasksState.filterStatus);
    const data = await api(`/api/items?${params.toString()}`);
    tasksState.tasks = data.items || [];
  } catch (error) {
    toast(error.message, "error");
    tasksState.tasks = [];
  }
  renderTaskContent();
}

function renderTaskContent() {
  const container = document.querySelector("#task-container");
  if (!tasksState.tasks.length) {
    container.innerHTML = `<div class="empty-state">${t("tasks.empty", "暂无任务")}</div>`;
    return;
  }

  if (tasksState.viewMode === "kanban") {
    renderTaskKanban(container);
  } else {
    renderTaskList(container);
  }
}

function renderTaskKanban(container) {
  const columns = [
    { key: "todo", label: t("tasks.statusTodo", "待办") },
    { key: "in_progress", label: t("tasks.statusInProgress", "进行中") },
    { key: "done", label: t("tasks.statusDone", "已完成") },
  ];

  container.innerHTML = `<div class="kanban-board">${columns
    .map((col) => {
      const items = tasksState.tasks.filter((t) => {
        const meta = t.meta_json || {};
        return (meta.status || "todo") === col.key;
      });
      return `<div class="kanban-col" data-status="${col.key}">
        <h3 class="kanban-col-title">${col.label} <span class="count">${items.length}</span></h3>
        <div class="kanban-col-body">${items
          .map((t) => taskCardHtml(t))
          .join("") || '<div class="kanban-empty">拖拽任务至此</div>'}</div>
      </div>`;
    })
    .join("")}</div>`;

  // 点击卡片打开编辑 modal
  container.querySelectorAll(".task-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("[data-next-status]") || e.target.closest("[data-delete-task]")) return;
      const id = Number(card.dataset.taskId);
      const task = tasksState.tasks.find((t) => t.id === id);
      if (task) showTaskModal(task);
    });
  });

  // 状态切换按钮
  container.querySelectorAll("[data-next-status]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.nextStatus);
      const cur = btn.dataset.curStatus;
      const next = cur === "todo" ? "in_progress" : "done";
      try {
        await api(`/api/items/${id}`, { method: "PUT", body: { meta_json: JSON.stringify({ status: next }) } });
        await loadTasks();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });

  // 删除按钮
  container.querySelectorAll("[data-delete-task]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(t("tasks.confirmDelete", "确定删除此任务？"))) return;
      try {
        await api(`/api/items/${btn.dataset.deleteTask}`, { method: "DELETE" });
        await loadTasks();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

function renderTaskList(container) {
  container.innerHTML = `<div class="task-list">${tasksState.tasks
    .map((t) => `<div class="task-list-item" data-task-id="${t.id}">${taskCardHtml(t)}</div>`)
    .join("")}</div>`;

  // 点击卡片打开编辑 modal
  container.querySelectorAll(".task-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("[data-next-status]") || e.target.closest("[data-delete-task]")) return;
      const id = Number(card.dataset.taskId);
      const task = tasksState.tasks.find((t) => t.id === id);
      if (task) showTaskModal(task);
    });
  });

  // 状态切换按钮
  container.querySelectorAll("[data-next-status]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.nextStatus);
      const cur = btn.dataset.curStatus;
      const next = cur === "todo" ? "in_progress" : "done";
      try {
        await api(`/api/items/${id}`, { method: "PUT", body: { meta_json: JSON.stringify({ status: next }) } });
        await loadTasks();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });

  // 删除按钮
  container.querySelectorAll("[data-delete-task]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(t("tasks.confirmDelete", "确定删除此任务？"))) return;
      try {
        await api(`/api/items/${btn.dataset.deleteTask}`, { method: "DELETE" });
        await loadTasks();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

function taskCardHtml(task) {
  const meta = task.meta_json || {};
  const priority = meta.priority ?? 5;
  let priorityColor = "#38a169";
  let priorityLabel = "";
  if (priority >= 9) { priorityColor = "#e53e3e"; priorityLabel = "高"; }
  else if (priority >= 7) { priorityColor = "#ed8936"; priorityLabel = "中"; }

  const dueDateHtml = meta.due_date
    ? `<span class="muted" style="font-size:0.85em">&#128197; ${esc(String(meta.due_date).slice(0, 10))}</span>`
    : "";

  const status = meta.status || "todo";
  const statusLabel = status === "todo" ? t("tasks.statusTodo", "待办") : status === "in_progress" ? t("tasks.statusInProgress", "进行中") : t("tasks.statusDone", "已完成");
  const nextLabel = status === "todo" ? t("tasks.statusInProgress", "进行中") : t("tasks.statusDone", "已完成");

  const tagsHtml = (meta.tags || []).length
    ? meta.tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")
    : "";

  return `<article class="task-card" data-task-id="${task.id}" style="border-left:3px solid ${priorityColor};padding:10px 12px;background:var(--surface, #f7f7f7);border-radius:8px">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
      <strong style="flex:1;word-break:break-word">${esc(task.title)}</strong>
      <button class="btn" style="font-size:11px;padding:2px 8px;flex-shrink:0" data-delete-task="${task.id}" title="${t("tasks.delete", "删除")}">&#10005;</button>
    </div>
    <div style="margin-top:6px;display:flex;flex-wrap:wrap;align-items:center;gap:6px">
      ${priorityLabel ? `<span style="background:${priorityColor};color:#fff;padding:1px 6px;border-radius:3px;font-size:0.75em">P${priority} ${priorityLabel}</span>` : ""}
      ${dueDateHtml}
      ${tagsHtml}
    </div>
    ${meta.description ? `<p style="margin:6px 0 0;font-size:0.85em;color:var(--muted, #888)">${esc(String(meta.description).slice(0, 80))}</p>` : ""}
    <div style="margin-top:8px;display:flex;align-items:center;gap:8px">
      ${status !== "done"
        ? `<button class="btn" style="font-size:11px;padding:3px 10px" data-next-status="${task.id}" data-cur-status="${status}">&#8594; ${esc(nextLabel)}</button>`
        : `<span class="tag" style="background:#38a169;color:#fff">&#10003; ${esc(statusLabel)}</span>`}
    </div>
  </article>`;
}

function showTaskModal(existing) {
  const oldOverlay = document.querySelector("#task-modal-overlay");
  if (oldOverlay) oldOverlay.remove();

  const isEdit = !!existing;
  const meta = existing ? existing.meta_json || {} : {};
  const taskId = isEdit ? existing.id : null;

  const overlay = document.createElement("div");
  overlay.id = "task-modal-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal task-modal">
      <h3>${isEdit ? t("tasks.title", "编辑任务") : t("tasks.new", "新建任务")}</h3>
      <div class="form-grid">
        <div class="field wide">
          <label>${t("tasks.title", "任务标题")}</label>
          <input id="task-title" value="${esc(isEdit ? existing.title : "")}" />
        </div>
        <div class="field wide">
          <label>${t("tasks.description", "任务描述")}</label>
          <textarea id="task-desc">${esc(meta.description || "")}</textarea>
        </div>
        <div class="field">
          <label>${t("tasks.priority", "优先级")} (1-10)</label>
          <input id="task-priority" type="number" min="1" max="10" value="${meta.priority ?? 5}" />
        </div>
        <div class="field">
          <label>${t("tasks.dueDate", "截止日期")}</label>
          <input id="task-due" type="date" value="${esc((meta.due_date || "").slice(0, 10))}" />
        </div>
        <div class="field">
          <label>${t("tasks.reminder", "提醒时间")}</label>
          <input id="task-reminder" type="datetime-local" value="${esc(meta.reminder || "")}" />
        </div>
        <div class="field wide">
          <label>${t("tasks.tags", "标签")} (逗号分隔)</label>
          <input id="task-tags" value="${esc((meta.tags || []).join(", "))}" />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn primary" id="task-save">${t("tasks.save", "保存任务")}</button>
        ${isEdit ? `<button class="btn danger" id="task-delete">${t("tasks.delete", "删除")}</button>` : ""}
        <button class="btn" id="task-cancel">取消</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  overlay.querySelector("#task-cancel").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

  overlay.querySelector("#task-save").addEventListener("click", async () => {
    const newMeta = {
      ...meta,
      description: overlay.querySelector("#task-desc").value,
      priority: Number(overlay.querySelector("#task-priority").value) || 5,
      due_date: overlay.querySelector("#task-due").value || null,
      reminder: overlay.querySelector("#task-reminder").value || null,
      tags: overlay.querySelector("#task-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
    };

    const payload = {
      kind: "task",
      title: overlay.querySelector("#task-title").value.trim(),
      meta_json: newMeta,
    };
    try {
      if (isEdit) {
        await api(`/api/items/${taskId}`, { method: "PUT", body: payload });
      } else {
        await api("/api/items", { method: "POST", body: payload });
      }
      overlay.remove();
      loadTasks();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  if (isEdit) {
    overlay.querySelector("#task-delete").addEventListener("click", async () => {
      if (!confirm("确定要删除该任务吗？")) return;
      try {
        await api(`/api/items/${taskId}`, { method: "DELETE" });
        overlay.remove();
        loadTasks();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
}

// ── 便签 ──
async function renderNotes() {
  const workspace = document.querySelector("#workspace");
  workspace.className = "workspace";

  workspace.innerHTML = `
    <section class="page-pane">
      <div class="page-header"><h2>${t("nav.notes", "便签")}</h2></div>
      <div class="toolbar">
        <button class="btn primary" id="note-new">+ ${t("notes.new", "新建便签")}</button>
        <select id="note-filter-cat">
          <option value="">全部</option>
          ${["个人", "工作", "学习", "灵感", "其他"].map((cat) => `<option value="${cat}" ${notesState.filterCategory === cat ? "selected" : ""}>${cat}</option>`).join("")}
        </select>
      </div>
      <div id="note-grid"><div class="empty-state">${t("notes.loading", "加载便签...")}</div></div>
    </section>
  `;

  document.querySelector("#note-new").addEventListener("click", () => showNoteModal());
  document.querySelector("#note-filter-cat").addEventListener("change", (e) => {
    notesState.filterCategory = e.target.value || null;
    loadNotes();
  });

  await loadNotes();
}

async function loadNotes() {
  const grid = document.querySelector("#note-grid");
  try {
    const params = new URLSearchParams({ kind: "note" });
    if (notesState.filterCategory) params.set("category", notesState.filterCategory);
    const data = await api(`/api/items?${params.toString()}`);
    notesState.notes = data.items || [];
  } catch (error) {
    toast(error.message, "error");
    notesState.notes = [];
  }

  if (!notesState.notes.length) {
    grid.innerHTML = `<div class="empty-state">${t("notes.empty", "暂无便签")}</div>`;
    return;
  }

  // 排序：置顶优先
  notesState.notes.sort((a, b) => {
    const aMeta = a.meta_json || {};
    const bMeta = b.meta_json || {};
    const aPin = aMeta.pinned ? 1 : 0;
    const bPin = bMeta.pinned ? 1 : 0;
    return bPin - aPin;
  });

  grid.innerHTML = notesState.notes
    .map((n) => {
      const meta = n.meta_json || {};
      const color = meta.color || "#F9E79F";
      const content = meta.content || "";
      const summary = content.replace(/[#*`>\[\]]/g, "").slice(0, 80);
      const tags = meta.tags || [];
      const pinned = !!meta.pinned;

      return `<article class="note-card" data-note-id="${n.id}" style="border-top:4px solid ${esc(color)}">
        <div class="note-card-head">
          <h4>${esc(n.title || "无标题")}${pinned ? ' <span class="pinned-mark">&#128204;</span>' : ""}</h4>
        </div>
        <p class="note-summary">${esc(summary)}</p>
        ${tags.length ? `<div class="note-tags">${tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}</div>` : ""}
        <div class="note-color-bar" style="background:${esc(color)};height:3px;margin-top:8px;border-radius:2px"></div>
      </article>`;
    })
    .join("");

  grid.querySelectorAll(".note-card").forEach((card) => {
    card.addEventListener("click", () => {
      const id = Number(card.dataset.noteId);
      const note = notesState.notes.find((n) => n.id === id);
      if (note) showNoteModal(note);
    });
  });
}

function showNoteModal(existing) {
  const oldOverlay = document.querySelector("#note-modal-overlay");
  if (oldOverlay) oldOverlay.remove();

  const isEdit = !!existing;
  const meta = existing ? existing.meta_json || {} : {};
  const noteId = isEdit ? existing.id : null;

  const colors = ["#F9E79F", "#AED6F1", "#D7BDE2", "#A3E4D7", "#FADBD8"];
  const colorNames = ["黄", "蓝", "紫", "绿", "粉"];
  const currColor = meta.color || "#F9E79F";

  const categories = ["个人", "工作", "学习", "灵感", "其他"];

  const overlay = document.createElement("div");
  overlay.id = "note-modal-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal note-modal note-full-modal">
      <div class="note-modal-toolbar">
        <button class="btn" id="note-toggle-preview">${t("notes.preview", "预览")}</button>
        ${isEdit && !meta.pinned ? `<button class="btn" id="note-pin">${t("notes.pin", "置顶")}</button>` : ""}
        ${isEdit && meta.pinned ? `<button class="btn" id="note-unpin">${t("notes.unpin", "取消置顶")}</button>` : ""}
        <button class="btn primary" id="note-save">${t("notes.save", "保存便签")}</button>
        ${isEdit ? `<button class="btn danger" id="note-delete">${t("notes.delete", "删除")}</button>` : ""}
        <button class="btn" id="note-cancel">取消</button>
      </div>
      <div class="form-grid" style="margin-top:12px">
        <div class="field" style="grid-column:1/-1">
          <input id="note-title" placeholder="${t("notes.title", "标题")}" value="${esc(isEdit ? existing.title : "")}" style="font-size:1.2em;font-weight:600" />
        </div>
        <div class="field">
          <label>${t("notes.category", "分类")}</label>
          <select id="note-category">
            ${categories.map((cat) => `<option value="${cat}" ${meta.category === cat ? "selected" : ""}>${cat}</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <label>${t("notes.color", "颜色")}</label>
          <div class="color-picker">
            ${colors.map((c, i) => `<span class="color-swatch ${c === currColor ? "active" : ""}" data-color="${c}" style="background:${c}" title="${colorNames[i]}"></span>`).join("")}
          </div>
          <input type="hidden" id="note-color" value="${esc(currColor)}" />
        </div>
        <div class="field" style="grid-column:1/-1">
          <label>${t("notes.tags", "标签")} (逗号分隔)</label>
          <input id="note-tags" value="${esc((meta.tags || []).join(", "))}" />
        </div>
        <div class="field" style="grid-column:1/-1">
          <label>${t("notes.content", "内容 (Markdown)")}</label>
          <textarea id="note-content" rows="14" style="font-family:monospace">${esc(meta.content || "")}</textarea>
          <div id="note-preview" style="display:none;padding:12px;background:var(--bg,#f8f8f8);border-radius:6px;margin-top:8px;min-height:200px"></div>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  overlay.querySelector("#note-cancel").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

  // 编辑/预览切换
  let previewMode = false;
  overlay.querySelector("#note-toggle-preview").addEventListener("click", () => {
    previewMode = !previewMode;
    const btn = overlay.querySelector("#note-toggle-preview");
    const textarea = overlay.querySelector("#note-content");
    const preview = overlay.querySelector("#note-preview");
    if (previewMode) {
      btn.textContent = t("notes.edit", "编辑");
      textarea.style.display = "none";
      preview.style.display = "block";
      preview.innerHTML = simpleMarkdown(textarea.value);
    } else {
      btn.textContent = t("notes.preview", "预览");
      textarea.style.display = "";
      preview.style.display = "none";
    }
  });

  // 颜色选择
  overlay.querySelectorAll(".color-swatch").forEach((swatch) => {
    swatch.addEventListener("click", () => {
      overlay.querySelectorAll(".color-swatch").forEach((s) => s.classList.remove("active"));
      swatch.classList.add("active");
      overlay.querySelector("#note-color").value = swatch.dataset.color;
    });
  });

  // 置顶/取消置顶
  const pinBtn = overlay.querySelector("#note-pin");
  const unpinBtn = overlay.querySelector("#note-unpin");
  if (pinBtn) {
    pinBtn.addEventListener("click", async () => {
      try {
        await api(`/api/items/${noteId}`, {
          method: "PUT",
          body: { kind: "note", title: existing.title, meta_json: { ...meta, pinned: true } },
        });
        overlay.remove();
        loadNotes();
      } catch (error) { toast(error.message, "error"); }
    });
  }
  if (unpinBtn) {
    unpinBtn.addEventListener("click", async () => {
      try {
        const newMeta = { ...meta };
        delete newMeta.pinned;
        await api(`/api/items/${noteId}`, {
          method: "PUT",
          body: { kind: "note", title: existing.title, meta_json: newMeta },
        });
        overlay.remove();
        loadNotes();
      } catch (error) { toast(error.message, "error"); }
    });
  }

  overlay.querySelector("#note-save").addEventListener("click", async () => {
    const newMeta = {
      ...meta,
      content: overlay.querySelector("#note-content").value,
      category: overlay.querySelector("#note-category").value,
      color: overlay.querySelector("#note-color").value,
      tags: overlay.querySelector("#note-tags").value.split(",").map((s) => s.trim()).filter(Boolean),
    };

    const payload = {
      kind: "note",
      title: overlay.querySelector("#note-title").value.trim() || "无标题",
      meta_json: newMeta,
    };
    try {
      if (isEdit) {
        await api(`/api/items/${noteId}`, { method: "PUT", body: payload });
      } else {
        await api("/api/items", { method: "POST", body: payload });
      }
      overlay.remove();
      loadNotes();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  if (isEdit) {
    overlay.querySelector("#note-delete").addEventListener("click", async () => {
      if (!confirm("确定要删除该便签吗？")) return;
      try {
        await api(`/api/items/${noteId}`, { method: "DELETE" });
        overlay.remove();
        loadNotes();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }
}

// 简易 Markdown 渲染（粗体、斜体、链接、标题、换行）
function simpleMarkdown(text) {
  if (!text) return "";
  return esc(text)
    .replace(/^### (.+)$/gm, "<h5>$1</h5>")
    .replace(/^## (.+)$/gm, "<h4>$1</h4>")
    .replace(/^# (.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
    .replace(/\n/g, "<br>");
}

// ── 同步任务进度查看 ──
async function showSyncJobsModal() {
  const oldOverlay = document.querySelector("#sync-jobs-modal-overlay");
  if (oldOverlay) oldOverlay.remove();

  const overlay = document.createElement("div");
  overlay.id = "sync-jobs-modal-overlay";
  overlay.className = "modal-overlay";
  overlay.innerHTML = `
    <div class="modal sync-jobs-modal" style="max-width:640px">
      <h3>${t("sync.jobsTitle", "同步任务进度")}</h3>
      <div id="sync-jobs-list"><div class="empty-state">${t("common.loading", "加载中...")}</div></div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn" id="sync-jobs-close">${t("common.close", "关闭")}</button>
      </div>
    </div>
  `;

  document.body.appendChild(overlay);

  overlay.querySelector("#sync-jobs-close").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });

  // 加载最近 5 条 sync jobs
  try {
    const jobs = await api("/api/sync/jobs?limit=5");
    const list = overlay.querySelector("#sync-jobs-list");
    if (!jobs || !jobs.length) {
      list.innerHTML = `<div class="empty-state">${t("sync.noJobs", "暂无同步任务")}</div>`;
      return;
    }
    const statusLabels = {
      pending: t("sync.statusPending", "等待中"),
      running: t("sync.statusRunning", "运行中"),
      success: t("sync.statusSuccess", "成功"),
      completed: t("sync.statusCompleted", "已完成"),
      failed: t("sync.statusFailed", "失败"),
    };
    const statusColors = {
      pending: "#f39c12",
      running: "#3498db",
      success: "#2ecc71",
      completed: "#2ecc71",
      failed: "#e74c3c",
    };
    list.innerHTML = jobs
      .map((job) => {
        const status = job.status || "pending";
        const label = statusLabels[status] || status;
        const color = statusColors[status] || "#888";
        return `<div class="item-card" style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <strong>#${job.id}</strong>
            <span class="tag" style="background:${color};color:#fff">${label}</span>
          </div>
          <p class="muted" style="margin:4px 0">
            ${t("sync.trigger", "触发方式")}: ${esc(job.trigger || "")}
            &nbsp;|&nbsp; ${t("sync.mailboxId", "邮箱")}: ${esc(String(job.mailbox_id || ""))}
          </p>
          <p class="muted" style="margin:0;font-size:0.8em">
            ${t("sync.created", "创建")}: ${esc(String(job.created_at || "").slice(0, 19))}
            ${job.started_at ? ` | ${t("sync.started", "开始")}: ${esc(String(job.started_at).slice(0, 19))}` : ""}
            ${job.finished_at ? ` | ${t("sync.finished", "完成")}: ${esc(String(job.finished_at).slice(0, 19))}` : ""}
          </p>
          ${job.error ? `<p style="color:#e74c3c;margin:4px 0 0;font-size:0.85em">${esc(job.error)}</p>` : ""}
        </div>`;
      })
      .join("");
  } catch (error) {
    const list = overlay.querySelector("#sync-jobs-list");
    list.innerHTML = `<div class="empty-state" style="color:#e74c3c">${esc(error.message)}</div>`;
  }
}

init();
