import {sourceGenres,refineCandidates,resultPage,resultScope} from './results-view.mjs';
import {reviewQueryLimits} from './query-limits.mjs';
import {AudioMap} from './graph.mjs';
import {indexConnections} from './search-motion.mjs';
import {MANIFEST_SHA,ARTIST_METADATA_SHA} from './studio-release.mjs';
import {rankCandidates} from './rerank.mjs';
import {ServerSearch,publicCharacterLimit} from './contracts.mjs';
import {SERVER_CONFIG,loadDeploymentConfig} from './server-config.mjs';
import {loadAudioDelivery,previewForTrack,UNAVAILABLE_PREVIEW} from './audio-delivery.mjs';
import {HNSW,exactSearch} from './hnsw.mjs';
import {BrowserEncoder} from '../../listen-lab/src/controller.mjs';
import {RELEASE} from '../../listen-lab/src/release.mjs';
import {metadataSearch} from '../../listen-lab/src/retrieval.mjs';
const $=s=>document.querySelector(s),esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const direction=new URL(location.href).searchParams.get('direction')==='field'?'field':'atlas';document.body.dataset.direction=direction;document.querySelector(`nav a[href="?direction=${direction}"]`).setAttribute('aria-current','page');
let resultChannel='sound',pageIndex=0,viewPage=resultPage([]),searchPending=false;
let catalog,manifest,index,vectors,examples,map,rows=[],selected=null,kept=[],undo=[],playing=null,queryGeneration=0,modelGeneration=0,inflight=null,currentQuery='',exportUrl=null,server=null,engineSelection='server',artistRecords=null,candidateRows=[],currentTrace=null,rankingPacket=null,spreadArtists=false,queryReview=null,audioDelivery=new Map(),serverConfig=SERVER_CONFIG,playbackGeneration=0,connectionGeneration=0,serverState='checking',pageActive=true,readinessNoticeGeneration=null,pageGeneration=0;
const encoder=new BrowserEncoder({status:state=>{const ready=['ready','encoding'].includes(state.state);$('#model-state').textContent=state.state;updateEngineLabel();$('#unload-local').hidden=state.state==='unloaded'||state.state==='error';$('#unload-local').textContent=state.state==='loading'?'Cancel download':'Unload model';$('#model-progress').hidden=state.state!=='loading';if(state.stage==='download'){$('#model-progress').max=state.total;$('#model-progress').value=state.received;$('#model-detail').textContent=`${(state.received/1e6).toFixed(1)} / ${(state.total/1e6).toFixed(1)} MB`;}else if(state.stage==='initializing'){$('#model-progress').removeAttribute('value');$('#model-detail').textContent='Download verified. Preparing the CPU runtime…';}else $('#model-detail').textContent=state.error??(ready?'Model ready on this device. Descriptions run locally only when this engine is selected.':state.state==='loading'?'Preparing local search…':'No model is running. Recorded examples remain available.');}});
function setSearchBusy(busy){searchPending=busy;$('#results-region').setAttribute('aria-busy',String(busy));$('#cancel-search').hidden=!busy;$('#search').textContent=busy?'Searching…':'Find music';}
function updateEngineLabel(){
  const lookup=$('#query-kind').value==='lookup';
  $('#query-label').textContent=lookup?'Enter a recorded title or artist':'Describe the music you want';
  $('#query').placeholder=lookup?'Find a title or artist in this collection…':'Describe a sound, a mood, a moment…';
  const localReady=['ready','encoding'].includes(encoder.state);
  const text=engineSelection==='server'?(serverState==='ready'?'Server ready':serverState==='checking'?'Checking server…':'Server unavailable'):engineSelection==='local'?(localReady?'On-device ready':encoder.state==='loading'?'Preparing on device…':'On-device off'):'Live search paused';
  $('#open-engine').textContent=text;$('#open-engine').setAttribute('aria-label','Search settings: '+text);
  $('#server-state').textContent=serverState==='ready'?(engineSelection==='server'?'Ready · selected':'Ready'):serverState==='checking'?'Checking…':'Unavailable';
  const checking=engineSelection==='server'&&serverState==='checking'&&$('#query-kind').value!=='lookup';
  $('#search').disabled=!catalog||checking;
  if(checking)$('#search').textContent='Checking server…';else if(!searchPending&&$('#search').textContent==='Checking server…')setSearchBusy(false);
  $('#use-server').disabled=serverState==='checking';
  $('#use-server').textContent=serverState!=='ready'?'Check server availability':engineSelection==='server'?'Pause server search':'Use server search';
  $('#enable-local').hidden=encoder.state==='loading'||(localReady&&engineSelection==='local');
  $('#enable-local').textContent=localReady?'Use on-device search':'Download & enable';
  const limit=engineSelection==='server'&&serverState==='ready'&&!lookup?publicCharacterLimit(server?.manifest):null;
  $('#query').maxLength=limit??4096;
  $('#search-processing').textContent=$('#query-kind').value==='lookup'?'Title / artist lookup stays in this browser.':engineSelection==='local'?'Search is processed on this device.':engineSelection==='recorded'?'Live search is paused. Recorded examples stay in this browser.':`Search is processed on this server. Only submitted descriptions are sent.${limit?` Up to ${limit.toLocaleString()} characters.`:''}`;
}

