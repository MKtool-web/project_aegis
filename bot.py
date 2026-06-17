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

# 🔥 기존의 고정 TARGET_WEIGHTS는 삭제하고, 프론트엔드와 동일한 AI 오토파일럿 로직을 장착합니다.
def get_ai_target_ratios(vix, q_rsi, s_rsi):
    t_qqqm = 30; t_spym = 30; t_sgov = 40; t_qld = 0
    
    # 진성 공포장 (Tactical Strike)
    if vix > 30 or (q_rsi < 30 and vix >= 18) or (s_rsi < 30 and vix >= 18):
        t_qqqm = 27; t_spym = 28; t_sgov = 25; t_qld = 20
    # 지수 개편 등으로 인한 과열기 (Profit Take)
    elif q_rsi > 70 or s_rsi > 70:
        t_qqqm = 20; t_spym = 20; t_sgov = 60; t_qld = 0
        
    return {'QQQM': t_qqqm, 'SPYM': t_spym, 'SGOV': t_sgov, 'QLD': t_qld, 'GMMF': 0.0}


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
            # 수정: 0이나 빈 값을 반환하지 않고 에러를 던져서 계산을 차단함
            raise ConnectionError(f"{ticker} 데이터 수신 최종 실패") 

def analyze_market(ticker):
    df = get_market_data_safe(ticker, "2mo")
    # if len(df) < 14: return 0, 50 (삭제: 위에서 에러로 차단되므로 불필요)
    return df['Close'].iloc[-1], ta.momentum.RSIIndicator(df['Close'], window=14).rsi().iloc[-1]

# ==========================================
# 3. 🧠 최신 V26.5 마스터 스코어 (단일 통제 시스템)
# ==========================================
def calculate_aegis_master_score(ticker, current_price, rsi, vix, ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, target_weight, current_weight, my_krw):
    score = 0.0
    
    score_A = 0
    if rsi < 50:
        if vix >= 18: 
            score_A += (50 - rsi) * 1.5
        else:
            pass 

    if vix > 20: score_A += (vix - 20) * 1.0
    if current_price < ma200: score_A += 20
    score += min(score_A, 60)
    
    score_B = 0
    gap = target_weight - current_weight
    if gap > 5.0:  
        score_B += (gap - 5.0) * 2.5
    score += min(score_B, 30)
    
    score_C = 0
    kst = pytz.timezone('Asia/Seoul')
    today = datetime.now(kst).day
    days_passed = (today - 5) if today >= 5 else (today + 30 - 5)
    
    if my_krw >= 100000:
        # 현금이 많을수록 하루당 점수가 부드럽게 증가 (60만원에서 최대 1.8)
        rate_per_day = 0.8 + min(1.0, (my_krw - 100000) / 500000) * 1.0
        score_C = days_passed * rate_per_day
        
    score += min(score_C, 50)
    
    score_D = 0
    blended_base_rate = (my_avg_rate * 0.3) + (krw_ma60 * 0.7) 
    
    if curr_rate > blended_base_rate: 
        score_D += (curr_rate - blended_base_rate) * 0.5
        
    if dxy_curr > dxy_ma20: 
        score_D = score_D * 0.5 
        
    score -= min(score_D, 50)

    # 🔥 Score E: 과열 페널티 (비싼 장에서 줍줍 방지)
    # RSI가 낮은 폭락장에서는 0이라, 공포매수는 그대로 살아있음
    score_E = 0
    if rsi > 55:
        score_E += (rsi - 55) * 1.2          # 예: RSI 70 → (70-55)*1.2 = 18점 차감
    if current_price > ma200 * 1.10:          # 현재가가 200일선보다 10% 이상 위
        score_E += 15
    score -= score_E

    return score
