import streamlit as st
import pandas as pd
import yfinance as yf
import time
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

# ==========================================
# 0. 설정 및 DB 연결
# ==========================================
st.set_page_config(page_title="Project Aegis", layout="wide")

conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# ==========================================
# 1. 핵심 엔진 (야후 파이낸스 & 환율)
# ==========================================
@st.cache_data(ttl=300) 
def get_current_price(ticker):
    try:
        if ticker == "GMMF": return 100.0 # GMMF는 일단 고정 (나중에 수정)
        stock = yf.Ticker(ticker)
        price = stock.history(period="1d")['Close'].iloc[-1]
        return float(price)
    except:
        return 0.0 

@st.cache_data(ttl=300)
def get_usd_krw():
    try:
        exchange = yf.Ticker("KRW=X")
        price = exchange.history(period="1d")['Close'].iloc[-1]
        return float(price)
    except:
        return 1450.0

# ==========================================
# 2. AI 리밸런싱 로직 (환율 고려 버전)
# ==========================================
class Rebalancer:
    def __init__(self, current_holdings):
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0} 
        self.holdings = current_holdings

    def analyze(self, investment_krw, current_rate):
        investment_usd = investment_krw / current_rate
        portfolio = {}
        total_value_usd = 0
        
        # 현재 자산 가치 계산
        for ticker, qty in self.holdings.items():
            price = get_current_price(ticker)
            if price == 0: price = 100 
            val = qty * price
            portfolio[ticker] = {'qty': qty, 'price': price, 'value': val}
            total_value_usd += val
            
        total_asset_usd = total_value_usd + investment_usd
        recommendations = []
        
        # 환율 코멘트 추가
        currency_msg = ""
        if current_rate > 1450:
            currency_msg = "⚠️ [환율 경고] 현재 환율이 높습니다(1,450원↑). 무리한 매수보다는 관망을 추천합니다."
        elif current_rate < 1350:
            currency_msg = "✅ [환율 호재] 환율이 낮습니다. 적극적으로 달러 자산을 늘리세요."
        
        for ticker, target_ratio in self.TARGET_RATIO.items():
            if target_ratio == 0: continue
            target_amt = total_asset_usd * target_ratio
            current_amt = portfolio.get(ticker, {'value': 0})['value']
            
            if current_amt < target_amt:
                shortfall = target_amt - current_amt
                price = portfolio.get(ticker, {'price': 100})['price']
                buy_qty = int(shortfall // price)
                
                if buy_qty > 0:
                    cost_krw = buy_qty * price * current_rate
                    recommendations.append({'ticker': ticker, 'qty': buy_qty, 'cost': cost_krw})
                    
        return recommendations, currency_msg

# ==========================================
# 3. 데이터 로딩 & 수익률 계산 로직
# ==========================================
st.title("🛡️ Project Aegis V4.0 (환율 정밀 분석)")

try:
    # F열(Exchange_Rate)까지 읽기 위해 usecols 범위 늘림 (0~5)
    data = conn.read(spreadsheet=SHEET_URL, usecols=[0, 1, 2, 3, 4, 5], ttl=0)
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.sort_values(by="Date", ascending=False)
except Exception as e:
    st.error(f"DB 연결/읽기 오류: 엑셀에 'Exchange_Rate' 열을 추가했는지 확인하세요! ({e})")
    df = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate"])

# 보유량 및 매입 원금 계산
total_invested_krw = 0 # 내가 쓴 총 원화(KRW)
current_holdings = {}

if not df.empty:
    # 1. 보유 수량 계산
    current_holdings = df.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
    
    # 2. 매입 원금 계산 (매수 당시 환율 적용)
    # (단순화를 위해 '현재 보유분'에 대한 매입가만 추산하는 방식이 아니라, 전체 투입 누적액으로 계산)
    buys = df[df['Action']=='BUY']
    sells = df[df['Action']=='SELL']
    
    # 총 매수 금액 (원화) = 수량 * 달러단가 * 매수환율
    total_bought_krw = (buys['Qty'] * buys['Price'] * buys['Exchange_Rate']).sum()
    # 총 매도 금액 (원화)
    total_sold_krw = (sells['Qty'] * sells['Price'] * sells['Exchange_Rate']).sum()
    
    # "현재 내 돈이 얼마나 들어가 있나" (순수 투자 원금)
    total_invested_krw = total_bought_krw - total_sold_krw
else:
    current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}

