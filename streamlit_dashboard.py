import streamlit as st
import pandas as pd
import requests
import base64
import io
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="TMF 交易紀錄", page_icon="📈", layout="wide")

TZ_TW = timezone(timedelta(hours=8))
REPO            = "KevinYang515/trading-dashboard"
API_URL         = f"https://api.github.com/repos/{REPO}/contents/logs/trade_records.csv"
BALANCE_API_URL = f"https://api.github.com/repos/{REPO}/contents/logs/balance_log.csv"
BT_A_URL        = f"https://api.github.com/repos/{REPO}/contents/backtest/results_breakout.csv"
BT_B_URL        = f"https://api.github.com/repos/{REPO}/contents/backtest/results_scalp.csv"
PAPER_LOG_URL   = f"https://api.github.com/repos/{REPO}/contents/logs/paper_trade_log.csv"
SIGNALS_DIR_URL = f"https://api.github.com/repos/{REPO}/contents/daily_signals"
TMF_POINT_VALUE = 10
HORIZONS        = [1, 3, 5, 7, 10, 21]


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


@st.cache_data(ttl=120)
def load_paper_log():
    try:
        resp = requests.get(PAPER_LOG_URL, timeout=10)
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8-sig")
        return pd.read_csv(io.StringIO(content), comment='#')
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=120)
def list_daily_signals():
    try:
        resp = requests.get(SIGNALS_DIR_URL, timeout=10)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return sorted([it["name"] for it in resp.json() if it["name"].endswith(".csv")],
                      reverse=True)
    except Exception:
        return []


@st.cache_data(ttl=120)
def load_signal_csv(filename):
    url = f"https://api.github.com/repos/{REPO}/contents/daily_signals/{filename}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8-sig")
        return pd.read_csv(io.StringIO(content))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_crash_study(threshold: float = -0.05):
    try:
        import yfinance as yf
        twii = yf.download("^TWII", start="2000-01-01", progress=False, auto_adjust=True)
        if twii.empty:
            return pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float)
        if isinstance(twii.columns, pd.MultiIndex):
            twii.columns = twii.columns.get_level_values(0)
        close = twii["Close"].dropna()
        daily_ret = close.pct_change()
        crash_days = daily_ret[daily_ret <= threshold].index

        records = []
        for date in crash_days:
            loc = close.index.get_loc(date)
            row = {
                "日期": date.strftime("%Y-%m-%d"),
                "當日跌幅(%)": round(daily_ret[date] * 100, 2),
                "收盤價": int(round(close[date], 0)),
            }
            for h in HORIZONS:
                future_loc = loc + h
                if future_loc < len(close):
                    fwd = (close.iloc[future_loc] - close[date]) / close[date]
                    row[f"+{h}d(%)"] = round(fwd * 100, 2)
                else:
                    row[f"+{h}d(%)"] = None
            records.append(row)

        if not records:
            return pd.DataFrame(), pd.DataFrame(), daily_ret

        events_df = pd.DataFrame(records).sort_values("日期", ascending=False).reset_index(drop=True)

        stats_rows = []
        for h in HORIZONS:
            col = f"+{h}d(%)"
            vals = events_df[col].dropna()
            if len(vals) == 0:
                continue
            stats_rows.append({
                "持有期間": f"+{h} 交易日",
                "樣本數": int(len(vals)),
                "上漲次數": int((vals > 0).sum()),
                "上漲機率": f"{(vals > 0).mean()*100:.0f}%",
                "平均報酬": f"{vals.mean():.1f}%",
                "中位數": f"{vals.median():.1f}%",
                "最差": f"{vals.min():.1f}%",
                "最佳": f"{vals.max():.1f}%",
                "_mean": vals.mean(),
            })
        stats_df = pd.DataFrame(stats_rows)
        return events_df, stats_df, daily_ret

    except Exception as e:
        st.error(f"極端行情資料載入失敗：{e}")
        return pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float)


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

