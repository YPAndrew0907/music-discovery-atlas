"""No network: enforce the public release URL and fixed artifact boundaries."""
import importlib.util
from pathlib import Path
import tempfile
import signal
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('audio_installer',ROOT/'scripts/install_audio_release.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class AudioInstallerTests(unittest.TestCase):
    def test_exact_versioned_project_release_only(self):
        value='https://github.com/YPAndrew0907/music-discovery-atlas/releases/download/audio-v1/music-audio-108.zip'
        self.assertEqual(installer.release_url(value),value)
        for bad in [value+'?token=secret',value+'#fragment',value.replace('https:','http:'),
                    value.replace('YPAndrew0907','someone'),value.replace('/download/','/latest/'),
                    value.replace('/audio-v1/','/../'),value+'/extra',
                    value.replace('github.com','github.com.evil.example')]:
            with self.assertRaises(ValueError,msg=bad):installer.release_url(bad)

    def test_unreviewed_local_archive_rejected_before_extract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fake=root/'fake.zip';fake.write_bytes(b'not the pinned release')
            with self.assertRaises(ValueError):installer.inspect_archive(fake,root/'catalog.json')
            self.assertEqual([p.name for p in root.iterdir()],['fake.zip'])

    def test_redirect_domain_guard(self):
        handler=installer.ReleaseRedirects()
        with self.assertRaises(ValueError):
            handler.redirect_request(None,None,302,'',{},'http://release-assets.githubusercontent.com/object')
        with self.assertRaises(ValueError):
            handler.redirect_request(None,None,302,'',{},'https://unapproved.example/object')

    def test_absolute_deadline_interrupts_blocking_activity_and_restores_handler(self):
        before=signal.getsignal(signal.SIGALRM)
        with self.assertRaises(TimeoutError):
            with installer.download_deadline(.02): time.sleep(.2)
        self.assertEqual(signal.getsignal(signal.SIGALRM),before)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))

if __name__=='__main__':unittest.main()
