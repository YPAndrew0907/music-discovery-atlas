// This is a conservative notice detector, not a query parser or a filter.
// The unchanged raw description still goes to the paired text encoder.
const LANGUAGES='English|Spanish|French|German|Italian|Portuguese|Japanese|Korean|Chinese|Mandarin|Cantonese|Hindi|Arabic|Russian|Turkish|Hebrew|Persian|Swedish|Norwegian|Danish|Finnish|Polish|Dutch|Greek|Vietnamese|Thai|Indonesian|Swahili|Latin';
const VOCALS=/\b(?:(?:no|without|exclude|excluding)\s+(?:any\s+)?(?:vocals?|singing|lyrics|voices?)|(?:with|include|including)\s+(?:female\s+|male\s+)?(?:vocals?|singing|lyrics)|(?:female|male)\s+(?:vocals?|voices?|singers?|singing)|(?:instrumentals?\s+only|only\s+instrumentals?|instrumental|wordless))\b/gi;
const LANGUAGE=new RegExp(`\\b(?:(?:not?\\s+|without\\s+)?(?:in\\s+(?:${LANGUAGES})|(?:${LANGUAGES})\\s+(?:lyrics|vocals|singing|language|only)|only\\s+(?:${LANGUAGES}))|non[- ]English)\\b`,'gi');
const NUMBER='(?:\\d+(?:\\.\\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|thirty|sixty)';
const DURATION=new RegExp(`\\b(?:(?:under|over|less than|more than|at least|at most|about|around|exactly)\\s+${NUMBER}\\s*[- ]?\\s*(?:minutes?|mins?|seconds?|secs?)|${NUMBER}\\s*[- ]?\\s*(?:minutes?|mins?|seconds?|secs?)\\s+(?:long|tracks?|songs?|recordings?))\\b`,'gi');
const FIELD_LABELS={vocals:'Vocals',language:'Language',duration:'Full-track length'};

export function reviewQueryLimits(raw,{channel='description'}={}){
  if(typeof raw!=='string')throw new TypeError('Description must be a string');
  const requests=[];
  if(channel==='description')for(const[field,pattern]of [['vocals',VOCALS],['language',LANGUAGE],['duration',DURATION]]){
    for(const match of raw.matchAll(new RegExp(pattern.source,pattern.flags))){
      requests.push({field,sourceText:match[0],start:match.index,end:match.index+match[0].length,status:'unverified',applied:false});
    }
  }
  requests.sort((a,b)=>a.start-b.start||a.field.localeCompare(b.field));
  const fields=[...new Set(requests.map(r=>r.field))];
  return {schemaVersion:1,rawQuery:raw,channel,requests,appliedFilters:[],
    summary:fields.length?fields.map(f=>FIELD_LABELS[f]).join(', ')+' unverified. These are sound matches; these requests are not filters.':'',
    durationNote:fields.includes('duration')?'Full-track lengths are unavailable. The playable excerpts are approximately 30 seconds.':''};
}
