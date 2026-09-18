const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/admin.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function _escHtml('), source.indexOf('function renderBotThinkBoard(')), context);
const h = { bias: 'BULLISH', action: 'READY_TO_BUY', confidence: 78,
    trigger: 'Close=4300, EMA9>EMA21. EMA9=4299 | EMA21=4295', target_zone: '4304 - 4310' };
const rejected = context._horizonBlock('Ngắn hạn', h, true, false,
    {allowed: false, reason: 'Thiếu SL USD'});
assert.ok(rejected.includes('Thiếu SL USD'));
assert.ok(rejected.includes('78/100'));
assert.ok(!rejected.includes('78%'));
assert.ok(!rejected.includes('MARKET BUY ngay'));
const approved = context._horizonBlock('Ngắn hạn', h, true, false,
    {allowed: true, entry_price: 4300.02, stop_loss: 4290.02, target_price: 4315.02,
        target_profit_usd: 15, rr_ratio: 1.5, calculated_lot: .01, reason: 'Đạt kiểm tra'});
assert.ok(approved.includes('SL gửi sàn'));
assert.ok(approved.includes('4290.02'));
assert.ok(approved.includes('TP ròng USD (bot)'));
assert.ok(approved.includes('1.50R'));
console.log('Dashboard entry checks passed');
