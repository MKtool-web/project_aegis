import streamlit as st
import pandas as pd
import yfinance as yf
import time
import requests
import altair as alt 
import ta
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta

# ==========================================
# 0. 기본 설정
# ==========================================
st.set_page_config(page_title="Project Aegis V15.1 (Real-Asset Check)", layout="wide")
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

@st.cache_data(ttl=300)
def get_market_analysis(ticker):
    try:
        df = yf.Ticker(ticker).history(period="2mo")
        if len(df) < 14: return 0, 0, pd.DataFrame()
        df['RSI'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
        return df['Close'].iloc[-1], df['RSI'].iloc[-1], df
    except: return 0, 0, pd.DataFrame()

@st.cache_data(ttl=300)
def get_vix_data():
    try:
        df = yf.Ticker("^VIX").history(period="2mo")
        return df['Close'].iloc[-1], df
    except: return 0, pd.DataFrame()

def calculate_wallet_balance_detail(df_stock, df_cash):
    krw_deposit = 0; krw_used = 0; usd_gained = 0
    if not df_cash.empty:
        for col in ['Amount_KRW', 'Amount_USD']:
            if col in df_cash.columns: df_cash[col] = pd.to_numeric(df_cash[col].astype(str).str.replace(',',''), errors='coerce').fillna(0)
        krw_deposit = df_cash[df_cash['Type'] == 'Deposit']['Amount_KRW'].sum()
        krw_used = df_cash[df_cash['Type'] == 'Exchange']['Amount_KRW'].sum()
        usd_gained = df_cash[df_cash['Type'] == 'Exchange']['Amount_USD'].sum()

    usd_spent = 0; usd_earned = 0; stock_details = []
    if not df_stock.empty:
        for col in ['Qty', 'Price', 'Fee']:
            if col in df_stock.columns: df_stock[col] = pd.to_numeric(df_stock[col].astype(str).str.replace(',',''), errors='coerce').fillna(0)
        buys = df_stock[df_stock['Action'] == 'BUY']
        for _, row in buys.iterrows():
            cost = (row['Qty'] * row['Price']) + row['Fee']
            usd_spent += cost
            stock_details.append(f"[-] 매수 {row['Ticker']}: ${cost:.2f}")
        sells = df_stock[df_stock['Action'] == 'SELL']
        for _, row in sells.iterrows():
            revenue = (row['Qty'] * row['Price']) - row['Fee']
            usd_earned += revenue
            stock_details.append(f"[+] 매도 {row['Ticker']}: ${revenue:.2f}")
        divs = df_stock[df_stock['Action'] == 'DIVIDEND']
        for _, row in divs.iterrows():
            revenue = row['Price'] - row['Fee']
            usd_earned += revenue
            stock_details.append(f"[+] 배당 {row['Ticker']}: ${revenue:.2f}")

    return {'KRW': krw_deposit - krw_used, 'USD': usd_gained - usd_spent + usd_earned, 
            'Detail_USD_In': usd_gained, 'Detail_USD_Out': usd_spent, 'Detail_USD_Earned': usd_earned, 'Stock_Log': stock_details}

def calculate_tax_guard(df_stock):
    if df_stock.empty: return {'realized_profit': 0, 'tax_estimated': 0, 'log': [], 'remaining_allowance': 2500000}
    df = df_stock.copy(); df['Date'] = pd.to_datetime(df['Date']); df = df.sort_values(by='Date')
    holdings = {}; current_year = datetime.now().year; realized_profit_krw = 0; tax_log = []
    for _, row in df.iterrows():
        ticker = row['Ticker']; qty = row['Qty']; price = row['Price']; fee = row['Fee']; rate = row['Exchange_Rate']
        if ticker not in holdings: holdings[ticker] = {'qty': 0, 'total_cost_krw': 0}
        if row['Action'] == 'BUY':
            cost_krw = (qty * price * rate) + (fee * rate)
            holdings[ticker]['qty'] += qty; holdings[ticker]['total_cost_krw'] += cost_krw
        elif row['Action'] == 'SELL':
            if holdings[ticker]['qty'] > 0:
                avg_buy_price_krw = holdings[ticker]['total_cost_krw'] / holdings[ticker]['qty']
                sell_revenue_krw = (qty * price * rate) - (fee * rate)
                buy_cost_krw = avg_buy_price_krw * qty
                profit = sell_revenue_krw - buy_cost_krw
                holdings[ticker]['qty'] -= qty; holdings[ticker]['total_cost_krw'] -= buy_cost_krw
                if row['Date'].year == current_year:
                    realized_profit_krw += profit; tax_log.append(f"{row['Date'].strftime('%Y-%m-%d')} {ticker} 매도: {int(profit):,}원 (수익)")
    return {'realized_profit': realized_profit_krw, 'tax_estimated': max(0, realized_profit_krw - 2500000) * 0.22, 
            'remaining_allowance': max(0, 2500000 - realized_profit_krw), 'log': tax_log}

def log_cash_flow(date, type_, krw, usd, rate):
    try:
        df = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0)
        if 'Type' not in df.columns: df = pd.DataFrame(columns=["Date", "Type", "Amount_KRW", "Amount_USD", "Ex_Rate"])
        date_str = date.strftime("%Y-%m-%d")
        new_row = pd.DataFrame([{"Date": date_str, "Type": type_, "Amount_KRW": krw, "Amount_USD": usd, "Ex_Rate": rate}])
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=pd.concat([df, new_row], ignore_index=True))
    except: st.error("CashFlow 오류")

