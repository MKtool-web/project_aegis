import streamlit as st
import pandas as pd
import yfinance as yf
import time
import requests
import altair as alt 
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta

# ==========================================
# 0. 기본 설정
# ==========================================
st.set_page_config(page_title="Project Aegis V10.1", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

def send_test_message():
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": "🔔 [Aegis] 정상 작동 중입니다."})
        st.sidebar.success("✅ 전송 성공!")
    except:
        st.sidebar.error("⚠️ Secrets 설정을 확인하세요.")

# ==========================================
# 1. 데이터 관리 (삭제 및 읽기)
# ==========================================
def delete_data_by_index(worksheet_name, index_to_delete):
    """특정 행 삭제 함수"""
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet=worksheet_name, ttl=0)
        if index_to_delete in df.index:
            df = df.drop(index_to_delete).reset_index(drop=True)
            conn.update(spreadsheet=SHEET_URL, worksheet=worksheet_name, data=df)
            return True
        return False
    except Exception as e:
        st.error(f"삭제 실패: {e}")
        return False

# ==========================================
# 2. 데이터 엔진
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
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=pd.concat([df, new_row], ignore_index=True))
    except: st.error("CashFlow 시트 오류")

# 🔥 [NEW] 과거 추세 역산 함수 (차트용)
def calculate_history(df_stock, df_cash):
    if df_stock.empty and df_cash.empty: return pd.DataFrame()
    
    # 모든 날짜 범위 생성
    dates = []
    if not df_stock.empty: dates.append(pd.to_datetime(df_stock['Date']).min())
    if not df_cash.empty: dates.append(pd.to_datetime(df_cash['Date']).min())
    
    if not dates: return pd.DataFrame()
    
    start_date = min(dates)
    end_date = datetime.today()
    date_range = pd.date_range(start=start_date, end=end_date)
    
    history = []
    
    # 누적 변수 초기화
    cum_cash_krw = 0
    cum_cash_usd = 0
    cum_invested_krw = 0 # 총 투자 원금
    cum_stock_qty = {'SGOV':0, 'SPYM':0, 'QQQM':0, 'GMMF':0}
    
    # 데이터프레임 날짜 정렬
    df_s = df_stock.copy()
    df_s['Date'] = pd.to_datetime(df_s['Date'])
    df_c = df_cash.copy()
    df_c['Date'] = pd.to_datetime(df_c['Date'])

    for d in date_range:
        # 1. 입출금/환전 반영
        day_cash = df_c[df_c['Date'] == d]
        for _, row in day_cash.iterrows():
            if row['Type'] == 'Deposit': 
                cum_cash_krw += row['Amount_KRW']
                cum_invested_krw += row['Amount_KRW'] # 투자 원금 증가
            elif row['Type'] == 'Exchange':
                cum_cash_krw -= row['Amount_KRW']
                cum_cash_usd += row['Amount_USD']
        
        # 2. 주식 거래 반영
        day_stock = df_s[df_s['Date'] == d]
        for _, row in day_stock.iterrows():
            cost = (row['Qty'] * row['Price']) + row['Fee']
            if row['Action'] == 'BUY':
                cum_cash_usd -= cost
                cum_stock_qty[row['Ticker']] += row['Qty']
            elif row['Action'] == 'SELL':
                net_gain = (row['Qty'] * row['Price']) - row['Fee']
                cum_cash_usd += net_gain
                cum_stock_qty[row['Ticker']] -= row['Qty']
            elif row['Action'] == 'DIVIDEND':
                net_div = row['Price'] - row['Fee']
                cum_cash_usd += net_div

        # 3. 그 날의 상태 기록
        # (그래프를 위해 추정 자산 가치도 계산하면 좋지만, 속도상 수량/현금 추이만 기록)
        history.append({
            "Date": d,
            "Total_Invested": cum_invested_krw,
            "Cash_KRW": cum_cash_krw,
            "Cash_USD": cum_cash_usd,
            "Stock_SGOV": cum_stock_qty['SGOV'],
            "Stock_QQQM": cum_stock_qty['QQQM'],
            "Stock_SPYM": cum_stock_qty['SPYM']
        })
        
    return pd.DataFrame(history)

# ==========================================
# 3. 로딩 (기존 Sheet1 사용)
# ==========================================
try:
    # 기존 시트 그대로 읽어옴
    df_stock = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0).sort_values(by="Date", ascending=False).fillna(0)
except: 
    # 만약 진짜 없으면 빈 프레임 (하지만 선생님 시트는 있으니 여기로 안 빠질 겁니다)
    df_stock = pd.DataFrame()

try:
    df_cash = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0).fillna(0)
except: df_cash = pd.DataFrame()

