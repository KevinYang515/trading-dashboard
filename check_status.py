"""
查詢永豐帳戶目前部位、今日成交、以及 trade_records.csv 的滑價統計
用法: python check_status.py
"""
import shioaji as sj
import os
import csv
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()
TZ_TW = timezone(timedelta(hours=8))
TMF_POINT_VALUE = 10

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ['SJ_API_KEY'],
    secret_key=os.environ['SJ_SECRET_KEY'],
    contracts_timeout=10000
)

now = datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
print(f"\n{'='*50}")
print(f"  帳戶狀況查詢  {now}")
print(f"{'='*50}")

# ── 1. 目前部位 ──────────────────────────────────
print("\n【目前部位】")
positions = api.list_positions(api.futopt_account)
tmf_positions = [p for p in positions if "TMF" in p.code]

if not tmf_positions:
    print("  目前無部位（空手）")
else:
    for p in tmf_positions:
        direction = "多" if p.direction.value == "Buy" else "空"
        pnl_sign  = "+" if p.pnl >= 0 else ""
        print(f"  {p.code} | {direction} {p.quantity} 口 | "
              f"成本:{p.price:.0f} | 現價:{p.last_price:.0f} | "
              f"浮動損益: {pnl_sign}{p.pnl:.0f} 元")

# ── 2. 今日成交明細 ──────────────────────────────
print("\n【今日委託/成交】")
api.update_status(api.futopt_account)
trades = api.list_trades()
tmf_trades = [t for t in trades if "TMF" in t.contract.code]

if not tmf_trades:
    print("  今日無成交記錄")
else:
    for t in tmf_trades:
        s = t.status
        action = t.order.action.value
        deals  = getattr(s, 'deals', [])
        if deals:
            total_qty  = sum(d.quantity for d in deals)
            fill_price = sum(d.price * d.quantity for d in deals) / total_qty
            fill_str   = f"成交價:{fill_price:.0f}"
        else:
            fill_str = f"狀態:{s.status.value}"
        order_time = s.order_datetime.strftime("%H:%M:%S") if s.order_datetime else "--"
        print(f"  {order_time} | {t.contract.code} | {action} {s.deal_quantity}口 | {fill_str}")

# ── 3. trade_records.csv 滑價統計 ────────────────
print("\n【滑價統計（來自 trade_records.csv）】")
csv_path = "logs/trade_records.csv"
try:
    rows = []
    with open(csv_path, encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if row['slippage_pts']:
                rows.append(float(row['slippage_pts']))

    if not rows:
        print("  尚無滑價資料（fill_price 尚未記錄）")
    else:
        avg = sum(rows) / len(rows)
        mx  = max(rows)
        print(f"  筆數: {len(rows)} | 平均滑價: {avg:+.1f} 點 ({avg*TMF_POINT_VALUE:+.0f} 元)")
        print(f"  最大滑價: {mx:+.1f} 點 ({mx*TMF_POINT_VALUE:+.0f} 元)")
except FileNotFoundError:
    print("  找不到 trade_records.csv")

print()