def log_stock_trade(date, ticker, action, qty, price, rate, fee):
    try:
        sheet_name = "Sheet1"
        try: conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0, usecols=[0])
        except: sheet_name = "시트1"
        df = conn.read(spreadsheet=SHEET_URL, worksheet=sheet_name, ttl=0)
        date_str = date.strftime("%Y-%m-%d")
        new_row = pd.DataFrame([{"Date": date_str, "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Exchange_Rate": rate, "Fee": fee}])
        conn.update(spreadsheet=SHEET_URL, worksheet=sheet_name, data=pd.concat([df, new_row], ignore_index=True))
    except: st.error("시트 오류")

def delete_data_by_date(target_date_str):
    try:
        sheet_name = "Sheet1"
        try: conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0, usecols=[0])
        except: sheet_name = "시트1"
        df_s = conn.read(spreadsheet=SHEET_URL, worksheet=sheet_name, ttl=0)
        if not df_s.empty and 'Date' in df_s.columns:
            df_s['Date'] = df_s['Date'].astype(str); df_s = df_s[df_s['Date'] != target_date_str]
            conn.update(spreadsheet=SHEET_URL, worksheet=sheet_name, data=df_s)
        df_c = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0)
        if not df_c.empty and 'Date' in df_c.columns:
            df_c['Date'] = df_c['Date'].astype(str); df_c = df_c[df_c['Date'] != target_date_str]
            conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=df_c)
        return True
    except: return False

def calculate_history(df_stock, df_cash):
    if df_stock.empty and df_cash.empty: return pd.DataFrame()
    dates = []
    if not df_stock.empty and 'Date' in df_stock.columns: dates.append(pd.to_datetime(df_stock['Date']).min())
    if not df_cash.empty and 'Date' in df_cash.columns: dates.append(pd.to_datetime(df_cash['Date']).min())
    if not dates: return pd.DataFrame()
    start_date = min(dates); end_date = datetime.today(); date_range = pd.date_range(start=start_date, end=end_date)
    history = []; cum_cash_krw = 0; cum_cash_usd = 0; cum_invested_krw = 0; cum_stock_qty = {'SGOV':0, 'SPYM':0, 'QQQM':0, 'GMMF':0}
    df_s = df_stock.copy()
    if not df_s.empty:
        df_s['Date'] = pd.to_datetime(df_s['Date'])
        for col in ['Qty', 'Price', 'Fee']: df_s[col] = pd.to_numeric(df_s[col], errors='coerce').fillna(0)
    df_c = df_cash.copy()
    if not df_c.empty:
        df_c['Date'] = pd.to_datetime(df_c['Date'])
        for col in ['Amount_KRW', 'Amount_USD']: df_c[col] = pd.to_numeric(df_c[col], errors='coerce').fillna(0)
    for d in date_range:
        if not df_c.empty:
            day_cash = df_c[df_c['Date'] == d]
            for _, row in day_cash.iterrows():
                if row['Type'] == 'Deposit': cum_cash_krw += row['Amount_KRW']; cum_invested_krw += row['Amount_KRW']
                elif row['Type'] == 'Exchange': cum_cash_krw -= row['Amount_KRW']; cum_cash_usd += row['Amount_USD']
        if not df_s.empty:
            day_stock = df_s[df_s['Date'] == d]
            for _, row in day_stock.iterrows():
                cost = (row['Qty'] * row['Price']) + row['Fee']
                if row['Action'] == 'BUY': cum_cash_usd -= cost; cum_stock_qty[row['Ticker']] += row['Qty']
                elif row['Action'] == 'SELL': net_gain = (row['Qty'] * row['Price']) - row['Fee']; cum_cash_usd += net_gain; cum_stock_qty[row['Ticker']] -= row['Qty']
                elif row['Action'] == 'DIVIDEND': net_div = row['Price'] - row['Fee']; cum_cash_usd += net_div
        history.append({"Date": d, "Total_Invested": cum_invested_krw, "Cash_KRW": cum_cash_krw, "Cash_USD": cum_cash_usd, 
                        "Stock_SGOV": cum_stock_qty.get('SGOV',0), "Stock_QQQM": cum_stock_qty.get('QQQM',0), "Stock_SPYM": cum_stock_qty.get('SPYM',0), "Stock_GMMF": cum_stock_qty.get('GMMF',0)})
    return pd.DataFrame(history)

