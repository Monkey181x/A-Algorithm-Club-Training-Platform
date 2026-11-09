(() => {
  const root = document.getElementById('extensionResources');
  const escape = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let items = [];
  let selectedCategory = 'all';
  let categories = [];
  const categoryOptions = value => `<option value="" ${!value ? 'selected' : ''} disabled>请选择分类</option>` + categories.map(c => `<option value="${escape(c.key)}" ${value === c.key ? 'selected' : ''}>${escape(c.name)}</option>`).join('');
  async function api(url, options) {
    const response = await clubApi.request(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || '操作失败，请重试');
    return payload;
  }
  async function load() {
    window.PokerModule?.close();
    root.innerHTML = '<p role="status">正在加载资源…</p>';
    try {
      const result = await api('/api/extensions'); items = result.items; categories = result.categories;
      if (selectedCategory !== 'all' && !categories.some(c => c.key === selectedCategory)) selectedCategory = 'all';
      root.innerHTML = `<section class="page-heading"><div><p class="kicker">EXPLORE & LEARN</p><h1>拓展</h1></div>${result.can_manage ? '<div class="extension-heading-actions"><button class="secondary-button" id="extensionCategories">管理分类</button><button class="primary-button" id="extensionAdd">＋ 添加资源</button></div>' : ''}</section><div data-site-themed="true"><div class="segmented extension-filters" role="tablist" aria-label="拓展分类">${[['all','全部'],...categories.map(c => [c.key,c.name])].map(([value,label]) => `<button class="segment ${selectedCategory === value ? 'active' : ''}" type="button" data-category="${value}">${escape(label)}</button>`).join('')}</div></div><div class="extension-wall"><article class="extension-tile" data-resource-category="${result.poker_category || 'games'}"><button type="button" class="extension-tile poker-entry" id="pokerEntry"><span class="extension-placeholder" aria-hidden="true">♠</span><span class="extension-copy"><small>RBC POKER CLUB</small><strong>德州扑克</strong><span>多人联机 · 免费练习 · 人机对战</span></span></button>${result.can_manage ? '<div class="extension-controls"><button id="pokerCategoryEdit">编辑归属</button></div>' : ''}</article>${items.map(item => `<article class="extension-tile" data-resource-category="${escape(item.category || 'library')}"><a class="extension-link" href="${escape(item.href)}" ${item.kind === 'link' ? 'target="_blank" rel="noopener noreferrer"' : 'download'} aria-label="${escape(item.title)} · ${item.kind === 'file' ? '下载文件' : '打开链接'}">${item.cover ? `<img src="${escape(item.cover)}" alt="" loading="lazy">` : `<span class="extension-placeholder" aria-hidden="true">${item.kind === 'file' ? '↓' : '↗'}</span>`}<span class="extension-copy"><small>${item.kind === 'file' ? '↓ 文件下载' : '↗ 外部链接'}</small><strong>${escape(item.title)}</strong><span>${escape(item.description || '')}</span></span></a>${result.can_manage ? `<div class="extension-controls"><button data-edit="${item.id}">编辑</button><button data-delete="${item.id}">删除</button></div>` : ''}</article>`).join('')}</div>`;
      const filter = () => {
        root.querySelectorAll('[data-resource-category]').forEach(tile => {
          tile.hidden = selectedCategory !== 'all' && tile.dataset.resourceCategory !== selectedCategory;
        });
        syncSegmented(root.querySelector('.extension-filters'), root.querySelector(`[data-category="${selectedCategory}"]`));
      };
      const tabs = [...root.querySelectorAll('[data-category]')];
      tabs.forEach((button, index) => {
        button.onclick = () => { selectedCategory = button.dataset.category; filter(); };
        button.onkeydown = event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
          tabs[next].focus(); tabs[next].click();
        };
      });
      filter();
      root.querySelector('#pokerCategoryEdit')?.addEventListener('click', () => {
        const el = dialog(`<form class="extension-form"><h2>德州扑克归属</h2><label>显示分类<select name="category" required>${categoryOptions(result.poker_category || 'games')}</select></label><p role="alert"></p><div class="extension-actions"><button type="button" data-cancel class="secondary-button">取消</button><button class="primary-button" type="submit">保存</button></div></form>`);
        el.querySelector('form').onsubmit = async event => {
          event.preventDefault();
          const button = el.querySelector('[type=submit]'); button.disabled = true;
          try { await api('/api/extensions/builtin/poker', {method:'POST', body:new FormData(event.target)}); el.close(); await load(); }
          catch (error) { el.querySelector('[role=alert]').textContent = error.message; }
          finally { button.disabled = false; }
        };
      });
      root.querySelector('#pokerEntry').onclick = () => window.PokerModule.open();
      root.querySelector('#extensionCategories')?.addEventListener('click', manageCategories);
      root.querySelector('#extensionAdd')?.addEventListener('click', () => editor());
      root.querySelectorAll('[data-edit]').forEach(button => button.onclick = () => editor(items.find(i => i.id === Number(button.dataset.edit))));
      root.querySelectorAll('[data-delete]').forEach(button => button.onclick = () => remove(items.find(i => i.id === Number(button.dataset.delete))));
    } catch (error) { root.innerHTML = `<p role="alert">${escape(error.message)}</p><button id="extensionRetry" class="secondary-button">重试</button>`; root.querySelector('button').onclick = load; }
  }
  function dialog(content) {
    const el = document.createElement('dialog'); el.className = 'extension-dialog'; el.innerHTML = content;
    document.body.append(el); el.addEventListener('close', () => el.remove()); el.showModal();
    el.querySelector('[data-cancel]').onclick = () => el.close(); return el;
  }
  function manageCategories() {
    const el = dialog(`<h2>管理分类</h2><p>上移、下移后自动保存顺序。只有空分类可以删除；请先调整已有资源的归属。${categories.length ? '' : '请先添加分类，再添加资源。'}</p><div class="extension-category-list">${categories.map((c, index) => `<div><span>${escape(c.name)}（${c.count} 个资源）</span><div class="extension-category-actions"><button type="button" data-move-category="${escape(c.key)}" data-direction="up" ${index === 0 ? 'disabled' : ''} aria-label="上移${escape(c.name)}">上移</button><button type="button" data-move-category="${escape(c.key)}" data-direction="down" ${index === categories.length - 1 ? 'disabled' : ''} aria-label="下移${escape(c.name)}">下移</button><button type="button" data-remove-category="${escape(c.key)}" ${c.count ? 'disabled title="请先移动分类内的资源"' : ''}>删除</button></div></div>`).join('')}</div><form class="extension-form"><label>新分类名称<input name="name" required maxlength="30" placeholder="输入分类名称"></label><button class="primary-button" type="submit">添加分类</button></form><p role="alert"></p><div class="extension-actions"><button type="button" data-cancel class="secondary-button">关闭</button></div>`);
    let busy = false;
    el.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    async function save(url, options) {
      if (busy) return;
      busy = true;
      const buttons = [...el.querySelectorAll('button')];
      const disabled = buttons.map(b => b.disabled);
      buttons.forEach(b => b.disabled = true);
      try { await api(url, options); el.close(); await load(); manageCategories(); }
      catch (error) { el.querySelector('[role=alert]').textContent = error.message; }
      finally { busy = false; buttons.forEach((b, i) => b.disabled = disabled[i]); }
    }
    el.querySelector('form').onsubmit = event => {
      event.preventDefault();
      save('/api/extensions/categories', {method:'POST', body:new FormData(event.target)});
    };
    el.querySelectorAll('[data-move-category]').forEach(button => button.onclick = () => {
      const body = new FormData(); body.set('direction', button.dataset.direction);
      save(`/api/extensions/categories/${encodeURIComponent(button.dataset.moveCategory)}/move`, {method:'POST', body});
    });
    el.querySelectorAll('[data-remove-category]').forEach(button => button.onclick = () => {
      const category = categories.find(c => c.key === button.dataset.removeCategory);
      const confirmation = dialog(`<h2>删除分类</h2><p>确定删除空分类“${escape(category.name)}”？</p><div class="extension-actions"><button type="button" data-cancel>取消</button><button type="button" data-confirm>删除</button></div>`);
      confirmation.querySelector('[data-confirm]').onclick = () => {
        confirmation.close();
        save(`/api/extensions/categories/${encodeURIComponent(category.key)}`, {method:'DELETE'});
      };
    });
  }
  function remove(item) {
    const el = dialog(`<h2>删除资源</h2><p>确定删除“${escape(item.title)}”？删除后成员将无法再访问此资源。</p><p role="alert"></p><div class="extension-actions"><button data-cancel class="secondary-button">取消</button><button data-confirm class="primary-button">确认删除</button></div>`);
    el.querySelector('[data-confirm]').onclick = async event => {
      event.target.disabled = true;
      try { await api(`/api/extensions/${item.id}`, {method:'DELETE'}); el.close(); await load(); }
      catch (error) { el.querySelector('[role=alert]').textContent = error.message; event.target.disabled = false; }
    };
  }
  function editor(item = {}) {
    if (!categories.length) { manageCategories(); return; }
    const el = dialog(`<form class="extension-form"><h2>${item.id ? '编辑资源' : '添加资源'}</h2><label>名称<input name="title" maxlength="100" required value="${escape(item.title)}"></label><label>简介<textarea name="description" maxlength="300" rows="2">${escape(item.description)}</textarea></label><label>显示分类<select name="category" required>${categoryOptions(item.category)}</select></label><label>资源类型<select name="kind"><option value="link">超链接</option><option value="file" ${item.kind === 'file' ? 'selected' : ''}>下载文件</option></select></label><label data-url>链接地址<input type="url" name="url" placeholder="https://" value="${escape(item.url)}"></label><label data-file>下载文件<input type="file" name="file"><small>${item.filename ? `当前：${escape(item.filename)}，不选择则保留` : ''}</small></label><label>方格封面（可选）<input type="file" name="cover" accept="image/png,image/jpeg,image/webp"></label>${item.cover ? '<label class="extension-checkbox"><input type="checkbox" name="remove_cover" value="1">移除现有封面</label>' : ''}<p role="alert"></p><div class="extension-actions"><button type="button" data-cancel class="secondary-button">取消</button><button type="submit" class="primary-button">保存资源</button></div></form>`);
    const form = el.querySelector('form'), kind = form.elements.kind;
    function toggle() {
      form.querySelector('[data-url]').hidden = kind.value !== 'link';
      form.querySelector('[data-file]').hidden = kind.value !== 'file';
      form.elements.url.disabled = kind.value !== 'link';
      form.elements.url.required = kind.value === 'link';
      form.elements.file.disabled = kind.value !== 'file';
      form.elements.file.required = kind.value === 'file' && item.kind !== 'file';
    }
    kind.onchange = toggle; toggle();
    let busy = false;
    el.addEventListener('cancel', e => { if (busy) e.preventDefault(); });
    form.onsubmit = async event => {
      event.preventDefault(); if (busy) return;
      const body = new FormData(form);
      busy = true; form.querySelectorAll('button').forEach(b => b.disabled = true);
      try { await api(`/api/extensions${item.id ? '/' + item.id : ''}`, {method:'POST', body}); el.close(); await load(); }
      catch (error) { form.querySelector('[role=alert]').textContent = error.message; }
      finally { busy = false; form.querySelectorAll('button').forEach(b => b.disabled = false); }
    };
  }
  window.ExtensionResources = {load};
})();
