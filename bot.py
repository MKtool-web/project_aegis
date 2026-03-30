import os
import json
import gspread
import pandas as pd
import yfinance as yf
import requests
import ta
import pytz 
import traceback
import time
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==========================================
# 1. 환경 설정 및 전역 변수
# ==========================================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
SHEET_URL = "https://docs.google.com/spreadsheets/d/19EidY2HZI2sHzvuchXX5sKfugHLtEG0QY1Iq61kzmbU/edit?gid=0#gid=0"

# 🔥 [설정] 봇 행동 기준
MIN_KRW_ACTION = 10000   
MIN_USD_ACTION = 100     
REVERSE_EX_GAP = 15      
SPREAD_RATE = 0.009 

# 장기 투자 포트폴리오 목표 비중
TARGET_WEIGHTS = {'QQQM': 40.0, 'SPYM': 40.0, 'SGOV': 20.0, 'GMMF': 0.0}

# ==========================================
# 2. 기본 유틸리티 함수
# ==========================================
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
            
            df_stock = pd.DataFrame(sheet.worksheet(sheet_name).get_all_records())
            time.sleep(1) 
            df_cash = pd.DataFrame(sheet.worksheet("CashFlow").get_all_records())
            
            return df_stock, df_cash
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(5)
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

def get_market_data_safe(ticker, period="2mo"):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker).history(period=period)
            if df.empty: raise ValueError(f"{ticker} 데이터 없음")
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return pd.DataFrame()

def analyze_market(ticker):
    df = get_market_data_safe(ticker, "2mo")
    if len(df) < 14: return 0, 50
    return df['Close'].iloc[-1], ta.momentum.RSIIndicator(df['Close'], window=14).rsi().iloc[-1]

# ==========================================
# 3. 🧠 전면 개편된 Aegis Master Score 산식
# ==========================================
def calculate_aegis_master_score(ticker, current_price, rsi, vix, ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, target_weight, current_weight, my_krw):
    score = 0.0
    
    # A. 시장 기회 점수
    score_A = 0
    if rsi < 50: score_A += (50 - rsi) * 1.5
    if vix > 20: score_A += (vix - 20) * 1.0
    if current_price < ma200: score_A += 20
    score += min(score_A, 60)
    
    # B. 포트폴리오 밸런스 점수 (시나리오 4: 달리는 말의 딜레마 - 허용 오차 밴드 ±5% 적용)
    score_B = 0
    gap = target_weight - current_weight
    if gap > 5.0:  
        score_B += (gap - 5.0) * 2.5
    score += min(score_B, 30)
    
    # C. 시간 압박 점수 (시나리오 1: 이월된 현금 연동)
    score_C = 0
    today = datetime.now().day
    days_passed = (today - 5) if today >= 5 else (today + 30 - 5)
    
    if my_krw >= 600000: # 한 달 치 투자금(약 60만 원) 이상 놀고 있을 때 풀가동
        score_C = days_passed * 1.8
    elif my_krw >= 100000: # 애매한 금액은 완만한 압박
        score_C = days_passed * 0.8
        
    score += min(score_C, 50)
    
    # D. 환율 페널티 점수 (시나리오 2: 구조적 고환율 MA60 적용)
    score_D = 0
    blended_base_rate = (my_avg_rate * 0.3) + (krw_ma60 * 0.7) 
    
    if curr_rate > blended_base_rate: 
        score_D += (curr_rate - blended_base_rate) * 0.5
        
    if dxy_curr > dxy_ma20: 
        score_D = score_D * 0.5 
        
    score -= min(score_D, 50)
    return score

