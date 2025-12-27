import streamlit as st
import pandas as pd
import yfinance as yf
import time
import requests
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

st.set_page_config(page_title="Project Aegis V7.0", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 텔레그램 테스트
def send_test_message():
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": "🔔 [테스트] Aegis 정상 작동 중!"})
        st.sidebar.success("✅ 전송 성공!")
    except:
        st.sidebar.error("⚠️ Secrets 설정이 필요합니다.")

# ==========================================
# 1. 데이터 엔진
# ==========================================
@st.cache_data(ttl=300) 
def get_current_price(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="1d")
        if not hist.empty: return float(hist['Close'].iloc[-1])
        return 0.0
    except: return 0.0

@st.cache_data(ttl=300)
def get_usd_krw():
    try:
        return float(yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1])
    except: return 1450.0

def get_wallet_balance():
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="Wallet", usecols=[0, 1], ttl=0)
        return dict(zip(df['Currency'], df['Amount']))
    except: return {'KRW': 0, 'USD': 0}

def update_wallet(currency, amount, operation="add"):
    df = conn.read(spreadsheet=SHEET_URL, worksheet="Wallet", usecols=[0, 1], ttl=0)
    idx = df.index[df['Currency'] == currency].tolist()
    if not idx:
        df = pd.concat([df, pd.DataFrame([{'Currency': currency, 'Amount': 0}])], ignore_index=True)
        idx = [len(df) - 1]
    
    curr = float(df.at[idx[0], 'Amount'])
    df.at[idx[0], 'Amount'] = curr + amount if operation == "add" else curr - amount
    conn.update(spreadsheet=SHEET_URL, worksheet="Wallet", data=df)

# 🔥 [NEW] 현금 흐름 기록 함수
def log_cash_flow(date, type_, krw, usd, rate):
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", usecols=[0,1,2,3,4], ttl=0)
        new_row = pd.DataFrame([{"Date": str(date), "Type": type_, "Amount_KRW": krw, "Amount_USD": usd, "Ex_Rate": rate}])
        updated = pd.concat([df, new_row], ignore_index=True)
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=updated)
    except:
        st.error("⚠️ 'CashFlow' 시트를 찾을 수 없습니다.")

# ==========================================
# 2. 메인 로직
# ==========================================
st.title("🛡️ Project Aegis V7.0 (Total Care)")

# 데이터 로딩
try:
    df_stock = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0).sort_values(by="Date", ascending=False).fillna(0)
except: df_stock = pd.DataFrame()

try:
    df_cash = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0).fillna(0)
except: df_cash = pd.DataFrame()

my_wallet = get_wallet_balance()
krw_rate = get_usd_krw()

# 📊 통계 계산 (선생님이 원하신 기능)
total_deposit = 0
total_exchange_krw = 0
total_exchange_usd = 0
avg_exchange_rate = 0

if not df_cash.empty:
    # 1. 총 입금액 (순수 원화 투입)
    total_deposit = df_cash[df_cash['Type'] == 'Deposit']['Amount_KRW'].sum()
    
    # 2. 총 환전액 및 평균 환율
    exchanges = df_cash[df_cash['Type'] == 'Exchange']
    if not exchanges.empty:
        total_exchange_krw = exchanges['Amount_KRW'].sum()
        total_exchange_usd = exchanges['Amount_USD'].sum()
        # 가중 평균 환율 = 총 들어간 원화 / 총 받은 달러
        avg_exchange_rate = total_exchange_krw / total_exchange_usd if total_exchange_usd > 0 else 0

# 주식 보유량
current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}
if not df_stock.empty:
    current_holdings = df_stock.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()

# ==========================================
# 3. 사이드바 (입금/환전 개선)
# ==========================================
st.sidebar.header("🏦 자금 관리")
col1, col2 = st.sidebar.columns(2)
col1.metric("🇰🇷 잔고", f"{int(my_wallet.get('KRW',0)):,}원")
col2.metric("🇺🇸 잔고", f"${my_wallet.get('USD',0):.2f}")

mode = st.sidebar.radio("메뉴", ["주식 거래", "입금/환전"], horizontal=True)

