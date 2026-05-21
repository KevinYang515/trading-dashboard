"""
TMF 波動突破策略回測
獨立腳本，不影響 app.py 交易機器人

策略邏輯：
  觸發時刻（開盤/收盤/特定時間）快照當前價 P
  掛雙邊觸發單（OCO模擬）：
    BUY  STOP @ P + offset  → 目標 +target pts / 停損 -stop pts
    SELL STOP @ P - offset  → 目標 -target pts / 停損 +stop pts
  time_limit 分鐘內未觸發 → 取消
"""

import shioaji as sj
import os
import sys
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from datetime import datetime, timedelta
from itertools import product

# ── 設定 ──────────────────────────────────────────────
ENV_PATH   = os.path.join(os.path.dirname(__file__), '..', '.env')
DAYS_BACK  = 90        # 拉幾天歷史資料
POINT_VAL      = 10    # TMF 每點 10 元
COMMISSION_PTS = 2.5   # 手續費（round-trip，點）

# 觸發時間（HH:MM，台灣時間）
TRIGGER_TIMES = {
    "期貨開盤": "08:45",
    "現貨開盤": "09:00",
    "現貨收盤": "13:30",
    "期貨日盤收": "13:45",
}

# 參數掃描空間
OFFSETS    = [5, 10, 15, 20]               # 觸發距離（點）
TARGETS    = [10, 20, 30, 40, 60, 80, 100] # 獲利目標（點）
STOPS      = [5, 10, 15, 20, 30, 40, 50]   # 停損（點）
TIME_LIMS  = [5, 10, 15, 30, 60]           # 訂單有效時間（分鐘）
# ─────────────────────────────────────────────────────


def fetch_kbars(api, days: int) -> pd.DataFrame:
    contract = api.Contracts.Futures.MXF.MXFR1
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end   = datetime.now().strftime("%Y-%m-%d")
    kbars = api.kbars(contract, start=start, end=end)
    df = pd.DataFrame({**kbars})
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values("ts").reset_index(drop=True)
    df["date"] = df["ts"].dt.date
    df["time"] = df["ts"].dt.strftime("%H:%M")
    return df


def simulate_trade(df_day: pd.DataFrame, trigger_time: str,
                   offset: int, target: int, stop: int, time_limit: int) -> dict | None:
    """
    單日單觸發時間的回測，回傳交易結果或 None（未觸發）
    """
    # 找觸發那根 K 棒
    snap = df_day[df_day["time"] == trigger_time]
    if snap.empty:
        return None

    snap_idx  = snap.index[0]
    snap_open = float(snap.iloc[0]["Open"])   # 用開盤價當快照價

    buy_entry  = snap_open + offset
    sell_entry = snap_open - offset
    buy_tp     = buy_entry  + target
    buy_sl     = buy_entry  - stop
    sell_tp    = sell_entry - target
    sell_sl    = sell_entry + stop

    # 往後的 K 棒（含觸發那根，最多 time_limit 根）
    window = df_day.loc[snap_idx: snap_idx + time_limit - 1]

    direction = None
    entry_price = None

    for _, bar in window.iterrows():
        hi = float(bar["High"])
        lo = float(bar["Low"])

        if direction is None:
            # 未進場：看是否觸發
            if hi >= buy_entry:
                direction   = "LONG"
                entry_price = buy_entry
            elif lo <= sell_entry:
                direction   = "SHORT"
                entry_price = sell_entry

        if direction == "LONG":
            if lo <= buy_sl:
                net = -stop - COMMISSION_PTS
                return {"direction": "LONG", "result": "STOP",
                        "pnl_pts": net, "pnl_twd": net * POINT_VAL,
                        "entry": entry_price}
            if hi >= buy_tp:
                net = target - COMMISSION_PTS
                return {"direction": "LONG", "result": "TP",
                        "pnl_pts": net, "pnl_twd": net * POINT_VAL,
                        "entry": entry_price}

        elif direction == "SHORT":
            if hi >= sell_sl:
                net = -stop - COMMISSION_PTS
                return {"direction": "SHORT", "result": "STOP",
                        "pnl_pts": net, "pnl_twd": net * POINT_VAL,
                        "entry": entry_price}
            if lo <= sell_tp:
                net = target - COMMISSION_PTS
                return {"direction": "SHORT", "result": "TP",
                        "pnl_pts": net, "pnl_twd": net * POINT_VAL,
                        "entry": entry_price}

    # 超時未平倉 → 用最後一根收盤強平
    if direction is not None:
        last_close = float(window.iloc[-1]["Close"])
        raw_pnl = (last_close - entry_price) if direction == "LONG" else (entry_price - last_close)
        net = round(raw_pnl - COMMISSION_PTS, 1)
        return {"direction": direction, "result": "TIMEOUT",
                "pnl_pts": net, "pnl_twd": round(net * POINT_VAL, 0),
                "entry": entry_price}

    return None  # 未觸發