# ==========================================
# 4. 메인 봇 실행 로직
# ==========================================
def run_bot():
    try:
        is_open, status_msg = is_market_open()
        is_bank_open = is_banking_hours()
        
        df_stock, df_cash = get_sheet_data()
        
        vix_df = get_market_data_safe("^VIX", "5d")
        vix = vix_df['Close'].iloc[-1] if not vix_df.empty else 0
        time.sleep(1)
        
        qqqm_price, qqqm_rsi = analyze_market("QQQM")
        time.sleep(1)
        spym_price, spym_rsi = analyze_market("SPYM")
        time.sleep(1)
        sgov_df = get_market_data_safe("SGOV", "5d")
        sgov_price = sgov_df['Close'].iloc[-1] if not sgov_df.empty else 100.0
        
        # 3개월치 환율 데이터 (MA60 뉴노멀 기준 확보)
        ex_df = get_market_data_safe("KRW=X", "3mo")
        if ex_df.empty: raise ValueError("환율 데이터 수신 실패")
        curr_rate = ex_df['Close'].iloc[-1]
        ma_20 = ex_df['Close'].tail(20).mean() 
        krw_ma60 = ex_df['Close'].tail(60).mean() 
        
        if curr_rate == 0 or qqqm_price == 0: raise ValueError("시장 데이터 수신 실패")

        my_avg_rate = calculate_my_avg_exchange_rate(df_cash, df_stock)
        my_krw, my_usd = calculate_balances(df_cash, df_stock)
        
        total_div = 0.0
        current_holdings = {}
        if not df_stock.empty:
            df_stock['Qty'] = pd.to_numeric(df_stock['Qty'], errors='coerce').fillna(0)
            df_stock['Price'] = pd.to_numeric(df_stock['Price'], errors='coerce').fillna(0)
            df_stock['Fee'] = pd.to_numeric(df_stock['Fee'], errors='coerce').fillna(0)
            
            divs = df_stock[df_stock['Action'] == 'DIVIDEND']
            total_div = (divs['Price'] - divs['Fee']).sum()
            
            current_holdings = df_stock.groupby("Ticker").apply(lambda x: x.loc[x['Action']=='BUY','Qty'].sum() - x.loc[x['Action']=='SELL','Qty'].sum()).to_dict()

        qqqm_qty = current_holdings.get('QQQM', 0)
        spym_qty = current_holdings.get('SPYM', 0)
        sgov_qty = current_holdings.get('SGOV', 0)
        
        qqqm_value = qqqm_qty * qqqm_price
        spym_value = spym_qty * spym_price
        sgov_value = sgov_qty * sgov_price
        
        total_portfolio_usd = qqqm_value + spym_value + sgov_value + my_usd
        
        qqqm_current_weight = (qqqm_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0
        spym_current_weight = (spym_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0
        sgov_current_weight = (sgov_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0

        dxy_df = get_market_data_safe("DX-Y.NYB", "1mo")
        dxy_curr = dxy_df['Close'].iloc[-1] if not dxy_df.empty else 100
        dxy_ma20 = dxy_df['Close'].mean() if not dxy_df.empty else 100
        
        qqqm_1y = get_market_data_safe("QQQM", "1y")
        qqqm_ma200 = qqqm_1y['Close'].mean() if len(qqqm_1y) >= 200 else qqqm_price
        
        spym_1y = get_market_data_safe("SPYM", "1y")
        spym_ma200 = spym_1y['Close'].mean() if len(spym_1y) >= 200 else spym_price

        # 파라미터 업데이트: krw_ma60 및 my_krw 전달
        qqqm_score = calculate_aegis_master_score("QQQM", qqqm_price, qqqm_rsi, vix, qqqm_ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, TARGET_WEIGHTS['QQQM'], qqqm_current_weight, my_krw)
        spym_score = calculate_aegis_master_score("SPYM", spym_price, spym_rsi, vix, spym_ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, TARGET_WEIGHTS['SPYM'], spym_current_weight, my_krw)

        real_buy_rate = curr_rate * (1 + SPREAD_RATE)  
        real_sell_rate = curr_rate * (1 - SPREAD_RATE) 

        msg = f"📡 **[Aegis Smart Strategy]**\n"
        msg += f"📅 {datetime.now().strftime('%m/%d %H:%M')} ({status_msg})\n"
        msg += f"💰 잔고: ￦{int(my_krw):,} / ${my_usd:.2f}\n"
        msg += f"❄️ 배당 스노우볼: ${total_div:.2f}\n"
        msg += f"📊 지표: VIX {vix:.1f} / Q-RSI {qqqm_rsi:.1f}\n"
        msg += f"🧠 **AI Score**: QQQM {qqqm_score:.0f}점 | SPYM {spym_score:.0f}점\n\n"

        should_send = False

        # 시나리오 3: L자형 침체 방어 (VIX 30 이상 시 분할 매수 30% 제한)
        pacing_ratio = 0.3 if vix > 30 else 1.0

        if max(qqqm_score, spym_score) >= 100.0:
            target_ticker = "QQQM" if qqqm_score >= spym_score else "SPYM"
            if my_krw >= MIN_KRW_ACTION and is_bank_open:
                amount_to_exchange = my_krw * pacing_ratio
                msg += f"🔥 **[전략적 긴급 환전]** 스코어 100점 돌파!\n"
                if pacing_ratio < 1.0:
                    msg += f"⚠️ VIX 급등으로 현금 소진 속도를 조절합니다 (30% 분할).\n"
                msg += f"👉 추천: {int(amount_to_exchange):,}원 환전 후 {target_ticker} 매수\n\n"
                should_send = True
            elif my_usd >= MIN_USD_ACTION and is_open:
                buy_mode = f"달러의 {pacing_ratio*100:.0f}% 투입"
                msg += f"📈 **[전략적 긴급 매수]** 스코어 100점 돌파!\n👉 추천: {buy_mode}하여 {target_ticker} 매수\n\n"
                should_send = True

        # 이하 일반 로직 (should_send가 False일 때만 작동)
        buy_diff = real_buy_rate - my_avg_rate
        is_cheap_historically = curr_rate < (ma_20 - 5.0)

        if my_krw >= MIN_KRW_ACTION and is_bank_open and not should_send: 
            suggest_percent = 0
            strategy_msg = ""
            
            if -15 < buy_diff <= -5: suggest_percent = 30; strategy_msg = "📉 환율 소폭 하락."
            elif -30 < buy_diff <= -15: suggest_percent = 50; strategy_msg = "📉📉 환율 매력적!"
            elif buy_diff <= -30: suggest_percent = 100; strategy_msg = "💎 [바겐세일] 역대급 환율!"
            elif buy_diff > -5 and is_cheap_historically:
                suggest_percent = 30
                strategy_msg = f"🌊 [물결 타기] 평단보단 높지만,\n최근 평균({ma_20:,.0f}원)보다 저렴합니다."
                
            if suggest_percent > 0:
                amount_to_exchange = my_krw * (suggest_percent / 100)
                msg += f"💵 **[환전 추천]** (예상 {real_buy_rate:,.0f}원)\n{strategy_msg}\n👉 추천: {int(amount_to_exchange):,}원\n\n"
                should_send = True

        sell_diff = real_sell_rate - my_avg_rate
        is_stock_cheap = (qqqm_rsi < 50 or vix > 25)
        
        if my_usd >= 100 and sell_diff >= REVERSE_EX_GAP and not is_stock_cheap and is_bank_open:
            msg += f"🇰🇷 **[역환전 기회]**\n• 수수료 떼고도 {sell_diff:+.0f}원 이득!\n👉 달러 일부 원화 환전.\n\n"
            should_send = True

        if my_usd >= MIN_USD_ACTION and (is_open or vix > 30) and not should_send:
            if qqqm_rsi < 40:
                buy_mode = "소수점 매수" if my_usd < qqqm_price else "1주 이상 매수"
                intensity = "30%" if qqqm_rsi >= 30 else "50% (공포매수)"
                msg += f"📈 **[QQQM 매수 추천]**\n• AI 판단: 조정장 (RSI {qqqm_rsi:.1f})\n• 현재가: ${qqqm_price:.2f}\n👉 달러의 {intensity} {buy_mode} 진행!\n\n"
                should_send = True
            elif spym_rsi < 40: 
                buy_mode = "소수점 매수" if my_usd < spym_price else "1주 이상 매수"
                msg += f"🛡️ **[SPYM 매수 추천]**\n• AI 판단: S&P500 조정 (RSI {spym_rsi:.1f})\n👉 달러의 30% {buy_mode} 진행!\n\n"
                should_send = True

        if my_usd >= MIN_USD_ACTION and is_open and not should_send:
            if sgov_current_weight < TARGET_WEIGHTS['SGOV']:
                msg += f"🛡️ **[SGOV 파킹 (안전 자산 충전)]**\n"
                msg += f"• AI 판단: 현재 위험 자산(주식) 관망 유지\n"
                msg += f"• SGOV 비중: 현재 {sgov_current_weight:.1f}% (목표 {TARGET_WEIGHTS['SGOV']:.0f}%)\n"
                msg += f"👉 노는 달러를 SGOV에 파킹하여 배당(이자)을 챙깁니다.\n\n"
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
