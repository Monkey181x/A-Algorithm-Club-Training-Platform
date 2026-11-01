const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function fixture({confirmed=true,request}={}) {
  const calls=[],messages=[];let tokens=0;
  const source=fs.readFileSync(require.resolve('../currency.js'),'utf8');
  const start=source.indexOf('  const consumableUsesInFlight');
  const end=source.indexOf('  let shopRenderVersion',start);
  const context={window:{clubDialog:{confirm:async()=>confirmed},clubApi:{
    requestId:()=>`token-${++tokens}`,
    request:async(url,options)=>{calls.push({url,options});return request?request(calls.length):{ok:true,json:async()=>({status:'dispatching'})};},
  }},showToast:message=>messages.push(message)};
  vm.createContext(context);vm.runInContext(source.slice(start,end),context);
  return {use:id=>vm.runInContext(`useConsumable(${id})`,context),calls,messages};
}

test('canceling use does not send a request or consume an item',async()=>{
  const f=fixture({confirmed:false});assert.equal(await f.use(12),false);assert.equal(f.calls.length,0);
});

test('confirmed use submits one unit with a stable idempotency token',async()=>{
  const f=fixture();assert.equal(await f.use(12),true);
  assert.equal(f.calls[0].url,'/api/currency/inventory/12/use');
  assert.equal(JSON.parse(f.calls[0].options.body).request_id,'token-1');
  assert.match(f.messages[0],/派送中/);
});

test('network retries reuse the same request rather than spending a second item',async()=>{
  const f=fixture({request:async attempt=>{if(attempt===1)throw Error('offline');return {ok:true,json:async()=>({status:'dispatching',duplicate:true})};}});
  assert.equal(await f.use(12),false);assert.equal(await f.use(12),true);
  assert.equal(f.calls[0].options.body,f.calls[1].options.body);
});

test('concurrent clicks cannot submit two use requests',async()=>{
  let release;const f=fixture({request:()=>new Promise(resolve=>{release=resolve;})});
  const first=f.use(12);await Promise.resolve();
  assert.equal(await f.use(12),false);
  release({ok:true,json:async()=>({status:'dispatching'})});
  assert.equal(await first,true);assert.equal(f.calls.length,1);
});
