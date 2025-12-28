import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
import ta
import pytz 
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# 1. 환경 설정
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 🔥 [설정] 봇 최소 반응 금액 (이월된 자금 포함, 이 정도는 있어야 봇이 움직임)
MIN_KRW_ACTION = 300000  # 원화 30만원 이상일 때 환전 조언
MIN_USD_ACTION = 300     # 달러 $300 이상일 때 매수 조언

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=data)
    except Exception as e: print(f"전송 실패: {e}")

def is_market_open():
    nyc_tz = pytz.timezone('America/New_York')
    now_nyc = datetime.now(nyc_tz)
    if now_nyc.weekday() >= 5: return False, "주말 (휴장)"
    market_start = now_nyc.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now_nyc.replace(hour=16, minute=30, second=0, microsecond=0)
    if market_start <= now_nyc <= market_end: return True, "장 운영 중 🟢"
    return False, "장 마감 🔴"

def get_sheet_data():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_url(SHEET_URL)
        sheet_name = "Sheet1"
        try: sheet.worksheet("Sheet1")
        except: sheet_name = "시트1"
        return pd.DataFrame(sheet.worksheet(sheet_name).get_all_records()), pd.DataFrame(sheet.worksheet("CashFlow").get_all_records())
    except: return pd.DataFrame(), pd.DataFrame()

