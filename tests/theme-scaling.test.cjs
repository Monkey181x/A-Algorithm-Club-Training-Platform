const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture(inline = '') {
  let value=inline, priority=inline?'important':'', target=parseFloat(inline)||12;
  let rendered=target, previousTarget=target, animating=false, colorCanceled=0;
  const flush=()=>{target=parseFloat(value)||12;if(target!==previousTarget){animating=true;previousTarget=target;}};
  const el={
    isConnected:true, namespaceURI:'http://www.w3.org/1999/xhtml',
    childNodes:[{nodeType:3,textContent:'872 RBC'}], closest:()=>null, matches:()=>false,
    style:{
      getPropertyValue:()=>value,getPropertyPriority:()=>priority,
      setProperty(_key,v,p){value=v;priority=p;}, removeProperty(){value='';priority='';},
    },
    getAnimations(){
      flush();
      return [{transitionProperty:'color',cancel(){colorCanceled++;}},
        ...(animating?[{transitionProperty:'font-size',cancel(){animating=false;rendered=target;}}]:[])];
    },
  };
  const source=fs.readFileSync(require.resolve('../theme-settings.js'),'utf8');
  const start=source.indexOf('  function cancelFontTransitions(');
  const end=source.indexOf('\n  function apply()',start);
  const settings={uiScale:1.2,cardScale:1,blocks:{light:{}}};
  const context={settings,fontStyles:new Map(),mode:()=> 'light',
    document:{querySelectorAll:()=>[el]},
    getComputedStyle(){flush();return {fontSize:(animating?rendered:target)+'px'};},
  };
  vm.createContext(context); vm.runInContext(source.slice(start,end),context);
  const apply=()=>vm.runInContext('scaleText()',context);
  return {apply,settings,el,styles:context.fontStyles,
    size:()=>parseFloat(value)||12,priority:()=>priority,colorCanceled:()=>colorCanceled};
}

test('repeated theme application with font transitions never compounds the scale',()=>{
  const f=fixture();
  for(let i=0;i<100;i++){f.apply();assert.ok(Math.abs(f.size()-14.4)<.001);}
  assert.equal(f.colorCanceled(),0);
});

test('changing and resetting the scale uses the original size and inline priority',()=>{
  const f=fixture('18px');f.apply();assert.ok(Math.abs(f.size()-21.6)<.001);
  f.settings.uiScale=.8;f.apply();assert.equal(f.size(),14.4);
  f.settings.uiScale=1;f.apply();assert.equal(f.size(),18);assert.equal(f.priority(),'important');
});

test('returning to default removes injected font styles and releases detached elements',()=>{
  const f=fixture();f.apply();f.settings.uiScale=1;f.apply();
  assert.equal(f.el.style.getPropertyValue('font-size'),'');
  f.el.isConnected=false;f.apply();assert.equal(f.styles.size,0);
});
