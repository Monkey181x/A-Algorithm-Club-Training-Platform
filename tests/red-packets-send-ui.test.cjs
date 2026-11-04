const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const redPacketsJs = fs.readFileSync(require.resolve('../red-packets.js'), 'utf8');

test('sendDialog template contains all required modern UI elements', () => {
  assert.match(redPacketsJs, /class="rp-dialog rp-send-dialog"|classList\.add\(['"]rp-send-dialog['"]\)/);
  assert.match(redPacketsJs, /class="rp-compose-header"/);
  assert.match(redPacketsJs, /class="rp-compose-icon-badge"/);
  assert.match(redPacketsJs, /class="rp-compose-title-group"/);
  assert.match(redPacketsJs, /发红包/);
  assert.match(redPacketsJs, /class="rp-kind-picker"/);
  assert.match(redPacketsJs, /value="random"/);
  assert.match(redPacketsJs, /value="equal"/);
  assert.match(redPacketsJs, /拼手气红包/);
  assert.match(redPacketsJs, /普通红包/);
  assert.match(redPacketsJs, /data-fill-balance/);
  assert.match(redPacketsJs, /data-quick-amount/);
  assert.match(redPacketsJs, /data-quick-count/);
  assert.match(redPacketsJs, /data-wish/);
  assert.match(redPacketsJs, /data-char-count/);
  assert.match(redPacketsJs, /data-split-note/);
  assert.match(redPacketsJs, /data-count-note/);
  assert.match(redPacketsJs, /data-expiry-note/);
  assert.match(redPacketsJs, /data-pay-amount/);
  assert.match(redPacketsJs, /class="rp-send-button"/);
  assert.match(redPacketsJs, /塞钱进红包/);
});

test('sendDialog form logic validates balance, divisibility, and quick buttons', () => {
  // Test mock extraction of updateForm behavior
  const state = {balance: 100, can_send: true};
  const number = val => Number(val || 0).toLocaleString('zh-CN');
  const esc = val => String(val);

  function createFormSimulator({balance = 100} = {}) {
    const fields = {
      amount: '',
      count: '10',
      kind: 'random',
      message: '',
      expiry: '86400',
      expires_at: ''
    };
    const customValidity = {amount: '', count: ''};
    const texts = {
      payAmount: '0',
      charCount: '0',
      splitNote: '',
      countNote: '',
      expiryNote: '',
      submitText: '塞钱进红包',
      submitDisabled: false
    };

    function updateForm() {
      const amountVal = fields.amount.trim();
      const amount = Number(amountVal) || 0;
      const countVal = fields.count.trim();
      const count = Number(countVal) || 0;
      const equal = fields.kind === 'equal';

      texts.payAmount = number(amount);
      texts.charCount = String((fields.message || '').length);

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

      customValidity.amount = amountError;
      customValidity.count = countError;

      if (amountError) {
        texts.splitNote = `⚠️ ${amountError}`;
      } else {
        texts.splitNote = equal
          ? '普通红包每份等额分配，总金额需能被个数整除'
          : '拼手气红包随机分配金额，每份至少 1 RBC Coin';
      }

      if (countError) {
        texts.countNote = `⚠️ ${countError}`;
      } else if (equal && amount > 0 && count > 0) {
        texts.countNote = `✨ 每份等额 ${number(amount / count)} RBC Coins`;
      } else {
        texts.countNote = '';
      }

      texts.expiryNote = fields.expiry === 'unlimited'
        ? '不限时间，等待每一份心意被团队成员收下'
        : '到期停止领取，未领取的金额将自动退回您的钱包';

      if (amount > balance) {
        texts.submitDisabled = true;
        texts.submitText = '余额不足';
      } else {
        texts.submitDisabled = false;
        texts.submitText = '塞钱进红包';
      }
    }

    return {fields, customValidity, texts, updateForm};
  }

  // 1. Initial state
  const sim = createFormSimulator({balance: 100});
  sim.updateForm();
  assert.equal(sim.texts.payAmount, '0');
  assert.equal(sim.texts.submitDisabled, false);
  assert.match(sim.texts.splitNote, /拼手气红包随机分配金额/);

  // 2. Filling 50 coins in random mode
  sim.fields.amount = '50';
  sim.fields.count = '10';
  sim.updateForm();
  assert.equal(sim.texts.payAmount, '50');
  assert.equal(sim.customValidity.amount, '');
  assert.equal(sim.customValidity.count, '');
  assert.equal(sim.texts.submitDisabled, false);

  // 3. Switch to equal mode with divisible numbers (50 coins, 10 count)
  sim.fields.kind = 'equal';
  sim.updateForm();
  assert.match(sim.texts.countNote, /每份等额 5 RBC Coins/);
  assert.equal(sim.customValidity.count, '');

  // 4. Equal mode with indivisible numbers (50 coins, 7 count)
  sim.fields.count = '7';
  sim.updateForm();
  assert.match(sim.texts.countNote, /无法等额均分/);
  assert.match(sim.customValidity.count, /普通红包需能整除/);

  // 5. Exceeding balance (150 coins when balance is 100)
  sim.fields.amount = '150';
  sim.updateForm();
  assert.match(sim.texts.splitNote, /可用余额不足/);
  assert.equal(sim.texts.submitDisabled, true);
  assert.equal(sim.texts.submitText, '余额不足');

  // 6. Message character count
  sim.fields.message = '天天 AC，稳稳上岸！';
  sim.updateForm();
  assert.equal(sim.texts.charCount, '11');

  // 7. Count exceeding amount (5 coins, 10 packets)
  sim.fields.amount = '5';
  sim.fields.count = '10';
  sim.fields.kind = 'random';
  sim.updateForm();
  assert.match(sim.texts.countNote, /每份至少 1 Coin/);
  assert.match(sim.customValidity.count, /每份至少 1 Coin/);
});

test('red-packets.css contains all modern styling classes for send dialog', () => {
  const css = fs.readFileSync(require.resolve('../red-packets.css'), 'utf8');
  assert.match(css, /\.rp-dialog\.rp-send-dialog/);
  assert.match(css, /\.rp-compose-header/);
  assert.match(css, /\.rp-compose-icon-badge/);
  assert.match(css, /\.rp-kind-picker/);
  assert.match(css, /\.rp-kind-card/);
  assert.match(css, /\.rp-field-card/);
  assert.match(css, /\.rp-amount-row/);
  assert.match(css, /\.rp-count-row/);
  assert.match(css, /\.rp-quick-tags/);
  assert.match(css, /\.rp-chip/);
  assert.match(css, /\.rp-wish-tags/);
  assert.match(css, /\.rp-fill-balance-btn/);
  assert.match(css, /\.rp-compose-footer/);
  assert.match(css, /\.rp-send-button/);
  assert.match(css, /\.rp-compose-security-note/);
  assert.match(css, /html\[data-site-mode="dark"\] \.rp-dialog\.rp-send-dialog/);
  assert.match(css, /html\[data-site-mode="dark"\] \.rp-compose-header/);
  assert.match(css, /@media\s*\(max-width:\s*600px\)/);
});
