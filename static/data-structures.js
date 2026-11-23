(() => {
  const root = document.querySelector('#dataStructureContent');
  const endpoint = '/api/data-structures';
  let requestVersion = 0;
  let implementationVersion = 0;
  let managementVersion = 0;
  const submissionLabels = {unsubmitted: '未提交', submitted: '已提交', rejected: '已驳回'};
  const implementationTime = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short',
  });

  function mutate(url, method, data) {
    return communityRequest(url, {
      method,
      headers: {'Content-Type': 'application/json'},
      body: data === undefined ? undefined : JSON.stringify(data),
    });
  }

  function renderError(container, error, retry) {
    container.innerHTML = '<p role="alert"></p><button type="button" class="secondary-button">重试</button>';
    container.querySelector('p').textContent = error.message;
    container.querySelector('button').onclick = retry;
  }

  async function load(collectionId = null, {focusHeading = false, focusCollectionId = null, scope = 'browse', implementationScope = 'team'} = {}) {
    const version = ++requestVersion;
    root.innerHTML = '<p role="status">正在加载数据结构…</p>';
    try {
      const url = collectionId ? `${endpoint}/${collectionId}` : endpoint;
      const data = await communityRequest(`${url}?scope=${scope}`);
      if (version !== requestVersion) return;
      if (collectionId) renderCollection(data, scope, implementationScope);
      else renderCollections(data);
      if (focusHeading) root.querySelector('h2')?.focus();
      if (focusCollectionId !== null) {
        const option = root.querySelector(`[data-collection-id="${focusCollectionId}"]`);
        (option || root.closest('.page-view').querySelector('h1'))?.focus();
      }
    } catch (error) {
      if (version !== requestVersion) return;
      if (collectionId && error.status === 404) {
        showToast(error.message);
        if (scope === 'manage') setView('admin');
        else await load(null, {focusCollectionId: collectionId, scope});
        return;
      }
      renderError(root, error, () => load(collectionId, {focusHeading, focusCollectionId, scope, implementationScope}));
    }
  }

  function renderCollections({collections}) {
    root.innerHTML = '<div class="data-structure-list"></div>';
    const list = root.querySelector('.data-structure-list');
    list.innerHTML = collections.map(item => `
      <button type="button" class="panel data-structure-item" data-collection-id="${item.id}">
        <span class="data-structure-item-label">
          <strong>${escapeHtml(item.name)}</strong>
          <span class="muted">${item.is_open ? '开放提交' : '已关闭'} · ${item.implementation_count} 份实现</span>
        </span>
        <span class="data-structure-submission-status" data-status="${escapeHtml(item.my_submission_status)}">
          ${submissionLabels[item.my_submission_status]}
        </span>
      </button>`).join('') || '<p class="queue-empty">本团队还没有数据结构。</p>';
    list.querySelectorAll('[data-collection-id]').forEach(button => {
      button.onclick = () => load(Number(button.dataset.collectionId), {focusHeading: true});
    });
  }

  function renderCollection(data, scope, implementationScope) {
    const {collection, implementations, languages, can_submit: canSubmit} = data;
    root.innerHTML = `
      <div class="data-structure-detail-toolbar" data-site-themed="true">
        <button type="button" class="secondary-button" data-back>${scope === 'manage' ? '返回管理工作台' : '返回列表'}</button>
        ${scope === 'manage' ? '' : `<div class="segmented data-structure-scope-switch" role="tablist" aria-label="实现范围">
          <button id="dataStructureMineTab" type="button" class="segment" role="tab"
            aria-controls="dataStructureImplementations" data-implementation-scope="mine">我的</button>
          <button id="dataStructureTeamTab" type="button" class="segment" role="tab"
            aria-controls="dataStructureImplementations" data-implementation-scope="team">团队</button>
        </div>`}
      </div>
      <section class="panel data-structure-panel">
        <div class="panel-heading">
          <h2 tabindex="-1">${escapeHtml(collection.name)}</h2>
          ${scope !== 'manage' && canSubmit && collection.languages.length ? '<button type="button" class="primary-button" data-submit>提交</button>' : ''}
        </div>
        <p class="muted">${collection.is_open ? '开放提交' : '已关闭提交'}</p>
        ${collection.requirements ? `<p class="data-structure-notes">${escapeHtml(collection.requirements)}</p>` : ''}
      </section>
      <div id="dataStructureImplementations" class="data-structure-implementation-grid" data-implementations></div>`;
    root.querySelector('[data-back]').onclick = () => {
      if (scope === 'manage') setView('admin');
      else load(null, {focusCollectionId: collection.id, scope});
    };
    const reload = () => load(collection.id, {scope, implementationScope});
    const submit = root.querySelector('[data-submit]');
    if (submit) submit.onclick = () => editImplementation(data, null, collection.languages, reload);
    const list = root.querySelector('[data-implementations]');

    function renderImplementations() {
      const entries = scope === 'manage' ? implementations : implementations.filter(
        entry => implementationScope === 'mine' ? entry.is_mine : !entry.is_rejected,
      );
      const empty = scope !== 'manage' && implementationScope === 'mine' ? '你还没有提交代码。' : '暂无代码实现。';
      list.innerHTML = entries.map(entry => implementationCard(entry, languages)).join('')
        || `<p class="queue-empty">${empty}</p>`;
      list.querySelectorAll('[data-entry-id]').forEach(card => {
        const button = card.querySelector('button');
        button.onclick = () => openImplementation(data, Number(card.dataset.entryId), button, scope, reload);
      });
    }

    const tabs = root.querySelector('.data-structure-scope-switch');
    if (tabs) {
      list.setAttribute('role', 'tabpanel');
      const selectTab = button => {
        syncSegmented(tabs, button);
        list.setAttribute('aria-labelledby', button.id);
      };
      selectTab(tabs.querySelector(`[data-implementation-scope="${implementationScope}"]`));
      bindSegmentedKeyboard(tabs);
      tabs.querySelectorAll('[data-implementation-scope]').forEach(button => {
        button.onclick = () => {
          const next = button.dataset.implementationScope;
          if (next === implementationScope) return;
          implementationScope = next;
          ++implementationVersion;
          selectTab(button);
          renderImplementations();
        };
      });
    }
    renderImplementations();
  }

  function implementationCard(entry, languages) {
    const author = uiMemberName(entry.author);
    const language = languages[entry.language] || entry.language;
    const avatar = entry.member_id
      ? memberAvatar(entry.member_id, author)
      : `<span class="leader-avatar" aria-hidden="true">${escapeHtml(author.slice(0, 1))}</span>`;

    return `<article class="panel data-structure-implementation-card" data-entry-id="${entry.id}">
      <div class="member-line">
        ${avatar}
        <div class="data-structure-author">
          <strong>${escapeHtml(author)}</strong>
          <small class="muted">更新于 <time datetime="${escapeHtml(entry.updated_at)}">${implementationTime.format(new Date(entry.updated_at))}</time></small>
        </div>
        <span class="submission-language-tag" aria-label="代码语言：${escapeHtml(language)}">${escapeHtml(language)}</span>
      </div>
      <div class="data-structure-implementation-actions">
        ${entry.is_mine ? '<span class="muted">我的实现</span>' : ''}
        ${entry.is_rejected ? '<span class="data-structure-rejected">已驳回</span>' : ''}
        ${entry.training_points > 0 ? `<span class="score">+${Number(entry.training_points)} 分</span>` : ''}
        <button type="button" class="secondary-button" aria-label="查看详情：${escapeHtml(author)} · ${escapeHtml(language)}">查看详情</button>
      </div>
    </article>`;
  }

  function openDialog(content, {focusContainer = false} = {}) {
    const dialog = document.createElement('dialog');
    dialog.className = 'data-structure-dialog';
    dialog.setAttribute('aria-labelledby', 'dataStructureDialogTitle');
    dialog.innerHTML = content;
    dialog.querySelector('h2').id = 'dataStructureDialogTitle';
    dialog.addEventListener('close', () => dialog.remove(), {once: true});
    dialog.querySelector('[data-close]').onclick = () => dialog.close();
    if (focusContainer) dialog.tabIndex = -1;
    document.body.append(dialog);
    dialog.showModal();
    if (focusContainer) dialog.focus({preventScroll: true});
    return dialog;
  }

  function submitForm(dialog, save, validate) {
    const form = dialog.querySelector('form');
    const submit = form.querySelector('[type=submit]');
    const status = form.querySelector('[role=alert]');
    dialog.addEventListener('cancel', event => {
      if (submit.disabled) event.preventDefault();
    });
    form.onsubmit = async event => {
      event.preventDefault();
      if (submit.disabled || (validate && !validate())) return;
      const values = new FormData(form);
      const label = submit.textContent;
      const controls = [...form.elements].filter(control => !control.disabled);
      controls.forEach(control => { control.disabled = true; });
      form.setAttribute('aria-busy', 'true');
      submit.textContent = '正在保存…';
      status.textContent = '';
      try {
        await save(values);
        dialog.close();
      } catch (error) {
        status.textContent = error.message;
      } finally {
        controls.forEach(control => { control.disabled = false; });
        form.removeAttribute('aria-busy');
        submit.textContent = label;
      }
    };
  }

  function materialTabs(prefix, editing = false) {
    return `<div class="submission-material-tabs" role="tablist" aria-label="内容类型">
      <button type="button" role="tab" id="${prefix}CodeTab" aria-controls="${prefix}CodePane" aria-selected="true">
        代码${editing ? ' <small class="material-requirement">必填</small>' : ''}
      </button>
      <button type="button" role="tab" id="${prefix}BlogTab" aria-controls="${prefix}BlogPane" aria-selected="false" tabindex="-1">
        Blog${editing ? ' <small class="material-requirement">可选</small>' : ''}
      </button>
    </div>`;
  }

  function bindMaterialTabs(dialog) {
    const tabs = [...dialog.querySelectorAll('[role="tab"]')];
    function selectTab(index) {
      tabs.forEach((tab, position) => {
        const selected = index === position;
        tab.setAttribute('aria-selected', String(selected));
        tab.tabIndex = selected ? 0 : -1;
        dialog.querySelector('#' + tab.getAttribute('aria-controls')).hidden = !selected;
      });
    }
    tabs.forEach((tab, index) => {
      tab.onclick = () => selectTab(index);
      tab.onkeydown = event => {
        const next = {ArrowLeft: 1 - index, ArrowRight: 1 - index, Home: 0, End: 1}[event.key];
        if (next === undefined) return;
        event.preventDefault();
        selectTab(next);
        tabs[next].focus();
      };
    });
    return selectTab;
  }

  function renderBlog(container, blog, legacyNotes) {
    container.replaceChildren();
    if (blog) {
      renderSubmissionBlog(container, blog);
      return;
    }
    const message = document.createElement('p');
    const legacy = blog == null && legacyNotes;
    message.className = legacy ? 'data-structure-notes' : 'muted';
    message.textContent = legacy || '尚未填写 Blog。';
    container.append(message);
  }

  function entryUrl(collectionId, entryId) {
    return `${endpoint}/${collectionId}/implementations${entryId ? `/${entryId}` : ''}`;
  }

  async function openImplementation(data, id, button, scope, reload) {
    const {collection, languages, can_submit: canSubmit, can_manage: canManage} = data;
    const version = ++implementationVersion;
    const collectionVersion = requestVersion;
    button.disabled = true;
    try {
      const entry = await communityRequest(`${entryUrl(collection.id, id)}?scope=${scope}`);
      if (version !== implementationVersion || collectionVersion !== requestVersion
          || !root.closest('.page-view').classList.contains('active')) return;
      const canEdit = scope !== 'manage' && entry.is_mine && canSubmit && collection.languages.includes(entry.language);
      const dialog = openDialog(`
        <h2>${escapeHtml(uiMemberName(entry.author))} · ${escapeHtml(languages[entry.language] || entry.language)}</h2>
        ${entry.is_rejected ? `<p class="data-structure-notes"><strong>已驳回${entry.training_archived ? '' : ' · 不计分'}</strong> · ${entry.rejection_note ? escapeHtml(entry.rejection_note) : '旧记录未填写原因'}</p>` : ''}
        ${entry.training_tracked && (!entry.is_rejected || entry.training_archived)
          ? `<p class="muted">${entry.training_archived ? '归档时计入 · ' : ''}有效提交 ${Number(entry.training_submissions)} 次 · 积分 ${Number(entry.training_points)} 分</p>`
          : !entry.is_rejected ? '<p class="muted">未计入训练统计</p>' : ''}
        ${materialTabs('structureDetail')}
        <div id="structureDetailCodePane" role="tabpanel" aria-labelledby="structureDetailCodeTab">
          <pre><code></code></pre>
        </div>
        <div id="structureDetailBlogPane" class="data-structure-blog-pane blog-rendered" role="tabpanel" aria-labelledby="structureDetailBlogTab" hidden></div>
        <div class="data-structure-actions">
          <button type="button" class="secondary-button" data-close>关闭</button>
          ${canEdit ? `<button type="button" class="primary-button" data-edit>${entry.is_rejected ? '修改重交' : '编辑代码'}</button>` : ''}
          ${scope === 'manage' && canManage && !entry.is_rejected ? '<button type="button" class="secondary-button" data-reject>驳回</button>' : ''}
        </div>`);
      bindMaterialTabs(dialog);
      const code = dialog.querySelector('#structureDetailCodePane code');
      code.textContent = entry.code;
      decorateSubmissionCode(code, entry.language);
      renderBlog(dialog.querySelector('#structureDetailBlogPane'), entry.blog, entry.notes);
      const edit = dialog.querySelector('[data-edit]');
      if (edit) edit.onclick = () => {
        dialog.close();
        editImplementation(data, entry, [entry.language], reload);
      };
      const reject = dialog.querySelector('[data-reject]');
      if (reject) reject.onclick = () => {
        dialog.close();
        rejectImplementation(collection.id, entry);
      };
    } catch (error) {
      showToast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function rejectImplementation(collectionId, entry) {
    const dialog = openDialog(`
      <form class="data-structure-form">
        <h2>驳回提交</h2>
        <p>驳回后，代码和 Blog 将从团队提交中移除。作者可以查看原因并修改重交。</p>
        <label>驳回原因<textarea name="rejection_note" rows="4" maxlength="1000" required></textarea></label>
        <p role="alert" class="data-structure-error"></p>
        <div class="data-structure-actions">
          <button type="button" class="secondary-button" data-close>取消</button>
          <button type="submit" class="primary-button">确认驳回</button>
        </div>
      </form>`);
    submitForm(dialog, async values => {
      await mutate(entryUrl(collectionId, entry.id), 'PATCH', {
        action: 'reject', rejection_note: values.get('rejection_note'), version: entry.updated_at,
      });
      showToast('已驳回，可由作者修改重交');
      if (entry.training_tracked && !entry.training_archived) void loadDashboardSummary().catch(() => {});
      await load(collectionId, {scope: 'manage'});
    });
  }

  function editImplementation({collection, languages, training_rules: rules}, entry, available, reload) {
    const countsForTraining = entry ? entry.training_tracked && !entry.training_archived : rules.available;
    const requestId = entry ? null : window.clubApi.requestId();
    const label = entry?.is_rejected ? '修改重交' : entry ? '保存修改' : '提交';
    const dialog = openDialog(`
      <form class="data-structure-form">
        <h2>${entry?.is_rejected ? '修改重交' : entry ? '编辑代码' : '提交'} · ${escapeHtml(collection.name)}</h2>
        <div>
          ${materialTabs('structureEdit', true)}
          <div id="structureEditCodePane" role="tabpanel" aria-labelledby="structureEditCodeTab">
            <div class="field-label"><span>${countsForTraining
              ? `必填 · ${Number(rules.code_points)} 分`
              : entry?.training_archived ? '积分已随学期归档' : '本次不计入训练统计'}</span></div>
            <label class="code-language-label">代码语言 <select name="language" ${entry ? 'disabled' : ''}>
              ${available.map(language => `<option value="${escapeHtml(language)}">${escapeHtml(languages[language])}</option>`).join('')}
            </select></label>
            <div class="data-structure-editor">
              <pre aria-hidden="true"><code></code></pre>
              <textarea name="code" aria-label="代码" placeholder="在此填写数据结构代码" maxlength="200000" aria-required="true" spellcheck="false" autocapitalize="off" wrap="off"></textarea>
            </div>
          </div>
          <div id="structureEditBlogPane" class="data-structure-blog-pane" role="tabpanel" aria-labelledby="structureEditBlogTab" hidden>
            ${countsForTraining ? `<div class="field-label"><span>可选 · ${Number(rules.blog_points)} 分</span></div>` : ''}
            <button type="button" class="blog-open-button" data-open-blog>打开 Markdown 编辑器</button>
            <div class="blog-rendered editor-blog-preview" data-blog-summary></div>
          </div>
        </div>
        <p role="alert" class="data-structure-error"></p>
        <div class="data-structure-actions">
          <button type="button" class="secondary-button" data-close>取消</button>
          <button type="submit" class="primary-button">${label}</button>
        </div>
      </form>
      <div class="toast" role="status"></div>`, {focusContainer: true});
    const selectTab = bindMaterialTabs(dialog);
    const {code, language} = dialog.querySelector('form').elements;
    code.value = entry?.code || '';
    const toast = dialog.querySelector('.toast');
    let hideToast;
    function validateCode() {
      if (code.value.trim()) return true;
      selectTab(0);
      code.focus();
      toast.textContent = '请填写数据结构代码';
      toast.classList.add('show');
      clearTimeout(hideToast);
      hideToast = setTimeout(() => toast.classList.remove('show'), 2800);
      return false;
    }
    let blog = entry?.blog ?? null;
    const legacyNotes = entry?.notes || '';
    const summary = dialog.querySelector('[data-blog-summary]');
    renderBlog(summary, blog, legacyNotes);
    dialog.querySelector('[data-open-blog]').onclick = () => {
      // Escape punctuation so descriptions originally stored as plain text keep their meaning.
      const content = blog ?? legacyNotes.replace(/[\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e]/g, '\\$&');
      window.openMarkdownEditor({
        content,
        onSave: markdown => {
          blog = markdown;
          renderBlog(summary, blog, legacyNotes);
        },
      });
    };
    const highlight = dialog.querySelector('.data-structure-editor pre');
    const layer = highlight.querySelector('code');
    function syncScroll() {
      highlight.scrollTop = code.scrollTop;
      highlight.scrollLeft = code.scrollLeft;
    }
    function highlightDraft() {
      layer.textContent = code.value + '\n ';
      window.CodeColors?.colorElement(layer, language.value);
      syncScroll();
    }
    code.oninput = highlightDraft;
    code.onscroll = syncScroll;
    language.onchange = highlightDraft;
    highlightDraft();

    submitForm(dialog, async values => {
      const payload = {language: language.value, code: values.get('code')};
      if (!entry) payload.request_id = requestId;
      if (blog !== null) payload.blog = blog;
      if (entry?.is_rejected) payload.action = 'resubmit';
      if (entry) payload.version = entry.updated_at;
      const result = await mutate(entryUrl(collection.id, entry?.id), entry ? 'PATCH' : 'POST', payload);
      showToast(entry?.is_rejected ? '已重新提交，团队成员可查看' : entry ? '已保存代码' : '代码已提交');
      if (result.training_points > 0 && !entry?.training_archived) void loadDashboardSummary().catch(() => {});
      await reload();
    }, validateCode);
  }

  async function loadManagement(expectedTeamId) {
    const version = ++managementVersion;
    const host = document.querySelector('#wbDataStructureList');
    host.innerHTML = '<p role="status">正在加载数据结构…</p>';
    const add = document.querySelector('#wbAddDataStructure');
    const order = document.querySelector('#wbOrderDataStructures');
    add.disabled = true;
    order.hidden = true;
    try {
      const {collections, languages, team_id: teamId, can_manage: canManage} = await communityRequest(`${endpoint}?scope=manage`);
      if (version !== managementVersion) return;
      if (teamId !== expectedTeamId) throw new Error('当前团队已切换，请刷新页面');
      add.disabled = !canManage;
      add.onclick = () => editCollection(null, teamId, languages);
      order.hidden = !canManage || collections.length < 2;
      order.onclick = () => editOrder(collections, teamId);
      host.innerHTML = canManage ? collections.map(item => `
        <article class="workbench-team">
          <div class="workbench-team-heading">
            <div><h3>${escapeHtml(item.name)}</h3>
            <small>${item.is_open ? '开放提交' : '已关闭'} · ${item.implementation_count} 份实现</small></div>
            <div class="data-structure-actions" data-admin-item="${item.id}">
              <button type="button" class="secondary-button" data-view-item>查看实现</button>
              <button type="button" class="secondary-button" data-edit-item>编辑</button>
              <button type="button" class="secondary-button" data-delete-item ${item.implementation_count ? 'disabled' : ''}>删除</button>
            </div>
          </div>
          ${item.implementation_count ? '<p class="muted">已有代码提交，无法删除。</p>' : ''}
        </article>`).join('') || '<p class="queue-empty">暂无数据结构。</p>' : '';
      host.querySelectorAll('[data-admin-item]').forEach(actions => {
        const item = collections.find(collection => collection.id === Number(actions.dataset.adminItem));
        actions.querySelector('[data-edit-item]').onclick = () => editCollection(item, teamId, languages);
        actions.querySelector('[data-view-item]').onclick = () => {
          setView('dataStructures', {preserveContent: true});
          load(item.id, {focusHeading: true, scope: 'manage'});
        };
        actions.querySelector('[data-delete-item]').onclick = event => deleteCollection(item, event.currentTarget, teamId);
      });
    } catch (error) {
      if (version === managementVersion) renderError(host, error, () => loadManagement(expectedTeamId));
    }
  }

  function editOrder(collections, teamId) {
    const items = [...collections];
    const previousIds = items.map(item => item.id);
    const dialog = openDialog(`
      <form class="data-structure-form">
        <h2>调整数据结构顺序</h2>
        <ol class="data-structure-order-list" aria-label="数据结构顺序"></ol>
        <p role="alert" class="data-structure-error"></p>
        <div class="data-structure-actions">
          <button type="button" class="secondary-button" data-close>取消</button>
          <button type="submit" class="primary-button">保存排序</button>
        </div>
      </form>`);
    const list = dialog.querySelector('ol');

    function renderOrder() {
      list.innerHTML = items.map((item, index) => `
        <li class="data-structure-order-item" data-order-id="${item.id}">
          <strong>${escapeHtml(item.name)}</strong>
          <div class="data-structure-actions">
            <button type="button" class="secondary-button" data-order-step="-1"
              aria-label="上移 ${escapeHtml(item.name)}" ${index === 0 ? 'disabled' : ''}>上移</button>
            <button type="button" class="secondary-button" data-order-step="1"
              aria-label="下移 ${escapeHtml(item.name)}" ${index === items.length - 1 ? 'disabled' : ''}>下移</button>
          </div>
        </li>`).join('');
    }

    list.onclick = event => {
      const button = event.target.closest('[data-order-step]');
      if (!button || button.disabled) return;
      const id = Number(button.closest('li').dataset.orderId);
      const index = items.findIndex(item => item.id === id);
      const step = Number(button.dataset.orderStep);
      const next = index + step;
      [items[index], items[next]] = [items[next], items[index]];
      renderOrder();
      const row = list.children[next];
      const sameDirection = row.querySelector(`[data-order-step="${step}"]:not(:disabled)`);
      (sameDirection || row.querySelector('button:not(:disabled)')).focus();
    };

    renderOrder();
    submitForm(dialog, async () => {
      try {
        await mutate(`${endpoint}/order`, 'PATCH', {
          previous_ids: previousIds,
          ids: items.map(item => item.id),
        });
      } catch (error) {
        if (error.status === 409) await loadManagement(teamId);
        throw error;
      }
      showToast('已保存排序');
      await loadManagement(teamId);
    });
  }

  function editCollection(item, teamId, languageOptions) {
    const dialog = openDialog(`
      <form class="data-structure-form">
        <h2>${item ? '编辑数据结构' : '新增数据结构'}</h2>
        <label>名称<input name="name" maxlength="100" required></label>
        <label>实现要求<textarea name="requirements" rows="4" maxlength="5000"></textarea></label>
        <fieldset>
          <legend>允许提交的语言</legend>
          <div class="data-structure-languages">
            <button type="button" class="secondary-button" data-select-all-languages>全选</button>
            ${Object.entries(languageOptions).map(([value, label]) => `
              <label><input type="checkbox" name="languages" value="${escapeHtml(value)}"
                ${item?.languages.includes(value) || (!item && value === 'cpp') ? 'checked' : ''}>${escapeHtml(label)}</label>`).join('')}
          </div>
        </fieldset>
        <label class="workbench-check"><input type="checkbox" name="is_open" ${item?.is_open === false ? '' : 'checked'}>开放提交</label>
        <p role="alert" class="data-structure-error"></p>
        <div class="data-structure-actions">
          <button type="button" class="secondary-button" data-close>取消</button>
          <button type="submit" class="primary-button">保存数据结构</button>
        </div>
      </form>`);
    const form = dialog.querySelector('form');
    form.elements.namedItem('name').value = item?.name || '';
    form.elements.requirements.value = item?.requirements || '';
    form.querySelector('[data-select-all-languages]').onclick = () => {
      form.querySelectorAll('input[name="languages"]').forEach(checkbox => { checkbox.checked = true; });
    };
    submitForm(dialog, async values => {
      const languages = values.getAll('languages');
      if (!languages.length) throw new Error('请至少选择一种代码语言');
      try {
        await mutate(item ? `${endpoint}/${item.id}` : endpoint, item ? 'PATCH' : 'POST', {
          name: values.get('name'),
          requirements: values.get('requirements'),
          languages,
          is_open: values.has('is_open'),
          ...(item ? {version: item.version} : {team_id: teamId}),
        });
      } catch (error) {
        if (item && error.status === 409) await loadManagement(teamId);
        throw error;
      }
      showToast('已保存数据结构');
      await loadManagement(teamId);
    });
  }

  async function deleteCollection(item, button, teamId) {
    if (button.disabled) return;
    button.disabled = true;
    try {
      if (!await clubDialog.confirm({
        title: '删除数据结构', text: `确定删除「${item.name}」？删除后，成员将无法再查看该数据结构。`,
        confirmText: '确认删除',
      })) return;
      await mutate(`${endpoint}/${item.id}`, 'DELETE');
      showToast('已删除数据结构');
      await loadManagement(teamId);
    } catch (error) {
      showToast(error.message);
      if (error.status === 409 || error.status === 404) await loadManagement(teamId);
    } finally {
      button.disabled = false;
    }
  }

  window.DataStructures = {load, loadManagement};
})();
