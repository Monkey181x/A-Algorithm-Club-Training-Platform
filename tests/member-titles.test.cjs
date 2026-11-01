const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
function render(title){
  const context={window:{}}; vm.createContext(context);
  vm.runInContext(fs.readFileSync(require.resolve('../static/member-titles.js'),'utf8'),context);
  return context.window.MemberTitles.markup(title);
}
test('an absent or cleared title produces no empty badge',()=>{
  assert.equal(render(null),''); assert.equal(render({text:'  '}),'');
});
test('title text is escaped and cannot inject elements',()=>{
  const badge=render({text:'<img onerror=x>',font:'serif',color:'#a86889'});
  assert.match(badge,/&lt;img onerror=x&gt;/); assert.doesNotMatch(badge,/<img/);
  assert.match(badge,/data-title-font="serif"/);
});
test('arbitrary CSS and remote font values are rejected by the renderer',()=>{
  const badge=render({text:'Title',color:'red;position:fixed',font:'url(https://example.com)'});
  assert.doesNotMatch(badge,/position|https|url\(/); assert.match(badge,/--member-title-color:#c96c7a/);
  assert.match(badge,/data-title-font="system"/);
});
test('very light and dark colors are marked for legible theme variants',()=>{
  assert.match(render({text:'Light',color:'#ffffff'}),/data-title-tone="light"/);
  assert.match(render({text:'Dark',color:'#000000'}),/data-title-tone="dark"/);
});

test('multiple titles preserve independent colors and font styles',()=>{
 const context={window:{}};vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../static/member-titles.js'),'utf8'),context);
 const html=context.window.MemberTitles.list([{text:'First',color:'#a86889',font:'serif'},{text:'Second',color:'#559b8a',font:'mono'}]);
 assert.match(html,/#a86889/);assert.match(html,/#559b8a/);assert.match(html,/data-title-font="serif"/);assert.match(html,/data-title-font="mono"/);
 assert.equal(context.window.MemberTitles.list([]),'');
 assert.match(context.window.MemberTitles.cohort({squad_short:'十四分队'},true),/十四分队/);
});

test('squad search accepts names, Chinese labels, squad numbers and admission years',()=>{
 const context={window:{}};vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../static/member-titles.js'),'utf8'),context);
 const row={name:'ColdCumber',cohort:{admission_year:2026,squad_number:14,squad_label:'第十四分队',squad_short:'十四分队'}};
 for(const query of ['十四分队','14','14分队','第14分队','2026级','coldcumber','Cold 十四'])assert.equal(context.window.MemberTitles.matchesMember(row,query),true,query);
 assert.equal(context.window.MemberTitles.matchesMember(row,'十三分队'),false);
 assert.equal(context.window.MemberTitles.matchesMember(row,'15'),false);
 assert.equal(row.cohort.squad_number,14);
 const badge=context.window.MemberTitles.cohort({...row.cohort,label:'2026级 · 第十四分队'});
 assert.match(badge,/第十四分队/);assert.doesNotMatch(badge,/2026级/);
});
