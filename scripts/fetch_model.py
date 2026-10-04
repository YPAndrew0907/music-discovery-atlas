"""Explicit image-build fetch of one public model; no runtime downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download-model', action='store_true')
    args = parser.parse_args()
    if not args.download_model:
        raise SystemExit('Explicit --download-model is required')
    row = json.loads((ROOT / 'runtime-assets.json').read_text())['weights'][0]
    if row['path'] != 'model/text_model_quantized.onnx' or row['bytes'] != 126603263:
        raise SystemExit('Unexpected model asset')
    expected_url = 'https://huggingface.co/Xenova/clap-htsat-unfused/resolve/c28f2883575e590e04d3146ff0713c2448d691ba/onnx/text_model_quantized.onnx'
    if row['url'] != expected_url:
        raise SystemExit('Unexpected model source')
    target = ROOT / row['path']
    if target.exists():
        if target.stat().st_size == row['bytes'] and hashlib.file_digest(target.open('rb'), 'sha256').hexdigest() == row['sha256']:
            return
        raise SystemExit('Existing model does not match pinned asset')
    temp = target.with_suffix('.partial')
    total = 0
    digest = hashlib.sha256()
    started = time.monotonic()
    try:
        with urllib.request.urlopen(expected_url, timeout=30) as response, temp.open('xb') as stream:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > row['bytes'] or time.monotonic() - started > 600:
                    raise ValueError('Download bound exceeded')
                digest.update(chunk)
                stream.write(chunk)
        if total != row['bytes'] or digest.hexdigest() != row['sha256']:
            raise ValueError('Model integrity mismatch')
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise SystemExit('Pinned model download failed; no URL or credential logged') from None


if __name__ == '__main__':
    main()
