#!/bin/bash
# 13:25 台灣時間執行：算候選 + 下尾盤撮合買單
export HOME=/home/kevin850515123456789
# finlab 認證：2026-08-01起改用 ~/.finlab/credentials.json (機器已跑過 `python -m finlab login`)，不再需要 FINLAB_TOKEN
export SJ_API_KEY=$(grep -m1 '^SJ_API_KEY=' ~/stock/.env | cut -d= -f2-)
export SJ_SECRET_KEY=$(grep -m1 '^SJ_SECRET_KEY=' ~/stock/.env | cut -d= -f2-)
cd /home/kevin850515123456789/stock/live/overnight
LOG=/home/kevin850515123456789/stock/live/overnight/logs/cron_buy.log
echo "$(date '+%Y-%m-%d %H:%M:%S') [START] overnight buy" >> $LOG
/home/kevin850515123456789/stock/bin/python3 fetch_finlab.py >> $LOG 2>&1
/home/kevin850515123456789/stock/bin/python3 overnight_live.py buy >> $LOG 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') [END] overnight buy" >> $LOG
