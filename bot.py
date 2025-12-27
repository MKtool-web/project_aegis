import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
import ta
import pytz # 시간대 처리 (서머타임 자동 적용)
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# 1. 환경 설정
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=data)

# 🔥 [NEW] 스마트 시장 시간 체크 (서머타임 자동 해결)
def is_market_open():
    # 미국 동부 시간(NYC) 기준 설정
    nyc_tz = pytz.timezone('America/New_York')
    now_nyc = datetime.now(nyc_tz)
    
    # 1. 주말 체크 (0:월 ~ 6:일) -> 토(5), 일(6)은 휴장
    if now_nyc.weekday() >= 5: 
        return False, "주말 (휴장)"

    # 2. 장 운영 시간 체크 (09:30 ~ 16:00)
    # 데이터 수집을 위해 장전/장후 30분 정도 여유를 두고 체크
    market_start = now_nyc.replace(hour=9, minute=0, second=0, microsecond=0)
    market_end = now_nyc.replace(hour=16, minute=30, second=0, microsecond=0)
    
    if market_start <= now_nyc <= market_end:
        return True, "장 운영 중 (Open)"
    else:
        return False, "장 마감 (Closed)"

def get_sheet_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(SHEET_URL)
    
    # 시트 이름 스마트 감지
    sheet_name = "Sheet1"
    try: sheet.worksheet("Sheet1")
    except: sheet_name = "시트1"
    
    stock_data = sheet.worksheet(sheet_name).get_all_records()
    cash_data = sheet.worksheet("CashFlow").get_all_records()
    
    return pd.DataFrame(stock_data), pd.DataFrame(cash_data)

def analyze_market(ticker):
    # 최근 2달 데이터 (RSI 계산 안정성 확보)
    df = yf.Ticker(ticker).history(period="2mo")
    if len(df) < 14: return 0, 50 # 데이터 부족 시 기본값
    
    # 현재가
    price = df['Close'].iloc[-1]
    
    # RSI 계산 (14일 기준)
    rsi = ta.momentum.RSIIndicator(df['Close'], window=14).rsi().iloc[-1]
    
    return price, rsi

def get_vix():
    try:
        vix = yf.Ticker("^VIX").history(period="5d")
        return vix['Close'].iloc[-1]
    except: return 0

def run_bot():
    # 1. 시장 시간 체크
    is_open, status_msg = is_market_open()
    
    # 2. 데이터 수집
    df_stock, df_cash = get_sheet_data()
    
    # 3. 시장 분석 (RSI & VIX)
    qqqm_price, qqqm_rsi = analyze_market("QQQM")
    vix = get_vix()
    krw = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]

    # 4. AI 판단 로직 (신호 감지)
    signals = []
    
    # VIX (공포지수)
    if vix > 30:
        signals.append("😱 **[경고] 공포 지수 급등!** (VIX > 30)")
        signals.append("   → 저점 매수 기회일 수 있습니다.")
    elif vix < 12:
        signals.append("😌 **[주의] 시장이 너무 평온합니다.**")

    # RSI (QQQM)
    if qqqm_rsi < 30:
        signals.append(f"🟢 **[매수 기회] QQQM 과매도 구간** (RSI {qqqm_rsi:.1f})")
    elif qqqm_rsi > 70:
        signals.append(f"🔴 **[매도 주의] QQQM 과열 구간** (RSI {qqqm_rsi:.1f})")
    else:
        signals.append(f"⚪ QQQM 상태: 중립 (RSI {qqqm_rsi:.1f})")

    # 5. 리포트 작성
    msg = f"📡 **[Aegis Market Watch]**\n"
    msg += f"🕒 상태: {status_msg}\n"
    msg += f"📅 날짜: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    
    msg += f"💵 환율: {krw:,.0f}원\n"
    msg += f"📊 VIX: {vix:.2f}\n"
    msg += f"📈 QQQM: ${qqqm_price:.2f}\n\n"
    
    msg += "🤖 **[AI 분석 리포트]**\n"
    for s in signals:
        msg += s + "\n"
        
    # 긴급 호출 (공포장 or 과매도 일 때만 강조)
    if qqqm_rsi < 30 or vix > 30:
        msg += "\n🚨 **Action Required: 앱을 확인하세요!**"
        
    send_telegram(msg)

if __name__ == "__main__":
    run_bot()
