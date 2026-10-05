"""Packed storage must preserve exact values, traces and input immutability."""
import importlib.util
import json
from pathlib import Path
import struct
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
from hnsw_trace import HNSW
spec=importlib.util.spec_from_file_location('tuple_storage_reference',ROOT/'tests/fixtures/hnsw_trace_tuple_reference.py')
reference=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class PackedStorageTests(unittest.TestCase):
    def test_snapshot_is_readonly_and_caller_mutation_cannot_change_results(self):
        graph={'schemaVersion':1,'algorithm':'hnsw-static-cosine-v1','count':2,'dimensions':2,
               'M':2,'efConstruction':4,'seed':43,'spaceId':'test:packed-storage','maxLevel':0,'entry':0,
               'links':[[[1]],[[0]]]}
        matrix=[[1.,0.],[0.,1.]]
        index=HNSW(graph,matrix)
        self.assertIsInstance(index.vectors[0],memoryview)
        self.assertTrue(index.vectors[0].readonly)
        self.assertIsInstance(index.vectors[0].obj, bytes)
        with self.assertRaises(TypeError):
            index.vectors[0][0]=0
        matrix[0][0]=0
        self.assertEqual(index.exact_search([1.,0.],k=2)[0],{'id':0,'distance':0.0})

    def test_mutable_byte_buffers_are_copied_before_storage(self):
        graph={'schemaVersion':1,'algorithm':'hnsw-static-cosine-v1','count':2,'dimensions':2,
               'M':2,'efConstruction':4,'seed':43,'spaceId':'test:packed-storage','maxLevel':0,'entry':0,
               'links':[[[1]],[[0]]]}
        for wrap in (lambda value:value, memoryview):
            with self.subTest(buffer_type=wrap):
                mutable=bytearray(struct.pack('<4f',1.,0.,0.,1.))
                index=HNSW(graph,wrap(mutable))
                mutable[:]=b'\x00'*len(mutable)
                self.assertEqual(list(index.vectors[0]),[1.,0.])
                self.assertEqual(index.exact_search([1.,0.],k=2)[0],{'id':0,'distance':0.0})

    def test_actual1000_exact_scores_and_full_traces_match_previous_storage(self):
        data=ROOT/'corpus-releases/fma1000'
        graph=json.loads((data/'index.json').read_bytes())
        matrix=(data/'vectors.f32').read_bytes()
        previous,current=reference.HNSW(graph,matrix),HNSW(graph,matrix)
        examples=json.loads((data/'examples.json').read_bytes())['examples']
        queries=[item['queryVector'] for item in examples]
        queries += [struct.unpack_from('<512f',matrix,row*2048) for row in (0,499,500,999)]
        for query in queries:
            with self.subTest(query_head=query[:3]):
                self.assertEqual(current.exact_search(query,k=16),previous.exact_search(query,k=16))
                self.assertEqual(current.search(query,k=16,ef=32,trace=True),previous.search(query,k=16,ef=32,trace=True))

    def test_actual2000_exact_scores_and_traces_match_previous_storage(self):
        data=ROOT/'corpus-releases/fma2000'
        graph=json.loads((data/'index.json').read_bytes())
        matrix=(data/'vectors.f32').read_bytes()
        previous,current=reference.HNSW(graph,matrix),HNSW(graph,matrix)
        queries=[row['queryVector'] for row in json.loads((data/'examples.json').read_bytes())['examples']]
        for query in queries:
            self.assertEqual(current.exact_search(query,k=16),previous.exact_search(query,k=16))
            self.assertEqual(current.search(query,k=16,ef=32,trace=True),previous.search(query,k=16,ef=32,trace=True))


if __name__=='__main__':
    unittest.main()
