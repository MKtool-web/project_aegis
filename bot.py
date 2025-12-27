import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
from oauth2client.service_account import ServiceAccountCredentials

# 1. 환경 설정
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 2. 텔레그램 전송 함수
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=data)

# 3. 데이터 수집 (구글 시트 + 야후 파이낸스)
def get_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    sheet = client.open_by_url(SHEET_URL)
    
    # 거래 내역 (평단가 계산용)
    df = pd.DataFrame(sheet.sheet1.get_all_records())
    
    # 지갑 잔고 (매수 여력 확인용)
    try:
        ws_wallet = sheet.worksheet("Wallet")
        wallet_data = ws_wallet.get_all_records()
        wallet = {row['Currency']: row['Amount'] for row in wallet_data}
    except:
        wallet = {'KRW': 0, 'USD': 0}
        
    return df, wallet

def get_market_info(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="2d")
        price = float(hist['Close'].iloc[-1])
        prev = float(hist['Close'].iloc[0]) if len(hist) > 1 else price
        change = ((price - prev) / prev) * 100
        return price, change
    except:
        return 0.0, 0.0

# 4. 뇌 (판단 로직)
def run_bot():
    df, wallet = get_data()
    
    # 시장 데이터 수집
    krw_price, _ = get_market_info("KRW=X")
    qqqm_price, qqqm_change = get_market_info("QQQM")
    sgov_price, _ = get_market_info("SGOV")
    
    if krw_price < 1000: krw_price = 1450.0 # 에러 방지용
    
    # 내 평단가 계산
    buys = df[df['Action'] == 'BUY']
    if not buys.empty:
        total_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
        total_usd = (buys['Qty'] * buys['Price'] + buys['Fee']).sum()
        my_avg_rate = total_krw / total_usd if total_usd > 0 else 1450.0
    else:
        my_avg_rate = 1450.0
        
    # 괴리율 (현재환율 / 내평단)
    gap_ratio = krw_price / my_avg_rate
    
    my_krw = wallet.get('KRW', 0)
    my_usd = wallet.get('USD', 0)
    
    msg = ""
    should_send = False

    # ---------------------------------------------------
    # 상황 1: 환전 찬스 (환율이 쌀 때)
    # ---------------------------------------------------
    # 조건: 내 평단보다 1.5% 이상 싸고 & 원화가 10만원 이상 있을 때
    if gap_ratio < 0.985 and my_krw >= 100000:
        msg += f"✅ [환전 기회] 환율 {krw_price:,.0f}원 (내 평단대비 저렴)\n"
        msg += f"💰 보유 원화: {int(my_krw):,}원\n"
        
        # 환전 추천 금액 계산 (50% 환전 가정)
        recommend_exchange = my_krw * 0.5
        msg += f"👉 추천: {int(recommend_exchange):,}원 정도를 달러로 환전해 두세요.\n\n"
        should_send = True

    # ---------------------------------------------------
    # 상황 2: 주식 매수 찬스 (달러가 있을 때)
    # ---------------------------------------------------
    # 조건: 달러가 있고 & (주식이 폭락했거나 OR 그냥 적립식 매수 타이밍일 때)
    # 여기서는 '달러가 충분히 쌓이면 매수 추천'하는 로직
    if my_usd > qqqm_price: # 최소 QQQM 1주 살 돈이 있으면
        buy_qty = int(my_usd // qqqm_price)
        
        # 주식 폭락 시 긴급 알림
        if qqqm_change < -2.0:
            msg += f"🚨 [주식 세일] QQQM이 {qqqm_change:.2f}% 급락 중입니다!\n"
            msg += f"💵 보유 달러: ${my_usd:.2f}\n"
            msg += f"👉 추천: 지금 바로 **QQQM {buy_qty}주**를 줍줍하세요!\n\n"
            should_send = True
        
        # 폭락은 아니지만, 달러가 많이 쌓여있을 때 (놀고 있는 돈 투자 권유)
        elif my_usd > 500: 
            msg += f"💡 [투자 제안] 놀고 있는 달러(${my_usd:.2f})가 많습니다.\n"
            msg += f"👉 추천: **QQQM {buy_qty}주** 혹은 **SGOV {int(my_usd // sgov_price)}주** 매수를 고려하세요.\n\n"
            should_send = True

    # ---------------------------------------------------
    # 상황 3: 위기 경고 (환율 폭등)
    # ---------------------------------------------------
    if krw_price > 1460:
        msg += f"⚠️ [고환율 경고] 1,460원 돌파. 당분간 환전은 멈추세요.\n"
        should_send = True

    # 메시지 전송
    if should_send:
        # 메시지 맨 위에 헤더 붙이기
        final_msg = "🛡️ [Aegis AI 알림]\n" + msg
        send_telegram(final_msg)
        print("Notification Sent.")
    else:
        print("No significant events. Silent mode.")

if __name__ == "__main__":
    run_bot()
