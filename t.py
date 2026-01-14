import asyncio
import threading
import time
from datetime import datetime
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from flask import Flask, render_template_string, jsonify
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

# ==================== 從 Excel 自動讀取 5點賠率 ====================
def load_five_odds_from_excel(excel_file):
    print(f"正在從 Excel 檔讀取 5點賠率: {excel_file}")
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
                        print(f"警告：場次 {race_no} 馬號 {horse_no} 格式錯誤，已忽略")
            if odds_dict:
                five_odds_all[int(race_no)] = odds_dict
                print(f"場次 {race_no} 讀取完成：{len(odds_dict)} 匹馬")
        return five_odds_all
    except Exception as e:
        print(f"讀取 Excel 失敗: {e}")
        return {}

EXCEL_FILE = "HKJC_odds_tracker_live_20260114_1768294826.xlsx"
five_odds_from_excel = load_five_odds_from_excel(EXCEL_FILE)

# ==================== 計算理論賠率 ====================
def calculate_theory_odds(odds_dict):
    if not odds_dict:
        return {'A1': 0, 'A2': 0, 'A3': 0}
    sorted_horses = sorted(odds_dict.items(), key=lambda x: x[1])
    a1 = sorted_horses[0:2] if len(sorted_horses) >= 2 else sorted_horses[:len(sorted_horses)]
    a2 = sorted_horses[2:5] if len(sorted_horses) >= 5 else sorted_horses[2:len(sorted_horses)]
    a3 = sorted_horses[5:] if len(sorted_horses) >= 5 else []
    
    def sort_by_horse_no(group):
        return sorted(group, key=lambda x: x[0])
    
    a1 = sort_by_horse_no(a1)
    a2 = sort_by_horse_no(a2)
    a3 = sort_by_horse_no(a3)
    
    def theory(group):
        total_prob = sum(1 / odds for _, odds in group if odds > 0)
        return round(1 / total_prob, 2) if total_prob > 0 else 0
    
    return {'A1': theory(a1), 'A2': theory(a2), 'A3': theory(a3)}

def assign_groups(sorted_horses):
    groups = {
        'A1': sorted_horses[0:2] if len(sorted_horses) >= 2 else sorted_horses[:len(sorted_horses)],
        'A2': sorted_horses[2:5] if len(sorted_horses) >= 5 else sorted_horses[2:len(sorted_horses)],
        'A3': sorted_horses[5:] if len(sorted_horses) >= 5 else []
    }
    for g in groups:
        groups[g] = sorted(groups[g], key=lambda x: int(x[0]))
    return groups

def change_label(change):
    if change == 0: return "0 (不變)"
    return f"落飛" if change > 0 else f"回飛"

# ==================== 主 async 流程（雲端用預設值） ====================
async def main():
    global base_url, start_race, end_race, pages
    
    date = datetime.now().strftime("%Y-%m-%d")
    venue = "HV"
    start_race = 1
    end_race = 9
    
    base_url = f"https://bet.hkjc.com/ch/racing/wpq/{date}/{venue}"
    print(f"使用預設賽日: {date} {venue}, 場次 {start_race}–{end_race}")
    
    if not base_url:
        print("未設定賽日，結束")
        return
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        
        tasks = []
        for race_no in range(start_race, end_race + 1):
            context = await browser.new_context()
            page = await context.new_page()
            pages[race_no] = page
            tasks.append(asyncio.create_task(monitor_race(page, race_no)))
        
        await asyncio.gather(*tasks)
        
        await asyncio.sleep(3600 * 24)

