import shioaji as sj
from shioaji.constant import Action, OrderType, FuturesOCType, FuturesPriceType
from flask import Flask, request, jsonify
import threading
import subprocess
import time
import os
import logging
import csv
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
import requests
from datetime import datetime, timedelta, timezone
import calendar
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
TZ_TW = timezone(timedelta(hours=8))

# ==========================================
# 0. 日誌系統
# ==========================================
LINE_NOTIFY_TOKEN = os.environ.get("LINE_NOTIFY_TOKEN", "")

def setup_logger():
    Path("logs").mkdir(exist_ok=True)
    logger = logging.getLogger("TradingBot")
    logger.setLevel(logging.INFO)

    file_handler = TimedRotatingFileHandler(
        filename="logs/trade_log.log", when="midnight", interval=1,
        backupCount=30, encoding='utf-8'
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S'))

    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
    return logger

logger = setup_logger()

def send_line_notify(msg):
    if not LINE_NOTIFY_TOKEN:
        return
    try:
        requests.post(
            "https://notify-api.line.me/api/notify",
            headers={"Authorization": "Bearer " + LINE_NOTIFY_TOKEN},
            data={'message': msg}
        )
    except Exception as e:
        logger.error(f"[Line] 發送失敗: {e}")

# ==========================================
# 1. 系統核心參數
# ==========================================
API_KEY        = os.environ.get("SJ_API_KEY")
SECRET_KEY     = os.environ.get("SJ_SECRET_KEY")
CA_PATH        = os.environ.get("SJ_CA_PATH", "Sinopac-1.pfx")
CA_PASS        = os.environ.get("SJ_CA_PASS")
PERSON_ID      = os.environ.get("SJ_PERSON_ID")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# TMF 每點價值 (小台 50 元/點)
TMF_POINT_VALUE = 10

api = sj.Shioaji(simulation=False)
api_lock = threading.Lock()

_last_signal_lock  = threading.Lock()
_last_signal_time   = None
_last_signal_target = None

# ==========================================
# 2. 成交記錄 CSV
# ==========================================
TRADE_CSV = "logs/trade_records.csv"
TRADE_HEADERS = [
    "datetime", "ticker", "action", "quantity", "contract", "delivery_month",
    "signal_price", "fill_price", "slippage_pts", "slippage_twd",
    "pos_before", "target_pos", "order_status", "note"
]

def init_trade_csv():
    path = Path(TRADE_CSV)
    if not path.exists():
        with open(TRADE_CSV, 'w', newline='', encoding='utf-8-sig') as f:
            csv.DictWriter(f, fieldnames=TRADE_HEADERS).writeheader()

def append_trade_csv(record: dict):
    with open(TRADE_CSV, 'a', newline='', encoding='utf-8-sig') as f:
        csv.DictWriter(f, fieldnames=TRADE_HEADERS).writerow(record)
    threading.Thread(target=_git_push_csv, daemon=True).start()

def _git_push_csv():
    try:
        repo = Path(__file__).parent
        ts = datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
        subprocess.run(["git", "-C", str(repo), "add", "logs/trade_records.csv"],
                       check=True, capture_output=True)
        result = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", f"trade: {ts}"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            subprocess.run(["git", "-C", str(repo), "push"], check=True, capture_output=True)
            logger.info("[Git] CSV pushed to GitHub")
        else:
            logger.info("[Git] 無變更，略過 push")
    except Exception as e:
        logger.warning(f"[Git] push 失敗: {e}")

init_trade_csv()

# ==========================================
# 3. 智慧換月邏輯
# ==========================================
def get_target_delivery_month():
    now = datetime.now(TZ_TW)
    year, month = now.year, now.month

    c = calendar.Calendar(firstweekday=calendar.SUNDAY)
    wednesdays = [
        day for week in c.monthdatescalendar(year, month)
        for day in week
        if day.weekday() == calendar.WEDNESDAY and day.month == month
    ]
    settlement = datetime(year, month, wednesdays[2].day, 13, 30, 0, tzinfo=TZ_TW)
    rollover   = settlement - timedelta(days=1)

    if now >= rollover:
        month += 1
        if month > 12:
            month = 1
            year += 1

    return f"{year}{month:02d}"

def get_active_contract(api_instance):
    try:
        target_month = get_target_delivery_month()
        tmf_contracts = [c for c in api_instance.Contracts.Futures.TMF if len(c.code) <= 5]
        if not tmf_contracts:
            return None
        for c in tmf_contracts:
            if c.delivery_month == target_month:
                logger.info(f"[Contract] 鎖定: {c.code} ({c.delivery_month})")
                return c
        logger.warning(f"[Contract] 找不到 {target_month}，改用近月合約")
        return sorted(tmf_contracts, key=lambda x: x.delivery_month)[0]
    except Exception as e:
        logger.error(f"[Contract Error] {e}")
        return None

# ==========================================
# 4. 登入與連線
# ==========================================
def perform_login():
    logger.info("[System] 執行登入程序...")
    try:
        api.login(api_key=API_KEY, secret_key=SECRET_KEY, contracts_timeout=10000)
        api.activate_ca(ca_path=CA_PATH, ca_passwd=CA_PASS, person_id=PERSON_ID)
        api.set_default_account(api.futopt_account)
        logger.info("[System] 登入成功！")
        return True
    except Exception as e:
        logger.error(f"[Error] 登入失敗: {e}")
        send_line_notify(f"\n⚠️ 登入失敗！\n{e}")
        return False

perform_login()

def check_token_and_relogin():
    try:
        api.list_positions(api.futopt_account, timeout=5000)
    except Exception as e:
        logger.warning(f"[Connection] 連線異常 ({e})，重連中...")
        send_line_notify("\n⚠️ 連線中斷，嘗試重連...")
        if perform_login():
            logger.info("[Connection] 重連成功。")
            send_line_notify("\n✅ 重連成功。")
        else:
            logger.critical("[Connection] 重連失敗！")
            send_line_notify("\n❌ 重連失敗，請手動檢查！")

def get_total_position():
    try:
        positions = api.list_positions(api.futopt_account, timeout=5000)
        total = 0
        for pos in positions:
            if "TMF" in pos.code:
                total += pos.quantity if pos.direction == Action.Buy else -pos.quantity
        return total
    except Exception as e:
        logger.error(f"[Error] 獲取庫存失敗: {e}")
        raise

# ==========================================
# 5. 核心交易邏輯（含成交記錄）
# ==========================================
def execute_trade_alignment(target_pos, signal_price=None, ticker="Unknown"):
    with api_lock:
        check_token_and_relogin()

        try:
            current_pos = get_total_position()
        except Exception as e:
            send_line_notify(f"\n❌ 無法讀取庫存，略過下單。\n{e}")
            return False, "無法獲取部位"

        diff = target_pos - current_pos
        if diff == 0:
            return True, f"部位已對齊: {current_pos}"

        order_action = Action.Buy if diff > 0 else Action.Sell
        order_qty    = abs(diff)

        try:
            contract = get_active_contract(api)
            if contract is None:
                tmf_all = [c for c in api.Contracts.Futures.TMF if len(c.code) <= 5]
                if tmf_all:
                    contract = sorted(tmf_all, key=lambda x: x.delivery_month)[0]
            if not contract:
                raise Exception("無法獲取 TMF 合約物件")

            logger.info(f"[Trade] {contract.code} ({contract.delivery_month}) | {order_action} {order_qty} 口")

            order = api.Order(
                action=order_action,
                price=0,
                quantity=int(order_qty),
                order_type=OrderType.IOC,
                price_type=FuturesPriceType.MKP,
                octype=FuturesOCType.Auto,
                account=api.futopt_account
            )
            trade = api.place_order(contract, order)

            # 等待成交回報
            logger.info("等待成交回報 (2s)...")
            time.sleep(2)

            # 刷新委託狀態以取得成交價
            try:
                api.update_status(api.futopt_account)
                fill_status = str(trade.status.status)

                # 優先從 list_trades() 取得 deals（比直接讀 trade.status.deals 更可靠）
                fill_price = None
                deals = []
                all_trades = api.list_trades()
                order_id = trade.status.id
                for t in all_trades:
                    if t.status.id == order_id:
                        deals = getattr(t.status, 'deals', [])
                        break

                # fallback：直接從 trade.status.deals 取
                if not deals:
                    deals = getattr(trade.status, 'deals', [])

                if deals:
                    total_qty = sum(d.quantity for d in deals)
                    fill_price = round(
                        sum(d.price * d.quantity for d in deals) / total_qty, 1
                    )
            except Exception as ex:
                logger.warning(f"[FillPrice] 取得成交價失敗: {ex}")
                fill_price  = None
                fill_status = str(getattr(trade.status, 'status', 'Unknown'))

            # 計算滑價
            slippage_pts = None
            slippage_twd = None
            if fill_price and signal_price:
                if order_action == Action.Buy:
                    slippage_pts = round(fill_price - signal_price, 1)   # 正 = 買貴了
                else:
                    slippage_pts = round(signal_price - fill_price, 1)   # 正 = 賣便宜了
                slippage_twd = slippage_pts * TMF_POINT_VALUE

            # 記錄到 CSV
            record = {
                "datetime":       datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S"),
                "ticker":         ticker,
                "action":         "BUY" if order_action == Action.Buy else "SELL",
                "quantity":       order_qty,
                "contract":       contract.code,
                "delivery_month": contract.delivery_month,
                "signal_price":   signal_price  if signal_price  is not None else "",
                "fill_price":     fill_price     if fill_price    is not None else "",
                "slippage_pts":   slippage_pts   if slippage_pts  is not None else "",
                "slippage_twd":   slippage_twd   if slippage_twd  is not None else "",
                "pos_before":     current_pos,
                "target_pos":     target_pos,
                "order_status":   fill_status,
                "note":           ""
            }
            append_trade_csv(record)

            # 組合 log 訊息
            price_str = f" | 成交:{fill_price}" if fill_price else ""
            slip_str  = f" | 滑價:{slippage_pts:+.1f}點({slippage_twd:+.0f}元)" \
                        if slippage_pts is not None else ""
            status_msg = (
                f"下單成功: {fill_status}, "
                f"合約: {contract.delivery_month}, 數量: {order_qty}"
                f"{price_str}{slip_str}"
            )
            logger.info(f"[Result] {status_msg}")

            act_str = "買進" if order_action == Action.Buy else "賣出"
            line_msg = (
                f"\n✅ 下單成功"
                f"\n合約：{contract.delivery_month}"
                f"\n動作：{act_str} {order_qty} 口"
                f"\n庫存：{current_pos} → {target_pos}"
            )
            if fill_price:
                line_msg += f"\n成交價：{fill_price}"
            if slippage_pts is not None:
                line_msg += f"\n滑價：{slippage_pts:+.1f}點 ({slippage_twd:+.0f}元)"
            send_line_notify(line_msg)

            return True, status_msg

        except Exception as e:
            now_str = datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
            append_trade_csv({
                "datetime": now_str, "ticker": ticker,
                "action": "BUY" if order_action == Action.Buy else "SELL",
                "quantity": order_qty, "contract": getattr(contract, 'code', ''),
                "delivery_month": getattr(contract, 'delivery_month', ''),
                "signal_price": signal_price or "", "fill_price": "",
                "slippage_pts": "", "slippage_twd": "",
                "pos_before": current_pos, "target_pos": target_pos,
                "order_status": "ERROR", "note": str(e)
            })
            send_line_notify(f"\n❌ 下單異常！\n{e}")
            raise Exception(f"下單異常: {e}")

# ==========================================
# 6. 背景心跳
# ==========================================
def keep_alive_and_check_token():
    while True:
        time.sleep(900)
        try:
            now = datetime.now(TZ_TW)
            if (now.hour == 13 and now.minute >= 45) or (now.hour == 14):
                continue
            api.list_positions(api.futopt_account, timeout=3000)
            logger.info("[Heartbeat] Token 正常。")
        except Exception as e:
            logger.warning(f"[Heartbeat] Token 失效 ({e})，重啟...")
            send_line_notify("\n⚠️ Token 失效，系統重啟中...")
            os._exit(1)

# ==========================================
# 7. Webhook
# ==========================================
@app.route('/webhook', methods=['POST'])
def webhook():
    # 驗證 token
    if WEBHOOK_SECRET:
        if request.args.get("token", "") != WEBHOOK_SECRET:
            logger.warning("[Auth] 未授權請求，已拒絕。")
            return jsonify({"status": "error", "msg": "Unauthorized"}), 401

    # 期交所休息時間
    now = datetime.now(TZ_TW)
    if (now.hour == 13 and now.minute >= 45) or (now.hour == 14):
        logger.warning("[Signal] 休息時間，拒絕下單 (13:45-15:00)")
        return jsonify({"status": "ignored", "msg": "Market Closed"}), 200

    data = request.json
    if not data:
        return jsonify({"status": "error", "msg": "Empty data"}), 400

    incoming_ticker = data.get('ticker', 'Unknown')

    try:
        target_pos = int(data.get('target_pos', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "msg": "Invalid target_pos"}), 400

    # signal_price：TradingView alert 帶入 {{close}}
    try:
        signal_price = float(data['signal_price']) if data.get('signal_price') else None
    except (TypeError, ValueError):
        signal_price = None

    # 重複信號防護 (5 秒內相同目標部位)
    global _last_signal_time, _last_signal_target
    with _last_signal_lock:
        if (_last_signal_target == target_pos and
                _last_signal_time is not None and
                (now - _last_signal_time).total_seconds() < 5):
            logger.warning(f"[Signal] 重複信號略過: target_pos={target_pos}")
            return jsonify({"status": "ignored", "msg": "Duplicate signal"}), 200
        _last_signal_time   = now
        _last_signal_target = target_pos

    logger.info(f"[Signal] {incoming_ticker} | target={target_pos} | signal_price={signal_price}")

    try:
        success, detail = execute_trade_alignment(target_pos, signal_price=signal_price, ticker=incoming_ticker)
        status = "success" if success else "warning"
        code   = 200
        return jsonify({"status": status, "detail": str(detail)}), code
    except Exception as e:
        logger.critical(f"[Critical] {e}")
        send_line_notify(f"\n🔥 系統崩潰！\n{e}")
        return jsonify({"status": "error", "msg": str(e)}), 500

# ==========================================
# 8. Dashboard
# ==========================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>交易紀錄 {{ date }}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f1117; color: #e0e0e0; padding: 16px; }
  h1 { font-size: 1.2rem; font-weight: 600; margin-bottom: 16px; color: #fff; }
  .nav { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; }
  .nav a { color: #7eb8f7; text-decoration: none; font-size: 1.2rem; padding: 4px 10px;
            border: 1px solid #333; border-radius: 6px; }
  .nav a:hover { background: #1e2230; }
  .nav .cur { font-size: 1rem; font-weight: 600; color: #fff; }
  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
  .card { background: #1a1d27; border-radius: 10px; padding: 14px 20px; min-width: 140px; flex: 1; }
  .card .label { font-size: .75rem; color: #888; margin-bottom: 4px; }
  .card .value { font-size: 1.4rem; font-weight: 700; }
  .green { color: #4caf82; }
  .red   { color: #e05c5c; }
  .gray  { color: #aaa; }
  table { width: 100%; border-collapse: collapse; font-size: .82rem; }
  th { background: #1a1d27; color: #888; font-weight: 500; padding: 8px 10px;
       text-align: left; border-bottom: 1px solid #2a2d3a; }
  td { padding: 8px 10px; border-bottom: 1px solid #1e2130; }
  tr.buy  td { background: rgba(76,175,130,.06); }
  tr.sell td { background: rgba(224,92, 92,.06); }
  tr:hover td { background: #1e2230; }
  .tag-buy  { color: #4caf82; font-weight: 600; }
  .tag-sell { color: #e05c5c; font-weight: 600; }
  .tag-filled  { color: #7eb8f7; }
  .tag-error   { color: #f0a040; }
  .empty { text-align: center; padding: 40px; color: #555; }
</style>
</head>
<body>
<h1>TMF 交易紀錄</h1>
<div class="nav">
  <a href="{{ prev_url }}">&lt;</a>
  <span class="cur">{{ date }}</span>
  <a href="{{ next_url }}">&gt;</a>
</div>

<div class="cards">
  <div class="card">
    <div class="label">成交筆數</div>
    <div class="value">{{ filled }}</div>
  </div>
  <div class="card">
    <div class="label">平均滑價</div>
    <div class="value {{ 'red' if avg_slip > 0 else 'green' if avg_slip < 0 else 'gray' }}">
      {{ "%+.1f"|format(avg_slip) }} 點
    </div>
  </div>
  <div class="card">
    <div class="label">總滑價金額</div>
    <div class="value {{ 'red' if total_slip_twd > 0 else 'green' if total_slip_twd < 0 else 'gray' }}">
      {{ "%+d"|format(total_slip_twd|int) }} 元
    </div>
  </div>
  <div class="card">
    <div class="label">目前部位</div>
    <div class="value">{{ net_pos }}</div>
  </div>
</div>

{% if rows %}
<table>
  <thead>
    <tr>
      <th>時間</th><th>動作</th><th>合約</th><th>口數</th>
      <th>信號價</th><th>成交價</th><th>滑價(點)</th><th>滑價(元)</th>
      <th>前部位</th><th>目標</th><th>狀態</th>
    </tr>
  </thead>
  <tbody>
  {% for r in rows %}
    <tr class="{{ 'buy' if r.action == 'BUY' else 'sell' }}">
      <td>{{ r.datetime[11:19] }}</td>
      <td class="{{ 'tag-buy' if r.action == 'BUY' else 'tag-sell' }}">
        {{ '買' if r.action == 'BUY' else '賣' }}</td>
      <td>{{ r.contract }}</td>
      <td>{{ r.quantity }}</td>
      <td>{{ r.signal_price }}</td>
      <td>{{ r.fill_price }}</td>
      <td>{{ ("%+.1f"|format(r.slippage_pts|float)) if r.slippage_pts else '' }}</td>
      <td>{{ ("%+d"|format(r.slippage_twd|float|int)) if r.slippage_twd else '' }}</td>
      <td>{{ r.pos_before }}</td>
      <td>{{ r.target_pos }}</td>
      <td class="{{ 'tag-filled' if 'Filled' in r.order_status else 'tag-error' }}">
        {{ r.order_status.replace('Status.','') }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<div class="empty">當日無交易紀錄</div>
{% endif %}
</body>
</html>"""

@app.route('/dashboard')
def dashboard():
    if WEBHOOK_SECRET and request.args.get("token", "") != WEBHOOK_SECRET:
        return "<h2>401 Unauthorized</h2>", 401

    today = datetime.now(TZ_TW).strftime("%Y-%m-%d")
    date_str = request.args.get("date", today)

    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
    except ValueError:
        d = _date.today()
        date_str = d.isoformat()

    prev_date = (d - timedelta(days=1)).isoformat()
    next_date = (d + timedelta(days=1)).isoformat()
    token_qs  = f"&token={WEBHOOK_SECRET}" if WEBHOOK_SECRET else ""
    prev_url  = f"/dashboard?date={prev_date}{token_qs}"
    next_url  = f"/dashboard?date={next_date}{token_qs}"

    rows = []
    try:
        with open(TRADE_CSV, encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row['datetime'].startswith(date_str):
                    rows.append(row)
    except FileNotFoundError:
        pass

    filled_rows = [r for r in rows if 'Filled' in r.get('order_status', '')]
    slip_pts = [float(r['slippage_pts']) for r in filled_rows if r.get('slippage_pts')]
    slip_twd = [float(r['slippage_twd']) for r in filled_rows if r.get('slippage_twd')]
    avg_slip       = sum(slip_pts) / len(slip_pts) if slip_pts else 0
    total_slip_twd = sum(slip_twd) if slip_twd else 0

    net_pos = 0
    if rows:
        try:
            net_pos = int(rows[-1]['target_pos'])
        except (ValueError, KeyError):
            pass

    from jinja2 import Template
    html = Template(DASHBOARD_HTML).render(
        date=date_str,
        prev_url=prev_url,
        next_url=next_url,
        rows=rows,
        filled=len(filled_rows),
        avg_slip=avg_slip,
        total_slip_twd=total_slip_twd,
        net_pos=net_pos,
    )
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


if __name__ == '__main__':
    threading.Thread(target=keep_alive_and_check_token, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
