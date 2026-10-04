"""Explicit, pinned build-time installation of the reviewed 108-clip release.

No runtime downloader. A concrete public GitHub Release URL must be supplied.
The repository's disabled manifest and integrity manifest are not modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import signal
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BYTES = 101205244
SHA256 = '507bad0f5f87f3b51965640d69ec680619cb5b8359b3c7ba0e16c751f3dbc132'
CATALOG_SHA = 'e7cfd8347b77929c5c593afdaec9e390509acaed725cacadc448fc6bcae1c013'

def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def release_url(value):
    u=urlsplit(value)
    if (u.scheme!='https' or u.netloc!='github.com' or u.query or u.fragment
        or not u.path.startswith('/YPAndrew0907/music-discovery-atlas/releases/download/')
        or len(u.path.split('/'))!=7 or any(x in ('','.','..') for x in u.path.split('/')[1:])):
        raise ValueError('Expected an exact versioned release asset URL for the reviewed repository')
    return value

class ReleaseRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self): self.count=0
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        self.count+=1;u=urlsplit(newurl)
        if self.count>3 or u.scheme!='https' or u.hostname not in {
            'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}:
            raise ValueError('Unexpected release redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

@contextmanager
def download_deadline(seconds=600):
    # The official Linux build invokes this on its main thread. An absolute
    # alarm also interrupts a read that is kept alive by slow trickled bytes.
    if not hasattr(signal,'setitimer'): raise RuntimeError('Absolute download deadline unavailable')
    def expired(signum,frame): raise TimeoutError('Release download deadline')
    previous=signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL)!=(0.0,0.0): raise RuntimeError('Existing timer; do not replace it')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try: yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)

def download(url,target):
    opener=urllib.request.build_opener(ReleaseRedirects());start=time.monotonic();size=0
    request=urllib.request.Request(release_url(url),headers={'User-Agent':'music-atlas-reviewed-build/1'})
    with download_deadline(), opener.open(request,timeout=30) as response, target.open('xb') as f:
        if response.status!=200: raise ValueError('Unexpected release response')
        length=response.headers.get('Content-Length')
        if length is not None and int(length)!=BYTES: raise ValueError('Release size differs')
        while chunk:=response.read(1024*1024):
            size+=len(chunk)
            if size>BYTES or time.monotonic()-start>600: raise ValueError('Release download limit')
            f.write(chunk)
    if size!=BYTES or digest(target)!=SHA256: raise ValueError('Release digest mismatch')

def inspect_archive(archive,catalog_path):
    archive=Path(archive);catalog_path=Path(catalog_path)
    if archive.is_symlink() or archive.stat().st_size!=BYTES or digest(archive)!=SHA256:
        raise ValueError('Unreviewed audio archive')
    if digest(catalog_path)!=CATALOG_SHA: raise ValueError('Unreviewed catalog')
    catalog=json.loads(catalog_path.read_text());tracks=catalog['tracks']
    if len(tracks)!=108 or len({t['id'] for t in tracks})!=108: raise ValueError('Catalog count')
    expected={t['audio']:t for t in tracks}
    expected.update({n:None for n in ['credits/catalog.json','credits/track-attribution.html','MANIFEST.json','README.txt']})
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len(entries)!=112 or {i.filename for i in entries}!=set(expected): raise ValueError('Unexpected archive inventory')
        for i in entries:
            mode=i.external_attr>>16
            if i.is_dir() or stat.S_ISLNK(mode) or i.flag_bits&1 or i.compress_type!=zipfile.ZIP_STORED:
                raise ValueError('Unexpected ZIP entry type')
            if i.filename.startswith('audio/'):
                t=expected[i.filename]
                if i.file_size!=t['audioBytes'] or hashlib.sha256(z.read(i)).hexdigest()!=t['audioSha256']:
                    raise ValueError('Audio entry differs from catalog')
        if z.read('credits/catalog.json')!=catalog_path.read_bytes(): raise ValueError('Credit catalog mismatch')
        manifest=json.loads(z.read('MANIFEST.json'))
        if manifest['catalogId']!=catalog['id'] or manifest['catalogSha256']!=CATALOG_SHA or manifest['count']!=108:
            raise ValueError('Audio manifest mismatch')
    return catalog

def install(archive,root,source_url=None):
    root=Path(root);catalog=inspect_archive(archive,root/'music-search-studio/data/catalog.json')
    target=root/'audio-preview';credit_target=root/'audio-release-credits';delivery=root/'audio-delivery.verified.json'
    if target.exists() or credit_target.exists() or delivery.exists(): raise ValueError('Existing installation; do not overwrite')
    with tempfile.TemporaryDirectory(prefix='.audio-install-',dir=root) as temp:
        temp=Path(temp);audio=temp/'audio';audio.mkdir();credits=temp/'credits';credits.mkdir()
        with zipfile.ZipFile(archive) as z:
            for t in catalog['tracks']:
                p=audio/Path(t['audio']).name
                with z.open(t['audio']) as src,p.open('xb') as dst: shutil.copyfileobj(src,dst,1024*1024)
                if digest(p)!=t['audioSha256']: raise ValueError('Extracted audio mismatch')
            for name in ['credits/catalog.json','credits/track-attribution.html','MANIFEST.json','README.txt']:
                (credits/Path(name).name).write_bytes(z.read(name))
        rows=[{'id':t['id'],'available':True,'url':'/audio/'+Path(t['audio']).name,
               'bytes':t['audioBytes'],'sha256':t['audioSha256']} for t in catalog['tracks']]
        record={'schemaVersion':1,'catalogId':catalog['id'],'catalogSha256':CATALOG_SHA,
                'enabled':True,'publicDeliveryVerified':True,'tracks':rows,
                'deliveryVerificationScope':'Pinned local image files; external browser/host delivery still requires acceptance',
                'releaseArchive':{'bytes':BYTES,'sha256':SHA256,'sourceUrl':source_url}}
        # The existing field means the exact local routes are verified; this
        # receipt explicitly leaves external host/browser acceptance open.
        (temp/'delivery.json').write_text(json.dumps(record,indent=2)+'\n')
        audio.rename(target);credits.rename(credit_target);(temp/'delivery.json').rename(delivery)
    return {'tracks':108,'audioBytes':sum(t['audioBytes'] for t in catalog['tracks']),
            'archiveSha256':SHA256,'runtimeEnabled':False,'installedDirectory':'audio-preview',
            'manifest':'audio-delivery.verified.json','hostedDeliveryTested':False}

def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--release-url');g.add_argument('--archive',type=Path)
    p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    if args.archive:
        if args.verify_only: inspect_archive(args.archive,ROOT/'music-search-studio/data/catalog.json');print('Verified pinned 108-track archive');return
        print(json.dumps(install(args.archive,ROOT),indent=2));return
    if args.verify_only:raise SystemExit('Verify-only accepts a local archive only')
    with tempfile.TemporaryDirectory(prefix='music-audio-release-') as temp:
        archive=Path(temp)/'release.zip';download(args.release_url,archive)
        print(json.dumps(install(archive,ROOT,release_url(args.release_url)),indent=2))

if __name__=='__main__':main()
