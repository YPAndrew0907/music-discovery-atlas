"""Exercise the real static gateway without loading native model dependencies."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from web_gateway import WebGateway

class UiPackageTests(unittest.TestCase):
    def test_checked_in_package_pins_and_the_complete_served_ui_are_valid(self):
        package = json.loads((ROOT / 'package-manifest.json').read_text())
        # The pinned native model is fetched explicitly during the image build,
        # not committed to Git. Check its cross-manifest identity when absent.
        weight = json.loads((ROOT / 'runtime-assets.json').read_text())['weights'][0]
        self.assertEqual(weight['path'], 'model/text_model_quantized.onnx')
        for item in package['files']:
            with self.subTest(path=item['path']):
                path = ROOT / item['path']
                if item['path'] == weight['path'] and not path.exists():
                    self.assertEqual(item['bytes'], weight['bytes'])
                    self.assertEqual(item['sha256'], weight['sha256'])
                    continue
                data = path.read_bytes()
                self.assertEqual(len(data), item['bytes'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
        from release_v2 import selected_release_v2
        selected = selected_release_v2(ROOT, package)
        if selected is None:
            gateway = WebGateway(object(), web_root=ROOT / 'web', web_manifest=ROOT / 'web-manifest.json',
                                 catalog_path=ROOT / 'web/search-studio/data/catalog.json')
        else:  # release format v2: the served page must be the one built for the selected release
            gateway = WebGateway(object(), web_root=ROOT / 'web', web_manifest=ROOT / 'web-manifest.json',
                                 catalog_path=None, release_v2=selected.release)
            self.addCleanup(gateway.collection.close)
            self.assertIn('search-studio/src/collection-api.mjs', gateway.assets)
            self.assertNotIn('search-studio/data/catalog.json', gateway.assets)
        self.assertIn('search-studio/src/results-view.mjs', gateway.assets)
        self.assertIn('search-studio/src/graph.mjs', gateway.assets)
        self.assertFalse(gateway.audio.manifest['enabled'])

if __name__ == '__main__':
    unittest.main()
