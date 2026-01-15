import asyncio
import threading
import time
from datetime import datetime
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from flask import Flask, jsonify
import os
import gc

app = Flask(__name__)

# 全局變數
global_data = {
    "race_data": {},
    "status": "未開始",
    "last_update": None
}

base_url = ""
start_race = 1
end_race = 9
race_data = {}

# ==================== Excel 讀取 ====================
def load_five_odds_from_excel(excel_file):
    print(f"讀取 Excel: {excel_file}")
    try:
        df = pd.read_excel(excel_file, sheet_name=0, header=None)
        df.columns = range(df.shape[1])
        five_odds_all = {}
        for race_no in df[1].unique():
            if pd.isna(race_no) or not isinstance(race_no, (int, float)):
                continue
            race_df = df[df[1] == race_no]
            odds_dict = {}
            for _, row in race_df.iterrows():
                horse_no = row[2]
                five = row[8]
                if pd.notna(horse_no) and pd.notna(five):
                    try:
                        odds_dict[int(horse_no)] = float(five)
                    except:
                        print(f"格式錯: 場 {race_no} 馬 {horse_no}")
            if odds_dict:
                five_odds_all[int(race_no)] = odds_dict
                print(f"場 {race_no} 讀取 {len(odds_dict)} 匹馬")
        return five_odds_all
    except Exception as e:
        print(f"Excel 錯誤: {e}")
        return {}

EXCEL_FILE = "HKJC_odds_tracker_live_20260114_1768294826.xlsx"
five_odds_from_excel = load_five_odds_from_excel(EXCEL_FILE)

# ==================== 理論賠率計算 ====================
def calculate_theory_odds(odds_dict):
    if not odds_dict:
        return {'A1': 'N/A', 'A2': 'N/A', 'A3': 'N/A'}
    sorted_horses = sorted(odds_dict.items(), key=lambda x: x[1])
    a1 = sorted_horses[:2] if len(sorted_horses) >= 2 else sorted_horses
    a2 = sorted_horses[2:5] if len(sorted_horses) >= 5 else sorted_horses[2:]
    a3 = sorted_horses[5:] if len(sorted_horses) >= 5 else []
    
    def theory(group):
        total_prob = sum(1 / odds for _, odds in group if odds > 0)
        return round(1 / total_prob, 2) if total_prob > 0 else 'N/A'
    
    return {'A1': theory(a1), 'A2': theory(a2), 'A3': theory(a3)}

# ==================== 單 page scraper ====================
async def main():
    global base_url
    date = datetime.now().strftime("%Y-%m-%d")
    venue = "ST"  # 改 "HV" 如果夜賽
    base_url = f"https://bet.hkjc.com/ch/racing/wpq/{date}/{venue}"
    print(f"Scraper started - Base URL: {base_url}")

    async with async_playwright() as p:
        print("啟動 chromium 瀏覽器...")
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'])
        print("chromium 啟動成功")
        page = await browser.new_page()  # 只開 1 page

        while True:
            print("開始掃描所有場次...")
            for race_no in range(start_race, end_race + 1):
                url = f"{base_url}/{race_no}"
                print(f"Scraping race {race_no}: {url}")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=60000)
                    print(f"Goto success for race {race_no}")

                    # Selector 調整：用通用版，避免 id 錯
                    table = await page.query_selector("table, div[class*='odds'], .compact, [id*='odds-table']")
                    if not table:
                        print(f"Race {race_no} - No table found, skip")
                        continue

                    rows = await table.query_selector_all("tr")
                    print(f"Race {race_no} - Found {len(rows)} rows")

                    horse_names = {}
                    current_odds = {}
                    has_odds = False

                    for row in rows[1:-1]:
                        cols = await row.query_selector_all("td")
                        if len(cols) >= 6:
                            horse_no_text = (await cols[0].inner_text()).strip()
                            if horse_no_text.isdigit():
                                horse_no = int(horse_no_text)
                                horse_name = (await cols[3].inner_text()).strip()
                                horse_names[horse_no] = horse_name

                                win_element = await cols[4].query_selector("div[class*='win'] a, a, span")
                                if win_element:
                                    win_odds_str = (await win_element.inner_text()).strip()
                                    print(f"Race {race_no} - Horse {horse_no} odds: {win_odds_str}")
                                    if win_odds_str.upper() not in ["SCR", "N/A", ""]:
                                        try:
                                            current_odds[horse_no] = float(win_odds_str)
                                            has_odds = True
                                        except:
                                            pass

                    five_odds = five_odds_from_excel.get(race_no, {})
                    race_data[race_no] = {
                        'horse_names': horse_names,
                        'current_odds': current_odds,
                        'five_odds': five_odds,
                        'five_theory': calculate_theory_odds(five_odds),
                        'current_theory': calculate_theory_odds(current_odds) if has_odds else {'A1':'N/A','A2':'N/A','A3':'N/A'},
                        'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'status': '已偵測到賠率' if has_odds else '馬名載入完成，無即時賠率'
                    }

                    global_data["race_data"][race_no] = race_data[race_no].copy()
                    global_data["status"] = "更新中"
                    global_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    print(f"Race {race_no} scraped OK | Horses: {len(horse_names)} | Odds: {len(current_odds)}")

                except Exception as e:
                    print(f"Race {race_no} error: {str(e)}")

            gc.collect()
            print("掃描完成，等待 30 秒...")
            await asyncio.sleep(30)