# ==========================================
# 4. 메인 봇 실행 로직
# ==========================================
def run_bot():
    try:
        is_open, status_msg = is_market_open()
        is_bank_open = is_banking_hours()
        
        df_stock, df_cash = get_sheet_data()
        
        # 여기서 통신 에러가 나면 0으로 계산하지 않고 즉시 아래 except ConnectionError 로 빠짐
        vix_df = get_market_data_safe("^VIX", "5d")
        vix = vix_df['Close'].iloc[-1]
        time.sleep(1)
        
        qqqm_price, qqqm_rsi = analyze_market("QQQM")
        time.sleep(1)
        spym_price, spym_rsi = analyze_market("SPYM")
        time.sleep(1)
        qld_price, qld_rsi = analyze_market("QLD")
        time.sleep(1)
        sgov_df = get_market_data_safe("SGOV", "5d")
        sgov_price = sgov_df['Close'].iloc[-1]
        gmmf_df = get_market_data_safe("GMMF", "5d")
        gmmf_price = gmmf_df['Close'].iloc[-1] if not gmmf_df.empty else 100.0
        time.sleep(1)
        
        ex_df = get_market_data_safe("KRW=X", "3mo")
        curr_rate = ex_df['Close'].iloc[-1]
        ma_20 = ex_df['Close'].tail(20).mean() 
        krw_ma60 = ex_df['Close'].tail(60).mean()
        
        if curr_rate == 0 or qqqm_price == 0: raise ValueError("시장 데이터 수신 실패")

        # 🔥 [수정] 실시간 시장 상황(VIX, RSI)을 반영하여 목표 비중을 동적으로 먼저 계산합니다.
        dynamic_targets = get_ai_target_ratios(vix, qqqm_rsi, spym_rsi)

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
        qld_qty  = current_holdings.get('QLD', 0)
        sgov_qty = current_holdings.get('SGOV', 0)
        gmmf_qty = current_holdings.get('GMMF', 0)
        
        qqqm_value = qqqm_qty * qqqm_price
        spym_value = spym_qty * spym_price
        qld_value  = qld_qty * qld_price
        sgov_value = sgov_qty * sgov_price
        gmmf_value = gmmf_qty * gmmf_price
        
        total_portfolio_usd = qqqm_value + spym_value + qld_value + sgov_value + gmmf_value + my_usd
        
        qqqm_current_weight = (qqqm_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0
        spym_current_weight = (spym_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0
        qld_current_weight  = (qld_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0
        sgov_current_weight = (sgov_value / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0

        dxy_df = get_market_data_safe("DX-Y.NYB", "1mo")
        dxy_curr = dxy_df['Close'].iloc[-1] if not dxy_df.empty else 100
        dxy_ma20 = dxy_df['Close'].mean() if not dxy_df.empty else 100
        
        qqqm_1y = get_market_data_safe("QQQM", "1y")
        qqqm_ma200 = qqqm_1y['Close'].tail(200).mean() if len(qqqm_1y) >= 200 else qqqm_price
        spym_1y = get_market_data_safe("SPYM", "1y")
        spym_ma200 = spym_1y['Close'].tail(200).mean() if len(spym_1y) >= 200 else spym_price
        qld_1y = get_market_data_safe("QLD", "1y")
        qld_ma200 = qld_1y['Close'].tail(200).mean() if len(qld_1y) >= 200 else qld_price

        # 🔥 자동화 1: 봇이 모든 종목의 마스터 스코어를 똑같이 계산
        qqqm_score = calculate_aegis_master_score("QQQM", qqqm_price, qqqm_rsi, vix, qqqm_ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, dynamic_targets['QQQM'], qqqm_current_weight, my_krw)
        spym_score = calculate_aegis_master_score("SPYM", spym_price, spym_rsi, vix, spym_ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, dynamic_targets['SPYM'], spym_current_weight, my_krw)
        qld_score = calculate_aegis_master_score("QLD", qld_price, qld_rsi, vix, qld_ma200, curr_rate, my_avg_rate, krw_ma60, dxy_curr, dxy_ma20, dynamic_targets['QLD'], qld_current_weight, my_krw)

        real_buy_rate = curr_rate * (1 + SPREAD_RATE)  
        real_sell_rate = curr_rate * (1 - SPREAD_RATE) 

        kst = pytz.timezone('Asia/Seoul')
        msg = f"📡 **[Aegis Smart Strategy]**\n📅 {datetime.now(kst).strftime('%m/%d %H:%M')} ({status_msg})\n💰 잔고: ￦{int(my_krw):,} / ${my_usd:.2f}\n❄️ 배당 스노우볼: ${total_div:.2f}\n📊 지표: VIX {vix:.1f} / Q-RSI {qqqm_rsi:.1f} / QLD-RSI {qld_rsi:.1f}\n🧠 **AI Score**: QQQM {qqqm_score:.0f} | SPYM {spym_score:.0f} | QLD {qld_score:.0f}\n\n"

        should_send = False
        pacing_ratio = 0.3 if vix > 30 else 1.0

        # 🔥 추세 필터: 핵심 지수(QQQM)가 200일선 아래면 하락 추세로 보고 매수를 더 잘게 분할
        is_downtrend = qqqm_price < qqqm_ma200
        trend_factor = 0.5 if is_downtrend else 1.0   # 하락 추세면 매수 강도를 절반으로

        # 🔥 자동화 2: 100점 돌파 시 봇이 3개 종목을 경쟁시켜 가장 저평가된 1개만 매수 지시
        scores = {'QQQM': qqqm_score, 'SPYM': spym_score, 'QLD': qld_score}
        max_ticker = max(scores, key=scores.get)
        max_score = scores[max_ticker]

        if max_score >= 100.0:
            target_ticker = max_ticker
            if my_krw >= MIN_KRW_ACTION and is_bank_open:
                amount_to_exchange = my_krw * pacing_ratio
                msg += f"🔥 **[전략적 긴급 환전]** 최고점({max_score:.0f}점) 돌파!\n"
                if pacing_ratio < 1.0: msg += f"⚠️ VIX 급등으로 현금 소진 속도를 조절합니다 (30% 분할).\n"
                msg += f"👉 추천: {int(amount_to_exchange):,}원 환전 후 **{target_ticker} 매수**\n\n"
                should_send = True
            elif my_usd >= MIN_USD_ACTION and is_open:
                buy_mode = f"달러의 {pacing_ratio*100:.0f}% 투입"
                msg += f"📈 **[전략적 긴급 매수]** 최고점({max_score:.0f}점) 돌파!\n👉 추천: {buy_mode}하여 **{target_ticker} 매수**\n\n"
                should_send = True

        buy_diff = real_buy_rate - my_avg_rate
        is_cheap_historically = curr_rate < (ma_20 - 5.0)

        if my_krw >= MIN_KRW_ACTION and is_bank_open and not should_send: 
            suggest_percent = 0
            strategy_msg = ""
            if -15 < buy_diff <= -5: suggest_percent = 30; strategy_msg = "📉 환율 소폭 하락."
            elif -30 < buy_diff <= -15: suggest_percent = 50; strategy_msg = "📉📉 환율 매력적!"
            elif buy_diff <= -30: suggest_percent = 100; strategy_msg = "💎 [바겐세일] 역대급 환율!"
            elif buy_diff > -5 and is_cheap_historically:
                suggest_percent = 30; strategy_msg = f"🌊 [물결 타기] 평단보단 높지만,\n최근 평균({ma_20:,.0f}원)보다 저렴합니다."
                
            if suggest_percent > 0:
                amount_to_exchange = my_krw * (suggest_percent / 100)
                msg += f"💵 **[환전 추천]** (예상 {real_buy_rate:,.0f}원)\n{strategy_msg}\n👉 추천: {int(amount_to_exchange):,}원\n\n"
                should_send = True

        sell_diff = real_sell_rate - my_avg_rate
        is_stock_cheap = (qqqm_rsi < 50 or qld_rsi < 50 or vix > 25)
        is_fx_truly_high = curr_rate > krw_ma60   # 추가: 내 평단이 아니라 '최근 60일 시장 평균'보다도 높을 때만

        if (my_usd >= 100 and sell_diff >= REVERSE_EX_GAP and not is_stock_cheap
                and is_fx_truly_high and not should_send and is_bank_open):
            msg += f"🇰🇷 **[역환전 기회]**\n• 수수료 떼고도 {sell_diff:+.0f}원 이득!\n👉 달러 일부 원화 환전.\n\n"
            should_send = True

        # 🔥 진성 폭락장 직관적 매수 지시 (VIX 트리거)
        if my_usd >= MIN_USD_ACTION and (is_open or vix > 30) and not should_send:
            trend_note = "\n📉 하락 추세(200일선 아래): 강도 절반으로 분할 진입" if is_downtrend else ""
            if vix >= 25 and qld_rsi < 35:
                buy_mode = "소수점 매수" if my_usd < qld_price else "1주 이상 매수"
                qld_pct = 50   # QLD는 위성 공격 자산이므로 추세와 무관하게 항상 공격적으로
                msg += f"🎯 **[전술적 타격: QLD 줍줍]**\n• 듀얼 검증: VIX {vix:.1f} 폭등 (진성 공포장){trend_note}\n👉 위성 자금의 {qld_pct}%로 QLD(레버리지) {buy_mode} 진행!\n\n"
                should_send = True
            elif qqqm_rsi < 40 and vix >= 18:
                buy_mode = "소수점 매수" if my_usd < qqqm_price else "1주 이상 매수"
                base_pct = 30 if qqqm_rsi >= 30 else 50
                final_pct = int(base_pct * trend_factor)
                label = "공포매수" if qqqm_rsi < 30 else ""
                msg += f"📈 **[진성 하락장: QQQM 매수]**\n• 듀얼 검증: VIX {vix:.1f} 돌파{trend_note}\n👉 달러의 {final_pct}% {label} {buy_mode} 진행!\n\n"
                should_send = True
            elif spym_rsi < 40 and vix >= 18: 
                buy_mode = "소수점 매수" if my_usd < spym_price else "1주 이상 매수"
                spym_pct = int(30 * trend_factor)
                msg += f"🛡️ **[진성 하락장: SPYM 매수]**\n• 듀얼 검증: VIX {vix:.1f} 돌파{trend_note}\n👉 달러의 {spym_pct}% {buy_mode} 진행!\n\n"
                should_send = True
                
        # 🔥 현금 천장: 달러 현금이 포트폴리오의 일정 비율을 넘으면 경고 (현금이 노는 것 방지)
        CASH_CEILING_PCT = 35.0   # 달러 현금이 전체의 25%를 넘으면 과다로 판단
        usd_cash_weight = (my_usd / total_portfolio_usd * 100) if total_portfolio_usd > 0 else 0

        # SGOV 파킹 지시 (기존 조건 + 현금 천장 초과 시에도 발동)
        if my_usd >= MIN_USD_ACTION and is_open and not should_send:
            if usd_cash_weight > CASH_CEILING_PCT:
                sgov_buy_qty = my_usd / sgov_price
                msg += f"💰 **[현금 과다 경고: SGOV 파킹 권장]**\n"
                msg += f"• 달러 현금 비중: {usd_cash_weight:.1f}% (천장 {CASH_CEILING_PCT:.0f}% 초과)\n"
                msg += f"• 비싼 장이 길어지며 현금이 놀고 있습니다. 이자라도 받게 파킹을 권합니다.\n"
                msg += f"👉 남는 달러(${my_usd:.2f})로 SGOV 약 **{sgov_buy_qty:.2f}주** 매수 파킹\n\n"
                should_send = True
            elif sgov_current_weight < dynamic_targets['SGOV']:
                sgov_buy_qty = my_usd / sgov_price
                msg += f"🛡️ **[SGOV 파킹 (안전 자산 충전)]**\n"
                msg += f"• SGOV 비중: 현재 {sgov_current_weight:.1f}% (목표 {dynamic_targets['SGOV']:.0f}%)\n"
                msg += f"👉 남은 달러(${my_usd:.2f})로 SGOV 약 **{sgov_buy_qty:.2f}주**를 매수하여 파킹하세요.\n\n"
                should_send = True

        VOLATILITY_BUFFER = 8.0

        # 🔥 자동화 3: 과열 리밸런싱 (수익 실현 / 출구 전략) - 코어와 위성 모두 똑같이 통제
        if is_open:
            if qld_rsi > 70 and qld_current_weight >= (dynamic_targets['QLD'] + VOLATILITY_BUFFER): # 수정
                excess_pct = qld_current_weight - dynamic_targets['QLD']
                excess_usd = total_portfolio_usd * (excess_pct / 100)
                sell_qty = round(excess_usd / qld_price)
                sgov_qty_to_buy = round(excess_usd / sgov_price)
                if sell_qty >= 1:
                    msg += f"🔴 **[QLD 과열 익절 (위성 수익 실현)]** (RSI {qld_rsi:.1f})\n"
                    msg += f"• 현재 비중: {qld_current_weight:.1f}% (+{excess_pct:.1f}% 초과)\n"
                    msg += f"👉 **실행 가이드:** QLD **{sell_qty}주** 매도 후, SGOV **{sgov_qty_to_buy}주** 안전 파킹\n\n"
                    should_send = True

            elif qqqm_rsi > 70 and qqqm_current_weight >= (dynamic_targets['QQQM'] + VOLATILITY_BUFFER) and not should_send: # 수정
                excess_pct = qqqm_current_weight - dynamic_targets['QQQM']
                excess_usd = total_portfolio_usd * (excess_pct / 100)
                sell_qty = round(excess_usd / qqqm_price)
                sgov_qty_to_buy = round(excess_usd / sgov_price)
                if sell_qty >= 1:
                    msg += f"🔴 **[QQQM 과열 리밸런싱]** (RSI {qqqm_rsi:.1f})\n"
                    msg += f"• 현재 비중: {qqqm_current_weight:.1f}% (+{excess_pct:.1f}% 초과)\n"
                    msg += f"👉 **실행 가이드:** QQQM **{sell_qty}주** 매도 후, SGOV **{sgov_qty_to_buy}주** 파킹\n\n"
                    should_send = True

            elif spym_rsi > 70 and spym_current_weight >= (dynamic_targets['SPYM'] + VOLATILITY_BUFFER) and not should_send: # 수정
                excess_pct = spym_current_weight - dynamic_targets['SPYM']
                excess_usd = total_portfolio_usd * (excess_pct / 100)
                sell_qty = round(excess_usd / spym_price)
                sgov_qty_to_buy = round(excess_usd / sgov_price)
                if sell_qty >= 1:
                    msg += f"🔴 **[SPYM 과열 리밸런싱]** (RSI {spym_rsi:.1f})\n"
                    msg += f"• 현재 비중: {spym_current_weight:.1f}% (+{excess_pct:.1f}% 초과)\n"
                    msg += f"👉 **실행 가이드:** SPYM **{sell_qty}주** 매도 후, SGOV **{sgov_qty_to_buy}주** 파킹\n\n"
                    should_send = True
        # 🔥 자동화 4: SGOV 방어 해제 (공격 자금 장전)
        # 시장이 안정/하락장으로 접어들어 SGOV 목표 비중이 줄어들었을 때, 초과된 SGOV를 팔아 현금을 확보합니다.
        if is_open:
            if sgov_current_weight >= (dynamic_targets['SGOV'] + VOLATILITY_BUFFER):
                excess_pct = sgov_current_weight - dynamic_targets['SGOV']
                excess_usd = total_portfolio_usd * (excess_pct / 100)
                sgov_sell_qty = round(excess_usd / sgov_price)
                
                if sgov_sell_qty >= 1:
                    msg += f"⚔️ **[SGOV 방어 해제 (공격 자금 장전)]**\n"
                    msg += f"• SGOV 비중: {sgov_current_weight:.1f}% (+{excess_pct:.1f}% 초과)\n"
                    msg += f"👉 **실행 가이드:** 초과된 파킹 자산 SGOV **{sgov_sell_qty}주**를 매도하여 달러($)를 확보하세요. (이 달러는 폭락장 타격에 사용됩니다.)\n\n"
                    should_send = True
                    
        if should_send:
            send_telegram(msg)
            
    except ConnectionError as ce:
        send_telegram(f"⚠️ **[Aegis API 일시 장애]**\n야후 파이낸스 데이터 수신에 실패했습니다: {str(ce)}\n잘못된 매수를 막기 위해 봇 작동을 일시 중단합니다. 복구 후 재시도 바랍니다.")
        return 
        
    except Exception as e:
        send_telegram(f"⚠️ **[Aegis System Error]**\n🔻 에러 내용:\n{str(e)}")
        print(traceback.format_exc())

if __name__ == "__main__":
    run_bot()
