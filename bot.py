import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
import math
from oauth2client.service_account import ServiceAccountCredentials

# 1. 환경 설정
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
CHAT_ID = os.environ['TELEGRAM_CHAT_ID']
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 목표 포트폴리오 비율
TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=data)

def get_data():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(SHEET_URL)
    
    # 거래 내역 & 지갑 읽기
    df = pd.DataFrame(sheet.sheet1.get_all_records())
    try:
        wallet_data = sheet.worksheet("Wallet").get_all_records()
        wallet = {row['Currency']: row['Amount'] for row in wallet_data}
    except:
        wallet = {'KRW': 0, 'USD': 0}
    return df, wallet

def get_market_info(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d") # 5일치 데이터
        price = float(hist['Close'].iloc[-1])
        # 1일 전 대비 등락률
        prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else price
        change = ((price - prev) / prev) * 100
        return price, change
    except:
        return 0.0, 0.0

# 🔥 [핵심] 자금 집행 강도 계산 (얼마나 살 것인가?)
def calculate_spending_power(gap_ratio, stock_change):
    power = 0.5 # 기본: 가진 달러의 50%만 사용 (분할 매수 원칙)
    
    # 1. 환율이 쌀 때 (내 평단보다 저렴) -> 공격적
    if gap_ratio < 0.99: 
        power += 0.2
        
    # 2. 주식이 폭락할 때 (공포 매수) -> 아주 공격적
    if stock_change < -2.0:
        power += 0.3 # 100% 사용 가능하게 됨
        
    # 3. 환율이 너무 비쌀 때 -> 방어적
    if gap_ratio > 1.02:
        power -= 0.2
        
    # 4. 주식이 너무 올랐을 때 (과열) -> 방어적
    if stock_change > 2.0:
        power -= 0.2
        
    return max(0.1, min(power, 1.0)) # 최소 10% ~ 최대 100% 사이로 제한

def run_bot():
    df, wallet = get_data()
    
    # 시장 데이터
    krw_price, krw_change = get_market_info("KRW=X")
    qqqm_price, qqqm_change = get_market_info("QQQM")
    sgov_price, _ = get_market_info("SGOV")
    spym_price, _ = get_market_info("SPYM")
    
    if krw_price < 1000: krw_price = 1450.0

    # 내 평단가 계산
    buys = df[df['Action'] == 'BUY']
    if not buys.empty:
        total_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
        total_usd = (buys['Qty'] * buys['Price'] + buys['Fee']).sum()
        my_avg_rate = total_krw / total_usd if total_usd > 0 else 1450.0
    else:
        my_avg_rate = 1450.0
        
    gap_ratio = krw_price / my_avg_rate
    my_usd = wallet.get('USD', 0)
    my_krw = wallet.get('KRW', 0)
    
    msg = ""
    should_send = False

    # -----------------------------------------------
    # 1. 포트폴리오 비중 분석 (리밸런싱)
    # -----------------------------------------------
    # 현재 자산 가치 계산
    holdings = df.groupby("Ticker").apply(lambda x: x.loc[x['Action']=='BUY','Qty'].sum() - x.loc[x['Action']=='SELL','Qty'].sum()).to_dict()
    
    total_asset_usd = my_usd # 현금 포함
    port_val = {}
    
    prices = {'QQQM': qqqm_price, 'SPYM': spym_price, 'SGOV': sgov_price, 'GMMF': 100.0}
    
    for t, q in holdings.items():
        p = prices.get(t, 0)
        val = q * p
        port_val[t] = val
        total_asset_usd += val

    # -----------------------------------------------
    # 2. 매수 전략 수립
    # -----------------------------------------------
    # 달러가 조금이라도 있을 때 (예: 50달러 이상)
    if my_usd > 50:
        # 이번에 사용할 달러 계산 (AI 판단)
        spending_ratio = calculate_spending_power(gap_ratio, qqqm_change)
        budget_usd = my_usd * spending_ratio
        
        rec_msg = ""
        # 부족한 종목 찾기
        for ticker, ratio in TARGET_RATIO.items():
            if ratio == 0: continue
            target_val = total_asset_usd * ratio
            current_val = port_val.get(ticker, 0)
            
            if current_val < target_val:
                shortfall = target_val - current_val
                # 예산 범위 내에서 구매 가능한 수량
                # shortfall(부족분)과 budget(이번 집행액) 중 작은 쪽을 택함
                spend_amount = min(shortfall, budget_usd)
                price = prices.get(ticker, 100)
                qty = int(spend_amount // price)
                
                if qty > 0:
                    rec_msg += f"👉 {ticker} {qty}주 (약 ${qty*price:.1f})\n"
                    budget_usd -= (qty * price) # 예산 차감

        if rec_msg:
            msg += f"📢 [매수 제안] 보유 달러(${my_usd:.1f}) 중 {spending_ratio*100:.0f}%를 투입하세요.\n"
            msg += f"이유: 환율매력도({'좋음' if gap_ratio<1 else '나쁨'}), 시장상황({qqqm_change:+.1f}%)\n"
            msg += rec_msg
            should_send = True

    # -----------------------------------------------
    # 3. 환전/위기 알림
    # -----------------------------------------------
    if gap_ratio < 0.985 and my_krw >= 100000:
        msg += f"\n✅ [환전 찬스] 내 평단보다 환율이 쌉니다!\n"
        msg += f"보유 원화 {int(my_krw):,}원 중 일부 환전 추천\n"
        should_send = True
        
    if krw_price > 1460:
        msg += f"\n⚠️ [고환율 경고] 1,460원 돌파. 환전 보류.\n"
        should_send = True

    # 전송
    if should_send:
        final_msg = "🛡️ [Aegis AI Briefing]\n" + msg
        send_telegram(final_msg)
        print("Sent")
    else:
        print("Silent")

if __name__ == "__main__":
    run_bot()
