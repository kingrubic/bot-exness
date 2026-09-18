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

// Kế hoạch đa khung: WAIT phải nêu rõ đang chờ gì, không bịa entry/SL/TP.
const waitingInd = {
    analysis_tf: 'M15', setup_status: 'WATCHING_BUY', trade_bias: 'BUY',
    overall_trend: 'BULLISH', confidence: 62, atr: 4.36,
    atr_upper_band: 4364.71, atr_lower_band: 4355.99,
    support: { low: 4354.8, high: 4356.2 }, resistance: { low: 4364.2, high: 4366 },
    waiting_for: ['M15 đóng cửa trên 4364.20', 'H1 xác nhận TĂNG (hiện ĐI NGANG)'],
    entry: null, stop_loss: null, take_profit_1: null, take_profit_2: null, risk_reward: null,
    multi_tf: {
        entry_tf: 'M15', target_zone: '4356.20 - 4364.20',
        rows: [
            { timeframe: 'M15', trend: 'SIDEWAYS', confidence: 48, structure: 'RANGE', candles: 240, sufficient: true },
            { timeframe: 'H1', trend: 'BULLISH', confidence: 71, structure: 'HH/HL', candles: 240, sufficient: true },
            { timeframe: 'H4', trend: 'BULLISH', confidence: 83, structure: 'HH/HL', candles: 240, sufficient: true },
        ],
    },
};
const waitingCard = context._entryPlanBlock({ symbol: 'XAUUSD', timeframe: 'M15' }, waitingInd, null);
assert.ok(waitingCard.includes('Kế hoạch vào lệnh · M15'));
assert.ok(waitingCard.includes('WAIT'));
assert.ok(waitingCard.includes('Theo dõi BUY'));
assert.ok(waitingCard.includes('62/100'));
assert.ok(waitingCard.includes('M15 đóng cửa trên 4364.20'));
assert.ok(!waitingCard.includes('Entry kế hoạch'));
assert.ok(!waitingCard.includes('TP1 cấu trúc'));

const readyInd = Object.assign({}, waitingInd, {
    setup_status: 'BUY_READY', confidence: 81, waiting_for: [],
    entry: 4360.35, stop_loss: 4353.5, take_profit_1: 4372.4, take_profit_2: 4381,
    risk_reward_1: 1.34, risk_reward_2: 2.41, risk_reward: 2.41,
});
const readyCard = context._entryPlanBlock({ symbol: 'XAUUSD', timeframe: 'M15' }, readyInd, null);
assert.ok(readyCard.includes('Đủ điều kiện BUY'));
assert.ok(readyCard.includes('SL cấu trúc'));
assert.ok(readyCard.includes('4353.50'));
assert.ok(readyCard.includes('R:R tới TP1'));
assert.ok(readyCard.includes('1.34R'));
assert.ok(readyCard.includes('R:R tới TP2'));
assert.ok(readyCard.includes('2.41R'));

// Khối đa khung: mỗi khung một dòng, vùng S/R tách khỏi dải ATR.
const tfBlock = context._multiTfBlock(waitingInd);
['M15', 'H1', 'H4'].forEach(tf => assert.ok(tfBlock.includes(`>${tf}<`)));
assert.ok(tfBlock.includes('Xu hướng đa khung'));
assert.ok(!tfBlock.includes('DÀI HẠN'));
assert.ok(tfBlock.includes('Hỗ trợ (vùng)'));
assert.ok(tfBlock.includes('4354.80 - 4356.20'));
assert.ok(tfBlock.includes('Kháng cự (vùng)'));
assert.ok(tfBlock.includes('4364.20 - 4366.00'));
assert.ok(tfBlock.includes('ATR Upper Band'));
assert.ok(tfBlock.includes('4364.71'));
assert.ok(tfBlock.includes('ATR Lower Band'));
assert.ok(tfBlock.includes('4355.99'));

console.log('Dashboard entry checks passed');
