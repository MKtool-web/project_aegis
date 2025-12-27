import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
from oauth2client.service_account import ServiceAccountCredentials

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=data)

def get_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # 1. 거래 내역 읽기 (Sheet1)
    sheet = client.open_by_url(SHEET_URL)
    df = pd.DataFrame(sheet.sheet1.get_all_records())
    
    # 2. 지갑 잔고 읽기 (Wallet)
    try:
        ws_wallet = sheet.worksheet("Wallet")
        wallet_data = ws_wallet.get_all_records()
        wallet = {row['Currency']: row['Amount'] for row in wallet_data}
    except:
        wallet = {'KRW': 0, 'USD': 0}
        
    return df, wallet

def get_market_price(ticker):
    try:
        return float(yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1])
    except:
        return 0.0

def run_bot():
    df, wallet = get_data()
    krw_rate = get_market_price("KRW=X")
    if krw_rate < 1000: krw_rate = 1450.0 # 에러 방지
    
    # 내 평균 환전 단가 계산
    buys = df[df['Action'] == 'BUY']
    if not buys.empty:
        total_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
        total_usd = (buys['Qty'] * buys['Price'] + buys['Fee']).sum()
        my_avg_rate = total_krw / total_usd if total_usd > 0 else 1450.0
    else:
        my_avg_rate = 1450.0
        
    gap_ratio = krw_rate / my_avg_rate
    my_krw = wallet.get('KRW', 0)
    my_usd = wallet.get('USD', 0)
    
    msg = ""
    send_msg = False
    
    # 1. 환전 기회 (내 돈이 있을 때만 알림!)
    if gap_ratio < 0.985 and my_krw > 100000:
        msg += f"✅ [환전 찬스] 환율 {krw_rate:,.0f}원 (내 평단대비 저렴)\n"
        msg += f"💡 보유 원화 {int(my_krw):,}원 중 일부를 환전하세요!\n"
        send_msg = True
        
    # 2. 주식 매수 기회 (달러가 있을 때만!)
    qqqm_p = get_market_price("QQQM")
    if my_usd > qqqm_p: # 1주라도 살 돈이 있으면
        # (여기에 주가 하락 조건 등 추가 가능)
        pass # 일단 생략

    # 3. 긴급 공지 (조건 무관)
    if krw_rate > 1460:
        msg += f"⚠️ [고환율] 1,460원 돌파. 당분간 환전 금지.\n"
        send_msg = True

    if send_msg:
        send_telegram(msg)

if __name__ == "__main__":
    run_bot()