PAGES = [
    "📊 TMF 交易紀錄",
    "📈 回測結果",
    "⚠️ 極端行情研究",
    "🎯 Strategy D（出處置）",
]
with st.sidebar:
    st.markdown("### 選單")
    page = st.radio("page_nav", options=PAGES, label_visibility="collapsed")
    st.divider()
    st.caption("**TMF**：微型台指期貨自動交易")
    st.caption("**Strategy D**：出處置股動能跟進（模擬中）")


# ══════════════════════════════════════════════════════
# Page 1：TMF 交易紀錄
# ══════════════════════════════════════════════════════
if page == "📊 TMF 交易紀錄":
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
# Page 2：回測結果
# ══════════════════════════════════════════════════════
elif page == "📈 回測結果":
    st.subheader("回測資料：MXF 1分K，74 個交易日（2026-02-23 ~ 2026-05-23）")
    st.caption("手續費已含：NT$40 round-trip（4點）")

    bt_tab_a, bt_tab_b = st.tabs(["策略 A：波動突破", "策略 B：固定時間進多＋高掛 Limit"])

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


# ══════════════════════════════════════════════════════
# Page 3：極端行情研究
# ══════════════════════════════════════════════════════
elif page == "⚠️ 極端行情研究":
    st.subheader("極端行情研究：台灣加權指數單日大跌後走勢")
    st.caption("資料來源：Yahoo Finance ^TWII（2000 年至今）· 快取 1 小時")

    col_ctrl1, col_ctrl2 = st.columns([1, 3])
    with col_ctrl1:
        threshold_pct = st.selectbox(
            "觸發條件（單日跌幅）",
            options=[-3, -4, -5, -6, -7, -8, -10],
            index=2,
            format_func=lambda x: f"≤ {x}%"
        )
    threshold = threshold_pct / 100.0

    events_df, stats_df, daily_ret = load_crash_study(threshold)

    if events_df.empty:
        st.warning("資料載入失敗，請稍後重試")
    else:
        n_events = len(events_df)
        last_event_date = events_df["日期"].iloc[0]
        last_drop = events_df["當日跌幅(%)"].iloc[0]

        # ── 本次事件提示 ──────────────────────────────
        st.info(
            f"歷史上共 **{n_events}** 次單日跌幅 ≤ {threshold_pct}%　｜　"
            f"最近一次：**{last_event_date}**（{last_drop:+.1f}%）"
        )

        # ── 統計摘要 ──────────────────────────────────
        st.markdown("### 各持有期間統計摘要")

        display_stats = stats_df.drop(columns=["_mean"], errors="ignore")

        def color_prob(val):
            try:
                p = float(val.replace("%", ""))
                if p >= 60:
                    return "color: #4caf50; font-weight: bold"
                elif p <= 40:
                    return "color: #f44336; font-weight: bold"
            except Exception:
                pass
            return ""

        def color_return(val):
            try:
                v = float(val.replace("%", ""))
                if v > 0:
                    return "color: #4caf50"
                elif v < 0:
                    return "color: #f44336"
            except Exception:
                pass
            return ""

        styled = display_stats.style \
            .map(color_prob, subset=["上漲機率"]) \
            .map(color_return, subset=["平均報酬", "中位數", "最差", "最佳"])

        st.dataframe(styled, use_container_width=True, hide_index=True)

        # ── 平均報酬走勢圖 ────────────────────────────
        st.markdown("### 平均報酬隨持有天數變化")
        if "_mean" in stats_df.columns:
            chart_df = stats_df[["持有期間", "_mean"]].copy()
            chart_df = chart_df.rename(columns={"_mean": "平均報酬(%)", "持有期間": "期間"})
            chart_df = chart_df.set_index("期間")
            st.bar_chart(chart_df, height=280)

        # ── 分佈：全部 6 個期間 ────────────────────────
        st.markdown("### 報酬分佈（各持有期間）")
        row1_cols = st.columns(3)
        row2_cols = st.columns(3)
        dist_grid = row1_cols + row2_cols
        for i, h in enumerate(HORIZONS):
            col_name = f"+{h}d(%)"
            if col_name not in events_df.columns:
                continue
            vals = events_df[col_name].dropna()
            if vals.empty:
                continue
            bins = pd.cut(vals, bins=12)
            hist = vals.groupby(bins, observed=True).count()
            hist.index = [f"{b.left:.1f}~{b.right:.1f}" for b in hist.index]
            win_rate = (vals > 0).mean() * 100
            mean_val = vals.mean()
            with dist_grid[i]:
                st.markdown(
                    f"**+{h} 交易日**　"
                    f"<span style='color:{'#4caf50' if win_rate>=50 else '#f44336'}'>"
                    f"上漲 {win_rate:.0f}%</span>　"
                    f"均值 {mean_val:+.1f}%",
                    unsafe_allow_html=True,
                )
                st.bar_chart(hist, height=180)

        # ── 完整歷史事件表 ────────────────────────────
        st.markdown("### 完整歷史事件紀錄")
        st.caption("按跌幅日期由近到遠排列；綠色 = 正報酬，紅色 = 負報酬")

        fwd_cols = [f"+{h}d(%)" for h in HORIZONS]

        def color_fwd(val):
            try:
                v = float(val)
                if v > 0:
                    return "background-color: #0d2b1a"
                elif v < 0:
                    return "background-color: #2b0d0d"
            except Exception:
                pass
            return ""

        events_styled = events_df.style.map(color_fwd, subset=fwd_cols)
        st.dataframe(events_styled, use_container_width=True, hide_index=True, height=500)

        # ── 關鍵結論 ──────────────────────────────────
        st.markdown("### 關鍵結論")
        if not stats_df.empty:
            row1d = stats_df[stats_df["持有期間"] == "+1 交易日"].iloc[0] if not stats_df[stats_df["持有期間"] == "+1 交易日"].empty else None
            row5d = stats_df[stats_df["持有期間"] == "+5 交易日"].iloc[0] if not stats_df[stats_df["持有期間"] == "+5 交易日"].empty else None
            row21d = stats_df[stats_df["持有期間"] == "+21 交易日"].iloc[0] if not stats_df[stats_df["持有期間"] == "+21 交易日"].empty else None

            conclusions = []
            if row1d is not None:
                p1 = float(row1d["上漲機率"].replace("%", ""))
                m1 = float(row1d["平均報酬"].replace("%", ""))
                conclusions.append(
                    f"- **隔日（+1d）**：上漲機率 **{row1d['上漲機率']}**，平均報酬 **{row1d['平均報酬']}**"
                    + ("（統計上傾向繼續跌）" if p1 < 50 else "（統計上傾向反彈）")
                )
            if row5d is not None:
                conclusions.append(
                    f"- **一週（+5d）**：上漲機率 **{row5d['上漲機率']}**，平均報酬 **{row5d['平均報酬']}**"
                )
            if row21d is not None:
                p21 = float(row21d["上漲機率"].replace("%", ""))
                conclusions.append(
                    f"- **一個月（+21d）**：上漲機率 **{row21d['上漲機率']}**，平均報酬 **{row21d['平均報酬']}**"
                    + ("（長線多半回穩）" if p21 >= 55 else "")
                )
            if conclusions:
                st.markdown("\n".join(conclusions))
            st.markdown(
                f"> **操作參考**：跌幅 ≤ {threshold_pct}% 的極端事件後，"
                "隔日往往仍有賣壓（恐慌未消化）；1～2 週後若無新利空，"
                "歷史顯示多數案例出現明顯反彈。現貨部位是否調節，"
                "可參考隔日成交量與是否出現止跌訊號，而非單純依據跌幅。"
            )


