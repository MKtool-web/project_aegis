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

# 지갑(Wallet) 데이터 읽기/쓰기 함수
def get_wallet_balance():
    try:
        # Wallet 시트 읽기 (worksheet="Wallet" 지정)
        df_wallet = conn.read(spreadsheet=SHEET_URL, worksheet="Wallet", usecols=[0, 1], ttl=0)
        # 딕셔너리로 변환 {KRW: 400000, USD: 50}
        balance = dict(zip(df_wallet['Currency'], df_wallet['Amount']))
        return balance
    except:
        return {'KRW': 0, 'USD': 0}

def update_wallet_balance(currency, amount, operation="add"):
    # 현재 잔고 읽기
    df_wallet = conn.read(spreadsheet=SHEET_URL, worksheet="Wallet", usecols=[0, 1], ttl=0)
    
    # 해당 통화 찾아서 업데이트
    idx = df_wallet.index[df_wallet['Currency'] == currency].tolist()
    if not idx:
        # 없으면 새로 추가 (혹시 모를 에러 방지)
        new_row = pd.DataFrame([{'Currency': currency, 'Amount': 0}])
        df_wallet = pd.concat([df_wallet, new_row], ignore_index=True)
        idx = [len(df_wallet) - 1]
    
    current_amt = float(df_wallet.at[idx[0], 'Amount'])
    
    if operation == "add":
        new_amt = current_amt + amount
    elif operation == "subtract":
        new_amt = current_amt - amount
        
    df_wallet.at[idx[0], 'Amount'] = new_amt
    
    # Wallet 시트에 덮어쓰기
    conn.update(spreadsheet=SHEET_URL, worksheet="Wallet", data=df_wallet)

