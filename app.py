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
# 1. 핵심 엔진
# ==========================================
@st.cache_data(ttl=300) 
def get_current_price(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        else:
            return 0.0
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
# 2. AI 리밸런싱 로직
# ==========================================
class Rebalancer:
    def __init__(self, current_holdings):
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0} 
        self.holdings = current_holdings

    def analyze(self, investment_krw, current_rate):
        investment_usd = investment_krw / current_rate
        portfolio = {}
        total_value_usd = 0
        
        for ticker, qty in self.holdings.items():
            price = get_current_price(ticker)
            if price == 0: price = 100.0
            val = qty * price
            portfolio[ticker] = {'qty': qty, 'price': price, 'value': val}
            total_value_usd += val
            
        total_asset_usd = total_value_usd + investment_usd
        recommendations = []
        msg = ""

        # 환율 분석
        if current_rate > 1450:
            msg = f"⚠️ [환율 주의] 현재 {current_rate:,.0f}원입니다. 환전보다는 관망을 추천합니다."
        elif current_rate < 1380:
            msg = f"✅ [매수 기회] 환율이 {current_rate:,.0f}원까지 내려왔습니다. 달러 자산을 늘리세요."
        
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
                    
        return recommendations, msg

# ==========================================
# 3. 데이터 로딩 & 수익률 계산 (배당금 포함)
# ==========================================
st.title("🛡️ Project Aegis V5.0 (배당 & 자동화)")

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
    # 보유량 계산 (배당은 수량에 영향 없음)
    current_holdings = df.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
    
    buys = df[df['Action']=='BUY']
    sells = df[df['Action']=='SELL']
    divs = df[df['Action']=='DIVIDEND'] # 배당금 내역
    
    # 1. 총 매수 투입 (주식값 + 수수료)
    total_bought_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
    
    # 2. 총 매도 회수 (주식값 - 수수료)
    total_sold_krw = ((sells['Qty'] * sells['Price'] - sells['Fee']) * sells['Exchange_Rate']).sum()
    
    # 3. 총 배당 수익 (세후 금액 기준, 수수료는 보통 없지만 있으면 차감)
    # 배당은 'Price' 칸에 배당금 총액($)을 적는 것으로 가정
    total_div_krw = (divs['Price'] * divs['Exchange_Rate']).sum()

    # 🔥 순수 투자 원금 = (산 돈) - (판 돈) - (받은 배당금)
    # 배당을 받을수록 내 원금이 회수되는 효과!
    total_invested_krw = total_bought_krw - total_sold_krw - total_div_krw
else:
    current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}

# ==========================================
# 4. 화면 구성
# ==========================================
krw_rate = get_usd_krw()

st.sidebar.header("📝 거래/배당 기록")
with st.sidebar.form("input_form"):
    date = st.date_input("날짜", datetime.today())
    ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    
    # 🔥 DIVIDEND(배당) 추가
    action = st.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
    
    # 입력 필드 안내 메시지 변경
    if action == "DIVIDEND":
        st.info("💡 배당금 입력 모드: '가격' 칸에 받은 배당금 총액($)을 적으세요. 수량은 1로 두세요.")
        
    qty = st.number_input("수량 (배당일 땐 1)", min_value=0.0, value=1.0, step=0.01)
    
    # 가격 정보
    current_p = 0.0
    if action != "DIVIDEND":
        current_p = get_current_price(ticker)
        if current_p == 0 and not df.empty:
            last_rec = df[df['Ticker'] == ticker]
            if not last_rec.empty:
                 current_p = last_rec.iloc[0]['Price']
    
    label_price = "배당금 총액($)" if action == "DIVIDEND" else "단가($)"
    price = st.number_input(label_price, min_value=0.0, value=current_p if current_p > 0 else 0.0, format="%.2f")
    
    fee = st.number_input("수수료/세금($)", min_value=0.0, value=0.0, format="%.2f")
    ex_rate = st.number_input("적용 환율(₩)", min_value=0.0, value=krw_rate, format="%.2f")
    
    if st.form_submit_button("기록하기"):
        with st.spinner("☁️ 저장 중..."):
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
st.sidebar.header("💰 AI 분석")
investment = st.sidebar.number_input("여유 현금 (원)", min_value=0, value=0, step=10000)
run_ai = st.sidebar.button("분석 실행")

tab1, tab2, tab3 = st.tabs(["📊 자산 현황", "🤖 AI 전략", "📋 기록 장부"])

with tab1:
    total_val = 0
    asset_list = []
    
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            source = "실시간"
            if p == 0: # 백업 로직
                if not df.empty:
                    last_rec = df[(df['Ticker'] == t) & (df['Action'] == 'BUY')]
                    if not last_rec.empty:
                        p = last_rec.iloc[0]['Price']
                        source = "장부"
                if p == 0: p = 100.0
            
            val = q * p * krw_rate
            total_val += val
            asset_list.append({
                "종목": t, "수량": f"{q:,.1f}", "현재가($)": round(p, 2), 
                "평가액(원)": int(val), "데이터": source
            })
            
    profit = total_val - total_invested_krw
    profit_rate = (profit / total_invested_krw * 100) if total_invested_krw > 0 else 0
    
    m1, m2, m3 = st.columns(3)
    m1.metric("현재 환율", f"{krw_rate:,.0f} 원/$")
    m2.metric("순수 투자 원금 (배당차감)", f"{int(total_invested_krw):,.0f} 원")
    m3.metric("총 자산 평가액", f"{int(total_val):,.0f} 원", f"{int(profit):+,.0f} 원 ({profit_rate:.2f}%)")

    if total_div_krw > 0:
        st.caption(f"✨ 지금까지 받은 총 배당금: {int(total_div_krw):,.0f} 원 (원금 회수 효과)")

    if asset_list:
        st.dataframe(pd.DataFrame(asset_list), width='stretch')
        st.bar_chart(pd.DataFrame(asset_list).set_index("종목")["평가액(원)"])

with tab2:
    if run_ai:
        bot = Rebalancer(current_holdings)
        recs, msg = bot.analyze(investment, krw_rate)
        st.subheader("🤖 Aegis AI 리포트")
        if msg: st.info(msg)
        if recs:
            st.write(f"💵 **가용 자금 {investment:,.0f}원** 전략:")
            for r in recs:
                st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수")
        else:
            if not msg: st.balloons()
            st.success("✅ 포트폴리오 비율 완벽함.")

with tab3:
    st.subheader("📋 전체 기록 (배당 포함)")
    st.dataframe(df, width='stretch')
