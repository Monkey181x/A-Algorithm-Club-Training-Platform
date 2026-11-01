const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

function fixture(){
  const requests=[],images=[],revoked=[];
  class Image {
    constructor(){this.isConnected=false;images.push(this);this.decoded=new Promise((yes,no)=>{this.finishDecode=yes;this.failDecode=no;});}
    decode(){return this.decoded;}
    loaded(){this.naturalWidth=100;this.complete=true;this.onload();}
    remove(){this.isConnected=false;this.parentElement=null;}
  }
  const context={window:{},Image,AbortController,setTimeout,clearTimeout,
    URL:{createObjectURL:()=>`blob:${requests.length}`,revokeObjectURL:url=>revoked.push(url)},
    fetch:(url,options)=>new Promise(resolve=>requests.push({url,options,resolve})),
    document:{createElement:()=>({setAttribute(){}})}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require.resolve('../static/shop-images.js'),'utf8'),context);
  function slot(source){
    const classes=new Set(['is-image-loading']);
    return {dataset:{shopImage:source,imageAlt:'商品'},isConnected:true,children:[],classes,
      classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)},removeAttribute(){},
      querySelector(){return this.children.find(c=>c.className==='shop-product-image'&&c.parentElement===this);},
      append(node){this.children.push(node);node.parentElement=this;node.isConnected=this.isConnected;}};
  }
  const mount=(...slots)=>context.window.ShopImages.mount({querySelectorAll:()=>slots});
  const detach=slot=>{slot.isConnected=false;slot.children.forEach(node=>{node.isConnected=false;node.parentElement=null;});};
  async function fetchImage(index=0){requests[index].resolve({ok:true,blob:async()=>({})});await tick();}
  async function ready(image=images[0]){image.loaded();await tick();image.finishDecode();await tick();}
  return {slot,mount,detach,requests,images,fetchImage,ready,revoked};
}

test('images remain covered until both download and decoding complete',async()=>{
  const f=fixture(),slot=f.slot('/icon?v=1');f.mount(slot);
  assert.equal(slot.classes.has('is-image-ready'),false);
  await f.fetchImage();f.images[0].loaded();await tick();
  assert.equal(slot.classes.has('is-image-ready'),false);
  f.images[0].finishDecode();await tick();
  assert.equal(slot.classes.has('is-image-ready'),true);
});

test('duplicate URLs share one request and all different images start concurrently',async()=>{
  const f=fixture(),a=f.slot('/a'),b=f.slot('/a'),c=f.slot('/b');f.mount(a,b,c);
  assert.equal(f.requests.length,2);assert.equal(f.images.length,3);
  await f.fetchImage(0);await f.fetchImage(1);
  for(const image of f.images)await f.ready(image);
  assert.equal(f.images[0].src,f.images[1].src);
});

test('reentering uses the actual decoded node immediately without refetch or fade',async()=>{
  const f=fixture(),first=f.slot('/icon');f.mount(first);await f.fetchImage();await f.ready();
  f.detach(first);const next=f.slot('/icon');f.mount(next);
  assert.equal(f.requests.length,1);assert.equal(f.images.length,1);
  assert.equal(next.classes.has('is-image-cached'),true);
  assert.equal(next.classes.has('is-image-ready'),true);
  f.mount(next);assert.equal(f.images.length,1);
});

test('a fast switch reuses pending work and cannot reveal the obsolete slot',async()=>{
  const f=fixture(),first=f.slot('/icon');f.mount(first);f.detach(first);
  const next=f.slot('/icon');f.mount(next);await f.fetchImage();await f.ready();
  assert.equal(f.requests.length,1);
  assert.equal(first.classes.has('is-image-ready'),false);
  assert.equal(next.classes.has('is-image-ready'),true);
});

test('failure shows a fallback and the next visit can retry',async()=>{
  const f=fixture(),first=f.slot('/icon');f.mount(first);
  f.requests[0].resolve({ok:false});await tick();
  assert.equal(first.classes.has('is-image-error'),true);
  assert.equal(first.children.at(-1).textContent,'图片暂不可用');
  f.detach(first);const next=f.slot('/icon');f.mount(next);
  assert.equal(f.requests.length,2);await f.fetchImage(1);await f.ready(f.images[1]);
  assert.equal(next.classes.has('is-image-ready'),true);
});

test('a new image version loads fresh content without invalidating other cached images',async()=>{
  const f=fixture(),first=f.slot('/icon?v=1');f.mount(first);await f.fetchImage();await f.ready();
  f.detach(first);f.mount(f.slot('/icon?v=2'));assert.equal(f.requests.length,2);
  await f.fetchImage(1);await f.ready(f.images[1]);
});

test('decoding failure never exposes a partly decoded image',async()=>{
  const f=fixture(),slot=f.slot('/bad');f.mount(slot);await f.fetchImage();
  f.images[0].loaded();f.images[0].failDecode(Error('bad decode'));await tick();
  assert.equal(slot.classes.has('is-image-ready'),false);
  assert.equal(slot.classes.has('is-image-error'),true);
  assert.equal(f.revoked.length,1);
});

test('the shop paints its skeleton before metadata and does not wait for inventory',async()=>{
  const source=fs.readFileSync(require.resolve('../currency.js'),'utf8');
  const calls=[];let finish;
  const container={dataset:{shopTab:'shop'},innerHTML:'',querySelector:selector=>selector==='.shop-tabs'?{querySelectorAll:()=>[]}:null,querySelectorAll:()=>[]};
  const context={window:{clubApi:{request:url=>{calls.push(url);return new Promise(resolve=>finish=resolve);}},ShopImages:{mount(){}}},
    currencyData:{balance:0},escape:String,showToast(){}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  async function loadShopItems('),source.indexOf('  // Public API')),context);
  const rendered=vm.runInContext('renderShopView(container)',vm.createContext({...context,container}));
  assert.match(container.innerHTML,/shop-card-placeholder/);
  assert.deepEqual(calls,['/api/currency/shop/items']);
  finish({ok:true,json:async()=>({items:[{id:1,name:'First',description:'',price:1,stock:-1}]})});
  await rendered;assert.match(container.innerHTML,/First/);assert.doesNotMatch(container.innerHTML,/shop-card-placeholder/);
});