function preview(row){return previewForTrack(catalog?.tracks[row],audioDelivery);}
function playButton(row,{compact=false}={}){const t=catalog.tracks[row],p=preview(row),paused=playing!==row||$('#audio').paused;return `<button class="${compact?'play-track ':''}${p.available?'':'preview-unavailable'}" data-play="${row}" ${p.available?'':'disabled'} aria-label="${esc(p.available?(paused?'Play ':'Pause ')+t.title:UNAVAILABLE_PREVIEW+' for '+t.title)}">${p.available?(compact?(paused?'▷':'Ⅱ'):(paused?'Play':'Pause')):UNAVAILABLE_PREVIEW}</button>`;}
function refreshPlaybackAvailability(){$('#audio-availability').textContent=audioDelivery.size?`${audioDelivery.size.toLocaleString()} of ${catalog.tracks.length.toLocaleString()} recordings have verified previews.`:'Preview unavailable. Search, explore neighbors and keep tracks; audio has not been enabled.';if(candidateRows.length&&$('#preview-only').checked)applyDisplayPolicy({preserveSelection:true});else{renderResults();if(selected!==null)choose(selected);}}
function resetPlayer(){playbackGeneration++;const audio=$('#audio');audio.pause();audio.controls=false;audio.removeAttribute('src');audio.load();playing=null;$('#player').hidden=true;$('#player-toggle').disabled=true;$('#player-toggle').textContent=UNAVAILABLE_PREVIEW;$('#player-toggle').setAttribute('aria-label',UNAVAILABLE_PREVIEW);}

