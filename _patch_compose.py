import re

file_path = r"C:\Users\姓名\Documents\Codex\WuYou\backend\app\static\js\app.js"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

count = 0

# ── 1. 替换 toolbar HTML + textarea → contenteditable div + hidden input ──
old_toolbar_html = '''            <div class="format-toolbar" style="margin-bottom:8px;display:flex;gap:4px">
              <button type="button" class="btn" data-format="bold" title="${t("compose.toolbarBold","加粗")}"><b>B</b></button>
              <button type="button" class="btn" data-format="italic" title="${t("compose.toolbarItalic","斜体")}"><i>I</i></button>
              <button type="button" class="btn" data-format="list" title="${t("compose.toolbarList","无序列表")}">•</button>
              <button type="button" class="btn" data-format="link" title="${t("compose.toolbarLink","插入链接")}">🔗</button>
            </div>
            <textarea name="body" id="compose-body">${esc(draft.body || "")}</textarea>'''

new_toolbar_html = '''            <div class="format-toolbar" style="margin-bottom:8px;display:flex;gap:4px">
              <button type="button" class="btn" data-format="bold" title="${t("compose.toolbarBold","加粗")}"><b>B</b></button>
              <button type="button" class="btn" data-format="italic" title="${t("compose.toolbarItalic","斜体")}"><i>I</i></button>
              <button type="button" class="btn" data-format="underline" title="${t("compose.toolbarUnderline","下划线")}"><u>U</u></button>
              <button type="button" class="btn" data-format="strikethrough" title="${t("compose.toolbarStrikethrough","删除线")}"><s>S</s></button>
              <select data-format="fontSize" style="width:80px">
                <option value="1">${t("compose.fontSizeSmall","小")}</option>
                <option value="3" selected>${t("compose.fontSizeMedium","中")}</option>
                <option value="5">${t("compose.fontSizeLarge","大")}</option>
                <option value="7">${t("compose.fontSizeHuge","超大")}</option>
              </select>
              <button type="button" class="btn" data-format="insertUnorderedList" title="${t("compose.toolbarList","无序列表")}">•</button>
              <button type="button" class="btn" data-format="insertOrderedList" title="${t("compose.toolbarOrderedList","有序列表")}">1.</button>
              <button type="button" class="btn" data-format="link" title="${t("compose.toolbarLink","插入链接")}">🔗</button>
              <button type="button" class="btn" data-format="removeFormat" title="${t("compose.toolbarClearFormat","清除")}">Tx</button>
            </div>
            <div id="compose-body" contenteditable="true" style="min-height:200px;padding:8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);outline:none;overflow-y:auto;max-height:500px">${draft.body || ""}</div>
            <input type="hidden" name="body" id="compose-body-hidden" value="" />'''

while old_toolbar_html in content:
    content = content.replace(old_toolbar_html, new_toolbar_html, 1)
    count += 1
    print(f"Replaced toolbar HTML #{count}")

# ── 2. 替换 Markdown 格式工具栏 JS → execCommand 富文本工具栏 ──
old_format_js = '''  // ── 格式工具栏：在光标位置插入 Markdown 标记 ──
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
      else if (fmt === "list") { before = "\\n- "; after = ""; }
      else if (fmt === "link") { before = "["; after = `](${selected || "url"})`; }
      ta.setRangeText(before + selected + after, start, end, "select");
      ta.focus();
    });
  });'''

new_format_js = '''  document.querySelectorAll(".format-toolbar [data-format]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const fmt = btn.dataset.format;
      const editor = document.querySelector("#compose-body");
      editor.focus();
      if (fmt === "link") {
        const url = prompt("输入链接 URL:", "https://");
        if (url) document.execCommand("createLink", false, url);
      } else if (fmt === "fontSize") {
        // handled by change event
      } else {
        document.execCommand(fmt, false, null);
      }
    });
  });
  document.querySelectorAll(".format-toolbar select[data-format]").forEach((sel) => {
    sel.addEventListener("change", () => {
      const editor = document.querySelector("#compose-body");
      editor.focus();
      document.execCommand("fontSize", false, sel.value);
    });
  });'''

# The renderComposeWithTo version has a different comment
old_format_js2 = '''  // ── 格式工具栏 ──
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
      else if (fmt === "list") { before = "\\n- "; after = ""; }
      else if (fmt === "link") { before = "["; after = `](${selected || "url"})`; }
      ta.setRangeText(before + selected + after, start, end, "select");
      ta.focus();
    });
  });'''

if old_format_js in content:
    content = content.replace(old_format_js, new_format_js, 1)
    count += 1
    print("Replaced format JS (renderCompose)")

if old_format_js2 in content:
    content = content.replace(old_format_js2, new_format_js, 1)
    count += 1
    print("Replaced format JS (renderComposeWithTo)")

# ── 3. 更新 renderCompose() save-draft: body: form.get("body") → body: bodyHtml ──
# The first save-draft is in renderCompose
old_draft1 = '''  // ── 保存草稿 ──
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
  });'''

