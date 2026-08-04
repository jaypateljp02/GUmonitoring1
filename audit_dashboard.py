import asyncio, sys, os
sys.stdout.reconfigure(encoding='utf-8')

from playwright.async_api import async_playwright

BASE = "https://gu-monitoring.initiativesewafoundation.com/"
ARTIFACT_DIR = r"C:\Users\User\.gemini\antigravity-ide\brain\c6a02fa4-8064-48b6-9c94-84a01a72f3dd"

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        
        errors = []
        page.on("console", lambda msg: errors.append(f"CONSOLE {msg.type}: {msg.text}") if msg.type in ["error", "warning"] else None)
        page.on("pageerror", lambda err: errors.append(f"PAGE ERROR: {err}"))
        
        print("=== FULL DASHBOARD AUDIT ===")
        
        # 1. Load page
        await page.goto(BASE, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        # 2. Floor Plan tab (default)
        print("\n--- FLOOR PLAN TAB ---")
        await page.screenshot(path=os.path.join(ARTIFACT_DIR, "audit_floorplan.png"))
        body_text = await page.inner_text("body")
        undef_count = body_text.lower().count("undefined")
        print(f"  'undefined' occurrences in Floor Plan: {undef_count}")
        if undef_count > 0:
            # Find where undefined appears
            elements = await page.query_selector_all("*")
            for el in elements:
                text = await el.inner_text()
                if "undefined" in text.lower() and len(text) < 200:
                    tag = await el.evaluate("el => el.tagName")
                    print(f"  FOUND 'undefined' in <{tag}>: {text[:100]}")
        
        # 3. List View tab
        print("\n--- LIST VIEW TAB ---")
        await page.click("text=List View")
        await asyncio.sleep(3)
        await page.screenshot(path=os.path.join(ARTIFACT_DIR, "audit_listview.png"))
        body_text = await page.inner_text("body")
        undef_count = body_text.lower().count("undefined")
        print(f"  'undefined' occurrences in List View: {undef_count}")
        if undef_count > 0:
            cards = await page.query_selector_all(".room-card")
            for card in cards:
                text = await card.inner_text()
                if "undefined" in text.lower():
                    print(f"  FOUND in room card: {text[:150]}")
        
        # 4. Check room names specifically
        print("\n--- ROOM NAME CHECK ---")
        room_names = await page.query_selector_all(".room-name")
        for rn in room_names:
            text = await rn.inner_text()
            if "undefined" in text.lower() or not text.strip():
                print(f"  BAD room name: '{text}'")
        if not room_names:
            print("  No .room-name elements found")
        
        # 5. Plugs tab
        print("\n--- PLUGS TAB ---")
        await page.click("text=Plugs")
        await asyncio.sleep(3)
        await page.screenshot(path=os.path.join(ARTIFACT_DIR, "audit_plugs.png"))
        body_text = await page.inner_text("body")
        undef_count = body_text.lower().count("undefined")
        print(f"  'undefined' occurrences in Plugs: {undef_count}")
        
        # 6. Click on first room in List View to open detail
        print("\n--- DETAIL VIEW CHECK ---")
        await page.click("text=List View")
        await asyncio.sleep(2)
        cards = await page.query_selector_all(".room-card")
        if cards:
            await cards[0].click()
            await asyncio.sleep(3)
            await page.screenshot(path=os.path.join(ARTIFACT_DIR, "audit_detail.png"))
            body_text = await page.inner_text("body")
            undef_count = body_text.lower().count("undefined")
            print(f"  'undefined' occurrences in Detail View: {undef_count}")
        else:
            print("  No room cards found to click")
        
        # 7. Console errors summary
        print("\n--- CONSOLE ERRORS ---")
        if errors:
            for e in errors[:20]:
                print(f"  {e}")
        else:
            print("  No console errors detected!")
        
        print("\n=== AUDIT COMPLETE ===")
        await browser.close()

asyncio.run(run())
