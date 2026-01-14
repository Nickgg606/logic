import asyncio
import threading
import time
from datetime import datetime
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from flask import Flask, render_template_string, jsonify
import os  # 用來讀取環境變數 PORT

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

# ==================== 主 async 流程（雲端用預設值，無 GUI） ====================
async def main():
    global base_url, start_race, end_race, pages
    
    # 雲端預設賽日（可改為環境變數）
    date = datetime.now().strftime("%Y-%m-%d")
    venue = "HV"  # 或 "ST"
    start_race = 1
    end_race = 9
    
    base_url = f"https://bet.hkjc.com/ch/racing/wpq/{date}/{venue}"
    print(f"使用預設賽日: {date} {venue}, 場次 {start_race}–{end_race}")
    
    if not base_url:
        print("未設定賽日，結束")
        return
    
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        
        tasks = []
        for race_no in range(start_race, end_race + 1):
            context = await browser.new_context()
            page = await context.new_page()
            pages[race_no] = page
            tasks.append(asyncio.create_task(monitor_race(page, race_no)))
        
        await asyncio.gather(*tasks)
        
        await asyncio.sleep(3600 * 24)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))  # Render 會設 PORT，否則用 5000
    flask_thread = threading.Thread(target=app.run, kwargs={'host': '0.0.0.0', 'port': port, 'debug': False}, daemon=True)
    flask_thread.start()
    
    try:
        asyncio.run(main())
    except Exception as e:
        print("主流程錯誤:", e)
