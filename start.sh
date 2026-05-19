#!/bin/bash
# 啟動交易機器人（app.py + ngrok），用 supervisord 管理
# 每次開新 session 執行一次即可

cd /home/kevin850515123456789/stock

SOCK="logs/supervisor.sock"
PID="logs/supervisord.pid"

# 若已在跑就重載，否則啟動
if [ -f "$PID" ] && kill -0 $(cat "$PID") 2>/dev/null; then
    echo "[start.sh] supervisord 已在執行，重載設定..."
    supervisorctl -c supervisor.conf reread
    supervisorctl -c supervisor.conf update
else
    echo "[start.sh] 啟動 supervisord..."
    supervisord -c supervisor.conf
    sleep 2
fi

supervisorctl -c supervisor.conf status
