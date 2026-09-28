#!/usr/bin/env python3
# coding: utf-8
"""
overnight_live.py
=================
隔日沖策略即時版（Shioaji）

用法:
    python overnight_live.py buy        # 13:25 跑: 算候選 + 下尾盤撮合單
    python overnight_live.py sell       # 09:00 跑: 賣昨日持倉
    python overnight_live.py status     # 查目前部位
    python overnight_live.py dry        # 不連永豐，僅計算候選

策略:
    買: 中型50-300億 + F1+F2+F3 + 漲幅>=2% + AMTBIG=3x + NH20 + 漲幅排名前2
    賣: 隔日 9:00 開盤 cross

設定:
    SIMULATION = True   → 永豐 sandbox（模擬下單，不影響真實帳戶）
    SIMULATION = False  → 真實下單（需 CA、需 SJ_PERSON_ID）

環境變數（讀 .env 或 OS env）:
    SJ_API_KEY, SJ_SECRET_KEY
    （真實上線才需要）SJ_CA_PATH, SJ_CA_PASS, SJ_PERSON_ID
"""
import sys
import os
import json
import csv
import time
import warnings
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

# ── 設定 ────────────────────────────────────────────────
SIMULATION  = True              # ★ 模擬模式（永豐 sandbox），上線後改 False
N_TOP       = 2
CAPITAL     = 2_000_000         # 本金（每檔 50% = 100 萬）— 2026-07-29 依整張配置誤差分析上修
COST_PCT    = 0.000285 * 2 + 0.003   # 0.357% (僅做試算顯示)

# 策略參數（須與 generate_trade_log.py 同步）
MA_WIN        = 20
MIN_CAP_YI    = 50              # 50億
MAX_CAP_YI    = 300             # 300億
MIN_TURN_WAN  = 3000            # 3000萬
VOL_EXP       = 1.5
MIN_RET       = 0.02            # 漲幅 >= 2%
AMT_MULT      = 3.0             # 成交金額 > 20日均 × 3
NOT_LIMIT_PCT = 0.098
PRICE_EXCLUDE_LOW  = 30          # 2026-07-27 贏虧歸因發現：收盤價 30-60 元區間
PRICE_EXCLUDE_HIGH = 60          # 是唯一負報酬的價格帶，排除後 Sharpe 3.60→4.09

TZ_TW       = timezone(timedelta(hours=8))
# 路徑：相對於腳本所在目錄
SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = Path(os.environ.get('FINLAB_DIR', SCRIPT_DIR / 'finlab_db'))
LOG_DIR     = Path(os.environ.get('OVERNIGHT_LOG_DIR', SCRIPT_DIR / 'logs'))
LOG_DIR.mkdir(exist_ok=True, parents=True)

# 嘗試讀 .env（先看當前目錄，再看 tmf-bot/.env）
try:
    from dotenv import load_dotenv
    for p in [SCRIPT_DIR / '.env', SCRIPT_DIR.parent / '.env']:
        if p.exists():
            load_dotenv(p)
            print(f'[env] loaded {p}')
            break
except ImportError:
    pass

# ════════════════════════════════════════════════════════
#  載入歷史資料（finlab cache）
# ════════════════════════════════════════════════════════
def load_history():
    """載入近 25 個交易日資料（足夠算 MA20 + 1 天 buffer）"""
    def _load(f):
        df = pd.read_feather(DATA_DIR / f)
        if 'date' in df.columns:
            df = df.set_index('date')
        df.index = pd.to_datetime(df.index)
        df.columns = df.columns.astype(str)
        return df.sort_index()

    print('[load] finlab cache...')
    close  = _load('price#收盤價.feather')
    high   = _load('price#最高價.feather')
    low    = _load('price#最低價.feather')
    vol    = _load('price#成交股數.feather')
    amount = _load('price#成交金額.feather')

    info = pd.read_feather(DATA_DIR / 'company_basic_info.feather')
    shares_col = info.columns[18]   # 已發行普通股數
    short_col  = info.columns[2]    # 公司簡稱
    shares = info.set_index('stock_id')[shares_col].astype(float)
    shares.index = shares.index.astype(str)
    names  = info.set_index('stock_id')[short_col].to_dict()

    # 只留最近 25 天加速
    close  = close.iloc[-25:]
    high   = high.iloc[-25:]
    low    = low.iloc[-25:]
    vol    = vol.iloc[-25:]
    amount = amount.iloc[-25:]

    print(f'[load] data through {close.index[-1].date()}, '
          f'{close.shape[1]} stocks')
    return dict(close=close, high=high, low=low, vol=vol, amount=amount,
                shares=shares, names=names)