# ==================== 後台監控每場 ====================
async def monitor_race(page, race_no):
    url = f"{base_url}/{race_no}"
    race_data[race_no] = {'current_odds': {}, 'last_update': None, 'theory': {}, 'horse_names': {}, 'five_odds': {}, 'status': '載入馬名中...'}
    
    await page.goto(url, wait_until="networkidle", timeout=60000)
    try:
        table_id = f"rc-odds-table-compact-{race_no}"
        table = await page.wait_for_selector(f"#{table_id}", timeout=45000)
        rows = await table.query_selector_all("tr")
        horse_names = {}
        for row in rows[1:-1]:
            cols = await row.query_selector_all("td")
            if len(cols) >= 6:
                horse_no_text = (await cols[0].inner_text()).strip()
                horse_name = (await cols[3].inner_text()).strip()
                if horse_no_text.isdigit():
                    horse_names[int(horse_no_text)] = horse_name
        race_data[race_no]['horse_names'] = horse_names
        race_data[race_no]['status'] = '馬名載入完成，等待馬會出賠率...'
        print(f"第 {race_no} 場 馬名載入完成")
    except Exception as e:
        print(f"第 {race_no} 場 抓馬名失敗: {e}")
        race_data[race_no]['horse_names'] = {i: f"馬{i}" for i in range(1, 15)}
        race_data[race_no]['status'] = '賽事未開始 / 無賠率表，使用預設馬名'
    
    five_odds = auto_fill_five_odds(race_no, race_data[race_no]['horse_names'])
    race_data[race_no]['five_odds'] = five_odds
    
    while True:
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            table = await page.wait_for_selector(f"#{table_id}", timeout=45000)
            rows = await table.query_selector_all("tr")
            
            current_odds = {}
            has_odds = False
            for row in rows[1:-1]:
                cols = await row.query_selector_all("td")
                if len(cols) >= 6:
                    horse_no_text = (await cols[0].inner_text()).strip()
                    if horse_no_text.isdigit():
                        horse_no = int(horse_no_text)
                        try:
                            win_a = await cols[4].query_selector("div[class*='win'] a")
                            if win_a:
                                win_odds_str = (await win_a.inner_text()).strip()
                                if win_odds_str.upper() == "SCR":
                                    continue
                                win_odds = float(win_odds_str) if win_odds_str != "N/A" else None
                                if win_odds is not None:
                                    has_odds = True
                                current_odds[horse_no] = win_odds
                        except:
                            pass
            
            if has_odds:
                race_data[race_no]['status'] = '已偵測到賠率，正在更新...'
                race_data[race_no]['current_odds'] = current_odds
                race_data[race_no]['five_theory'] = calculate_theory_odds(race_data[race_no]['five_odds'])
                race_data[race_no]['current_theory'] = calculate_theory_odds(current_odds)
                race_data[race_no]['last_update'] = datetime.now()
            else:
                race_data[race_no]['status'] = '馬會未出賠率，等待中...'
            
            global_data["race_data"][race_no] = race_data[race_no].copy()
            global_data["status"] = "更新中"
            global_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 第 {race_no} 場 更新完成 | 狀態: {race_data[race_no]['status']} | 賠率數量: {len(current_odds)}")
            
        except Exception as e:
            race_data[race_no]['status'] = f'更新錯誤: {str(e)[:50]}...'
            print(f"第 {race_no} 場 更新錯誤: {e}")
        
        await asyncio.sleep(1)

# ==================== Flask API 路由（只定義一次） ====================
@app.route('/api/data')
def api_data():
    return jsonify(global_data)

# ==================== Flask 主頁面 ====================
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
                    .then(response => {
                        if (!response.ok) {
                            console.error('API 錯誤:', response.status);
                            document.getElementById('loading').innerText = '載入失敗，請檢查後端';
                            return;
                        }
                        return response.json();
                    })
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
                            content = '<p>暫無數據，請等待監控更新...</p>';
                        }

                        document.getElementById('content').innerHTML = content;
                    })
                    .catch(error => {
                        document.getElementById('loading').innerText = '載入失敗，請檢查後端';
                        console.error('更新錯誤:', error);
                    });
            }

            setTimeout(updatePage, 5000);
            setInterval(updatePage, 1000);
        </script>
    </body>
    </html>
    """

# ==================== Flask API 路由（只定義一次） ====================
@app.route('/api/data')
def api_data():
    return jsonify(global_data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    flask_thread = threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': port, 'debug': False}, daemon=True)
    flask_thread.start()
    
    try:
        asyncio.run(main())
    except Exception as e:
        print("主流程錯誤:", e)