async function sha(bytes){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
async function verified(name){const pin=manifest.files[name],response=await fetch(new URL('../'+(pin.path.startsWith('data/')?pin.path:'data/'+pin.path),import.meta.url));if(!response.ok)throw new Error('Collection file unavailable: '+name);const bytes=await response.arrayBuffer();if(await sha(bytes)!==pin.sha256)throw new Error('Collection identity check failed: '+name);return name==='vectors'?new Float32Array(bytes):JSON.parse(new TextDecoder().decode(bytes));}
function status(text,{quiet=false}={}){$('#status').textContent=text;$('#status').hidden=quiet;$('#search-detail').textContent=text;}
function renderQueryLimits(text,label){const channel=label.startsWith('Title / artist')?'lookup':label.startsWith('Audio neighbors')?'neighbors':'description';queryReview=reviewQueryLimits(text,{channel});const target=$('#query-limits');target.hidden=!queryReview.requests.length;$('.list-foot').hidden=!!queryReview.requests.length;const phrases=[...new Set(queryReview.requests.map(r=>r.sourceText))];target.innerHTML=queryReview.requests.length?`<p>Unverified: ${phrases.map(t=>'“'+esc(t)+'”').join(' · ')}</p><small>These requests are not filters.${queryReview.durationNote?' Full lengths are unknown; previews are 30 seconds.':''}</small>`:'';}
function captureControl(scope){const active=document.activeElement;if(!active?.closest(scope))return null;for(const key of ['select','play','keep','nearby','details','credit'])if(active.dataset[key]!==undefined)return {key,value:active.dataset[key]};return null;}
function restoreControl(saved,scope){if(saved)$(`${scope} [data-${saved.key}="${saved.value}"]`)?.focus({preventScroll:true});}
function renderResults(){
  const active=captureControl('#results');
  const openDetails=new Set([...document.querySelectorAll('#results .track-details[open]')].map(el=>el.dataset.row));
  $('#results').innerHTML=rows.map((r,n)=>{
    const t=catalog.tracks[r.row],current=playing===r.row,playingNow=current&&!$('#audio').paused;
    return `<li class="result ${selected===r.row?'selected':''} ${current?'is-playing':''}" data-row="${r.row}" ${current?'aria-current="true"':''}>
      <span class="rank">${String(viewPage.start+n+1).padStart(2,'0')}</span>
      <button class="track-select" data-select="${r.row}" aria-pressed="${selected===r.row}" aria-label="Inspect ${esc(t.title)}"><strong>${esc(t.title)}</strong><span>${esc(t.artist)}</span><span class="track-tags">${esc(t.genre||'No source genre')}${current?` · <span class="playing-label">${playingNow?'Playing':'Paused'}</span>`:''}</span></button>
      ${playButton(r.row,{compact:true})}
      <div class="result-meta"><button data-nearby="${r.row}" aria-label="Find audio neighbors of ${esc(t.title)}">Neighbors</button><button data-keep="${r.row}" aria-label="${kept.includes(r.row)?'Remove':'Keep'} ${esc(t.title)}" aria-pressed="${kept.includes(r.row)}">${kept.includes(r.row)?'Kept':'Keep'}</button>
      <details class="track-details" data-row="${r.row}" ${openDetails.has(String(r.row))?'open':''}><summary data-details="${r.row}">Details</summary><div><span>${r.score===null?'Recorded name / catalog match':r.score.toFixed(3)+' similarity · original sound rank '+r.sourceRank}</span><span>${esc(t.album||'No recorded album')} · ${esc(t.license)}</span><a data-credit="${r.row}" href="/notices/track-attribution.html#${t.id.replace(':','-')}">Credit &amp; license</a></div></details></div></li>`;
  }).join('');restoreControl(active,'#results');
}
function refinements(){return {text:$('#refine-text').value,genre:$('#genre-filter').value,previewOnly:!!$('#preview-only').checked};}
function updateGenreOptions(){
  const selectedGenre=$('#genre-filter').value,genres=sourceGenres(candidateRows,catalog.tracks);
  if(selectedGenre&&!genres.some(item=>item.genre===selectedGenre))genres.push({genre:selectedGenre,count:0});
  $('#genre-filter').innerHTML='<option value="">All source genres</option>'+genres.map(({genre,count})=>`<option value="${esc(genre)}">${esc(genre)} (${count})</option>`).join('');
  $('#genre-filter').value=selectedGenre;
}
function clearRefinements(){
  $('#refine-text').value='';$('#genre-filter').value='';$('#preview-only').checked=false;pageIndex=0;
  applyDisplayPolicy({preserveSelection:true});
}
function changePage(delta){
  pageIndex+=delta;applyDisplayPolicy({preserveSelection:true});$('#results-heading').focus({preventScroll:true});
  $('#results-heading').scrollIntoView({block:'start',behavior:'instant'});
}
function browseCollection(){
  queryGeneration++;server?.cancel();setSearchBusy(false);updateEngineLabel();
  showResult({text:'',ranked:catalog.tracks.map((_,row)=>({row,score:null})),trace:{events:[],finalResults:[]},label:'Collection browse · local metadata',timing:'All recorded catalog entries. No description was sent.',animate:false});
}

function choose(row,{scroll=false,explicit=false}={}){const focus=captureControl('#node-inspector');selected=row;$('#focus-track').disabled=false;map.select(row,{explicit});renderResults();if(scroll)$(`#results [data-row="${row}"]`)?.scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});const t=catalog.tracks[row];const outside=!rows.some(r=>r.row===row);$('#node-inspector').hidden=!outside;if(outside)$('#node-inspector').innerHTML=`<p class="eyebrow">Selected outside this result page</p><strong>${esc(t.title)}</strong><span>${esc(t.artist)}</span><div>${playButton(row)}<button data-nearby="${row}">Find neighbors</button><button data-keep="${row}">${kept.includes(row)?'Kept':'Keep'}</button></div>`;$('#selection-summary').textContent=`${t.title} · ${t.artist} · ${t.genre}`;restoreControl(focus,'#node-inspector');}
function applyDisplayPolicy({preserveSelection=false,animate=false}={}){
  const old=selected;
  let ordered=candidateRows.map((r,i)=>({...r,sourceRank:r.sourceRank??i+1}));
  if(rankingPacket){
    const policy=rankCandidates(rankingPacket,{mode:spreadArtists?'more-artists':'closest',limit:rankingPacket.candidates.length});
    ordered=policy.results.map(r=>({row:r.row,score:r.rawSimilarity,sourceRank:r.sourceRank}));
    $('#spread-note').textContent=spreadArtists?`Artist breadth within ${policy.candidateCount} sound candidates; original scores retained.`:'Closest sound first';
  }else $('#spread-note').textContent=candidateRows.some(r=>Number.isFinite(r.score))?'Closest sound first · artist option unavailable':'Recorded metadata · no sound ranking';
  const filter=refinements(),filtered=refineCandidates(ordered,catalog.tracks,filter,row=>preview(row).available);
  viewPage=resultPage(filtered,pageIndex);pageIndex=viewPage.page;rows=viewPage.rows.map((r,i)=>({...r,displayRank:viewPage.start+i+1}));
  const hasFilters=!!(filter.text.trim()||filter.genre||filter.previewOnly);
  $('#clear-refinements').disabled=!hasFilters;$('#empty-clear').hidden=!hasFilters;
  $('#spread-results').disabled=!rankingPacket;$('#spread-results').setAttribute('aria-pressed',String(!!rankingPacket&&spreadArtists));
  $('#result-scope').textContent=resultScope({channel:resultChannel,catalogCount:catalog.tracks.length,candidateCount:candidateRows.length,page:viewPage});
  $('#refinement-scope').textContent=resultChannel==='sound'||resultChannel==='neighbors'?`these ${candidateRows.length} candidates`:'recorded catalog matches';
  $('#page-position').textContent=viewPage.pages?`Page ${viewPage.page+1} of ${viewPage.pages}`:'No results';
  $('#page-indicator').textContent=viewPage.total?`${(viewPage.start+1).toLocaleString()}–${viewPage.end.toLocaleString()} / ${viewPage.total.toLocaleString()}`:'0 results';
  $('#previous-page').disabled=pageIndex===0;$('#next-page').disabled=pageIndex+1>=viewPage.pages;
  $('#results-empty').hidden=rows.length>0;
  $('#empty-detail').textContent=hasFilters?`No ${resultChannel==='sound'||resultChannel==='neighbors'?'retrieved sound candidates':'catalog matches'} meet these refinements. Clear them, try a new search, or browse the full collection.`:'No recorded title or artist matches this query. Try fewer words or browse the collection.';
  // A browse page is not a ranking: nothing is drawn as a match and the Matches stage is off.
  // Name matches are marked but unnumbered. Refinement and paging keep the viewer's map view.
  const mapRows=resultChannel==='browse'?[]:resultChannel==='lookup'?rows.map(r=>({...r,displayRank:null})):rows;
  $('#fit').disabled=resultChannel==='browse';
  map.setSearch(currentTrace,mapRows,{animate:animate&&$('#map-panel').open,preserveView:preserveSelection});
  selected=preserveSelection&&rows.some(r=>r.row===old)?old:rows[0]?.row??null;
  $('#focus-track').disabled=selected===null;$('#node-inspector').hidden=true;
  renderResults();if(selected!==null)choose(selected);
}

