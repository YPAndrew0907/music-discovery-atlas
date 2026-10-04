"""Pinned native encoder. Vectors are an internal API only, never HTTP output."""
import hashlib
import sys
import threading
import time
import numpy as np
import onnxruntime as ort
from loopback_service import Engine, digest, object_sha


class SearchCancelled(Exception):
    pass


class RequestControl:
    def __init__(self):
        self.event = threading.Event()
        self.run_options = ort.RunOptions()

    def cancel(self):
        self.event.set()
        self.run_options.terminate = True

    def check(self):
        if self.event.is_set():
            raise SearchCancelled('Search cancelled')


class NativeEncoder(Engine):
    def __init__(self, precision='q8'):
        super().__init__(precision)
        self.query_profile = {
            **self.runtime,
            'dimensions':512,'maxTokens':77,'inputTransform':'exact raw text',
            'normalization':'L2 projected 512-dimensional output',
            'tokenizer':'transformers.AutoTokenizer fast RoBERTa',
            'tokenizersVersion':__import__('tokenizers').__version__,
            'modelSha256':self.pair['identity']['textModelSha256'],
            'tokenizerSha256':self.pair['identity']['tokenizerSha256'],
            'pairedAudioModelSha256':self.pair['identity']['audioModelSha256'],
            'modelRevision':self.pair['identity']['revision'],
            'implementationVersion':'native-clap-text-v1',
            'implementationSha256':digest(__file__)}
        self.engine = 'experimental-native-'+precision+':'+object_sha(self.query_profile)

    def manifest(self):
        return {'schemaVersion':1,'engineId':self.engine,'queryProfileId':self.engine,
                'pairId':self.pair['id'],'queryProfile':self.query_profile,
                'catalogId':self.catalog_version,'catalogSha256':self.catalog_sha,
                'vectorsSha256':self.audio_receipt['vectorsSha256'],
                'catalogCount':len(self.ids),'dimensions':512,
                'audioExecutionProfileSha256':self.audio_receipt['executionProfileSha256'],
                'rawInputPolicy':'Exact raw text, no rewriting or trimming; maximum77 tokens',
                'quality':'Experimental small108 catalog; no listener relevance judgments',
                'privacy':'Remote mode transmits description to configured API; no application query history',
                'nativeOnly':True}

    def encode(self, raw_text, control=None):
        """Return (normalized float32 vector, token metadata, timing) internally."""
        control = control or RequestControl()
        control.check()
        start=time.perf_counter()
        original_ids=self.tokenizer(raw_text,add_special_tokens=True)['input_ids']
        inputs=self.tokenizer(raw_text,return_tensors='np',truncation=True,
                              max_length=77,padding=False)['input_ids'].astype(np.int64)
        tokenized=time.perf_counter()
        control.check()
        try:
            vector=self.session.run(None,{'input_ids':inputs},run_options=control.run_options)[0][0]
        except Exception:
            control.check()
            raise
        control.check()
        vector=np.asarray(vector/np.linalg.norm(vector),dtype=np.float32)
        if vector.shape!=(512,) or not np.isfinite(vector).all() or abs(float(np.linalg.norm(vector))-1)>1e-5:
            raise RuntimeError('Invalid normalized encoder output')
        return vector,{'originalTokens':len(original_ids),'usedTokens':int(inputs.shape[1]),
                       'truncated':len(original_ids)>77,'transform':'exact raw text'}, {
                       'tokenization':(tokenized-start)*1000,
                       'inferenceAndNormalization':(time.perf_counter()-tokenized)*1000}
