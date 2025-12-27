import streamlit as st
import pandas as pd
import yfinance as yf
import time
import requests
import altair as alt 
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta

# ==========================================
# 0. 기본 설정 & 자가 진단
# ==========================================
st.set_page_config(page_title="Project Aegis V11.1 (Self-Healing)", layout="wide")
conn = st.connection("gsheets", type=GSheetsConnection)
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 🔥 [NEW] 헤더 자동 복구 함수
def check_and_fix_headers():
    """시트의 헤더(제목)가 깨졌는지 확인하고 복구합니다."""
    try:
        # 1. Sheet1 (주식) 점검
        try:
            df_stock = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0)
            expected_cols = ["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"]
            # 컬럼이 하나라도 없으면 초기화 (데이터 보호를 위해 기존 데이터가 있으면 헤더만 끼워넣어야 하지만, 
            # 구조가 깨진 경우 리셋이 안전함. 여기서는 헤더가 아예 없는 경우 리셋)
            if not all(col in df_stock.columns for col in expected_cols):
                st.toast("⚠️ Sheet1 헤더 복구 중...")
                empty_stock = pd.DataFrame(columns=expected_cols)
                conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=empty_stock)
        except:
            # 시트가 아예 없거나 읽기 에러 시 재생성
            st.toast("⚠️ Sheet1 재생성 중...")
            empty_stock = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"])
            conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=empty_stock)

        # 2. CashFlow (현금) 점검
        try:
            df_cash = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0)
            expected_cols_c = ["Date", "Type", "Amount_KRW", "Amount_USD", "Ex_Rate"]
            if not all(col in df_cash.columns for col in expected_cols_c):
                st.toast("⚠️ CashFlow 헤더 복구 중...")
                empty_cash = pd.DataFrame(columns=expected_cols_c)
                conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=empty_cash)
        except:
            st.toast("⚠️ CashFlow 재생성 중...")
            empty_cash = pd.DataFrame(columns=["Date", "Type", "Amount_KRW", "Amount_USD", "Ex_Rate"])
            conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=empty_cash)
            
    except Exception as e:
        st.error(f"복구 실패: {e}")

# 앱 시작 시 자동 점검 실행
check_and_fix_headers()

def send_test_message():
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": "🔔 [Aegis] 시스템 정상 가동 중입니다."})
        st.sidebar.success("✅ 전송 성공!")
    except:
        st.sidebar.error("⚠️ Secrets 설정을 확인하세요.")

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

def calculate_wallet_balance(df_stock, df_cash):
    # 데이터프레임이 비어있거나 필수 컬럼이 없으면 0 리턴 (에러 방지)
    if df_cash.empty or 'Type' not in df_cash.columns:
        return {'KRW': 0, 'USD': 0}
        
    krw_deposit = df_cash[df_cash['Type'] == 'Deposit']['Amount_KRW'].sum()
    krw_used = df_cash[df_cash['Type'] == 'Exchange']['Amount_KRW'].sum()
    current_krw = krw_deposit - krw_used

    usd_gained = df_cash[df_cash['Type'] == 'Exchange']['Amount_USD'].sum()
    
    usd_spent = 0
    usd_earned = 0
    
    if not df_stock.empty and 'Action' in df_stock.columns:
        buys = df_stock[df_stock['Action'] == 'BUY']
        if not buys.empty:
            usd_spent = ((buys['Qty'] * buys['Price']) + buys['Fee']).sum()
        
        sells = df_stock[df_stock['Action'] == 'SELL']
        if not sells.empty:
            usd_earned += ((sells['Qty'] * sells['Price']) - sells['Fee']).sum()
        
        divs = df_stock[df_stock['Action'] == 'DIVIDEND']
        if not divs.empty:
            usd_earned += (divs['Price'] - divs['Fee']).sum()

    current_usd = usd_gained - usd_spent + usd_earned
    return {'KRW': current_krw, 'USD': current_usd}

def log_cash_flow(date, type_, krw, usd, rate):
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0)
        date_str = date.strftime("%Y-%m-%d")
        new_row = pd.DataFrame([{"Date": date_str, "Type": type_, "Amount_KRW": krw, "Amount_USD": usd, "Ex_Rate": rate}])
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=pd.concat([df, new_row], ignore_index=True))
    except: st.error("CashFlow 시트 오류")

def log_stock_trade(date, ticker, action, qty, price, rate, fee):
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0)
        date_str = date.strftime("%Y-%m-%d")
        new_row = pd.DataFrame([{"Date": date_str, "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": rate, "Fee": fee}])
        conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=pd.concat([df, new_row], ignore_index=True))
    except: st.error("Sheet1 오류")

def delete_data_by_date(target_date_str):
    try:
        df_s = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0)
        if not df_s.empty and 'Date' in df_s.columns:
            df_s['Date'] = df_s['Date'].astype(str)
            df_s = df_s[df_s['Date'] != target_date_str]
            conn.update(spreadsheet=SHEET_URL, worksheet="Sheet1", data=df_s)
            
        df_c = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0)
        if not df_c.empty and 'Date' in df_c.columns:
            df_c['Date'] = df_c['Date'].astype(str)
            df_c = df_c[df_c['Date'] != target_date_str]
            conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=df_c)
        return True
    except Exception as e:
        st.error(f"삭제 오류: {e}")
        return False