def run_backtest(df: pd.DataFrame, trigger_name: str, trigger_time: str,
                 offset: int, target: int, stop: int, time_limit: int) -> dict:
    trades = []
    for date, day_df in df.groupby("date"):
        day_df = day_df.reset_index(drop=True)
        result = simulate_trade(day_df, trigger_time, offset, target, stop, time_limit)
        if result:
            result["date"] = date
            result["trigger"] = trigger_name
            trades.append(result)

    if not trades:
        return {}

    tdf = pd.DataFrame(trades)
    wins  = (tdf["pnl_pts"] > 0).sum()
    total = len(tdf)
    total_pnl = tdf["pnl_twd"].sum()
    avg_pnl   = tdf["pnl_twd"].mean()
    sharpe    = (tdf["pnl_pts"].mean() / tdf["pnl_pts"].std() * np.sqrt(252)
                 if tdf["pnl_pts"].std() > 0 else 0)

    return {
        "trigger":    trigger_name,
        "offset":     offset,
        "target":     target,
        "stop":       stop,
        "time_limit": time_limit,
        "trades":     total,
        "win_rate":   round(wins / total * 100, 1),
        "total_pnl":  int(total_pnl),
        "avg_pnl":    round(avg_pnl, 0),
        "sharpe":     round(sharpe, 2),
    }


def main():
    load_dotenv(ENV_PATH)
    api_key    = os.environ.get("SJ_API_KEY")
    secret_key = os.environ.get("SJ_SECRET_KEY")

    print("=== 連線 Shioaji ===")
    api = sj.Shioaji(simulation=False)
    api.login(api_key, secret_key, fetch_contract=True)

    print(f"=== 拉取 {DAYS_BACK} 天 1分K ===")
    df = fetch_kbars(api, DAYS_BACK)
    api.logout()

    print(f"  資料: {len(df)} 筆, {df['date'].nunique()} 個交易日")
    print(f"  範圍: {df['ts'].min()} ~ {df['ts'].max()}")

    print("\n=== 開始參數掃描 ===")
    results = []
    combos = list(product(TRIGGER_TIMES.items(), OFFSETS, TARGETS, STOPS, TIME_LIMS))
    print(f"  共 {len(combos)} 組參數")

    for i, ((t_name, t_time), off, tgt, stp, tlim) in enumerate(combos):
        if i % 100 == 0:
            print(f"  進度: {i}/{len(combos)}")
        r = run_backtest(df, t_name, t_time, off, tgt, stp, tlim)
        if r:
            results.append(r)

    if not results:
        print("無結果")
        return

    rdf = pd.DataFrame(results)

    # 輸出 Top 20（依 Sharpe 排序）
    print("\n=== Top 20（Sharpe） ===")
    top = rdf.sort_values("sharpe", ascending=False).head(20)
    print(top.to_string(index=False))

    # 儲存完整結果
    out_path = os.path.join(os.path.dirname(__file__), "results.csv")
    rdf.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n完整結果已存至 {out_path}")

    # 各觸發時間最佳參數
    print("\n=== 各觸發時間最佳組合 ===")
    for t in TRIGGER_TIMES:
        sub = rdf[rdf["trigger"] == t].sort_values("sharpe", ascending=False).head(1)
        if not sub.empty:
            r = sub.iloc[0]
            print(f"  {t}: offset={r.offset} target={r.target} stop={r.stop} "
                  f"tlim={r.time_limit} | WR={r.win_rate}% EV={r.avg_pnl:.0f} Sharpe={r.sharpe}")


if __name__ == "__main__":
    main()
