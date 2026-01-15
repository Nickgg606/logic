import asyncio
import threading
import time
from datetime import datetime
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from flask import Flask, jsonify
import os

app = Flask(__name__)

# ==================== 全局變數 ====================
global_data = {
    "race_data": {},
    "status": "未開始",
    "last_update": None
}
pages = {}
base_url = ""
start_race = 1
end_race = 9
race_data = {}

# ==================== Excel + 理論計算 + change_label ====================
# (完全 copy 你原有，無改動)
def load_five_odds_from_excel(excel_file):
    # ... (你原有完整 function)
    pass  # ← 貼返你原有 code

EXCEL_FILE = "HKJC_odds_tracker_live_20260114_1768294826.xlsx"
five_odds_from_excel = load_five_odds_from_excel(EXCEL_FILE)

def calculate_theory_odds(odds_dict):
    # ... (你原有完整 function)
    pass

def assign_groups(sorted_horses):
    # ... (你原有)
    pass

def change_label(change):
    # ... (你原有)
    pass

# ==================== Auto fill five odds (你原有，假設有) ====================
def auto_fill_five_odds(race_no, horse_names):
    # 如果你原有 code 有呢個 function，貼返；否則加 placeholder
    return five_odds_from_excel.get(race_no, {})

# ==================== Async main & monitor ====================
async def monitor_race(page, race_no):
    url = f"{base_url}/{race_no}"
    race_data[race_no] = {'current_odds': {}, 'last_update': None, 'five_theory': {}, 'current_theory': {}, 'horse_names': {}, 'five_odds': {}, 'status': '載入馬名中...'}

    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Race {race_no} - Starting loop")
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            print(f"Race {race_no} - Goto success")

            # 調整 selector：如果 'rc-odds-table-compact-{race_no}' 唔 work，試下面通用版
            # 優先用 id，fallback 到 table 或 div containing 'odds'
            try:
                table = await page.wait_for_selector(f"#rc-odds-table-compact-{race_no}", timeout=30000)
            except PlaywrightTimeoutError:
                print(f"Race {race_no} - ID selector timeout, trying fallback")
                table = await page.query_selector("table.compact, div[class*='odds'], table")  # 調整呢度根據 inspect
                if not table:
                    raise Exception("No odds table found")

            rows = await table.query_selector_all("tr")
            print(f"Race {race_no} - Found {len(rows)} rows")

            horse_names = {}
            current_odds = {}
            has_odds = False

            for row in rows[1:-1]:  # skip header/footer
                cols = await row.query_selector_all("td")
                if len(cols) >= 6:
                    horse_no_text = (await cols[0].inner_text()).strip()
                    if horse_no_text.isdigit():
                        horse_no = int(horse_no_text)
                        horse_name = (await cols[3].inner_text()).strip()  # 馬名 column
                        horse_names[horse_no] = horse_name

                        # Odds selector：div[class*='win'] a 或 text 直接
                        win_element = await cols[4].query_selector("div[class*='win'] a, a, span")
                        if win_element:
                            win_odds_str = (await win_element.inner_text()).strip()
                            print(f"Race {race_no} - Horse {horse_no} odds text: {win_odds_str}")
                            if win_odds_str.upper() not in ["SCR", "N/A", ""]:
                                try:
                                    current_odds[horse_no] = float(win_odds_str)
                                    has_odds = True
                                except ValueError:
                                    pass

            # Update data
            five_odds = auto_fill_five_odds(race_no, horse_names)
            race_data[race_no]['five_odds'] = five_odds
            race_data[race_no]['horse_names'] = horse_names
            race_data[race_no]['current_odds'] = current_odds
            race_data[race_no]['five_theory'] = calculate_theory_odds(five_odds)
            race_data[race_no]['current_theory'] = calculate_theory_odds(current_odds) if has_odds else {'A1': 'N/A', 'A2': 'N/A', 'A3': 'N/A'}
            race_data[race_no]['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            race_data[race_no]['status'] = '已偵測到賠率，正在更新...' if has_odds else '馬名載入完成，等待馬會出賠率...'

            global_data["race_data"][race_no] = race_data[race_no].copy()
            global_data["status"] = "更新中"
            global_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print(f"Race {race_no} - Update success | Horses: {len(horse_names)} | Odds: {len(current_odds)}")

        except Exception as e:
            print(f"Race {race_no} - Error: {str(e)}")
            race_data[race_no]['status'] = f'錯誤: {str(e)[:50]}...'

        await asyncio.sleep(5)  # 每5秒，避免過頻

async def main():
    global base_url
    date = datetime.now().strftime("%Y-%m-%d")
    venue = "HV" if "HV" in date else "ST"  # 可加邏輯 detect
    base_url = f"https://bet.hkjc.com/ch/racing/wpq/{date}/{venue}"
    print(f"Base URL: {base_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'])
        tasks = []
        for race_no in range(start_race, end_race + 1):
            context = await browser.new_context()
            page = await context.new_page()
            pages[race_no] = page
            tasks.append(asyncio.create_task(monitor_race(page, race_no)))
        await asyncio.gather(*tasks)

# Run async in thread
def run_scraper():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except Exception as e:
        print(f"Scraper loop error: {e}")

threading.Thread(target=run_scraper, daemon=True).start()

# ==================== Flask routes ====================
# 你的 home() 完整 HTML + JS (保持不變，確保顯示所有字眼 + 分組表格)
@app.route('/')
def home():
    return """你的完整 HTML code 在這裡"""  # ← 貼返你原檔中 @app.route('/') 的 return """ ... """

@app.route('/api/data')
def api_data():
    return jsonify(global_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    flask_thread = threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': port, 'debug': False}, daemon=True)
    flask_thread.start()
    # 唔使 asyncio.run(main())，已移去 thread
