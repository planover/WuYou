"""Diagnostic 2: check console errors, longer waits"""
from playwright.sync_api import sync_playwright
import time, json, urllib.request

BASE = "http://localhost:8000"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    
    # Capture console logs
    logs = []
    page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))
    page.on("pageerror", lambda err: logs.append(f"[PAGE_ERROR] {err}"))
    
    page.goto(BASE); page.wait_for_load_state("networkidle"); time.sleep(1)
    uname = f"d2_{int(time.time())}"
    page.fill('input[name="username"]', uname)
    page.fill('input[name="email"]', f"{uname}@t.com")
    page.fill('input[name="password"]', "Test123456")
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle"); time.sleep(2)
    
    # Create task via API
    token = page.evaluate("() => localStorage.getItem('wuyou.token')")
    req = urllib.request.Request(
        f"{BASE}/api/items",
        data=json.dumps({"kind":"task","title":"T1","meta_json":{"status":"todo","priority":5}}).encode(),
        headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"},
        method="POST"
    )
    urllib.request.urlopen(req)
    time.sleep(0.5)
    
    # Navigate to tasks and wait MUCH longer
    page.locator('button[data-view="tasks"]').click()
    page.wait_for_load_state("networkidle")
    time.sleep(5)  # wait 5 full seconds for async JS
    
    cards = page.locator(".task-card").count()
    kanban = page.locator(".kanban-board").count()
    text = page.locator("#task-container").text_content()
    
    print(f"cards={cards}, kanban={kanban}, text={text[:80]}")
    
    if logs:
        errors = [l for l in logs if 'error' in l.lower() or 'ERROR' in l]
        if errors:
            print(f"\nERROR LOGS ({len(errors)}):")
            for e in errors[:10]: print(f"  {e}")
        else:
            print(f"\nLogs ({len(logs)} total):")
            for l in logs[:10]: print(f"  {l}")
    
    browser.close()
