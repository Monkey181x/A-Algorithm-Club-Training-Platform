/* RBC Coins red packets. Wallet changes and allocation are owned by the server. */
(() => {
  const root = document.getElementById('redPacketsView');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => Number(value || 0).toLocaleString('zh-CN');
  const date = value => new Date(value).toLocaleString('zh-CN');
  const envelope = '<img class="rp-envelope-icon" src="/static/red-packet.svg" alt="" aria-hidden="true">';
  const avatar = packet => `<span class="rp-sender-avatar"><span aria-hidden="true">${esc(Array.from(packet.sender || '红')[0])}</span>${packet.sender_avatar ? `<img src="${esc(packet.sender_avatar)}" alt="${esc(packet.sender)}的头像">` : ''}</span>`;
  const uuid = () => typeof crypto.randomUUID === 'function' ? crypto.randomUUID() :
    '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16));
  let active = false, epoch = 0, page = 1, state = null, timer, loading = false;
  const dialogs = new Set();

  async function api(path = '', data) {
    const response = await clubApi.request('/api/red-packets' + path, data ? {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data),
    } : {cache:'no-store'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || '操作失败，请重试');
      error.status = response.status;
      throw error;
    }
    return payload;
  }
  function sync(balance) {
    window.CurrencyModule?.syncBalance(balance);
  }
  function draw() {
    root.innerHTML = `<button class="secondary-button" type="button" data-back>← 返回拓展</button>
      <section class="page-heading rp-heading"><div><p class="kicker">RBC RED PACKETS</p><h1>红包广场</h1><p>和队友分享好运，每份红包都是一份心意。</p></div>
      <div class="rp-heading-actions"><span>钱包余额 <strong>${number(state.balance)}</strong> RBC Coins</span><button class="primary-button" type="button" data-send ${state.can_send ? '' : 'disabled'}>＋ 发红包</button></div></section>
      <div class="rp-toolbar"><div><strong>全部红包</strong><span>未领完的优先展示 · 最新发布在前</span></div><button class="secondary-button" type="button" data-refresh>刷新广场</button></div>
      <p class="rp-notice" role="status" aria-live="polite">${state.enabled ? '' : '当前团队尚未启用 RBC Coins，可以查看红包记录，暂时无法发放或领取。'}</p>
      <div class="rp-grid">${state.packets.length ? state.packets.map(packet => {
        const done = packet.is_exhausted;
        const claimed = packet.is_claimed;
        const expired = packet.is_expired;
        const status = claimed ? '✓ 已领取' : expired ? '已过期' : done ? '已领完' : '待拆开';
        return `<button type="button" class="rp-card ${done || expired ? 'rp-card-done' : ''} ${claimed ? 'rp-card-claimed' : ''}" data-packet="${packet.id}" aria-label="${esc(packet.sender)}的红包，${esc(status)}，查看详情">
          <span class="rp-card-top"><span>${packet.kind === 'equal' ? '普通红包' : '拼手气红包'}${packet.is_mine ? ' · 我发出的' : ''}</span><span class="rp-badge">${status}</span></span>
          <span class="rp-envelope-seal" aria-hidden="true"></span>
          <span class="rp-card-main"><strong>${esc(packet.message)}</strong></span>
          ${claimed ? `<span class="rp-card-amount">${number(packet.my_amount)} <small>RBC Coins · 已领取</small></span><span class="rp-card-summary">共 ${number(packet.total_amount)} Coins · ${packet.total_count} 份 · 已领 ${packet.total_count - packet.remaining_count} 份</span>` : `<span class="rp-card-prompt">${expired ? '红包已过期，期待下一份好运' : done ? '来晚一步，红包已领完' : '点开红包，收下这份心意'}</span>`}
          <span class="rp-card-bottom"><span>${esc(packet.sender)} 的红包</span><span>${claimed ? '查看领取记录' : expired ? '已过期' : done ? '已领完' : '点击查看'}</span></span>
          <span class="rp-card-date">${esc(date(packet.created_at))}<span class="rp-card-expiry">${packet.expires_at ? `${expired ? '已过期' : '有效至'} ${esc(date(packet.expires_at))}` : '无限期'}</span></span>
        </button>`;
      }).join('') : `<div class="panel rp-empty">${envelope}<h2>好运，从第一个红包开始</h2><p>还没有红包，发一份 RBC Coins 红包和队友分享吧。</p></div>`}</div>
      <nav class="rp-pagination" aria-label="红包分页"><button class="secondary-button" type="button" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button><span>第 ${page} / ${state.pages} 页</span><button class="secondary-button" type="button" data-page="${page + 1}" ${page >= state.pages ? 'disabled' : ''}>下一页</button></nav>
      <p class="rp-rules">每人每个红包限领一次，发起人也可以领取。每份至少 1 Coin；普通红包等额分配，拼手气红包随机分配。发出时扣除总金额，可设置有效期或无限期，到期未领金额退回钱包。</p>`;
  }
  async function refresh() {
    if (!active || loading) return;
    const token = epoch;
    loading = true;
    try {
      const result = await api('?page=' + page);
      if (!active || token !== epoch) return;
      state = result;
      sync(result.balance);
      draw();
    } catch (error) {
      if (!active || token !== epoch) return;
      const notice = root.querySelector('.rp-notice');
      if (notice) notice.textContent = error.message;
      else root.innerHTML = `<p role="alert">${esc(error.message)}</p><button class="secondary-button" data-refresh>重试</button><button class="secondary-button" data-back>返回拓展</button>`;
    } finally { if (token === epoch) loading = false; }
  }
  function dialog(markup) {
    const element = document.createElement('dialog');
    element.className = 'rp-dialog';
    element.setAttribute('aria-labelledby', 'rp-dialog-title');
    element.innerHTML = markup;
    dialogs.add(element);
    document.body.append(element);
    function fitViewport() {
      const viewport = window.visualViewport;
      const height = viewport?.height || window.innerHeight;
      element.style.setProperty('--rp-dialog-height', height + 'px');
    }
    fitViewport();
    window.addEventListener('resize', fitViewport);
    window.visualViewport?.addEventListener('resize', fitViewport);
    window.visualViewport?.addEventListener('scroll', fitViewport);
    element.addEventListener('close', () => {
      window.removeEventListener('resize', fitViewport);
      window.visualViewport?.removeEventListener('resize', fitViewport);
      window.visualViewport?.removeEventListener('scroll', fitViewport);
    });
    element.addEventListener('error', event => { if (event.target.matches('.rp-sender-avatar img')) event.target.remove(); }, true);
    element.addEventListener('close', () => { dialogs.delete(element); element.remove(); });
    element.addEventListener('click', event => { if (event.target.closest('[data-cancel]')) element.close(); });
    element.showModal();
    return element;
  }
  function sendDialog() {
    if (!state?.can_send) return;
    const el = dialog(`<form class="rp-compose-form" novalidate>
      <header class="rp-compose-header">
        <div class="rp-compose-header-content">
          <div class="rp-compose-icon-badge" aria-hidden="true">${envelope}</div>
          <div class="rp-compose-title-group">
            <h2 id="rp-dialog-title">发红包</h2>
            <p class="rp-compose-subtitle">把好运和心意分享给团队队友</p>
          </div>
        </div>
        <button class="rp-compose-close" type="button" data-cancel aria-label="关闭发红包">×</button>
      </header>

      <div class="rp-compose-body">
        <fieldset class="rp-kind-picker" role="radiogroup" aria-label="红包类型">
          <legend class="rp-visually-hidden">红包类型</legend>
          <label class="rp-kind-option">
            <input type="radio" name="kind" value="random" checked>
            <span class="rp-kind-card">
              <span class="rp-kind-icon">🧧</span>
              <span class="rp-kind-text">
                <strong>拼手气红包</strong>
                <small>金额随机 · 惊喜好运</small>
              </span>
            </span>
          </label>
          <label class="rp-kind-option">
            <input type="radio" name="kind" value="equal">
            <span class="rp-kind-card">
              <span class="rp-kind-icon">🪙</span>
              <span class="rp-kind-text">
                <strong>普通红包</strong>
                <small>每份等额 · 心意相同</small>
              </span>
            </span>
          </label>
        </fieldset>

        <div class="rp-field-card rp-amount-card">
          <div class="rp-field-top">
            <label for="rpAmountInput" class="rp-field-label">总金额</label>
            <div class="rp-balance-indicator">
              <span>可用: <strong>${number(state.balance)}</strong> Coins</span>
              <button type="button" class="rp-fill-balance-btn" data-fill-balance>全部塞入</button>
            </div>
          </div>
          <div class="rp-input-row rp-amount-row">
            <span class="rp-currency-symbol" aria-hidden="true">🪙</span>
            <input id="rpAmountInput" aria-label="红包总金额" name="amount" type="number" inputmode="numeric" min="1" step="1" required placeholder="0" autocomplete="off">
            <span class="rp-unit">Coins</span>
          </div>
          <div class="rp-quick-tags" role="group" aria-label="快捷金额选择">
            <button type="button" class="rp-chip" data-quick-amount="10">10 Coins</button>
            <button type="button" class="rp-chip" data-quick-amount="50">50 Coins</button>
            <button type="button" class="rp-chip" data-quick-amount="100">100 Coins</button>
            <button type="button" class="rp-chip" data-quick-amount="200">200 Coins</button>
            <button type="button" class="rp-chip" data-quick-amount="500">500 Coins</button>
          </div>
          <p class="rp-field-hint" data-split-note>份数不限，每份至少 1 Coin，金额随机分配</p>
        </div>

        <div class="rp-field-card rp-count-card">
          <div class="rp-field-top">
            <label for="rpCountInput" class="rp-field-label">红包个数</label>
            <span class="rp-field-sub">每人限领一次</span>
          </div>
          <div class="rp-input-row rp-count-row">
            <span class="rp-count-icon" aria-hidden="true">👥</span>
            <input id="rpCountInput" aria-label="红包个数" name="count" type="number" inputmode="numeric" min="1" step="1" value="10" required autocomplete="off">
            <span class="rp-unit">个</span>
          </div>
          <div class="rp-quick-tags" role="group" aria-label="快捷份数选择">
            <button type="button" class="rp-chip" data-quick-count="3">3个</button>
            <button type="button" class="rp-chip" data-quick-count="5">5个</button>
            <button type="button" class="rp-chip" data-quick-count="10">10个</button>
            <button type="button" class="rp-chip" data-quick-count="20">20个</button>
            <button type="button" class="rp-chip" data-quick-count="50">50个</button>
          </div>
          <p class="rp-field-hint" data-count-note></p>
        </div>

        <div class="rp-field-card rp-message-card">
          <div class="rp-field-top">
            <label for="rpMessageInput" class="rp-field-label">留一句祝福</label>
            <span class="rp-char-counter"><span data-char-count>0</span>/100</span>
          </div>
          <div class="rp-input-row rp-message-row">
            <input id="rpMessageInput" name="message" maxlength="100" placeholder="恭喜发财，大吉大利！" autocomplete="off">
          </div>
          <div class="rp-quick-tags rp-wish-tags" role="group" aria-label="快捷祝福语">
            <button type="button" class="rp-chip rp-wish-chip" data-wish="恭喜发财，大吉大利！">恭喜发财，大吉大利！</button>
            <button type="button" class="rp-chip rp-wish-chip" data-wish="天天 AC，稳稳上岸！">天天 AC，稳稳上岸！</button>
            <button type="button" class="rp-chip rp-wish-chip" data-wish="手气爆棚，把把保 AC！">手气爆棚，把把保 AC！</button>
            <button type="button" class="rp-chip rp-wish-chip" data-wish="队友们辛苦了，冲冲冲！">队友们辛苦了，冲冲冲！</button>
          </div>
        </div>

        <div class="rp-field-card rp-expiry-card">
          <div class="rp-field-top">
            <label for="rpExpirySelect" class="rp-field-label">有效期</label>
            <span class="rp-field-sub">到期未领自动退还</span>
          </div>
          <div class="rp-input-row rp-expiry-row">
            <span class="rp-expiry-icon" aria-hidden="true">⏱️</span>
            <select id="rpExpirySelect" name="expiry" aria-label="红包有效期">
              <option value="86400" selected>24 小时（推荐）</option>
              <option value="3600">1 小时（限时手速）</option>
              <option value="604800">7 天</option>
              <option value="unlimited">无限期（领完为止）</option>
              <option value="custom">自定义截止时间</option>
            </select>
          </div>
          <div class="rp-compose-custom" hidden>
            <label class="rp-custom-expiry-label">
              <span>选择具体截止时间</span>
              <input name="expires_at" type="datetime-local" aria-label="红包截止时间">
            </label>
          </div>
          <p class="rp-field-hint" data-expiry-note>到期停止领取，未领金额自动退回钱包</p>
        </div>

        <p class="rp-compose-error" role="alert" aria-live="polite"></p>
      </div>

      <footer class="rp-compose-footer">
        <div class="rp-compose-payment">
          <span class="rp-payment-label">合计支付</span>
          <div class="rp-payment-figure">
            <strong data-pay-amount>0</strong>
            <span class="rp-payment-unit">RBC Coins</span>
          </div>
        </div>
        <button class="rp-send-button" type="submit">
          <span class="rp-send-btn-icon">🧧</span>
          <span class="rp-send-btn-text">塞钱进红包</span>
        </button>
      </footer>
    </form>`);
    el.classList.add('rp-send-dialog');
    const form = el.querySelector('form');
    let busy = false, pending = null;

    function updateForm() {
      const amountVal = form.elements.amount.value.trim();
      const amount = Number(amountVal) || 0;
      const countVal = form.elements.count.value.trim();
      const count = Number(countVal) || 0;
      const equal = form.elements.kind.value === 'equal';
      const balance = Number(state?.balance || 0);

      // Total payment display
      form.querySelector('[data-pay-amount]').textContent = number(amount);

      // Character counter
      const msg = form.elements.message.value || '';
      form.querySelector('[data-char-count]').textContent = msg.length;

      // Note and validation
      const splitNote = form.querySelector('[data-split-note]');
      const countNote = form.querySelector('[data-count-note]');
      const submitBtn = form.querySelector('.rp-send-button');
      const submitText = form.querySelector('.rp-send-btn-text');

      let amountError = '';
      let countError = '';

      if (amountVal !== '' && amount <= 0) {
        amountError = '红包总金额必须为大于 0 的整数';
      } else if (amount > balance) {
        amountError = `可用余额不足（当前余额 ${number(balance)} Coins，差 ${number(amount - balance)} Coins）`;
      }

      if (countVal !== '' && count <= 0) {
        countError = '红包个数必须为正整数';
      } else if (amount > 0 && count > amount) {
        countError = '每份至少 1 Coin，红包个数不能超过总金额';
      } else if (equal && amount > 0 && count > 0) {
        if (amount % count !== 0) {
          countError = `普通红包需能整除（当前无法等额均分，余 ${amount % count} Coins）`;
        }
      }

      form.elements.amount.setCustomValidity(amountError);
      form.elements.count.setCustomValidity(countError);

      if (amountError) {
        splitNote.innerHTML = `<span class="rp-hint-warning">⚠️ ${esc(amountError)}</span>`;
      } else {
        splitNote.textContent = equal
          ? '普通红包每份等额分配，总金额需能被个数整除'
          : '拼手气红包随机分配金额，每份至少 1 RBC Coin';
      }

      if (countError) {
        countNote.innerHTML = `<span class="rp-hint-warning">⚠️ ${esc(countError)}</span>`;
      } else if (equal && amount > 0 && count > 0) {
        countNote.innerHTML = `✨ 每份等额 <strong>${number(amount / count)}</strong> RBC Coins`;
      } else {
        countNote.textContent = '';
      }

      // Quick amount chips active highlight
      form.querySelectorAll('[data-quick-amount]').forEach(chip => {
        chip.classList.toggle('active', Number(chip.dataset.quickAmount) === amount);
      });
      // Quick count chips active highlight
      form.querySelectorAll('[data-quick-count]').forEach(chip => {
        chip.classList.toggle('active', Number(chip.dataset.quickCount) === count);
      });

      // Expiry toggle
      const custom = form.elements.expiry.value === 'custom';
      form.querySelector('.rp-compose-custom').hidden = !custom;
      form.elements.expires_at.disabled = !custom;
      form.elements.expires_at.required = custom;
      const expiryNote = form.querySelector('[data-expiry-note]');
      expiryNote.textContent = form.elements.expiry.value === 'unlimited'
        ? '不限时间，等待每一份心意被团队成员收下'
        : '到期停止领取，未领取的金额将自动退回您的钱包';

      // Submit button state
      if (!busy) {
        if (amount > balance) {
          submitBtn.disabled = true;
          if (submitText) submitText.textContent = '余额不足';
        } else {
          submitBtn.disabled = false;
          if (submitText) submitText.textContent = '塞钱进红包';
        }
      }
    }

    form.addEventListener('click', event => {
      const chip = event.target.closest('button[type="button"]');
      if (!chip) return;
      if (chip.hasAttribute('data-fill-balance')) {
        form.elements.amount.value = state.balance;
        updateForm();
      } else if (chip.hasAttribute('data-quick-amount')) {
        form.elements.amount.value = chip.dataset.quickAmount;
        updateForm();
      } else if (chip.hasAttribute('data-quick-count')) {
        form.elements.count.value = chip.dataset.quickCount;
        updateForm();
      } else if (chip.hasAttribute('data-wish')) {
        form.elements.message.value = chip.dataset.wish;
        updateForm();
      }
    });

    form.addEventListener('input', updateForm);
    form.addEventListener('change', updateForm);
    updateForm();

    el.addEventListener('cancel', event => { if (busy) event.preventDefault(); });

    form.onsubmit = async event => {
      event.preventDefault();
      if (busy) return;
      const errorBox = form.querySelector('.rp-compose-error');
      errorBox.textContent = '';
      if (!pending) {
        updateForm();
        if (!form.reportValidity()) return;
        const fields = new FormData(form);
        const amount = Number(fields.get('amount'));
        const count = Number(fields.get('count'));
        if (!Number.isInteger(amount) || amount <= 0) {
          errorBox.textContent = '红包总金额必须为大于 0 的整数';
          return;
        }
        if (!Number.isInteger(count) || count <= 0) {
          errorBox.textContent = '红包个数必须为正整数';
          return;
        }
        if (amount < count) {
          errorBox.textContent = '每份红包至少需要 1 RBC Coin';
          return;
        }
        if (amount > state.balance) {
          errorBox.textContent = '可用余额不足';
          return;
        }
        const kind = fields.get('kind');
        if (kind === 'equal' && amount % count !== 0) {
          errorBox.textContent = '普通红包的总金额必须能被红包个数整除';
          return;
        }
        const expiry = fields.get('expiry');
        const expiresAt = expiry === 'unlimited' ? null : expiry === 'custom' ? new Date(fields.get('expires_at')) : new Date(Date.now() + Number(expiry) * 1000);
        if (expiresAt && (!Number.isFinite(expiresAt.getTime()) || expiresAt.getTime() <= Date.now())) {
          errorBox.textContent = '请选择晚于当前时间的截止时间';
          return;
        }
        pending = {
          amount,
          count,
          kind,
          message: fields.get('message'),
          expires_at: expiresAt ? expiresAt.toISOString() : null,
          request_id: uuid()
        };
      }
      busy = true;
      form.querySelectorAll('input,select,button').forEach(control => control.disabled = true);
      const submitText = form.querySelector('.rp-send-btn-text');
      if (submitText) submitText.textContent = '正在塞入…';
      try {
        const result = await api('', pending);
        sync(result.balance);
        el.close();
        if (active) {
          epoch++;
          loading = false;
          page = 1;
          await refresh();
        }
      } catch (error) {
        const uncertain = !error.status || error.status >= 500;
        errorBox.textContent = uncertain ? '尚未确认发送结果，请点击重试确认同一笔红包。' : error.message;
        if (!uncertain) pending = null;
        form.querySelectorAll('input,select').forEach(control => control.disabled = uncertain);
        if (!uncertain) updateForm();
        const submitBtn = form.querySelector('.rp-send-button');
        if (submitBtn) {
          submitBtn.disabled = false;
          if (submitText) submitText.textContent = uncertain ? '重试确认发送' : '塞钱进红包';
        }
      } finally {
        busy = false;
        form.querySelectorAll('button').forEach(control => control.disabled = false);
      }
    };
  }
  async function details(id) {
    const token = epoch;
    const el = dialog('<h2 id="rp-dialog-title">红包详情</h2><p role="status">正在打开红包…</p><button class="secondary-button" data-cancel>关闭</button>');
    let busy = false;
    el.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    function drawDetail(result, received = null) {
      const packet = result.packet;
      const myAmount = received ?? packet.my_amount;
      const claimed = myAmount != null;
      el.classList.toggle('rp-reveal-dialog', !claimed);
      if (!claimed) {
        el.innerHTML = `<div class="rp-cover"><button class="rp-cover-close" type="button" data-cancel aria-label="关闭红包">×</button>
          <div class="rp-cover-sender">${avatar(packet)}<strong>${esc(packet.sender)}</strong><span>给你发了一个${packet.kind === 'equal' ? '普通红包' : '拼手气红包'}</span></div>
          <h2 id="rp-dialog-title">${esc(packet.message)}</h2>
          <p class="rp-cover-state" role="status">${packet.is_expired ? '红包已过期，不能继续领取' : packet.is_exhausted ? '来晚一步，红包已领完' : '一份心意，等你拆开'}</p><p class="rp-cover-error" role="alert"></p>
          ${!packet.is_exhausted && !packet.is_expired ? `<button class="rp-open" type="button" data-claim aria-label="拆红包并领取" ${result.can_claim ? '' : 'disabled'}>拆</button>${result.can_claim ? '' : '<p class="rp-cover-state">当前身份或团队未开放领取</p>'}` : '<span class="rp-cover-empty" aria-hidden="true"></span>'}
          ${packet.best_luck?.length ? `<p class="rp-cover-best">手气最佳 · ${packet.best_luck.map(winner => esc(winner.name)).join('、')}</p>` : ''}<span class="rp-cover-footer">${packet.expires_at ? `有效至 ${esc(date(packet.expires_at))}` : '无限期 · RBC Coins 红包'}</span></div>`;
        return;
      }
      el.innerHTML = `<div class="rp-detail-header">${avatar(packet)}<p>${esc(packet.sender)} 的红包</p><h2 id="rp-dialog-title">${esc(packet.message)}</h2><span class="rp-detail-badge">✓ 已领取</span></div>
        <p class="rp-received" role="status">你领到了 <strong>${number(myAmount)}</strong> RBC Coins</p>
        <p class="rp-detail-summary">共 ${number(packet.total_amount)} Coins · 已领 ${packet.total_count - packet.remaining_count} / ${packet.total_count} 份 · 剩余 ${number(packet.remaining_amount)} Coins${packet.refunded_amount ? ` · 已退回 ${number(packet.refunded_amount)} Coins` : ''}${packet.refund_pending ? ' · 退款处理中' : ''}</p><p class="rp-detail-expiry">${packet.is_expired ? '已过期 · ' : ''}${packet.expires_at ? `有效至 ${esc(date(packet.expires_at))}` : '无限期'}</p>
        <p role="alert"></p><h3>领取记录</h3><div class="rp-claims">${(packet.claims || []).map(claim => `<div class="rp-claim"><div><strong>${esc(claim.name)}${claim.is_me ? '（我）' : ''}</strong>${claim.is_best_luck ? `<span class="rp-best-luck">♛ ${claim.is_tied_best ? '并列手气最佳' : '手气最佳'}</span>` : ''}<small>${esc(date(claim.created_at))}</small></div><strong>+${number(claim.amount)} <small>Coins</small></strong></div>`).join('')}</div><div class="extension-actions"><button class="secondary-button" type="button" data-cancel>关闭</button></div>`;
    }
    try {
      const result = await api('/' + id);
      if (!active || token !== epoch || !el.isConnected) return;
      drawDetail(result);
    } catch (error) {
      if (el.isConnected) el.querySelector('[role=status]').textContent = error.message;
    }
    el.addEventListener('click', async event => {
      const button = event.target.closest('[data-claim]');
      if (!button || button.disabled || busy) return;
      busy = true;
      el.querySelectorAll('button').forEach(control => control.disabled = true);
      try {
        const result = await api('/' + id + '/claim', {});
        sync(result.balance);
        if (!active || token !== epoch || !el.isConnected) return;
        el.querySelector('[role=alert]').textContent = `已领取 ${number(result.amount)} RBC Coins。`;
        button.remove();
        try { drawDetail(await api('/' + id), result.amount); }
        catch { /* Keep the successful receipt visible if refreshing details fails. */ }
        if (active) {
          epoch++;
          loading = false;
          await refresh();
        }
      } catch (error) {
        if (el.isConnected) {
          if (error.status === 409) {
            try { drawDetail(await api('/' + id)); } catch { /* Preserve the original error below. */ }
          }
          el.querySelector('[role=alert]').textContent = error.message;
        }
      } finally {
        busy = false;
        el.querySelectorAll('button').forEach(control => control.disabled = false);
      }
    });
  }
  function close() {
    active = false;
    epoch++;
    loading = false;
    clearInterval(timer);
    dialogs.forEach(el => el.close());
    root.hidden = true;
    document.getElementById('extensionResources').hidden = false;
  }
  function open() {
    window.PokerModule?.close();
    active = true;
    epoch++;
    page = 1;
    loading = false;
    state = null;
    document.getElementById('extensionResources').hidden = true;
    root.hidden = false;
    root.innerHTML = '<p role="status">正在加载红包广场…</p>';
    refresh();
    timer = setInterval(() => { if (!document.hidden && !dialogs.size) refresh(); }, 15000);
    window.scrollTo({top:0, behavior:'smooth'});
  }
  root.addEventListener('click', event => {
    const target = event.target.closest('button');
    if (!target || target.disabled) return;
    if (target.hasAttribute('data-back')) close();
    else if (target.hasAttribute('data-refresh')) refresh();
    else if (target.hasAttribute('data-send')) sendDialog();
    else if (target.hasAttribute('data-packet')) details(target.dataset.packet);
    else if (target.hasAttribute('data-page') && !loading) { page = Number(target.dataset.page); refresh(); }
  });
  window.RedPacketsModule = {open, close};
})();
