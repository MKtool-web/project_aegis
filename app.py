import streamlit as st
import pandas as pd
import yfinance as yf
import time
import requests # 텔레그램용 추가
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

st.set_page_config(page_title="Project Aegis", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# ==========================================
# 0. 텔레그램 테스트 기능
# ==========================================
# 🚨 Streamlit Secrets에 토큰이 있어야 작동합니다.
def send_test_message():
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        msg = "🔔 [테스트] Project Aegis 시스템이 정상 작동 중입니다!"
        data = {"chat_id": chat_id, "text": msg}
        res = requests.post(url, data=data)
        if res.status_code == 200:
            st.sidebar.success("✅ 전송 성공! 텔레그램을 확인하세요.")
        else:
            st.sidebar.error("❌ 전송 실패. 토큰을 확인하세요.")
    except Exception as e:
        st.sidebar.error(f"⚠️ 설정 오류: Secrets에 토큰이 없습니다. ({e})")

# ==========================================
# 1. 핵심 엔진
# ==========================================
@st.cache_data(ttl=300) 
def get_current_price(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1d")
        if not hist.empty: return float(hist['Close'].iloc[-1])
        return 0.0
    except: return 0.0

@st.cache_data(ttl=300)
def get_usd_krw():
    try:
        exchange = yf.Ticker("KRW=X")
        price = exchange.history(period="1d")['Close'].iloc[-1]
        return float(price)
    except: return 1450.0

def get_wallet_balance():
    try:
        df_wallet = conn.read(spreadsheet=SHEET_URL, worksheet="Wallet", usecols=[0, 1], ttl=0)
        return dict(zip(df_wallet['Currency'], df_wallet['Amount']))
    except: return {'KRW': 0, 'USD': 0}

def update_wallet_balance(currency, amount, operation="add"):
    df_wallet = conn.read(spreadsheet=SHEET_URL, worksheet="Wallet", usecols=[0, 1], ttl=0)
    idx = df_wallet.index[df_wallet['Currency'] == currency].tolist()
    if not idx:
        new_row = pd.DataFrame([{'Currency': currency, 'Amount': 0}])
        df_wallet = pd.concat([df_wallet, new_row], ignore_index=True)
        idx = [len(df_wallet) - 1]
    
    current_amt = float(df_wallet.at[idx[0], 'Amount'])
    new_amt = current_amt + amount if operation == "add" else current_amt - amount
    df_wallet.at[idx[0], 'Amount'] = new_amt
    conn.update(spreadsheet=SHEET_URL, worksheet="Wallet", data=df_wallet)

# ==========================================
# 2. AI 전략
# ==========================================
class Rebalancer:
    def __init__(self, current_holdings, wallet_balance):
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0} 
        self.holdings = current_holdings
        self.wallet = wallet_balance

    def analyze(self, current_rate):
        investment_usd = self.wallet.get('USD', 0) + (self.wallet.get('KRW', 0) / current_rate)
        portfolio = {}
        total_stock_value = 0
        
        for ticker, qty in self.holdings.items():
            price = get_current_price(ticker)
            if price == 0: price = 100.0
            val = qty * price
            portfolio[ticker] = {'qty': qty, 'price': price, 'value': val}
            total_stock_value += val
            
        total_asset_usd = total_stock_value + investment_usd
        recommendations = []
        msg = ""
        
        if current_rate > 1460:
            msg = f"⚠️ [고환율] 1,460원 돌파. 원화 유지 추천."
        elif current_rate < 1380:
            msg = f"✅ [환전 기회] 1,380원 아래! 환전 고려."

        my_usd = self.wallet.get('USD', 0)
        if my_usd > 10:
            for ticker, target_ratio in self.TARGET_RATIO.items():
                if target_ratio == 0: continue
                target_amt = total_asset_usd * target_ratio
                current_amt = portfolio.get(ticker, {'value': 0})['value']
                
                if current_amt < target_amt:
                    shortfall = target_amt - current_amt
                    price = portfolio.get(ticker, {'price': 100})['price']
                    buy_qty = int(min(shortfall, my_usd) // price)
                    if buy_qty > 0:
                        cost = buy_qty * price
                        recommendations.append({'ticker': ticker, 'qty': buy_qty, 'cost': cost})
                        my_usd -= cost
        return recommendations, msg

# ==========================================
# 3. 메인 로직 & UI
# ==========================================
st.title("🛡️ Project Aegis V6.1")

try:
    data = conn.read(spreadsheet=SHEET_URL, usecols=[0, 1, 2, 3, 4, 5, 6], ttl=0)
    df = pd.DataFrame(data)
    if not df.empty: df = df.sort_values(by="Date", ascending=False).fillna(0)
except: df = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"])

my_wallet = get_wallet_balance()

if not df.empty:
    current_holdings = df.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
    buys = df[df['Action']=='BUY']
    sells = df[df['Action']=='SELL']
    divs = df[df['Action']=='DIVIDEND']
    total_bought_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
    total_sold_krw = ((sells['Qty'] * sells['Price'] - sells['Fee']) * sells['Exchange_Rate']).sum()
    total_div_krw = (divs['Price'] * divs['Exchange_Rate']).sum()
    total_invested_krw = total_bought_krw - total_sold_krw - total_div_krw
else: current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}

krw_rate = get_usd_krw()

# [사이드바] 지갑 및 거래
st.sidebar.header("🏦 내 지갑")
col_w1, col_w2 = st.sidebar.columns(2)
col_w1.metric("🇰🇷 원화", f"{int(my_wallet.get('KRW',0)):,}원")
col_w2.metric("🇺🇸 달러", f"${my_wallet.get('USD',0):.2f}")

mode = st.sidebar.radio("작업 선택", ["주식 거래", "입금/환전"], horizontal=True)

with st.sidebar.form("action_form"):
    date = st.date_input("날짜", datetime.today())
    
    if mode == "입금/환전":
        act_type = st.selectbox("종류", ["원화 입금 (Deposit)", "달러 환전 (Exchange)"])
        amount = st.number_input("금액 (원화)", min_value=0, step=10000)
        
        # 🔥 UI 개선: 환전일 때만 환율 입력창 보여주기
        ex_rate_in = krw_rate
        if act_type == "달러 환전 (Exchange)":
            ex_rate_in = st.number_input("적용 환율", value=krw_rate)
        
        if st.form_submit_button("실행"):
            if act_type == "원화 입금 (Deposit)":
                update_wallet_balance('KRW', amount, "add")
                st.success(f"💰 {amount:,}원 입금 완료!")
            else:
                if my_wallet.get('KRW', 0) >= amount:
                    usd_got = amount / ex_rate_in
                    update_wallet_balance('KRW', amount, "subtract")
                    update_wallet_balance('USD', usd_got, "add")
                    st.success(f"💱 {amount:,}원 -> ${usd_got:.2f} 환전 완료!")
                else: st.error("❌ 잔고 부족!")
            time.sleep(1)
            st.rerun()
            
    else: # 주식 거래
        ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
        action = st.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
        qty = st.number_input("수량", min_value=0.0, value=1.0, step=0.01)
        
        cur_p = 0.0
        if action != "DIVIDEND": cur_p = get_current_price(ticker)
        price = st.number_input("단가/배당금($)", value=cur_p if cur_p > 0 else 0.0, format="%.2f")
        fee = st.number_input("수수료($)", value=0.0, format="%.2f")
        ex_rate = st.number_input("환율", value=krw_rate)
        
        if st.form_submit_button("기록하기"):
            total_cost_usd = (qty * price) + fee
            if action == "BUY":
                if my_wallet.get('USD', 0) >= total_cost_usd:
                    new_row = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": ex_rate, "Fee": fee}])
                    updated_df = pd.concat([df, new_row], ignore_index=True)
                    conn.update(spreadsheet=SHEET_URL, data=updated_df)
                    update_wallet_balance('USD', total_cost_usd, "subtract")
                    st.success("✅ 매수 완료!")
                    time.sleep(1)
                    st.cache_data.clear()
                    st.rerun()
                else: st.error(f"❌ 달러 부족! (필요: ${total_cost_usd:.2f})")
            elif action == "DIVIDEND":
                new_row = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": ex_rate, "Fee": fee}])
                updated_df = pd.concat([df, new_row], ignore_index=True)
                conn.update(spreadsheet=SHEET_URL, data=updated_df)
                update_wallet_balance('USD', price, "add")
                st.success("💰 배당금 입금!")
                time.sleep(1)
                st.cache_data.clear()
                st.rerun()
            else: st.warning("매도 기능은 기록만 됩니다.")

st.sidebar.markdown("---")
if st.sidebar.button("🔔 텔레그램 테스트 발송"):
    send_test_message()

run_ai = st.sidebar.button("🤖 AI 자산 분석")

tab1, tab2, tab3 = st.tabs(["📊 자산 현황", "🤖 AI 전략", "📋 기록 장부"])

with tab1:
    total_val = 0
    asset_list = []
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            if p == 0: p = 100.0
            val = q * p * krw_rate
            total_val += val
            asset_list.append({"종목": t, "수량": f"{q:,.1f}", "현재가($)": round(p, 2), "평가액(원)": int(val)})
            
    profit = total_val - total_invested_krw
    profit_rate = (profit / total_invested_krw * 100) if total_invested_krw > 0 else 0
    
    m1, m2, m3 = st.columns(3)
    m1.metric("보유 현금", f"{int(my_wallet.get('KRW',0) + my_wallet.get('USD',0)*krw_rate):,} 원")
    m2.metric("주식 평가액", f"{int(total_val):,} 원")
    m3.metric("총 자산", f"{int(total_val + my_wallet.get('KRW',0) + my_wallet.get('USD',0)*krw_rate):,} 원", f"{profit_rate:.2f}%")

    if asset_list: st.dataframe(pd.DataFrame(asset_list), width='stretch')

with tab2:
    if run_ai:
        bot = Rebalancer(current_holdings, my_wallet)
        recs, msg = bot.analyze(krw_rate)
        st.subheader("🤖 AI 전략 보고서")
        if msg: st.info(msg)
        if recs:
            st.write(f"💡 **보유 달러(${my_wallet.get('USD',0):.2f}) 활용 전략:**")
            for r in recs: st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수")
        else:
            if not msg: st.balloons()
            st.success("✅ 현재 포트폴리오 유지")

with tab3:
    st.subheader("📋 전체 기록")
    st.dataframe(df, width='stretch')