my_wallet = get_wallet_balance()
krw_rate = get_usd_krw()

# ==========================================
# 4. 사이드바 (Action에 따른 동적 UI)
# ==========================================
st.sidebar.header("🏦 자금 관리")
c1, c2 = st.sidebar.columns(2)
c1.metric("🇰🇷 원화", f"{int(my_wallet.get('KRW',0)):,}원")
c2.metric("🇺🇸 달러", f"${my_wallet.get('USD',0):.2f}")

mode = st.sidebar.radio("작업 선택", ["주식 거래", "입금/환전", "🗑️ 데이터 삭제"], horizontal=True)

if mode == "입금/환전":
    st.sidebar.subheader("💱 입금 및 환전")
    # 폼 밖에서 선택 (즉시 반응)
    act_type = st.sidebar.selectbox("종류", ["원화 입금 (Deposit)", "달러 환전 (Exchange)"])
    
    with st.sidebar.form("cash_form"):
        date = st.date_input("날짜", datetime.today())
        label_amt = "입금할 원화 금액" if "Deposit" in act_type else "환전에 쓴 원화 금액"
        amount_krw = st.number_input(label_amt, step=10000)
        
        # 환전일 때만 환율 입력창 등장
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

elif mode == "주식 거래":
    st.sidebar.subheader("📈 주식 매매 & 배당")
    # 🔥 폼 밖으로 뺐습니다 (배당 선택 시 수량 칸 숨기기 위해)
    ticker = st.sidebar.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    action = st.sidebar.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
    
    with st.sidebar.form("stock_form"):
        date = st.date_input("날짜", datetime.today())
        
        # 🔥 배당(DIVIDEND)이면 수량 칸 숨김!
        qty = 1.0
        if action != "DIVIDEND":
            qty = st.number_input("수량 (Qty)", value=1.0, step=0.01)
        
        # 가격 라벨 변경
        price_label = "배당금 총액 ($)" if action == "DIVIDEND" else "체결 단가 ($)"
        cur_p = 0.0
        if action != "DIVIDEND": cur_p = get_current_price(ticker)
        
        price = st.number_input(price_label, value=cur_p if cur_p>0 else 0.0, format="%.2f")
        
        # 수수료 설명
        fee_help = "세금/수수료 (배당은 세후면 0)"
        fee = st.number_input("수수료 ($)", value=0.0, help=fee_help, format="%.2f")
        rate = st.number_input("환율", value=krw_rate, format="%.2f")

        if st.form_submit_button("기록하기"):
            if action == "DIVIDEND": qty = 1.0 # 배당은 수량 1 고정

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
                # 배당 수입 (수수료 차감 후 입금)
                net_div = price - fee
                update_wallet('USD', net_div, "add")
                st.success("💰 배당금 입금")
                time.sleep(1)
                st.rerun()
            else: st.warning("매도 기록만 됩니다.")

elif mode == "🗑️ 데이터 삭제":
    st.sidebar.subheader("⚠️ 데이터 삭제")
    st.sidebar.caption("잘못 입력한 내역을 선택해서 지웁니다.")
    
    target_sheet = st.sidebar.radio("대상", ["주식 거래 내역", "자금 흐름 내역"])
    
    if target_sheet == "주식 거래 내역":
        if not df_stock.empty:
            # 보기 좋게 포맷팅
            del_idx = st.sidebar.selectbox("삭제할 항목 선택", df_stock.index, 
                                           format_func=lambda x: f"[{df_stock.at[x,'Date']}] {df_stock.at[x,'Ticker']} {df_stock.at[x,'Action']} ({df_stock.at[x,'Price']}$)")
            if st.sidebar.button("선택 항목 삭제"):
                if delete_data_by_index("Sheet1", del_idx):
                    st.success("삭제 완료!")
                    time.sleep(1)
                    st.rerun()
        else: st.sidebar.info("기록이 없습니다.")
    else:
        if not df_cash.empty:
            del_idx = st.sidebar.selectbox("삭제할 항목 선택", df_cash.index, 
                                           format_func=lambda x: f"[{df_cash.at[x,'Date']}] {df_cash.at[x,'Type']} ({int(df_cash.at[x,'Amount_KRW']):,}원)")
            if st.sidebar.button("선택 항목 삭제"):
                if delete_data_by_index("CashFlow", del_idx):
                    st.success("삭제 완료!")
                    time.sleep(1)
                    st.rerun()

st.sidebar.markdown("---")
if st.sidebar.button("🔔 텔레그램 테스트"): send_test_message()

# ==========================================
# 5. 메인 대시보드
# ==========================================
# 자산 계산
current_holdings = {}
total_stock_val_krw = 0
asset_details = []

