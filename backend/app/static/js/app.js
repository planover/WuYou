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
  viewMode: "month",
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
  let response = await fetch(`/static/locales/${state.locale}.json`);
  if (!response.ok) {
    try { const data = await api(`/api/locales/${state.locale}`); state.dict = data.messages || {}; document.documentElement.lang = state.locale; localStorage.setItem("wuyou.locale", state.locale); return; }
    catch { state.locale = "zh-CN"; localStorage.setItem("wuyou.locale", state.locale); response = await fetch("/static/locales/zh-CN.json"); }
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
  try {
    const themeData = await api(`/api/themes/${state.theme}`);
    const variables = themeData.variables || {};
    for (const [key, value] of Object.entries(variables)) { document.documentElement.style.setProperty(key, value); }
    delete document.documentElement.dataset.theme;
    localStorage.setItem("wuyou.theme", state.theme);
  } catch (error) { toast(error.message, "error"); state.theme = "light"; applyTheme(); }
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
  if (options.body && !(options.body instanceof FormData)) { headers.set("Content-Type", "application/json"); options.body = JSON.stringify(options.body); }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { localStorage.removeItem("wuyou.token"); state.token = ""; renderAuth(); throw new Error("请重新登录。"); }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) { throw new Error(data.detail || data.message || data || "请求失败。"); }
  return data;
}

async function init() {
  await applyTheme();
  await loadLocale(state.locale);
  if (!state.token) { renderAuth(); return; }
  try { state.user = await api("/api/auth/me"); await loadCommon(); renderShell(); await route("inbox"); }
  catch (error) { toast(error.message, "error"); renderAuth(); }
}

function renderAuth(mode = "register") {
  app.className = "";
  app.innerHTML = `<div class="auth-page"><div class="auth-card"><h1>📮 WuYou</h1><p class="slogan">你的邮件，都在坞里</p><div id="auth-form-area">${authFields(mode)}</div><p style="margin-top:16px;text-align:center"><a href="javascript:void(0)" id="switch-auth">${mode === "register" ? "已有账号？登录" : "还没有账号？注册"}</a></p></div></div>`;
  document.querySelector("#switch-auth").addEventListener("click", (e) => { e.preventDefault(); renderAuth(mode === "register" ? "login" : "register"); });
  document.querySelector("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload = mode === "login" ? { identifier: form.get("identifier"), password: form.get("password") } : { username: form.get("username") || null, email: form.get("email") || null, phone: form.get("phone") || null, password: form.get("password") };
    try {
      const result = await api(`/api/auth/${mode === "login" ? "login" : "register"}`, { method: "POST", body: payload });
      state.token = result.token; state.user = result.user; localStorage.setItem("wuyou.token", result.token);
      await loadCommon(); renderShell(); await route("inbox");
    } catch (error) { toast(error.message, "error"); }
  });
  const sendCodeBtn = document.querySelector("#send-code-btn");
  if (sendCodeBtn) sendCodeBtn.addEventListener("click", async function () {
    const email = document.querySelector("#reg-email")?.value?.trim() || "";
    const phone = document.querySelector("#reg-phone")?.value?.trim() || "";
    if (!email && !phone) { toast("请先输入邮箱或手机号", "error"); return; }
    const target = email ? { email } : { phone };
    try {
      const response = await fetch("/api/auth/verification-code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(target) });
      if (response.status === 429) { const data = await response.json(); const secs = parseInt(data.detail) || 60; _startCodeCountdown(this, secs); return; }
      if (response.status === 503) { toast(t("auth.smtpNotConfigured"), "error"); return; }
      if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "发送失败"); }
      toast(email ? t("auth.codeSentEmail") : t("auth.codeSentSms"));
      _startCodeCountdown(this, 60);
    } catch (error) { toast(error.message, "error"); }
  });
}

function authFields(mode) {
  if (mode === "register") return `<form class="auth-form" id="auth-form"><div class="auth-tabs" style="justify-content:center"><button type="button" data-auth="register" class="active">注册</button><button type="button" data-auth="login">登录</button></div><div class="field"><label>${t("auth.username")}</label><input name="username"/></div><div class="field"><label>${t("auth.email")}</label><input name="email" type="email" id="reg-email"/></div><div class="field"><label>${t("auth.phone")}</label><input name="phone" id="reg-phone"/></div><div class="field"><label>验证码</label><div style="display:flex;gap:8px"><input name="veri_code" placeholder="请输入验证码" style="flex:1"/><button type="button" class="btn" id="send-code-btn">发送验证码</button></div></div><div class="field"><label>${t("auth.password")}</label><input name="password" type="password" required minlength="8"/></div><button class="btn primary" type="submit" style="width:100%">${t("auth.register")}</button></form>`;
  return `<form class="auth-form" id="auth-form"><div class="auth-tabs" style="justify-content:center"><button type="button" data-auth="register">注册</button><button type="button" data-auth="login" class="active">登录</button></div><div class="field"><label>${t("auth.identifier")}</label><input name="identifier" required/></div><div class="field"><label>${t("auth.password")}</label><input name="password" type="password" required/></div><button class="btn primary" type="submit" style="width:100%">${t("auth.login")}</button></form>`;
}