# ==========================================
# 3. 로딩 및 메인
# ==========================================
st.title("🛡️ Project Aegis V15.1 (Real-Asset Check)")

# 데이터 로딩
sheet_name = "Sheet1"
try: conn.read(spreadsheet=SHEET_URL, worksheet="Sheet1", ttl=0, usecols=[0])
except: sheet_name = "시트1"
try:
    df_stock = conn.read(spreadsheet=SHEET_URL, worksheet=sheet_name, ttl=0).fillna(0)
    if 'Date' not in df_stock.columns:
        empty_stock = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"])
        conn.update(spreadsheet=SHEET_URL, worksheet=sheet_name, data=empty_stock)
        df_stock = empty_stock
    else:
        df_stock['Date'] = pd.to_datetime(df_stock['Date']).dt.strftime("%Y-%m-%d")
        df_stock = df_stock.sort_values(by="Date", ascending=False)
except: df_stock = pd.DataFrame()
try:
    df_cash = conn.read(spreadsheet=SHEET_URL, worksheet="CashFlow", ttl=0).fillna(0)
    if 'Type' not in df_cash.columns:
        empty_cash = pd.DataFrame(columns=["Date", "Type", "Amount_KRW", "Amount_USD", "Ex_Rate"])
        conn.update(spreadsheet=SHEET_URL, worksheet="CashFlow", data=empty_cash)
        df_cash = empty_cash
    else: df_cash['Date'] = pd.to_datetime(df_cash['Date']).dt.strftime("%Y-%m-%d")
except: df_cash = pd.DataFrame()

wallet_data = calculate_wallet_balance_detail(df_stock, df_cash)
tax_info = calculate_tax_guard(df_stock)
krw_rate = get_usd_krw()

# 사이드바
st.sidebar.header("🏦 자금 관리")
c1, c2 = st.sidebar.columns(2)
c1.metric("🇰🇷 원화", f"{int(wallet_data['KRW']):,}원")
c2.metric("🇺🇸 달러", f"${wallet_data['USD']:.2f}")

st.sidebar.markdown("---")
with st.sidebar.expander("🎯 목표 포트폴리오 설정"):
    st.caption("목표 비중 합계는 100%가 권장됩니다.")
    target_qqqm = st.slider("QQQM (성장)", 0, 100, 35, 5)
    target_spym = st.slider("SPYM (안정)", 0, 100, 35, 5)
    target_sgov = st.slider("SGOV (현금성)", 0, 100, 30, 5)
    total_target = target_qqqm + target_spym + target_sgov
    if total_target != 100: st.error(f"합계: {total_target}% (100%가 아닙니다!)")
    else: st.success("합계: 100% (완벽합니다)")

st.sidebar.markdown("---")
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
            if ex_rate_in > 0: st.caption(f"💵 예상 획득: ${amount_krw / ex_rate_in:.2f}")
        if st.form_submit_button("실행"):
            if "Deposit" in act_type:
                log_cash_flow(date, "Deposit", amount_krw, 0, 0)
                st.success("💰 입금 완료!")
            else:
                if wallet_data['KRW'] >= amount_krw:
                    usd_out = amount_krw / ex_rate_in
                    log_cash_flow(date, "Exchange", amount_krw, usd_out, ex_rate_in)
                    st.success("💱 환전 완료!")
                else: st.error("❌ 잔고 부족!")
            time.sleep(1)
            st.rerun()

