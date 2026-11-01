const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../app.js'),'utf8');

test('a refreshed historical day replaces cached completed flags and reopens the button',()=>{
  const old={date:'2026-10-23',submitted:true,hasSubmission:true,isMakeup:true,canSubmit:false};
  const current={...old,submitted:false,hasSubmission:false,isMakeup:false,makeupOpen:true,canSubmit:true};
  const entry={classList:{toggle(){}}};
  const context={pulseDays:[old],pulseToday:'2026-11-01',adminTestMode:false,entry,current};
  vm.createContext(context);
  const merge=source.slice(source.indexOf('function updatePulseDay('),source.indexOf('\nfunction renderActivityDay'));
  const button=source.slice(source.indexOf('function updateSubmissionEntry('),source.indexOf('\nfunction selectPulseDay'));
  vm.runInContext(merge+button+'\nupdatePulseDay(current);updateSubmissionEntry(entry,pulseDays[0],current.date);',context);
  assert.match(entry.innerHTML,/补交当日记录/);assert.equal(entry.disabled,false);
  context.current={...current,submitted:true,hasSubmission:true,isMakeup:true,canSubmit:false};
  vm.runInContext('updatePulseDay(current);updateSubmissionEntry(entry,pulseDays[0],current.date);',context);
  assert.match(entry.innerHTML,/当日已补签/);assert.equal(entry.disabled,true);
});

test('today returns to its first-submission action after the final submission disappears',()=>{
  const entry={classList:{toggle(){}}};
  const context={entry,day:{submitted:false,hasSubmission:false,canSubmit:true},pulseToday:'2026-11-01',adminTestMode:false};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function updateSubmissionEntry('),source.indexOf('\nfunction selectPulseDay'))+"\nupdateSubmissionEntry(entry,day,pulseToday);",context);
  assert.match(entry.innerHTML,/提交今日记录/);assert.equal(entry.disabled,false);
});

test('a leaderboard request started before deletion cannot overwrite the new solved total',async()=>{
  const pending=[],data={},inflight=new Map();
  const context={rankingData:data,leaderboardInFlight:inflight,rankingKey:(a,b)=>a+':'+b,
    rankingSelection:()=>({scope:'solved',platform:'codeforces'}),renderLeaderboard(){},
    clubApi:{request:()=>new Promise(resolve=>pending.push(resolve))}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function loadLeaderboard('),source.indexOf('\nasync function refreshAfterMutation')),context);
  const old=vm.runInContext("loadLeaderboard('solved')",context);
  const fresh=vm.runInContext("loadLeaderboard('solved','codeforces',{force:true})",context);
  pending[1]({ok:true,json:async()=>({rows:[{value:0}]})});await fresh;
  pending[0]({ok:true,json:async()=>({rows:[{value:1}]})});await old;
  assert.equal(data['solved:codeforces'][0].value,0);
});
