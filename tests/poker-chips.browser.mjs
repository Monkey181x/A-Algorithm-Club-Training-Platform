import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const {chromium} = require('../.local/poker-ui/node_modules/playwright');
const repo = path.resolve('.');
const allowed = new Set(['/tests/fixtures/poker-ui.html', '/tests/fixtures/poker-ui.js', '/poker.js', '/poker.css', '/styles.css', '/extensions.css', '/static/vendor/daisyui/poker-components.css']);
['/theme-model.js','/theme-settings.js','/theme-settings.css'].forEach(file=>allowed.add(file));
const server = createServer((req, res) => {
  const file = new URL(req.url, 'http://localhost').pathname;
  if (!allowed.has(file)) {res.writeHead(404); res.end(); return;}
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css' : 'text/html; charset=utf-8');
  let content = fs.readFileSync(path.join(repo, file), 'utf8');
  if (file.endsWith('poker-ui.html')) content = content.replace('</html>', '<link rel="stylesheet" href="/theme-settings.css"><script src="/theme-model.js"></script><script src="/theme-settings.js"></script></html>');
  if (file.endsWith('poker-ui.js')) content += `
    window.clubApi.request = async url => {
      if (window.showTable && url.endsWith('/fixture')) return {ok:true,json:async()=>({room:{...window.PokerPreview.room,mode:window.showTable},user_id:1})};
      return {ok:true, json:async()=>({current_room:window.showTable?'fixture':null, rooms:[], capacity:{players:0,limit:10,available:10}})};
    };`;
  res.end(content);
});
await new Promise(resolve=>server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({headless:true});
fs.mkdirSync('output/playwright/poker-chips', {recursive:true});
try {
  for (const width of [1280,390]) for (const theme of ['original','light','dark','custom']) {
    const page = await browser.newPage({viewport:{width,height:760}});
    const errors=[];page.on('pageerror', error=>errors.push(error.message));
    await page.addInitScript(theme=>{
      const mode=theme==='custom'?'dark':theme;
      const config={mode,glass:false,animations:false};
      if(theme==='custom') config.blocks={dark:{dialog:{enabled:true,background:'#142a35',accent:'#a9d6e5',textMode:'light'}}};
      localStorage.setItem('rbc-site-theme-v3',JSON.stringify(config));
    },theme);
    await page.goto(`http://127.0.0.1:${server.address().port}/tests/fixtures/poker-ui.html`);
    await page.locator('[data-create=friendly]').waitFor();
    for (const mode of ['friendly','practice']) {
      await page.locator(`[data-create=${mode}]`).click();
      await page.locator('.poker-dialog').waitFor();
      await page.waitForFunction(theme=>theme==='original'||document.querySelector('.poker-dialog')?.dataset.siteThemed==='true',theme);
      const metrics=await page.locator('.poker-dialog').evaluate(el=>{
        const style=getComputedStyle(el),r=el.getBoundingClientRect();
        return {bg:style.backgroundColor,accent:getComputedStyle(el.querySelector('.primary-button')).backgroundColor,
          overflow:el.scrollWidth>el.clientWidth,onscreen:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,
          label:el.getAttribute('aria-labelledby'),bodyBlock:el.querySelector('form').dataset.themeBlock};
      });
      assert.ok(metrics.onscreen,JSON.stringify({width,theme,mode,metrics}));assert.equal(metrics.overflow,false);assert.ok(metrics.label);
      assert.equal(metrics.bodyBlock,undefined);
      if(theme==='light') assert.equal(metrics.bg,'rgb(255, 255, 255)');
      if(theme==='dark') assert.equal(metrics.bg,'rgb(44, 44, 46)');
      if(theme==='custom') {assert.equal(metrics.bg,'rgb(20, 42, 53)');assert.equal(metrics.accent,'rgb(169, 214, 229)');}
      assert.match(await page.locator('.poker-dialog-note').innerText(),/仅限本桌使用/);
      await page.screenshot({path:`output/playwright/poker-chips/dialog-${mode}-${theme}-${width}.png`});
      if(mode==='practice') await page.keyboard.press('Escape');
      else await page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
      await page.locator('.poker-dialog').waitFor({state:'detached'});
    }
    await page.evaluate(()=>{window.showTable='friendly';window.PokerModule.open();});
    await page.locator('[data-leave]').click();
    assert.match(await page.locator('.poker-dialog-note').innerText(),/仅限本桌使用/);
    await page.screenshot({path:`output/playwright/poker-chips/dialog-leave-${theme}-${width}.png`});
    await page.locator('[data-cancel]').click();
    assert.deepEqual(errors,[]);await page.close();
  }
  console.log('Free poker dialogs passed: desktop/mobile, four themes, creation and leave.');
} finally {await browser.close(); server.close();}
