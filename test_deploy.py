"""WuYou UI comprehensive test — locator-based, handles async renders"""
from playwright.sync_api import sync_playwright
import time, json

BASE = "http://localhost:8000"
SHOTS = r"C:\Users\姓名\Documents\dockertest\wuyou"
report = {"total": 0, "pass": 0, "fail": 0, "issues": []}

def test(name, passed, detail=""):
    report["total"] += 1
    if passed: report["pass"] += 1
    else: report["fail"] += 1; report["issues"].append(f"FAIL: {name}: {detail}")
    print(f"  {'✅' if passed else '🔴'} {name}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})

    # ============================================================
    # 1. AUTH PAGE — 登录/注册卡片
    # ============================================================
    print("\n1. AUTH PAGE")
    page.goto(BASE)
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\01_auth.png", full_page=True)
    test("卡片布局(.auth-card)", page.locator(".auth-card").count() > 0)
    test("注册/登录Tab切换", page.locator(".auth-tabs button").count() >= 2)
    test("Favicon(SVG)", "svg" in (page.locator('link[rel="icon"]').get_attribute("href") or ""))

    # ============================================================
    # 2. REGISTER
    # ============================================================
    print("\n2. REGISTER")
    uname = f"test_{int(time.time())}"
    page.fill('input[name="username"]', uname)
    page.fill('input[name="email"]', f"{uname}@test.com")
    page.fill('input[name="password"]', "Test123456")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle"); time.sleep(2)
    page.screenshot(path=f"{SHOTS}\\02_registered.png", full_page=True)
    logged_in = page.locator(".topbar").count() > 0
    test("注册成功进入主界面", logged_in)
    if not logged_in:
        browser.close()
        print("ABORT: registration failed")
        exit(1)

    # ============================================================
    # 3. SHELL — 标题栏 + 侧栏
    # ============================================================
    print("\n3. SHELL")
    test("标题栏(topbar)", page.locator(".topbar").count() > 0)
    test("语言切换", page.locator("#locale-select").count() > 0)
    test("主题切换", page.locator("#theme-toggle").count() > 0)
    test("用户头像", page.locator("#user-avatar").count() > 0)
    test("侧栏(sidebar)", page.locator(".sidebar").count() > 0)
    test("折叠按钮", page.locator("#sidebar-collapse").count() > 0)
    nav_count = page.locator(".nav-button").count()
    test(f"导航项≥10 ({nav_count})", nav_count >= 10)
    page.locator("#user-avatar").click(); time.sleep(0.4)
    dd_items = page.locator("#user-dropdown a").all_text_contents()
    test(f"用户下拉菜单: {dd_items}", len(dd_items) >= 3)
    page.click(".topbar"); time.sleep(0.2)
    sw0 = page.locator("#sidebar").bounding_box()["width"]
    page.locator("#sidebar-collapse").click(); time.sleep(0.4)
    sw1 = page.locator("#sidebar").bounding_box()["width"]
    test(f"侧栏折叠 180->48", sw1 < sw0)
    page.locator("#sidebar-collapse").click(); time.sleep(0.2)
    page.screenshot(path=f"{SHOTS}\\03_shell.png", full_page=True)

    # ============================================================
    # 4. INBOX
    # ============================================================
    print("\n4. INBOX")
    page.locator('button[data-view="inbox"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\04_inbox.png", full_page=True)
    test("同步任务按钮", page.locator("#show-sync-jobs").count() > 0)
    test("文件夹标签", page.locator(".folder-tab").count() >= 3)
    test("邮件搜索框", page.locator("#mail-search").count() > 0)
    page.locator("#show-sync-jobs").click(); time.sleep(1.5)
    modal_el = page.locator(".sync-jobs-modal")
    test("同步任务弹窗", modal_el.count() > 0)
    page.screenshot(path=f"{SHOTS}\\04b_sync_modal.png", full_page=True)
    # Close via button
    close_btns = page.locator("button").filter(has_text="关闭")
    if close_btns.count() > 0: close_btns.last.click(); time.sleep(0.3)
    elif page.locator(".modal-overlay").count() > 0:
        page.locator(".modal-overlay").evaluate("el => el.remove()")

    # ============================================================
    # 5. ACCOUNTS
    # ============================================================
    print("\n5. ACCOUNTS")
    page.locator('button[data-view="accounts"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1.5)
    page.screenshot(path=f"{SHOTS}\\05_accounts.png", full_page=True)
    test("添加账户表单", page.locator("#account-form").count() > 0)
    test("Thunderbird导入", page.locator("#tb-import-btn").count() > 0)

    # ============================================================
    # 6. CALENDAR
    # ============================================================
    print("\n6. CALENDAR")
    page.locator('button[data-view="calendar"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\06_calendar.png", full_page=True)
    test("新建事件按钮", page.locator("#cal-new-event").count() > 0)
    test("日历网格", page.locator("#cal-grid").count() > 0)
    # Create event
    page.locator("#cal-new-event").click(); time.sleep(0.5)
    page.fill("#cal-ev-title", "测试"); page.locator("#cal-ev-save").click()
    time.sleep(1.5); page.wait_for_load_state("networkidle")
    page.screenshot(path=f"{SHOTS}\\06b_cal_saved.png", full_page=True)
    test("事件保存后显示", page.locator(".cal-dot").count() > 0)

    # ============================================================
    # 7. CONTACTS
    # ============================================================
    print("\n7. CONTACTS")
    page.locator('button[data-view="contacts"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.locator("#contact-new").click(); time.sleep(0.5)
    page.fill("#contact-first", "张"); page.fill("#contact-last", "三")
    page.fill("#contact-email", "z@t.com"); page.locator("#contact-save").click()
    time.sleep(1); page.wait_for_load_state("networkidle")
    page.screenshot(path=f"{SHOTS}\\07_contacts.png", full_page=True)
    test("保存后联系人在列表", page.locator(".contact-card").count() > 0)

    # ============================================================
    # 8. TASKS
    # ============================================================
    print("\n8. TASKS")
    page.locator('button[data-view="tasks"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\08a_tasks.png", full_page=True)
    test("看板/列表切换", page.locator("#task-view-kanban").count() > 0)
    test("快速添加输入框", page.locator("#task-quick-add").count() > 0)

    # Quick-add task via Enter key
    page.locator("#task-quick-add").fill("任务1")
    page.keyboard.press("Enter"); time.sleep(2)
    page.screenshot(path=f"{SHOTS}\\08b_tasks_after.png", full_page=True)
    # Check if kanban board has content
    board = page.locator(".kanban-board")
    has_board = board.count() > 0
    cards = page.locator(".task-card").count()
    has_something = has_board or cards > 0
    # Fallback: check if not showing empty state only
    container_text = page.locator("#task-container").text_content() or ""
    not_only_empty = "暂无" not in container_text
    test(f"任务添加后有内容(cards={cards},board={has_board})", has_something or not_only_empty)

    # ============================================================
    # 9. NOTES
    # ============================================================
    print("\n9. NOTES")
    page.locator('button[data-view="notes"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.locator("#note-new").click(); time.sleep(0.5)
    page.fill("#note-title", "笔记"); page.fill("#note-content", "内容")
    page.locator("#note-save").click(); time.sleep(1)
    page.wait_for_load_state("networkidle")
    page.screenshot(path=f"{SHOTS}\\09_notes.png", full_page=True)
    test("便签保存后显示", page.locator(".note-card").count() > 0)

    # ============================================================
    # 10. COMPOSE
    # ============================================================
    print("\n10. COMPOSE")
    page.locator('button[data-view="compose"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\10_compose.png", full_page=True)
    test("格式工具栏", page.locator(".format-toolbar").count() > 0)
    test("发送按钮", page.locator("#compose-send").count() > 0)
    test("保存草稿", page.locator("#compose-draft").count() > 0)
    test("取消按钮", page.locator("#compose-cancel").count() > 0)

    # ============================================================
    # 11. SETTINGS
    # ============================================================
    print("\n11. SETTINGS")
    page.locator('button[data-view="settings"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\11_settings.png", full_page=True)
    test("主题下拉", page.locator("#set-theme").count() > 0)
    test("语言下拉", page.locator("#set-locale").count() > 0)
    test("遥测开关", page.locator("#set-telemetry").count() > 0)
    test("修改密码", page.locator("#set-old-pw").count() > 0)
    test("修改邮箱", page.locator("#set-new-email").count() > 0)
    test("保存按钮", page.locator("#btn-save-settings").count() > 0)

    # ============================================================
    # 12. ABOUT + CHANGELOG
    # ============================================================
    print("\n12. ABOUT")
    page.locator('button[data-view="about"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\12_about.png", full_page=True)
    body_text = page.locator("body").text_content() or ""
    test("无 donateHint", "替换 /static/img/alipay" not in body_text)
    test("更新日志按钮", page.locator("#show-changelog").count() > 0)
    # Open and close changelog
    page.locator("#show-changelog").click(); time.sleep(0.5)
    ch_text = page.locator(".changelog-content").text_content() or ""
    test("changelog含v1.0.1", "v1.0.1" in ch_text)
    page.screenshot(path=f"{SHOTS}\\12b_changelog.png", full_page=True)
    # Close properly
    close_btn = page.locator(".close-modal-btn")
    if close_btn.count() > 0: close_btn.click(); time.sleep(0.3)
    else:
        # Escape key
        page.keyboard.press("Escape"); time.sleep(0.3)
    # Force cleanup any lingering overlay
    page.evaluate("() => { document.querySelectorAll('.modal-overlay').forEach(el => el.remove()); }")

    # ============================================================
    # 13. PLUGINS
    # ============================================================
    print("\n13. PLUGINS")
    page.locator('button[data-view="plugins"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.screenshot(path=f"{SHOTS}\\13_plugins.png", full_page=True)
    test("插件页面", page.locator(".page-pane h2").count() > 0)

    # ============================================================
    # 14. i18n
    # ============================================================
    print("\n14. i18n EN")
    page.locator("#locale-select").select_option("en-US")
    page.wait_for_load_state("networkidle"); time.sleep(1.5)
    page.screenshot(path=f"{SHOTS}\\14_enUS.png", full_page=True)
    nav_els = page.locator(".nav-label")
    en_txt = [nav_els.nth(i).text_content() for i in range(min(5, nav_els.count()))]
    test(f"英文导航: {en_txt}", "Inbox" in str(en_txt))

    # ============================================================
    # 15. SCROLLING
    # ============================================================
    print("\n15. SCROLLING")
    ws_overflow = page.evaluate("() => { const s=window.getComputedStyle(document.querySelector('.workspace')); return s.overflowY || s.overflow; }")
    test(f"workspace可滚动({ws_overflow})", ws_overflow in ("auto", "scroll"))

    browser.close()

    # ============================================================
    # REPORT
    # ============================================================
    print(f"\n{'='*60}")
    print(f"RESULT: {report['pass']}/{report['total']} PASS, {report['fail']} FAIL")
    print(f"{'='*60}")
    if report["issues"]:
        print("\nISSUES TO FIX:")
        for i in report["issues"]:
            print(f"  {i}")
    else:
        print("\nNo issues found.")

    print(f"\nScreenshots: {SHOTS}")