# ══════════════════════════════════════════════════════
# Page 4：Strategy D（出處置動能跟進）
# ══════════════════════════════════════════════════════
elif page == "🎯 Strategy D（出處置）":
    st.header("🎯 Strategy D — 出處置動能跟進")
    st.caption("⚠️ 目前全部紙上模擬，未實單。回測 3.5 年 135 筆，Sharpe 8.89。")

    # ── 一句話策略說明 ─────────────────────────────────
    with st.container(border=True):
        st.markdown("""
**做什麼？** 找昨天接近漲停（≥9%）的中大型股，**如果它最近剛結束處置**（在處置結束後 1-14 天內），今天開盤買進。

**怎麼出場？** TP +10 tick 達標賣 / 跌 2 tick 移動停損 / 13:00 強制平倉。
""")

    # ── 兩個 track 卡片 ────────────────────────────────
    col_a, col_b = st.columns(2)
    with col_a:
        with st.container(border=True):
            st.markdown("#### 💰 D-Cash（現股）")
            st.markdown("""
- 09:00 集合競價買入（掛漲停限價）
- ✅ 已完整回測，Sharpe 8.89
- 📍 **目前模擬中，準備上線**
""")
    with col_b:
        with st.container(border=True):
            st.markdown("#### ⚡ D-SSF（個股期貨）")
            st.markdown("""
- 08:45 期貨開盤買入
- ⏳ 沒歷史分鐘資料、無法精確回測
- 📍 **紙上模擬累積樣本中**
""")

    st.markdown(
        "📖 完整策略邏輯：[STRATEGY_D_PLAYBOOK.md]"
        "(https://github.com/KevinYang515/tmf-bot/blob/main/STRATEGY_D_PLAYBOOK.md)"
    )

    st.divider()

    # ── 最新候選訊號 ───────────────────────────────────
    st.subheader("🔔 最新進場候選")
    signal_files = list_daily_signals()
    if not signal_files:
        st.info("尚無候選清單。本機跑 `python backtest/daily_signal.py` 產生後 git push。")
    else:
        sel_sig = st.selectbox("選擇日期", options=signal_files, index=0,
                               format_func=lambda f: f.replace(".csv", "") + " 進場")
        sig = load_signal_csv(sel_sig)
        if sig.empty:
            st.info(f"{sel_sig} 無候選")
        else:
            ssf_n = int(sig['has_ssf'].sum()) if 'has_ssf' in sig.columns else 0
            c1, c2, c3 = st.columns(3)
            c1.metric("總候選", f"{len(sig)} 檔")
            c2.metric("有個股期貨", f"{ssf_n} 檔")
            c3.metric("只能做現股", f"{len(sig) - ssf_n} 檔")

            # 友善欄位 + 重排
            view = sig.copy()
            for c in ['prev_close', 'ret_prev_%', 'vol_ratio', 'days_post_disp',
                      'whale_chg_%', 'limit_up']:
                if c in view.columns:
                    view[c] = pd.to_numeric(view[c], errors='coerce')

            if 'has_ssf' in view.columns:
                view['期貨'] = view['has_ssf'].map({True: '✓', False: '—'})
            display_cols = []
            rename_map = {
                'code': '股號', 'name': '股名',
                'cap_label': '規模', 'prev_close': '昨日收盤',
                'ret_prev_%': '昨日漲幅(%)', 'vol_ratio': '量比',
                'days_post_disp': '出處置 N 天', 'whale_chg_%': '大戶變化(%)',
                'limit_up': '今日漲停價',
            }
            for c in rename_map:
                if c in view.columns: display_cols.append(c)
            display_cols.append('期貨')

            view_show = view[display_cols].rename(columns=rename_map)
            # 規模 friendly
            if '規模' in view_show.columns:
                view_show['規模'] = view_show['規模'].map({
                    'A_大型': '大型 >500億', 'B_中型': '中型 100-500億'})

            st.dataframe(view_show, use_container_width=True, hide_index=True,
                         column_config={
                             '昨日漲幅(%)': st.column_config.NumberColumn(format="%.1f%%"),
                             '量比': st.column_config.NumberColumn(format="%.2fx"),
                             '大戶變化(%)': st.column_config.NumberColumn(format="%+.2f"),
                             '昨日收盤': st.column_config.NumberColumn(format="%.1f"),
                             '今日漲停價': st.column_config.NumberColumn(format="%.1f"),
                         })

            st.caption("💡 下單流程：8:30-9:00 之間掛「限價買 @ 漲停價」進入集合競價，9:00 cross 成交在開盤價。")

    st.divider()

    # ── Paper Trade 紀錄 ──────────────────────────────
    st.subheader("📊 模擬交易紀錄")
    log = load_paper_log()

    if log.empty or 'date' not in log.columns or log['date'].isna().all():
        st.info("⏳ 還沒有任何模擬交易紀錄。\n\n每天試一筆，慢慢累積樣本後就會看到統計數字。")
    else:
        log = log.dropna(subset=['date'])
        log['date'] = pd.to_datetime(log['date'], errors='coerce')
        log = log.dropna(subset=['date'])
        for c in ['actual_pnl_per_share', 'actual_pnl_pct', 'slippage_ticks',
                  'signal_ret_prev', 'signal_days_post_disp']:
            if c in log.columns:
                log[c] = pd.to_numeric(log[c], errors='coerce')

        # 統計 metrics
        pnl = log['actual_pnl_per_share'].dropna() if 'actual_pnl_per_share' in log.columns else pd.Series()
        if not pnl.empty:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("總筆數", f"{len(log)}")
            c2.metric("勝率", f"{(pnl > 0).mean() * 100:.0f}%")
            c3.metric("平均賺/股", f"{pnl.mean():+.2f} 元")
            c4.metric("累積總 P&L", f"{pnl.sum():+.0f} 元/股")

            # By Track
            if 'track' in log.columns:
                st.markdown("**現股 vs 期貨 績效對比：**")
                track_agg = log.dropna(subset=['actual_pnl_per_share']).groupby('track').agg(
                    n=('actual_pnl_per_share', 'count'),
                    勝率=('actual_pnl_per_share', lambda x: (x > 0).mean() * 100),
                    累積=('actual_pnl_per_share', 'sum'),
                    平均=('actual_pnl_per_share', 'mean'),
                ).round(2).rename(columns={'n': '筆數'})
                track_agg.index = track_agg.index.map(
                    {'D-Cash': '💰 現股', 'D-SSF': '⚡ 期貨'}).fillna(track_agg.index)
                st.dataframe(track_agg, use_container_width=True)

        # 明細
        st.markdown("**所有紀錄：**")
        show = log[['date', 'track', 'code', 'name', 'actual_entry',
                    'actual_exit', 'exit_reason', 'actual_pnl_per_share']].copy() \
                if 'actual_entry' in log.columns else log
        if 'actual_entry' in log.columns:
            show.columns = ['日期', '類型', '股號', '股名', '進場價', '出場價', '出場原因', '損益/股']
        st.dataframe(show.sort_values(show.columns[0], ascending=False),
                     use_container_width=True, hide_index=True)

    st.divider()

    # ── 操作說明 ───────────────────────────────────────
    with st.expander("📖 怎麼開始？（紙上模擬流程）"):
        st.markdown("""
1. **前一天傍晚**（17:30 後）
   本機跑 `python backtest/daily_signal.py` → 自動產生明日候選 CSV
   → `git push` 到網站

2. **隔天早上 8:30-9:00**
   看「最新進場候選」頁面，紙上模擬決定要做哪幾檔

3. **9:00（現股）or 8:45（期貨）**
   假裝進場，記錄實際開盤價

4. **13:00 前**
   觀察 TP / 停損 / 收盤平倉，記錄結果

5. **手動填 `logs/paper_trade_log.csv`** → `git push` → 此頁自動更新
""")
