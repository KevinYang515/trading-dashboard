import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="TMF 交易紀錄", page_icon="📈", layout="wide")

TZ_TW = timezone(timedelta(hours=8))
CSV_URL = "https://raw.githubusercontent.com/KevinYang515/trading-dashboard/main/logs/trade_records.csv"
TMF_POINT_VALUE = 10

@st.cache_data(ttl=60)
def load_data():
    try:
        df = pd.read_csv(CSV_URL)
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
    """計算已實現損益，追蹤平均成本"""
    position = 0
    avg_cost = 0.0
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

# 損益計算
realized_pnl, cur_pos, avg_cost = calc_pnl(filled)

# 摘要卡片
c1, c2, c3, c4 = st.columns(4)
c1.metric("成交筆數", len(filled))

pnl_str = f"{int(realized_pnl):+,} 元" if len(filled) > 0 else "—"
c2.metric("今日已實現損益", pnl_str)

pos_str = f"{int(cur_pos)} 口"
if cur_pos != 0:
    pos_str += f"（{'多' if cur_pos > 0 else '空'}，均價 {int(avg_cost)}）"
c3.metric("收盤部位", pos_str)

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

# 歷史累積損益曲線
st.divider()
st.subheader("歷史累積損益")

all_filled = df[df['order_status'].str.contains('Filled', na=False)].copy().sort_values('datetime')

if not all_filled.empty and all_filled['fill_price'].notna().any():
    records = []
    position, avg_cost, cumulative = 0, 0.0, 0.0

    for _, row in all_filled.iterrows():
        if pd.isna(row['fill_price']):
            continue
        price = float(row['fill_price'])
        qty = int(row['quantity'])
        action = row['action']
        trade_pnl = 0.0

        if action == 'BUY':
            if position < 0:
                close_qty = min(qty, abs(position))
                trade_pnl = (avg_cost - price) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position += close_qty
            if qty > 0:
                avg_cost = (avg_cost * abs(position) + price * qty) / (abs(position) + qty) if (abs(position) + qty) > 0 else price
                position += qty
        else:
            if position > 0:
                close_qty = min(qty, position)
                trade_pnl = (price - avg_cost) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position -= close_qty
            if qty > 0:
                avg_cost = (avg_cost * abs(position) + price * qty) / (abs(position) + qty) if (abs(position) + qty) > 0 else price
                position -= qty

        cumulative += trade_pnl
        records.append({'時間': row['datetime'], '累積損益(元)': int(cumulative)})

    if records:
        chart_df = pd.DataFrame(records).set_index('時間')
        st.line_chart(chart_df)
    else:
        st.info("尚無已實現損益資料")
else:
    st.info("尚無已實現損益資料")

st.caption("資料每次成交後自動更新 · 快取 60 秒")