def calculate_history(df_stock, df_cash):
    # 컬럼 체크 (에러 방지)
    if df_stock.empty and df_cash.empty: return pd.DataFrame()
    if not df_stock.empty and 'Date' not in df_stock.columns: return pd.DataFrame()
    if not df_cash.empty and 'Date' not in df_cash.columns: return pd.DataFrame()
    
    dates = []
    if not df_stock.empty: dates.append(pd.to_datetime(df_stock['Date']).min())
    if not df_cash.empty: dates.append(pd.to_datetime(df_cash['Date']).min())
    if not dates: return pd.DataFrame()
    
    start_date = min(dates)
    end_date = datetime.today()
    date_range = pd.date_range(start=start_date, end=end_date)
    
    history = []
    cum_cash_krw = 0
    cum_cash_usd = 0
    cum_invested_krw = 0 
    cum_stock_qty = {'SGOV':0, 'SPYM':0, 'QQQM':0, 'GMMF':0}
    
    df_s = df_stock.copy()
    df_s['Date'] = pd.to_datetime(df_s['Date'])
    df_c = df_cash.copy()
    df_c['Date'] = pd.to_datetime(df_c['Date'])

    for d in date_range:
        day_cash = df_c[df_c['Date'] == d]
        for _, row in day_cash.iterrows():
            if row['Type'] == 'Deposit': 
                cum_cash_krw += row['Amount_KRW']
                cum_invested_krw += row['Amount_KRW']
            elif row['Type'] == 'Exchange':
                cum_cash_krw -= row['Amount_KRW']
                cum_cash_usd += row['Amount_USD']
        
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
# 3. 로딩 (기존 시트 사용)
# ==========================================
st.title("🛡️ Project Aegis V11.1 (Self-Healing)")

try:
    df_stock = conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0).fillna(0)
    if not df_stock.empty and 'Date' in df_stock.columns:
        df_stock['Date'] = pd.to_datetime(df_stock['Date']).dt.strftime("%Y-%m-%d")
        df_stock = df_stock.sort_values(by="Date", ascending=False)
except: df_stock = pd.DataFrame()

try:
    df_cash = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0).fillna(0)
    if not df_cash.empty and 'Date' in df_cash.columns:
        df_cash['Date'] = pd.to_datetime(df_cash['Date']).dt.strftime("%Y-%m-%d")
except: df_cash = pd.DataFrame()

my_wallet = calculate_wallet_balance(df_stock, df_cash)
krw_rate = get_usd_krw()

# ==========================================
# 4. 사이드바
# ==========================================
st.sidebar.header("🏦 자금 관리")
c1, c2 = st.sidebar.columns(2)
c1.metric("🇰🇷 원화", f"{int(my_wallet['KRW']):,}원")
c2.metric("🇺🇸 달러", f"${my_wallet['USD']:.2f}")

mode = st.sidebar.radio("작업 선택", ["주식 거래", "입금/환전", "🗑️ 데이터 관리"], horizontal=True)

if mode == "입금/환전":
    st.sidebar.subheader("💱 입금 및 환전")
    act_type = st.sidebar.selectbox("종류", ["원화 입금 (Deposit)", "달러 환전 (Exchange)"])
    
    with st.sidebar.form("cash_form"):
        date = st.date_input("날짜", datetime.today())
        label_amt = "입금할 원화 금액" if "Deposit" in act_type else "환전에 쓴 원화 금액"
        amount_krw = st.number_input(label_amt, step=10000)
        
        ex_rate_in = krw_rate
        if "Exchange" in act_type:
            ex_rate_in = st.number_input("적용 환율", value=krw_rate, format="%.2f")
            if ex_rate_in > 0:
                st.caption(f"💵 예상 획득: ${amount_krw / ex_rate_in:.2f}")
        
        if st.form_submit_button("실행"):
            if "Deposit" in act_type:
                log_cash_flow(date, "Deposit", amount_krw, 0, 0)
                st.success("💰 입금 완료!")
            else:
                if my_wallet['KRW'] >= amount_krw:
                    usd_out = amount_krw / ex_rate_in
                    log_cash_flow(date, "Exchange", amount_krw, usd_out, ex_rate_in)
                    st.success("💱 환전 완료!")
                else: st.error("❌ 잔고 부족! (입금 내역을 먼저 기록하세요)")
            time.sleep(1)
            st.rerun()