new_draft = '''  // ── 保存草稿 ──
  document.querySelector("#compose-draft").addEventListener("click", () => {
    const bodyHtml = document.querySelector("#compose-body").innerHTML;
    const form = new FormData(document.querySelector("#compose-form"));
    const draftData = {
      mailbox_id: form.get("mailbox_id"),
      recipients: form.get("recipients"),
      subject: form.get("subject"),
      body: bodyHtml,
      format: form.get("format"),
    };
    localStorage.setItem("wuyou.draft", JSON.stringify(draftData));
    toast(t("compose.draftSaved", "草稿已保存。"));
  });'''

while old_draft1 in content:
    content = content.replace(old_draft1, new_draft, 1)
    count += 1
    print(f"Replaced save-draft #{count}")

# ── 4. 更新 renderCompose() submit handler ──
old_submit1 = '''  // ── 发送（带 loading 状态） ──
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
        attachment_ids: composeAttachments.map((a) => a.id),
        in_reply_to: draft.in_reply_to || null,
      };'''

new_submit1 = '''  // ── 发送（带 loading 状态） ──
  document.querySelector("#compose-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sendBtn = document.querySelector("#compose-send");
    sendBtn.disabled = true;
    sendBtn.textContent = t("compose.sending", "发送中...");
    try {
      const bodyHtml = document.querySelector("#compose-body").innerHTML;
      const bodyText = document.querySelector("#compose-body").innerText;
      document.querySelector("#compose-body-hidden").value = bodyText;
      const payload = {
        mailbox_id: Number(form.get("mailbox_id")),
        recipients: String(form.get("recipients")).split(",").map((item) => item.trim()).filter(Boolean),
        subject: form.get("subject"),
        body: bodyText,
        format: "html",
        encryption_mode: form.get("encryption_mode"),
        attachment_ids: composeAttachments.map((a) => a.id),
        in_reply_to: draft.in_reply_to || null,
      };'''

if old_submit1 in content:
    content = content.replace(old_submit1, new_submit1)
    count += 1
    print("Replaced submit handler (renderCompose)")
else:
    print("WARNING: submit handler (renderCompose) NOT FOUND")

# ── 5. 更新 renderComposeWithTo() schedule handler ──
old_schedule = '''    const form = new FormData(document.querySelector("#compose-form"));
    const payload = {
      mailbox_id: Number(form.get("mailbox_id")),
      recipients: String(form.get("recipients")).split(",").map((item) => item.trim()).filter(Boolean),
      subject: form.get("subject"),
      body: form.get("body"),
      format: form.get("format"),
      encryption_mode: form.get("encryption_mode"),
      attachment_ids: composeAttachments.map((a) => a.id),
      in_reply_to: draft.in_reply_to || null,
      scheduled_at: isoTime,
    };'''

new_schedule = '''    const bodyHtml = document.querySelector("#compose-body").innerHTML;
    const bodyText = document.querySelector("#compose-body").innerText;
    document.querySelector("#compose-body-hidden").value = bodyText;
    const form = new FormData(document.querySelector("#compose-form"));
    const payload = {
      mailbox_id: Number(form.get("mailbox_id")),
      recipients: String(form.get("recipients")).split(",").map((item) => item.trim()).filter(Boolean),
      subject: form.get("subject"),
      body: bodyText,
      format: "html",
      encryption_mode: form.get("encryption_mode"),
      attachment_ids: composeAttachments.map((a) => a.id),
      in_reply_to: draft.in_reply_to || null,
      scheduled_at: isoTime,
    };'''

if old_schedule in content:
    content = content.replace(old_schedule, new_schedule)
    count += 1
    print("Replaced schedule handler (renderComposeWithTo)")
else:
    print("WARNING: schedule handler NOT FOUND")

# ── 6. 更新 renderComposeWithTo() submit handler ──
old_submit2 = '''  // ── 发送 ──
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
        format: form.get("format"),'''

new_submit2 = '''  // ── 发送 ──
  document.querySelector("#compose-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const sendBtn = document.querySelector("#compose-send");
    sendBtn.disabled = true;
    sendBtn.textContent = t("compose.sending", "发送中...");
    try {
      const bodyHtml = document.querySelector("#compose-body").innerHTML;
      const bodyText = document.querySelector("#compose-body").innerText;
      document.querySelector("#compose-body-hidden").value = bodyText;
      const payload = {
        mailbox_id: Number(form.get("mailbox_id")),
        recipients: String(form.get("recipients")).split(",").map((item) => item.trim()).filter(Boolean),
        subject: form.get("subject"),
        body: bodyText,
        format: "html",'''

if old_submit2 in content:
    content = content.replace(old_submit2, new_submit2)
    count += 1
    print("Replaced submit handler (renderComposeWithTo)")
else:
    print("WARNING: submit handler (renderComposeWithTo) NOT FOUND")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"\nDone. Total replacements: {count}")
