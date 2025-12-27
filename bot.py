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
    
    # 주식 거래 내역 (현재 보유량 파악용)
    df_stock = pd.DataFrame(sheet.sheet1.get_all_records())
    
    # 지갑 잔고
    try:
        wallet_data = sheet.worksheet("Wallet").get_all_records()
        wallet = {row['Currency']: row['Amount'] for row in wallet_data}
    except:
        wallet = {'KRW': 0, 'USD': 0}

    # 🔥 [V7.0 핵심] CashFlow에서 정확한 '내 평단가' 계산
    try:
        cf_data = sheet.worksheet("CashFlow").get_all_records()
        df_cash = pd.DataFrame(cf_data)
        
        exchanges = df_cash[df_cash['Type'] == 'Exchange']
        if not exchanges.empty:
            total_krw = exchanges['Amount_KRW'].sum()
            total_usd = exchanges['Amount_USD'].sum()
            my_avg_rate = total_krw / total_usd if total_usd > 0 else 1450.0
        else:
            # 기록 없으면 기존 방식(주식 매수 기록)으로 추정
            buys = df_stock[df_stock['Action'] == 'BUY']
            if not buys.empty:
                total_krw = ((buys['Qty'] * buys['Price'] + buys['Fee']) * buys['Exchange_Rate']).sum()
                total_usd = (buys['Qty'] * buys['Price'] + buys['Fee']).sum()
                my_avg_rate = total_krw / total_usd if total_usd > 0 else 1450.0
            else:
                my_avg_rate = 1450.0
    except:
        my_avg_rate = 1450.0

    return df_stock, wallet, my_avg_rate

def get_market_info(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        price = float(hist['Close'].iloc[-1])
        prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else price
        change = ((price - prev) / prev) * 100
        return price, change
    except:
        return 0.0, 0.0

# 🔥 [핵심 로직] 유기적 자금 집행 (Aggressive but Smart)
def calculate_spending_power(gap_ratio, stock_change):
    # 기본 전제: 상황이 나쁘지 않으면 100% 다 산다.
    power = 1.0 
    
    # 1. 환율 페널티 (내 평단보다 2% 이상 비쌀 때만 줄임)
    if gap_ratio > 1.02:
        penalty = (gap_ratio - 1.02) * 10 
        power -= penalty
        
    # 2. 주가 과열 페널티 (3% 급등 시 추격 매수 자제)
    if stock_change > 3.0:
        power -= 0.3
        
    # 3. 폭락장 보너스 (주식이 싸지면 페널티 무시하고 풀매수)
    if stock_change < -2.0:
        power = 1.0 

    return max(0.2, min(power, 1.0))

def run_bot():
    df, wallet, my_avg_rate = get_data()
    
    # 시장 데이터
    krw_price, _ = get_market_info("KRW=X")
    if krw_price < 1000: krw_price = 1450.0

    qqqm_price, qqqm_change = get_market_info("QQQM")
    sgov_price, _ = get_market_info("SGOV")
    spym_price, _ = get_market_info("SPYM")
    
    # 괴리율 계산
    gap_ratio = krw_price / my_avg_rate
    my_usd = wallet.get('USD', 0)
    my_krw = wallet.get('KRW', 0)
    
    msg = ""
    should_send = False

    # -----------------------------------------------
    # 1. 매수 전략 (보유 달러가 있을 때)
    # -----------------------------------------------
    if my_usd > 50:
        # 얼마나 쓸까? (자금 집행 강도)
        spending_ratio = calculate_spending_power(gap_ratio, qqqm_change)
        budget_usd = my_usd * spending_ratio
        
        # 무엇을 살까? (동적 포트폴리오 비율) 
        # 기본 비율
        target_ratio = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35, 'GMMF': 0.0}
        
        ratio_msg = "⚖️ [균형]"
        if gap_ratio > 1.015: # 환율 비쌈 -> SGOV(방어) 비중 확대
            target_ratio = {'SGOV': 0.70, 'SPYM': 0.15, 'QQQM': 0.15, 'GMMF': 0.0}
            ratio_msg = "🛡️ [방어]"
        elif gap_ratio < 0.99: # 환율 저렴 -> 주식(공격) 비중 확대
            target_ratio = {'SGOV': 0.10, 'SPYM': 0.45, 'QQQM': 0.45, 'GMMF': 0.0}
            ratio_msg = "🚀 [공격]"

        # 리밸런싱 계산 (현재 보유 자산 가치 + 현금 합산)
        holdings = df.groupby("Ticker").apply(lambda x: x.loc[x['Action']=='BUY','Qty'].sum() - x.loc[x['Action']=='SELL','Qty'].sum()).to_dict()
        prices = {'QQQM': qqqm_price, 'SPYM': spym_price, 'SGOV': sgov_price, 'GMMF': 100.0}
        
        total_asset_usd = my_usd # 현금 포함
        port_val = {}
        for t, q in holdings.items():
            val = q * prices.get(t, 0)
            port_val[t] = val
            total_asset_usd += val

        rec_msg = ""
        for ticker, ratio in target_ratio.items():
            if ratio == 0: continue
            target_val = total_asset_usd * ratio
            current_val = port_val.get(ticker, 0)
            
            if current_val < target_val:
                shortfall = target_val - current_val
                spend_amount = min(shortfall, budget_usd)
                price = prices.get(ticker, 100)
                qty = int(spend_amount // price)
                
                if qty > 0:
                    rec_msg += f"👉 {ticker} {qty}주 (약 ${qty*price:.1f})\n"
                    budget_usd -= (qty * price)

        if rec_msg:
            msg += f"📢 [매수 제안] {ratio_msg} 모드 가동\n"
            msg += f"보유달러 ${my_usd:.1f} 중 {int(spending_ratio*100)}% 투입\n"
            msg += rec_msg
            should_send = True

    # -----------------------------------------------
    # 2. 환전 알림
    # -----------------------------------------------
    if gap_ratio < 0.985 and my_krw >= 100000:
        msg += f"\n✅ [환전 기회] 환율 {krw_price:,.0f}원 (내 평단 {my_avg_rate:,.0f}원 대비 저렴)\n"
        msg += f"보유 원화 {int(my_krw):,}원 활용 추천\n"
        should_send = True

    # 테스트용: 알림이 없어도 무조건 생존신고 (나중에 주석 처리)
    if not should_send:
        msg = f"☕ [시장 감시 중] 환율: {krw_price:,.0f}원 / 평단: {my_avg_rate:,.0f}원\n특이사항 없음."
        # should_send = True # 🚨 테스트할 때만 주석 해제하세요!

    if should_send:
        final_msg = "🛡️ [Aegis AI]\n" + msg
        send_telegram(final_msg)

if __name__ == "__main__":
    run_bot()
