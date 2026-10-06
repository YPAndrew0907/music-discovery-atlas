# Rights quarantine

Status, 2026-10-06: built on the local branch `rights-fix-2000` (from `v2-deploy`) and merged into `platform-v2`. Nothing is pushed or deployed. The live site (`main` = `35cec9e`) still serves all 2,000 recordings, including the eight below, until `platform-v2` reaches `main` (stage 1 of `DEPLOY_PLAN_V2.md`).

This document explains the quarantine list, what the first entries are and why, what the rebuild without them changed, and the procedure for the next entry. The evidence comes from the rights research of 2026-10-06 (`rights/research/fma-dataset-and-automated-screen.md` in the project workspace, sections 0, 4.3, 4.5, 7 and 8, Appendix B).

## 1. The list

`corpus-releases/quarantine.json` names recordings that no corpus build may include. Each entry has:

| Field | Meaning |
|---|---|
| `id` | The recording, as in the catalog (`fma:93518`) |
| `action` | `remove`: the evidence rules the row out. `hold`: kept out and not served until a later reviewed change to the list says otherwise |
| `reason` | What was found, in one paragraph |
| `evidence` | Where the evidence is: research note sections and receipt files, as paths in the project workspace (they are not in this repository) |
| `date`, `reviewer` | When and by whom the entry was decided |
| `catalogRow` | The row as it was filed ("title" by artist, licence) |
| `audioSha256` | The exact audio bytes the entry applies to |

**Who reads it.**

- `scripts/build_corpus_release.py` applies it to every build. Listed rows leave the approved inputs in their original order, the frozen parent prefix is compared without them, and the exclusions are written into `build-receipt.json` (with reasons) and `embedding-provenance.json`.
- A listed recording's audio bytes under any other ID stop the build. That row needs its own review and entry; it is never dropped silently.
- `scripts/build_corpus_credits.py`, `scripts/pin_corpus_release.py` and `scripts/derive_hydration_plan.py` refuse a release that still holds a listed row or listed audio.
- Tests: `tests/test_rights_quarantine.py` (the list itself), `tests/test_fma2000_release.py` (the release, plan, credits and page data hold none of it) and `tests/test_corpus_rebuild_tools.py`.
- After a deploy, `tests/live_check_v2.mjs` requests every listed preview route and expects 404.

**Who does not read it.** The server never reads the list at run time. A removal reaches the live site only through a rebuilt, re-pinned release and a deploy (section 5). The Docker image carries the file next to the releases, so the list ships with the release it shaped.

## 2. The entries of 2026-10-06

All eight were in the live 2,000. Each was decided on 2026-10-06 by "rights research 2026-10-06".

| ID | Filed as | Action | Why |
|---|---|---|---|
| fma:93518 | "Mantilla" by Moon Veil (CC BY 3.0) | remove | Audio misbound: the file's ID3 tags name "Fractured Cogs" by Blue Prostitutes, tagged CC BY-NC-SA 3.0 US |
| fma:93519 | "Scorpio" by Moon Veil (CC BY 3.0) | remove | The same misbinding, and the bytes are identical to fma:25245 and fma:25246 (BY-NC-ND 3.0) and fma:93507 (BY-NC-SA 3.0 US) |
| fma:93520 | "Slow Moving" by Moon Veil (CC BY 3.0) | remove | The same misbinding |
| fma:93521 | "Ivory" by Moon Veil (CC BY 3.0) | remove | The same misbinding |
| fma:98077 | "Глазами Детей" by Чокнутый Пропеллер (CC BY 4.0) | remove | Audio misbound: the tags name "Разожгли костры" by Павкашавет бантут, whose metadata row (fma:113583) is BY-NC-SA; the file's copyright tag reads BY-NC-ND 3.0 |
| fma:154569 | "Shiver" by Mike B. Fort (CC BY 4.0) | remove | Cross-licence duplicate: bytes identical to fma:154568 (BY-NC-ND 4.0); the file's copyright tag reads BY-NC-ND 4.0 |
| fma:1382 | "Lady Love" by Parsley Flakes (CC BY 4.0) | hold | The FMA track page shows CC BY-NC today. A Wayback snapshot of 2017-05-04 shows "Attribution", so the 2017 metadata was right when crawled. It is also a pre-November-2013 upload labelled CC BY 4.0 |
| fma:125279 | "Candy Island" by Tyler Twombly (CC BY 4.0) | hold | The FMA track page shows CC BY-NC 4.0 today. The 2017 Wayback lookup returned only FMA's maintenance page |

