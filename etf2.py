import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import urllib3 
import gspread
from google.oauth2.service_account import Credentials
import json
import re

# 關閉verify產生的安全警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 0. 全域設定
# ==========================================
MY_ETF_LIST = [
    "0050.TW", 
    "00980A.TW", 
    "00878.TW", 
    "00981A.TW", 
    "00982A.TW",
    "00992A.TW",
    "00991A.TW",
    "00919.TW",
    "00929.TW",
    "0056.TW"
]
ETF_NAME_MAP = {
    "0050.TW": "0050 元大台灣50",
    "0056.TW": "0056 元大高股息",
    "00980A.TW": "00980A 野村臺灣優選(主動)",
    "00981A.TW": "00981A 統一台股增長(主動)",
    "00982A.TW": "00982A 群益台灣精選(主動)",
    "00991A.TW": "00991A 復華台灣未來50(主動)",
    "00992A.TW": "00992A 群益台灣科技創新(主動)",
    "00878.TW": "00878 國泰永續高股息",
    "00919.TW": "00919 群益台灣精選高息",
    "00929.TW": "00929 復華台灣科技優息"
}

# ⚠️名字與Google雲端硬碟建立的試算表名稱一致
SPREADSHEET_NAME = "ETF_Holdings_Data" 

# ==========================================
# 1. Google Sheets 連線設定
# ==========================================
@st.cache_resource
def get_gspread_client():
    # 從 Streamlit Secrets 讀取並解析 JSON
    creds_dict = json.loads(st.secrets["gcp_json"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

client = get_gspread_client()
spreadsheet = client.open(SPREADSHEET_NAME)

def get_history_from_gsheets(etf_id):
    try:
        worksheet = spreadsheet.worksheet(etf_id)
        data = worksheet.get_all_records()
        if not data:
            return None
        return pd.DataFrame(data)
    except gspread.exceptions.WorksheetNotFound:
        return None

def save_history_to_gsheets(etf_id, df):
    try:
        worksheet = spreadsheet.worksheet(etf_id)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        # 如果沒有該 ETF 的分頁，就自動建立一個
        worksheet = spreadsheet.add_worksheet(title=etf_id, rows="100", cols="10")
    
    # 轉換為 list of lists 以便寫入 (這裡只有 標的、比例、股數 三欄，佔用 A, B, C 欄)
    df_clean = df.fillna("")
    data = [df_clean.columns.values.tolist()] + df_clean.values.tolist()
    worksheet.update(values=data, range_name="A1")
    
    # ⚠️ 關鍵升級：讓 Python 自動把 Google Finance 陣列公式寫入 D1 欄位！
    # 這個公式會自動判定：先用 TPE(上市) 查，查不到自動切換 TWO(上櫃)
    # ⚠️ 關鍵升級：使用 [0-9]+ 最單純的正規表達式，防止括號在傳輸過程中被吃掉
    formula = '={"今日漲跌幅(%)"; ARRAYFORMULA(IF(A2:A="", "", IFERROR(GOOGLEFINANCE("TPE:" & REGEXEXTRACT(A2:A, "[0-9]+"), "changepct"), IFERROR(GOOGLEFINANCE("TWO:" & REGEXEXTRACT(A2:A, "[0-9]+"), "changepct"), 0))))}'
    
    # 強制以「使用者輸入(USER_ENTERED)」模式寫入，確保 Google 試算表會把它當作公式執行
    worksheet.update(values=[[formula]], range_name="D1", value_input_option="USER_ENTERED")

# ==========================================
# 2. 爬蟲函數
# ==========================================
@st.cache_data(ttl=3600)
def fetch_etf_holdings(etf_id):
    url = f"https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={etf_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    response = requests.get(url, headers=headers, verify=False)
    if response.status_code != 200:
        return None
        
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select("tbody tr")
    
    data_list = []
    for row in rows:
        col_stock = row.select_one("td.col05")
        col_weight = row.select_one("td.col06")
        col_shares = row.select_one("td.col07")
        
        if col_stock and col_weight and col_shares:
            stock_info = col_stock.text.strip()
            weight = float(col_weight.text.strip().replace(',', ''))
            shares = int(col_shares.text.strip().replace(',', ''))
            data_list.append({
                "標的": stock_info,
                "比例(%)": weight,
                "股數": shares
            })
            
    return pd.DataFrame(data_list)

# ==========================================
# 3. 比較增減函數
# ==========================================
@st.cache_data(ttl=3600)
def get_today_price_change(stock_names):
    # 1. 萃取股票代號，並同時準備上市(.TW)與上櫃(.TWO)兩種格式
    tickers_tw = []
    tickers_two = []
    for name in stock_names:
        match = re.search(r'\((.*?)\)', str(name))
        if match:
            # 去除原始的 .TW 尾巴，只留數字代號
            base_sym = match.group(1).replace('.TW', '').replace('.TWO', '')
            tickers_tw.append(f"{base_sym}.TW")
            tickers_two.append(f"{base_sym}.TWO")
            
    all_symbols = tickers_tw + tickers_two
    if not all_symbols:
        return {}
        
    # 2. 透過 Yahoo 報價 API 批量抓取 (速度最快、不被雲端阻擋)
    change_map = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    # 每次最多查詢 50 檔，避免網址過長
    chunk_size = 50
    for i in range(0, len(all_symbols), chunk_size):
        chunk = all_symbols[i:i+chunk_size]
        url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={','.join(chunk)}"
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                results = data.get("quoteResponse", {}).get("result", [])
                for item in results:
                    symbol = item.get("symbol")
                    # 直接抓取 API 算好的漲跌幅百分比
                    change_percent = item.get("regularMarketChangePercent", 0.0)
                    change_map[symbol] = change_percent
        except Exception:
            continue
            
    # 3. 對應回原本的中文名稱
    result_dict = {}
    for name in stock_names:
        match = re.search(r'\((.*?)\)', str(name))
        if match:
            base_sym = match.group(1).replace('.TW', '').replace('.TWO', '')
            # 優先抓 .TW 的資料，沒有的話再看 .TWO 有沒有資料
            val = change_map.get(f"{base_sym}.TW", change_map.get(f"{base_sym}.TWO", 0.0))
            result_dict[name] = val
        else:
            result_dict[name] = 0.0
            
    return result_dict
    
def compare_holdings(df_current, df_previous):
    df_current['標的'] = df_current['標的'].astype(str).str.strip()
    df_previous['標的'] = df_previous['標的'].astype(str).str.strip()

    df_current['比例(%)'] = pd.to_numeric(df_current['比例(%)'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    df_previous['比例(%)'] = pd.to_numeric(df_previous['比例(%)'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    
    df_current['股數'] = pd.to_numeric(df_current['股數'].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
    df_previous['股數'] = pd.to_numeric(df_previous['股數'].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
    
    df_merge = pd.merge(df_current, df_previous, on="標的", how="outer", suffixes=('_今', '_昨'))
    df_merge = df_merge.fillna(0)
    
    df_merge['比例增減(%)'] = (df_merge['比例(%)_今'] - df_merge['比例(%)_昨']).round(4)
    df_merge['股數增減'] = (df_merge['股數_今'] - df_merge['股數_昨']).astype(int)
    
    # 呼叫函數取得漲跌幅
    stock_names = df_merge['標的'].tolist()
    changes_dict = get_today_price_change(stock_names)
    
    # ⚠️ 關鍵修正：強制轉型為 float，避免空值變成無法格式化的字串
    df_merge['今日漲跌幅(%)'] = df_merge['標的'].map(changes_dict).fillna(0.0).astype(float)
    
    df_result = df_merge[['標的', '今日漲跌幅(%)', '比例(%)_今', '比例增減(%)', '股數_今', '股數增減']]
    df_result.columns = ['標的', '今日漲跌幅(%)', '今日比例(%)', '比例增減(%)', '今日股數', '股數增減']
    df_result['今日股數'] = df_result['今日股數'].astype(int)
    
    return df_result
    
# ==========================================
# 4. Streamlit 介面與邏輯
# ==========================================
st.set_page_config(page_title="ETF 持股變化追蹤", layout="wide")

# -- 側邊欄：List更新 --
with st.sidebar:
    st.header("⚙️ ETF List管理區")
    st.markdown("一鍵抓取並覆蓋清單內所有 ETF 至 Google 試算表。")
    st.write("目前追蹤清單：")
    st.code(", ".join(MY_ETF_LIST))

    st.divider()

    admin_pw = st.text_input("管理員密碼", type="password")

    if admin_pw == "andyetf888":
    
        if st.button("🚀 雲端一鍵更新所有 ETF", type="primary"):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            total_etf = len(MY_ETF_LIST)
            success_count = 0
            
            for i, etf in enumerate(MY_ETF_LIST):
                status_text.text(f"正在抓取 {etf} ({i+1}/{total_etf})...")
                fetch_etf_holdings.clear() 
                df_temp = fetch_etf_holdings(etf)
                
                if df_temp is not None and not df_temp.empty:
                    save_history_to_gsheets(etf, df_temp)
                    success_count += 1
                
                progress_bar.progress((i + 1) / total_etf)
                if i < total_etf - 1:
                    time.sleep(3)
            status_text.success(f"🎉 雲端更新完成！成功覆蓋 {success_count} 檔 ETF 紀錄。")
            
    else:
        # 密碼不對時，顯示提示，不顯示按鈕
        if admin_pw != "":
            st.error("密碼錯誤，無權限更新。")
        else:
            st.info("請輸入密碼解鎖更新按鈕。")
                    
        

# -- 主畫面：單檔查詢 --
st.title("📈 台股 ETF 每日持股變化追蹤")
st.markdown("抓取 MoneyDJ 資料，並與 Google 試算表歷史紀錄比對增減。")

col1, col2 = st.columns([1, 2])
with col1:
    etf_input = st.selectbox("請選擇要查詢的 ETF", MY_ETF_LIST)
with col2:
    st.write("") 
    st.write("") 
    fetch_btn = st.button("抓取今日資料並比較")

if 'df_current' not in st.session_state:
    st.session_state.df_current = None
if 'etf_id' not in st.session_state:
    st.session_state.etf_id = ""

if fetch_btn:
    with st.spinner(f"正在抓取 {etf_input} 最新資料..."):
        df_today = fetch_etf_holdings(etf_input)
        
        if df_today is not None and not df_today.empty:
            st.session_state.df_current = df_today
            st.session_state.etf_id = etf_input
            st.success("抓取成功！")
        else:
            st.error("抓取失敗，請檢查網路連線。")

if st.session_state.df_current is not None:
    current_etf = st.session_state.etf_id
    df_today = st.session_state.df_current
    
    st.divider()
    
    # 從 Google Sheets 讀取昨日資料
    df_yesterday = get_history_from_gsheets(current_etf)
    
    if df_yesterday is not None and not df_yesterday.empty:
        etf_full_name = ETF_NAME_MAP.get(current_etf, current_etf)
        st.subheader(f"{etf_full_name} - 持股變化比較")
        df_comparison = compare_holdings(df_today, df_yesterday)
        df_comparison = df_comparison.sort_values(by="比例增減(%)", ascending=False).reset_index(drop=True)
        
        def color_change(val):
            try:
                # 確保能轉為數字判斷
                val_float = float(val)
                if val_float > 0:
                    return 'color: red'
                elif val_float < 0:
                    return 'color: green'
            except:
                pass
            return 'color: gray'
            
        # 套用顏色並強制顯示正負號格式
        styled_df = df_comparison.style\
            .map(color_change, subset=['今日漲跌幅(%)', '比例增減(%)', '股數增減'])\
            .format({
                # ⚠️ 關鍵修正：使用 lambda 強制處理小數點與正負號
                "今日漲跌幅(%)": lambda x: f"{x:+.2f}" if pd.notna(x) else "+0.00",
                "今日比例(%)": "{:.4f}",
                "比例增減(%)": "{:+.4f}",
                "今日股數": "{:,.0f}",
                "股數增減": "{:+,.0f}"
            })
        
        st.dataframe(styled_df, use_container_width=True, height=500)
        
    else:
        st.info(f"Google 試算表中尚未建立 {current_etf} 的歷史紀錄。以下為今日最新持股。")
        st.dataframe(df_today, use_container_width=True)

    st.divider()
    st.markdown("### 單獨更新雲端歷史紀錄")
    st.warning("⚠️ 儲存後，今日的資料將寫入 Google 試算表，成為明天的「前次紀錄」。")
    if st.button(f"將 {current_etf} 寫入 Google 試算表"):
        with st.spinner("正在寫入雲端..."):
            save_history_to_gsheets(current_etf, df_today)
        st.success(f"✅ 已成功將 {current_etf} 今日資料儲存至 Google 試算表！")