# ════════════════════════════════════════════════════════
#  Universe：篩出中型股 50-300億
# ════════════════════════════════════════════════════════
def get_universe(hist):
    close   = hist['close']
    amount  = hist['amount']
    shares  = hist['shares'].reindex(close.columns)

    # 昨日（最新一日）收盤的市值
    last     = close.iloc[-1]
    mktcap   = last * shares
    avg_amt  = amount.rolling(MA_WIN).mean().iloc[-1]

    cond = (
        (avg_amt > MIN_TURN_WAN * 1e4)
        & (mktcap >= MIN_CAP_YI * 1e8)
        & (mktcap < MAX_CAP_YI * 1e8)
    )
    universe = last.index[cond.fillna(False)].tolist()
    print(f'[univ] {len(universe)} mid-cap stocks (50-300億)')
    return universe

# ════════════════════════════════════════════════════════
#  Shioaji 連線
# ════════════════════════════════════════════════════════
def setup_api():
    """登入 Shioaji。simulation=True 連 sandbox"""
    import shioaji as sj
    api = sj.Shioaji(simulation=SIMULATION)

    api_key = os.environ.get('SJ_API_KEY')
    sec_key = os.environ.get('SJ_SECRET_KEY')
    if not (api_key and sec_key):
        print('[err] 缺少 SJ_API_KEY / SJ_SECRET_KEY')
        sys.exit(1)

    print(f'[sj] login {"SIMULATION" if SIMULATION else "LIVE"}...')
    api.login(api_key=api_key, secret_key=sec_key, contracts_timeout=10000)

    # 真實下單才需要 CA
    if not SIMULATION:
        ca_path = os.environ.get('SJ_STK_CA_PATH', os.environ.get('SJ_CA_PATH'))
        ca_pass = os.environ.get('SJ_CA_PASS')
        person  = os.environ.get('SJ_PERSON_ID')
        if not (ca_path and ca_pass and person):
            print('[err] LIVE 模式缺 CA 環境變數')
            sys.exit(1)
        api.activate_ca(ca_path=ca_path, ca_passwd=ca_pass, person_id=person)

    print(f'[sj] logged in. stock_account={api.stock_account}')
    return api, sj

# ════════════════════════════════════════════════════════
#  抓即時 snapshot
# ════════════════════════════════════════════════════════
def fetch_snapshots(api, stock_ids):
    """逐檔抓即時 OHLCV"""
    contracts = []
    not_found = []
    for sid in stock_ids:
        try:
            c = api.Contracts.Stocks[sid]
            if c is not None:
                contracts.append(c)
            else:
                not_found.append(sid)
        except Exception:
            not_found.append(sid)

    print(f'[snap] {len(contracts)} contracts, {len(not_found)} not found in Shioaji')
    if not contracts:
        return pd.DataFrame()

    # 分批抓（Shioaji 一次抓太多會 timeout）
    snaps = []
    BATCH = 200
    for i in range(0, len(contracts), BATCH):
        chunk = contracts[i:i+BATCH]
        try:
            snaps.extend(api.snapshots(chunk))
        except Exception as e:
            print(f'[snap] batch {i} fail: {e}')

    rows = []
    for s in snaps:
        # Snapshot 沒有 yesterday_close 欄位（2026-07-31 上線首次實測發現），
        # 用 close - change_price（對比昨收的絕對變動）反推；此欄位僅供診斷,
        # compute_signals() 的訊號計算實際用的是 finlab 歷史快取的收盤價。
        rows.append({
            'stock_id'        : s.code,
            'cur_price'       : float(s.close),
            'cur_open'        : float(s.open),
            'cur_high'        : float(s.high),
            'cur_low'         : float(s.low),
            'cur_volume'      : float(s.total_volume) * 1000,  # total_volume 單位是「張」，×1000轉股數以對齊 finlab 成交股數
            'cur_amount'      : float(s.total_amount),     # 累計成交金額(元)
            'yesterday_close' : float(s.close) - float(s.change_price),
        })
    df = pd.DataFrame(rows).set_index('stock_id')
    print(f'[snap] got {len(df)} snapshots')
    return df