let _countdownTimer = null;
function _startCodeCountdown(button, seconds) { clearInterval(_countdownTimer); button.disabled = true; let r = seconds; button.textContent = `${r}s`; _countdownTimer = setInterval(() => { r--; if (r <= 0) { clearInterval(_countdownTimer); button.disabled = false; button.textContent = "发送验证码"; } else button.textContent = `${r}s`; }, 1000); }

async function loadCommon() { const [a, t, u] = await Promise.all([api("/api/accounts"), api("/api/mail/tags"), api("/api/mail/unread")]); state.accounts = a; state.tags = t; state.unread = u.unread; }

function renderShell() {
  app.className = "";
  app.innerHTML = `<div class="app-shell"><header class="topbar"><div class="brand" style="cursor:pointer" onclick="route('inbox')">📮 WuYou</div><div class="top-actions"><select id="locale-select"><option value="zh-CN">简体中文</option><option value="zh-TW">繁體中文</option><option value="en-US">English</option></select><button class="btn" id="theme-toggle">${state.theme === "dark" ? "☀️" : "🌙"}</button><div class="user-menu" style="position:relative"><button class="btn avatar-btn" id="user-avatar" title="${esc(state.user?.username||'用户')}">${(state.user?.username||'U')[0].toUpperCase()}</button><div class="dropdown" id="user-dropdown" style="display:none;position:absolute;right:0;top:40px;background:var(--surface);border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.15);padding:8px 0;min-width:140px;z-index:100"><a href="javascript:route('settings')" style="display:block;padding:8px 16px;color:var(--text);text-decoration:none">${t("nav.settings")}</a><a href="javascript:route('about')" style="display:block;padding:8px 16px;color:var(--text);text-decoration:none">${t("nav.about")}</a><a href="javascript:doLogout()" style="display:block;padding:8px 16px;color:#e53e3e;text-decoration:none">${t("auth.logout")}</a></div></div></div></header><aside class="sidebar" id="sidebar"><div class="sidebar-header"><button class="btn sidebar-collapse-btn" id="sidebar-collapse">☰</button></div>${views.map(([id, key, fallback, icon]) => `<button class="nav-button ${state.view === id ? "active" : ""}" data-view="${id}"><span class="nav-icon">${icon}</span><span class="nav-label">${t(key, fallback)}</span>${id === "unread" && state.unread ? `<span class="count">${state.unread}</span>` : ""}</button>`).join("")}</aside><main class="workspace" id="workspace"></main></div>`;
  document.querySelector("#locale-select").value = state.locale;
  document.querySelector("#locale-select").addEventListener("change", async (e) => { state.locale = e.target.value; localStorage.setItem("wuyou.locale", state.locale); await loadLocale(); renderShell(); await route(state.view); });
  document.querySelector("#theme-toggle").addEventListener("click", async () => { state.theme = state.theme === "dark" ? "light" : "dark"; await applyTheme(); renderShell(); route(state.view); });
  document.querySelector("#user-avatar").addEventListener("click", () => { const dd = document.querySelector("#user-dropdown"); dd.style.display = dd.style.display === "none" ? "block" : "none"; });
  document.addEventListener("click", (e) => { if (!e.target.closest(".user-menu")) { const dd = document.querySelector("#user-dropdown"); if (dd) dd.style.display = "none"; } });
  document.querySelectorAll("[data-view]").forEach(btn => btn.addEventListener("click", () => route(btn.dataset.view)));
  const sidebar = document.querySelector("#sidebar");
  if (sidebar) {
    const handle = document.createElement("div"); handle.className = "sidebar-resize-handle"; handle.style.cssText = "width:4px;cursor:col-resize;background:var(--border);position:absolute;right:0;top:0;bottom:0;z-index:5;display:none"; sidebar.style.position = "relative"; sidebar.appendChild(handle);
    sidebar.addEventListener("mouseenter", () => { if (!sidebar.classList.contains("collapsed")) handle.style.display = "block"; });
    sidebar.addEventListener("mouseleave", () => { handle.style.display = "none"; });
    let dragging = false, sX = 0, sW = 0;
    handle.addEventListener("mousedown", (e) => { dragging = true; sX = e.clientX; sW = sidebar.offsetWidth; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; });
    document.addEventListener("mousemove", (e) => { if (!dragging) return; const w = Math.max(160, Math.min(400, sW + e.clientX - sX)); sidebar.style.width = w + "px"; const sh = document.querySelector(".app-shell"); if (sh) sh.style.gridTemplateColumns = w + "px 1fr"; });
    document.addEventListener("mouseup", () => { if (dragging) { dragging = false; document.body.style.cursor = ""; document.body.style.userSelect = ""; localStorage.setItem("wuyou.sidebarWidth", sidebar.style.width); } });
  }
  const scb = document.querySelector("#sidebar-collapse");
  if (scb) scb.addEventListener("click", () => {
    const s = document.querySelector("#sidebar"); const sh = document.querySelector(".app-shell"); const ic = s.classList.toggle("collapsed");
    if (ic) { s.style.width = "48px"; sh.style.gridTemplateColumns = "48px 1fr"; s.querySelectorAll(".nav-label, .count").forEach(el => el.style.display = "none"); }
    else { const sw = localStorage.getItem("wuyou.sidebarWidth") || "180px"; s.style.width = sw; sh.style.gridTemplateColumns = sw + " 1fr"; s.querySelectorAll(".nav-label, .count").forEach(el => el.style.display = ""); }
  });
}