elif mode == "주식 거래":
    st.sidebar.subheader("📈 주식 매매 & 배당")
    ticker = st.sidebar.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    action = st.sidebar.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
    
    with st.sidebar.form("stock_form"):
        date = st.date_input("날짜", datetime.today())
        
        qty = 1.0
        if action != "DIVIDEND":
            qty = st.number_input("수량 (Qty)", value=1.0, step=0.01)
        
        price_label = "배당금 총액 ($)" if action == "DIVIDEND" else "체결 단가 ($)"
        cur_p = 0.0
        if action != "DIVIDEND": cur_p = get_current_price(ticker)
        price = st.number_input(price_label, value=cur_p if cur_p>0 else 0.0, format="%.2f")
        
        fee_help = "세금/수수료 (배당은 세후면 0)"
        fee = st.number_input("수수료 ($)", value=0.0, help=fee_help, format="%.2f")
        rate = st.number_input("환율", value=krw_rate, format="%.2f")

        if st.form_submit_button("기록하기"):
            if action == "DIVIDEND": qty = 1.0 
            cost = (qty * price) + fee
            
            if action == "BUY":
                if my_wallet['USD'] >= cost:
                    log_stock_trade(date, ticker, action, qty, price, rate, fee)
                    st.success("✅ 매수 완료")
                    time.sleep(1)
                    st.rerun()
                else: st.error("❌ 달러 부족! (환전 내역을 먼저 기록하세요)")
            elif action == "DIVIDEND":
                log_stock_trade(date, ticker, action, 1.0, price, rate, fee)
                st.success("💰 배당금 입금")
                time.sleep(1)
                st.rerun()
            else: st.warning("매도 기록만 됩니다.")

elif mode == "🗑️ 데이터 관리":
    st.sidebar.subheader("📅 날짜별 삭제")
    st.sidebar.info("선택한 날짜의 '모든 기록(입금/환전/주식)'이 삭제됩니다.")
    
    available_dates = set()
    if not df_stock.empty and 'Date' in df_stock.columns: available_dates.update(df_stock['Date'].unique())
    if not df_cash.empty and 'Date' in df_cash.columns: available_dates.update(df_cash['Date'].unique())
    
    if available_dates:
        target_date = st.sidebar.selectbox("삭제할 날짜", sorted(list(available_dates), reverse=True))
        if st.sidebar.button("🚨 해당 날짜 데이터 영구 삭제"):
            if delete_data_by_date(target_date):
                st.success(f"{target_date} 데이터가 모두 삭제되었습니다. 잔고가 자동 재계산됩니다.")
                time.sleep(2)
                st.rerun()
    else:
        st.sidebar.caption("삭제할 데이터가 없습니다.")

st.sidebar.markdown("---")
if st.sidebar.button("🔔 텔레그램 테스트"): send_test_message()

# ==========================================
# 5. 메인 대시보드
# ==========================================
current_holdings = {}
total_stock_val_krw = 0
asset_details = []

if not df_stock.empty and 'Action' in df_stock.columns:
    current_holdings = df_stock.groupby("Ticker").apply(lambda x: x.loc[x['Action']=='BUY','Qty'].sum() - x.loc[x['Action']=='SELL','Qty'].sum()).to_dict()
    for t, q in current_holdings.items():
        if q > 0:
            p = get_current_price(t)
            if p == 0: p = 100.0
            val_krw = q * p * krw_rate
            total_stock_val_krw += val_krw
            asset_details.append({"종목": t, "가치": val_krw, "수량": q})

total_deposit = 0
if not df_cash.empty and 'Type' in df_cash.columns:
    total_deposit = df_cash[df_cash['Type']=='Deposit']['Amount_KRW'].sum()

total_asset = total_stock_val_krw + my_wallet['KRW'] + (my_wallet['USD'] * krw_rate)
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
                {"Type": "현금(KRW)", "Value": my_wallet['KRW']},
                {"Type": "현금(USD)", "Value": my_wallet['USD'] * krw_rate}
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
            long_df = history_df.melt('Date', value_vars=['Stock_SGOV', 'Stock_QQQM', 'Stock_SPYM'], var_name='Ticker', value_name='Qty')
            c = alt.Chart(long_df).mark_line(point=True).encode(x='Date', y='Qty', color='Ticker', tooltip=['Date', 'Ticker', 'Qty']).interactive()
            st.altair_chart(c, use_container_width=True)
        elif chart_opt == "현금 잔고":
            long_df = history_df.melt('Date', value_vars=['Cash_KRW', 'Cash_USD'], var_name='Currency', value_name='Amount')
            c = alt.Chart(long_df).mark_line(point=True).encode(x='Date', y='Amount', color='Currency', tooltip=['Date', 'Currency', 'Amount']).interactive()
            st.altair_chart(c, use_container_width=True)
        elif chart_opt == "총 투자원금":
            c = alt.Chart(history_df).mark_line(point=True, color='red').encode(x='Date', y='Total_Invested', tooltip=['Date', 'Total_Invested']).interactive()
            st.altair_chart(c, use_container_width=True)
    else: st.info("데이터가 부족합니다.")

with tab3:
    st.subheader("📝 주식 거래 내역")
    st.dataframe(df_stock, use_container_width=True)
    st.markdown("---")
    st.subheader("📝 입출금/환전 내역")
    st.dataframe(df_cash, use_container_width=True)
