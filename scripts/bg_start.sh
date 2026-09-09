#!/bin/bash
cd "$(dirname "$0")/.."
if [ -f daemon.pid ] && kill -0 "$(cat daemon.pid)" 2>/dev/null; then
    echo "Daemon sudah jalan (PID $(cat daemon.pid))."
    exit 1
fi
source .venv/bin/activate
nohup python -u daemon.py > daemon.log 2>&1 &
echo $! > daemon.pid
echo "Daemon started with PID $(cat daemon.pid)"
echo "Dashboard: http://localhost:8080"