if not df_stock.empty:
    current_holdings = df_stock.groupby("Ticker").apply(lambda x: x.loc[x['Action']=='BUY','Qty'].sum() - x.loc[x['Action']=='SELL','Qty'].sum()).to_dict()
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            if p == 0: p = 100.0
            val_krw = q * p * krw_rate
            total_stock_val_krw += val_krw
            asset_details.append({"종목": t, "가치": val_krw, "수량": q})

total_deposit = df_cash[df_cash['Type']=='Deposit']['Amount_KRW'].sum() if not df_cash.empty else 0
total_asset = total_stock_val_krw + my_wallet.get('KRW',0) + (my_wallet.get('USD',0) * krw_rate)
net_profit = total_asset - total_deposit
profit_rate = (net_profit / total_deposit * 100) if total_deposit > 0 else 0

tab1, tab2, tab3 = st.tabs(["📊 자산 & 포트폴리오", "📈 추세 그래프", "📋 상세 기록"])

with tab1:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 자산", f"{int(total_asset):,}원")
    col2.metric("총 투자원금", f"{int(total_deposit):,}원")
    col3.metric("예상 수익", f"{int(net_profit):+,.0f}원", f"{profit_rate:.2f}%")
    col4.metric("현재 환율", f"{krw_rate:,.0f}원")
    st.markdown("---")

    c_chart1, c_chart2 = st.columns(2)
    with c_chart1:
        st.subheader("🍩 자산 구성")
        if total_asset > 0:
            asset_df = pd.DataFrame([
                {"Type": "주식", "Value": total_stock_val_krw},
                {"Type": "현금(KRW)", "Value": my_wallet.get('KRW',0)},
                {"Type": "현금(USD)", "Value": my_wallet.get('USD',0) * krw_rate}
            ])
            base = alt.Chart(asset_df).encode(theta=alt.Theta("Value", stack=True))
            pie = base.mark_arc(outerRadius=120, innerRadius=60).encode(
                color=alt.Color("Type"), order=alt.Order("Value", sort="descending"), tooltip=["Type", "Value"]
            )
            text = base.mark_text(radius=140).encode(text=alt.Text("Value", format=",.0f"), order=alt.Order("Value", sort="descending"), color=alt.value("black"))
            st.altair_chart(pie + text, use_container_width=True)
        else: st.info("자산이 없습니다.")

    with c_chart2:
        st.subheader("🥧 종목별 비중")
        if asset_details:
            stock_df = pd.DataFrame(asset_details)
            base2 = alt.Chart(stock_df).encode(theta=alt.Theta("가치", stack=True))
            pie2 = base2.mark_arc(outerRadius=120).encode(color=alt.Color("종목"), tooltip=["종목", "가치", "수량"])
            st.altair_chart(pie2, use_container_width=True)
        else: st.info("보유 주식이 없습니다.")

with tab2:
    st.subheader("📈 자산 변화 추이")
    history_df = calculate_history(df_stock, df_cash)
    
    if not history_df.empty:
        chart_opt = st.radio("그래프 선택", ["보유 수량", "현금 잔고", "총 투자원금"], horizontal=True)
        
        if chart_opt == "보유 수량":
            # Wide to Long 변환
            long_df = history_df.melt('Date', value_vars=['Stock_SGOV', 'Stock_QQQM', 'Stock_SPYM'], var_name='Ticker', value_name='Qty')
            c = alt.Chart(long_df).mark_line(point=True).encode(
                x='Date', y='Qty', color='Ticker', tooltip=['Date', 'Ticker', 'Qty']
            ).interactive()
            st.altair_chart(c, use_container_width=True)
            
        elif chart_opt == "현금 잔고":
            long_df = history_df.melt('Date', value_vars=['Cash_KRW', 'Cash_USD'], var_name='Currency', value_name='Amount')
            c = alt.Chart(long_df).mark_line(point=True).encode(
                x='Date', y='Amount', color='Currency', tooltip=['Date', 'Currency', 'Amount']
            ).interactive()
            st.altair_chart(c, use_container_width=True)
            
        elif chart_opt == "총 투자원금":
            c = alt.Chart(history_df).mark_line(point=True, color='red').encode(
                x='Date', y='Total_Invested', tooltip=['Date', 'Total_Invested']
            ).interactive()
            st.altair_chart(c, use_container_width=True)
    else:
        st.info("아직 추세를 그릴 데이터가 부족합니다.")

with tab3:
    st.subheader("📝 주식 거래 내역")
    st.dataframe(df_stock, use_container_width=True)
    st.markdown("---")
    st.subheader("📝 입출금/환전 내역")
    st.dataframe(df_cash, use_container_width=True)