**Why these actions.**

- **Misbound and duplicate rows (remove).** The credit names the wrong recording, and the licence of the audio actually served is a NonCommercial or NoDerivatives one, which this project does not admit (`server/corpus_release.py` admits CC BY 4.0, CC BY 3.0 unported and CC0 only). The research found the misbinding by comparing each file's embedded tags with its row, and the duplicates from the ZIP central directory (CRC-32 and size). The tags were re-read from the local cache on 2026-10-06 (`validation/rights-fix-2000/receipts/quarantined-audio-local-check.json`, ffprobe 8.1.1; no download).
- **Relicensed rows (hold).** CC licences are irrevocable, so the 2017 grant can be defended. The research recommends the conservative reading instead: do not serve a row whose licensor states a more restrictive licence today. A hold returns only through a reviewed list change, for example with the licensor's written permission.

`fma:30702` stays excluded as before. It predates this list (conflicting track and album notices) and is excluded by the builders and the hydration plan directly.

## 3. The rebuild of 2026-10-06

The release was rebuilt with the repository's builders, not edited by hand:

1. `scripts/reconstruct_release_inputs.py` rebuilt the builder's inputs from the reviewed 2,000-row release (`af67c98a…`, from Git). The original acquisition pipeline is not in the repository.
2. `scripts/build_corpus_release.py` built the release with the list applied. Node built the HNSW graph (`scripts/build_corpus_graph.mjs`, M 12, efConstruction 100, seed 43).
3. `scripts/build_corpus_credits.py` wrote the credits page.
4. `scripts/build_corpus_web.py` fitted the map and wrote the page data, using scikit-learn 1.8.0, numpy 2.3.5 and one thread.
5. `scripts/derive_hydration_plan.py` rebound the audio plan to the new release.
6. `scripts/pin_corpus_release.py` re-pinned the tree.

The driver and every receipt are in `validation/rights-fix-2000/` (`rebuild.sh`, `receipts/rebuild/`).

**Reproducible.** `validation/rights-fix-2000/repro.sh` ran the same steps again in a fresh export of the branch and compared the results with the committed tree. Every file was byte-identical: the whole release directory (including the t-SNE `layout.json`), the page data, both credits copies, both release modules and the audio plan. The pins `--check` found nothing to change (`receipts/repro/comparison.txt`). The map is reproducible on this Mac with these interpreters; another platform's numerics can move it, as an earlier check on the original release showed.

**Identities.**

