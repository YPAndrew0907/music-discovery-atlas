import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {refineCandidates,resultPage,resultScope,sourceGenres,RESULT_PAGE_SIZE} from '../web/search-studio/src/results-view.mjs';
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

test('sound refinement counts never imply full-catalog filtered retrieval',()=>{
  const pool=candidates.slice(0,16),filtered=pool.slice(3,6),page=resultPage(filtered);
  const summary=resultScope({channel:'sound',catalogCount:5000,candidateCount:16,page});
  assert.match(summary,/Showing 1–3 of 3 from 16 retrieved sound candidates/);
  assert.match(summary,/not the full collection/);
  const browse=resultScope({channel:'browse',catalogCount:5000,candidateCount:5000,page:resultPage(candidates)});
  assert.match(browse,/5,000 recordings in the collection/);
});