elif mode == "주식 거래":
    st.sidebar.subheader("📈 주식 매매 & 배당")
    ticker = st.sidebar.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
    action = st.sidebar.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
    with st.sidebar.form("stock_form"):
        date = st.date_input("날짜", datetime.today())
        qty = 1.0
        if action != "DIVIDEND": qty = st.number_input("수량 (Qty)", value=1.0, step=0.01)
        price_label = "배당금 총액 ($)" if action == "DIVIDEND" else "체결 단가 ($)"
        cur_p = 0.0
        if action != "DIVIDEND": cur_p = get_current_price(ticker)
        price = st.number_input(price_label, value=cur_p if cur_p>0 else 0.0, format="%.2f")
        fee = st.number_input("수수료 ($)", value=0.0, format="%.2f")
        rate = st.number_input("환율", value=krw_rate, format="%.2f")
        if st.form_submit_button("기록하기"):
            if action == "DIVIDEND": qty = 1.0 
            cost = (qty * price) + fee
            if action == "BUY":
                if wallet_data['USD'] >= cost:
                    log_stock_trade(date, ticker, action, qty, price, rate, fee)
                    st.success("✅ 매수 완료")
                    time.sleep(1)
                    st.rerun()
                else: st.error("❌ 달러 부족!")
            elif action == "DIVIDEND":
                log_stock_trade(date, ticker, action, 1.0, price, rate, fee)
                st.success("💰 배당금 입금")
                time.sleep(1)
                st.rerun()
            else: st.warning("매도 기록됨")

elif mode == "🗑️ 데이터 관리":
    st.sidebar.subheader("📅 날짜별 삭제")
    available_dates = set()
    if not df_stock.empty and 'Date' in df_stock.columns: available_dates.update(df_stock['Date'].unique())
    if not df_cash.empty and 'Date' in df_cash.columns: available_dates.update(df_cash['Date'].unique())
    if available_dates:
        target_date = st.sidebar.selectbox("삭제할 날짜", sorted(list(available_dates), reverse=True))
        if st.sidebar.button("🚨 해당 날짜 데이터 삭제"):
            if delete_data_by_date(target_date): st.success("삭제 완료"); time.sleep(2); st.rerun()
    else: st.sidebar.caption("데이터 없음")

st.sidebar.markdown("---")
if st.sidebar.button("🔔 텔레그램 테스트"): send_test_message()

# 메인 대시보드
current_holdings = {}
total_stock_val_krw = 0
asset_details = []
if not df_stock.empty and 'Action' in df_stock.columns:
    df_stock['Qty'] = pd.to_numeric(df_stock['Qty'], errors='coerce').fillna(0)
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
    df_cash['Amount_KRW'] = pd.to_numeric(df_cash['Amount_KRW'], errors='coerce').fillna(0)
    total_deposit = df_cash[df_cash['Type']=='Deposit']['Amount_KRW'].sum()

total_asset = total_stock_val_krw + wallet_data['KRW'] + (wallet_data['USD'] * krw_rate)
net_profit = total_asset - total_deposit
profit_rate = (net_profit / total_deposit * 100) if total_deposit > 0 else 0

# 탭 구성 (6개)
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 자산 & 포트폴리오", "⚖️ AI 리밸런싱", "📡 AI 시장 레이더", "👮‍♂️ 세금 지킴이", "📈 추세 그래프", "📋 상세 기록"])

