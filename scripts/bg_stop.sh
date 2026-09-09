#!/bin/bash
cd "$(dirname "$0")/.."

# stop bot dulu (jika jalan)
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
    echo "Stopping bot (PID $(cat bot.pid))..."
    kill "$(cat bot.pid)" 2>/dev/null
    for i in $(seq 1 30); do
        kill -0 "$(cat bot.pid)" 2>/dev/null || break
        sleep 1
    done
    rm -f bot.pid
fi

# stop daemon
if [ -f daemon.pid ] && kill -0 "$(cat daemon.pid)" 2>/dev/null; then
    echo "Stopping daemon (PID $(cat daemon.pid))..."
    kill "$(cat daemon.pid)" 2>/dev/null
    sleep 2
    rm -f daemon.pid
    echo "Daemon stopped."
else
    echo "No daemon running."
fi