# ==========================================
# 4. 화면 구성
# ==========================================
krw_rate = get_usd_krw()

# [사이드바]
st.sidebar.header("📝 정밀 거래 기록")
with st.sidebar.form("input_form"):
    date = st.date_input("날짜", datetime.today())
    ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    action = st.selectbox("유형", ["BUY", "SELL"])
    qty = st.number_input("수량", min_value=1, value=1)
    
    # 가격 정보 자동 로딩 시도
    current_p = get_current_price(ticker)
    price = st.number_input("체결 단가($)", min_value=0.0, value=current_p if current_p > 0 else 0.0, format="%.2f")
    
    # 🔥 환율 입력칸 추가 (자동으로 현재 환율 채워짐)
    ex_rate = st.number_input("적용 환율(₩)", min_value=0.0, value=krw_rate, format="%.2f")
    
    if st.form_submit_button("장부에 기록하기"):
        with st.spinner("☁️ 기록 중..."):
            new_row = pd.DataFrame([{
                "Date": str(date), 
                "Ticker": ticker, 
                "Action": action, 
                "Qty": qty, 
                "Price": price,
                "Exchange_Rate": ex_rate # 환율 저장
            }])
            updated_df = pd.concat([df, new_row], ignore_index=True)
            conn.update(spreadsheet=SHEET_URL, data=updated_df)
            time.sleep(1) 
            st.cache_data.clear() 
        st.sidebar.success("✅ 저장 완료!")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("💰 AI 분석 설정")
investment = st.sidebar.number_input("여유 현금 (원)", min_value=0, value=0, step=10000)
run_ai = st.sidebar.button("AI 분석 실행")

# [메인 화면]
tab1, tab2, tab3 = st.tabs(["📊 자산 & 수익률", "🤖 AI 분석", "📋 거래 장부"])

with tab1:
    total_val = 0
    asset_list = []
    
    # 평가액 계산
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            if p == 0: p = 100.0 # 가격 못 찾을 시 임시값
            
            val = q * p * krw_rate # 현재 가치
            total_val += val
            asset_list.append({"종목": t, "수량": f"{q}주", "현재가($)": round(p, 2), "평가액(원)": int(val)})
            
    # 수익률 계산
    profit = total_val - total_invested_krw
    profit_rate = (profit / total_invested_krw * 100) if total_invested_krw > 0 else 0
    
    # 상단 메트릭 (핵심!)
    m1, m2, m3 = st.columns(3)
    m1.metric("현재 환율", f"{krw_rate:,.0f} 원/$")
    m2.metric("총 매입 원금 (투자금)", f"{int(total_invested_krw):,.0f} 원")
    m3.metric("총 평가금액 (현재)", f"{int(total_val):,.0f} 원", f"{int(profit):+,.0f} 원 ({profit_rate:.2f}%)")

    st.markdown("---")
    if asset_list:
        st.dataframe(pd.DataFrame(asset_list), width='stretch')
        st.bar_chart(pd.DataFrame(asset_list).set_index("종목")["평가액(원)"])
    else:
        st.info("👈 거래를 입력하세요.")

with tab2:
    if run_ai:
        bot = Rebalancer(current_holdings)
        recs, msg = bot.analyze(investment, krw_rate)
        
        st.subheader("🤖 AI의 전략 보고서")
        
        # 환율 코멘트 출력
        if msg:
            st.info(msg)
            
        if recs:
            st.write(f"💵 **투자금 {investment:,.0f}원**으로 다음을 매수하세요:")
            for r in recs:
                st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수 (약 {r['cost']:,.0f}원)")
        else:
            if not msg: st.balloons()
            st.success("✅ 포트폴리오 비율이 양호합니다.")

with tab3:
    st.subheader("📋 전체 거래 내역 (환율 포함)")
    st.dataframe(df, width='stretch')