with tab1:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("총 자산", f"{int(total_asset):,}원")
    col2.metric("총 투자원금", f"{int(total_deposit):,}원")
    col3.metric("예상 수익", f"{int(net_profit):+,.0f}원", f"{profit_rate:.2f}%")
    col4.metric("현재 환율", f"{krw_rate:,.0f}원")
    st.markdown("---")
    
    with st.expander("🔍 잔고 계산 내역 상세"):
        st.write(f"1. 총 환전 입금: ${wallet_data['Detail_USD_In']:.2f}")
        st.write(f"2. 주식 매수 총액: ${wallet_data['Detail_USD_Out']:.2f}")
        st.write(f"3. 수익: ${wallet_data['Detail_USD_Earned']:.2f}")
        st.write(f"= 최종 잔고: ${wallet_data['USD']:.2f}")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🍩 자산 구성")
        if total_asset > 0:
            asset_df = pd.DataFrame([{"Type": "주식", "Value": total_stock_val_krw}, {"Type": "현금(KRW)", "Value": wallet_data['KRW']}, {"Type": "현금(USD)", "Value": wallet_data['USD'] * krw_rate}])
            asset_df['Percent'] = (asset_df['Value'] / total_asset * 100).round(1).astype(str) + '%'
            base = alt.Chart(asset_df).encode(theta=alt.Theta("Value", stack=True))
            pie = base.mark_arc(outerRadius=120, innerRadius=60).encode(color=alt.Color("Type"), order=alt.Order("Value", sort="descending"), tooltip=["Type", "Value", "Percent"])
            text = base.mark_text(radius=140).encode(text=alt.Text("Percent"), order=alt.Order("Value", sort="descending"), color=alt.value("black"))
            st.altair_chart(pie + text, use_container_width=True)
    with c2:
        st.subheader("🥧 종목 비중")
        if asset_details:
            stock_df = pd.DataFrame(asset_details)
            stock_df['Percent'] = (stock_df['가치'] / stock_df['가치'].sum() * 100).round(1).astype(str) + '%'
            base2 = alt.Chart(stock_df).encode(theta=alt.Theta("가치", stack=True))
            pie2 = base2.mark_arc(outerRadius=120).encode(color=alt.Color("종목"), tooltip=["종목", "가치", "Percent"])
            text2 = base2.mark_text(radius=140).encode(text=alt.Text("Percent"), order=alt.Order("가치", sort="descending"), color=alt.value("black"))
            st.altair_chart(pie2 + text2, use_container_width=True)

# 🔥 [NEW] AI 리밸런싱 탭 (지갑 잔고 연동)
with tab2:
    st.header("⚖️ AI Portfolio Rebalancer")
    st.caption("사이드바에서 설정한 '목표 비율'에 맞춰 리밸런싱을 제안합니다.")
    
    if asset_details:
        rebal_df = pd.DataFrame(asset_details)
        total_val = rebal_df['가치'].sum()
        rebal_df['Current_%'] = (rebal_df['가치'] / total_val * 100)
        targets = {'QQQM': target_qqqm, 'SPYM': target_spym, 'SGOV': target_sgov, 'GMMF': 0}
        rebal_df['Target_%'] = rebal_df['종목'].map(targets).fillna(0)
        rebal_df['Diff_%'] = rebal_df['Current_%'] - rebal_df['Target_%']
        rebal_df['Action_Value'] = total_val * (rebal_df['Target_%'] - rebal_df['Current_%']) / 100
        rebal_df['Action_Value_USD'] = rebal_df['Action_Value'] / krw_rate
        
        current_prices = {t: get_current_price(t) for t in rebal_df['종목']}
        rebal_df['Price_USD'] = rebal_df['종목'].map(current_prices)
        rebal_df['Action_Qty'] = (rebal_df['Action_Value_USD'] / rebal_df['Price_USD']).round(1)
        
        for _, row in rebal_df.iterrows():
            if row['Target_%'] == 0: continue
            
            col_info, col_action = st.columns([2, 1])
            with col_info:
                st.subheader(f"{row['종목']}")
                st.write(f"**현재 {row['Current_%']:.1f}%** vs **목표 {row['Target_%']:.1f}%** (차이: {row['Diff_%']:+.1f}%)")
                st.progress(min(1.0, max(0.0, row['Current_%']/100)))
            
            with col_action:
                if row['Action_Qty'] > 0.5:
                    cost_usd = row['Action_Value_USD']
                    # 🔥 [CHECK] 달러 잔고 확인 로직 추가
                    if wallet_data['USD'] >= cost_usd:
                        st.success(f"🔵 **매수 추천**\n\n약 {row['Action_Qty']}주\n(${cost_usd:.2f})\n(자금 충분 ✅)")
                    else:
                        shortage = cost_usd - wallet_data['USD']
                        st.warning(f"🟠 **매수 추천**\n\n약 {row['Action_Qty']}주\n(${cost_usd:.2f})\n(⚠️ ${shortage:.2f} 부족! 환전 필요)")
                        
                elif row['Action_Qty'] < -0.5:
                    st.error(f"🔴 **매도 추천**\n\n약 {abs(row['Action_Qty'])}주\n(${abs(row['Action_Value_USD']):.2f})")
                else:
                    st.info("⚪ **유지 (Good)**\n\n리밸런싱 불필요")
            st.markdown("---")
    else: st.info("보유 중인 주식이 없어 리밸런싱을 계산할 수 없습니다.")

