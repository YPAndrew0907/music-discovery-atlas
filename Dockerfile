# Official tag verified 2026-10-04. Resolve and record its immutable digest at build.
ARG PYTHON_BASE=python:3.12.14-slim-trixie
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    USE_TORCH=0 \
    USE_TF=0 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
# Version-pinned preview dependencies; wheel hashes remain a clean-build gate.
RUN python -c "import sys; assert sys.version_info[:3] == (3, 12, 14)" \
    && python -m pip install --only-binary=:all: --no-cache-dir --no-compile -r requirements.lock
COPY server/ /app/server/
COPY model/ /app/model/
COPY music-search-studio/data/ /app/music-search-studio/data/
COPY notices/ /app/notices/
COPY web/ /app/web/
COPY scripts/fetch_model.py /app/scripts/fetch_model.py
COPY scripts/install_audio_release.py /app/scripts/install_audio_release.py
COPY scripts/install_corpus_audio_release.py /app/scripts/install_corpus_audio_release.py
COPY scripts/hydrate_corpus_audio.py /app/scripts/hydrate_corpus_audio.py
COPY scripts/install_release_v2.py /app/scripts/install_release_v2.py
COPY scripts/hydrate_release_v2.py /app/scripts/hydrate_release_v2.py
COPY scripts/make_audio_delivery_v2.py /app/scripts/make_audio_delivery_v2.py
COPY audio-hydration.json /app/audio-hydration.json
COPY active-corpus.json /app/active-corpus.json
COPY corpus-releases/ /app/corpus-releases/
COPY package-manifest.json runtime-assets.json web-manifest.json audio-delivery.json /app/
# Release format v2 only: verify the bundled release, or fetch it from the selection's pinned
# content-addressed origin, before any other download. A v1 selection makes this a no-op.
RUN python scripts/install_release_v2.py
# Deliberate future network action: exactly one 126,603,263-byte pinned public model.
RUN python scripts/fetch_model.py --download-model
# Optional reviewed public Release asset, outside Git. Empty means no audio.
# This is a public URL, never a secret or expiring signed redirect URL.
ARG MUSIC_AUDIO_RELEASE_URL=""
# The selected 2000 release hydrates only its pinned official FMA member ranges.
# Absent/disabled selection and existing approved archive pins retain legacy behavior.
RUN python -c "import sys, requests, bz2; assert sys.version_info[:3] == (3, 12, 14); assert requests.__version__ == '2.34.2'; print('Hydration interpreter and requests dependency verified', flush=True)" \
    && python scripts/hydrate_corpus_audio.py --legacy-url "$MUSIC_AUDIO_RELEASE_URL"
USER 65532:65532
EXPOSE 10000
CMD ["python", "server/hosting.py"]