# ════════════════════════════════════════════════════════
#  計算策略訊號
#  ★ hist 必須是「截至昨日」的歷史，不含今日
#  ★ 20日均「含當日」，與 backtest 一致 (rolling(20) 包含當日)
# ════════════════════════════════════════════════════════
def compute_signals(snap, hist_yest):
    """
    snap     : 今日 snapshot (Shioaji 即時, 或 dry mode 下 finlab 今日)
    hist_yest: 截至「昨日」的歷史資料 (最後一筆 = 昨日)

    為了讓 ma20 含當日 (與回測一致):
      MA20(含今日) = (過去 19 日總和 + 今日) / 20
    """
    close   = hist_yest['close']
    vol     = hist_yest['vol']
    amount  = hist_yest['amount']

    # 過去 19 日總和 (從昨日往回 19 天)
    past19_close_sum = close.iloc[-19:].sum()
    past19_vol_sum   = vol.iloc[-19:].sum()
    past19_amt_sum   = amount.iloc[-19:].sum()

    # NH20: 過去 20 日 (不含今日) 最高收盤
    # 對應 backtest 的 close.rolling(20).max().shift(1)
    nh20_ref = close.rolling(MA_WIN).max().iloc[-1]
    yest_close = close.iloc[-1]

    # 對齊 index 並把「今日」併入算 MA20
    df = snap.copy()
    df['ma20']       = (past19_close_sum.reindex(df.index) + df['cur_price'])  / 20
    df['ma20_vol']   = (past19_vol_sum.reindex(df.index)   + df['cur_volume']) / 20
    df['ma20_amt']   = (past19_amt_sum.reindex(df.index)   + df['cur_amount']) / 20
    df['nh20_ref']   = nh20_ref.reindex(df.index)
    df['yest_close'] = yest_close.reindex(df.index)

    df['day_ret']    = df['cur_price'] / df['yest_close'] - 1
    df['vol_ratio']  = df['cur_volume'] / df['ma20_vol']
    df['amt_ratio']  = df['cur_amount'] / df['ma20_amt']

    # 條件
    df['F1'] = df['cur_price'] > df['ma20']                     # 站上 MA20
    df['F2'] = df['vol_ratio'] > VOL_EXP                        # 量比 > 1.5
    df['F3'] = df['cur_price'] > (df['cur_high'] + df['cur_low']) / 2   # 強勢收盤
    df['F_ret']     = df['day_ret'] >= MIN_RET                  # 漲幅 >= 2%
    df['F_amt']     = df['amt_ratio'] > AMT_MULT                # 超大量 3x
    df['F_nh20']    = df['cur_price'] >= df['nh20_ref']         # 突破 20日新高
    df['F_notlim']  = df['day_ret'] < NOT_LIMIT_PCT             # 排除漲停
    df['F_price']   = ~df['cur_price'].between(PRICE_EXCLUDE_LOW, PRICE_EXCLUDE_HIGH, inclusive='left')

    df['pass_all'] = (
        df['F1'] & df['F2'] & df['F3']
        & df['F_ret'] & df['F_amt'] & df['F_nh20'] & df['F_notlim'] & df['F_price']
    )

    qualify = df[df['pass_all']].copy()
    print(f'[sig] {len(qualify)} stocks pass all filters')
    top = qualify.nlargest(N_TOP, 'day_ret')
    return top, df