function showResult({text,ranked,trace,label,timing,encoderSpaceId=manifest.graphId,sourceRankingId='search-'+queryGeneration,animate=true}){
  // Validate the display policy's input before any state or label changes, so a rejected
  // result set leaves the previous results, labels and refinements fully consistent.
  const packet=ranked.length&&ranked.every(r=>Number.isFinite(r.score))&&artistRecords?{catalogId:catalog.id,encoderSpaceId,sourceRankingId,artistMetadataSha256:ARTIST_METADATA_SHA,candidates:ranked.map((r,i)=>({trackId:catalog.tracks[r.row].id,artistId:artistRecords.get(catalog.tracks[r.row].id),sourceRank:i+1,rawSimilarity:r.score,row:r.row}))}:null;
  if(packet)rankCandidates(packet,{mode:spreadArtists?'more-artists':'closest',limit:packet.candidates.length});
  if(!label.startsWith('Recorded'))for(const b of document.querySelectorAll('[data-example]'))b.setAttribute('aria-pressed','false');candidateRows=ranked;currentTrace=trace;pageIndex=0;resultChannel=label.startsWith('Collection')?'browse':label.startsWith('Title')?'lookup':label.startsWith('Audio')?'neighbors':'sound';updateGenreOptions();rankingPacket=packet;currentQuery=text;renderQueryLimits(text,label);$('#results').scrollTop=0;if(text)$('#query').value=text;$('#node-inspector').hidden=true;$('#results-heading').textContent=resultChannel==='browse'?'Collection':resultChannel==='lookup'?'Title / artist matches':'Sound matches';$('#results-source').textContent=label+(text?` · “${text.length>85?text.slice(0,82)+'…':text}”`:'');$('#engine-label').textContent=label.startsWith('Recorded')?'Recorded example':label.startsWith('Live')?'Live · '+(label.includes('server')?'server':'on device'):label.startsWith('Title')?'Title / artist':label.startsWith('Collection')?'Catalog browse':'Audio neighbors';applyDisplayPolicy({animate});const misses=trace.events.length?rows.filter(r=>!trace.finalResults.some(x=>x.id===r.row)).length:0,hidden=candidateRows.length-viewPage.total;status(`${timing} ${trace.events.length?'Results ready. The map follows this search through the collection.':'Choose a recording, then find its audio neighbors.'}${misses?` ${misses} exact result${misses===1?' is':'s are'} outside the approximate graph shortlist.`:''}${hidden>0?` Refinements hide ${hidden.toLocaleString()} of ${candidateRows.length.toLocaleString()}; clear them to see every result.`:''}`,{quiet:true});}
