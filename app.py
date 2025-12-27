import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# ==========================================
# 0. 설정 및 DB 연결
# ==========================================
st.set_page_config(page_title="Project Aegis V2", layout="wide")

# 구글 시트 연결 (Secrets에 넣은 정보 사용)
conn = st.connection("gsheets", type=GSheetsConnection)

# 🚨 여기에 아까 만든 구글 스프레드시트 주소를 넣으세요!
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# ==========================================
# 1. 핵심 엔진 (크롤링 & AI)
# ==========================================
def get_current_price(ticker):
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        price = soup.select_one("strong.quote-price").text.replace(',', '')
        return float(price)
    except:
        return 100.0 # 에러 시 임시값

def get_usd_krw():
    try:
        url = "https://finance.naver.com/marketindex/"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(res.text, "html.parser")
        usd = soup.select_one("div.head_info > span.value").text.replace(',', '')
        return float(usd)
    except:
        return 1450.0

# ==========================================
# 2. UI 구성
# ==========================================
st.title("🛡️ Project Aegis V2.0 (DB연동)")

# DB에서 데이터 읽어오기
data = conn.read(spreadsheet=SHEET_URL, usecols=[0, 1, 2, 3, 4])
df = pd.DataFrame(data)

# 사이드바: 거래 입력
st.sidebar.header("📝 거래 기록 (영구 저장)")
with st.sidebar.form("input_form"):
    date = st.date_input("날짜", datetime.today())
    ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM"])
    action = st.selectbox("유형", ["BUY", "SELL"])
    qty = st.number_input("수량", min_value=1, value=1)
    price = st.number_input("가격($)", min_value=0.0)
    
    if st.form_submit_button("장부에 기록하기"):
        # 새 데이터 추가 로직
        new_row = pd.DataFrame([{
            "Date": str(date),
            "Ticker": ticker,
            "Action": action,
            "Qty": qty,
            "Price": price
        }])
        updated_df = pd.concat([df, new_row], ignore_index=True)
        # 구글 시트에 업데이트
        conn.update(spreadsheet=SHEET_URL, data=updated_df)
        st.sidebar.success("✅ 저장 완료! (새로고침 됩니다)")
        st.rerun()

# 메인 화면: 자산 현황 계산
st.subheader("📊 현재 내 자산 (DB 기반)")

if not df.empty:
    # 보유량 계산 (BUY는 더하고 SELL은 빼기)
    holdings = df.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
    
    # 평가액 계산
    krw_rate = get_usd_krw()
    total_val = 0
    
    col1, col2, col3 = st.columns(3)
    col1.metric("환율", f"{krw_rate:,.0f} 원")
    
    asset_df_list = []
    for t, q in holdings.items():
        if q > 0:
            p = get_current_price(t)
            val = q * p * krw_rate
            total_val += val
            asset_df_list.append({"종목": t, "수량": q, "평가액": int(val)})
            
    col2.metric("총 자산", f"{int(total_val):,.0f} 원")
    
    st.dataframe(pd.DataFrame(asset_df_list))
else:
    st.info("👈 왼쪽에서 첫 거래를 기록해주세요!")
