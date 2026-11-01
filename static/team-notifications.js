/* Team notification switches and encrypted credential configuration. */
window.TeamNotifications = (() => {
  const flags = [
    ['report_enabled', '举报', '成员提交举报后通知'],
    ['item_use_enabled', '商品使用', '消耗品、卡密核销及补签券使用后通知'],
    ['physical_purchase_enabled', '购买实物商品', '实物订单购买成功后通知'],
    ['qualification_enabled', '达标审核', '按设定时间提醒本周尚未达标的成员'],
  ];
  const days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  const json = (method, data) => ({method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(data)});
  async function render(host, teamId) {
    host.dataset.notificationTeam = String(teamId);
    host.innerHTML = '<p class="muted" role="status">正在加载通知配置…</p>';
    const current = () => host.isConnected && host.dataset.notificationTeam === String(teamId);
    const url = `/api/management/teams/${teamId}/notifications`;
    try {
      let saved = await communityRequest(url);
      if (!current()) return;
      host.innerHTML = `<form class="team-notification-form" data-notification-form>
        <div class="notification-intro"><div><h3>通知配置</h3><p class="muted">为当前团队选择需要发送的通知。</p></div><label class="notification-toggle-row notification-master"><span>启用通知</span><input class="nt-toggle" type="checkbox" name="enabled" ${saved.enabled ? 'checked' : ''} aria-controls="notificationDetails" aria-expanded="${saved.enabled}"></label></div>
        <div id="notificationDetails" data-notification-details ${saved.enabled ? '' : 'hidden'}>
          <fieldset class="nt-fieldset notification-section"><legend class="nt-fieldset-legend">api_token 密钥</legend>
            <label for="notificationToken">通知服务密钥</label><input class="nt-input" id="notificationToken" name="api_token" type="password" autocomplete="new-password" maxlength="4096" placeholder="${saved.has_api_token ? '留空保留已保存的密钥' : '填写至少 32 位密钥，或点击随机生成'}" aria-describedby="notificationTokenHint">
            <p class="muted" id="notificationTokenHint" data-token-hint>${saved.has_api_token ? '密钥已加密保存，留空保留。' : '尚未保存密钥。'}请与 AstrBot 插件中的 api_token 保持一致。</p>
            <div class="notification-actions"><button class="nt-btn secondary-button" type="button" data-generate-token>随机生成</button><button class="nt-btn secondary-button" type="button" data-reveal-token aria-pressed="false">显示密钥</button><button class="nt-btn secondary-button" type="button" data-save-token>保存密钥</button></div>
          </fieldset>
          <fieldset class="nt-fieldset notification-section"><legend class="nt-fieldset-legend">通知内容</legend>
            ${flags.map(([key,label,hint]) => `<label class="notification-toggle-row"><span><strong>${label}</strong><small>${hint}</small></span><input class="nt-toggle" name="${key}" type="checkbox" ${saved[key] ? 'checked' : ''}${key === 'qualification_enabled' ? ' aria-controls="notificationSchedule"' : ''}></label>`).join('')}
          </fieldset>
          <fieldset class="nt-fieldset notification-section" id="notificationSchedule" data-review-schedule ${saved.qualification_enabled ? '' : 'hidden'}><legend class="nt-fieldset-legend">审核时间</legend><div class="notification-schedule-fields"><label>每周<select class="nt-select" name="review_weekday">${days.map((day,i) => `<option value="${i}" ${saved.review_weekday === i ? 'selected' : ''}>${day}</option>`).join('')}</select></label><label>几点<input class="nt-input" name="review_time" type="time" step="60" value="${escapeHtml(saved.review_time)}" required></label></div><p class="muted">北京时间。积分小于 6 或有效提交小于 4 即列入名单；每个训练周提醒一次。</p></fieldset>
          ${saved.endpoint_configured ? '' : '<p class="notification-service-hint">通知服务地址尚未配置，请完成 AstrBot 接入后启用发送。</p>'}
        </div>
        <p class="notification-feedback" role="status" aria-live="polite" data-notification-feedback></p>
        <div class="notification-actions"><button class="nt-btn primary-button" type="submit">保存通知配置</button></div>
      </form>`;
      const form = host.querySelector('form');
      const token = form.elements.api_token;
      const feedback = (text, error = false) => {
        const el = form.querySelector('[data-notification-feedback]');
        el.textContent = text; el.dataset.error = String(error);
      };
      function updateVisibility() {
        const enabled = form.elements.enabled.checked;
        form.querySelector('[data-notification-details]').hidden = !enabled;
        form.elements.enabled.setAttribute('aria-expanded', String(enabled));
        form.querySelector('[data-review-schedule]').hidden = !form.elements.qualification_enabled.checked;
        form.elements.qualification_enabled.setAttribute('aria-expanded', String(form.elements.qualification_enabled.checked));
        form.elements.review_weekday.disabled = form.elements.review_time.disabled = !enabled || !form.elements.qualification_enabled.checked;
      }
      form.addEventListener('change', updateVisibility);
      updateVisibility();
      async function act(operation) {
        const buttons = [...form.querySelectorAll('button')];
        buttons.forEach(el => { el.disabled = true; });
        form.setAttribute('aria-busy', 'true'); feedback('正在处理…');
        try { await operation(); }
        catch (error) { if (current()) feedback(error.message || '保存失败，请重试', true); }
        finally { buttons.forEach(el => { el.disabled = false; }); form.removeAttribute('aria-busy'); }
      }
      form.querySelector('[data-generate-token]').onclick = () => act(async () => {
        const data = await communityRequest(url, json('POST', {action:'generate_token'}));
        if (!current()) return;
        token.value = data.api_token; token.type = 'text';
        form.querySelector('[data-reveal-token]').textContent = '隐藏密钥';
        form.querySelector('[data-reveal-token]').setAttribute('aria-pressed', 'true');
        feedback('已生成新密钥，尚未保存。请同步填写到 AstrBot 插件。'); token.focus(); token.select();
      });
      form.querySelector('[data-reveal-token]').onclick = event => {
        const reveal = token.type === 'password'; token.type = reveal ? 'text' : 'password';
        event.currentTarget.textContent = reveal ? '隐藏密钥' : '显示密钥';
        event.currentTarget.setAttribute('aria-pressed', String(reveal));
      };
      const validateToken = () => {
        if (token.value && (token.value.length < 32 || /\s/.test(token.value))) throw new Error('密钥至少 32 位，且不能包含空白字符。');
        if (!token.value && !saved.has_api_token) throw new Error('请先填写密钥或点击随机生成。');
      };
      form.querySelector('[data-save-token]').onclick = () => act(async () => {
        validateToken();
        const data = await communityRequest(url, json('PUT', {api_token: token.value}));
        if (!current()) return;
        saved = data; form.querySelector('[data-token-hint]').textContent = '密钥已加密保存。请与 AstrBot 插件中的 api_token 保持一致。';
        feedback('密钥已保存。');
      });
      form.addEventListener('submit', event => {
        event.preventDefault();
        act(async () => {
          if (form.elements.enabled.checked || token.value) validateToken();
          const data = {enabled: form.elements.enabled.checked, api_token: token.value,
            review_weekday: Number(form.elements.review_weekday.value), review_time: form.elements.review_time.value};
          flags.forEach(([key]) => { data[key] = form.elements[key].checked; });
          saved = await communityRequest(url, json('PUT', data));
          if (!current()) return;
          feedback('通知配置已保存。'); showToast('通知配置已保存');
        });
      });
    } catch (error) {
      if (!current()) return;
      host.replaceChildren();
      const text = document.createElement('p'); text.setAttribute('role','alert'); text.textContent = error.message || '通知配置加载失败';
      const retry = document.createElement('button'); retry.type='button'; retry.className='secondary-button'; retry.textContent='重试'; retry.onclick=()=>render(host,teamId);
      host.append(text, retry);
    }
  }
  return {render};
})();