# ==================== Flask 路由 ====================
@app.route('/')
def home():
    return """
    <!doctype html>
    <html>
    <head>
        <title>獨贏賠率監控</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; background: #f8f9fa; }
            h1 { color: #333; }
            .status { font-weight: bold; color: green; }
            .error { color: red; }
            table { border-collapse: collapse; width: 100%; margin-bottom: 15px; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
            .group { margin-bottom: 20px; }
            .group-title { background-color: #e0e0e0; padding: 8px; font-weight: bold; }
            .group-theory { background-color: #f0f0f0; padding: 6px; font-size: 0.95em; }
            .rise { color: green; font-weight: bold; }
            .fall { color: red; font-weight: bold; }
            .same { color: gray; }
            .intro { background-color: #fffbe6; padding: 15px; border-left: 5px solid #ffd700; margin-bottom: 30px; font-size: 0.95em; line-height: 1.6; }
        </style>
    </head>
    <body>
        <h1>獨贏賠率監控</h1>
        <p>狀態: <span id="status" class="status">載入中...</span></p>
        <p>最後更新: <span id="last_update">載入中...</span></p>
        <div class="intro">
            <strong>隔夜賠率懶人包📋</strong><br><br>
            1. 隔夜賠率是什麼<br>
            - 根據馬會以往開出的組合獨贏計算方式進行 A1, A2, A3 分類<br><br>
            2. A1, A2, A3 的排序用途<br>
            - A1 > A2&A3，若A1 組合持續落飛，對搵 "熱膽" 有幫助<br>
            - 隔夜組合若 A1 < A2 / A3，熱門不穩<br>
            - A2 的持續落飛對 A1 勝率有明顯影響<br>
            - 暫時觀察到有效預測在對比 5 p.m. 與 開跑前，但Logic algo 採集了幾個時間點照分享比大家<br><br>
            留意以下重點<br>
            - Backtest 是根據歷史 最終賠率<br>
            - 最終的賠率是一個 未知變量
        </div>
        <div id="loading">正在載入數據...</div>
        <div id="content"></div>
        <script>
            function updatePage() {
                fetch('/api/data')
                    .then(response => response.json())
                    .then(data => {
                        document.getElementById('status').innerText = data.status || '未知';
                        document.getElementById('last_update').innerText = data.last_update || '未知';
                        document.getElementById('loading').style.display = 'none';
                        let content = '';
                        if (data.race_data && Object.keys(data.race_data).length > 0) {
                            let sortedRaces = Object.keys(data.race_data).sort((a, b) => Number(a) - Number(b));
                            sortedRaces.forEach(race_no => {
                                let race = data.race_data[race_no];
                                content += `<h2>第 ${race_no} 場</h2>`;
                                content += `<p>狀態: ${race.status || '未知'}</p>`;
                                if (race.last_update) {
                                    content += `<p>最後更新: ${race.last_update}</p>`;
                                }
                                if (race.current_odds) {
                                    content += `<h3>即時賠率</h3>`;
                                    content += `<p>A1: ${race.current_theory?.A1 || 'N/A'} | A2: ${race.current_theory?.A2 || 'N/A'} | A3: ${race.current_theory?.A3 || 'N/A'}</p>`;
                                }
                                if (race.horse_names) {
                                    content += `<h3>馬名列表（A1/A2/A3 分組）</h3>`;
                                    let sorted_five = Object.entries(race.five_odds || {}).sort((a, b) => a[1] - b[1]);
                                    let groups = {
                                        'A1': sorted_five.slice(0, 2),
                                        'A2': sorted_five.slice(2, 5),
                                        'A3': sorted_five.slice(5)
                                    };
                                    for (let group_name in groups) {
                                        let group = groups[group_name];
                                        if (group.length > 0) {
                                            content += `<div class="group">`;
                                            content += `<div class="group-title">${group_name}</div>`;
                                            content += `<div class="group-theory">5點理論: ${race.five_theory?.[group_name] || 'N/A'}　|　現時理論: ${race.current_theory?.[group_name] || 'N/A'}</div>`;
                                            content += `<table><tr><th>馬號</th><th>馬名</th><th>5點賠率</th><th>現時賠率</th><th>升降值</th></tr>`;
                                            group.forEach(([horse_no, five]) => {
                                                let curr = race.current_odds?.[horse_no] || 'N/A';
                                                let change = (typeof five === 'number' && typeof curr === 'number') ? (five - curr) : 0;
                                                let changeClass = change > 0 ? 'rise' : change < 0 ? 'fall' : 'same';
                                                let changeText = change > 0 ? `+${change.toFixed(1)} (落飛)` : change < 0 ? `${change.toFixed(1)} (回飛)` : '0 (不變)';
                                                content += `<tr><td>${horse_no}</td><td>${race.horse_names[horse_no] || 'N/A'}</td><td>${five}</td><td>${curr}</td><td class="${changeClass}">${changeText}</td></tr>`;
                                            });
                                            content += `</table></div>`;
                                        }
                                    }
                                }
                                content += `<hr>`;
                            });
                        } else {
                            content = '<p>暫無數據，請等待監控更新... (今日可能無賽事)</p>';
                        }
                        document.getElementById('content').innerHTML = content;
                    })
                    .catch(error => {
                        document.getElementById('loading').innerText = '載入失敗，請檢查後端';
                        console.error('更新錯誤:', error);
                    });
            }
            setTimeout(updatePage, 5000);
            setInterval(updatePage, 5000);  // 改 5秒一次，減負荷
        </script>
    </body>
    </html>
    """

@app.route('/api/data')
def api_data():
    return jsonify(global_data)

# ==================== 啟動 ====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    
    def run_flask():
        app.run(host='0.0.0.0', port=port, debug=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    def run_scraper():
        print("Starting scraper thread...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(main())
        except Exception as e:
            print("Scraper error:", e)
    
    scraper_thread = threading.Thread(target=run_scraper, daemon=True)
    scraper_thread.start()
    
    # 保持主 thread 活著
    while True:
        time.sleep(3600)