with tab3:
    st.header("📡 AI Market Radar")
    col_vix, col_qqqm, col_spym = st.columns(3)
    vix_val, vix_hist = get_vix_data()
    vix_delta = vix_val - vix_hist['Close'].iloc[-2] if len(vix_hist) > 1 else 0
    with col_vix:
        st.metric("VIX (공포지수)", f"{vix_val:.2f}", f"{vix_delta:.2f}", delta_color="inverse")
        if vix_val > 30: st.error("😱 극도의 공포 (매수 기회!)")
        elif vix_val < 15: st.warning("😌 너무 평온함 (주의)")
        else: st.info("😐 보통 시장")
    q_price, q_rsi, q_hist = get_market_analysis("QQQM")
    with col_qqqm:
        st.metric("QQQM RSI (14)", f"{q_rsi:.1f}")
        if q_rsi < 30: st.success("🟢 과매도 (Strong Buy)")
        elif q_rsi > 70: st.error("🔴 과매수 (Sell Warning)")
        else: st.info("⚪ 중립")
    s_price, s_rsi, s_hist = get_market_analysis("SPYM")
    with col_spym:
        st.metric("SPYM RSI (14)", f"{s_rsi:.1f}")
        if s_rsi < 30: st.success("🟢 과매도 (Buy)")
        elif s_rsi > 70: st.error("🔴 과매수 (Sell)")
        else: st.info("⚪ 중립")
    if not q_hist.empty:
        q_hist = q_hist.reset_index()
        chart = alt.Chart(q_hist).mark_line().encode(x='Date', y='RSI', tooltip=['Date', 'RSI']).properties(height=300)
        st.altair_chart(chart, use_container_width=True)

with tab4:
    st.header("👮‍♂️ 2025년 세금 지킴이 (Tax Guard)")
    t1, t2, t3 = st.columns(3)
    t1.metric("올해 실현 수익", f"{int(tax_info['realized_profit']):,}원")
    t2.metric("남은 비과세 한도", f"{int(tax_info['remaining_allowance']):,}원", delta_color="normal" if tax_info['remaining_allowance'] > 0 else "inverse")
    t3.metric("예상 세금 (22%)", f"{int(tax_info['tax_estimated']):,}원")
    progress = min(1.0, max(0.0, tax_info['realized_profit'] / 2500000))
    st.write(f"📊 **한도 소진율: {progress*100:.1f}%**")
    st.progress(progress)
    if tax_info['log']:
        for log in tax_info['log']: st.text(log)
    else: st.info("올해 매도 내역 없음")

with tab5:
    st.subheader("📈 자산 변화 추이")
    history_df = calculate_history(df_stock, df_cash)
    if not history_df.empty:
        chart_opt = st.radio("그래프 선택", ["보유 수량", "현금 잔고", "총 투자원금"], horizontal=True)
        if chart_opt == "보유 수량":
            long_df = history_df.melt('Date', value_vars=['Stock_SGOV', 'Stock_QQQM', 'Stock_SPYM', 'Stock_GMMF'], var_name='Ticker', value_name='Qty')
            c = alt.Chart(long_df).mark_line(point=True).encode(x='Date', y='Qty', color='Ticker', tooltip=['Date', 'Ticker', 'Qty']).interactive()
            st.altair_chart(c, use_container_width=True)
        elif chart_opt == "현금 잔고":
            long_df = history_df.melt('Date', value_vars=['Cash_KRW', 'Cash_USD'], var_name='Currency', value_name='Amount')
            c = alt.Chart(long_df).mark_line(point=True).encode(x='Date', y='Amount', color='Currency', tooltip=['Date', 'Currency', 'Amount']).interactive()
            st.altair_chart(c, use_container_width=True)
        elif chart_opt == "총 투자원금":
            c = alt.Chart(history_df).mark_line(point=True, color='red').encode(x='Date', y='Total_Invested', tooltip=['Date', 'Total_Invested']).interactive()
            st.altair_chart(c, use_container_width=True)
    else: st.info("데이터 부족")

with tab6:
    st.dataframe(df_stock, use_container_width=True)
    st.dataframe(df_cash, use_container_width=True)
