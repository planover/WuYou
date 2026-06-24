"""WuYou UI 自动化测试 — 验证全部 14 项反馈修复状态"""
from playwright.sync_api import sync_playwright
import json, time, os

BASE = "http://localhost:8000"
RESULTS = []
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "dockertest", "wuyou")

def check(name, passed, detail=""):
    mark = "✅" if passed else "❌"
    RESULTS.append(f"{mark} {name}: {detail}" if detail else f"{mark} {name}")
    print(f"{mark} {name}")

def screenshot(page, name):
    path = f"C:\\Users\\姓名\\Documents\\dockertest\\wuyou\\screenshot_{name}.png"
    page.screenshot(path=path, full_page=True)
    print(f"  📸 {name} -> {path}")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    
    # ============================================================
    # 1. 访问首页 — 应看到登录/注册卡片
    # ============================================================
    page.goto(BASE)
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "01_auth_register")
    
    # 检查 favicon
    favicon = page.locator('link[rel="icon"]').get_attribute("href")
    check("WEB-01 Favicon", "data:image/svg" in (favicon or ""), favicon[:60] if favicon else "MISSING")
    
    # 检查卡片式布局
    card = page.locator(".auth-card")
    check("WEB-02 登录页卡片布局", card.count() > 0)
    
    # 检查 slogan
    slogan_text = page.locator(".auth-card .slogan").text_content()
    check("WEB-03 登录页 slogan", "坞" in slogan_text, slogan_text)
    
    # 检查注册/登录 tab 切换
    tabs = page.locator(".auth-tabs button")
    tab_count = tabs.count()
    check("WEB-04 注册/登录 tab 存在", tab_count >= 2, f"{tab_count} tabs")
    
    # 切换到登录 tab
    login_tab = page.locator(".auth-tabs button").nth(1)
    login_tab.click()
    time.sleep(0.5)
    screenshot(page, "01b_auth_login")
    
    # ============================================================
    # 2. 注册新用户
    # ============================================================
    # 切回注册 tab
    page.locator(".auth-tabs button").nth(0).click()
    time.sleep(0.3)
    
    username = f"testuser_{int(time.time())}"
    page.fill('input[name="username"]', username)
    page.fill('input[name="email"]', f"{username}@test.com")
    page.fill('input[name="password"]', "Test123456")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    time.sleep(2)
    
    # 检查是否注册成功进入主界面
    has_topbar = page.locator(".topbar").count() > 0
    check("WEB-05 注册成功进入主界面", has_topbar)
    
    if not has_topbar:
        screenshot(page, "ERROR_after_register")
        browser.close()
        print("\n=== 测试结果 ===")
        for r in RESULTS:
            print(r)
        exit()
    
    screenshot(page, "02_inbox_default")
    
    # ============================================================
    # 3. 标题栏交互
    # ============================================================
    brand = page.locator(".topbar .brand")
    brand_clickable = brand.get_attribute("onclick") or brand.get_attribute("style")
    check("WEB-06 标题栏品牌可点击", brand_clickable is not None)
    
    # 用户头像
    avatar = page.locator("#user-avatar")
    has_avatar = avatar.count() > 0
    check("WEB-07 用户头像存在", has_avatar)
    
    # 点击头像展开下拉菜单
    if has_avatar:
        avatar.click()
        time.sleep(0.5)
        dropdown = page.locator("#user-dropdown")
        dropdown_visible = dropdown.is_visible()
        check("WEB-08 用户下拉菜单可展开", dropdown_visible)
        
        menu_items = dropdown.locator("a")
        menu_texts = [menu_items.nth(i).text_content() for i in range(menu_items.count())]
        has_settings = any("设置" in t for t in menu_texts)
        has_about = any("关于" in t for t in menu_texts)
        has_logout = any("退出" in t or "Sign out" in t for t in menu_texts)
        check("WEB-09 下拉菜单含设置/关于/退出", has_settings and has_about and has_logout,
              str(menu_texts))
        
        # 点击空白关闭
        page.click(".topbar")
        time.sleep(0.3)
    
    # 语言切换
    locale_select = page.locator("#locale-select")
    has_locale = locale_select.count() > 0
    check("WEB-10 标题栏语言切换", has_locale)
    
    # 主题切换
    theme_btn = page.locator("#theme-toggle")
    has_theme = theme_btn.count() > 0
    check("WEB-11 标题栏主题切换", has_theme)
    
    screenshot(page, "03_topbar")
    
    # ============================================================
    # 4. 侧栏
    # ============================================================
    sidebar = page.locator(".sidebar")
    check("WEB-12 侧栏存在", sidebar.count() > 0)
    
    # 折叠按钮
    collapse_btn = page.locator("#sidebar-collapse")
    check("WEB-13 侧栏折叠按钮", collapse_btn.count() > 0)
    
    # 测试折叠
    original_width = sidebar.bounding_box()["width"]
    collapse_btn.click()
    time.sleep(0.5)
    collapsed_width = sidebar.bounding_box()["width"]
    collapsed = collapsed_width < original_width
    check("WEB-14 侧栏可折叠", collapsed, f"{original_width}px -> {collapsed_width}px")
    
    # 展开侧栏
    collapse_btn.click()
    time.sleep(0.5)
    
    # 导航项
    nav_buttons = page.locator(".nav-button")
    nav_count = nav_buttons.count()
    nav_texts = [nav_buttons.nth(i).locator(".nav-label").text_content() for i in range(nav_count)]
    check("WEB-15 侧栏导航项 ≥ 10 个", nav_count >= 10, f"{nav_count} items: {nav_texts}")
    
    screenshot(page, "04_sidebar")
    
    # ============================================================
    # 5. 收件箱 — 同步任务按钮
    # ============================================================
    sync_jobs_btn = page.locator("#show-sync-jobs")
    has_sync_jobs_btn = sync_jobs_btn.count() > 0
    check("WEB-16 收件箱同步任务按钮", has_sync_jobs_btn)
    
    # 点击同步任务按钮
    if has_sync_jobs_btn:
        sync_jobs_btn.click()
        time.sleep(1)
        modal = page.locator(".sync-jobs-modal")
        check("WEB-17 同步任务 modal", modal.count() > 0)
        screenshot(page, "05_sync_jobs_modal")
        # 关闭 modal
        page.locator("#sync-jobs-close").click()
        time.sleep(0.3)
    
    # 文件夹标签
    folder_tabs = page.locator(".folder-tab")
    check("WEB-18 文件夹标签栏", folder_tabs.count() >= 4, f"{folder_tabs.count()} tabs")
    
    screenshot(page, "05_inbox_with_folders")
    
    # ============================================================
    # 6. 邮箱账户页
    # ============================================================
    page.click('button[data-view="accounts"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)  # 等待异步加载账户状态
    
    screenshot(page, "06_accounts")
    
    # 检查页面标题
    accounts_title = page.locator(".page-header h2")
    check("WEB-19 邮箱账户页面标题", accounts_title.count() > 0, 
          accounts_title.text_content() if accounts_title.count() > 0 else "N/A")
    
    # 检查添加账户表单
    account_form = page.locator("#account-form")
    check("WEB-20 添加账户表单", account_form.count() > 0)
    
    # 检查 Thunderbird 导入
    tb_import = page.locator("#tb-import-btn")
    check("WEB-21 Thunderbird 导入按钮", tb_import.count() > 0)
    
    # ============================================================
    # 7. 日历页
    # ============================================================
    page.click('button[data-view="calendar"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "07_calendar")
    
    cal_toolbar = page.locator("#cal-new-event")
    check("WEB-22 日历新建事件按钮", cal_toolbar.count() > 0)
    
    cal_grid = page.locator("#cal-grid")
    check("WEB-23 日历网格渲染", cal_grid.count() > 0)
    
    # 测试新建事件 modal
    cal_toolbar.click()
    time.sleep(0.5)
    event_modal = page.locator(".cal-modal")
    check("WEB-24 日历事件 modal", event_modal.count() > 0)
    
    # 填写标题并保存
    page.fill("#cal-ev-title", "测试事件")
    page.click("#cal-ev-save")
    time.sleep(1)
    screenshot(page, "07b_calendar_after_save")
    
    # 检查事件是否出现在日历上
    cal_dots = page.locator(".cal-dot")
    check("WEB-25 日历保存后事件显示", cal_dots.count() > 0, f"{cal_dots.count()} dots")
    
    # ============================================================
    # 8. 通讯录
    # ============================================================
    page.click('button[data-view="contacts"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "08_contacts")
    
    contact_new = page.locator("#contact-new")
    check("WEB-26 通讯录新建按钮", contact_new.count() > 0)
    
    # 新建联系人
    contact_new.click()
    time.sleep(0.5)
    page.fill("#contact-first", "张三")
    page.fill("#contact-last", "三")
    page.fill("#contact-email", "zhangsan@test.com")
    page.click("#contact-save")
    time.sleep(1)
    
    contact_cards = page.locator(".contact-card")
    check("WEB-27 通讯录保存后显示", contact_cards.count() > 0, f"{contact_cards.count()} contacts")
    screenshot(page, "08b_contacts_after_save")
    
    # ============================================================
    # 9. 任务
    # ============================================================
    page.click('button[data-view="tasks"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "09_tasks")
    
    # 看板/列表切换
    kanban_btn = page.locator("#task-view-kanban")
    list_btn = page.locator("#task-view-list")
    check("WEB-28 任务看板切换按钮", kanban_btn.count() > 0 and list_btn.count() > 0)
    
    # 快速添加任务
    quick_add = page.locator("#task-quick-add")
    check("WEB-29 任务快速添加输入框", quick_add.count() > 0)
    
    # 通过 API 直接创建任务（headless 键盘事件不完美）
    token = page.evaluate("() => localStorage.getItem('wuyou.token')")
    import urllib.request, json as _json
    task_req = urllib.request.Request(
        "http://localhost:8000/api/items",
        data=_json.dumps({"kind": "task", "title": "Headless Test Task", "meta_json": {"status": "todo", "priority": 5}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST"
    )
    urllib.request.urlopen(task_req)
    time.sleep(1)
    
    page.click('button[data-view="tasks"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    
    task_cards = page.locator(".task-card")
    check("WEB-30 任务保存后显示", task_cards.count() > 0, f"{task_cards.count()} tasks")
    screenshot(page, "09b_tasks_after_save")
    
    # 状态切换
    next_btn = page.locator("[data-next-status]")
    check("WEB-31 任务状态切换按钮", next_btn.count() > 0)
    
    # 删除按钮
    delete_btn = page.locator("[data-delete-task]")
    check("WEB-32 任务删除按钮", delete_btn.count() > 0)
    
    # ============================================================
    # 10. 便签
    # ============================================================
    page.click('button[data-view="notes"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "10_notes")
    
    note_new = page.locator("#note-new")
    check("WEB-33 便签新建按钮", note_new.count() > 0)
    
    note_new.click()
    time.sleep(0.5)
    page.fill("#note-title", "测试便签")
    page.fill("#note-content", "这是一条测试便签内容")
    page.click("#note-save")
    time.sleep(1)
    
    note_cards = page.locator(".note-card")
    check("WEB-34 便签保存后显示", note_cards.count() > 0, f"{note_cards.count()} notes")
    screenshot(page, "10b_notes_after_save")
    
    # ============================================================
    # 11. 写邮件
    # ============================================================
    page.click('button[data-view="compose"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "11_compose")
    
    # 格式工具栏
    format_toolbar = page.locator(".format-toolbar [data-format]")
    check("WEB-35 写邮件格式工具栏", format_toolbar.count() >= 3, f"{format_toolbar.count()} buttons")
    
    # 发送按钮
    send_btn = page.locator("#compose-send")
    check("WEB-36 发送按钮", send_btn.count() > 0)
    
    # 保存草稿
    draft_btn = page.locator("#compose-draft")
    check("WEB-37 保存草稿按钮", draft_btn.count() > 0)
    
    # 取消按钮
    cancel_btn = page.locator("#compose-cancel")
    check("WEB-38 取消按钮", cancel_btn.count() > 0)
    
    # ============================================================
    # 12. 设置页
    # ============================================================
    page.click('button[data-view="settings"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "12_settings")
    
    # 主题下拉
    theme_select = page.locator("#set-theme")
    check("WEB-39 设置主题下拉", theme_select.count() > 0)
    
    # 语言下拉
    locale_select2 = page.locator("#set-locale")
    check("WEB-40 设置语言下拉", locale_select2.count() > 0)
    
    # 遥测开关
    telemetry_cb = page.locator("#set-telemetry")
    check("WEB-41 设置遥测开关", telemetry_cb.count() > 0)
    
    # 远程同步地址
    remote_url = page.locator("#set-remote-url")
    check("WEB-42 设置远程同步地址输入", remote_url.count() > 0)
    
    # 修改密码
    old_pw = page.locator("#set-old-pw")
    new_pw = page.locator("#set-new-pw")
    change_pw_btn = page.locator("#btn-change-pw")
    check("WEB-43 设置修改密码表单", old_pw.count() > 0 and new_pw.count() > 0 and change_pw_btn.count() > 0)
    
    # 修改邮箱
    email_input = page.locator("#set-new-email")
    send_code = page.locator("#btn-send-email-code")
    check("WEB-44 设置修改邮箱表单", email_input.count() > 0 and send_code.count() > 0)
    
    # 保存全部
    save_all = page.locator("#btn-save-settings")
    check("WEB-45 设置保存全部按钮", save_all.count() > 0)
    
    # ============================================================
    # 13. 关于页
    # ============================================================
    page.click('button[data-view="about"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "13_about")
    
    # 检查无 donateHint 文本
    page_text = page.locator("body").text_content()
    has_donate_hint = "替换 /static/img/alipay-qr.png" in page_text or "Replace /static/img/alipay-qr.png" in page_text
    check("WEB-46 关于页无 donateHint 文案", not has_donate_hint)
    
    # changelog 按钮
    changelog_btn = page.locator("#show-changelog")
    check("WEB-47 关于页更新日志按钮", changelog_btn.count() > 0)
    
    # 点击 changelog 弹窗
    if changelog_btn.count() > 0:
        changelog_btn.click()
        time.sleep(0.5)
        changelog_modal = page.locator(".changelog-content")
        changelog_text = changelog_modal.text_content() if changelog_modal.count() > 0 else ""
        check("WEB-48 changelog modal 弹窗", changelog_modal.count() > 0)
        check("WEB-49 changelog 含 v1.0.1", "v1.0.1" in changelog_text)
        check("WEB-50 changelog 无外部链接跳转", "http" not in (changelog_modal.get_attribute("innerHTML") or "").split("href=")[-1][:20] if "href=" in (changelog_modal.get_attribute("innerHTML") or "") else True)
        screenshot(page, "13b_changelog_modal")
        # 关闭
        page.click('button:has-text("关闭")')
        time.sleep(0.3)
    
    # ============================================================
    # 14. 插件社区
    # ============================================================
    page.click('button[data-view="plugins"]')
    page.wait_for_load_state("networkidle")
    time.sleep(1.5)
    screenshot(page, "14_plugins")
    
    plugins_title = page.locator(".page-header h2")
    check("WEB-51 插件社区页面", plugins_title.count() > 0)
    
    # ============================================================
    # 15. i18n 检查 — 切换语言
    # ============================================================
    # 切换英文
    page.select_option("#locale-select", "en-US")
    page.wait_for_load_state("networkidle")
    time.sleep(1)
    screenshot(page, "15_enUS_inbox")
    
    nav_labels_en = page.locator(".nav-label")
    en_texts = [nav_labels_en.nth(i).text_content() for i in range(min(5, nav_labels_en.count()))]
    check("WEB-52 英文界面 nav 标签", "Inbox" in str(en_texts), str(en_texts))
    
    # 切换回中文
    page.select_option("#locale-select", "zh-CN")
    page.wait_for_load_state("networkidle")
    time.sleep(0.5)
    
    # ============================================================
    # 总结
    # ============================================================
    browser.close()
    
    passed = sum(1 for r in RESULTS if r.startswith("✅"))
    failed = sum(1 for r in RESULTS if r.startswith("❌"))
    total = len(RESULTS)
    
    print("\n" + "=" * 60)
    print(f"测试结果: {passed}/{total} 通过, {failed} 失败")
    print("=" * 60)
    for r in RESULTS:
        print(r)
    
    if failed > 0:
        print(f"\n❌ {failed} 项未通过！")
    else:
        print(f"\n✅ 全部 {total} 项通过！")
