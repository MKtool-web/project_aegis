import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
import ta
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
    # 최근 30일 데이터 가져오기
    df = yf.Ticker(ticker).history(period="1mo")
    if len(df) < 14: return 0, 0, 50 # 데이터 부족 시 기본값
    
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
    df_stock, df_cash = get_sheet_data()
    
    # 1. 내 자산 현황 파악 (평단가 등 계산 로직 생략 - 심플하게 시장 분석 위주)
    # (필요시 V11.4의 calculate_wallet_balance_detail 로직 이식 가능)
    
    # 2. 시장 분석 (RSI & VIX)
    qqqm_price, qqqm_rsi = analyze_market("QQQM")
    spym_price, spym_rsi = analyze_market("SPYM")
    vix = get_vix()
    krw = yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]

    # 3. AI 판단 로직
    signals = []
    
    # VIX (공포지수) 체크
    if vix > 30:
        signals.append("😱 [공포 극대화] VIX 지수 폭등! 대바겐세일 가능성 높음.")
    elif vix < 12:
        signals.append("😌 [너무 평온] 시장이 너무 낙관적입니다. 급락 주의.")

    # RSI 체크 (QQQM)
    if qqqm_rsi < 30:
        signals.append(f"🟢 [QQQM 과매도] RSI {qqqm_rsi:.1f} (줍줍 찬스!)")
    elif qqqm_rsi > 70:
        signals.append(f"🔴 [QQQM 과열] RSI {qqqm_rsi:.1f} (추격 매수 자제)")
    else:
        signals.append(f"⚪ [QQQM 중립] RSI {qqqm_rsi:.1f}")

    # 4. 리포트 작성
    msg = f"📡 [Aegis Market Watch]\n{datetime.now().strftime('%Y-%m-%d')}\n\n"
    msg += f"💵 환율: {krw:,.0f}원\n"
    msg += f"📊 VIX: {vix:.2f}\n"
    msg += f"📈 QQQM: ${qqqm_price:.2f}\n\n"
    
    msg += "🤖 [AI 분석 결과]\n"
    for s in signals:
        msg += s + "\n"
        
    # 긴급 매수 신호 (RSI 30 미만 or VIX 30 초과)
    if qqqm_rsi < 30 or vix > 30:
        msg += "\n🚨 **긴급 제안: 지금은 용기를 내서 살 때입니다!**"
        
    send_telegram(msg)

if __name__ == "__main__":
    run_bot()
