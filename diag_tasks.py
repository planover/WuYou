"""Diagnostic: test task creation end-to-end"""
from playwright.sync_api import sync_playwright
import time

BASE = "http://localhost:8000"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    
    # Login
    page.goto(BASE); page.wait_for_load_state("networkidle"); time.sleep(1)
    uname = f"diag_{int(time.time())}"
    page.fill('input[name="username"]', uname)
    page.fill('input[name="email"]', f"{uname}@t.com")
    page.fill('input[name="password"]', "Test123456")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle"); time.sleep(2)
    
    # Go to tasks
    page.locator('button[data-view="tasks"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    
    # Try direct JS call instead of keyboard
    token = page.evaluate("() => localStorage.getItem('wuyou.token')")
    print(f"Token: {'OK' if token else 'MISSING'}")
    
    import urllib.request, json
    # Create task via API
    req = urllib.request.Request(
        f"{BASE}/api/items",
        data=json.dumps({"kind":"task","title":"API创建的任务","meta_json":{"status":"todo","priority":5}}).encode(),
        headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"},
        method="POST"
    )
    resp = urllib.request.urlopen(req)
    print(f"API create: {resp.status} {resp.read().decode()[:100]}")
    time.sleep(1)
    
    # Now also try keyboard method
    inp = page.locator("#task-quick-add")
    inp.click()
    inp.fill("键盘添加")
    # Try dispatch Enter event
    page.evaluate("""() => {
        const input = document.querySelector('#task-quick-add');
        input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',code:'Enter',keyCode:13,bubbles:true}));
    }""")
    time.sleep(2)
    
    # Navigate away and back to trigger fresh render
    page.locator('button[data-view="contacts"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(1)
    page.locator('button[data-view="tasks"]').click()
    page.wait_for_load_state("networkidle"); time.sleep(2)
    
    # Check DOM
    cards = page.locator(".task-card").count()
    kanban = page.locator(".kanban-board").count()
    container_html = page.locator("#task-container").inner_html()[:300]
    print(f"After nav: cards={cards}, kanban={kanban}")
    print(f"Container HTML: {container_html}")
    
    # Check if empty state is shown
    empty_text = page.locator("#task-container").text_content()
    print(f"Container text: {repr(empty_text)}")
    
    browser.close()
