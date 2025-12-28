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

# 🔥 [설정] 봇 행동 기준
MIN_KRW_ACTION = 10000   # 원화 1만원만 있어도 환전 기회 포착
MIN_USD_ACTION = 100     # 달러 $100 이상일 때 매수 조언
REVERSE_EX_GAP = 15      # 평단보다 15원 이상 비쌀 때 역환전 고려

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=data)
    except Exception as e: print(f"전송 실패: {e}")

# 미국 시장 시간 (주식 매매용)
def is_market_open():
    nyc_tz = pytz.timezone('America/New_York')
    now_nyc = datetime.now(nyc_tz)
    if now_nyc.weekday() >= 5: return False, "주말 (휴장)"
    market_start = now_nyc.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now_nyc.replace(hour=16, minute=30, second=0, microsecond=0)
    if market_start <= now_nyc <= market_end: return True, "장 운영 중 🟢"
    return False, "장 마감 🔴"

# 🔥 [NEW] 한국 은행 시간 (환전용) - 주말/야간 차단
def is_banking_hours():
    kst_tz = pytz.timezone('Asia/Seoul')
    now_kst = datetime.now(kst_tz)
    
    # 1. 주말 체크 (토=5, 일=6)
    if now_kst.weekday() >= 5: return False
    
    # 2. 시간 체크 (09:00 ~ 16:00)
    # 16시 이후엔 가환율 적용될 수 있으므로 보수적으로 잡음
    if 9 <= now_kst.hour < 16: return True
    
    return False

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

def calculate_balances(df_cash, df_stock):
    krw = 0; usd = 0
    if not df_cash.empty:
        df_cash['Amount_KRW'] = pd.to_numeric(df_cash['Amount_KRW'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        df_cash['Amount_USD'] = pd.to_numeric(df_cash['Amount_USD'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        krw += df_cash[df_cash['Type'] == 'Deposit']['Amount_KRW'].sum()
        krw -= df_cash[df_cash['Type'] == 'Exchange']['Amount_KRW'].sum()
        usd += df_cash[df_cash['Type'] == 'Exchange']['Amount_USD'].sum()
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

def calculate_my_avg_exchange_rate(df_cash):
    if df_cash.empty: return 1450.0
    buys = df_cash[df_cash['Type'] == 'Exchange']
    if buys.empty: return 1450.0
    total_krw = pd.to_numeric(buys['Amount_KRW'].astype(str).str.replace(',', ''), errors='coerce').sum()
    total_usd = pd.to_numeric(buys['Amount_USD'].astype(str).str.replace(',', ''), errors='coerce').sum()
    return total_krw / total_usd if total_usd else 1450.0

def analyze_market(ticker):
    try:
        df = yf.Ticker(ticker).history(period="2mo")
        if len(df) < 14: return 0, 50
        return df['Close'].iloc[-1], ta.momentum.RSIIndicator(df['Close'], window=14).rsi().iloc[-1]
    except: return 0, 50

def run_bot():
    is_open, status_msg = is_market_open()
    is_bank_open = is_banking_hours() # 🔥 은행 영업시간 체크
    
    df_stock, df_cash = get_sheet_data()
    if df_stock.empty: return

    try:
        vix = yf.Ticker("^VIX").history(period="5d")['Close'].iloc[-1]
        qqqm_price, qqqm_rsi = analyze_market("QQQM")
        curr_rate = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]
    except: return

    my_avg_rate = calculate_my_avg_exchange_rate(df_cash)
    my_krw, my_usd = calculate_balances(df_cash, df_stock)
    rate_diff = curr_rate - my_avg_rate
    
    msg = f"📡 **[Aegis Smart Strategy]**\n"
    msg += f"📅 {datetime.now().strftime('%m/%d %H:%M')} ({status_msg})\n"
    msg += f"💰 잔고: ￦{int(my_krw):,} / ${my_usd:.2f}\n"
    msg += f"📊 지표: VIX {vix:.1f} / RSI {qqqm_rsi:.1f}\n\n"

    should_send = False

    # ============================================
    # 🧠 전략 1. 환전 (은행 시간 AND 돈 있을 때)
    # ============================================
    # 🔥 [수정] 은행 영업시간(is_bank_open)일 때만 알림 보냄!
    if my_krw >= MIN_KRW_ACTION and is_bank_open: 
        suggest_percent = 0
        strategy_msg = ""
        if -15 < rate_diff <= -5:
            suggest_percent = 30
            strategy_msg = "📉 환율 소폭 하락. 잔고의 30% 분할 환전."
        elif -30 < rate_diff <= -15:
            suggest_percent = 50
            strategy_msg = "📉📉 환율 매력적! 잔고의 50% 확보."
        elif rate_diff <= -30:
            suggest_percent = 100
            strategy_msg = "💎 **[바겐세일]** 역대급 환율. 전액 환전!"
            
        if suggest_percent > 0:
            amount_to_exchange = my_krw * (suggest_percent / 100)
            msg += f"💵 **[환전 추천]** (현재 {curr_rate:,.0f}원)\n"
            msg += f"{strategy_msg}\n"
            msg += f"👉 추천: {int(amount_to_exchange):,}원\n\n"
            should_send = True

    # ============================================
    # 🧠 전략 2. 역환전 (은행 시간 AND 조건 충족 시)
    # ============================================
    is_stock_cheap = (qqqm_rsi < 50 or vix > 25)
    
    # 🔥 [수정] 역환전도 은행 시간에만!
    if my_usd >= 100 and rate_diff >= REVERSE_EX_GAP and not is_stock_cheap and is_bank_open:
        msg += f"🇰🇷 **[역환전 기회]** (환차익 실현)\n"
        msg += f"• 환율 평단보다 {rate_diff:+.0f}원 높음.\n"
        msg += f"• 주식 매수 타이밍 아님.\n"
        msg += f"👉 달러 일부를 원화로 환전하세요.\n\n"
        should_send = True

    # ============================================
    # 🧠 전략 3. 주식 매매 (미국 장 시간 OR 폭락장)
    # ============================================
    # 주식은 여전히 미국 장 시간(is_open)이나 폭락장(vix>30)에 알림
    if my_usd >= MIN_USD_ACTION and (is_open or vix > 30):
        if 30 <= qqqm_rsi < 40:
            msg += "📈 **[매수 추천]** 조정장 진입. 달러의 30% 매수.\n"
            should_send = True
        elif qqqm_rsi < 30:
            msg += "😱 **[공포 매수]** 과매도 구간. 달러의 50% 과감하게 매수!\n"
            should_send = True
    
    if qqqm_rsi > 70 and is_open:
        msg += "🔴 **[매도 경고]** 과열 (RSI > 70). 수익 실현 고려.\n"
        should_send = True

    if should_send:
        send_telegram(msg)

if __name__ == "__main__":
    run_bot()
