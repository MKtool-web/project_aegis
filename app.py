import streamlit as st
import pandas as pd
import yfinance as yf
import time
import requests
import altair as alt 
from streamlit_gsheets import GSheetsConnection
from datetime import datetime

st.set_page_config(page_title="Project Aegis V9.0", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# ==========================================
# 0. 관리자 기능 (DB 초기화)
# ==========================================
def reset_all_data():
    try:
        # 1. 주식 장부 초기화
        empty_stock = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"])
        conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=empty_stock)
        
        # 2. 지갑 초기화 (0원으로 리셋)
        init_wallet = pd.DataFrame([
            {"Currency": "KRW", "Amount": 0},
            {"Currency": "USD", "Amount": 0}
        ])
        conn.update(spreadsheet=SHEET_URL, worksheet="Wallet", data=init_wallet)
        
        # 3. 현금 흐름 초기화
        empty_cash = pd.DataFrame(columns=["Date", "Type", "Amount_KRW", "Amount_USD", "Ex_Rate"])
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=empty_cash)
        
        return True
    except Exception as e:
        st.error(f"초기화 실패: {e}")
        return False

# 텔레그램 테스트
def send_test_message():
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": "🔔 [Aegis] 시스템 정상 가동 중입니다."})
        st.sidebar.success("✅ 전송 성공!")
    except:
        st.sidebar.error("⚠️ Secrets 설정 확인 필요")

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

def log_cash_flow(date, type_, krw, usd, rate):
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0)
        new_row = pd.DataFrame([{"Date": str(date), "Type": type_, "Amount_KRW": krw, "Amount_USD": usd, "Ex_Rate": rate}])
        updated = pd.concat([df, new_row], ignore_index=True)
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=updated)
    except: st.error("⚠️ 'CashFlow' 시트가 없습니다.")

# ==========================================
# 2. 메인 로직
# ==========================================
st.title("🛡️ Project Aegis V9.0 (Reboot)")

# DB 읽기
try:
    df_stock = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0).sort_values(by="Date", ascending=False).fillna(0)
except: df_stock = pd.DataFrame()

try:
    df_cash = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0).fillna(0)
except: df_cash = pd.DataFrame()

my_wallet = get_wallet_balance()
krw_rate = get_usd_krw()

# 통계 계산
total_deposit = 0
total_exchange_krw = 0
total_exchange_usd = 0
avg_exchange_rate = 0

if not df_cash.empty:
    total_deposit = df_cash[df_cash['Type'] == 'Deposit']['Amount_KRW'].sum()
    exchanges = df_cash[df_cash['Type'] == 'Exchange']
    if not exchanges.empty:
        total_exchange_krw = exchanges['Amount_KRW'].sum()
        total_exchange_usd = exchanges['Amount_USD'].sum()
        avg_exchange_rate = total_exchange_krw / total_exchange_usd if total_exchange_usd > 0 else 0

current_holdings = {}
total_stock_val_krw = 0
asset_details = []

if not df_stock.empty:
    current_holdings = df_stock.groupby("Ticker").apply(
        lambda x: x.loc[x['Action']=='BUY', 'Qty'].sum() - x.loc[x['Action']=='SELL', 'Qty'].sum()
    ).to_dict()
    
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            if p == 0: p = 100.0
            val_krw = q * p * krw_rate
            total_stock_val_krw += val_krw
            asset_details.append({"종목": t, "가치": val_krw, "비중": 0})

cash_krw = my_wallet.get('KRW', 0)
cash_usd_to_krw = my_wallet.get('USD', 0) * krw_rate
total_asset = total_stock_val_krw + cash_krw + cash_usd_to_krw

# ==========================================
# 3. 사이드바
# ==========================================
st.sidebar.header("🏦 자금 관리")
c1, c2 = st.sidebar.columns(2)
c1.metric("🇰🇷 원화", f"{int(cash_krw):,}원")
c2.metric("🇺🇸 달러", f"${my_wallet.get('USD',0):.2f}")

mode = st.sidebar.radio("메뉴", ["주식 거래", "입금/환전"], horizontal=True)

# 폼 밖에서 선택 (즉시 반응)
if mode == "입금/환전":
    act_type = st.selectbox("종류", ["원화 입금 (Deposit)", "달러 환전 (Exchange)"])
else:
    act_type = None

