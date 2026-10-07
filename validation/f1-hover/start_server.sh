#!/bin/zsh
# Start server/hosting.py from a built root the way the image's CMD does (WORKDIR = the root, anonymous preview,
# audio on from the root's hydrated pack), behind the stdlib TLS terminator validation/server/tls_proxy.py (it stands
# in for Render's TLS and pipes bytes unchanged). Same procedure as validation/f1-hover/tools/start_server.sh and
# validation/v2-integrate/final/tools/start_server2.sh, with venv-3.12.14 and without MUSIC_RIGHTS_CONTACT.
# Records spawn->ready and the PIDs. Usage: start_server.sh <run> <root> <backend-port> <proxy-port> <runs-dir>
set -u
RUN=${1:?run}; ROOT=${2:?root}; BACKEND_PORT=${3:?port}; PROXY_PORT=${4:?proxy}; RUNS=${5:?runs}
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05; D=$RUNS/$RUN; mkdir -p "$D"
PY=$B/venv-3.12.14/bin/python  # the image interpreter: on 3.12.13 ($B/venv) the encoder refuses the runtime profile
if lsof -nP -iTCP:$BACKEND_PORT -iTCP:$PROXY_PORT -sTCP:LISTEN >/dev/null 2>&1; then echo "ports busy"; exit 2; fi
$PY "$B/validation/server/tls_proxy.py" $PROXY_PORT $BACKEND_PORT "$B/validation/server/tls/cert.pem" "$B/validation/server/tls/key.pem" > "$D/proxy.log" 2>&1 &
PROXY_PID=$!
cd "$ROOT"
SPAWN=$(python3 -c 'import time; print(time.time())')
env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 USE_TORCH=0 USE_TF=0 \
  TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 PORT=$BACKEND_PORT \
  MUSIC_SERVICE_MODE=anonymous-preview MUSIC_ENABLE_ANONYMOUS_PREVIEW=1 MUSIC_PUBLIC_ORIGIN=https://127.0.0.1:$PROXY_PORT \
  MUSIC_DEPLOYMENT_GENERATION=f1-hover-local-proof MUSIC_ENABLE_AUDIO_PREVIEWS=1 \
  MUSIC_AUDIO_PACK_DIR=$ROOT/audio-preview MUSIC_AUDIO_MANIFEST_PATH=$ROOT/audio-delivery.verified.json \
  $PY server/hosting.py > "$D/server.stdout.log" 2> "$D/server.stderr.log" &
SERVER_PID=$!
READY=""; CPU_READY=""
while [ $(python3 -c "import time; print(int(time.time()-$SPAWN))") -lt 300 ]; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:$BACKEND_PORT/healthz || true)
  if [ "$CODE" = "200" ]; then READY=$(python3 -c "import time; print(round(time.time()-$SPAWN,3))"); CPU_READY=$(ps -o cputime= -p $SERVER_PID | tr -d ' '); break; fi
  if ! kill -0 $SERVER_PID 2>/dev/null; then echo "server exited early"; break; fi
  sleep 0.05
done
echo "{\"run\":\"$RUN\",\"root\":\"$ROOT\",\"commit\":\"$(git -C $ROOT rev-parse HEAD)\",\"python\":\"$($PY -c 'import sys,sqlite3;print(sys.version.split()[0],sqlite3.sqlite_version)')\",\"serverPid\":$SERVER_PID,\"proxyPid\":$PROXY_PID,\"backendPort\":$BACKEND_PORT,\"proxyPort\":$PROXY_PORT,\"readinessSeconds\":${READY:-null},\"cpuAtReadiness\":\"$CPU_READY\",\"loadavgAtReady\":\"$(sysctl -n vm.loadavg)\"}" | tee "$D/pids.json"
[ -n "$READY" ] || { echo "NOT READY"; tail -20 "$D/server.stderr.log"; exit 3; }
