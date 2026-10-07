#!/bin/zsh
# The F1 real-UI proof, each run on a fresh server process (every budget starts empty), before (777a886) and after
# (df16a36 = HEAD) interleaved: the sweeps, 60 s of mousing by one visitor, by four at once, the explicit-rows budget
# check, then the v2 page check (EXPECT_COUNT=670) on HEAD. Usage: run_proof.sh
set -u
S=/private/tmp/claude-501/-Users-yipengandrewwang-SOP-2027/e9fa11a5-2ba7-4a47-9ca8-942e7f9bef1b/scratchpad/f1r
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05; T=$S/tools; R=$S/runs; O=$S/out
PY=$B/venv-3.12.14/bin/python; export NODE_PATH=$B/tooling/node_modules
RUNNING=""
say() { echo "[$(date +%H:%M:%S) load $(sysctl -n vm.loadavg | awk '{print $2}')] $*"; }
cleanup() { if [ -n "$RUNNING" ]; then $T/stop_server.sh $RUNNING $R > $R/$RUNNING.stop.log 2>&1; say "stopped $RUNNING: $(tail -1 $R/$RUNNING.stop.log | cut -c1-120)"; RUNNING=""; fi; }
trap cleanup EXIT INT TERM
start() { local name=$1 root=$2 port=$3; $T/start_server.sh $name $root $port $((port+1)) $R > $R/$name.start.log 2>&1; local rc=$?
  [ -f $R/$name/pids.json ] && RUNNING=$name; say "start $name rc=$rc: $(tail -1 $R/$name.start.log | cut -c1-90)..."; [ $rc = 0 ]; }
pid() { python3 -c "import json; print(json.load(open('$R/$1/pids.json'))['serverPid'])"; }
BEFORE="before-777a886 $S/wt/before-777a886 8861"; AFTER="after-df16a36 $S/wt/head-df16a36 8871"
one() {  # one() <what> <label> <root> <port>
  local what=$1 L=$2 ROOT=$3 PORT=$4; local ORIGIN=https://127.0.0.1:$((PORT+1)) E=$O/$2; mkdir -p $E
  case $what in
    sweeps) start $L-sweeps $ROOT $PORT && { node $T/f1_hover_proof.cjs $ORIGIN $L $E/sweeps sweeps > $E/sweeps.log 2>&1; say "$L sweeps rc=$?: $(tail -1 $E/sweeps.log | cut -c1-300)"; } ;;
    mousing1|mousing4) local n=${what#mousing}; start $L-$what $ROOT $PORT && { node $T/f1_hover_proof.cjs $ORIGIN $L $E/mousing-$n mousing $n $(pid $L-$what) > $E/mousing-$n.log 2>&1; say "$L $what rc=$?: $(tail -1 $E/mousing-$n.log | cut -c1-600)"; } ;;
    rows) start $L-rows-budget $ROOT $PORT && { $PY $T/rows_budget_check.py $ORIGIN $(pid $L-rows-budget) $E/rows-budget.json > $E/rows-budget.log 2>&1; say "$L rows budget rc=$?: $(tail -1 $E/rows-budget.log | cut -c1-500)"; } ;;
    pagecheck) start $L-page-check $ROOT $PORT && { env MUSIC_UI_ORIGIN=$ORIGIN EXPECT_COUNT=670 OUT_DIR=$E/browser-v2-check node $B/platform_v2/tools/browser_v2_check.mjs > $E/browser-v2-check.log 2>&1; say "$L browser_v2_check rc=$?: $(grep -E 'checks passed' $E/browser-v2-check.log | tail -1)"; grep -E '^FAIL' $E/browser-v2-check.log | cut -c1-200; } ;;
  esac
  cleanup
}
for what in sweeps mousing1 mousing4 rows; do one $what ${=BEFORE}; one $what ${=AFTER}; done
one pagecheck ${=AFTER}
say "done"