with st.sidebar.form("input"):
    date = st.date_input("날짜", datetime.today())
    
    if mode == "입금/환전":
        label_amt = "입금할 원화 금액" if "Deposit" in act_type else "환전에 쓴 원화 금액"
        amount_krw = st.number_input(label_amt, step=10000)
        
        ex_rate_in = krw_rate
        if "Exchange" in act_type:
            ex_rate_in = st.number_input("적용 환율", value=krw_rate, format="%.2f")
            if ex_rate_in > 0:
                st.caption(f"💵 예상 획득: ${amount_krw / ex_rate_in:.2f}")

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
        
        # 🔥 UI 개선: 선택하면 즉시 폼이 갱신되진 않지만, 실행 로직에서 처리
        # (Streamlit 구조상 폼 안에서 동적 UI는 제한적이나, 
        #  action을 폼 밖으로 뺄 수도 있음. 여기선 폼 안에 두고 '수량' 칸을 배당일 때 무시하는 방식 사용)
        action = st.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
        
        # 🔥 배당일 때는 수량 입력 무의미 -> 기본값 1.0 처리
        # (사용자에게 혼란을 줄이지 않기 위해 메시지 표시)
        if action == "DIVIDEND":
            st.info("💡 배당금은 '수량'을 입력하지 않습니다. 아래 금액만 적어주세요.")
            qty = 1.0
        else:
            qty = st.number_input("수량", value=1.0)
        
        # 라벨 변경 (st.empty 사용 불가한 폼 내부라, 범용 라벨 사용)
        label_price = "체결 단가 / 배당금 총액 ($)"
        
        cur_p = 0.0
        if action != "DIVIDEND": cur_p = get_current_price(ticker)
        price = st.number_input(label_price, value=cur_p if cur_p>0 else 0.0)
        
        fee_label = "세금/수수료 ($) - 배당은 세후면 0"
        fee = st.number_input(fee_label, value=0.0)
        rate = st.number_input("환율", value=krw_rate)

        if st.form_submit_button("기록"):
            if action == "DIVIDEND":
                # 배당은 수량 1개로 처리하고, 금액을 그대로 사용
                qty = 1.0
                
            cost = (qty * price) + fee
            
            if action == "BUY":
                if my_wallet.get('USD', 0) >= cost:
                    new = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": rate, "Fee": fee}])
                    conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=pd.concat([df_stock, new], ignore_index=True))
                    update_wallet('USD', cost, "subtract")
                    st.success("✅ 매수 완료")
                    time.sleep(1)
                    st.rerun()
                else: st.error("❌ 달러 부족")
            elif action == "DIVIDEND":
                new = pd.DataFrame([{"Date": str(date), "Ticker": ticker, "Action": action, "Qty": 1.0, "Price": price, "Exchange_Rate": rate, "Fee": fee}])
                conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=pd.concat([df_stock, new], ignore_index=True))
                
                # 배당금은 'Price' 자체가 입금액 (수수료 있으면 차감)
                net_income = price - fee 
                update_wallet('USD', net_income, "add")
                st.success("💰 배당금 입금")
                time.sleep(1)
                st.rerun()
            else: st.warning("매도 기록됨")

st.sidebar.markdown("---")

# 🔥 관리자 메뉴 (DB 초기화)
with st.sidebar.expander("⚠️ 관리자 메뉴"):
    st.warning("주의: 모든 데이터를 삭제하고 0원부터 시작합니다.")
    if st.button("🚨 모든 데이터 삭제 (Reset)"):
        if reset_all_data():
            st.cache_data.clear()
            st.success("데이터가 초기화되었습니다!")
            time.sleep(2)
            st.rerun()

if st.sidebar.button("🔔 텔레그램 테스트"): send_test_message()

# ==========================================
# 4. 대시보드
# ==========================================
tab1, tab2 = st.tabs(["📊 자산 & 차트", "📋 기록 장부"])

with tab1:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 자산", f"{int(total_asset):,}원")
    
    diff_rate = krw_rate - avg_exchange_rate
    col2.metric("내 평단 / 현재", f"{avg_exchange_rate:,.0f}원 / {krw_rate:,.0f}원", f"{diff_rate:+.0f}원")
    col3.metric("총 환전액", f"{int(total_exchange_krw):,}원")
    
    net_profit = total_asset - total_deposit
    profit_rate = (net_profit / total_deposit * 100) if total_deposit > 0 else 0
    col4.metric("추정 수익", f"{int(net_profit):+,.0f}원", f"{profit_rate:.2f}%")

    st.markdown("---")

    c_chart1, c_chart2 = st.columns(2)
    with c_chart1:
        st.subheader("🍩 자산 구성")
        if total_asset > 0:
            asset_df = pd.DataFrame([
                {"Type": "주식", "Value": total_stock_val_krw},
                {"Type": "현금(KRW)", "Value": cash_krw},
                {"Type": "현금(USD)", "Value": cash_usd_to_krw}
            ])
            base = alt.Chart(asset_df).encode(theta=alt.Theta("Value", stack=True))
            pie = base.mark_arc(outerRadius=120, innerRadius=60).encode(
                color=alt.Color("Type"), order=alt.Order("Value", sort="descending"), tooltip=["Type", "Value"]
            )
            text = base.mark_text(radius=140).encode(
                text=alt.Text("Value", format=",.0f"), order=alt.Order("Value", sort="descending"), color=alt.value("black")  
            )
            st.altair_chart(pie + text, use_container_width=True)
        else:
            st.info("자산 데이터가 없습니다.")

    with c_chart2:
        st.subheader("🥧 종목별 비중")
        if asset_details:
            stock_df = pd.DataFrame(asset_details)
            base2 = alt.Chart(stock_df).encode(theta=alt.Theta("가치", stack=True))
            pie2 = base2.mark_arc(outerRadius=120).encode(
                color=alt.Color("종목"), tooltip=["종목", "가치"]
            )
            st.altair_chart(pie2, use_container_width=True)
        else: st.info("보유 주식이 없습니다.")

    st.subheader("📜 보유 자산 상세")
    if asset_details: st.dataframe(pd.DataFrame(asset_details), width='stretch')

with tab2:
    st.subheader("주식 거래 내역")
    st.dataframe(df_stock, width='stretch')
    st.markdown("---")
    st.subheader("자금 흐름 (입금/환전)")
    st.dataframe(df_cash, width='stretch')
