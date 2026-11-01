/* Contest catalogs and account AC progress. */
(() => {
  const root = document.getElementById('contestsView');
  if (!root) return;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = {icpc:'ICPC / CCPC',codeforces:'Codeforces',atcoder:'AtCoder',nowcoder:'牛客'};
  let kind = 'icpc', data, generation = 0, timer, active = false, selected = false, page = 1, queryTimer, series = '';
  const calendar = [...root.children].filter(el => !el.classList.contains('page-heading'));
  const mode = document.createElement('div'); mode.className = 'contest-center-modes segmented';
  mode.innerHTML = '<button type="button" class="segment active" data-center-mode="calendar" aria-pressed="true">赛事日程</button><button type="button" class="segment" data-center-mode="tracker" aria-pressed="false">补题 Tracker</button>';
  root.querySelector('.page-heading').after(mode);
  const panel = document.createElement('section'); panel.id = 'trackerPanel'; panel.hidden = true;
  panel.innerHTML = `<div class="tracker-top"><div class="tracker-kind-tabs segmented" role="group" aria-label="Tracker 平台">${Object.entries(names).map(([key,name]) => `<button type="button" class="segment${key===kind?' active':''}" data-tracker-kind="${key}" aria-pressed="${key===kind}">${name}</button>`).join('')}</div><div class="tracker-actions"><button type="button" class="secondary-button" id="trackerRefresh">刷新目录</button><button type="button" class="primary-button" id="trackerSync">同步账号</button><button type="button" class="primary-button" id="trackerClaim" hidden>领取奖励</button></div></div>
    <div class="tracker-summary"><div><span>比赛</span><strong id="trackerContestCount">—</strong></div><div><span>已完成</span><strong id="trackerCompleted">—</strong></div><div><span>已 AC</span><strong id="trackerSolved">—</strong></div></div>
    <section class="panel tracker-browser"><div class="tracker-controls"><label class="tracker-search"><span class="sr-only">搜索比赛或题目</span><input id="trackerSearch" type="search" maxlength="100" placeholder="搜索比赛、题目…"></label><label>账号<select id="trackerAccount"><option value="">全部账号</option></select></label></div><div class="tracker-series-tabs segmented" role="group" aria-label="比赛类别">${['','ICPC','CCPC','省赛'].map(value=>`<button type="button" class="segment${value===''?' active':''}" data-tracker-series="${value}" aria-pressed="${value===''}">${value||'全部'}</button>`).join('')}</div>
    <div class="tracker-status-row"><p id="trackerStatus" role="status"></p><span id="trackerUpdated"></span></div><div id="trackerMatrix" class="tracker-matrix" tabindex="0" aria-label="比赛题目矩阵"></div><div class="tracker-pagination"><span id="trackerResultCount"></span><button type="button" class="secondary-button" id="trackerPrev">上一页</button><span id="trackerPage"></span><button type="button" class="secondary-button" id="trackerNext">下一页</button></div></section>`;
  root.append(panel);
  const el = id => panel.querySelector('#'+id);
  function syncTabs() {
    for (const group of [mode,...panel.querySelectorAll('.segmented')]) {
      if (group.getClientRects().length) window.syncSegmented?.(group);
    }
  }
  const request = async (url,options={}) => {
    const response = await window.clubApi.request(url,options);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || '请求失败，请重试');
    return result;
  };
  function selectMode(next) {
    selected = next === 'tracker'; generation++; clearTimeout(timer);
    for (const item of calendar) item.hidden = selected;
    root.querySelector('#contestRefresh').hidden = selected;
    panel.hidden = !selected;
    for (const button of mode.querySelectorAll('button')) {
      const current = button.dataset.centerMode === next;
      button.classList.toggle('active',current); button.setAttribute('aria-pressed',String(current));
    }
    syncTabs();
    if (selected && active) load();
  }
  mode.addEventListener('click',event => { const button = event.target.closest('[data-center-mode]'); if (button) selectMode(button.dataset.centerMode); });
  function options(id,values,label) {
    const select = el(id), current = select.value;
    const signature = JSON.stringify(values);
    if (select.dataset.signature === signature) return;
    select.innerHTML = `<option value="">${label}</option>` + values.map(value => `<option value="${escape(value.value ?? value)}">${escape(value.label ?? value)}</option>`).join('');
    select.value = current; select.dataset.signature = signature;
  }
  function color(problem) {
    if (problem.tier) return 'tier-'+problem.tier;
    const value = problem.difficulty;
    if (typeof value !== 'number' || !Number.isFinite(value)) return '';
    const limits = kind === 'codeforces' ? [1200,1400,1600,1900,2100,2400] : kind === 'atcoder' ? [400,800,1200,1600,2000,2400,2800] : [1100,1600,2100,2600];
    return 'difficulty-'+limits.filter(n => value>=n).length;
  }
  function render() {
    options('trackerAccount',data.accounts.map(a => ({value:a.id,label:a.account})),'全部账号');
    panel.querySelector('.tracker-series-tabs').hidden = kind !== 'icpc';
    el('trackerContestCount').textContent = data.total.toLocaleString();
    el('trackerCompleted').textContent = data.solved_contests.toLocaleString();
    el('trackerSolved').textContent = `${data.solved_problems.toLocaleString()} / ${data.problem_count.toLocaleString()}`;
    const pendingAccounts = data.accounts.some(a => a.status === 'pending');
    el('trackerClaim').hidden=!data.reward_total;
    el('trackerClaim').textContent=`领取 ${data.reward_total || 0} Coins`;
    el('trackerSync').disabled = !data.accounts.length || pendingAccounts;
    el('trackerSync').textContent = pendingAccounts ? '同步中…' : '同步账号';
    el('trackerRefresh').disabled = data.status === 'pending' || data.details_status === 'pending';
    el('trackerRefresh').textContent = el('trackerRefresh').disabled ? '更新中…' : '刷新目录';
    const failures = data.accounts.filter(a=>a.status==='failed').map(a=>`${a.account}：${a.error || '同步失败，请重试'}`);
    el('trackerStatus').textContent = data.error || failures.join('；') || (data.status==='pending' ? '正在更新目录…' : data.details_status==='pending' ? '正在更新题目…' : '');
    el('trackerUpdated').textContent = data.updated_at ? new Date(data.updated_at).toLocaleString('zh-CN') : '';
    const width = Math.max(1,...data.contests.map(c=>c.problems.length));
    el('trackerMatrix').innerHTML = data.contests.length ? `<table><thead><tr><th scope="col">比赛</th>${Array.from({length:width},(_,i)=>`<th scope="col">${i+1}</th>`).join('')}</tr></thead><tbody>${data.contests.map(c=>`<tr><th scope="row"><a href="${escape(c.url)}" target="_blank" rel="noopener noreferrer">${escape(c.title)}</a><div><span>${escape([c.year,c.category,c.site].filter(Boolean).join(' · '))}</span><strong>${c.solved} / ${c.problems.length} AC</strong></div></th>${c.problems.map(p=>`<td><a class="tracker-problem ${color(p)}${p.accepted?' is-ac':''}" href="${escape(p.url)}" target="_blank" rel="noopener noreferrer" aria-label="${escape(p.label+' '+p.title+(p.accepted?' 已 AC':''))}" title="${escape(p.title+(p.difficulty!=null?' · '+p.difficulty:'')+(p.difficulty_source?' · '+p.difficulty_source:''))}"><strong>${escape(p.label)}</strong><small>${p.accepted?'✓ AC':p.difficulty??({gold:'金',silver:'银',bronze:'铜',iron:'铁'}[p.tier] || '—')}</small></a>${p.reward?`<button type="button" class="tracker-reward" data-reward-key="${escape(p.claim_key)}" aria-label="领取 ${escape(p.label)} 的 ${p.reward} Coins">+${p.reward}</button>`:''}</td>`).join('')}${'<td></td>'.repeat(width-c.problems.length)}</tr>`).join('')}</tbody></table>` : `<div class="tracker-empty">${data.status==='pending'?'正在加载比赛…':data.status==='idle'?'点击“刷新目录”加载比赛':'暂无比赛'}</div>`;
    page = data.page;
    el('trackerResultCount').textContent = `${data.total} 场比赛`;
    el('trackerPage').textContent = `${page} / ${data.pages}`;
    el('trackerPrev').disabled = page<=1;el('trackerNext').disabled = page>=data.pages;
    syncTabs();
  }
  async function load() {
    if (!active || !selected) return;
    clearTimeout(timer);
    const version = ++generation;
    const params = new URLSearchParams({page});
    for (const [key,id] of Object.entries({q:'trackerSearch',account:'trackerAccount'})) if(el(id).value) params.set(key,el(id).value);
    if (kind==='icpc' && series) params.set('category',series);
    try {
      const result = await request(`/api/trackers/${kind}?${params}`);
      if (version!==generation || !selected || !active) return;
      data = result;render();
      if (data.status==='pending' || data.details_status==='pending' || data.accounts.some(a=>a.status==='pending')) timer=setTimeout(load,3000);
    } catch(error) {if(version===generation) el('trackerStatus').textContent=error.message;}
  }
  panel.querySelector('.tracker-kind-tabs').addEventListener('click',event=>{
    const button=event.target.closest('[data-tracker-kind]');if(!button)return;
    kind=button.dataset.trackerKind;page=1;data=null;series='';
    for(const select of panel.querySelectorAll('select')) {select.value='';delete select.dataset.signature;}
    el('trackerSearch').value='';
    panel.querySelectorAll('[data-tracker-series]').forEach(b=>{b.classList.toggle('active',b.dataset.trackerSeries==='');});
    panel.querySelectorAll('[data-tracker-kind]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});
    syncTabs();el('trackerMatrix').innerHTML='<div class="tracker-empty">正在加载…</div>';load();
  });
  panel.querySelector('.tracker-series-tabs').addEventListener('click',event=>{
    const button=event.target.closest('[data-tracker-series]');if(!button)return;
    series=button.dataset.trackerSeries;page=1;
    panel.querySelectorAll('[data-tracker-series]').forEach(b=>b.classList.toggle('active',b===button));
    syncTabs();load();
  });
  for (const select of panel.querySelectorAll('select')) select.addEventListener('change',()=>{page=1;load();});
  el('trackerSearch').addEventListener('input',()=>{clearTimeout(queryTimer);queryTimer=setTimeout(()=>{page=1;load();},250);});
  el('trackerPrev').onclick=()=>{page--;load();};el('trackerNext').onclick=()=>{page++;load();};
  el('trackerRefresh').onclick=async()=>{
    const version=generation,current=kind;el('trackerRefresh').disabled=true;
    try {await request(`/api/trackers/${current}/refresh`,{method:'POST'});if(version===generation)await load();}
    catch(error){if(version===generation){el('trackerStatus').textContent=error.message;el('trackerRefresh').disabled=false;}}
  };
  el('trackerSync').onclick=async()=>{
    const ids=data.accounts.filter(a=>!el('trackerAccount').value || String(a.id)===el('trackerAccount').value).map(a=>a.id);
    const version=generation;el('trackerSync').disabled=true;
    try {await request('/api/profile/platform-accounts/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});if(version===generation)await load();}
    catch(error){if(version===generation){el('trackerStatus').textContent=error.message;el('trackerSync').disabled=false;}}
  };
  async function claim(keys,button){
    const version=generation;button.disabled=true;
    try {
      const result=await request('/api/profile/platform-rewards/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keys,platform:{icpc:'qoj',codeforces:'codeforces',atcoder:'atcoder',nowcoder:'nowcoder'}[kind],...(el('trackerAccount').value?{account:Number(el('trackerAccount').value)}:{})})});
      await window.CurrencyModule?.init();
      window.dispatchEvent(new Event('platform-rewards-updated'));
      window.showToast?.(`已领取 ${result.total} Coins`);
      if(version===generation){await load();el('trackerStatus').textContent=`已领取 ${result.total} Coins`;}
    } catch(error){if(version===generation)el('trackerStatus').textContent=error.message;button.disabled=false;}
  }
  el('trackerClaim').onclick=event=>claim(data.reward_keys,event.target);
  el('trackerMatrix').addEventListener('click',event=>{const button=event.target.closest('[data-reward-key]');if(button)claim([button.dataset.rewardKey],button);});
  window.contestTracker={activate(value){active=value;clearTimeout(timer);if(!value)generation++;if(value){syncTabs();if(selected)load();}}};
  new ResizeObserver(syncTabs).observe(panel);
  document.fonts?.ready.then(syncTabs);
  window.addEventListener('platform-accounts-updated',()=>{if(active&&selected)load();});
  window.addEventListener('platform-solved-updated',()=>{if(active&&selected)load();});
  window.addEventListener('platform-rewards-updated',()=>{if(active&&selected)load();});
})();