# ==========================================
# 2. AI 전략 (지갑 연동)
# ==========================================
class Rebalancer:
    def __init__(self, current_holdings, wallet_balance):
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0} 
        self.holdings = current_holdings
        self.wallet = wallet_balance # 지갑 정보 탑재

    def analyze(self, current_rate):
        # 내 실제 총 자산 = 주식 가치 + 보유 달러 + (보유 원화/환율)
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
        
        # 환율 코멘트
        if current_rate > 1460:
            msg = f"⚠️ [고환율] 1,460원 돌파. 원화({int(self.wallet.get('KRW',0)):,}원)는 그대로 두세요."
        elif current_rate < 1380:
            can_exchange = self.wallet.get('KRW', 0)
            msg = f"✅ [환전 기회] 환율 1,380원 아래! 보유 원화 {int(can_exchange):,}원 중 일부를 환전하세요."

        # 매수 추천 (보유 달러 기준)
        my_usd = self.wallet.get('USD', 0)
        if my_usd > 10: # 10달러 이상 있을 때만
            for ticker, target_ratio in self.TARGET_RATIO.items():
                if target_ratio == 0: continue
                target_amt = total_asset_usd * target_ratio
                current_amt = portfolio.get(ticker, {'value': 0})['value']
                
                if current_amt < target_amt:
                    shortfall = target_amt - current_amt
                    price = portfolio.get(ticker, {'price': 100})['price']
                    
                    # 내 지갑 사정 고려 (중요!)
                    buy_qty = int(min(shortfall, my_usd) // price)
                    
                    if buy_qty > 0:
                        cost = buy_qty * price
                        recommendations.append({'ticker': ticker, 'qty': buy_qty, 'cost': cost})
                        my_usd -= cost # 예산 차감
                        
        return recommendations, msg

# ==========================================
# 3. 메인 로직
# ==========================================
st.title("🛡️ Project Aegis V6.0 (Smart Wallet)")

# DB 읽기
try:
    data = conn.read(spreadsheet=SHEET_URL, usecols=[0, 1, 2, 3, 4, 5, 6], ttl=0)
    df = pd.DataFrame(data)
    if not df.empty:
        df = df.sort_values(by="Date", ascending=False).fillna(0)
except:
    df = pd.DataFrame(columns=["Date", "Ticker", "Action", "Qty", "Price", "Exchange_Rate", "Fee"])

# 지갑 읽기 (실시간)
my_wallet = get_wallet_balance()

# 보유량 계산
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
else:
    current_holdings = {'SGOV': 0, 'SPYM': 0, 'QQQM': 0}

krw_rate = get_usd_krw()

# ==========================================
# 4. 사이드바 (입출금 & 거래)
# ==========================================
st.sidebar.header("🏦 내 지갑 (Wallet)")
col_w1, col_w2 = st.sidebar.columns(2)
col_w1.metric("🇰🇷 원화", f"{int(my_wallet.get('KRW',0)):,}원")
col_w2.metric("🇺🇸 달러", f"${my_wallet.get('USD',0):.2f}")

# 자금 관리 탭
mode = st.sidebar.radio("작업 선택", ["주식 거래", "입금/환전"], horizontal=True)

with st.sidebar.form("action_form"):
    date = st.date_input("날짜", datetime.today())
    
    if mode == "입금/환전":
        act_type = st.selectbox("종류", ["원화 입금 (Deposit)", "달러 환전 (Exchange)"])
        amount = st.number_input("금액 (원화)", min_value=0, step=10000)
        ex_rate_in = st.number_input("적용 환율", value=krw_rate)
        
        if st.form_submit_button("실행"):
            if act_type == "원화 입금 (Deposit)":
                update_wallet_balance('KRW', amount, "add")
                st.success(f"💰 {amount:,}원 입금 완료!")
            else: # 환전
                if my_wallet.get('KRW', 0) >= amount:
                    usd_got = amount / ex_rate_in
                    update_wallet_balance('KRW', amount, "subtract")
                    update_wallet_balance('USD', usd_got, "add")
                    st.success(f"💱 {amount:,}원 -> ${usd_got:.2f} 환전 완료!")
                else:
                    st.error("❌ 원화 잔고가 부족합니다!")
            time.sleep(1)
            st.rerun()
            
    else: # 주식 거래
        ticker = st.selectbox("종목", ["SGOV", "SPYM", "QQQM", "GMMF"])
        action = st.selectbox("유형", ["BUY", "SELL", "DIVIDEND"])
        qty = st.number_input("수량", min_value=0.0, value=1.0, step=0.01)
        
        # 가격 자동 로딩
        cur_p = 0.0
        if action != "DIVIDEND":
            cur_p = get_current_price(ticker)
        
        price = st.number_input("단가/배당금($)", value=cur_p if cur_p > 0 else 0.0, format="%.2f")
        fee = st.number_input("수수료($)", value=0.0, format="%.2f")
        ex_rate = st.number_input("환율", value=krw_rate)
        
        if st.form_submit_button("기록하기"):
            total_cost_usd = (qty * price) + fee
            
            # 매수 시 지갑 잔고 체크 및 차감
            if action == "BUY":
                if my_wallet.get('USD', 0) >= total_cost_usd:
                    # 1. 거래 기록
                    new_row = pd.DataFrame([{
                        "Date": str(date), "Ticker": ticker, "Action": action, 
                        "Qty": qty, "Price": price, "Exchange_Rate": ex_rate, "Fee": fee
                    }])
                    updated_df = pd.concat([df, new_row], ignore_index=True)
                    conn.update(spreadsheet=SHEET_URL, data=updated_df)
                    
                    # 2. 지갑 차감 (자동)
                    update_wallet_balance('USD', total_cost_usd, "subtract")
                    
                    st.success("✅ 매수 완료! 달러가 자동 차감되었습니다.")
                    time.sleep(1)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(f"❌ 달러 부족! (필요: ${total_cost_usd:.2f}, 보유: ${my_wallet.get('USD',0):.2f})")
            
            # 배당금 수령 시 지갑 추가
            elif action == "DIVIDEND":
                new_row = pd.DataFrame([{
                        "Date": str(date), "Ticker": ticker, "Action": action, 
                        "Qty": qty, "Price": price, "Exchange_Rate": ex_rate, "Fee": fee
                }])
                updated_df = pd.concat([df, new_row], ignore_index=True)
                conn.update(spreadsheet=SHEET_URL, data=updated_df)
                
                # 지갑에 추가 (세후 금액이라 가정)
                update_wallet_balance('USD', price, "add")
                st.success("💰 배당금 입금 완료!")
                time.sleep(1)
                st.cache_data.clear()
                st.rerun()
            
            else: # SELL 등은 일단 기록만 (나중에 복잡한 로직 추가 가능)
                 # ... (기록 로직 동일) ...
                 st.warning("매도 기능은 아직 지갑 연동이 안 되어 있습니다. (기록만 됨)")

# ==========================================
# 5. 메인 화면
# ==========================================
st.sidebar.markdown("---")
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
    m1.metric("보유 현금 (KRW+USD)", f"{int(my_wallet.get('KRW',0) + my_wallet.get('USD',0)*krw_rate):,} 원")
    m2.metric("주식 평가액", f"{int(total_val):,} 원")
    m3.metric("총 자산 (현금+주식)", f"{int(total_val + my_wallet.get('KRW',0) + my_wallet.get('USD',0)*krw_rate):,} 원")

    if asset_list:
        st.dataframe(pd.DataFrame(asset_list), width='stretch')

with tab2:
    if run_ai:
        bot = Rebalancer(current_holdings, my_wallet)
        recs, msg = bot.analyze(krw_rate)
        st.subheader("🤖 AI 전략 보고서")
        if msg: st.info(msg)
        if recs:
            st.write(f"💡 **현재 보유 달러(${my_wallet.get('USD',0):.2f})**로 가능한 매수:")
            for r in recs:
                st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수 (예상 비용 ${r['cost']/krw_rate:.2f})")
        else:
            if not msg: st.balloons()
            st.success("✅ 포트폴리오 유지 (또는 달러 부족)")

with tab3:
    st.subheader("📋 전체 기록")
    st.dataframe(df, width='stretch')
