const {test}=require('node:test');
const assert=require('node:assert/strict');
const T=require('../theme-model.js');

test('settings imports clamp extreme sizes and reject CSS injection',()=>{
  const s=T.sanitize({uiScale:100,cardScale:-5,radius:999,blur:NaN,accent:'url(https://example.com)',palettes:{light:{surface:'red; display:none'}}});
  assert.equal(s.uiScale,1.4);assert.equal(s.cardScale,.8);assert.equal(s.radius,26);assert.equal(s.blur,18);
  assert.equal(s.accent,'#d96c75');assert.equal(s.palettes.light.surface,'#ffffff');assert.equal(Object.hasOwn(s,'wallpaper'),false);
});
test('light and dark customization survive independent serialization',()=>{
  const s=T.defaults();s.palettes.light.surface='#12345680';s.blocks.light.daily={...T.blockDefaults(),enabled:true,background:'#abcdef',scale:1.2};
  const round=T.sanitize(JSON.parse(JSON.stringify(s)));
  assert.equal(round.palettes.light.surface,'#12345680');assert.equal(round.palettes.dark.surface,'#1c1c1e');
  assert.equal(round.blocks.light.daily.scale,1.2);assert.equal(round.blocks.dark.daily,undefined);
});
test('UI text and card text are independent without multiplying twice',()=>{
  const s=T.defaults();s.uiScale=1.4;s.cardScale=.8;
  assert.equal(T.blockTokens(s,'light','sidebar').scale,1.4);
  assert.equal(T.blockTokens(s,'light','recent').scale,.8);
  s.blocks.light.recent={...T.blockDefaults(),enabled:true,scale:1.2};
  assert.equal(T.blockTokens(s,'light','recent').scale,.96);
});
test('customizing one block does not change another block or the global palette',()=>{
  const s=T.defaults(),before=T.clone(s.palettes);s.mode='light';
  s.blocks.light.progress={...T.blockDefaults(),enabled:true,background:'#112233',textMode:'auto',accent:'#abcdef',radius:24};
  const custom=T.blockTokens(s,'light','progress'),other=T.blockTokens(s,'light','daily');
  assert.equal(custom.text,'#ffffff');assert.equal(custom.radius,24);assert.equal(custom.accent,'#abcdef');
  assert.equal(other.radius,12);assert.equal(other.accent,'#f0605e');assert.deepEqual(s.palettes,before);
});
test('auto text picks a legible foreground on actual composited backgrounds',()=>{
  for(const bg of ['#ffffff','#000000','#112233','#ffcc00','#007aff','#f0605e'])assert.ok(T.contrast(bg,T.autoText(bg))>=4.5);
  assert.equal(T.composite('#00000080','#ffffff'),'#7f7f7f');
});
test('glass off makes global and per-block surfaces opaque',()=>{
  const s=T.defaults();s.opacity=.1;s.glass=false;s.blocks.light.daily={...T.blockDefaults(),enabled:true,background:'#336699',opacity:.1};
  assert.equal(T.blockTokens(s,'light','daily').background,'rgba(51, 102, 153, 1)');
});
test('original, system, light, dark and custom modes remain distinct',()=>{
  const s=T.defaults();assert.equal(s.mode,'custom');assert.equal(s.accent,'#d96c75');s.mode='original';assert.equal(T.modeOf(s,true),'light');
  s.mode='system';assert.equal(T.modeOf(s,true),'dark');s.mode='light';assert.equal(T.modeOf(s,true),'light');
  s.mode='custom';s.customDark=true;assert.equal(T.modeOf(s,false),'dark');
});
test('accent-derived theme preserves manually customized semantic roles',()=>{
  const s=T.defaults();s.mode='custom';s.accent='#007aff';s.palettes.light.surface='#ffeedd';
  const p=T.paletteOf(s,'light');assert.equal(p.surface,'#ffeedd');assert.equal(p.accent,'#007aff');assert.notEqual(p.pageBackground,T.palettes.light.pageBackground);
});
test('an explicitly chosen default color remains an override in a derived theme',()=>{
  const s=T.defaults();s.mode='custom';s.accent='#007aff';s.paletteOverrides.light=['surface'];
  assert.equal(T.paletteOf(s,'light').surface,'#ffffff');
  assert.notEqual(T.paletteOf(s,'light').pageBackground,T.palettes.light.pageBackground);
  const round=T.sanitize(JSON.parse(JSON.stringify(s)));assert.equal(T.paletteOf(round,'light').surface,'#ffffff');
});
test('invalid block IDs, prototype keys and arbitrary file fields are not imported',()=>{
  const input=JSON.parse('{"__proto__":{"x":1},"blocks":{"light":{"madeUp":{"enabled":true},"__proto__":{"enabled":true}}},"secret":"oops","wallpaper":{"data":"javascript:alert(1)"}}');
  const s=T.sanitize(input);assert.equal(s.blocks.light.madeUp,undefined);assert.equal(s.secret,undefined);assert.equal(s.wallpaper,undefined);assert.equal({}.x,undefined);
});
test('resetting one mode and one block preserves other custom choices',()=>{
  const s=T.defaults();s.blocks.light.daily={...T.blockDefaults(),enabled:true,background:'#123456'};s.blocks.dark.daily={...T.blockDefaults(),enabled:true,background:'#654321'};
  delete s.blocks.light.daily;const round=T.sanitize(s);assert.equal(round.blocks.light.daily,undefined);assert.equal(round.blocks.dark.daily.background,'#654321');
});
