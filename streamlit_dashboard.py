import streamlit as st
import pandas as pd
import requests
import base64
import io
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="TMF 交易紀錄", page_icon="📈", layout="wide")

TZ_TW = timezone(timedelta(hours=8))
API_URL = "https://api.github.com/repos/KevinYang515/trading-dashboard/contents/logs/trade_records.csv"
TMF_POINT_VALUE = 10

@st.cache_data(ttl=60)
def load_data():
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        content = base64.b64decode(resp.json()['content']).decode('utf-8-sig')
        df = pd.read_csv(io.StringIO(content))
        if df.empty:
            return df
        df['datetime'] = pd.to_datetime(df['datetime'])
        df['date'] = df['datetime'].dt.date
        for col in ['signal_price', 'fill_price', 'slippage_pts', 'slippage_twd',
                    'pos_before', 'target_pos', 'quantity']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except Exception as e:
        st.error(f"無法讀取資料：{e}")
        return pd.DataFrame()

def calc_pnl(trades):
    """計算已實現損益，從第一筆的 pos_before 初始化"""
    if trades.empty:
        return 0.0, 0, 0.0
    first = trades.iloc[0]
    position = int(first['pos_before']) if pd.notna(first['pos_before']) else 0
    avg_cost = float(first['signal_price']) if position != 0 and pd.notna(first['signal_price']) else 0.0
    realized = 0.0

    for _, row in trades.iterrows():
        if pd.isna(row['fill_price']):
            continue
        price = row['fill_price']
        qty = int(row['quantity'])
        action = row['action']

        if action == 'BUY':
            if position < 0:
                close_qty = min(qty, abs(position))
                realized += (avg_cost - price) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position += close_qty
            if qty > 0:
                avg_cost = (avg_cost * position + price * qty) / (position + qty) if (position + qty) > 0 else price
                position += qty
        else:  # SELL
            if position > 0:
                close_qty = min(qty, position)
                realized += (price - avg_cost) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position -= close_qty
            if qty > 0:
                avg_cost = (avg_cost * abs(position) + price * qty) / (abs(position) + qty) if (abs(position) + qty) > 0 else price
                position -= qty

    return realized, position, avg_cost

df = load_data()

st.title("📈 TMF 交易紀錄")

if df.empty:
    st.info("尚無交易資料")
    st.stop()

# 日期選擇
available_dates = sorted(df['date'].unique(), reverse=True)
today = datetime.now(TZ_TW).date()
default_idx = 0
if today in available_dates:
    default_idx = list(available_dates).index(today)

selected_date = st.selectbox("選擇日期", options=available_dates, index=default_idx,
                              format_func=lambda d: str(d))

day_df = df[df['date'] == selected_date].copy()
filled = day_df[day_df['order_status'].str.contains('Filled', na=False)]

# 損益計算（從第一筆的 pos_before 初始化，才能正確計算跨 session 的平倉）
realized_pnl, _, avg_cost = calc_pnl(filled)
# 目前部位直接讀最後一筆 target_pos（最準確）
cur_pos = int(filled.iloc[-1]['target_pos']) if not filled.empty else 0

# 摘要卡片
c1, c2, c3, c4 = st.columns(4)
c1.metric("成交筆數", len(filled))

pnl_str = f"{int(realized_pnl):+,} 元" if len(filled) > 0 else "—"
c2.metric("今日已實現損益", pnl_str)

if cur_pos == 0:
    pos_label, pos_delta = "平倉", None
else:
    pos_label = f"{'多' if cur_pos > 0 else '空'} {abs(int(cur_pos))} 口"
    pos_delta = f"均價 {int(avg_cost)}"
c3.metric("收盤部位", pos_label, delta=pos_delta, delta_color="off")

slip_pts = pd.to_numeric(filled['slippage_pts'], errors='coerce').dropna()
c4.metric("平均滑價", f"{int(slip_pts.mean()):+d} 點" if len(slip_pts) else "—")

st.divider()

# 成交明細表
if filled.empty:
    st.info("當日無成交紀錄")
else:
    display = filled[[
        'datetime', 'action', 'contract', 'quantity',
        'signal_price', 'fill_price', 'slippage_pts', 'slippage_twd',
        'pos_before', 'target_pos', 'order_status'
    ]].copy()

    display['datetime'] = display['datetime'].dt.strftime('%H:%M:%S')
    for col in ['signal_price', 'fill_price', 'slippage_twd']:
        display[col] = display[col].apply(lambda x: int(x) if pd.notna(x) else '')
    display['slippage_pts'] = display['slippage_pts'].apply(
        lambda x: f"{int(x):+d}" if pd.notna(x) else ''
    )
    display['action'] = display['action'].map({'BUY': '買', 'SELL': '賣'})
    display['order_status'] = display['order_status'].str.replace('Status.', '', regex=False)

    display.columns = ['時間', '動作', '合約', '口數',
                       '信號價', '成交價', '滑價(點)', '滑價(元)',
                       '前部位', '目標', '狀態']

    def color_row(row):
        bg = 'background-color: #0d2b1a' if row['動作'] == '買' else 'background-color: #2b0d0d'
        return [bg] * len(row)

    st.dataframe(display.style.apply(color_row, axis=1),
                 use_container_width=True, hide_index=True)

# 歷史每日損益長條圖
st.divider()
st.subheader("歷史每日損益（元）")

all_filled = df[df['order_status'].str.contains('Filled', na=False)].copy().sort_values('datetime')

if not all_filled.empty and all_filled['fill_price'].notna().any():
    # 從第一筆的 pos_before 初始化，避免跨 session 的平倉被誤算
    first = all_filled.iloc[0]
    position = int(first['pos_before']) if pd.notna(first['pos_before']) else 0
    avg_cost = float(first['signal_price']) if position != 0 and pd.notna(first['signal_price']) else 0.0
    daily_pnl = {}

    for _, row in all_filled.iterrows():
        if pd.isna(row['fill_price']):
            continue
        price = float(row['fill_price'])
        qty = int(row['quantity'])
        action = row['action']
        trade_date = str(row['date'])
        trade_pnl = 0.0

        if action == 'BUY':
            if position < 0:
                close_qty = min(qty, abs(position))
                trade_pnl = (avg_cost - price) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position += close_qty
            if qty > 0:
                total = abs(position) + qty
                avg_cost = (avg_cost * abs(position) + price * qty) / total if total > 0 else price
                position += qty
        else:
            if position > 0:
                close_qty = min(qty, position)
                trade_pnl = (price - avg_cost) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position -= close_qty
            if qty > 0:
                total = abs(position) + qty
                avg_cost = (avg_cost * abs(position) + price * qty) / total if total > 0 else price
                position -= qty

        daily_pnl[trade_date] = daily_pnl.get(trade_date, 0.0) + trade_pnl

    if daily_pnl:
        dates = sorted(daily_pnl.keys())
        cum, cum_rows = 0.0, []
        for d in dates:
            cum += daily_pnl[d]
            cum_rows.append({'日期': d, '當日損益': int(daily_pnl[d]), '累積損益': int(cum)})

        pnl_df = pd.DataFrame(cum_rows).set_index('日期')
        st.bar_chart(pnl_df[['當日損益']])
        st.caption("累積損益")
        st.line_chart(pnl_df[['累積損益']])
    else:
        st.info("尚無已實現損益資料")
else:
    st.info("尚無已實現損益資料")

st.caption("資料每次成交後自動更新 · 快取 60 秒")
