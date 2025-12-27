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
# 1. 핵심 엔진 (가격 조회 + 비상 대책)
# ==========================================
@st.cache_data(ttl=300) 
def get_current_price(ticker):
    """
    1. 야후 파이낸스에서 조회를 시도합니다.
    2. 실패하면 0.0을 반환합니다.
    """
    try:
        # GMMF 차단 해제! 모든 티커 조회 시도
        stock = yf.Ticker(ticker)
        
        # history()는 데이터가 없으면 빈 표를 줍니다.
        hist = stock.history(period="1d")
        
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        else:
            return 0.0 # 데이터 없음
    except:
        return 0.0 # 에러 발생

@st.cache_data(ttl=300)
def get_usd_krw():
    try:
        exchange = yf.Ticker("KRW=X")
        price = exchange.history(period="1d")['Close'].iloc[-1]
        return float(price)
    except:
        return 1450.0

# ==========================================
# 2. AI 리밸런싱 로직
# ==========================================
class Rebalancer:
    def __init__(self, current_holdings, df_history):
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0} 
        self.holdings = current_holdings
        self.df_history = df_history

    def get_price_safe(self, ticker):
        # 1. 실시간 시세 조회
        price = get_current_price(ticker)
        
        # 2. 실패(0원) 시, 내 장부에서 가장 최근 가격 가져오기
        if price == 0 and not self.df_history.empty:
            recent_record = self.df_history[self.df_history['Ticker'] == ticker]
            if not recent_record.empty:
                price = recent_record.iloc[0]['Price'] # 최신 기록 사용
        
        # 3. 그래도 없으면 100불 임시값
        if price == 0: price = 100.0
        return price

    def analyze(self, investment_krw, current_rate):
        investment_usd = investment_krw / current_rate
        portfolio = {}
        total_value_usd = 0
        
        for ticker, qty in self.holdings.items():
            price = self.get_price_safe(ticker)
            val = qty * price
            portfolio[ticker] = {'qty': qty, 'price': price, 'value': val}
            total_value_usd += val
            
        total_asset_usd = total_value_usd + investment_usd
        recommendations = []
        
        currency_msg = ""
        if current_rate > 1450:
            currency_msg = "⚠️ [환율 경고] 환율(1,450원↑)이 높습니다. 매수 시 신중하세요."
        elif current_rate < 1350:
            currency_msg = "✅ [환율 호재] 환율이 안정적입니다. 적립식 매수하기 좋습니다."
        
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
# 3. 데이터 로딩 & 정밀 수익률 계산
# ==========================================
st.title("🛡️ Project Aegis V4.2 (스마트 가격 조회)")

try:
    data = conn.read(spreadsheet=SHEET_URL, usecols=[0, 1, 2, 3, 4, 5, 6], ttl=0)
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.sort_values(by="Date", ascending=False)
        df = df.fillna(0)
except Exception as e:
    st.error(f"DB 오류: {e}")
    df = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"])

total_invested_krw = 0 
current_holdings = {}

if not df.empty:
    current_holdings = df.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
    
    buys = df[df['Action']=='BUY']
    sells = df[df['Action']=='SELL']
    
    total_bought_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
    total_sold_krw = ((sells['Qty'] * sells['Price'] - sells['Fee']) * sells['Exchange_Rate']).sum()
    total_invested_krw = total_bought_krw - total_sold_krw
else:
    current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}

# ==========================================
# 4. 화면 구성
# ==========================================
krw_rate = get_usd_krw()

st.sidebar.header("📝 정밀 거래 기록")
with st.sidebar.form("input_form"):
    date = st.date_input("날짜", datetime.today())
    ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    action = st.selectbox("유형", ["BUY", "SELL"])
    qty = st.number_input("수량", min_value=1, value=1)
    
    # 가격 자동 채우기 (실패 시 마지막 기록 사용)
    current_p = get_current_price(ticker)
    if current_p == 0 and not df.empty:
         # DB에서 마지막 기록 찾기
         last_record = df[df['Ticker'] == ticker]
         if not last_record.empty:
             current_p = last_record.iloc[0]['Price']
             
    price = st.number_input("체결 단가($)", min_value=0.0, value=current_p if current_p > 0 else 0.0, format="%.2f")
    fee = st.number_input("수수료($)", min_value=0.0, value=0.0, format="%.2f")
    ex_rate = st.number_input("적용 환율(₩)", min_value=0.0, value=krw_rate, format="%.2f")
    
    if st.form_submit_button("장부에 기록하기"):
        with st.spinner("☁️ 기록 중..."):
            new_row = pd.DataFrame([{
                "Date": str(date), "Ticker": ticker, "Action": action, 
                "Qty": qty, "Price": price, "Exchange_Rate": ex_rate, "Fee": fee
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

tab1, tab2, tab3 = st.tabs(["📊 자산 & 수익률", "🤖 AI 분석", "📋 거래 장부"])

with tab1:
    total_val = 0
    asset_list = []
    
    for t, q in current_holdings.items():
        if q > 0:
            # 1. 인터넷 조회 시도
            p = get_current_price(t)
            source = "실시간"
            
            # 2. 실패 시 내 장부(DB)에서 가져오기
            if p == 0:
                if not df.empty:
                    last_rec = df[df['Ticker'] == t]
                    if not last_rec.empty:
                        p = last_rec.iloc[0]['Price']
                        source = "장부기록"
                if p == 0: p = 100.0 # 그래도 없으면 100
            
            val = q * p * krw_rate
            total_val += val
            asset_list.append({
                "종목": t, 
                "수량": f"{q}주", 
                "현재가($)": round(p, 2), 
                "출처": source, # 가격 출처 표시
                "평가액(원)": int(val)
            })
            
    profit = total_val - total_invested_krw
    profit_rate = (profit / total_invested_krw * 100) if total_invested_krw > 0 else 0
    
    m1, m2, m3 = st.columns(3)
    m1.metric("현재 환율", f"{krw_rate:,.0f} 원/$")
    m2.metric("총 투입 원금", f"{int(total_invested_krw):,.0f} 원")
    m3.metric("현재 평가액", f"{int(total_val):,.0f} 원", f"{int(profit):+,.0f} 원 ({profit_rate:.2f}%)")

    st.markdown("---")
    if asset_list:
        st.dataframe(pd.DataFrame(asset_list), width='stretch')
        st.bar_chart(pd.DataFrame(asset_list).set_index("종목")["평가액(원)"])
    else:
        st.info("👈 거래를 입력하세요.")

with tab2:
    if run_ai:
        bot = Rebalancer(current_holdings, df)
        recs, msg = bot.analyze(investment, krw_rate)
        st.subheader("🤖 AI의 전략 보고서")
        if msg: st.info(msg)
        if recs:
            st.write(f"💵 **투자금 {investment:,.0f}원**으로 다음을 매수하세요:")
            for r in recs:
                st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수")
        else:
            if not msg: st.balloons()
            st.success("✅ 포트폴리오 비율이 양호합니다.")

with tab3:
    st.subheader("📋 전체 거래 내역")
    st.dataframe(df, width='stretch')
