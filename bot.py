import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
import ta
import pytz 
import traceback
import time # 🔥 [필수] 시간 지연을 위해 추가됨
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# 1. 환경 설정
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 🔥 [설정] 봇 행동 기준
MIN_KRW_ACTION = 10000   
MIN_USD_ACTION = 100     
REVERSE_EX_GAP = 15      

# 🔥 [설정] 현실적인 수수료율 (Spread Rate: 0.9%)
SPREAD_RATE = 0.009 

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

def is_banking_hours():
    kst_tz = pytz.timezone('Asia/Seoul')
    now_kst = datetime.now(kst_tz)
    if now_kst.weekday() >= 5: return False
    if 9 <= now_kst.hour < 16: return True
    return False

# 🔥 [강화 1] 구글 시트 503 에러 방어 (재접속 로직)
def get_sheet_data():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            sheet = client.open_by_url(SHEET_URL)
            
            sheet_name = "Sheet1"
            try: sheet.worksheet("Sheet1")
            except: sheet_name = "시트1"
            
            # API 호출 사이에 짧은 휴식
            df_stock = pd.DataFrame(sheet.worksheet(sheet_name).get_all_records())
            time.sleep(1) 
            df_cash = pd.DataFrame(sheet.worksheet("CashFlow").get_all_records())
            
            return df_stock, df_cash
        except Exception as e:
            print(f"⚠️ 시트 연결 실패 ({attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5) # 실패 시 5초 대기 후 재시도
                continue
            else:
                raise e

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

def calculate_my_avg_exchange_rate(df_cash, df_stock):
    has_stock = False
    if not df_stock.empty:
        df_stock['Qty'] = pd.to_numeric(df_stock['Qty'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        total_buy = df_stock[df_stock['Action'] == 'BUY']['Qty'].sum()
        total_sell = df_stock[df_stock['Action'] == 'SELL']['Qty'].sum()
        if (total_buy - total_sell) > 0.001: has_stock = True

    if df_cash.empty: return 1450.0
    df = df_cash.copy()
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values('Date')
    
    total_usd_held = 0.0
    total_krw_spent = 0.0
    last_valid_rate = 1450.0 
    
    for _, row in df.iterrows():
        try:
            amt_krw = float(str(row['Amount_KRW']).replace(',', ''))
            amt_usd = float(str(row['Amount_USD']).replace(',', ''))
        except: continue
            
        if row['Type'] == 'Exchange':
            total_usd_held += amt_usd
            total_krw_spent += amt_krw
            if total_usd_held > 0: last_valid_rate = total_krw_spent / total_usd_held
            
        elif row['Type'] == 'Exchange_USD_to_KRW':
            if total_usd_held > 0:
                current_avg = total_krw_spent / total_usd_held
                sell_usd = min(amt_usd, total_usd_held) 
                total_usd_held -= sell_usd
                total_krw_spent -= (sell_usd * current_avg)
            
            if total_usd_held <= 0.1:
                total_usd_held = 0
                total_krw_spent = 0

    if total_usd_held > 0: return total_krw_spent / total_usd_held
    if has_stock: return last_valid_rate
    return 1450.0

# 🔥 [강화 2] 야후 파이낸스 Rate Limit 방어 (안전하게 데이터 가져오기)
def get_market_data_safe(ticker, period="2mo"):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker).history(period=period)
            if df.empty: raise ValueError(f"{ticker} 데이터 없음")
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2) # 실패 시 2초 대기
                continue
            return pd.DataFrame()

def analyze_market(ticker):
    # 안전한 데이터 가져오기 함수 사용
    df = get_market_data_safe(ticker, "2mo")
    if len(df) < 14: return 0, 50
    return df['Close'].iloc[-1], ta.momentum.RSIIndicator(df['Close'], window=14).rsi().iloc[-1]

def run_bot():
    try:
        is_open, status_msg = is_market_open()
        is_bank_open = is_banking_hours()
        
        df_stock, df_cash = get_sheet_data()
        
        # 🔥 [강화 3] 연속 호출 시 딜레이 추가 (과속 방지)
        vix_df = get_market_data_safe("^VIX", "5d")
        vix = vix_df['Close'].iloc[-1] if not vix_df.empty else 0
        time.sleep(1) # 1초 휴식
        
        qqqm_price, qqqm_rsi = analyze_market("QQQM")
        time.sleep(1) # 1초 휴식
        
        spym_price, spym_rsi = analyze_market("SPYM")
        time.sleep(1) # 1초 휴식
        
        # 환율 및 MA20 분석
        ex_df = get_market_data_safe("KRW=X", "1mo")
        if ex_df.empty: raise ValueError("환율 데이터 수신 실패")
        
        curr_rate = ex_df['Close'].iloc[-1]
        ma_20 = ex_df['Close'].mean() # 최근 1달 평균 환율
        
        if curr_rate == 0 or qqqm_price == 0: raise ValueError("시장 데이터 수신 실패 (가격 0)")

        my_avg_rate = calculate_my_avg_exchange_rate(df_cash, df_stock)
        my_krw, my_usd = calculate_balances(df_cash, df_stock)
        
        # 배당금 총액 계산
        total_div = 0.0
        if not df_stock.empty:
            df_stock['Price'] = pd.to_numeric(df_stock['Price'], errors='coerce').fillna(0)
            df_stock['Fee'] = pd.to_numeric(df_stock['Fee'], errors='coerce').fillna(0)
            divs = df_stock[df_stock['Action'] == 'DIVIDEND']
            total_div = (divs['Price'] - divs['Fee']).sum()

        real_buy_rate = curr_rate * (1 + SPREAD_RATE)  
        real_sell_rate = curr_rate * (1 - SPREAD_RATE) 

        msg = f"📡 **[Aegis Smart Strategy]**\n"
        msg += f"📅 {datetime.now().strftime('%m/%d %H:%M')} ({status_msg})\n"
        msg += f"💰 잔고: ￦{int(my_krw):,} / ${my_usd:.2f}\n"
        msg += f"❄️ 배당 스노우볼: ${total_div:.2f}\n"
        msg += f"📊 지표: VIX {vix:.1f} / Q-RSI {qqqm_rsi:.1f}\n\n"

        should_send = False

        # 1. 환전 (살 때) - 부자의 딜레마 해결
        buy_diff = real_buy_rate - my_avg_rate
        
        # 상대적 저평가 (Historic Cheapness) 조건
        is_cheap_historically = real_buy_rate < (ma_20 - 5.0)

        if my_krw >= MIN_KRW_ACTION and is_bank_open: 
            suggest_percent = 0
            strategy_msg = ""
            
            # Case A: 절대적 저평가 (내 평단보다 쌈)
            if -15 < buy_diff <= -5: suggest_percent = 30; strategy_msg = "📉 환율 소폭 하락."
            elif -30 < buy_diff <= -15: suggest_percent = 50; strategy_msg = "📉📉 환율 매력적!"
            elif buy_diff <= -30: suggest_percent = 100; strategy_msg = "💎 [바겐세일] 역대급 환율!"
            
            # Case B: 상대적 저평가 (내 평단보단 비싸지만 MA20보단 쌈)
            elif buy_diff > -5 and is_cheap_historically:
                suggest_percent = 30
                strategy_msg = f"🌊 [물결 타기] 평단보단 높지만,\n최근 평균({ma_20:,.0f}원)보다 저렴합니다."
                
            if suggest_percent > 0:
                amount_to_exchange = my_krw * (suggest_percent / 100)
                msg += f"💵 **[환전 추천]** (예상 {real_buy_rate:,.0f}원)\n{strategy_msg}\n👉 추천: {int(amount_to_exchange):,}원\n\n"
                should_send = True

        # 2. 역환전 (팔 때)
        sell_diff = real_sell_rate - my_avg_rate
        is_stock_cheap = (qqqm_rsi < 50 or vix > 25)
        
        if my_usd >= 100 and sell_diff >= REVERSE_EX_GAP and not is_stock_cheap and is_bank_open:
            msg += f"🇰🇷 **[역환전 기회]**\n• 수수료 떼고도 {sell_diff:+.0f}원 이득!\n👉 달러 일부 원화 환전.\n\n"
            should_send = True

        # 3. AI 포트폴리오 매수
        if my_usd >= MIN_USD_ACTION and (is_open or vix > 30):
            if qqqm_rsi < 40:
                buy_mode = "소수점 매수" if my_usd < qqqm_price else "1주 이상 매수"
                intensity = "30%" if qqqm_rsi >= 30 else "50% (공포매수)"
                msg += f"📈 **[QQQM 매수 추천]**\n• AI 판단: 조정장 (RSI {qqqm_rsi:.1f})\n• 현재가: ${qqqm_price:.2f}\n👉 달러의 {intensity} {buy_mode} 진행!\n\n"
                should_send = True
            elif spym_rsi < 40: 
                buy_mode = "소수점 매수" if my_usd < spym_price else "1주 이상 매수"
                msg += f"🛡️ **[SPYM 매수 추천]**\n• AI 판단: S&P500 조정 (RSI {spym_rsi:.1f})\n👉 달러의 30% {buy_mode} 진행!\n\n"
                should_send = True
        
        if qqqm_rsi > 70 and is_open:
            msg += "🔴 **[QQQM 과열]** (RSI > 70). 수익 실현 고려.\n"
            should_send = True

        if should_send:
            send_telegram(msg)

    except Exception as e:
        error_msg = f"⚠️ **[Aegis System Error]**\n봇 실행 중 문제가 발생했습니다.\n\n🔻 에러 내용:\n{str(e)}\n\n👉 GitHub Actions 로그를 확인해주세요."
        send_telegram(error_msg)
        print(traceback.format_exc())

if __name__ == "__main__":
    run_bot()