async function doLogout() { try { await api("/api/auth/logout", { method: "POST" }); } catch {} localStorage.removeItem("wuyou.token"); state.token = ""; renderAuth(); }

async function route(view) {
  state.view = view;
  document.querySelectorAll(".nav-button").forEach(btn => btn.classList.toggle("active", btn.dataset.view === view));
  if (view === "inbox" || view === "unread") return renderInbox(view === "unread" ? "unread" : "all");
  const ws = document.querySelector("#workspace"); ws.className = "workspace";
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

async function renderInbox(status, query, folderRole) {
  if (folderRole !== null) state.folderRole = folderRole;
  const ws = document.querySelector("#workspace"); ws.className = "workspace inbox-split";
  ws.innerHTML = `<div class="mail-layout"><section class="list-pane"><div class="toolbar"><input id="mail-search"/><button class="btn" id="sync-all">${t("mail.sync")}</button><button class="btn" id="show-sync-jobs">📊 ${t("sync.jobs")}</button></div><div class="folder-tabs">${["all","inbox","sent","trash","archive","junk"].map(r => `<button class="folder-tab" data-folder="${r}">${r}</button>`).join("")}</div><div id="mail-list"><div class="empty-state">${t("common.loading")}</div></div></section></div><section class="reader-pane" id="reader"><div class="reader-empty">${t("mail.pick")}</div></section>`;
  document.querySelector("#sync-all").addEventListener("click", syncAll);
  document.querySelector("#show-sync-jobs").addEventListener("click", showSyncJobsModal);
}

async function syncAll() { if (!state.accounts.length) { toast(t("accounts.needFirst"), "error"); return; } for (const a of state.accounts) try { await api(`/api/accounts/${a.id}/sync`, { method: "POST" }); } catch(e) {} }

async function renderCalendar() {
  const ws = document.querySelector("#workspace"); ws.className = "workspace";
  const y = calendarState.currentDate.getFullYear(); const m = calendarState.currentDate.getMonth();
  const fd = `${y}-${String(m+1).padStart(2,"0")}-01`; const ld = new Date(y,m+1,0).getDate();
  const td = `${y}-${String(m+1).padStart(2,"0")}-${String(ld).padStart(2,"0")}`;
  ws.innerHTML = `<section class="page-pane"><div class="page-header"><h2>${t("nav.calendar")}</h2></div><div class="toolbar"><button class="btn" id="cal-prev">${t("calendar.prev")}</button><span class="cal-title">${y}年${m+1}月</span><button class="btn" id="cal-next">${t("calendar.next")}</button><button class="btn primary" id="cal-new-event">+${t("calendar.newEvent")}</button></div><div id="cal-grid" class="cal-grid-wrapper">${t("calendar.loading")}</div></section>`;
  document.querySelector("#cal-prev").addEventListener("click", () => { calendarState.currentDate.setMonth(calendarState.currentDate.getMonth()-1); renderCalendar(); });
  document.querySelector("#cal-next").addEventListener("click", () => { calendarState.currentDate.setMonth(calendarState.currentDate.getMonth()+1); renderCalendar(); });
  document.querySelector("#cal-new-event").addEventListener("click", () => showEventModal());
  try { const d = await api(`/api/items?kind=calendar_event&from_date=${fd}&to_date=${td}`); calendarState.events = d.items || []; } catch { calendarState.events = []; }
  renderMonthGrid(y, m);
}

function renderMonthGrid(y, m) {
  const g = document.querySelector("#cal-grid"); const td = new Date();
  const em = {}; calendarState.events.forEach(ev => { const mt = ev.meta_json || {}; const k = String(mt.start_at || "").slice(0,10); if (!em[k]) em[k] = []; em[k].push(ev); });
  const fD = new Date(y,m,1).getDay(); const dM = new Date(y,m+1,0).getDate();
  const so = fD === 0 ? 6 : fD - 1;
  let h = '<div class="cal-weekdays">'; ["一","二","三","四","五","六","日"].forEach(d => { h += `<div class="cal-weekday">${d}</div>`; }); h += "</div>";
  const tc = Math.ceil((so + dM) / 7) * 7; let day = 1;
  for (let i = 0; i < tc; i++) {
    if (i % 7 === 0) h += '<div class="cal-week">';
    if (i < so) h += '<div class="cal-day other-month"></div>';
    else if (day > dM) { h += '<div class="cal-day other-month"></div>'; day++; }
    else {
      const ds = `${y}-${String(m+1).padStart(2,"0")}-${String(day).padStart(2,"0")}`;
      const iT = td.getFullYear()===y&&td.getMonth()===m&&td.getDate()===day;
      const evs = em[ds]||[]; const dh = evs.slice(0,3).map(ev=>`<span class="cal-dot" data-event-id="${ev.id}" style="background:${esc((ev.meta_json||{}).color||"#4A90D9")}"></span>`).join("");
      h += `<div class="cal-day${iT?" today":""}" data-date="${ds}"><span class="cal-day-num">${day}</span><div class="cal-dots">${dh}</div></div>`;
      day++;
    }
    if ((i+1)%7===0||i===tc-1) h += "</div>";
  }
  g.innerHTML = h;
  g.querySelectorAll(".cal-day:not(.other-month)").forEach(c => { c.addEventListener("click",(e)=>{ if(!e.target.classList.contains("cal-dot")) showEventModal(c.dataset.date); }); });
  g.querySelectorAll(".cal-dot").forEach(dt => { dt.addEventListener("click",(e)=>{ e.stopPropagation(); const ev=calendarState.events.find(i=>i.id===Number(dt.dataset.eventId)); if(ev) showEventModal(null,ev); }); });
}

function showEventModal(ds, ex) {
  const oo = document.querySelector("#cal-modal-overlay"); if (oo) oo.remove();
  const ie = !!ex; const em = ie ? (ex.meta_json||{}) : {};
  const et = ie ? ex.title||"" : "";
  const es = ie ? (em.start_at ? String(em.start_at).slice(0,16) : "") : ds ? (ds+"T09:00") : new Date().toISOString().slice(0,16);
  const ee = ie ? (em.end_at ? String(em.end_at).slice(0,16) : "") : "";
  const ov = document.createElement("div"); ov.id = "cal-modal-overlay"; ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal"><h3>${ie?"编辑事件":"新建事件"}</h3><div class="form-grid"><div class="field wide"><label>标题</label><input id="cal-ev-title" value="${esc(et)}"/></div><div class="field"><label>开始</label><input id="cal-ev-start" type="datetime-local" value="${esc(es)}"/></div><div class="field"><label>结束</label><input id="cal-ev-end" type="datetime-local" value="${esc(ee)}"/></div></div><div class="btn-row"><button class="btn primary" id="cal-ev-save">保存</button>${ie?'<button class="btn danger" id="cal-ev-delete">删除</button>':""}<button class="btn" id="cal-ev-cancel">取消</button></div></div>`;
  document.body.appendChild(ov);
  ov.querySelector("#cal-ev-cancel").addEventListener("click",()=>ov.remove());
  ov.addEventListener("click",(e)=>{ if(e.target===ov) ov.remove(); });
  ov.querySelector("#cal-ev-save").addEventListener("click",async()=>{
    const p = { kind:"calendar_event", title:ov.querySelector("#cal-ev-title").value, meta_json:{ start_at:ov.querySelector("#cal-ev-start").value||null, end_at:ov.querySelector("#cal-ev-end").value||null } };
    try { if (ie) await api(`/api/items/${ex.id}`,{method:"PUT",body:p}); else await api("/api/items",{method:"POST",body:p}); ov.remove(); renderCalendar(); } catch(e) { toast(e.message,"error"); }
  });
  if (ie) ov.querySelector("#cal-ev-delete").addEventListener("click",async()=>{ try { await api(`/api/items/${ex.id}`,{method:"DELETE"}); ov.remove(); renderCalendar(); } catch(e) { toast(e.message,"error"); } });
}

function renderCompose() {
  const ws = document.querySelector("#workspace");
  const dr = JSON.parse(localStorage.getItem("wuyou.draft")||"{}");
  ws.innerHTML = `<section class="page-pane"><div class="page-header"><h2>${t("compose.title")}</h2></div><form class="panel item-card compose" id="compose-form"><div class="form-grid"><div class="field wide"><label>${t("compose.from")}</label><select name="mailbox_id" required>${state.accounts.map(a=>`<option value="${a.id}">${esc(a.display_name)}</option>`).join("")}</select></div><div class="field wide"><label>${t("compose.to")}</label><input name="recipients" required/></div><div class="field wide"><label>${t("compose.subject")}</label><input name="subject" required/></div><div class="field wide"><label>${t("compose.body")}</label><div class="format-toolbar"><button type="button" class="btn" data-format="bold"><b>B</b></button><button type="button" class="btn" data-format="italic"><i>I</i></button><button type="button" class="btn" data-format="list">-</button><button type="button" class="btn" data-format="link">link</button></div><textarea name="body" id="compose-body">${esc(dr.body||"")}</textarea></div></div><div class="btn-row"><button class="btn primary" type="submit" id="compose-send">${t("compose.send")}</button><button class="btn" type="button" id="compose-draft">${t("compose.saveDraft")}</button><button class="btn" type="button" id="compose-cancel">${t("compose.cancel")}</button></div></form></section>`;
  const bt = document.querySelector("#compose-body");
  document.querySelectorAll(".format-toolbar [data-format]").forEach(b => { b.addEventListener("click",()=>{ const fmt=b.dataset.format; const ta=bt; const s=ta.selectionStart; const e=ta.selectionEnd; const sel=ta.value.substring(s,e); let be="",af=""; if(fmt==="bold"){be="**";af="**"}else if(fmt==="italic"){be="*";af="*"}else if(fmt==="list"){be="\n- ";af=""}else if(fmt==="link"){be="[";af=`](${sel||"url"})`}ta.setRangeText(be+sel+af,s,e,"select");ta.focus();}); });
  document.querySelector("#compose-draft").addEventListener("click",()=>{ const f=new FormData(document.querySelector("#compose-form")); localStorage.setItem("wuyou.draft",JSON.stringify({mailbox_id:f.get("mailbox_id"),recipients:f.get("recipients"),subject:f.get("subject"),body:f.get("body")})); toast(t("compose.draftSaved")); });
  document.querySelector("#compose-cancel").addEventListener("click",()=>{ if(confirm(t("compose.cancelConfirm"))){ localStorage.removeItem("wuyou.draft"); route("inbox"); } });
  document.querySelector("#compose-form").addEventListener("submit",async(e)=>{ e.preventDefault(); const f=new FormData(e.currentTarget); const sb=document.querySelector("#compose-send"); sb.disabled=true; sb.textContent=t("compose.sending"); try{ await api("/api/mail/send",{method:"POST",body:{mailbox_id:Number(f.get("mailbox_id")),recipients:String(f.get("recipients")).split(",").map(i=>i.trim()).filter(Boolean),subject:f.get("subject"),body:f.get("body")}}); localStorage.removeItem("wuyou.draft"); e.currentTarget.reset(); }catch(e){toast(e.message,"error")}finally{sb.disabled=false;sb.textContent=t("compose.send")} });
}

async function renderAccounts() {
  const ws = document.querySelector("#workspace"); ws.className = "workspace";
  ws.innerHTML = `<section class="page-pane"><div class="page-header"><h2>${t("accounts.title")}</h2></div><div class="grid" id="accounts-grid"><div class="empty-state">加载中...</div></div><form class="panel item-card" id="account-form" style="margin-top:14px"><h3>${t("accounts.add")}</h3><div class="form-grid"><div class="field"><label>${t("accounts.display")}</label><input name="display_name" required/></div><div class="field"><label>${t("accounts.email")}</label><input name="email_address" type="email" required/></div><div class="field"><label>${t("accounts.secret")}</label><input name="secret" type="password" required/></div></div><button class="btn primary" type="submit">${t("accounts.save")}</button></form><article class="item-card" style="margin-top:14px"><h3>${t("accounts.tbImport")}</h3><div class="field"><input id="tb-profile-path"/></div><button class="btn" id="tb-import-btn">${t("accounts.tbImportBtn")}</button></article></section>`;
  if (state.accounts.length > 0) {
    const g = document.querySelector("#accounts-grid");
    const aw = await Promise.all(state.accounts.map(async a => { let lj = null; try { const j = await api(`/api/sync/jobs?mailbox_id=${a.id}&limit=1`); lj = (j&&j.length>0)?j[0]:null; } catch {} return {account:a,lastJob:lj}; }));
    g.innerHTML = aw.map(({account:a,lastJob:lj}) => { let st="未知",sc="var(--muted)",ls="从未"; if(lj){ if(lj.status==="success"){st="在线";sc="var(--green)"}else if(lj.status==="failed"){st="错误";sc="var(--red)"}else if(lj.status==="running"){st="同步中";sc="var(--yellow)"}; if(lj.finished_at)ls=new Date(lj.finished_at).toLocaleString(); } return `<article class="item-card"><h3>${esc(a.display_name)}</h3><p>${esc(a.email_address)}</p><span style="color:${sc}">${st}</span> | ${ls}</article>`; }).join("");
  }
  document.querySelector("#account-form").addEventListener("submit",async(e)=>{ e.preventDefault(); const f=new FormData(e.currentTarget); try{ await api("/api/accounts",{method:"POST",body:{display_name:f.get("display_name"),email_address:f.get("email_address"),secret:f.get("secret")}}); await loadCommon(); renderShell(); route("accounts"); }catch(e){toast(e.message,"error")} });
}

async function renderPlugins() { const ws = document.querySelector("#workspace"); ws.innerHTML = `<section class="page-pane"><h2>${t("plugins.title")}</h2></section>`; }

async function renderSettings() {
  const ws = document.querySelector("#workspace");
  const d = await api("/api/settings");
  const s = d.settings || {};
  const th = state.theme; const lo = state.locale;
  const tl = s["telemetry_enabled"] === true || s["telemetry_enabled"] === "true" || s["telemetry_enabled"] === 1;
  const ru = s["remote_sync_endpoint"] || "";
  ws.innerHTML = `<section class="page-pane"><h2>${t("nav.settings")}</h2><div class="settings-form"><div class="form-group"><label>${t("settings.theme")}</label><select id="set-theme"><option value="light" ${th==="light"?"selected":""}>日间模式</option><option value="dark" ${th==="dark"?"selected":""}>夜间模式</option></select></div><div class="form-group"><label>${t("settings.language")}</label><select id="set-locale"><option value="zh-CN" ${lo==="zh-CN"?"selected":""}>简体中文</option><option value="en-US" ${lo==="en-US"?"selected":""}>English</option><option value="zh-TW" ${lo==="zh-TW"?"selected":""}>繁體中文</option></select></div><div class="form-group"><label>${t("settings.telemetry")}</label><input type="checkbox" id="set-telemetry" ${tl?"checked":""}><small>${t("settings.telemetryHelp")}</small></div><div class="form-group"><label>${t("settings.remoteSyncEndpoint")}</label><input type="text" id="set-remote-url" value="${esc(ru)}" style="width:100%"></div><div class="form-group"><label>${t("settings.changePassword")}</label><input type="password" id="set-old-pw" placeholder="原密码"><input type="password" id="set-new-pw" placeholder="新密码(至少8位)"><button class="btn primary" id="btn-change-pw">${t("settings.save")}</button></div><div class="form-group"><label>${t("settings.changeEmail")}</label><input type="email" id="set-new-email" placeholder="新邮箱"><button class="btn primary" id="btn-send-email-code">发送验证码</button><input type="text" id="set-email-code" placeholder="验证码" maxlength="6"><button class="btn" id="btn-confirm-email">确认修改</button></div><div class="btn-row"><button class="btn primary" id="btn-save-settings">${t("settings.saveAll")}</button></div></div></section>`;
  document.getElementById("btn-save-settings").onclick = async () => {
    const nt = document.getElementById("set-theme").value; if (nt !== state.theme) { state.theme = nt; localStorage.setItem("wuyou.theme", state.theme); applyTheme(); }
    const nl = document.getElementById("set-locale").value; if (nl !== state.locale) { state.locale = nl; localStorage.setItem("wuyou.locale", state.locale); await loadLocale(state.locale); renderShell(); }
    const te = document.getElementById("set-telemetry").checked; await api("/api/settings", { method: "PUT", body: {key:"telemetry_enabled", value: te} });
    const rl = document.getElementById("set-remote-url").value.trim(); if (rl) await api("/api/settings", { method: "PUT", body: {key:"remote_sync_endpoint", value: rl} });
    toast(t("settings.saved"));
  };
  document.getElementById("btn-change-pw").onclick = async () => {
    const o = document.getElementById("set-old-pw").value; const n = document.getElementById("set-new-pw").value;
    if (!o || !n) return toast("请填写密码", "error"); if (n.length < 8) return toast("新密码至少8位", "error");
    const r = await api("/api/auth/change-password", { method: "PUT", body: {old_password: o, new_password: n} });
    if (r.message) toast(r.message);
  };
  document.getElementById("btn-send-email-code").onclick = async () => {
    const em = document.getElementById("set-new-email").value.trim(); if (!em || !em.includes("@")) return toast("请输入有效邮箱", "error");
    await api("/api/auth/verification-code", { method: "POST", body: {target_type:"email", target:em, purpose:"change_contact"} });
  };
  document.getElementById("btn-confirm-email").onclick = async () => {
    const em = document.getElementById("set-new-email").value.trim(); const cd = document.getElementById("set-email-code").value.trim();
    if (!em || !cd) return toast("请填写邮箱和验证码", "error");
    await api("/api/auth/change-contact", { method: "PUT", body: {target_type:"email", target:em, code:cd} });
  };
}

async function renderAbout() {
  const ws = document.querySelector("#workspace");
  ws.innerHTML = `<section class="page-pane"><h2>${t("about.title")}</h2><div class="item-card"><h3>WuYou</h3><p>${t("about.slogan")}</p><p><button class="btn" id="show-changelog">${t("about.changelog")}</button></p></div></section>`;
  document.getElementById("show-changelog").onclick = () => {
    const m = document.createElement("div"); m.className = "modal-overlay";
    m.innerHTML = `<div class="modal" style="max-width:700px"><h3>更新日志</h3><h4>v1.0.1</h4><p>🔒 安全加固+国际化完善</p><h4>v1.0.0</h4><p>首个正式版本</p><button class="btn" onclick="this.closest('.modal-overlay').remove()">关闭</button></div>`;
    document.body.appendChild(m); m.onclick = (e) => { if (e.target === m) m.remove(); };
  };
}

async function renderContacts() {
  const ws = document.querySelector("#workspace"); ws.className = "workspace";
  ws.innerHTML = `<section class="page-pane"><div class="page-header"><h2>${t("nav.contacts")}</h2></div><div class="toolbar"><button class="btn primary" id="contact-new">+${t("contacts.new")}</button></div><div id="contact-list"><div class="empty-state">${t("contacts.loading")}</div></div></section>`;
  document.querySelector("#contact-new").addEventListener("click", () => showContactModal());
  await loadContacts();
}

async function loadContacts() {
  const l = document.querySelector("#contact-list");
  try { const d = await api("/api/items?kind=contact"); contactsState.contacts = d.items || []; } catch { contactsState.contacts = []; }
  if (!contactsState.contacts.length) { l.innerHTML = `<div class="empty-state">${t("contacts.empty")}</div>`; return; }
  l.innerHTML = contactsState.contacts.map(c => { const m = c.meta_json || {}; return `<article class="item-card contact-card" data-contact-id="${c.id}"><h3>${esc(m.first_name || c.title || "")} ${esc(m.last_name || "")}</h3></article>`; }).join("");
  l.querySelectorAll(".contact-card").forEach(c => { c.addEventListener("click",()=>{ const ct = contactsState.contacts.find(c=>c.id===Number(c.dataset.contactId)); if(ct) showContactModal(ct); }); });
}

function showContactModal(ex) {
  const oo = document.querySelector("#contact-modal-overlay"); if (oo) oo.remove();
  const ie = !!ex; const m = ex ? ex.meta_json || {} : {};
  const ov = document.createElement("div"); ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal"><h3>${ie?"编辑":"新建联系人"}</h3><div class="form-grid"><div class="field"><label>名</label><input id="contact-first" value="${esc(m.first_name||"")}"/></div><div class="field"><label>姓</label><input id="contact-last" value="${esc(m.last_name||"")}"/></div><div class="field"><label>邮箱</label><input id="contact-email" value="${esc(m.email||"")}"/></div><div class="field"><label>电话</label><input id="contact-phone" value="${esc(m.phone||"")}"/></div></div><div class="btn-row"><button class="btn primary" id="contact-save">保存</button>${ie?'<button class="btn danger" id="contact-delete">删除</button>':""}<button class="btn" id="contact-cancel">取消</button></div></div>`;
  document.body.appendChild(ov); ov.querySelector("#contact-cancel").addEventListener("click",()=>ov.remove());
  ov.querySelector("#contact-save").addEventListener("click",async()=>{
    const p = { kind:"contact", title:[ov.querySelector("#contact-first").value,ov.querySelector("#contact-last").value].filter(Boolean).join(" ")||"未命名", meta_json:{first_name:ov.querySelector("#contact-first").value,last_name:ov.querySelector("#contact-last").value,email:ov.querySelector("#contact-email").value,phone:ov.querySelector("#contact-phone").value} };
    try { if (ie) await api(`/api/items/${ex.id}`,{method:"PUT",body:p}); else await api("/api/items",{method:"POST",body:p}); ov.remove(); loadContacts(); } catch(e) { toast(e.message,"error"); }
  });
}

async function renderTasks() {
  const ws = document.querySelector("#workspace"); ws.className = "workspace";
  ws.innerHTML = `<section class="page-pane"><div class="page-header"><h2>${t("nav.tasks")}</h2></div><div class="toolbar"><button class="btn ${tasksState.viewMode==="kanban"?"primary":""}" id="task-view-kanban">${t("tasks.kanban")}</button><button class="btn ${tasksState.viewMode==="list"?"primary":""}" id="task-view-list">${t("tasks.list")}</button><input id="task-quick-add" placeholder="${t("tasks.newPlaceholder")}"/></div><div id="task-container"><div class="empty-state">${t("tasks.loading")}</div></div></section>`;
  document.querySelector("#task-quick-add").addEventListener("keydown",async(e)=>{ if(e.key!=="Enter")return; const t=e.target.value.trim(); if(!t)return; try{ await api("/api/items",{method:"POST",body:{kind:"task",title:t,meta_json:{status:"todo",priority:5}}}); e.target.value=""; await loadTasks(); }catch(e){toast(e.message,"error")} });
  await loadTasks();
}

async function loadTasks() { try { const d = await api("/api/items?kind=task"); tasksState.tasks = d.items || []; } catch { tasksState.tasks = []; } renderTaskContent(); }

function renderTaskContent() {
  const c = document.querySelector("#task-container");
  if (!tasksState.tasks.length) { c.innerHTML = `<div class="empty-state">${t("tasks.empty")}</div>`; return; }
  if (tasksState.viewMode === "kanban") renderTaskKanban(c); else renderTaskList(c);
}

function renderTaskKanban(c) {
  const cols = [{key:"todo",label:t("tasks.statusTodo")},{key:"in_progress",label:t("tasks.statusInProgress")},{key:"done",label:t("tasks.statusDone")}];
  c.innerHTML = `<div class="kanban-board">${cols.map(col=>{ const it = tasksState.tasks.filter(t=>{ const m=t.meta_json||{}; return (m.status||"todo")===col.key; }); return `<div class="kanban-col"><h3>${col.label} (${it.length})</h3>${it.map(t=>taskCardHtml(t)).join("")}</div>`; }).join("")}</div>`;
  c.querySelectorAll(".task-card").forEach(cd => { cd.addEventListener("click",()=>{ const tk = tasksState.tasks.find(t=>t.id===Number(cd.dataset.taskId)); if(tk) showTaskModal(tk); }); });
}

function renderTaskList(c) { c.innerHTML = tasksState.tasks.map(t=>taskCardHtml(t)).join(""); c.querySelectorAll(".task-card").forEach(cd => { cd.addEventListener("click",()=>{ const tk = tasksState.tasks.find(t=>t.id===Number(cd.dataset.taskId)); if(tk) showTaskModal(tk); }); }); }

function taskCardHtml(t) { const m = t.meta_json||{}; const pr = m.priority??5; return `<article class="task-card" data-task-id="${t.id}"><strong>${esc(t.title)}</strong> P${pr}</article>`; }

function showTaskModal(ex) {
  const ie = !!ex; const m = ex ? ex.meta_json || {} : {};
  const ov = document.createElement("div"); ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal"><h3>${ie?"编辑任务":"新建任务"}</h3><div class="form-grid"><div class="field wide"><label>标题</label><input id="task-title" value="${esc(ie?ex.title:"")}"/></div></div><div class="btn-row"><button class="btn primary" id="task-save">保存</button>${ie?'<button class="btn danger" id="task-delete">删除</button>':""}<button class="btn" id="task-cancel">取消</button></div></div>`;
  document.body.appendChild(ov); ov.querySelector("#task-cancel").addEventListener("click",()=>ov.remove());
  ov.querySelector("#task-save").addEventListener("click",async()=>{ try{ if(ie) await api(`/api/items/${ex.id}`,{method:"PUT",body:{kind:"task",title:ov.querySelector("#task-title").value,meta_json:m}}); else await api("/api/items",{method:"POST",body:{kind:"task",title:ov.querySelector("#task-title").value,meta_json:{status:"todo",priority:5}}}); ov.remove(); loadTasks(); }catch(e){toast(e.message,"error")} });
}

async function renderNotes() {
  const ws = document.querySelector("#workspace"); ws.className = "workspace";
  ws.innerHTML = `<section class="page-pane"><div class="page-header"><h2>${t("nav.notes")}</h2></div><div class="toolbar"><button class="btn primary" id="note-new">+${t("notes.new")}</button></div><div id="note-grid"><div class="empty-state">${t("notes.loading")}</div></div></section>`;
  document.querySelector("#note-new").addEventListener("click",()=>showNoteModal());
  await loadNotes();
}

async function loadNotes() {
  const g = document.querySelector("#note-grid");
  try { const d = await api("/api/items?kind=note"); notesState.notes = d.items || []; } catch { notesState.notes = []; }
  if (!notesState.notes.length) { g.innerHTML = `<div class="empty-state">${t("notes.empty")}</div>`; return; }
  g.innerHTML = notesState.notes.map(n => { const m = n.meta_json||{}; return `<article class="note-card" data-note-id="${n.id}" style="border-top:4px solid ${esc(m.color||"#F9E79F")}"><h4>${esc(n.title||"无标题")}</h4></article>`; }).join("");
  g.querySelectorAll(".note-card").forEach(c => { c.addEventListener("click",()=>{ const nt = notesState.notes.find(n=>n.id===Number(c.dataset.noteId)); if(nt) showNoteModal(nt); }); });
}

function showNoteModal(ex) {
  const ie = !!ex; const m = ex ? ex.meta_json || {} : {};
  const ov = document.createElement("div"); ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal"><h3>${ie?"编辑便签":"新建便签"}</h3><div class="form-grid"><div class="field wide"><label>标题</label><input id="note-title" value="${esc(ie?ex.title:"")}"/></div><div class="field wide"><label>内容</label><textarea id="note-content">${esc(m.content||"")}</textarea></div></div><div class="btn-row"><button class="btn primary" id="note-save">保存</button>${ie?'<button class="btn danger" id="note-delete">删除</button>':""}<button class="btn" id="note-cancel">取消</button></div></div>`;
  document.body.appendChild(ov); ov.querySelector("#note-cancel").addEventListener("click",()=>ov.remove());
  ov.querySelector("#note-save").addEventListener("click",async()=>{ const p = {kind:"note",title:ov.querySelector("#note-title").value.trim()||"无标题",meta_json:{...m,content:ov.querySelector("#note-content").value}}; try{ if(ie) await api(`/api/items/${ex.id}`,{method:"PUT",body:p}); else await api("/api/items",{method:"POST",body:p}); ov.remove(); loadNotes(); }catch(e){toast(e.message,"error")} });
}

async function showSyncJobsModal() {
  const ov = document.createElement("div"); ov.className = "modal-overlay";
  ov.innerHTML = `<div class="modal sync-jobs-modal" style="max-width:640px"><h3>${t("sync.jobsTitle")}</h3><div id="sync-jobs-list"><div class="empty-state">${t("common.loading")}</div></div><div class="btn-row"><button class="btn" id="sync-jobs-close">${t("common.close")}</button></div></div>`;
  document.body.appendChild(ov); ov.querySelector("#sync-jobs-close").addEventListener("click",()=>ov.remove());
  try {
    const js = await api("/api/sync/jobs?limit=5"); const l = ov.querySelector("#sync-jobs-list");
    if (!js || !js.length) { l.innerHTML = `<div class="empty-state">${t("sync.noJobs")}</div>`; return; }
    l.innerHTML = js.map(j => `<div class="item-card"><strong>#${j.id}</strong> <span>${j.status||"pending"}</span><p>${esc(j.trigger||"")} | ${esc(String(j.mailbox_id||""))}</p>${j.error?`<p style="color:red">${esc(j.error)}</p>`:""}</div>`).join("");
  } catch(e) { ov.querySelector("#sync-jobs-list").innerHTML = `<div class="empty-state" style="color:red">${esc(e.message)}</div>`; }
}

init();
