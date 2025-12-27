import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==========================================
# 1. 환경 설정
# ==========================================
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 선생님의 이번 달 투자 예정 금액 (예: 2월까지 40만원)
# 추후 앱에서 'Spare Cash'를 읽어오도록 고도화 가능
MONTHLY_BUDGET_KRW = 400000 

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=data)

# ==========================================
# 2. 데이터 수집 엔진
# ==========================================
def get_market_data():
    """실시간 시장 데이터 수집"""
    tickers = {
        "KRW": "KRW=X",
        "QQQM": "QQQM",
        "SPYM": "SPYM",
        "SGOV": "SGOV"
    }
    data = {}
    for name, ticker in tickers.items():
        try:
            # period='2d'로 해서 어제와 오늘 비교 (등락폭 계산)
            hist = yf.Ticker(ticker).history(period="2d")
            current = float(hist['Close'].iloc[-1])
            prev = float(hist['Close'].iloc[0]) if len(hist) > 1 else current
            change_pct = ((current - prev) / prev) * 100
            
            data[name] = {"price": current, "change": change_pct}
        except:
            data[name] = {"price": 0.0, "change": 0.0}
            
    # 환율 에러 시 기본값
    if data["KRW"]["price"] < 1000: data["KRW"]["price"] = 1450.0
    
    return data

def get_my_portfolio():
    """구글 시트에서 내 장부 분석"""
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    sheet = client.open_by_url(SHEET_URL).sheet1
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    
    if df.empty:
        return 0, 0, 0 # 데이터 없음
    
    # 내 평균 환전 단가 계산 (가중 평균)
    # 총 투입 원화(환전액) / 총 투입 달러
    # (BUY와 DIVIDEND만 고려, SELL은 복잡하므로 일단 제외하거나 추후 정교화)
    buys = df[df['Action'] == 'BUY']
    
    total_krw_in = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
    total_usd_in = (buys['Qty'] * buys['Price'] + buys['Fee']).sum()
    
    my_avg_rate = total_krw_in / total_usd_in if total_usd_in > 0 else 1450.0
    
    # 현재 보유 달러 가치 (추산)
    current_holdings_usd = total_usd_in # 매도 없다고 가정 시
    
    return my_avg_rate, current_holdings_usd

# ==========================================
# 3. AI 전략 판단 엔진 (핵심)
# ==========================================
def analyze_strategy():
    market = get_market_data()
    my_avg_rate, my_usd_assets = get_my_portfolio()
    
    cur_rate = market["KRW"]["price"]
    qqqm_change = market["QQQM"]["change"]
    
    # 괴리율 계산 (현재환율 / 내평단)
    # 1.0보다 작으면 내 평단보다 싼 것 (이득), 크면 비싼 것 (손해)
    gap_ratio = cur_rate / my_avg_rate
    
    msg = f"🛡️ [Aegis AI 전략보고]\n"
    msg += f"• 현재환율: {cur_rate:,.0f}원\n"
    msg += f"• 내 평단가: {my_avg_rate:,.0f}원 (괴리율 {gap_ratio*100:.1f}%)\n"
    msg += f"• QQQM변동: {qqqm_change:+.2f}%\n"
    msg += "-" * 20 + "\n"

    # 🔥 판단 로직 (Threshold 없는 상대 평가)
    
    signal_level = "HOLD" # 기본 관망
    
    # Case 1: 환율 바겐세일 (내 평단보다 1.5% 이상 저렴)
    if gap_ratio < 0.985:
        signal_level = "BUY_USD"
        msg += "✅ [환전 찬스] 환율이 내 평단보다 저렴합니다!\n"
        msg += "👉 전략: 여유 현금을 달러로 환전하세요.\n"
        msg += "👉 추천: 환전 후 SPYM/QQQM 비중 확대 (6:4 비율)\n"
        
        # 구체적 매수 수량 제안
        can_buy_amt = MONTHLY_BUDGET_KRW * 0.5 # 예산의 절반 투입 가정
        can_buy_usd = can_buy_amt / cur_rate
        qqqm_qty = int((can_buy_usd * 0.6) / market["QQQM"]["price"])
        msg += f"💡 예시: {int(can_buy_amt/10000)}만원 환전 시 -> QQQM 약 {qqqm_qty}주 매수 가능\n"

    # Case 2: 주식 폭락장 (환율 무시하고 줍줍)
    elif qqqm_change < -2.5:
        signal_level = "BUY_STOCK"
        msg += "🚨 [공포 탐지] QQQM이 급락 중입니다(-2.5%↓)!\n"
        msg += "👉 전략: 환율이 조금 비싸더라도 환전해서 주식을 사야 할 때입니다.\n"
        msg += "👉 추천: 보유 중인 SGOV가 있다면 즉시 매도하여 QQQM 매수\n"

    # Case 3: 환율 고공행진 (내 평단보다 2% 이상 비쌈)
    elif gap_ratio > 1.02:
        signal_level = "DEFENSIVE"
        msg += "⚠️ [고환율 경고] 내 평단보다 환율이 높습니다.\n"
        if qqqm_change > 0:
            msg += "👉 전략: 무리한 환전 금지. 원화 채굴(예금/CMA) 집중.\n"
            msg += "👉 추천: 달러가 있다면 SGOV 매수하여 이자 수익 확보\n"
        else:
            msg += "👉 전략: 주식이 내렸지만 환율이 너무 비쌉니다. 신중하세요.\n"

    # Case 4: 평범한 상황
    else:
        msg += "☕ [관망] 특이사항 없습니다. 시장을 지켜보는 중입니다.\n"

    # 텔레그램 전송 (중요한 신호일 때만 보내거나, 하루 한 번 요약용)
    # 현재는 테스트를 위해 무조건 전송 (나중엔 if signal_level != "HOLD": 로 변경)
    send_telegram(msg)

if __name__ == "__main__":
    try:
        analyze_strategy()
        print("Analysis Complete.")
    except Exception as e:
        print(f"Error: {e}")
        # 에러 나면 나한테 알림 (디버깅용)
        # send_telegram(f"❌ 봇 에러 발생: {e}")
