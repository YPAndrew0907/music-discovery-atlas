import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {refineCandidates,resultPage,resultScope,compactResultScope,sourceGenres,RESULT_PAGE_SIZE} from '../web/search-studio/src/results-view.mjs';
const catalog=JSON.parse(await readFile(new URL('../web/search-studio/data/catalog.json',import.meta.url)));
const candidates=catalog.tracks.map((_,row)=>({row,score:null,sourceRank:row+1}));

test('real catalog refinements use recorded genre and names without reindexing or changing scores',()=>{
  const genre=catalog.tracks[1500].genre;
  const selected=refineCandidates(candidates,catalog.tracks,{genre});
  assert.deepEqual(selected.map(r=>r.row),catalog.tracks.flatMap((t,row)=>t.genre===genre?[row]:[]));
  assert.ok(selected.every(r=>candidates[r.row]===r));
  assert.equal(sourceGenres(candidates,catalog.tracks).reduce((n,g)=>n+g.count,0),catalog.tracks.length);
  const matches=refineCandidates(candidates,catalog.tracks,{text:catalog.tracks[1500].album});
  assert.ok(matches.some(r=>r.row===1500));
});

test('preview refinement uses only the supplied verified availability gate',()=>{
  const available=new Set([2,47,1200]);
  const rows=refineCandidates(candidates,catalog.tracks,{previewOnly:true},row=>available.has(row));
  assert.deepEqual(rows.map(r=>r.row),[2,47,1200]);
  assert.equal(refineCandidates(candidates,catalog.tracks,{previewOnly:true}).length,0);
});

test('5k synthetic metadata fixture stays bounded per page, includes every ID once, and clamps empty/last pages',()=>{
  // Synthetic metadata exercises UI scale; this does not add recordings to any release.
  const tracks=Array.from({length:5000},(_,i)=>({title:`Recording ${i}`,artist:i%2?'Café Artist':'Other',album:'Test',genre:i%2?'A':'B'}));
  const pool=tracks.map((_,row)=>({row,score:null}));
  const pages=Math.ceil(pool.length/RESULT_PAGE_SIZE),seen=[];
  for(let n=0;n<pages;n++){const page=resultPage(pool,n);assert.ok(page.rows.length<=12);seen.push(...page.rows.map(r=>r.row));}
  assert.deepEqual(seen,pool.map(r=>r.row));assert.equal(resultPage(pool,9999).end,5000);
  const filtered=refineCandidates(pool,tracks,{text:'cafe',genre:'A'});assert.equal(filtered.length,2500);
  assert.equal(resultPage([],99).page,0);assert.equal(resultPage([],99).pages,0);
});

const pool5k=()=>Array.from({length:5000},(_,row)=>({row,score:null}));
test('sound refinement counts never imply full-catalog filtered retrieval',()=>{
  const pool=candidates.slice(0,16),filtered=pool.slice(3,6),page=resultPage(filtered);
  const summary=resultScope({channel:'sound',catalogCount:5000,candidateCount:16,page});
  assert.match(summary,/Showing 1–3 of 3 from 16 retrieved sound candidates/);
  assert.match(summary,/not the full collection/);
  const browse=resultScope({channel:'browse',catalogCount:5000,candidateCount:5000,page:resultPage(candidates)});
  assert.match(browse,/5,000 recordings in the collection/);
  // Every count in a scope line uses the same locale formatting (no bare 2000 beside 2,000).
  assert.match(resultScope({channel:'browse',catalogCount:5000,candidateCount:5000,page:resultPage(pool5k(),400)}),/^Showing 4,801–4,812 of 5,000 recordings · 5,000 recordings in the collection\./);
  assert.match(resultScope({channel:'lookup',catalogCount:5000,candidateCount:1234,page:resultPage(pool5k().slice(0,1234),100)}),/^Showing 1,201–1,212 of 1,234 name matches · 1,234 title \/ artist matches across 5,000 recordings\./);
});

test('compact scope lines name the range, the pool and the searched collection with the same locale grouping',()=>{
  const pool=n=>Array.from({length:n},(_,row)=>({row,score:null}));
  assert.equal(compactResultScope({channel:'sound',catalogCount:2000,candidateCount:16,page:resultPage(pool(16))}),'1–12 of 16 · searched 2,000');
  assert.equal(compactResultScope({channel:'neighbors',catalogCount:2000,candidateCount:16,page:resultPage(pool(16),1)}),'13–16 of 16 · searched 2,000');
  assert.equal(compactResultScope({channel:'lookup',catalogCount:2000,candidateCount:7,page:resultPage(pool(7))}),'1–7 of 7 name matches');
  assert.equal(compactResultScope({channel:'browse',catalogCount:2000,candidateCount:2000,page:resultPage(pool(2000))}),'1–12 of 2,000 recordings');
  assert.equal(compactResultScope({channel:'browse',catalogCount:5000,candidateCount:5000,page:resultPage(pool5k(),400)}),'4,801–4,812 of 5,000 recordings');
  // Refinements that hide every candidate keep the pool size; an empty lookup says so plainly.
  assert.equal(compactResultScope({channel:'sound',catalogCount:5000,candidateCount:16,page:resultPage([])}),'0 of 16 · searched 5,000');
  assert.equal(compactResultScope({channel:'lookup',catalogCount:5000,candidateCount:0,page:resultPage([])}),'0 name matches');
  // The compact line never claims a different range or pool than the full sentence it summarises.
  for(const [channel,count,page] of [['sound',16,0],['sound',16,1],['browse',5000,416],['lookup',1234,102]]){
    const view=resultPage(pool(count),page),full=resultScope({channel,catalogCount:5000,candidateCount:count,page:view}),compact=compactResultScope({channel,catalogCount:5000,candidateCount:count,page:view});
    assert.ok(full.includes(compact.split(' · ')[0].replace(/ (recordings|name matches)$/,'')),`${compact} vs ${full}`);
  }
});
