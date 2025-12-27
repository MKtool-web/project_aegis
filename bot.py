import os
import json
import gspread
import requests
import yfinance as yf
from oauth2client.service_account import ServiceAccountCredentials

# 1. 설정 로드 (GitHub Secrets에서 가져옴)
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=data)

def get_exchange_rate():
    try:
        return yf.Ticker("KRW=X").history(period="1d")['Close'].iloc[-1]
    except:
        return 1450.0

def run_bot():
    # 구글 시트 연결
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # 환율 체크
    rate = get_exchange_rate()
    msg = f"🛡️ [Aegis 모닝 브리핑]\n현재 환율: {rate:,.0f}원\n"
    
    # 전략 판단
    if rate < 1380:
        msg += "\n🔥 [긴급] 환율이 많이 내렸습니다(1380원↓). \n달러 매수 혹은 미국 주식 추가 매수 타이밍입니다!"
        send_telegram(msg) # 중요할 때만 알림 (또는 매일 받으려면 조건 제거)
    elif rate > 1460:
        msg += "\n⚠️ 환율이 너무 높습니다. 당분간 환전은 자제하세요."
        # send_telegram(msg) # 필요하면 주석 해제

    # (테스트용) 무조건 한번 보내보기
    # send_telegram(msg)
    print("Bot finished.")

if __name__ == "__main__":
    run_bot()