with st.sidebar.form("input"):
    date = st.date_input("날짜", datetime.today())
    
    if mode == "입금/환전":
        act_type = st.selectbox("종류", ["원화 입금 (Deposit)", "달러 환전 (Exchange)"])
        
        # 🔥 UI 구분: 입금이면 '입금액', 환전이면 '환전할 원화'
        label_amt = "입금할 원화 금액" if "Deposit" in act_type else "환전에 쓴 원화 금액"
        amount_krw = st.number_input(label_amt, step=10000)
        
        ex_rate_in = 0.0
        if "Exchange" in act_type:
            ex_rate_in = st.number_input("적용 환율", value=krw_rate, format="%.2f")
            if ex_rate_in > 0:
                st.caption(f"예상 획득 달러: ${amount_krw / ex_rate_in:.2f}")

        if st.form_submit_button("실행"):
            if "Deposit" in act_type:
                update_wallet('KRW', amount_krw, "add")
                log_cash_flow(date, "Deposit", amount_krw, 0, 0)
                st.success("💰 입금 완료!")
            else:
                if my_wallet.get('KRW', 0) >= amount_krw:
                    usd_out = amount_krw / ex_rate_in
                    update_wallet('KRW', amount_krw, "subtract")
                    update_wallet('USD', usd_out, "add")
                    log_cash_flow(date, "Exchange", amount_krw, usd_out, ex_rate_in)
                    st.success("💱 환전 완료!")
                else: st.error("❌ 잔고 부족")
            time.sleep(1)
            st.rerun()

    else: # 주식 거래
        ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
        action = st.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
        qty = st.number_input("수량", value=1.0)
        
        cur_p = 0.0
        if action != "DIVIDEND": cur_p = get_current_price(ticker)
        price = st.number_input("단가/배당금($)", value=cur_p if cur_p>0 else 0.0)
        fee = st.number_input("수수료($)", value=0.0)
        rate = st.number_input("환율", value=krw_rate)

        if st.form_submit_button("기록"):
            cost = (qty * price) + fee
            if action == "BUY":
                if my_wallet.get('USD', 0) >= cost:
                    new = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": rate, "Fee": fee}])
                    conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=pd.concat([df_stock, new], ignore_index=True))
                    update_wallet('USD', cost, "subtract")
                    st.success("매수 완료")
                    time.sleep(1)
                    st.rerun()
                else: st.error("달러 부족")
            elif action == "DIVIDEND":
                new = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": rate, "Fee": fee}])
                conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=pd.concat([df_stock, new], ignore_index=True))
                update_wallet('USD', price, "add")
                st.success("배당금 수령")
                time.sleep(1)
                st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🔔 텔레그램 테스트"): send_test_message()

# ==========================================
# 4. 대시보드 (통계 강화)
# ==========================================
tab1, tab2 = st.tabs(["📊 자산 & 통계", "📋 기록 장부"])

with tab1:
    # 1. 핵심 요약 카드
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 입금액(KRW)", f"{int(total_deposit):,}원")
    c2.metric("총 환전액(KRW)", f"{int(total_exchange_krw):,}원")
    c3.metric("내 평균 환율", f"{avg_exchange_rate:,.1f}원/$")
    c4.metric("현재 환율", f"{krw_rate:,.1f}원/$", f"{krw_rate - avg_exchange_rate:.1f}원")

    st.markdown("---")
    
    # 2. 자산 상세
    total_val = 0
    asset_list = []
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            if p == 0: p = 100.0
            val = q * p * krw_rate
            total_val += val
            asset_list.append({"종목": t, "수량": q, "현재가($)": round(p,2), "평가액(원)": int(val)})
    
    m1, m2 = st.columns(2)
    m1.metric("보유 현금 합계", f"{int(my_wallet.get('KRW',0) + my_wallet.get('USD',0)*krw_rate):,}원")
    m2.metric("총 자산 (현금+주식)", f"{int(total_val + my_wallet.get('KRW',0) + my_wallet.get('USD',0)*krw_rate):,}원")
    
    if asset_list: st.dataframe(pd.DataFrame(asset_list), width='stretch')

with tab2:
    st.subheader("주식 거래 내역")
    st.dataframe(df_stock, width='stretch')
    st.markdown("---")
    st.subheader("자금 흐름 (입금/환전)")
    st.dataframe(df_cash, width='stretch')
