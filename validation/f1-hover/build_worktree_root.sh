#!/bin/zsh
# A v2 root for the F1 proof: a git worktree of REF (detached) put through the image build's steps, as
# validation/v2-integrate/final/tools/build_root.sh does for a git-archive root: the deploy plan's activation of
# fma2000-v2 (release format 2.1, b537a7ac...) and its --check, then the Dockerfile RUN steps (install_release_v2.py,
# the interpreter check, fetch_model.py verifying the pinned model, hydrate_corpus_audio.py --verify-plan) and the v2
# hydration publishing from the verified local MP3 cache (validation/v2-deploy/hydrate_from_cache.py).
# Usage: build_worktree_root.sh <ref> <root> <out-dir>
set -eu
REF=${1:?ref}; ROOT=${2:?root}; OUT=${3:?out}
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05; REPO=$B/repo; PY=$B/venv-3.12.14/bin/python
REL=$B/validation/v2-integrate/final/regen/fma2000-v2.1
SHA=b537a7ace86ea6eebdd95b2d4cfc908e75487aeef8408295330d02c3e278d740
mkdir -p $OUT
git -C $REPO worktree add --detach $ROOT $REF > $OUT/worktree-add.out 2>&1
echo "{\"ref\":\"$REF\",\"commit\":\"$(git -C $ROOT rev-parse HEAD)\",\"root\":\"$ROOT\",\"release\":\"$REL\",\"releaseSha256\":\"$SHA\",\"python\":\"$($PY -c 'import sys,sqlite3;print(sys.version.split()[0],sqlite3.sqlite_version)')\",\"loadavg\":\"$(sysctl -n vm.loadavg)\",\"startedAt\":\"$(date -u +%FT%TZ)\"}" > $OUT/build-context.json
cd $ROOT
step() { local name=$1; shift; local t0=$(python3 -c 'import time;print(time.time())'); "$@" > $OUT/$name.out 2> $OUT/$name.err; echo "$name $(python3 -c "import time;print(round(time.time()-$t0,2))") s"; }
step activate        $PY scripts/activate_release_v2.py --release-dir $REL --expected-manifest-sha256 $SHA --name fma2000-v2
step activate-check  $PY scripts/activate_release_v2.py --check
step install         /usr/bin/time -l $PY scripts/install_release_v2.py
step interpreter     $PY -c "import sys, requests, bz2; assert sys.version_info[:3] == (3, 12, 14); assert requests.__version__ == '2.34.2'; print('Hydration interpreter and requests dependency verified', flush=True)"
cp $REPO/model/text_model_quantized.onnx model/
step fetch-model     $PY scripts/fetch_model.py --download-model
step hydrate-plan    $PY scripts/hydrate_corpus_audio.py --verify-plan
step hydrate-cache   /usr/bin/time -l $PY $B/validation/v2-deploy/hydrate_from_cache.py $ROOT $B/audio2000
git -C $ROOT status --short | wc -l | tr -d ' ' > $OUT/status-lines.txt
echo "{\"finishedAt\":\"$(date -u +%FT%TZ)\",\"loadavg\":\"$(sysctl -n vm.loadavg)\"}" > $OUT/build-finished.json
