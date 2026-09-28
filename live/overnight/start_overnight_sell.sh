#!/bin/bash
# 08:55 台灣時間執行：賣出昨日持倉（吃09:00開盤集合競價）
export HOME=/home/kevin850515123456789
export SJ_API_KEY=$(grep -m1 '^SJ_API_KEY=' ~/stock/.env | cut -d= -f2-)
export SJ_SECRET_KEY=$(grep -m1 '^SJ_SECRET_KEY=' ~/stock/.env | cut -d= -f2-)
cd /home/kevin850515123456789/stock/live/overnight
LOG=/home/kevin850515123456789/stock/live/overnight/logs/cron_sell.log
echo "$(date '+%Y-%m-%d %H:%M:%S') [START] overnight sell" >> $LOG
/home/kevin850515123456789/stock/bin/python3 overnight_live.py sell >> $LOG 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') [END] overnight sell" >> $LOG
