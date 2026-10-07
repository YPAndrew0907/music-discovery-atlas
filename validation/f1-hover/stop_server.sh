#!/bin/zsh
# Stop a run started by start_server.sh. Usage: stop_server.sh <run> <runs-dir>
RUN=${1:?run}; D=${2:?runs}/$RUN
read_pid() { python3 -c "import json; print(json.load(open('$D/pids.json'))['$1'])"; }
SERVER_PID=$(read_pid serverPid); PROXY_PID=$(read_pid proxyPid)
CPU_TOTAL=$(ps -o cputime= -p $SERVER_PID 2>/dev/null | tr -d ' ')
kill -0 $SERVER_PID 2>/dev/null && kill -TERM $SERVER_PID
for i in $(seq 1 150); do kill -0 $SERVER_PID 2>/dev/null || break; sleep 0.2; done
kill -0 $SERVER_PID 2>/dev/null && { echo "server still alive; SIGKILL"; kill -KILL $SERVER_PID; }
kill -TERM $PROXY_PID 2>/dev/null; sleep 0.3
echo "{\"run\":\"$RUN\",\"cpuTimeBeforeStop\":\"$CPU_TOTAL\"}" | tee "$D/stopped.json"
lsof -nP -iTCP:$(read_pid backendPort) -iTCP:$(read_pid proxyPort) -sTCP:LISTEN 2>/dev/null && echo "WARNING: listener still up"
exit 0
