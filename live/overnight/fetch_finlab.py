#!/usr/bin/env python3
# coding: utf-8
"""
fetch_finlab.py
===============
抓 finlab 隔日沖策略需要的 6 個 price feather + company_basic_info
寫入 ./finlab_db/（或環境變數 FINLAB_DIR 指定的目錄）

預期執行排程：
    每日 02:00 (台灣時間) — 此時 finlab 已含前一交易日資料

認證：
    2026-08-01 起 finlab 停用 api_token 登入，改用瀏覽器 OAuth session
    （憑證存於 ~/.finlab/credentials.json，machine-bound，需在目標機器上
    執行過一次 `python -m finlab login` 才會存在）。本腳本不再呼叫
    finlab.login()，直接依賴該機器上已存在的 session。

環境變數：
    FINLAB_DIR      — 輸出目錄 (預設 ./finlab_db)
"""
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

import finlab
from finlab import data

TZ_TW = timezone(timedelta(hours=8))

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR    = Path(os.environ.get('FINLAB_DIR', SCRIPT_DIR / 'finlab_db'))
OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f'[{datetime.now(TZ_TW):%Y-%m-%d %H:%M:%S}] fetch_finlab start → {OUT_DIR}', flush=True)

DATASETS = [
    'price:收盤價',
    'price:開盤價',
    'price:最高價',
    'price:最低價',
    'price:成交股數',
    'price:成交金額',
]

for name in DATASETS:
    print(f'  pulling {name} ...', flush=True)
    df = data.get(name)
    safe = name.replace(':', '#')
    df.reset_index().to_feather(OUT_DIR / f'{safe}.feather')
    print(f'    -> {df.shape}', flush=True)

print('  pulling company_basic_info ...', flush=True)
info = data.get('company_basic_info')
info.reset_index().to_feather(OUT_DIR / 'company_basic_info.feather')
print(f'    -> {info.shape}', flush=True)

print(f'[{datetime.now(TZ_TW):%Y-%m-%d %H:%M:%S}] done', flush=True)
