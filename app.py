import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

# ==========================================
# 1. 핵심 엔진 (크롤링 & AI 계산)
# ==========================================

def get_soup(url):
    """사람인 척하고 접속하는 함수"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        return BeautifulSoup(res.text, "html.parser")
    except:
        return None

def get_current_price(ticker):
    """미국 Finviz 사이트에서 주가 크롤링"""
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        soup = get_soup(url)
        if not soup: return None
        price_tag = soup.select_one("strong.quote-price")
        if price_tag:
            return float(price_tag.text.replace(',', ''))
        return None
    except:
        return None

def get_usd_krw():
    """네이버 금융에서 실시간 환율 크롤링"""
    try:
        url = "https://finance.naver.com/marketindex/"
        soup = get_soup(url)
        if not soup: return 1450.0
        usd_tag = soup.select_one("div.head_info > span.value")
        if usd_tag:
            return float(usd_tag.text.replace(',', ''))
        return 1450.0
    except:
        return 1450.0

class Rebalancer:
    def __init__(self):
        # 목표 비중 설정
        self.TARGET_RATIO = {'SGOV': 0.30, 'SPYM': 0.35, 'QQQM': 0.35} # 예시 종목으로 수정 (GMMF->SGOV 등)
        # 내 보유 수량 (일단 하드코딩, 나중에 기능 추가)
        self.CURRENT_HOLDINGS = {'SGOV': 4, 'SPYM': 5, 'QQQM': 2} 

    def analyze(self, investment_krw):
        exchange_rate = get_usd_krw()
        investment_usd = investment_krw / exchange_rate
        
        # 현재 자산 가치 계산
        portfolio = {}
        total_value_usd = 0
        
        for ticker, qty in self.CURRENT_HOLDINGS.items():
            price = get_current_price(ticker)
            if price is None: price = 100.0 # 조회 실패시 임시값
            val = qty * price
            portfolio[ticker] = {'qty': qty, 'price': price, 'value': val}
            total_value_usd += val
            
        total_asset_usd = total_value_usd + investment_usd
        recommendations = []
        
        for ticker, target_ratio in self.TARGET_RATIO.items():
            target_amt = total_asset_usd * target_ratio
            current_amt = portfolio.get(ticker, {'value': 0})['value']
            
            if current_amt < target_amt:
                shortfall = target_amt - current_amt
                price = portfolio.get(ticker, {'price': 100})['price']
                buy_qty = int(shortfall // price)
                
                if buy_qty > 0:
                    cost_krw = buy_qty * price * exchange_rate
                    recommendations.append({
                        'ticker': ticker,
                        'qty': buy_qty,
                        'price_usd': price,
                        'cost': cost_krw
                    })
        return recommendations

# ==========================================
# 2. 화면 (UI) 구성
# ==========================================

st.set_page_config(page_title="Project Aegis", layout="wide")

# 🔐 로그인 기능
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

if not st.session_state['authenticated']:
    st.title("🔒 Project Aegis 로그인")
    password = st.text_input("비밀번호", type="password")
    if st.button("접속"):
        if password == "1234":  # 비밀번호 변경 가능
            st.session_state['authenticated'] = True
            st.rerun()
        else:
            st.error("비밀번호 오류")
    st.stop()

st.title("🛡️ Project Aegis : Cloud Ver.")

# 사이드바
st.sidebar.header("투자 입력")
investment = st.sidebar.number_input("이번 달 투자금(원)", value=400000, step=10000)
if st.sidebar.button("AI 분석 실행"):
    st.session_state['run'] = True

# 메인 대시보드
krw = get_usd_krw()
st.metric("현재 환율 (네이버)", f"{krw:,.0f} 원/$")

tab1, tab2 = st.tabs(["📊 내 자산", "🤖 AI 추천"])

with tab1:
    st.info("현재 보유 수량은 코드에 고정되어 있습니다. (추후 DB 연결 예정)")
    data = {
        '종목': ['SGOV', 'SPYM', 'QQQM'],
        '수량': [4, 5, 2]
    }
    st.dataframe(pd.DataFrame(data), use_container_width=True)

with tab2:
    if st.session_state.get('run'):
        bot = Rebalancer()
        recs = bot.analyze(investment)
        if recs:
            st.write(f"💰 **투자금 {investment:,.0f}원**으로 다음을 매수하세요:")
            for r in recs:
                st.success(f"👉 **{r['ticker']}** : {r['qty']}주 매수 (약 {r['cost']:,.0f}원)")
        else:
            st.success("비율이 완벽합니다. 달러만 환전하세요.")
