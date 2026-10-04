"""Deployment-only loader; copied as server/loopback_service.py by package_server.py.

The encoder and API sources remain byte-identical. Only the data-loading adapter
changes: this reads the reviewed release assets, never the research corpus.
"""
import os
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',USE_TORCH='0',USE_TF='0',
                  OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',TOKENIZERS_PARALLELISM='false')
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
import onnxruntime as ort
import transformers
from transformers import AutoTokenizer

WORK=Path(__file__).resolve().parent.parent
Q8_SHA='1a3df8b197e249816e08415fd040434c44762b2eea7eb7bf8a48a0f0bf3c14e5'
TOKENIZER_SHA='dc239041d98de27ffc3975473a1a23e3db4c937b23c138c38bbc66588bd247e5'
VECTORS_SHA='ec8e5c9996f229028cf9859dbfb11306a604af42be1e6465f61d9aa50b4d6fd8'
CATALOG_SHA='e7cfd8347b77929c5c593afdaec9e390509acaed725cacadc448fc6bcae1c013'
CATALOG_ID='fma108:d440beae31b28758956db0a39e221e9842852d6a088c1cf6ab0e50cd8f967320'
PAIR_ID='experimental-fma-q8:3d5a3ebe3ac4bd09fa96f05fdefc2df1ae841d2562dea913abb8a5990b377566'


def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest()


def object_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def verify_package():
    manifest=json.loads((WORK/'package-manifest.json').read_text())
    if manifest.get('schemaVersion')!=1 or manifest.get('kind')!='music-q8-server-build-context':
        raise RuntimeError('Unknown package manifest')
    seen=set()
    for row in manifest['files']:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or row['path'] in seen:
            raise RuntimeError('Invalid package path')
        seen.add(row['path'])
        path=WORK/relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['bytes'] or digest(path)!=row['sha256']:
            raise RuntimeError('Package file integrity failure')
    return manifest


class Engine:
    def __init__(self,precision):
        if precision!='q8':
            raise ValueError('This deployment package contains only Q8')
        start=time.perf_counter()
        self.precision=precision
        package=verify_package()
        data=WORK/'music-search-studio/data'
        models=WORK/'model'
        self.pack=WORK/'not-a-research-pack'
        self.model=models/'text_model_quantized.onnx'
        if digest(self.model)!=Q8_SHA or digest(models/'tokenizer.json')!=TOKENIZER_SHA:
            raise RuntimeError('Pinned text artifact mismatch')
        if digest(data/'vectors.f32')!=VECTORS_SHA or digest(data/'catalog.json')!=CATALOG_SHA:
            raise RuntimeError('Pinned catalog/vector mismatch')
        self.pair=json.loads((WORK/'model/model-space-q8.json').read_text())
        if self.pair['id']!=PAIR_ID or PAIR_ID!='experimental-fma-q8:'+object_sha(self.pair['identity']):
            raise RuntimeError('Pinned pairing contract mismatch')
        self.metadata=json.loads((data/'catalog.json').read_text())
        self.catalog=self.metadata
        self.catalog_version=CATALOG_ID
        self.catalog_sha=CATALOG_SHA
        if self.metadata['id']!=CATALOG_ID or self.metadata['dimensions']!=512:
            raise RuntimeError('Catalog identity mismatch')
        self.ids=[row['id'] for row in self.metadata['tracks']]
        if len(self.ids)!=108 or len(set(self.ids))!=108 or self.ids!=json.loads((data/'ids.json').read_text()):
            raise RuntimeError('Catalog order mismatch')
        self.vectors=np.frombuffer((data/'vectors.f32').read_bytes(),dtype='<f4').reshape((108,512))
        if not np.isfinite(self.vectors).all() or np.max(np.abs(np.linalg.norm(self.vectors,axis=1)-1))>1e-5:
            raise RuntimeError('Invalid normalized audio vectors')
        self.audio_receipt={'vectorsSha256':VECTORS_SHA,
            'executionProfileSha256':object_sha(self.pair['identity']['audioExecutionProfile'])}
        self.runtime={'package':'onnxruntime','version':ort.__version__,'provider':'CPUExecutionProvider',
            'intraOpThreads':1,'interOpThreads':1,'graphOptimizationLevel':'all','executionMode':'sequential',
            'transformers':transformers.__version__,'python':sys.version.split()[0]}
        expected=package['nativeRuntime']
        if self.runtime!=expected:
            raise RuntimeError('Native runtime differs from reviewed package profile')
        self.runtime_sha=object_sha(self.runtime)
        self.timings={'verifyAssetsMs':(time.perf_counter()-start)*1000}
        start=time.perf_counter()
        self.tokenizer=AutoTokenizer.from_pretrained(str(models),local_files_only=True)
        self.timings['tokenizerLoadMs']=(time.perf_counter()-start)*1000
        options=ort.SessionOptions()
        options.intra_op_num_threads=1
        options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level=ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        start=time.perf_counter()
        self.session=ort.InferenceSession(str(self.model),sess_options=options,providers=['CPUExecutionProvider'])
        self.timings['ortSessionLoadMs']=(time.perf_counter()-start)*1000