function exampleSearch(id,{animate=true}={}){queryGeneration++;server?.cancel();$('#query-kind').value='description';setSearchBusy(false);updateEngineLabel();const item=examples.examples.find(x=>x.id===id);if(!item)return;map?.pause();const q=new Float32Array(item.queryVector),ranked=exactSearch(vectors,q,16).map(r=>({row:r.id,score:1-r.distance})),found=index.search(q,{k:16,ef:32,trace:true,spaceId:manifest.graphId});showResult({text:item.text,ranked,trace:found.trace,label:'Recorded example · native Q8',timing:'Saved public description, real audio-vector search.',encoderSpaceId:item.queryProfileId,sourceRankingId:item.queryVectorSha256,animate});for(const b of document.querySelectorAll('[data-example]'))b.setAttribute('aria-pressed',String(b.dataset.example===id));}
async function runQuery(text){if(!pageActive||!catalog||!map)return;const generation=++queryGeneration;server?.cancel();map?.pause();setSearchBusy(false);updateEngineLabel();if(!text.trim()){status($('#query-kind').value==='lookup'?'Enter a recorded title or artist, or choose Browse collection.':'Describe the music you want to hear.');$('#query').focus();return;}if($('#query-kind').value==='lookup'){const found=metadataSearch(text,catalog.tracks,catalog.tracks.length);showResult({text,ranked:found.map(r=>({row:r.row,score:null})),trace:{events:[],finalResults:[]},label:'Title / artist lookup',timing:found.length?'Matched recorded names only.':'No title or artist matches in this limited catalog.'});setSearchBusy(false);return;}if(engineSelection==='server'){
  if(server?.manifest&&serverState==='ready'){
    const limit=publicCharacterLimit(server.manifest);
    if(limit&&text.length>limit){status(`This description is ${text.length.toLocaleString()} characters; this server accepts at most ${limit.toLocaleString()}. Nothing was sent. Shorten it and submit again.`);$('#query').focus();return;}
    await runServerQuery(text,generation);return;
  }
  if(serverState==='checking'){readinessNoticeGeneration=generation;status('Checking server availability. Nothing was sent. Submit again when the server is ready.');updateEngineLabel();return;}
  status('Server search is unavailable. Nothing was sent. Checking availability…');
  await connectServer();
  if(generation===queryGeneration&&pageActive&&engineSelection==='server')status(serverState==='ready'?'Server ready. Submit your description again to search.':'Server search is unavailable. Nothing was sent. Try again later, choose a recorded example, or enable on-device search in settings.');
  return;
}
if(engineSelection!=='local'||!['ready','encoding'].includes(encoder.state)){
  status(engineSelection==='local'?'On-device search is not ready. Enable the model in settings or choose a recorded example. Nothing was sent.':'Live search is paused. Choose a recorded example or select a search engine in settings. Nothing was sent.');
  $('#engine-dialog').showModal();return;
}
map.pause();status('Reading your description on this device…');setSearchBusy(true);try{if(inflight)await inflight.catch(()=>{});if(generation!==queryGeneration)return;const started=performance.now();inflight=encoder.encode(text);const response=await inflight;if(generation!==queryGeneration)return;const e=response.encoded;if(e.spaceId!==RELEASE.encoderSpaceId||e.modelSha256!==RELEASE.textAssets[0].sha256||e.tokenizerSha256!==RELEASE.textAssets[1].sha256)throw new Error('The encoder does not match this collection');const q=new Float32Array(e.vector);const found=exactSearch(vectors,q,16);const graph=await index.searchAsync(q,{k:16,ef:32,trace:true,spaceId:manifest.graphId});if(generation!==queryGeneration)return;showResult({text,ranked:found.map(r=>({row:r.id,score:1-r.distance})),trace:graph.trace,label:'Live search · on-device Q8',encoderSpaceId:e.spaceId,sourceRankingId:'local-'+generation,timing:`${Math.round(performance.now()-started)} ms encode and search.${e.truncated?' Input reached the 77-token limit.':''}`});for(const b of document.querySelectorAll('[data-example]'))b.setAttribute('aria-pressed','false');}catch(e){if(generation===queryGeneration&&e.name!=='AbortError')status(e.message);}finally{if(generation===queryGeneration){inflight=null;setSearchBusy(false);}}}
async function runServerQuery(text,generation){map.pause();status('Finding recordings on the configured server… Previous results remain until this search finishes.');setSearchBusy(true);try{const response=await server.search(text,new Set(catalog.tracks.map(t=>t.id)));if(generation!==queryGeneration)return;if(!response.trace?.events||response.indexSha256!==manifest.indexSha256||response.graphId!==manifest.graphId)throw new Error('Server trace does not match this audio graph');const ranked=response.results.map(r=>{const row=catalog.tracks.findIndex(t=>t.id===r.id);if(row<0||(r.row!==undefined&&r.row!==row))throw new Error('Server recording order mismatch');return{row,score:r.cosineSimilarity};});showResult({text,ranked,trace:response.trace,label:'Live search · server Q8',encoderSpaceId:response.engineId,sourceRankingId:response.requestId,timing:`${Math.round(response.timingMs.serverCompute)} ms server compute; network time is additional.${response.tokenization.truncated?' Input reached the 77-token limit.':''}`});}catch(e){if(generation===queryGeneration&&e.name!=='AbortError'){const failure=describeServerFailure(e);status(failure.text);if(failure.unavailable)markServerUnavailable();}}finally{if(generation===queryGeneration)setSearchBusy(false);}}
// Refusals and transport failures are explained in the server's own terms. A server that
// stopped answering, errored or changed identity is no longer "ready": the next submit
// re-checks availability instead of retrying blindly, and nothing is sent meanwhile.
function describeServerFailure(e){
  const limits=server?.manifest?.publicPreview??{},tail='Previous results remain; no fallback search was run.';
  const perMinute=Number.isInteger(limits.searchesPerMinute)?limits.searchesPerMinute:null,perHour=Number.isInteger(limits.searchesPerProcessHour)?limits.searchesPerProcessHour:null,maxChars=publicCharacterLimit(server?.manifest);
  if(Number.isInteger(e.status)){
    if(e.status===429)return{text:`Server search failed: search limit reached${perMinute?` (anonymous previews allow ${perMinute} searches per minute${perHour?` and ${perHour} per hour`:''})`:''}. ${tail} Try again in ${e.retryAfter?`${e.retryAfter} s`:'a minute'}.`,unavailable:false};
    if(e.status===400)return{text:`Server search failed: the server refused this description (${e.message})${maxChars?`; it accepts at most ${maxChars.toLocaleString()} characters`:''}. ${tail} Shorten it and try again.`,unavailable:false};
    if(e.status===409)return{text:`Server search failed: this page no longer matches the server release (${e.message}). ${tail} Reload the page to continue.`,unavailable:true};
    if(e.status===499)return{text:`Server search failed: the server reported this search as cancelled. ${tail} Submit it again.`,unavailable:false};
    if(e.status>=500)return{text:`Server search failed: server error (${e.status}: ${e.message}). ${tail} Try again later.`,unavailable:true};
    return{text:`Server search failed: the server refused this request (${e.status}: ${e.message}). ${tail}`,unavailable:false};
  }
  if(e.name==='TypeError')return{text:`Server search failed: server unreachable (${e.message}). ${tail} Check the connection and submit again; availability is re-checked first.`,unavailable:true};
  return{text:`Server search failed: ${e.message}. ${tail} Try again later.`,unavailable:false};
}
function markServerUnavailable(){
  server=null;serverState='unavailable';
  $('#server-detail').textContent='The server did not complete the last search. Nothing else was sent. Check availability again before searching, or choose recorded examples or on-device search.';
  updateEngineLabel();
}
async function connectServer(){
  const generation=++connectionGeneration;
  server?.cancel();server=null;serverState='checking';updateEngineLabel();
  try{
    const config=await loadDeploymentConfig({pageOrigin:location.origin});
    if(generation!==connectionGeneration||!pageActive)return;
    serverConfig=config;
    if(!config.enabled){serverState='unavailable';$('#server-detail').textContent='This deployment has not enabled a ready server. This availability check sent no description. Try again later, or choose recorded examples or on-device search.';updateServerReadiness();return;}
    const candidate=new ServerSearch({endpoint:config.endpoint,pageOrigin:location.origin,expected:{catalogId:manifest.catalogId,graphId:manifest.graphId,indexSha256:manifest.indexSha256,catalogSha256:manifest.files.catalog.sha256,vectorsSha256:manifest.vectorsSha256,allowedEngineIds:manifest.allowedQueryProfiles.filter(p=>p.kind==='server-live').map(p=>p.id)}});
    await candidate.connect();
    if(generation!==connectionGeneration||!pageActive){candidate.cancel();return;}
    server=candidate;serverState='ready';
    const limits=candidate.manifest.publicPreview,perMinute=Number.isInteger(limits?.searchesPerMinute)?limits.searchesPerMinute:null,perHour=Number.isInteger(limits?.searchesPerProcessHour)?limits.searchesPerProcessHour:null,maxChars=publicCharacterLimit(candidate.manifest);
    const allowance=perMinute?` Anonymous previews allow ${perMinute} searches per minute${perHour?` and ${perHour} per hour`:''}${maxChars?`, up to ${maxChars.toLocaleString()} characters each`:''}.`:'';
    $('#server-detail').textContent=`${config.privacySummary} Recipient: ${config.recipient}. Only descriptions deliberately submitted with Find music are sent. Checking readiness never sends the input. Existing results keep their original source label.${allowance}`;
  }catch{
    if(generation!==connectionGeneration||!pageActive)return;
    server=null;serverConfig=SERVER_CONFIG;serverState='unavailable';
    $('#server-detail').textContent='This deployment has not confirmed a ready, matching server. Nothing was sent. Try again later, or choose recorded examples or on-device search.';
  }
  updateServerReadiness();
}
function updateServerReadiness(){
  updateEngineLabel();
  if(readinessNoticeGeneration===queryGeneration&&engineSelection==='server'){readinessNoticeGeneration=null;status(serverState==='ready'?'Server ready. Nothing was sent; submit your description to search.':'Server search is unavailable. Nothing was sent. Try again later or use search settings.');}
}

