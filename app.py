import streamlit as st
import pandas as pd
import requests
import time # 👈 시간 지연을 위해 추가된 부품
from bs4 import BeautifulSoup
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# ==========================================
# 0. 설정 및 DB 연결
# ==========================================
st.set_page_config(page_title="Project Aegis", layout="wide")

conn = st.connection("gsheets", type=GSheetsConnection)

# 🚨 선생님 엑셀 주소 (그대로 유지)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# ==========================================
# 1. 핵심 엔진 (크롤링 & AI)
# ==========================================
@st.cache_data(ttl=300) 
def get_current_price(ticker):
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        price = soup.select_one("strong.quote-price").text.replace(',', '')
        return float(price)
    except:
        return 100.0

@st.cache_data(ttl=300)
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
# 2. AI 리밸런싱 로직
# ==========================================
class Rebalancer:
    def __init__(self, current_holdings):
        # 🚨 GMMF를 목표 비중에도 추가해야 AI가 인식합니다! (일단 0%로 둠)
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35} 
        self.holdings = current_holdings

    def analyze(self, investment_krw, exchange_rate):
        investment_usd = investment_krw / exchange_rate
        portfolio = {}
        total_value_usd = 0
        
        for ticker, qty in self.holdings.items():
            price = get_current_price(ticker)
            val = qty * price
            portfolio[ticker] = {'qty': qty, 'price': price, 'value': val}
            total_value_usd += val
            
        total_asset_usd = total_value_usd + investment_usd
        recommendations = []
        
        for ticker, target_ratio in self.TARGET_RATIO.items():
            target_amt = total_asset_usd * target_ratio
            current_amt = portfolio.get(ticker, {'value': 0})['value']
            
            if current_amt < target_amt:
                shortfall = target_amt - current_amt
                price = portfolio.get(ticker, {'price': 100})['price']
                buy_qty = int(shortfall // price)
                
                if buy_qty > 0:
                    cost_krw = buy_qty * price * exchange_rate
                    recommendations.append({'ticker': ticker, 'qty': buy_qty, 'cost': cost_krw})
        return recommendations

# ==========================================
# 3. 데이터 로딩 (즉시 로딩 모드)
# ==========================================
st.title("🛡️ Project Aegis V3.1")

try:
    # ttl=0 으로 캐시 끄기
    data = conn.read(spreadsheet=SHEET_URL, usecols=[0, 1, 2, 3, 4], ttl=0)
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.sort_values(by="Date", ascending=False)
except Exception as e:
    st.error(f"DB 연결 오류: {e}")
    df = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price"])

if not df.empty:
    current_holdings = df.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
else:
    current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}

# ==========================================
# 4. 화면 구성
# ==========================================
st.sidebar.header("📝 거래 기록")
with st.sidebar.form("input_form"):
    date = st.date_input("날짜", datetime.today())
    
    # 🚨 여기에 GMMF 추가했습니다!
    ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    
    action = st.selectbox("유형", ["BUY", "SELL"])
    qty = st.number_input("수량", min_value=1, value=1)
    price = st.number_input("가격($)", min_value=0.0)
    
    if st.form_submit_button("장부에 기록하기"):
        with st.spinner("☁️ 구글 엑셀에 저장 중... (잠시만요!)"):
            new_row = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": qty, "Price": price}])
            updated_df = pd.concat([df, new_row], ignore_index=True)
            conn.update(spreadsheet=SHEET_URL, data=updated_df)
            
            # 🔥 핵심: 구글이 저장할 시간을 2초 줍니다.
            time.sleep(2) 
            st.cache_data.clear() 
            
        st.sidebar.success("✅ 저장 완료!")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("💰 AI 분석 설정")
investment = st.sidebar.number_input("여유 현금 (원)", min_value=0, value=0, step=10000)
run_ai = st.sidebar.button("AI 분석 실행")

tab1, tab2, tab3 = st.tabs(["📊 자산 현황", "🤖 AI 분석", "📋 거래 장부"])

with tab1:
    krw_rate = get_usd_krw()
    total_val = 0
    asset_list = []
    col1, col2 = st.columns(2)
    col1.metric("현재 환율", f"{krw_rate:,.0f} 원/$")
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            val = q * p * krw_rate
            total_val += val
            asset_list.append({"종목": t, "수량": f"{q}주", "현재가($)": p, "평가액(원)": int(val)})
    col2.metric("총 자산 (추정)", f"{int(total_val):,.0f} 원")
    if asset_list:
        st.dataframe(pd.DataFrame(asset_list), width='stretch')
        st.bar_chart(pd.DataFrame(asset_list).set_index("종목")["평가액(원)"])
    else:
        st.info("👈 거래를 입력하세요.")

with tab2:
    if run_ai:
        if total_val == 0 and investment == 0:
            st.warning("데이터가 부족합니다.")
        else:
            bot = Rebalancer(current_holdings)
            recs = bot.analyze(investment, krw_rate)
            st.subheader("🤖 AI의 매수 제안")
            if recs:
                st.write(f"💵 **투자금 {investment:,.0f}원**으로 다음을 매수하세요:")
                for r in recs:
                    st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수")
            else:
                st.balloons()
                st.success("✅ 비율 완벽! 환전만 하세요.")

with tab3:
    st.subheader("📋 전체 거래 내역")
    st.dataframe(df, width='stretch')