# 🔥 [NEW] 잔고 계산 로직 (App과 동일하게 출금/역환전 반영)
def calculate_balances(df_cash, df_stock):
    krw = 0; usd = 0
    if not df_cash.empty:
        df_cash['Amount_KRW'] = pd.to_numeric(df_cash['Amount_KRW'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        df_cash['Amount_USD'] = pd.to_numeric(df_cash['Amount_USD'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        
        # 1. 입금/환전
        krw += df_cash[df_cash['Type'] == 'Deposit']['Amount_KRW'].sum()
        krw -= df_cash[df_cash['Type'] == 'Exchange']['Amount_KRW'].sum()
        usd += df_cash[df_cash['Type'] == 'Exchange']['Amount_USD'].sum()
        
        # 2. 역환전/출금 (봇도 이제 이 돈이 없다는 걸 암)
        krw += df_cash[df_cash['Type'] == 'Exchange_USD_to_KRW']['Amount_KRW'].sum()
        usd -= df_cash[df_cash['Type'] == 'Exchange_USD_to_KRW']['Amount_USD'].sum()
        krw -= df_cash[df_cash['Type'] == 'Withdraw']['Amount_KRW'].sum()

    if not df_stock.empty:
        for col in ['Qty', 'Price', 'Fee']:
            df_stock[col] = pd.to_numeric(df_stock[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        buys = df_stock[df_stock['Action'] == 'BUY']
        usd -= ((buys['Qty'] * buys['Price']) + buys['Fee']).sum()
        sells = df_stock[df_stock['Action'] == 'SELL']
        usd += ((sells['Qty'] * sells['Price']) - sells['Fee']).sum()
        divs = df_stock[df_stock['Action'] == 'DIVIDEND']
        usd += (divs['Price'] - divs['Fee']).sum()
        
    return krw, usd

def calculate_my_avg_rate(df_cash):
    if df_cash.empty: return 1450.0
    # 평단은 '매수' 기록 기준
    exchanges = df_cash[df_cash['Type'] == 'Exchange']
    if exchanges.empty: return 1450.0
    total_krw = pd.to_numeric(exchanges['Amount_KRW'].astype(str).str.replace(',', ''), errors='coerce').sum()
    total_usd = pd.to_numeric(exchanges['Amount_USD'].astype(str).str.replace(',', ''), errors='coerce').sum()
    return total_krw / total_usd if total_usd else 1450.0

def analyze_market(ticker):
    try:
        df = yf.Ticker(ticker).history(period="2mo")
        if len(df) < 14: return 0, 50
        return df['Close'].iloc[-1], ta.momentum.RSIIndicator(df['Close'], window=14).rsi().iloc[-1]
    except: return 0, 50

def run_bot():
    is_open, status_msg = is_market_open()
    df_stock, df_cash = get_sheet_data()
    if df_stock.empty: return

    # 데이터 분석
    try:
        vix = yf.Ticker("^VIX").history(period="5d")['Close'].iloc[-1]
        qqqm_price, qqqm_rsi = analyze_market("QQQM")
        curr_rate = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]
    except: return

    # 자산 상태 (이월 자금 포함된 실제 잔고)
    my_avg_rate = calculate_my_avg_rate(df_cash)
    my_krw, my_usd = calculate_balances(df_cash, df_stock)
    rate_diff = curr_rate - my_avg_rate
    
    msg = f"📡 **[Aegis Smart Strategy]**\n"
    msg += f"📅 {datetime.now().strftime('%m/%d %H:%M')} ({status_msg})\n"
    msg += f"💰 보유 총알: ￦{int(my_krw):,} / ${my_usd:.2f}\n\n"

    should_send = False

    # ============================================
    # 🧠 전략 1. 스마트 분할 환전 (Smart Split)
    # ============================================
    # "이번 달 예산"이 아니라 "현재 내 원화 잔고(my_krw)"를 기준으로 판단함 (이월 자금 해결!)
    if my_krw >= MIN_KRW_ACTION: 
        suggest_percent = 0
        strategy_msg = ""

        # 1단계: 조금 저렴 (-5원 ~ -15원) -> 30% 환전
        if -15 < rate_diff <= -5:
            suggest_percent = 30
            strategy_msg = "📉 환율이 소폭 하락했습니다. 보유 원화의 30%만 분할 환전하세요."
        
        # 2단계: 많이 저렴 (-15원 ~ -30원) -> 50% 환전
        elif -30 < rate_diff <= -15:
            suggest_percent = 50
            strategy_msg = "📉📉 환율이 매력적입니다! 보유 원화의 절반(50%)을 확보하세요."
            
        # 3단계: 대폭락 (-30원 이상) -> 100% 환전
        elif rate_diff <= -30:
            suggest_percent = 100
            strategy_msg = "💎 **[바겐세일]** 역대급 기회입니다. 원화를 모두 달러로 바꾸세요!"
            
        if suggest_percent > 0:
            amount_to_exchange = my_krw * (suggest_percent / 100)
            msg += f"💵 **[환전 추천]** (현재 {curr_rate:,.0f}원)\n"
            msg += f"{strategy_msg}\n"
            msg += f"👉 **추천 금액: {int(amount_to_exchange):,}원**\n\n"
            should_send = True

    # ============================================
    # 🧠 전략 2. 스마트 매매 (Buy & Sell)
    # ============================================
    # 매수 로직 (달러 있을 때)
    if my_usd >= MIN_USD_ACTION and (is_open or vix > 30):
        if 30 <= qqqm_rsi < 40:
            msg += "📈 **[매수 추천]** QQQM 조정장 진입. 달러의 30% 매수.\n"
            should_send = True
        elif qqqm_rsi < 30:
            msg += "😱 **[공포 매수]** 과매도 구간입니다. 달러의 50% 과감하게 매수!\n"
            should_send = True
    
    # 🔥 [NEW] 매도(수익 실현) 로직 추가 (별개로 작동)
    # 주식을 보유하고 있을 때만 작동해야 하지만, 단순화를 위해 RSI 기준으로 조언
    if qqqm_rsi > 70 and is_open:
        msg += "🔴 **[매도 경고]** QQQM이 과열되었습니다 (RSI > 70).\n"
        msg += "👉 수익 실현(리밸런싱)을 고려하거나, 추가 매수를 멈추세요.\n"
        should_send = True

    if should_send:
        send_telegram(msg)

if __name__ == "__main__":
    run_bot()
