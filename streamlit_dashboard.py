import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

st.set_page_config(page_title="TMF 交易紀錄", page_icon="📈", layout="wide")

TZ_TW = timezone(timedelta(hours=8))
CSV_URL = "https://raw.githubusercontent.com/KevinYang515/trading-dashboard/main/logs/trade_records.csv"

@st.cache_data(ttl=60)
def load_data():
    try:
        df = pd.read_csv(CSV_URL)
        if df.empty:
            return df
        df['datetime'] = pd.to_datetime(df['datetime'])
        df['date'] = df['datetime'].dt.date
        return df
    except Exception as e:
        st.error(f"無法讀取資料：{e}")
        return pd.DataFrame()

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

selected_date = st.selectbox(
    "選擇日期",
    options=available_dates,
    index=default_idx,
    format_func=lambda d: str(d)
)

day_df = df[df['date'] == selected_date].copy()

# 摘要卡片
filled = day_df[day_df['order_status'].str.contains('Filled', na=False)]
slip_pts = pd.to_numeric(filled['slippage_pts'], errors='coerce').dropna()
slip_twd = pd.to_numeric(filled['slippage_twd'], errors='coerce').dropna()

c1, c2, c3, c4 = st.columns(4)
c1.metric("成交筆數", len(filled))
c2.metric("平均滑價", f"{slip_pts.mean():+.0f} 點" if len(slip_pts) else "—")
c3.metric("總滑價金額", f"{slip_twd.sum():+.0f} 元" if len(slip_twd) else "—")

net_pos = int(day_df.iloc[-1]['target_pos']) if not day_df.empty else 0
c4.metric("收盤部位", net_pos)

st.divider()

# 成交明細表
if day_df.empty:
    st.info("當日無交易紀錄")
else:
    display = day_df[[
        'datetime', 'action', 'contract', 'quantity',
        'signal_price', 'fill_price', 'slippage_pts', 'slippage_twd',
        'pos_before', 'target_pos', 'order_status'
    ]].copy()
    display['datetime'] = display['datetime'].dt.strftime('%H:%M:%S')
    for col in ['signal_price', 'fill_price', 'slippage_twd']:
        display[col] = pd.to_numeric(display[col], errors='coerce').apply(
            lambda x: int(x) if pd.notna(x) else ''
        )
    display['slippage_pts'] = pd.to_numeric(display['slippage_pts'], errors='coerce').apply(
        lambda x: f"{int(x):+d}" if pd.notna(x) else ''
    )
    display.columns = [
        '時間', '動作', '合約', '口數',
        '信號價', '成交價', '滑價(點)', '滑價(元)',
        '前部位', '目標', '狀態'
    ]

    def color_row(row):
        bg = 'background-color: #0d2b1a' if row['動作'] == 'BUY' else 'background-color: #2b0d0d'
        return [bg] * len(row)

    st.dataframe(
        display.style.apply(color_row, axis=1),
        use_container_width=True,
        hide_index=True
    )

# 滑價趨勢圖
st.divider()
st.subheader("滑價趨勢（所有交易日）")
all_filled = df[df['order_status'].str.contains('Filled', na=False)].copy()
all_filled['slippage_pts'] = pd.to_numeric(all_filled['slippage_pts'], errors='coerce')
all_filled = all_filled.dropna(subset=['slippage_pts'])

if not all_filled.empty:
    chart_df = all_filled.set_index('datetime')[['slippage_pts']].rename(columns={'slippage_pts': '滑價(點)'})
    st.line_chart(chart_df)
else:
    st.info("尚無滑價資料")

st.caption(f"資料每次 git push 後更新 · 快取 60 秒")