| | Before (2,000) | After (1,992) |
|---|---|---|
| Release (`release.json` SHA-256) | `af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283` | `32015637189671d9f2fa44429bd8baa56696439fa6b8f1967337c2d10bbfbe42` |
| Catalog ID | `fma2000:954e9088…` | `fma2000:5b0a3ccd9307b284ff90416b1722cc7a8620573858663c05450595ea7a0e7531` |
| Graph ID | `experimental-clap-audio-graph:a8f8c699…` | `experimental-clap-audio-graph:a894cb5f38ffed73548969aacb821b348a02312cbc0e1087141e73946849f45a` |
| Vectors | `12c1dfc0…` | `5a11cc9be05d30553d61c5d798b8b6b8dba5268f30b081440f91e1d03bed49ba` |
| Ordered IDs (canonical) | `d449dd08…` | `e7dac8948f1a787835a556e2376fcae976af2c4a728e60d73b45fed24ecd8a01` |
| Index (`index.json`) | `998b8201…` | `5bec8ddc4c9532e83b3c0045d6ee02b59fb4306c6918009780a4f2aa491d6e54` |
| Audio plan (`audio-hydration.json`) | `cc00b6d1…` | `edc829227393039b3ef46bfbfa12381b3e5cae84e7442208aec22c4bc9a557b4` |
| v1 page manifest | `d0de747a…` | `afabc1692312ab4f8170a5c44112aa7250730be8051e3ff61d99062e23182766` |
| Credits page | `326fdaed…` | `45ae129825ac4d6ae279eab4546553670eeab4738bb1bf4d68f4fb2d040ae569` |
| v2 conversion, release format 2.0 (`release.json`) | `806b19ed…` | `279cd21b116f084001162c8e10177511e7ffda89064189328e31d73c5fea68b7` |
| v2 page manifest after activation, before the scale-UI merge | `7e837997…` | `ca2b04956b9850e922fa6ce9fcb3c829db803d30bccf9e388b820b754ec3458e` |
| v2 conversion, release format 2.1 (the merged converter's default; what stage 2 pins) | `aa54e992…` | `b537a7ace86ea6eebdd95b2d4cfc908e75487aeef8408295330d02c3e278d740` |
| v2 page manifest after activation with the merged code (layout schema 3) | — | `cebbefa8a90dc43473b5c9ff77480e73ec6a52dcd58155292862e170f8dcaf4f` (2.1), `f6b89f937240bac91b10d95c1dc07a7b72c9889440c505d66deacfe975f71322` (2.0) |

**Counts.**

| | Before | After |
|---|---|---|
| Recordings | 2,000 | 1,992 |
| CC BY 4.0 / CC BY 3.0 / CC0 | 1,373 / 448 / 179 | 1,369 / 444 / 179 |
| Source artist IDs | 551 | 550 |
| Genres | 14 | 14 |
| Audio bytes | 2,034,768,876 | 2,028,759,233 |
| Plan members (`fma_large` / `fma_small`) | 1,551 / 449 | 1,548 / 444 |
| Planned range bytes | 1,951,856,486 | 1,945,985,122 |
| Evidence files and bytes | 2,000 and 7,019,915 | 1,992 and 6,997,754 |
| Browse pages of 12 | 167 | 166 |

**What is byte-identical to the reviewed release** (`validation/rights-fix-2000/receipts/rebuild/identity-check.json`):

- the ordered IDs, minus the eight;
- every remaining catalog row and rights row;
- every remaining evidence file;
- every remaining artist record;
- every remaining embedding receipt;
- every remaining vector.

The first 994 rows are still exactly the frozen fma1000 rows without its six listed ones; `tests/test_fma2000_release.py` checks this.

**What the builders recompute:**

- the HNSW index and graph ID;
- the six recorded example searches (minimum recall@8 0.875);
- the t-SNE map: trustworthiness 0.9698 and 8-neighbour recall 0.458, against 0.9679 and 0.4595 before. Positions are a new fit, so the map looks different. The map never determines search results.

The v2 conversion of the new release passes every converter proof:

- the database rebuilds the v1 objects and the catalog bytes;
- all 1,992 evidence blobs are identical;
- the CSR decodes to the index;
- the parity oracle runs 66 queries with 0 exact and 0 trace mismatches;
- converting twice gives byte-identical files.

## 4. Where the eight rows still exist

- **The live site.** Until this branch is deployed, `main` serves all eight, with audio. This is the most urgent item.
- **The historical releases** `corpus-releases/fma500` and `fma1000`. They hold fma:1382, 93518 to 93521 and 98077 as catalog and rights rows (metadata, no audio). They are frozen parents: the builder proves the new release against them, and they are never selected or served. They are in the image because the image copies all of `corpus-releases/`. Dropping old releases from the image is already a follow-up in `docs/DEPLOY_PLAN_V2.md` section 7.4.
- **The legacy 108 catalog** in `music-search-studio/data/` contains fma:1382. It is used only if `active-corpus.json` is disabled or absent. In that mode `audio-delivery.json` is disabled, so no audio plays, but 1382's title and artist would appear in search results. It is an emergency fallback, not a served path. Changing its strict pins is a separate review.
- **Git history.** Older commits hold the metadata rows, never audio. The FMA metadata is CC BY 4.0 and is not what the quarantine is about. History does not need rewriting.
- **The fma5777 candidate** on the external drive also holds these rows. Any rebuild of it through these builders now drops them. The research lists further fma5777 rows to remove or hold (fma:93522, 93523, 139659 to 139662, 144760, 149803, 3855, 7179). They are not in the live release and are not on this list yet; add them before fma5777 is ever activated.

## 5. Procedure

### 5.1 When a row must leave

The research proposes these response times (section 8.4 of the FMA note; T3 of the hosting note). They are the user's to adopt:

- **Acknowledge** a notice within 2 working days.
- **Take the row out of the served release** within 1 business day where possible, and within 7 days at the latest.
- **Treat a licensor's current NonCommercial or NoDerivatives statement as a removal request.**
- **Keep a dated ledger** of every notice and its outcome.

**Steps.**

1. **Add the entry** to `corpus-releases/quarantine.json`: all eight fields, evidence that someone else can find, date and reviewer. Then run `python scripts/rights_quarantine.py`, which validates the list.
2. **Rebuild** (5.2).
3. **Run the gates** (5.3).
4. **Commit and deploy.** Deploying is the user's decision. On Render a push to `main` is live in about 4 minutes. The build hydrates only the plan's ranges, so a removed row's audio is not even downloaded.

There is no faster runtime switch. The server does not read the list. The audio delivery manifest pins every file, and a changed inventory fails closed, which would take the whole site down rather than hide one track. The rebuild below is scripted and takes minutes.

### 5.2 Rebuild

**Interpreters.** Use `venv-3.12.14` for everything except the map, which needs scikit-learn 1.8.0. `validation/rights-fix-2000/venv-build` was made with `uv`: CPython 3.12.14, numpy 2.3.5, scikit-learn 1.8.0, scipy 1.18.1, threadpoolctl 3.7.0.

**Source.** The source is the reviewed 2,000-row release (`af67c98a…`). It is in Git at any commit before `d7cf8d2`, for example `374fed9`. With N the number of rows that remain after the updated list:

```sh
B=/Users/yipengandrewwang/SOP_2027/music_app_2026-10-05; PY=$B/venv-3.12.14/bin/python; S=<a new stage directory>
mkdir -p $S/source && git archive 374fed9 corpus-releases/fma2000 | tar -x -C $S/source
$PY scripts/reconstruct_release_inputs.py --release-dir $S/source/corpus-releases/fma2000 \
  --expected-manifest-sha256 af67c98ae1d6edce3a89ec696f1348972ecedd62d067bf27f7a09ac1982ba283 \
  --audio-dir $B/audio2000 --output-dir $S/inputs
rm -rf corpus-releases/fma2000
$PY scripts/build_corpus_release.py --ingestion-dir $S/inputs/ingestion --embeddings-dir $S/inputs/embeddings \
  --output-dir corpus-releases/fma2000 --count N --coverage "<N screened FMA excerpts: ...>"
NEW=$(shasum -a 256 corpus-releases/fma2000/release.json | cut -d' ' -f1)
$PY scripts/build_corpus_credits.py --release-dir corpus-releases/fma2000 --expected-manifest-sha256 $NEW --count N --output $S/credits.html
<sklearn venv>/bin/python scripts/build_corpus_web.py --release-dir corpus-releases/fma2000 --expected-manifest-sha256 $NEW \
  --credits $S/credits.html --count N
$PY scripts/derive_hydration_plan.py --expected-manifest-sha256 $NEW --count N   # prints the three pins below
# edit scripts/hydrate_corpus_audio.py: PLAN_SHA, RELEASE_SHA, RELEASE_COUNT (reviewed pins, by hand)
$PY scripts/pin_corpus_release.py --expected-manifest-sha256 $NEW --count N
$PY scripts/pin_corpus_release.py --expected-manifest-sha256 $NEW --count N --check   # must print no changes
```

**By hand, for a new N:**

- Add N to the reviewed counts in `scripts/build_corpus_graph.mjs`.
- Set the defaults (`--count`, `FMA2000_COVERAGE`) in `scripts/build_corpus_release.py` and `scripts/build_corpus_web.py`.
- Update the tests that pin the release:
  - `tests/v2_fixtures.py`: `V1_SHA`, `V1_COUNT`, `V1_EVIDENCE_BYTES`;
  - `tests/test_fma2000_release.py`;
  - the range and audio byte totals in `tests/test_audio_hydration.py` and `tests/test_hydrate_release_v2.py`;
  - the page counts in the Node tests and both browser fixtures;
  - `tests/fixtures/fma2000-v1-web-manifest.json` (copy the new `web/search-studio/data/manifest.json`);
  - the defaults in `tests/live_check_v2.mjs`.

**Always rebuild from the reviewed 2,000 rows.** An already-filtered release is not a valid source: the builder checks the embedding export against the unfiltered frozen parent (fma1000), so the export must still hold every parent row. The same source, list, builders and interpreters give the same bytes (section 3). The catalog ID hashes the reconstructed input files, so it follows the source as well.

### 5.3 Gates before a push

- Python: `$B/venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
- Node: `node --test tests/web_*.test.mjs`.
- Both browser fixtures (`tests/browser_search_ui.mjs`, `tests/browser_collection_v2.mjs`).
- The pins `--check`, `scripts/verify_corpus_release.py` and `scripts/hydrate_corpus_audio.py --verify-plan`.
- A local real server behind the TLS terminator, with `validation/atlas/live_check_atlas.mjs` (its local copy) at 17/17.
- For v2: the conversion, the activation `--check` and `tests/live_check_v2.mjs` at 26/26.

`validation/rights-fix-2000/build_root.sh` runs the image build's steps on a disposable root; `MODE=v1` gives the v1 selection, the default gives an activated v2 root.

### 5.4 Releasing a hold

Delete the entry in a reviewed commit that says why. The commit should cite new evidence: the licensor's written permission, or the licensor's page showing an admitted licence again. Then rebuild. A `remove` entry should not return unless the evidence itself turns out to be wrong.

## 6. Takedown contact and proposed page

No takedown contact exists in the repository or on the site, and none is invented here.

**Contact.** The contact is `[TAKEDOWN CONTACT: to be added by the user]`. It should be an e-mail address the user reads, plus, if the user registers a DMCA agent, the agent's name and postal address. The hosting research recommends registering (T1: $6 in the Copyright Office directory, renewed every three years). That registration needs the user's full legal name and a street address, which only the user can decide to publish.

**Do not serve the page until the contact is filled in.** A page with a blank contact is worse than none.

Proposed `/notices/takedown.html`, linked from the credits page header and the About dialog:

> **Rights and removal requests**
>
> Music Discovery Atlas is a non-commercial research demo. It streams 30-second excerpts of recordings that their creators published on the Free Music Archive under CC BY 4.0, CC BY 3.0 or CC0, with the credit and licence of each recording on the [track credits page](/notices/track-attribution.html). It has no ads, payments or sponsors, and no user uploads.
>
> If you hold rights in a recording here and did not license it this way, or if you want your credit changed or removed, write to **[TAKEDOWN CONTACT]**. Please include:
>
> 1. the recording's title, or its credits-page link (each entry has its own anchor, such as `#fma-1383`);
> 2. who you are and how the recording is yours, or whom you act for;
> 3. what you would like: removal of the recording, or removal or correction of the credit;
> 4. a way to reach you.
>
> For a notice under the U.S. Digital Millennium Copyright Act, please also include the elements of 17 U.S.C. §512(c)(3)(A): your physical or electronic signature; identification of the work and of the material you say infringes it; a statement of your good-faith belief that the use is not authorized by the owner, its agent or the law; and a statement, under penalty of perjury, that your notice is accurate and that you are authorized to act for the owner.
>
> We acknowledge requests within two working days. We take the recording out of the search, the map and playback while we look into it, usually within one business day and always within seven days. We restore it only if the question is settled in writing. Removing a credit on request is an obligation of the CC licences, and we honour it the same way. If a source turns out to have mislabelled rights, we stop taking recordings from that source until its other recordings have been checked.

**Supporting changes when the page goes live:**

- Add the page to `web-manifest.json`, the Git and Docker allowlists, and `package-manifest.json`, as for every served file.
- Link it from the credits page header (`scripts/build_corpus_credits.py`) and the About dialog.

## 7. Open items

1. **Deploy.** Deploying `platform-v2` (stage 1 of `DEPLOY_PLAN_V2.md`) is the user's decision. Until then the eight rows are live.
2. **The contact and the takedown page** (section 6).
3. **The review is not finished for the rest of the 2,000.** The research expects about 1,500 of the 2,000 to pass the full conservative gate. The other two groups:
   - 444 deployed rows are no longer listed on FMA. Their 2017 grant still stands, and the policy for them is a decision still to take.
   - About 4% of the listed rows are expected to show a changed licence.

   The full live-page pass (about 1,556 page requests at a 2-second spacing), Wayback checks for the delisted rows, and the cover and remix review are the next step. Each finding becomes an entry here, with a rebuild.

   **The adopted decision goes further.** The rights decision adopted later the same day (`rights/RIGHTS_DECISION_2026-10-06.md` section 7 and `rights/DECISION.json` in the project workspace) takes 764 of the 2,000 off now and keeps 1,236 serving. The 764 are:
   - the six removals here;
   - 121 pre-2013 holds, fma:1382 among them;
   - 433 delisted rows;
   - 27 rows whose Internet Archive record is more restrictive;
   - fma:125279;
   - 15 disabled WFMU rows;
   - 5 contest and song-title holds;
   - 156 manual or authority checks.

   It asks for a request-time suppression list rather than a rebuild per row, and for the site changes of its section 7.2 before the next deploy. This list carries 8 of the 764. The suppression list and the site changes are not built.
4. **The 122 deployed pre-2013 rows labelled "CC BY 4.0"** need the mandatory per-row live and Wayback check (research section 7, step 7). fma:1382 came from that stratum. fma:125279 did not, so the live check is needed across the whole release too.
5. **The legacy 108 fallback** still contains fma:1382 (section 4).
