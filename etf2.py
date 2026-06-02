import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import os
import time
import urllib3 

# 關閉因為 verify=False 而產生的安全警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 0. 設定你要追蹤的 ETF 清單
# ==========================================
MY_ETF_LIST = [
    "0050.TW", 
    "00980A.TW", 
    "00878.TW", 
    "00981A.TW", 
    "00982A.TW",
    "00992A.TW",
    "00991A.TW"
]

@st.cache_data(ttl=3600)
def fetch_etf_holdings(etf_id):
    url = f"https://www.moneydj.com/ETF/X/Basic/Basic0007B.xdjhtm?etfid={etf_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 修改這裡：加上 verify=False
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
            # 清理資料：移除逗號並轉換型態
            weight = float(col_weight.text.strip().replace(',', ''))
            shares = int(col_shares.text.strip().replace(',', ''))
            
            data_list.append({
                "標的": stock_info,
                "比例(%)": weight,
                "股數": shares
            })
            
    return pd.DataFrame(data_list)

# ==========================================
# 2. 比較增減函數
# ==========================================
def compare_holdings(df_current, df_previous):
    # 使用 outer join 合併新舊資料，確保新增或剔除的成分股都能比對到
    df_merge = pd.merge(df_current, df_previous, on="標的", how="outer", suffixes=('_今', '_昨'))
    
    # 將 NaN (缺失值) 補為 0 (代表前一日沒有，或是今日被剔除)
    df_merge = df_merge.fillna(0)
    
    # 計算增減
    df_merge['比例增減(%)'] = (df_merge['比例(%)_今'] - df_merge['比例(%)_昨']).round(4)
    df_merge['股數增減'] = (df_merge['股數_今'] - df_merge['股數_昨']).astype(int)
    
    # 重新整理顯示欄位順序
    df_result = df_merge[['標的', '比例(%)_今', '比例增減(%)', '股數_今', '股數增減']]
    df_result.columns = ['標的', '今日比例(%)', '比例增減(%)', '今日股數', '股數增減']
    
    return df_result

# ==========================================
# 3. Streamlit 介面與邏輯 (側邊欄批次更新)
# ==========================================
st.set_page_config(page_title="ETF 持股變化追蹤", layout="wide")

with st.sidebar:
    st.header("⚙️ 批次管理區")
    st.markdown("一鍵抓取並覆蓋清單內所有 ETF 的本地紀錄。")
    st.write("目前追蹤清單：")
    st.code(", ".join(MY_ETF_LIST))
    
    if st.button("🚀 一鍵更新所有 ETF", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total_etf = len(MY_ETF_LIST)
        success_count = 0
        
        for i, etf in enumerate(MY_ETF_LIST):
            status_text.text(f"正在抓取 {etf} ({i+1}/{total_etf})...")
            
            # 強制清除快取，確保抓到最新資料
            fetch_etf_holdings.clear() 
            df_temp = fetch_etf_holdings(etf)
            
            if df_temp is not None and not df_temp.empty:
                temp_file_path = f"{etf}_history.csv"
                df_temp.to_csv(temp_file_path, index=False, encoding='utf-8-sig')
                success_count += 1
            
            # 更新進度條
            progress_bar.progress((i + 1) / total_etf)
            
            # 暫停 3 秒防封鎖 (最後一檔不用等)
            if i < total_etf - 1:
                time.sleep(3)
                
        status_text.success(f"🎉 更新完成！成功覆蓋 {success_count} 檔 ETF 紀錄。")

# ==========================================
# 4. Streamlit 介面與邏輯 (主畫面查詢)
# ==========================================
st.title("📈 台股 ETF 每日持股變化追蹤")
st.markdown("抓取 MoneyDJ 資料，並與歷史紀錄比對增減。")

# 使用者輸入區 (改用下拉選單連動清單)
col1, col2 = st.columns([1, 2])
with col1:
    etf_input = st.selectbox("請選擇要查詢的 ETF", MY_ETF_LIST)
with col2:
    st.write("") # 排版用
    st.write("") 
    fetch_btn = st.button("抓取今日資料並比較")

# 初始化 session_state，讓資料在按鈕點擊後不會消失
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

# 顯示資料與比較邏輯
if st.session_state.df_current is not None:
    current_etf = st.session_state.etf_id
    df_today = st.session_state.df_current
    file_path = f"{current_etf}_history.csv"
    
    st.divider()
    
    # 檢查是否有歷史檔案可供比較
    if os.path.exists(file_path):
        st.subheader(f"{current_etf} - 持股變化比較")
        
        # 讀取昨天的資料
        df_yesterday = pd.read_csv(file_path)
        
        # 進行比較
        df_comparison = compare_holdings(df_today, df_yesterday)
        
        # 排序：依照比例增減，由大到小排序，方便觀察投信買了誰
        df_comparison = df_comparison.sort_values(by="比例增減(%)", ascending=False).reset_index(drop=True)
        
        # 使用 Pandas Styler 替增減欄位上色 (台股習慣：正為紅，負為綠)
        def color_change(val):
            if val > 0:
                return 'color: red'
            elif val < 0:
                return 'color: green'
            return 'color: gray'
            
        styled_df = df_comparison.style.map(color_change, subset=['比例增減(%)', '股數增減'])
        
        # 顯示表格
        st.dataframe(styled_df, use_container_width=True, height=500)
        
    else:
        st.info(f"尚未建立 {current_etf} 的歷史紀錄。以下為今日最新持股。請點擊下方按鈕儲存作為未來的比較基準。")
        st.dataframe(df_today, use_container_width=True)

    # 提供儲存按鈕，將今日資料覆蓋寫入 CSV
    st.divider()
    st.markdown("### 單獨更新歷史紀錄")
    st.warning("⚠️ 儲存後，今日的資料將成為明天的「前次紀錄」。(若已使用左側一鍵更新，則無需點擊)")
    if st.button(f"將 {current_etf} 存為最新歷史紀錄"):
        df_today.to_csv(file_path, index=False, encoding='utf-8-sig')
        st.success(f"已成功將 {current_etf} 今日資料儲存至 {file_path}！下次查詢時將以此為比較基準。")