function nearby(row){const returnFocus=!!captureControl('#results')||!!captureControl('#node-inspector');queryGeneration++;server?.cancel();$('#query-kind').value='description';map.pause();const t=catalog.tracks[row],q=vectors.slice(row*512,(row+1)*512);const ranked=exactSearch(vectors,q,16,{excludeId:row}).map(r=>({row:r.id,score:1-r.distance}));const found=index.search(q,{k:17,ef:32,trace:true,spaceId:manifest.graphId});showResult({text:`More like ${t.title} by ${t.artist}`,ranked,trace:found.trace,label:'Audio neighbors · selected recording',encoderSpaceId:manifest.graphId,sourceRankingId:'audio-'+t.id,timing:`Compared the selected recording with the same ${catalog.tracks.length.toLocaleString()} audio vectors.`});setSearchBusy(false);updateEngineLabel();if(returnFocus)$('#results .track-select')?.focus({preventScroll:true});}
function updateShelf(){if(exportUrl){URL.revokeObjectURL(exportUrl);exportUrl=null;}$('#export-content').hidden=true;$('#export-download').hidden=true;const wasShelfControl=!!document.activeElement?.closest('#shelf');$('#shelf-count').textContent=kept.length;$('#undo').disabled=!undo.length;$('#export').disabled=!kept.length;$('#shelf').innerHTML=kept.length?kept.map(row=>{const t=catalog.tracks[row];return`<div><span>${esc(t.title)}<small>${esc(t.artist)}</small></span>${playButton(row)}<button data-keep="${row}">Remove</button></div>`;}).join(''):'<p>No tracks kept yet.</p>';renderResults();if(selected!==null)choose(selected);if(wasShelfControl)($('#shelf button')??$('#undo')).focus();}
function keep(row){undo.push([...kept]);if(undo.length>30)undo.shift();kept=kept.includes(row)?kept.filter(x=>x!==row):[...kept,row];updateShelf();}
async function play(row){const approved=preview(row);if(!approved.available){status(UNAVAILABLE_PREVIEW);return;}const audio=$('#audio'),t=catalog.tracks[row];if(playing===row&&!audio.paused){audio.pause();return;}const generation=++playbackGeneration;if(playing!==row){audio.pause();audio.src=approved.url;playing=row;$('#playing-title').textContent=t.title;$('#playing-artist').textContent=t.artist+' · audio preview';$('#playing-credit').href='/notices/track-attribution.html#'+t.id.replace(':','-');}audio.controls=true;$('#player-toggle').disabled=false;$('#player').hidden=false;choose(row,{explicit:true});try{await audio.play();}catch(e){if(generation===playbackGeneration&&e.name!=='AbortError')status('Playback could not start: '+e.message);}}
for(const name of ['play','pause','ended'])$('#audio').addEventListener(name,()=>{const audio=$('#audio'),approved=preview(playing);if(name==='play'&&(!approved.available||audio.src!==approved.url)){resetPlayer();status(UNAVAILABLE_PREVIEW);renderResults();return;}renderResults();$('#player-toggle').disabled=!approved.available;$('#player-toggle').textContent=approved.available?(audio.paused?'Play':'Pause'):UNAVAILABLE_PREVIEW;$('#player-toggle').setAttribute('aria-label',approved.available?(audio.paused?'Play audio':'Pause audio'):UNAVAILABLE_PREVIEW);});
$('#audio').addEventListener('error',()=>{if(playing===null)return;audioDelivery.delete(catalog.tracks[playing].id);resetPlayer();refreshPlaybackAvailability();updateShelf();status(UNAVAILABLE_PREVIEW+'. This approved excerpt could not load.');});

