import streamlit as st
import pandas as pd
import requests
import base64
import io
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="TMF 交易紀錄", page_icon="📈", layout="wide")

TZ_TW = timezone(timedelta(hours=8))
REPO           = "KevinYang515/trading-dashboard"
API_URL        = f"https://api.github.com/repos/{REPO}/contents/logs/trade_records.csv"
BALANCE_API_URL= f"https://api.github.com/repos/{REPO}/contents/logs/balance_log.csv"
BT_A_URL       = f"https://api.github.com/repos/{REPO}/contents/backtest/results_breakout.csv"
BT_B_URL       = f"https://api.github.com/repos/{REPO}/contents/backtest/results_scalp.csv"
TMF_POINT_VALUE = 10


def _gh_csv(url: str, ttl=300) -> pd.DataFrame:
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8-sig")
        return pd.read_csv(io.StringIO(content))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_trade_data():
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(content))
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["date"] = df["datetime"].dt.date
        for col in ["signal_price", "fill_price", "slippage_pts", "slippage_twd",
                    "pos_before", "target_pos", "quantity"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as e:
        st.error(f"無法讀取資料：{e}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def load_balance_data():
    try:
        resp = requests.get(BALANCE_API_URL, timeout=10)
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(content))
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["date"] = df["datetime"].dt.date
        for col in ["yesterday_balance", "today_balance", "equity",
                    "future_settle_profitloss", "future_open_position", "available_margin"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_backtest_a():
    return _gh_csv(BT_A_URL)


@st.cache_data(ttl=300)
def load_backtest_b():
    return _gh_csv(BT_B_URL)


def calc_pnl(trades):
    if trades.empty:
        return 0.0, 0, 0.0
    first = trades.iloc[0]
    position = int(first["pos_before"]) if pd.notna(first["pos_before"]) else 0
    avg_cost = float(first["signal_price"]) if position != 0 and pd.notna(first["signal_price"]) else 0.0
    realized = 0.0
    for _, row in trades.iterrows():
        if pd.isna(row["fill_price"]):
            continue
        price = row["fill_price"]
        qty = int(row["quantity"])
        action = row["action"]
        if action == "BUY":
            if position < 0:
                close_qty = min(qty, abs(position))
                realized += (avg_cost - price) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position += close_qty
            if qty > 0:
                avg_cost = (avg_cost * position + price * qty) / (position + qty) if (position + qty) > 0 else price
                position += qty
        else:
            if position > 0:
                close_qty = min(qty, position)
                realized += (price - avg_cost) * close_qty * TMF_POINT_VALUE
                qty -= close_qty
                position -= close_qty
            if qty > 0:
                avg_cost = (avg_cost * abs(position) + price * qty) / (abs(position) + qty) if (abs(position) + qty) > 0 else price
                position -= qty
    return realized, position, avg_cost


# ── 頁面架構 ────────────────────────────────────────────
st.title("📈 TMF 交易系統")
tab_trade, tab_bt = st.tabs(["交易紀錄", "回測結果"])


# ══════════════════════════════════════════════════════
# Tab 1：交易紀錄（原有內容）
# ══════════════════════════════════════════════════════
with tab_trade:
    df = load_trade_data()

    if df.empty:
        st.info("尚無交易資料")
    else:
        available_dates = sorted(df["date"].unique(), reverse=True)
        today = datetime.now(TZ_TW).date()
        default_idx = list(available_dates).index(today) if today in available_dates else 0
        selected_date = st.selectbox("選擇日期", options=available_dates, index=default_idx,
                                     format_func=lambda d: str(d))

        day_df = df[df["date"] == selected_date].copy()
        filled = day_df[day_df["order_status"].str.contains("Filled", na=False)]

        realized_pnl, _, avg_cost = calc_pnl(filled)
        cur_pos = int(filled.iloc[-1]["target_pos"]) if not filled.empty else 0

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
        slip_pts = pd.to_numeric(filled["slippage_pts"], errors="coerce").dropna()
        c4.metric("平均滑價", f"{int(slip_pts.mean()):+d} 點" if len(slip_pts) else "—")

        st.divider()

        if filled.empty:
            st.info("當日無成交紀錄")
        else:
            display = filled[["datetime", "action", "contract", "quantity",
                               "signal_price", "fill_price", "slippage_pts", "slippage_twd",
                               "pos_before", "target_pos", "order_status"]].copy()
            display["datetime"] = display["datetime"].dt.strftime("%H:%M:%S")
            for col in ["signal_price", "fill_price", "slippage_twd"]:
                display[col] = display[col].apply(lambda x: int(x) if pd.notna(x) else "")
            display["slippage_pts"] = display["slippage_pts"].apply(
                lambda x: f"{int(x):+d}" if pd.notna(x) else "")
            display["action"] = display["action"].map({"BUY": "買", "SELL": "賣"})
            display["order_status"] = display["order_status"].str.replace("Status.", "", regex=False)
            display.columns = ["時間", "動作", "合約", "口數",
                                "信號價", "成交價", "滑價(點)", "滑價(元)", "前部位", "目標", "狀態"]

            def color_row(row):
                bg = "background-color: #0d2b1a" if row["動作"] == "買" else "background-color: #2b0d0d"
                return [bg] * len(row)

            st.dataframe(display.style.apply(color_row, axis=1),
                         use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("歷史帳戶餘額（元）")
        bal_df = load_balance_data()
        if not bal_df.empty:
            daily_bal = (bal_df.sort_values("datetime").groupby("date").last().reset_index())
            daily_bal["date"] = daily_bal["date"].astype(str)
            daily_bal = daily_bal.set_index("date")
            col_a, col_b = st.columns(2)
            with col_a:
                st.metric("最新權益數", f"{int(daily_bal['equity'].iloc[-1]):,} 元")
            with col_b:
                st.metric("可動用保證金", f"{int(daily_bal['available_margin'].iloc[-1]):,} 元")
            st.line_chart(daily_bal[["equity"]], height=220)
            st.caption("每日帳戶明細")
            show_cols = ["equity", "today_balance", "future_settle_profitloss",
                         "future_open_position", "available_margin", "session"]
            st.dataframe(daily_bal[show_cols].rename(columns={
                "equity": "權益數", "today_balance": "本日餘額",
                "future_settle_profitloss": "期貨平倉損益",
                "future_open_position": "浮動損益",
                "available_margin": "可動用保證金", "session": "時段"}),
                use_container_width=True)
        else:
            st.info("尚無帳戶餘額資料（每日 13:46 及 05:01 自動記錄）")

        st.caption("資料每次成交後自動更新 · 快取 60 秒")


# ══════════════════════════════════════════════════════
# Tab 2：回測結果
# ══════════════════════════════════════════════════════
with tab_bt:
    st.subheader("回測資料：MXF 1分K，74 個交易日（2026-02-23 ~ 2026-05-23）")
    st.caption("手續費已含：NT$40 round-trip（4點）")

    bt_tab_a, bt_tab_b = st.tabs(["策略 A：波動突破", "策略 B：固定時間進多＋高掛 Limit"])

    # ── 策略 A ──────────────────────────────────────
    with bt_tab_a:
        st.markdown("""
        **邏輯：** 觸發時間 K 棒開盤價 ± offset，突破才進場
        **觸發時間：** 08:46 / 09:00 / 13:30 / 13:45
        **參數：** offset × target × stop × time_limit（3,920 組）
        """)

        rdf_a = load_backtest_a()
        if rdf_a.empty:
            st.info("回測資料載入中...")
        else:
            cols_a = ["trigger", "offset", "target", "stop", "time_limit",
                      "trades", "win_rate", "total_pnl", "avg_pnl", "sharpe"]
            cols_a = [c for c in cols_a if c in rdf_a.columns]

            st.markdown("#### Top 20（Sharpe）")
            top20 = rdf_a.sort_values("sharpe", ascending=False).head(20)[cols_a]
            top20.columns = [c.replace("_", " ").title() for c in top20.columns]
            st.dataframe(top20, use_container_width=True, hide_index=True)

            st.markdown("#### 各觸發時間最佳組合")
            best_per = rdf_a.loc[rdf_a.groupby("trigger")["sharpe"].idxmax()][cols_a]
            best_per.columns = [c.replace("_", " ").title() for c in best_per.columns]
            st.dataframe(best_per, use_container_width=True, hide_index=True)

            st.markdown("#### 總損益 by 觸發時間（最佳組合）")
            chart_data = rdf_a.loc[rdf_a.groupby("trigger")["sharpe"].idxmax()][["trigger", "total_pnl"]]
            chart_data = chart_data.set_index("trigger")
            st.bar_chart(chart_data)

    # ── 策略 B ──────────────────────────────────────
    with bt_tab_b:
        st.markdown("""
        **邏輯：** 固定時間直接進多，掛 limit sell 在 entry + target，時間到市價平
        **方向濾網：** 09:00 用 08:46–08:59 方向；08:46 用夜盤方向（前日 15:00~當日 05:00）
        **最大單筆損失：** 09:00 版不定（timeout）；08:46 版固定（-stop-4 點）
        """)

        rdf_b = load_backtest_b()
        if rdf_b.empty:
            st.info("回測資料載入中...")
        else:
            cols_b = ["trigger", "target", "stop", "time_limit", "trades",
                      "tp_rate", "sl_rate", "timeout_rate", "win_rate",
                      "total_pnl", "avg_pnl", "loss_avg", "sharpe"]
            cols_b = [c for c in cols_b if c in rdf_b.columns]

            st.markdown("#### Top 20（Sharpe）")
            top20b = rdf_b.sort_values("sharpe", ascending=False).head(20)[cols_b]
            top20b.columns = [c.replace("_", " ").title() for c in top20b.columns]
            st.dataframe(top20b, use_container_width=True, hide_index=True)

            c1, c2 = st.columns(2)

            with c1:
                st.markdown("#### 09:00：各時間限制（stop=0，方向濾網）")
                sub_900 = (rdf_b[(rdf_b["trigger"] == "現貨開盤") & (rdf_b["stop"] == 0)]
                           .sort_values("time_limit")[cols_b])
                sub_900.columns = [c.replace("_", " ").title() for c in sub_900.columns]
                st.dataframe(sub_900, use_container_width=True, hide_index=True)

            with c2:
                st.markdown("#### 08:46：各停損設定（方向濾網）")
                sub_846 = (rdf_b[(rdf_b["trigger"] == "期貨開盤") & (rdf_b["time_limit"] == 5)]
                           .sort_values("stop")[cols_b])
                sub_846.columns = [c.replace("_", " ").title() for c in sub_846.columns]
                st.dataframe(sub_846, use_container_width=True, hide_index=True)

            st.markdown("#### Sharpe 比較（各觸發 × 各 target，stop=0）")
            pivot = (rdf_b[rdf_b["stop"] == 0]
                     .groupby(["trigger", "target"])["sharpe"].max()
                     .unstack("trigger")
                     .fillna(0))
            st.bar_chart(pivot, height=300)

    st.caption("回測資料快取 5 分鐘 · 完整結果見 backtest/results_*.csv")
