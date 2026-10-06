// The v1 fma2000 page data that the v1-path tests run against, wherever this tree keeps it.
// A tree that serves the v1 page holds it in web/search-studio/data/. After a release-format-v2
// release is activated (scripts/activate_release_v2.py) that directory holds the v2 page data, and
// the same v1 bytes come from corpus-releases/fma2000/ (byte-identical copies of the page files)
// plus the one file without a twin there, the v1 page manifest, kept as a test fixture.
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

const web = new URL('../web/search-studio/data/', import.meta.url);
const release = new URL('../corpus-releases/fma2000/', import.meta.url);
export const V1_MANIFEST_FIXTURE = new URL('./fixtures/fma2000-v1-web-manifest.json', import.meta.url);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');

export const servedManifest = JSON.parse(await readFile(new URL('manifest.json', web)));
export const servesV1 = servedManifest.format !== 2;
export const v1DataUrl = name => servesV1 ? new URL(name, web) : name === 'manifest.json' ? V1_MANIFEST_FIXTURE : new URL(name, release);
export const v1Data = name => readFile(v1DataUrl(name));
export const V1_MANIFEST_SHA = sha(await v1Data('manifest.json'));
export const V1_ARTIST_METADATA_SHA = sha(await v1Data('artist-records.json'));
// The page module that pins the v1 data, as a v1 tree serves it.
export const v1StudioRelease = `export const MANIFEST_SHA='${V1_MANIFEST_SHA}';\nexport const ARTIST_METADATA_SHA='${V1_ARTIST_METADATA_SHA}';\n`;