# ════════════════════════════════════════════════════════
#  下尾盤撮合單（買）
# ════════════════════════════════════════════════════════
def place_buy_orders(api, sj, top, hist):
    """13:25-13:30 下尾盤撮合限價買單"""
    from shioaji.constant import Action, StockPriceType, OrderType, StockOrderLot

    cap_per = CAPITAL // N_TOP
    orders_log = []
    names = hist['names']

    for sid, row in top.iterrows():
        price = round(row['cur_price'], 2)
        qty_lots = int((cap_per / price) // 1000)  # 1 張 = 1000 股
        if qty_lots == 0:
            print(f'  [{sid}] 資金不足 1 張 @ {price}, skip')
            continue

        name = names.get(sid, sid)
        cost = qty_lots * 1000 * price
        print(f'  BUY {sid} {name:10s} @ {price:7.2f} × {qty_lots} 張 '
              f'= {cost:>10,.0f}元')

        order = api.Order(
            action      = Action.Buy,
            price       = price,              # 限價 = 當下價
            quantity    = qty_lots,
            price_type  = StockPriceType.LMT,
            order_type  = OrderType.ROD,
            order_lot   = StockOrderLot.Common,
            account     = api.stock_account,
        )
        try:
            contract = api.Contracts.Stocks[sid]
            trade = api.place_order(contract, order)
            status = str(trade.status.status)
            order_id = trade.status.id
            print(f'    → {status} (id={order_id})')
        except Exception as e:
            print(f'    → ORDER FAIL: {e}')
            status = f'ERROR: {e}'
            order_id = ''

        orders_log.append({
            'stock_id'   : sid,
            'name'       : name,
            'price'      : price,
            'qty_lots'   : qty_lots,
            'cost'       : cost,
            'day_ret_pct': round(row['day_ret']*100, 2),
            'vol_ratio'  : round(row['vol_ratio'], 2),
            'amt_ratio'  : round(row['amt_ratio'], 2),
            'order_id'   : order_id,
            'status'     : status,
            'placed_at'  : datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return orders_log

# ════════════════════════════════════════════════════════
#  下開盤撮合單（賣）
# ════════════════════════════════════════════════════════
def place_sell_orders(api, sj, positions):
    """09:00 開盤集合競價賣出昨日部位"""
    from shioaji.constant import Action, StockPriceType, OrderType, StockOrderLot

    sells = []
    for pos in positions:
        sid      = pos['stock_id']
        name     = pos['name']
        qty_lots = pos['qty_lots']

        # 漲停價作為限價賣（保證集合競價 cross 在開盤價，無滑點）
        # 直接用昨日收盤價 × 0.9（往下掛 10%）保證一定成交
        # 隔日沖賣方掛跌停 = 一定 cross
        buy_price = pos['price']
        sell_lmt  = round(buy_price * 0.91, 2)   # 跌停附近

        print(f'  SELL {sid} {name:10s} × {qty_lots} 張 @ LMT {sell_lmt}（吃集合競價開盤）')

        order = api.Order(
            action      = Action.Sell,
            price       = sell_lmt,
            quantity    = qty_lots,
            price_type  = StockPriceType.LMT,
            order_type  = OrderType.ROD,
            order_lot   = StockOrderLot.Common,
            account     = api.stock_account,
        )
        try:
            contract = api.Contracts.Stocks[sid]
            trade = api.place_order(contract, order)
            status = str(trade.status.status)
            order_id = trade.status.id
            print(f'    → {status} (id={order_id})')
        except Exception as e:
            print(f'    → ORDER FAIL: {e}')
            status = f'ERROR: {e}'
            order_id = ''

        sells.append({
            'stock_id'  : sid,
            'name'      : name,
            'qty_lots'  : qty_lots,
            'sell_lmt'  : sell_lmt,
            'order_id'  : order_id,
            'status'    : status,
            'placed_at' : datetime.now(TZ_TW).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return sells

# ════════════════════════════════════════════════════════
#  Log 管理
# ════════════════════════════════════════════════════════
def save_orders_log(orders, kind):
    today = date.today().isoformat()
    path = LOG_DIR / f'{kind}_{today}.csv'
    if not orders:
        print(f'[log] no orders to save')
        return
    fieldnames = list(orders[0].keys())
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(orders)
    print(f'[log] saved → {path}')

def load_last_buy_log():
    """找最近一筆 buy_*.csv 作為「昨日持倉」"""
    files = sorted(LOG_DIR.glob('buy_*.csv'))
    if not files:
        return [], None
    latest = files[-1]
    with open(latest, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r['price']    = float(r['price'])
        r['qty_lots'] = int(r['qty_lots'])
    print(f'[log] loaded {latest.name} ({len(rows)} positions)')
    return rows, latest.stem.replace('buy_', '')

# ════════════════════════════════════════════════════════
#  模式
# ════════════════════════════════════════════════════════
def cmd_buy(dry=False):
    print(f'\n{"="*70}')
    print(f'  BUY MODE  {datetime.now(TZ_TW)}  ({"DRY" if dry else "SIMULATION" if SIMULATION else "LIVE"})')
    print(f'{"="*70}\n')

    hist     = load_history()
    universe = get_universe(hist)

    # 判斷 finlab cache 最後一天是否為「今日已收盤」資料
    # LIVE 模式 (13:25 跑): finlab 最後一天 = 昨日
    # DRY 模式 (盤後跑): finlab 最後一天可能 = 今日 (用來模擬)
    if dry:
        # finlab[-1] 視為「今日 snapshot」, finlab[:-1] 為「昨日歷史」
        snap_date = hist['close'].index[-1]
        snap = pd.DataFrame({
            'cur_price'  : hist['close'].iloc[-1],
            'cur_open'   : hist['close'].iloc[-1],
            'cur_high'   : hist['high'].iloc[-1],
            'cur_low'    : hist['low'].iloc[-1],
            'cur_volume' : hist['vol'].iloc[-1],
            'cur_amount' : hist['amount'].iloc[-1],
        }).loc[universe].dropna()
        # hist 砍掉最後一天，剩下 = 截至昨日
        hist_yest = {k: (v.iloc[:-1] if hasattr(v, 'iloc') else v) for k, v in hist.items()}
        print(f'[dry] snap_date={snap_date.date()}, hist through {hist_yest["close"].index[-1].date()}')
        print(f'[dry] {len(snap)} universe stocks with snap')
    else:
        api, sj = setup_api()
        snap = fetch_snapshots(api, universe)
        hist_yest = hist   # LIVE 模式 finlab 最後一天本來就是昨日

    top, all_df = compute_signals(snap, hist_yest)

    # 2026-08-04新增：不論有無候選股都存下完整snapshot計算結果，
    # 供事後跟finlab官方收盤資料比對，驗證即時報價(尤其SIMULATION帳戶)
    # 是否準確反映當天實際數字。純記錄用途，不影響任何判斷/下單邏輯。
    if not dry:
        snap_log_path = LOG_DIR / f'snapshot_{date.today().isoformat()}.csv'
        all_df.to_csv(snap_log_path, encoding='utf-8-sig')
        print(f'[log] snapshot saved -> {snap_log_path}')

    print(f'\n=== 候選股 ({len(top)} 檔，預計買進）===')
    if len(top) == 0:
        print('  無符合條件股票，今日不交易')
        return

    names = hist['names']
    for sid, row in top.iterrows():
        print(f'  {sid} {names.get(sid, sid):10s}  '
              f'價={row["cur_price"]:7.2f}  漲={row["day_ret"]*100:+5.2f}%  '
              f'量比={row["vol_ratio"]:4.1f}x  量金比={row["amt_ratio"]:4.1f}x  '
              f'NH20={row["nh20_ref"]:.2f}')

    if dry:
        print(f'\n[dry] 不下單，候選股以上')
        return

    print(f'\n[order] placing buy orders...')
    orders = place_buy_orders(api, sj, top, hist)
    save_orders_log(orders, 'buy')
    api.logout()

def cmd_sell(dry=False):
    print(f'\n{"="*70}')
    print(f'  SELL MODE  {datetime.now(TZ_TW)}  ({"DRY" if dry else "SIMULATION" if SIMULATION else "LIVE"})')
    print(f'{"="*70}\n')

    positions, buy_date = load_last_buy_log()
    if not positions:
        print('  無昨日持倉，今日不賣出')
        return

    print(f'  昨日持倉 ({buy_date}):')
    for p in positions:
        print(f'    {p["stock_id"]} {p["name"]:10s} × {p["qty_lots"]} 張 @ {p["price"]}')

    if dry:
        print(f'\n[dry] 不下單')
        return

    api, sj = setup_api()
    sells = place_sell_orders(api, sj, positions)
    save_orders_log(sells, 'sell')
    api.logout()

def cmd_status():
    api, sj = setup_api()
    print(f'\nStock account: {api.stock_account}')
    try:
        positions = api.list_positions(api.stock_account)
        print(f'\n持倉 ({len(positions)}):')
        for p in positions:
            print(f'  {p}')
    except Exception as e:
        print(f'  list_positions fail: {e}')
    try:
        trades = api.list_trades()
        print(f'\n委託 ({len(trades)}):')
        for t in trades:
            print(f'  {t.contract.code} {t.order.action} {t.order.quantity} @ {t.order.price} '
                  f'status={t.status.status}')
    except Exception as e:
        print(f'  list_trades fail: {e}')
    api.logout()

# ════════════════════════════════════════════════════════
#  Entry
# ════════════════════════════════════════════════════════
def usage():
    print(__doc__)
    sys.exit(1)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        usage()
    mode = sys.argv[1].lower()
    if mode == 'buy':
        cmd_buy(dry=False)
    elif mode == 'sell':
        cmd_sell(dry=False)
    elif mode == 'status':
        cmd_status()
    elif mode == 'dry':
        cmd_buy(dry=True)
    else:
        usage()