$('#browse-collection').onclick=browseCollection;$('#empty-browse').onclick=()=>{$('#refine-text').value='';$('#genre-filter').value='';$('#preview-only').checked=false;browseCollection();$('#results-heading').focus({preventScroll:true});};
$('#clear-refinements').onclick=()=>{clearRefinements();$('#refine-text').focus();};
$('#empty-clear').onclick=()=>{clearRefinements();$('#refine-text').focus();};
for(const [id,event] of [['refine-text','input'],['genre-filter','change'],['preview-only','change']])$('#'+id).addEventListener(event,()=>{pageIndex=0;applyDisplayPolicy({preserveSelection:true});});
$('#previous-page').onclick=()=>changePage(-1);$('#next-page').onclick=()=>changePage(1);
$('#cancel-search').onclick=()=>{queryGeneration++;server?.cancel();const local=engineSelection==='local'&&inflight;if(local){modelGeneration++;encoder.cancel('Search cancelled');inflight=null;}setSearchBusy(false);map?.pause();updateEngineLabel();status(local?'On-device search cancelled and model unloaded. Previous results remain; enable the model again in settings to search.':'Search cancelled. Previous results remain.');$('#query').focus();};
$('#map-panel').addEventListener('toggle',()=>{if(!map)return;if($('#map-panel').open)map.draw();else map.pause();});
$('#query-form').addEventListener('submit',e=>{e.preventDefault();void runQuery($('#query').value);});
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.example){exampleSearch(b.dataset.example);}else if(b.dataset.select!==undefined)choose(Number(b.dataset.select),{explicit:true});else if(b.dataset.play!==undefined)void play(Number(b.dataset.play));else if(b.dataset.keep!==undefined)keep(Number(b.dataset.keep));else if(b.dataset.nearby!==undefined)nearby(Number(b.dataset.nearby));});
$('#use-server').onclick=async()=>{
  queryGeneration++;server?.cancel();setSearchBusy(false);
  if(serverState!=='ready'){await connectServer();return;}
  engineSelection=engineSelection==='server'?'recorded':'server';
  status(engineSelection==='server'?'Server search selected. Submit a description with Find music to send it to this server.':'Server search paused. New descriptions will not be sent.');
  updateEngineLabel();$('#engine-dialog').close();
};
$('#query-kind').addEventListener('change',()=>{queryGeneration++;server?.cancel();setSearchBusy(false);updateEngineLabel();});
$('#open-engine').onclick=()=>$('#engine-dialog').showModal();$('#open-about').onclick=()=>$('#about-dialog').showModal();$('#open-shelf').onclick=()=>$('#shelf-dialog').showModal();
$('#enable-local').onclick=async()=>{
  engineSelection='local';queryGeneration++;server?.cancel();setSearchBusy(false);updateEngineLabel();
  if(['ready','encoding'].includes(encoder.state)){status('On-device search selected. New descriptions stay in this browser.');$('#engine-dialog').close();return;}
  const generation=++modelGeneration;
  try{await encoder.prepare();if(generation!==modelGeneration||!pageActive||engineSelection!=='local')return;status('On-device search is ready. Search a description when you want.');}
  catch(e){if(generation===modelGeneration&&pageActive&&engineSelection==='local'&&e.name!=='AbortError')status(e.message);}
};
$('#unload-local').onclick=()=>{if(engineSelection==='local')queryGeneration++;modelGeneration++;encoder.cancel('Stopped by user');inflight=null;setSearchBusy(false);updateEngineLabel();status('Model unloaded. Existing results keep their original search label.');};
$('#spread-results').onclick=()=>{spreadArtists=!spreadArtists;pageIndex=0;applyDisplayPolicy({preserveSelection:true});};
$('#trace-play').onclick=()=>{if(map.playing)map.pause();else map.replay();};$('#trace-skip').onclick=()=>{map.finish();$('#trace-play').focus({preventScroll:true});};$('#zoom-in').onclick=()=>map.zoom(1.2);$('#zoom-out').onclick=()=>map.zoom(1/1.2);$('#fit').onclick=()=>map.fit();$('#overview').onclick=()=>map.fit('all');$('#focus-track').onclick=()=>map.fit('selected');
$('#player-toggle').onclick=()=>{if(playing!==null)void play(playing);};$('#close-player').onclick=()=>{resetPlayer();renderResults();};$('#undo').onclick=()=>{if(undo.length){kept=undo.pop();updateShelf();}};
$('#export').onclick=()=>{if(exportUrl)URL.revokeObjectURL(exportUrl);const data={schemaVersion:1,catalogId:catalog.id,query:currentQuery,queryReview,view:{channel:resultChannel,refinements:refinements(),page:pageIndex+1,matchingCandidates:viewPage.total,candidateCount:candidateRows.length},rankingContext:rankingPacket?{policy:'artist-spread-v1',mode:spreadArtists?'more-artists':'closest',sourceRankingId:rankingPacket.sourceRankingId,encoderSpaceId:rankingPacket.encoderSpaceId,artistMetadataSha256:rankingPacket.artistMetadataSha256,candidateCount:rankingPacket.candidates.length,displayed:rows.map((r,i)=>({trackId:catalog.tracks[r.row].id,displayRank:r.displayRank,sourceRank:r.sourceRank,rawSimilarity:r.score}))}:{channel:candidateRows.some(r=>Number.isFinite(r.score))?'sound':'title-artist'},tracks:kept.map(row=>catalog.tracks[row])};const value=JSON.stringify(data,null,2);$('#export-content').value=value;$('#export-content').hidden=false;exportUrl=URL.createObjectURL(new Blob([value],{type:'application/json'}));$('#export-download').href=exportUrl;$('#export-download').download='music-discovery-kept.json';$('#export-download').hidden=false;$('#export-content').focus();};
async function init(){try{const manifestResponse=await fetch(new URL('../data/manifest.json',import.meta.url));if(!manifestResponse.ok)throw new Error('Collection manifest unavailable');const manifestBytes=await manifestResponse.arrayBuffer();if(await sha(manifestBytes)!==MANIFEST_SHA)throw new Error('Collection release identity mismatch');manifest=JSON.parse(new TextDecoder().decode(manifestBytes));[catalog,vectors,examples]=await Promise.all([verified('catalog'),verified('vectors'),verified('examples')]);audioDelivery=await loadAudioDelivery({catalog,catalogSha256:manifest.files.catalog.sha256,pageOrigin:location.origin});const[graph,layout]=await Promise.all([verified('index'),verified('layout')]);if(catalog.id!==manifest.catalogId||catalog.id!==RELEASE.catalogId||graph.spaceId!==manifest.graphId||layout.positions.length!==catalog.tracks.length)throw new Error('Catalog/index identity mismatch');try{const artistResponse=await fetch(new URL('../data/artist-records.json',import.meta.url));if(!artistResponse.ok)throw new Error('Artist metadata unavailable');const artistBytes=await artistResponse.arrayBuffer();if(await sha(artistBytes)!==ARTIST_METADATA_SHA)throw new Error('Artist metadata identity mismatch');const artistData=JSON.parse(new TextDecoder().decode(artistBytes));if(artistData.catalogId!==catalog.id||artistData.rows.length!==catalog.tracks.length||artistData.rows.some((r,i)=>r.trackId!==catalog.tracks[i].id||typeof r.artistId!=='string'))throw new Error('Artist order mismatch');artistRecords=new Map(artistData.rows.map(r=>[r.trackId,r.artistId]));}catch{artistRecords=null;}index=HNSW.load(graph,vectors);$('#catalog-count').textContent=catalog.tracks.length.toLocaleString()+' recordings · sound and catalog search';$('#about-count').textContent=catalog.tracks.length.toLocaleString()+' recordings';$('#map-panel').open=!globalThis.matchMedia?.('(max-width: 760px)').matches;const css=getComputedStyle(document.body),colors=Object.fromEntries(['paper','ink','muted','rule','node','visited','frontier','result'].map(key=>[key,css.getPropertyValue('--'+key).trim()]));map=new AudioMap($('#map'),{positions:layout.positions,tracks:catalog.tracks,connections:indexConnections(graph.links),colors,onDensity:s=>{$('#map-density').textContent=`${s.visible} / ${catalog.tracks.length.toLocaleString()} points shown · ${s.edges?`${s.edges} stored connections`:'zoom in for connections'}`;},onSelect:row=>choose(row,{scroll:true,explicit:true}),onHover:(id,e)=>{const tip=$('#tooltip');tip.hidden=id===null;if(id!==null){const t=catalog.tracks[id],r=$('#map').getBoundingClientRect();tip.innerHTML=`<strong>${esc(t.title)}</strong><br>${esc(t.artist)}`;tip.style.left=Math.min(r.width-240,Math.max(8,e.clientX-r.left+15))+'px';tip.style.top=Math.max(48,e.clientY-r.top-50)+'px';}},onTrace:s=>{$('#trace-play').disabled=!s.total;$('#trace-skip').disabled=!s.total||s.completed;$('#trace-skip').hidden=!s.total||s.completed;$('#trace-play').textContent=s.playing?'Pause':!s.completed?'Continue':'Watch again';$('#trace-fill').style.width=(s.total?s.progress*100:100)+'%';$('#trace-note').textContent=!s.total?'':s.reducedMotion?'Motion reduced':!s.completed?(s.phase==='entry'?'Starting':s.phase==='descent'?'Narrowing':s.phase==='results'?'Matches':'Exploring'):'Ready';}});$('#examples').innerHTML=examples.examples.map(e=>`<button data-example="${esc(e.id)}" aria-pressed="false">${esc(e.label)}</button>`).join('');for(const b of document.querySelectorAll('[data-needs-catalog]'))b.disabled=false;$('#layout-method').textContent=layout.description??'The fixed layout is fitted to the same normalized 512-dimensional audio vectors. See the exact manifest for parameters and limitations.';exampleSearch(examples.examples[0].id,{animate:false});updateShelf();refreshPlaybackAvailability();document.body.dataset.ready='true';await connectServer();}catch(e){status(e.message);$('#search').disabled=true;$('#enable-local').disabled=true;}}
window.addEventListener('pagehide',event=>{pageActive=false;pageGeneration++;queryGeneration++;modelGeneration++;connectionGeneration++;server?.cancel();server=null;serverState='checking';encoder.cancel('Page closed');inflight=null;setSearchBusy(false);resetPlayer();audioDelivery=new Map();if(event.persisted)map?.pause();else map?.destroy();$('#audio').pause();if(exportUrl){URL.revokeObjectURL(exportUrl);exportUrl=null;$('#export-download').hidden=true;}});
window.addEventListener('pageshow',async event=>{
  if(!event.persisted)return;pageActive=true;map?.draw();if(!catalog||!manifest)return;
  const generation=++pageGeneration,queryAtReturn=queryGeneration;
  await connectServer();
  if(!pageActive||generation!==pageGeneration)return;
  const delivery=await loadAudioDelivery({catalog,catalogSha256:manifest.files.catalog.sha256,pageOrigin:location.origin});
  if(!pageActive||generation!==pageGeneration)return;
  audioDelivery=delivery;updateShelf();refreshPlaybackAvailability();
  if(queryAtReturn===queryGeneration)status(engineSelection==='server'?(serverState==='ready'?'Server ready. Submit a description to search. Previous results keep their source label.':'Server search is unavailable. Previous results remain; nothing was sent.'):engineSelection==='local'?'On-device search remains selected. Enable the model again to search. Previous results remain.':'Live search remains paused. Previous results remain.');
});await init();
