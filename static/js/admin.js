/**
 * EXNESS AUTO-TRADE AI PLATFORM - ADMIN JAVASCRIPT ENGINE
 * Handles AJAX CRUD, Live Realtime Polling, Chart.js, Notifications & Modals
 */

// CSRF Helper for all AJAX mutations
function getCsrfToken() {
    let cookieValue = '';
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, 10) === ('csrftoken=')) {
                cookieValue = decodeURIComponent(cookie.substring(10));
                break;
            }
        }
    }
    return cookieValue;
}

function apiFetch(url, options = {}) {
    const headers = Object.assign({
        'X-CSRFToken': getCsrfToken(),
        'X-Requested-With': 'XMLHttpRequest',
    }, options.headers || {});
    return fetch(url, Object.assign({ credentials: 'same-origin' }, options, { headers }));
}

function fetchLiveTicks() {
    /* Live feed already polls /api/live-ticks/; keep stub so callers don't throw. */
}

function showQuickTradeAlert(ok, message) {
    const alertBox = document.getElementById('quick-trade-alert');
    if (!alertBox) return;
    alertBox.classList.remove('d-none', 'alert-danger', 'alert-success');
    alertBox.classList.add(ok ? 'alert-success' : 'alert-danger');
    alertBox.textContent = message || '';
}

function unwrapApiList(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.results)) return payload.results;
    if (payload && Array.isArray(payload.wallets)) return payload.wallets;
    if (payload && Array.isArray(payload.symbols)) return payload.symbols;
    return [];
}

function humanizeApiError(text, status) {
    const raw = String(text || '');
    if (/^\s*</.test(raw) || /OperationalError|database is locked|DOCTYPE html/i.test(raw)) {
        return 'Database đang bận. Lệnh có thể đã vào MT5 — kiểm tra bảng vị thế rồi thử lại nếu chưa thấy.';
    }
    return raw.slice(0, 180) || `HTTP ${status}`;
}

async function parseApiJson(res) {
    const text = await res.text();
    if (!text) return {};
    try {
        const data = JSON.parse(text);
        if (data && typeof data.error === 'string' && /DOCTYPE html|OperationalError/i.test(data.error)) {
            data.error = humanizeApiError(data.error, res.status);
        }
        return data;
    } catch (e) {
        return { success: false, error: humanizeApiError(text, res.status) };
    }
}

// Global State
let walletsData = [];
let symbolsData = [];
let cachedSymbols = [];
let countdownSeconds = 5;
let pollingInterval = null;
let isPolling = false;
let uiPointerBusy = false;
let uiPointerBusyTimer = null;
let lastHistorySig = '';
let lastPlansSig = '';

function setUiPointerBusy(on) {
    if (on) {
        uiPointerBusy = true;
        if (uiPointerBusyTimer) {
            clearTimeout(uiPointerBusyTimer);
            uiPointerBusyTimer = null;
        }
        return;
    }
    if (uiPointerBusyTimer) clearTimeout(uiPointerBusyTimer);
    uiPointerBusyTimer = setTimeout(() => {
        uiPointerBusy = false;
        uiPointerBusyTimer = null;
    }, 400);
}

document.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.btn-action-icon, button.btn-outline-danger, #tbody-positions, #tbody-plans, #tbody-wallets, #modal-quick-trade')) {
        setUiPointerBusy(true);
    }
});
document.addEventListener('pointerup', () => setUiPointerBusy(false));
document.addEventListener('pointercancel', () => setUiPointerBusy(false));
document.addEventListener('click', (e) => {
    const closeBtn = e.target.closest('[data-action="close-pos"]');
    if (closeBtn) {
        e.preventDefault();
        e.stopPropagation();
        closeSinglePosition(closeBtn.dataset.id, closeBtn.dataset.ticket);
        return;
    }
    const delPlan = e.target.closest('[data-action="delete-plan"]');
    if (delPlan) {
        e.preventDefault();
        e.stopPropagation();
        deleteTradingPlan(delPlan.dataset.id);
    }
});

// DOM Ready
document.addEventListener('DOMContentLoaded', () => {
    initGlobalEventListeners();
    initOverviewLiveFeed();
    initWalletsPage();
    initSymbolsPage();
    checkUnresolvedErrors();
    // Poll notifications & error logs continuously every 4 seconds
    setInterval(checkUnresolvedErrors, 4000);
    initAllSelect2();
});

/* ==================== SELECT2 INITIALIZER ==================== */
function initAllSelect2(context) {
    if (typeof jQuery !== 'undefined' && typeof jQuery.fn.select2 !== 'undefined') {
        const selector = context ? jQuery(context).find('select:not(.no-select2)') : jQuery('select:not(.no-select2)');
        selector.each(function() {
            const $el = jQuery(this);
            const modal = $el.closest('.modal');
            const hasModal = modal.length > 0;
            const placeholder = $el.attr('placeholder') || $el.find('option:first').text() || 'Chọn một mục...';
            
            // Check if already initialized, avoid duplicate wrapper
            if ($el.hasClass('select2-hidden-accessible')) {
                $el.select2('destroy');
            }

            $el.select2({
                width: '100%',
                dropdownParent: hasModal ? modal : jQuery(document.body),
                placeholder: placeholder,
                allowClear: false
            });
        });
    }
}

/* ==================== GLOBAL EVENT LISTENERS ==================== */
function initGlobalEventListeners() {
    // Re-init Select2 whenever any Bootstrap modal is shown
    if (typeof jQuery !== 'undefined') {
        jQuery(document).on('shown.bs.modal', '.modal', function () {
            initAllSelect2(this);
        });
    }

    // Mobile & Desktop Sidebar Toggle Handler
    document.querySelectorAll('.js-sidebar-toggle').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const isMobile = window.innerWidth < 992;
            if (isMobile) {
                document.body.classList.toggle('sidebar-open');
                let backdrop = document.querySelector('.sidebar-backdrop');
                if (!backdrop) {
                    backdrop = document.createElement('div');
                    backdrop.className = 'sidebar-backdrop';
                    backdrop.addEventListener('click', () => {
                        document.body.classList.remove('sidebar-open');
                    });
                    document.body.appendChild(backdrop);
                }
            } else {
                document.body.classList.toggle('sidebar-collapsed');
            }
        });
    });

    // Auto-close mobile sidebar ONLY when clicking direct navigation destination links (exclude parent dropdown toggles)
    document.querySelectorAll('#main-sidebar .navbar__list a, #main-sidebar .navbar__sub-list a').forEach(link => {
        link.addEventListener('click', (e) => {
            const href = link.getAttribute('href');
            // If it's a parent toggle with href="#" or has class "js-arrow", DO NOT close the mobile drawer
            if (!href || href === '#' || link.classList.contains('js-arrow')) {
                return;
            }
            if (window.innerWidth < 992) {
                document.body.classList.remove('sidebar-open');
            }
        });
    });

    // Refresh Overview
    document.getElementById('btn-refresh-overview')?.addEventListener('click', () => {
        fetchLiveTicks();
    });
}

async function closeAllPositionsPrompt() {
    const n = (cachedOverviewPositions || []).length;
    if (!n) {
        showToast('Không có vị thế mở để đóng', 'error');
        return;
    }
    if (!confirm(`Đóng TOÀN BỘ ${n} vị thế đang mở trên Exness MT5?`)) {
        return;
    }
    const btn = document.getElementById('btn-close-all-positions');
    if (btn) btn.disabled = true;
    try {
        const res = await apiFetch('/api/positions/close-all/', { method: 'POST' });
        const data = await parseApiJson(res);
        if (data.success || data.message) {
            showToast(data.message || 'Đã gửi lệnh đóng toàn bộ', data.success ? 'success' : 'error');
            refreshAllData();
            fetchLiveTicks();
        } else {
            showToast(data.error || 'Lỗi đóng lệnh khẩn cấp', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    } finally {
        if (btn) btn.disabled = (cachedOverviewPositions || []).length === 0;
    }
}

// Number & Currency formatting helpers
function formatMoney(amount) {
    if (amount === undefined || amount === null || isNaN(amount)) return '$0.00';
    const num = Number(amount);
    return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPrice(price, category = '') {
    if (price === undefined || price === null || isNaN(price)) return '0.00';
    const num = Number(price);
    let decimals = 2;
    if (category === 'FOREX' || category === 'CURRENCY') {
        decimals = 5;
    } else if (category === 'METALS') {
        decimals = 3;
    } else if (category === 'CRYPTO') {
        decimals = 2;
    }
    return num.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatPnl(amount, options = {}) {
    if (amount === undefined || amount === null || isNaN(amount)) return '$0.00';
    const num = Number(amount);
    const isPos = num > 0;
    const isNeg = num < 0;
    const absVal = Math.abs(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    
    let sign = '';
    let arrow = '';
    let colorClass = 'text-muted';
    let badgeClass = 'bg-light text-secondary border';

    if (isPos) {
        sign = '+';
        arrow = '<i class="fa-solid fa-arrow-trend-up me-1"></i>';
        colorClass = 'text-success font-weight-bold';
        badgeClass = 'badge-profit';
    } else if (isNeg) {
        sign = '-';
        arrow = '<i class="fa-solid fa-arrow-trend-down me-1"></i>';
        colorClass = 'text-danger font-weight-bold';
        badgeClass = 'badge-loss';
    }

    if (options.asBadge) {
        return `<span class="badge ${badgeClass} px-2 py-1">${arrow}${sign}$${absVal}</span>`;
    }
    return `<span class="${colorClass}">${arrow}${sign}$${absVal}</span>`;
}

// Track previous price for each symbol to trigger tick up/down flash, old price comparison and arrows
const prevPriceStore = {};

function formatPosPrice(currentPrice, symbol) {
    const curr = Number(currentPrice);
    const sym = String(symbol || '');
    let decimals = 2;
    if (sym.includes('EUR') || sym.includes('GBP') || sym.includes('USDJPY')) decimals = 5;
    else if (sym.includes('XAU') || sym.includes('XAG')) decimals = 2;
    return curr.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function renderLivePriceWithTick(symbolKey, currentPrice, originalSymbol = '') {
    const prev = prevPriceStore[symbolKey];
    const curr = Number(currentPrice);
    prevPriceStore[symbolKey] = curr;

    const sym = originalSymbol || symbolKey;
    let decimals = 2;
    if (sym.includes('EUR') || sym.includes('GBP') || sym.includes('USDJPY')) {
        decimals = 5;
    }
    const currFormatted = curr.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

    if (prev === undefined || prev === 0 || prev === curr) {
        return `<span class="font-weight-bold text-dark">$${currFormatted}</span>`;
    }

    const diff = curr - prev;
    const isUp = diff > 0;
    const flashClass = isUp ? 'flash-price-up text-success' : 'flash-price-down text-danger';
    return `<span class="font-weight-bold ${flashClass}">$${currFormatted}</span>`;
}

/* ==================== BOT VS USER REPORT — ví đang kích hoạt ==================== */
let currentReportScope = '';
let lastLiveOverviewData = null;
let cachedGlobalOverview = null;

function syncActiveWalletScope(data) {
    const wallets = (data && Array.isArray(data.wallets)) ? data.wallets : (Array.isArray(walletsData) ? walletsData : []);
    const ov = data && data.overview ? data.overview : {};
    const active = wallets.find(w => w.is_active) || wallets.find(w => String(w.id) === String(ov.active_wallet_id));
    const id = ov.active_wallet_id || (active && active.id) || '';
    const name = ov.active_wallet_name || (active && active.name) || '';
    const login = active ? (active.mt5_login || '') : '';
    currentReportScope = id ? String(id) : '';

    const label = active
        ? `💼 ${name} (#${login})`
        : 'Chưa có ví kích hoạt';
    const reportEl = document.getElementById('report-wallet-filter');
    if (reportEl) {
        reportEl.innerHTML = active
            ? `<option value="${active.id}" selected>${label}</option>`
            : '<option value="">Chưa có ví kích hoạt</option>';
        reportEl.disabled = true;
    }
    const histEl = document.getElementById('filter-hist-wallet');
    if (histEl) {
        histEl.innerHTML = active
            ? `<option value="${active.id}" selected>Ví #${login} (${name})</option>`
            : '<option value="">Chưa có ví kích hoạt</option>';
        histEl.disabled = true;
    }
    return active;
}

function onReportWalletFilterChanged(val) {
    currentReportScope = String(val || '').trim();
    updateOverviewReportDisplay();
}

async function updateOverviewReportDisplay() {
    let bm = null;
    let um = null;
    let scopeLabel = 'Chưa có ví kích hoạt';

    const wList = (lastLiveOverviewData && Array.isArray(lastLiveOverviewData.wallets) && lastLiveOverviewData.wallets.length > 0)
        ? lastLiveOverviewData.wallets
        : (Array.isArray(walletsData) ? walletsData : []);
    let targetWallet = wList.find(w => w.is_active)
        || wList.find(w => String(w.id) === String(currentReportScope));

    if (targetWallet && targetWallet.bot_metrics && targetWallet.user_metrics) {
        bm = targetWallet.bot_metrics;
        um = targetWallet.user_metrics;
        scopeLabel = targetWallet.name || `Ví #${targetWallet.mt5_login || targetWallet.id}`;
        currentReportScope = String(targetWallet.id);
    } else if (lastLiveOverviewData && lastLiveOverviewData.overview) {
        bm = lastLiveOverviewData.overview.bot_metrics;
        um = lastLiveOverviewData.overview.user_metrics;
        scopeLabel = lastLiveOverviewData.overview.active_wallet_name || scopeLabel;
    } else if (currentReportScope) {
        try {
            const res = await fetch(`/api/wallets/${currentReportScope}/`);
            if (res.ok) {
                const data = await res.json();
                if (data && data.report) {
                    bm = data.report.bot_metrics;
                    um = data.report.user_metrics;
                    scopeLabel = data.report.name || scopeLabel;
                }
            }
        } catch (err) {}
    }

    const botScopeEl = document.getElementById('ov-bot-scope-label');
    if (botScopeEl) botScopeEl.innerHTML = `<i class="fa-solid fa-bolt text-success me-1"></i>${scopeLabel}`;
    const userScopeEl = document.getElementById('ov-user-scope-label');
    if (userScopeEl) userScopeEl.innerHTML = `<i class="fa-solid fa-bolt text-primary me-1"></i>${scopeLabel}`;

    // Cập nhật số liệu BOT
    if (bm) {
        const bTrades = document.getElementById('ov-bot-trades');
        if (bTrades) bTrades.innerText = bm.total_trades ?? 0;
        const bWins = document.getElementById('ov-bot-wins');
        if (bWins) bWins.innerText = bm.winning_trades ?? 0;
        const bLosses = document.getElementById('ov-bot-losses');
        if (bLosses) bLosses.innerText = bm.losing_trades ?? 0;
        const bWinrate = document.getElementById('ov-bot-winrate');
        if (bWinrate) bWinrate.innerText = `${bm.win_rate ?? 0}%`;
        const bVol = document.getElementById('ov-bot-vol');
        if (bVol) bVol.innerText = `${bm.total_volume ?? 0} Lot`;
        const bProfit = document.getElementById('ov-bot-profit');
        if (bProfit) bProfit.innerHTML = formatPnl(bm.total_profit ?? 0);
        const bToday = document.getElementById('ov-bot-today');
        if (bToday) bToday.innerHTML = formatPnl(bm.today_pnl ?? 0);
    }

    // Cập nhật số liệu USER
    if (um) {
        const uTrades = document.getElementById('ov-user-trades');
        if (uTrades) uTrades.innerText = um.total_trades ?? 0;
        const uWins = document.getElementById('ov-user-wins');
        if (uWins) uWins.innerText = um.winning_trades ?? 0;
        const uLosses = document.getElementById('ov-user-losses');
        if (uLosses) uLosses.innerText = um.losing_trades ?? 0;
        const uWinrate = document.getElementById('ov-user-winrate');
        if (uWinrate) uWinrate.innerText = `${um.win_rate ?? 0}%`;
        const uVol = document.getElementById('ov-user-vol');
        if (uVol) uVol.innerText = `${um.total_volume ?? 0} Lot`;
        const uProfit = document.getElementById('ov-user-profit');
        if (uProfit) uProfit.innerHTML = formatPnl(um.total_profit ?? 0);
        const uToday = document.getElementById('ov-user-today');
        if (uToday) uToday.innerHTML = formatPnl(um.today_pnl ?? 0);
    }
}

/* ==================== REALTIME LIVE FEED (DIRECT JS HTTP POLLING 1S) ==================== */
function initOverviewLiveFeed() {
    if (!document.getElementById('kpi-balance') && !document.getElementById('live-symbols-container') && !document.getElementById('tbody-wallets') && !document.getElementById('tbody-positions')) return;

    // Bắt đầu luồng HTTP Polling tự động làm mới mỗi 1.0s siêu tốc, mượt mà và ổn định
    startPollingLiveTicks();
}

function startPollingLiveTicks() {
    if (isPolling) return;
    isPolling = true;

    async function continuousLoop() {
        if (!isPolling) return;
        try {
            const res = await fetch('/api/live-ticks/');
            if (res.ok) {
                const data = await res.json();
                handleLiveTicksData(data);
            }
        } catch (e) {}
        setTimeout(continuousLoop, 120);
    }

    continuousLoop();
}

function handleLiveTicksData(data) {
    if (!data) return;

    // 1. Update Top KPIs
    const ov = data.overview;
    if (ov) {
        const kpiBal = document.getElementById('kpi-balance');
        if (kpiBal) kpiBal.innerText = formatMoney(ov.total_balance);

        const kpiEq = document.getElementById('kpi-equity');
        if (kpiEq) kpiEq.innerText = formatMoney(ov.total_equity);

        const kpiFloat = document.getElementById('kpi-floating');
        if (kpiFloat) kpiFloat.innerHTML = formatPnl(ov.total_floating_pnl);

        const kpiPosCount = document.getElementById('kpi-positions-count');
        if (kpiPosCount) kpiPosCount.innerText = data.positions ? data.positions.length : (ov.active_positions_count ?? 0);

        const kpiToday = document.getElementById('kpi-today');
        if (kpiToday) {
            const v = Number(ov.total_today_pnl).toFixed(2);
            if (kpiToday.dataset.val !== v) {
                kpiToday.dataset.val = v;
                kpiToday.innerHTML = formatPnl(ov.total_today_pnl);
            }
        }

        const kpiWallets = document.getElementById('kpi-wallets-count');
        if (kpiWallets) kpiWallets.innerText = ov.active_wallet_name || '—';

        const kpiWr = document.getElementById('kpi-winrate');
        if (kpiWr) kpiWr.innerText = `${ov.overall_winrate}%`;
    }

    // 1.1 Update BOT vs USER Report by Scope (All or per Wallet)
    lastLiveOverviewData = data;
    syncActiveWalletScope(data);
    if (typeof updateOverviewReportDisplay === 'function') {
        updateOverviewReportDisplay();
    }

    // 2. Update Live Market Ticker Cards (Clean separated Price and Diff Badge)
    if (data.symbols) {
        data.symbols.forEach(s => {
            const priceEl = document.getElementById(`sym-price-${s.symbol}`) || document.getElementById(`live-price-${s.symbol}`);
            const diffEl = document.getElementById(`sym-diff-${s.symbol}`) || document.getElementById(`live-diff-${s.symbol}`);
            const spreadEl = document.getElementById(`sym-spread-${s.symbol}`) || document.getElementById(`live-spread-${s.symbol}`);

            const prev = prevPriceStore['card_' + s.symbol];
            const curr = Number(s.current_price);
            prevPriceStore['card_' + s.symbol] = curr;

            let decimals = 2;
            if (s.symbol.includes('EUR') || s.symbol.includes('GBP') || s.symbol.includes('USDJPY')) {
                decimals = 5;
            } else if (s.category === 'METALS') {
                decimals = 3;
            }
            const currFormatted = curr.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

            if (priceEl) {
                priceEl.innerText = '$' + currFormatted;
                if (prev !== undefined && prev !== 0 && prev !== curr) {
                    const diff = curr - prev;
                    const isUp = diff > 0;
                    const diffFormatted = Math.abs(diff).toFixed(decimals);

                    priceEl.classList.remove('flash-price-up', 'flash-price-down');
                    void priceEl.offsetWidth; // trigger reflow for clean re-animation
                    priceEl.classList.add(isUp ? 'flash-price-up' : 'flash-price-down');

                    if (diffEl) {
                        diffEl.innerHTML = isUp 
                            ? `<span class="diff-badge-up"><i class="fa-solid fa-arrow-trend-up"></i> +$${diffFormatted}</span>`
                            : `<span class="diff-badge-down"><i class="fa-solid fa-arrow-trend-down"></i> -$${diffFormatted}</span>`;
                    }
                }
            }

            if (spreadEl && s.spread !== undefined) spreadEl.innerText = s.spread;
        });
    }

    // 3. Update Active Positions (patch in-place so close buttons stay clickable)
    if (data.positions) {
        renderOverviewPositions(data.positions);
    }

    // 4. Update Wallets if on Wallets Page or Dashboard
    if (data.wallets && Array.isArray(data.wallets) && data.wallets.length > 0) {
        if (document.getElementById('table-wallets') || document.getElementById('tbody-wallets')) {
            renderWalletsTable(data.wallets);
        }
        syncActiveWalletScope(data);
    }

    if (data.plans) {
        const sig = (data.plans || []).map(p => p.id + ':' + p.status).join('|');
        if (sig !== lastPlansSig) {
            lastPlansSig = sig;
            renderOverviewPlans(data.plans);
        }
    }

    if (Array.isArray(data.history)) {
        const first = data.history[0];
        const last = data.history[data.history.length - 1];
        const sig = data.history.length + ':' + (first?.ticket || '') + ':' + (last?.ticket || '') + ':' + (first?.pnl ?? '') + ':' + (first?.closed_at || '');
        if (sig !== lastHistorySig) {
            lastHistorySig = sig;
            rawOverviewHistory = data.history;
            filterOverviewHistory(false);
        }
    }

    const timerEl = document.getElementById('countdown-timer');
    if (timerEl) {
        timerEl.innerHTML = `<span class="realtime-live-dot"></span> <span class="text-success fw-bold">AUTO LIVE (1s)</span>`;
    }
}

async function refreshOverviewDeepData() {
    try {
        const walletsRes = await fetch('/api/wallets/');
        const wallets = await walletsRes.json();
        const list = Array.isArray(wallets) ? wallets : [];
        const current = list.find(w => w.is_active) || list[0];

        if (!current) {
            renderOverviewPlans([]);
            rawOverviewHistory = [];
            filterOverviewHistory(false);
            return;
        }

        const detailRes = await fetch(`/api/wallets/${current.id}/`);
        const detail = await detailRes.json();
        const plans = (detail.plans || []).map(pl => ({...pl, wallet_name: current.name, wallet_id: current.id}));
        const history = (detail.history || []).map(h => ({...h, wallet_name: current.name, wallet_id: current.id}));

        renderOverviewPlans(plans);
        rawOverviewHistory = history;
        currentReportScope = String(current.id);
        syncActiveWalletScope({ wallets: list, overview: { active_wallet_id: current.id, active_wallet_name: current.name } });
        filterOverviewHistory(false);
    } catch (e) {}
}

let currentOverviewPosPage = 1;
let currentOverviewPlansPage = 1;
let currentOverviewHistPage = 1;
const ADMIN_PAGE_SIZE = 20;

let cachedOverviewPositions = [];
let cachedOverviewPlans = [];
let cachedOverviewHistory = [];

function changeOverviewPosPage(page) {
    currentOverviewPosPage = page;
    renderOverviewPositions(cachedOverviewPositions);
}

function changeOverviewPlansPage(page) {
    currentOverviewPlansPage = page;
    renderOverviewPlans(cachedOverviewPlans);
}

function changeOverviewHistPage(page) {
    currentOverviewHistPage = page;
    renderOverviewHistory(cachedOverviewHistory);
}

function updatePositionsTotals(positions) {
    const rows = positions || [];
    const total = rows.reduce((sum, p) => sum + (Number(p.floating_pnl) || 0), 0);
    const html = formatPnl(total, { asBadge: true });
    const headerEl = document.getElementById('positions-total-pnl');
    if (headerEl) headerEl.innerHTML = html;
    const btn = document.getElementById('btn-close-all-positions');
    if (btn) btn.disabled = rows.length === 0;
}

function renderOverviewPositions(positions) {
    cachedOverviewPositions = positions || [];
    const tbody = document.getElementById('tbody-positions');
    const badgeCount = document.getElementById('badge-open-count');
    if (!tbody) return;

    if (badgeCount) badgeCount.innerText = cachedOverviewPositions.length;
    updatePositionsTotals(cachedOverviewPositions);

    if (cachedOverviewPositions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12" class="text-center py-4 text-muted"><i class="fa-solid fa-circle-check text-success me-2"></i> Không có vị thế mở nào đang chạy</td></tr>`;
        tbody.dataset.sig = '';
        renderPaginationComponent('pagination-overview-positions', 0, 1, ADMIN_PAGE_SIZE, 'changeOverviewPosPage');
        return;
    }

    const totalPages = Math.ceil(cachedOverviewPositions.length / ADMIN_PAGE_SIZE) || 1;
    if (currentOverviewPosPage > totalPages) currentOverviewPosPage = totalPages;
    if (currentOverviewPosPage < 1) currentOverviewPosPage = 1;

    const startIndex = (currentOverviewPosPage - 1) * ADMIN_PAGE_SIZE;
    const paged = cachedOverviewPositions.slice(startIndex, startIndex + ADMIN_PAGE_SIZE);
    const sig = currentOverviewPosPage + '|' + paged.map(p => String(p.ticket)).sort().join(',');
    const rowsReady = paged.every(p => document.getElementById('pos-row-' + p.ticket));
    const skipRebuild = uiPointerBusy || (rowsReady && tbody.dataset.sig === sig);

    if (skipRebuild) {
        patchOverviewPositionRows(paged);
        updatePositionsTotals(cachedOverviewPositions);
        return;
    }

    tbody.dataset.sig = sig;
    tbody.innerHTML = paged.map(p => {
        const isBot = (p.source === 'BOT');
        const tagBadge = isBot 
            ? `<span class="badge bg-primary text-white font-monospace shadow-sm" style="font-size: 0.72rem; letter-spacing: 0.5px;"><i class="fa-solid fa-robot me-1"></i>BOT</span>` 
            : `<span class="badge text-white font-monospace shadow-sm" style="background-color: #6f42c1; font-size: 0.72rem; letter-spacing: 0.5px;"><i class="fa-solid fa-user me-1"></i>USER</span>`;

        return `
        <tr id="pos-row-${p.ticket}">
            <td class="font-monospace font-weight-bold">#${p.ticket}</td>
            <td>${tagBadge}</td>
            <td><span class="badge bg-light text-dark border font-weight-bold">${p.wallet_name}</span></td>
            <td><span class="font-weight-bold text-dark">${p.symbol}</span></td>
            <td><span class="badge ${p.position_type === 'BUY' ? 'badge-buy' : 'badge-sell'}">${p.position_type}</span></td>
            <td class="font-weight-bold">${p.lot_size} Lot</td>
            <td>$${p.open_price}</td>
            <td class="font-weight-bold pos-cell-price" data-price=""><span class="pos-price-num font-weight-bold text-dark">$${formatPosPrice(p.current_price, p.symbol)}</span></td>
            <td class="pos-cell-sltp"><small class="text-muted">$${p.stop_loss} / $${p.take_profit}</small></td>
            <td class="pos-cell-pnl">
                ${formatPnl(p.floating_pnl, { asBadge: true })}
            </td>
            <td>
                ${p.is_breakeven_set ? '<span class="badge bg-info text-dark me-1">BE</span>' : ''}
                ${p.is_trailing ? '<span class="badge bg-warning text-dark">Trailing</span>' : ''}
            </td>
            <td class="text-end text-nowrap">
                <button type="button" class="btn btn-sm btn-outline-danger btn-action-icon" data-action="close-pos" data-id="${p.id}" data-ticket="${p.ticket}" title="Đóng Lệnh">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </td>
        </tr>
        `;
    }).join('');

    renderPaginationComponent('pagination-overview-positions', cachedOverviewPositions.length, currentOverviewPosPage, ADMIN_PAGE_SIZE, 'changeOverviewPosPage');
}

function patchOverviewPositionRows(paged) {
    paged.forEach(p => {
        const row = document.getElementById('pos-row-' + p.ticket);
        if (!row) return;
        const priceEl = row.querySelector('.pos-cell-price');
        const pnlEl = row.querySelector('.pos-cell-pnl');
        const slEl = row.querySelector('.pos-cell-sltp');
        if (priceEl) {
            const shown = formatPosPrice(p.current_price, p.symbol);
            let numEl = priceEl.querySelector('.pos-price-num');
            if (!numEl) {
                priceEl.innerHTML = `<span class="pos-price-num font-weight-bold text-dark">$${shown}</span>`;
                numEl = priceEl.querySelector('.pos-price-num');
                priceEl.dataset.shown = shown;
            } else if (priceEl.dataset.shown !== shown) {
                numEl.textContent = '$' + shown;
                priceEl.dataset.shown = shown;
            }
        }
        if (pnlEl) {
            const v = Number(p.floating_pnl).toFixed(2);
            if (pnlEl.dataset.val !== v) {
                pnlEl.dataset.val = v;
                pnlEl.innerHTML = formatPnl(p.floating_pnl, { asBadge: true });
            }
        }
        if (slEl) {
            const html = `<small class="text-muted">$${p.stop_loss} / $${p.take_profit}</small>`;
            if (slEl.dataset.val !== html) {
                slEl.dataset.val = html;
                slEl.innerHTML = html;
            }
        }
    });
}

function renderOverviewPlans(plans) {
    cachedOverviewPlans = plans || [];
    const tbody = document.getElementById('tbody-plans');
    if (!tbody) return;
    if (uiPointerBusy && tbody.querySelector('[data-action="delete-plan"]')) return;

    if (cachedOverviewPlans.length === 0) {
        tbody.innerHTML = `<tr><td colspan="12" class="text-center py-4 text-muted"><i class="fa-solid fa-brain text-warning me-2"></i> Chưa có kế hoạch AI nào được khởi tạo</td></tr>`;
        tbody.dataset.sig = '';
        renderPaginationComponent('pagination-overview-plans', 0, 1, ADMIN_PAGE_SIZE, 'changeOverviewPlansPage');
        return;
    }

    const totalPages = Math.ceil(cachedOverviewPlans.length / ADMIN_PAGE_SIZE) || 1;
    if (currentOverviewPlansPage > totalPages) currentOverviewPlansPage = totalPages;
    if (currentOverviewPlansPage < 1) currentOverviewPlansPage = 1;

    const startIndex = (currentOverviewPlansPage - 1) * ADMIN_PAGE_SIZE;
    const paged = cachedOverviewPlans.slice(startIndex, startIndex + ADMIN_PAGE_SIZE);
    const sig = currentOverviewPlansPage + '|' + paged.map(pl => String(pl.id)).join(',');
    const rowsReady = paged.every(pl => document.getElementById('plan-row-' + pl.id));
    if (rowsReady && tbody.dataset.sig === sig) {
        paged.forEach(pl => {
            const row = document.getElementById('plan-row-' + pl.id);
            if (!row) return;
            const priceEl = row.querySelector('.plan-entry');
            if (priceEl) {
                const shown = 'MARKET ' + pl.direction + ' @ $' + pl.entry_price;
                if (priceEl.dataset.val !== shown) {
                    priceEl.dataset.val = shown;
                    priceEl.textContent = shown;
                }
            }
            const statusEl = row.querySelector('.plan-status');
            if (statusEl && statusEl.dataset.st !== pl.status) {
                statusEl.dataset.st = pl.status;
                statusEl.textContent = pl.status_display || pl.status;
            }
        });
        return;
    }
    tbody.dataset.sig = sig;

    tbody.innerHTML = paged.map(pl => {
        let statusBadge = 'bg-secondary';
        if (pl.status === 'PENDING_TRIGGER') statusBadge = 'bg-warning text-dark';
        else if (pl.status === 'EXECUTING') statusBadge = 'bg-success';
        else if (pl.status === 'COMPLETED') statusBadge = 'bg-info';
        else if (pl.status === 'CANCELLED') statusBadge = 'bg-danger';

        return `
            <tr id="plan-row-${pl.id}">
                <td class="font-monospace font-weight-bold">#${pl.id}</td>
                <td><small class="font-weight-bold">${pl.wallet_name}</small></td>
                <td><b>${pl.symbol}</b> <small class="text-muted">[${pl.timeframe}]</small></td>
                <td><span class="badge ${pl.direction === 'BUY' ? 'badge-buy' : 'badge-sell'}">${pl.direction}</span></td>
                <td class="font-weight-bold text-primary font-monospace plan-entry" data-val="MARKET ${pl.direction} @ $${pl.entry_price}">MARKET ${pl.direction} @ $${pl.entry_price}</td>
                <td class="text-muted small">—</td>
                <td class="text-success small">Khi lãi ≥ min TP</td>
                <td><span class="badge bg-light text-dark border font-monospace">MARKET</span></td>
                <td><b>${pl.calculated_lot} Lot</b></td>
                <td><span class="badge ${statusBadge} plan-status" data-st="${pl.status}">${pl.status_display || pl.status}</span></td>
                <td><small class="text-muted text-truncate d-inline-block" style="max-width: 230px;" title="${pl.rationale}">${pl.rationale}</small></td>
                <td class="text-end text-nowrap">
                    <button type="button" class="btn btn-sm btn-outline-danger btn-action-icon" data-action="delete-plan" data-id="${pl.id}" title="Xóa Kế Hoạch Này">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    renderPaginationComponent('pagination-overview-plans', cachedOverviewPlans.length, currentOverviewPlansPage, ADMIN_PAGE_SIZE, 'changeOverviewPlansPage');
}

async function clearAllTradingPlansPrompt() {
    if (!confirm("Bạn có chắc chắn muốn xóa TOÀN BỘ kế hoạch giao dịch AI (Trading Plans) hiện tại?")) return;
    try {
        const res = await fetch('/api/plans/clear/', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || 'Đã xóa toàn bộ kế hoạch giao dịch AI!', 'success');
            refreshOverviewDeepData();
        } else {
            showToast(data.error || 'Lỗi khi xóa kế hoạch AI', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

async function deleteTradingPlan(planId) {
    if (!confirm(`Bạn có chắc chắn muốn xóa kế hoạch AI #${planId}?`)) return;
    try {
        const res = await fetch(`/api/plans/${planId}/delete/`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || `Đã xóa kế hoạch AI #${planId}!`, 'success');
            refreshOverviewDeepData();
        } else {
            showToast(data.error || 'Lỗi khi xóa kế hoạch AI', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

let rawOverviewHistory = [];

function filterOverviewHistory(resetPage = true) {
    if (resetPage) currentOverviewHistPage = 1;
    const srcFilter = document.getElementById('filter-hist-source')?.value || 'ALL';
    const walletFilter = document.getElementById('filter-hist-wallet')?.value || currentReportScope || '';
    const symbolFilter = document.getElementById('filter-hist-symbol')?.value || 'ALL';
    const typeFilter = document.getElementById('filter-hist-type')?.value || 'ALL';
    const query = (document.getElementById('search-hist-query')?.value || '').toLowerCase().trim();

    cachedOverviewHistory = (rawOverviewHistory || []).filter(h => {
        const matchSrc = (srcFilter === 'ALL' || h.source === srcFilter);
        const matchWallet = (!walletFilter || walletFilter === 'ALL' || String(h.wallet_id) === String(walletFilter));
        const matchSymbol = (symbolFilter === 'ALL' || h.symbol === symbolFilter);
        const matchType = (typeFilter === 'ALL' || h.position_type === typeFilter);
        const matchQuery = !query || String(h.ticket).includes(query) || (h.comment && h.comment.toLowerCase().includes(query)) || (h.symbol && h.symbol.toLowerCase().includes(query));
        return matchSrc && matchWallet && matchSymbol && matchType && matchQuery;
    });

    const badgeCount = document.getElementById('badge-history-count');
    if (badgeCount) badgeCount.innerText = cachedOverviewHistory.length;

    renderOverviewHistory(cachedOverviewHistory);
}

function renderOverviewHistory(history) {
    const histData = history !== undefined ? history : cachedOverviewHistory;
    const tbody = document.getElementById('tbody-history-summary');
    if (!tbody) return;

    if (!histData || histData.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted"><i class="fa-solid fa-clock-rotate-left text-secondary me-2"></i> Chưa có lịch sử khớp lệnh nào phù hợp với bộ lọc</td></tr>`;
        renderPaginationComponent('pagination-overview-history', 0, 1, ADMIN_PAGE_SIZE, 'changeOverviewHistPage');
        return;
    }

    const totalPages = Math.ceil(histData.length / ADMIN_PAGE_SIZE) || 1;
    if (currentOverviewHistPage > totalPages) currentOverviewHistPage = totalPages;
    if (currentOverviewHistPage < 1) currentOverviewHistPage = 1;

    const startIndex = (currentOverviewHistPage - 1) * ADMIN_PAGE_SIZE;
    const paged = histData.slice(startIndex, startIndex + ADMIN_PAGE_SIZE);

    tbody.innerHTML = paged.map(h => {
        const isBot = (h.source === 'BOT');
        const tagBadge = isBot 
            ? `<span class="badge bg-primary text-white font-monospace shadow-sm" style="font-size: 0.72rem; letter-spacing: 0.5px;"><i class="fa-solid fa-robot me-1"></i>BOT</span>` 
            : `<span class="badge text-white font-monospace shadow-sm" style="background-color: #6f42c1; font-size: 0.72rem; letter-spacing: 0.5px;"><i class="fa-solid fa-user me-1"></i>USER</span>`;

        return `
        <tr>
            <td class="font-monospace small font-weight-bold">#${h.ticket}</td>
            <td>${tagBadge}</td>
            <td><span class="badge bg-light text-dark border font-weight-bold">${h.wallet_name || 'Ví MT5'}</span></td>
            <td><b class="text-dark font-monospace">${h.symbol}</b></td>
            <td><span class="badge ${h.position_type === 'BUY' ? 'badge-buy' : 'badge-sell'}">${h.position_type}</span></td>
            <td class="font-weight-bold">${h.lot_size} Lot</td>
            <td><small class="font-monospace">$${h.open_price} &rarr; $${h.close_price}</small></td>
            <td>
                <span class="small text-muted font-monospace"><i class="fa-regular fa-clock text-primary me-1"></i>${h.opened_at || '---'}</span>
            </td>
            <td>
                <span class="small text-muted font-monospace"><i class="fa-regular fa-calendar-check text-success me-1"></i>${h.closed_at || '---'}</span>
            </td>
            <td>
                ${formatPnl(h.pnl, { asBadge: true })}
                ${(h.commission || h.swap) ? `<div class="small text-muted font-monospace" style="font-size: 0.68rem;" title="Phí hoa hồng: ${h.commission || 0} USD, Swap: ${h.swap || 0} USD">Phí: ${((h.commission || 0) + (h.swap || 0)).toFixed(2)} USD</div>` : ''}
            </td>
            <td><span class="badge bg-light text-secondary border small">${h.close_reason_display || h.close_reason}</span></td>
        </tr>
        `;
    }).join('');

    renderPaginationComponent('pagination-overview-history', histData.length, currentOverviewHistPage, ADMIN_PAGE_SIZE, 'changeOverviewHistPage');
}

async function closeSinglePosition(positionId, ticket) {
    if (!confirm(`Bạn có chắc chắn muốn đóng lệnh #${ticket}?`)) return;
    try {
        const res = await apiFetch(`/api/positions/${positionId}/close/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticket: String(ticket || '') }),
        });
        const data = await parseApiJson(res);
        if (data.success) {
            showToast(data.message, 'success');
            refreshAllData();
            fetchLiveTicks();
        } else {
            showToast(data.error || 'Lỗi khi đóng vị thế', 'error');
            alert(`Lỗi đóng lệnh #${ticket}: ${data.error || 'Không thể đóng lệnh trên sàn Exness MT5'}`);
            refreshAllData();
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

/* ==================== WALLETS PAGE CRUD ==================== */
function initWalletsPage() {
    if (!document.getElementById('table-wallets')) return;
    loadWalletsList();
    document.getElementById('btn-refresh-wallets')?.addEventListener('click', () => loadWalletsList(true));

    // Polling 2s cho danh sách ví để tối ưu hiệu năng và giữ giao diện hoàn toàn tĩnh khi không có lệnh
    setInterval(loadWalletsList, 2000);
}

async function loadWalletsList(forceFullRender = false) {
    try {
        const res = await fetch('/api/wallets/');
        walletsData = await res.json();
        renderWalletsTable(walletsData, forceFullRender);
    } catch (e) {
        console.error("Error loading wallets:", e);
    }
}

let currentWalletsPage = 1;
const WALLETS_PAGE_SIZE = 20;

function changeWalletsPage(page) {
    currentWalletsPage = page;
    renderWalletsTable(walletsData, true);
}

function displayWalletModalError(msg) {
    const alertBox = document.getElementById('wallet-modal-alert');
    const alertMsg = document.getElementById('wallet-modal-alert-msg');
    const modalEl = document.getElementById('modal-wallet');
    const modalOpen = !!(modalEl && modalEl.classList.contains('show'));
    if (alertBox && alertMsg && modalOpen) {
        alertMsg.innerText = msg;
        alertBox.classList.remove('d-none');
        alertBox.classList.add('d-flex');
        alertBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
    }
    showToast(msg, 'error');
}

function clearWalletModalError() {
    const alertBox = document.getElementById('wallet-modal-alert');
    if (alertBox) {
        alertBox.classList.add('d-none');
        alertBox.classList.remove('d-flex');
    }
}

function renderWalletsTable(wallets, forceFullRender = false) {
    const tbody = document.getElementById('tbody-wallets');
    if (!tbody) return;

    if (!wallets || wallets.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted"><i class="fa-solid fa-circle-exclamation text-warning me-2"></i> Chưa có ví nào được cấu hình</td></tr>`;
        renderPaginationComponent('pagination-wallets', 0, 1, WALLETS_PAGE_SIZE, 'changeWalletsPage');
        return;
    }

    walletsData = wallets;

    const totalPages = Math.ceil(wallets.length / WALLETS_PAGE_SIZE) || 1;
    if (currentWalletsPage > totalPages) currentWalletsPage = totalPages;
    if (currentWalletsPage < 1) currentWalletsPage = 1;

    const startIndex = (currentWalletsPage - 1) * WALLETS_PAGE_SIZE;
    const paged = wallets.slice(startIndex, startIndex + WALLETS_PAGE_SIZE);

    // 1. In-place conditional DOM update (chỉ thay đổi cell nào có giá trị thay đổi thực tế)
    const allRowsExist = paged.every(w => document.getElementById(`wallet-row-${w.id}`));
    const existingRowCount = tbody.querySelectorAll('tr[id^="wallet-row-"]').length;

    if (uiPointerBusy && tbody.querySelector('.btn-action-icon') && !forceFullRender) {
        if (!(allRowsExist && existingRowCount === paged.length)) return;
    }

    if (!forceFullRender && allRowsExist && existingRowCount === paged.length) {
        paged.forEach(w => {
            const row = document.getElementById(`wallet-row-${w.id}`);
            if (!row) return;

            const balEl = row.querySelector('.wallet-cell-balance');
            const eqEl = row.querySelector('.wallet-cell-equity');
            const floatEl = row.querySelector('.wallet-cell-floating');
            const freeMarginEl = row.querySelector('.wallet-cell-freemargin');
            const todayEl = row.querySelector('.wallet-cell-today');
            const statusEl = row.querySelector('.wallet-cell-status');

            const newBal = Number(w.balance);
            const newEq = Number(w.equity);

            if (balEl) {
                const prevBal = Number(balEl.dataset.val);
                if (prevBal !== newBal) {
                    balEl.innerText = formatMoney(newBal);
                    balEl.classList.remove('flash-price-up', 'flash-price-down');
                    void balEl.offsetWidth;
                    balEl.classList.add(newBal > prevBal ? 'flash-price-up' : 'flash-price-down');
                    balEl.dataset.val = newBal;
                }
            }

            if (eqEl) {
                const prevEq = Number(eqEl.dataset.val);
                if (prevEq !== newEq) {
                    eqEl.innerText = formatMoney(newEq);
                    eqEl.classList.remove('flash-price-up', 'flash-price-down');
                    void eqEl.offsetWidth;
                    eqEl.classList.add(newEq > prevEq ? 'flash-price-up' : 'flash-price-down');
                    eqEl.dataset.val = newEq;
                }
            }

            if (floatEl && floatEl.dataset.val !== String(w.floating_pnl)) {
                floatEl.dataset.val = String(w.floating_pnl);
                floatEl.innerHTML = formatPnl(w.floating_pnl, { asBadge: true });
            }

            const freeMarginStr = formatMoney(w.margin_free || 0);
            if (freeMarginEl && freeMarginEl.innerText !== freeMarginStr) {
                freeMarginEl.innerText = freeMarginStr;
            }

            if (todayEl && todayEl.dataset.val !== String(w.today_pnl)) {
                todayEl.dataset.val = String(w.today_pnl);
                todayEl.innerHTML = formatPnl(w.today_pnl, { asBadge: true });
            }

            if (statusEl && statusEl.dataset.status !== w.bot_status) {
                statusEl.dataset.status = w.bot_status;
                const badgeClass = w.bot_status === 'RUNNING' ? 'bg-success' : (w.bot_status === 'PAUSED' ? 'bg-warning text-dark' : 'bg-danger');
                statusEl.innerHTML = `<span class="badge ${badgeClass}">${w.bot_status_display}</span>`;
            }
        });
    } else {
        // 2. Render toàn bộ bảng
        tbody.innerHTML = paged.map(w => {
            let typeBadge = 'badge-demo';
            if (w.account_type === 'REAL') typeBadge = 'badge-real';
            else if (w.account_type === 'SIMULATION') typeBadge = 'badge-sim';

            return `
                <tr id="wallet-row-${w.id}">
                    <td>
                        <div class="font-weight-bold text-dark">${w.name}</div>
                        <small class="text-muted">${(w.allowed_symbols || []).join(', ')}</small>
                    </td>
                    <td><span class="badge ${typeBadge}">${w.account_type_display}</span></td>
                    <td class="font-monospace font-weight-bold text-primary">#${w.mt5_login}</td>
                    <td><span class="badge bg-light text-dark border font-monospace">${w.mt5_server}</span></td>
                    <td class="font-weight-bold text-dark font-monospace"><span class="badge bg-light text-primary border font-monospace px-2 py-1">${Number(w.default_lot_size || 0.01).toFixed(2)} Lot</span></td>
                    <td class="font-weight-bold font-monospace"><span class="badge bg-light text-dark border px-2 py-1">${Number(w.max_open_trades || 1)} lệnh</span></td>
                    <td class="font-weight-bold font-monospace"><span class="badge bg-light text-success border px-2 py-1">$${Number(w.min_take_profit_usd || 1).toFixed(2)}</span></td>
                    <td class="font-weight-bold font-monospace"><span class="badge bg-light text-danger border px-2 py-1">${Number(w.risk_percent || 1.5).toFixed(1)}%</span></td>
                    <td class="font-weight-bold text-success wallet-cell-balance" data-val="${w.balance}">${formatMoney(w.balance)}</td>
                    <td class="font-weight-bold text-primary wallet-cell-equity" data-val="${w.equity}">${formatMoney(w.equity)}</td>
                    <td class="wallet-cell-floating" data-val="${w.floating_pnl}">${formatPnl(w.floating_pnl, { asBadge: true })}</td>
                    <td class="font-weight-bold text-secondary wallet-cell-freemargin">${formatMoney(w.margin_free || 0)}</td>
                    <td class="wallet-cell-today" data-val="${w.today_pnl}">${formatPnl(w.today_pnl, { asBadge: true })}</td>
                    <td class="wallet-cell-status" data-status="${w.bot_status}">
                        <span class="badge ${w.bot_status === 'RUNNING' ? 'bg-success' : (w.bot_status === 'PAUSED' ? 'bg-warning text-dark' : 'bg-danger')}">
                            ${w.bot_status_display}
                        </span>
                    </td>
                    <td class="text-end text-nowrap">
                        <div class="d-inline-flex align-items-center gap-1">
                            <button class="btn btn-sm btn-outline-primary btn-action-icon" onclick="openEditWalletModal(${w.id})" title="Chỉnh sửa ví">
                                <i class="fa-solid fa-pen"></i>
                            </button>
                            <a href="/wallet/${w.id}/" class="btn btn-sm btn-outline-info btn-action-icon" target="_blank" title="Xem báo cáo chi tiết">
                                <i class="fa-solid fa-eye"></i>
                            </a>
                            <button class="btn btn-sm btn-outline-danger btn-action-icon" onclick="deleteWallet(${w.id})" title="Xóa ví">
                                <i class="fa-solid fa-trash"></i>
                            </button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    }

    renderPaginationComponent('pagination-wallets', wallets.length, currentWalletsPage, WALLETS_PAGE_SIZE, 'changeWalletsPage');
}

let masterDataCache = null;

function updateSelectedSymbolsCount() {
    const checked = document.querySelectorAll('.symbol-checkbox:checked');
    const el = document.getElementById('selected-symbols-count');
    if (el) {
        el.innerHTML = `Đã chọn: <b class="${checked.length > 0 ? 'text-primary' : 'text-danger'}">${checked.length}</b> cặp`;
    }
}

function selectAllSymbols(selectAll) {
    document.querySelectorAll('.symbol-checkbox').forEach(chk => {
        const parent = chk.closest('.symbol-chip-item');
        if (!parent || parent.style.display !== 'none') {
            chk.checked = selectAll;
        }
    });
    updateSelectedSymbolsCount();
}

function selectDefaultPairs() {
    const defaults = ['XAUUSD', 'BTCUSD', 'ETHUSD'];
    document.querySelectorAll('.symbol-checkbox').forEach(chk => {
        chk.checked = defaults.includes(chk.value);
    });
    updateSelectedSymbolsCount();
}

function selectGoldOnly() {
    selectDefaultPairs();
}

function onAccountTypeChanged() {
    const isReal = document.getElementById('wallet-type-real') ? document.getElementById('wallet-type-real').checked : true;
    const serverSelect = document.getElementById('wallet-mt5-server');
    const alertText = document.getElementById('wallet-sync-alert-text');

    if (alertText) {
        if (isReal) {
            alertText.innerHTML = 'Ví <strong>Real Live MT5</strong> sẽ <strong>tự động đồng bộ số dư (Balance) và vốn (Equity) thực tế từ máy chủ Exness</strong> khi kết nối thành công (không cần nhập vốn thủ công).';
        } else {
            alertText.innerHTML = 'Ví <strong>Demo Thử Nghiệm</strong> sẽ <strong>tự động đồng bộ số dư tài khoản thử nghiệm trực tiếp từ máy chủ Exness Trial</strong> khi kết nối.';
        }
    }

    if (serverSelect && masterDataCache && masterDataCache.servers) {
        const currentVal = serverSelect.value;
        const filteredServers = masterDataCache.servers.filter(s => {
            if (!isReal) return s.server_type === 'DEMO' || s.server_name.includes('Trial') || s.server_name.includes('Demo');
            return s.server_type === 'REAL' || s.server_name.includes('Real');
        });

        const serversToRender = filteredServers.length > 0 ? filteredServers : masterDataCache.servers;
        serverSelect.innerHTML = serversToRender.map(s => `
            <option value="${s.server_name}">${s.server_name} (${s.server_type_display || (s.server_type === 'REAL' ? 'Real' : 'Demo')})</option>
        `).join('');

        if (currentVal && serversToRender.some(s => s.server_name === currentVal)) {
            serverSelect.value = currentVal;
        }
    }
}

async function loadMasterDataForWalletModal() {
    try {
        if (!masterDataCache) {
            const res = await fetch('/api/admin/master-data/');
            masterDataCache = await res.json();
        }
        
        onAccountTypeChanged();

        // Populate Symbols Structured by Category
        const symbolsBox = document.getElementById('wallet-symbols-checkboxes');
        if (symbolsBox && masterDataCache.symbols && masterDataCache.symbols.length > 0) {
            const checkedValues = Array.from(document.querySelectorAll('.symbol-checkbox:checked')).map(c => c.value);

            // Group symbols by category
            const categories = [
                { key: 'METALS', name: 'Kim Loại Quý (Metals)', icon: 'fa-solid fa-coins text-warning' },
                { key: 'FOREX', name: 'Tiền Tệ Ngoại Hối (Forex)', icon: 'fa-solid fa-money-bill-transfer text-primary' },
                { key: 'CRYPTO', name: 'Tiền Mã Hóa (Crypto)', icon: 'fa-brands fa-bitcoin text-warning' },
                { key: 'INDICES', name: 'Chỉ Số Chứng Khoán (Indices)', icon: 'fa-solid fa-chart-line text-success' },
                { key: 'ENERGY', name: 'Năng Lượng (Energy & Oil)', icon: 'fa-solid fa-fire text-danger' }
            ];

            let html = '';
            categories.forEach(cat => {
                const groupSymbols = masterDataCache.symbols.filter(s => (s.category === cat.key) || (!s.category && cat.key === 'FOREX'));
                if (groupSymbols.length === 0) return;

                html += `
                    <div class="symbol-category-block">
                        <div class="symbol-cat-header">
                            <i class="${cat.icon}"></i>
                            <span>${cat.name}</span>
                        </div>
                        <div class="row g-2">
                            ${groupSymbols.map(sym => `
                                <div class="col-6 col-md-4 symbol-chip-item" data-symbol="${sym.symbol}" data-name="${sym.display_name}">
                                    <label class="symbol-card-label" for="chk-sym-${sym.symbol}">
                                        <input type="checkbox" class="symbol-checkbox" value="${sym.symbol}" id="chk-sym-${sym.symbol}" ${checkedValues.includes(sym.symbol) ? 'checked' : ''} onchange="updateSelectedSymbolsCount()">
                                        <div class="symbol-card-box">
                                            <div class="d-flex justify-content-between align-items-center gap-1">
                                                <span class="symbol-card-code font-monospace fw-bold">${sym.symbol}</span>
                                                <i class="fa-solid fa-check symbol-check-icon"></i>
                                            </div>
                                            <div class="symbol-card-name text-truncate">${sym.display_name}</div>
                                        </div>
                                    </label>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            });

            symbolsBox.innerHTML = html;
            updateSelectedSymbolsCount();
        }
    } catch (e) {
        console.error("Error loading master data for wallet modal:", e);
    }
}

function filterModalSymbols(query) {
    const q = (query || '').toLowerCase().trim();
    const items = document.querySelectorAll('.symbol-chip-item');
    items.forEach(item => {
        const sym = (item.getAttribute('data-symbol') || '').toLowerCase();
        const name = (item.getAttribute('data-name') || '').toLowerCase();
        if (!q || sym.includes(q) || name.includes(q)) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
        }
    });

    // Hide empty category blocks
    document.querySelectorAll('.symbol-category-block').forEach(block => {
        const visibleChips = block.querySelectorAll('.symbol-chip-item:not([style*="display: none"])');
        block.style.display = visibleChips.length > 0 ? '' : 'none';
    });
}

function filterMarketTicker(query) {
    const q = (query || '').toLowerCase().trim();
    const cols = document.querySelectorAll('.ticker-card-col');
    cols.forEach(col => {
        const sym = (col.getAttribute('data-symbol') || '').toLowerCase();
        const name = (col.getAttribute('data-name') || '').toLowerCase();
        if (!q || sym.includes(q) || name.includes(q)) {
            col.style.display = '';
        } else {
            col.style.display = 'none';
        }
    });
}

async function openAddWalletModal() {
    clearWalletModalError();
    await loadMasterDataForWalletModal();
    document.getElementById('wallet-id').value = '';
    document.getElementById('wallet-name').value = '';
    document.getElementById('wallet-mt5-login').value = '';
    
    const dangerZone = document.getElementById('wallet-edit-danger-zone');
    if (dangerZone) dangerZone.classList.add('d-none');

    // Mặc định chọn loại tài khoản Real (Live MT5)
    const realRadio = document.getElementById('wallet-type-real');
    if (realRadio) realRadio.checked = true;
    onAccountTypeChanged();

    const passInput = document.getElementById('wallet-mt5-password');
    passInput.value = '';
    passInput.required = true;
    const passHelp = document.getElementById('wallet-password-help');
    if (passHelp) passHelp.innerText = 'Mật khẩu MT5 giao dịch';
    if (document.getElementById('wallet-default-lot')) document.getElementById('wallet-default-lot').value = '0.01';
    if (document.getElementById('wallet-max-open-trades')) document.getElementById('wallet-max-open-trades').value = '5';
    if (document.getElementById('wallet-min-take-profit')) document.getElementById('wallet-min-take-profit').value = '1.00';
    if (document.getElementById('wallet-bot-status')) document.getElementById('wallet-bot-status').value = 'RUNNING';
    if (document.getElementById('wallet-is-active')) document.getElementById('wallet-is-active').checked = true;

    // Default to XAUUSD + BTCUSD + ETHUSD
    selectDefaultPairs();

    if (typeof jQuery !== 'undefined') {
        jQuery('#modal-wallet select').trigger('change');
    }
}

async function openEditWalletModal(id) {
    clearWalletModalError();
    await loadMasterDataForWalletModal();
    const w = walletsData.find(x => x.id === id);
    if (!w) return;

    document.getElementById('wallet-id').value = w.id;
    document.getElementById('wallet-name').value = w.name;
    document.getElementById('wallet-mt5-login').value = w.mt5_login;

    const dangerZone = document.getElementById('wallet-edit-danger-zone');
    if (dangerZone) dangerZone.classList.remove('d-none');
    
    if (w.account_type === 'REAL') {
        const realRadio = document.getElementById('wallet-type-real');
        if (realRadio) realRadio.checked = true;
    } else {
        const demoRadio = document.getElementById('wallet-type-demo');
        if (demoRadio) demoRadio.checked = true;
    }
    onAccountTypeChanged();

    const passInput = document.getElementById('wallet-mt5-password');
    passInput.value = '';
    passInput.required = false;
    const passHelp = document.getElementById('wallet-password-help');
    if (passHelp) passHelp.innerText = 'Để trống nếu muốn giữ nguyên mật khẩu cũ';
    document.getElementById('wallet-mt5-server').value = w.mt5_server;
    if (document.getElementById('wallet-default-lot')) document.getElementById('wallet-default-lot').value = Number(w.default_lot_size || 0.01).toFixed(2);
    if (document.getElementById('wallet-max-open-trades')) document.getElementById('wallet-max-open-trades').value = String(w.max_open_trades || 5);
    if (document.getElementById('wallet-min-take-profit')) document.getElementById('wallet-min-take-profit').value = Number(w.min_take_profit_usd || 1).toFixed(2);
    if (document.getElementById('wallet-risk-percent')) document.getElementById('wallet-risk-percent').value = Number(w.risk_percent || 1.5);
    if (document.getElementById('wallet-max-daily-loss')) document.getElementById('wallet-max-daily-loss').value = Number(w.max_daily_loss_percent || 4);
    if (document.getElementById('wallet-bot-status')) document.getElementById('wallet-bot-status').value = w.bot_status || 'RUNNING';
    if (document.getElementById('wallet-is-active')) document.getElementById('wallet-is-active').checked = w.is_active;

    const allowed = w.allowed_symbols || [];
    document.querySelectorAll('.symbol-checkbox').forEach(chk => {
        chk.checked = allowed.includes(chk.value);
    });
    updateSelectedSymbolsCount();

    if (typeof jQuery !== 'undefined') {
        jQuery('#modal-wallet select').trigger('change');
    }

    const modal = new bootstrap.Modal(document.getElementById('modal-wallet'));
    modal.show();
}

async function clearWalletPlansFromModal() {
    const id = document.getElementById('wallet-id')?.value;
    if (!id) return;
    const name = document.getElementById('wallet-name')?.value || `Ví #${id}`;
    if (!confirm(`Bạn có chắc chắn muốn xóa TOÀN BỘ kế hoạch AI của ví "${name}"?`)) return;

    try {
        const res = await fetch(`/api/wallets/${id}/clear-plans/`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || 'Đã xóa toàn bộ kế hoạch AI của ví!', 'success');
            if (typeof refreshOverviewDeepData === 'function') refreshOverviewDeepData();
        } else {
            showToast(data.error || 'Lỗi khi xóa kế hoạch AI', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

async function clearWalletHistoryFromModal() {
    const id = document.getElementById('wallet-id')?.value;
    if (!id) return;
    const name = document.getElementById('wallet-name')?.value || `Ví #${id}`;
    if (!confirm(`Bạn có chắc chắn muốn xóa TOÀN BỘ lịch sử lệnh đã đóng của ví "${name}"?`)) return;

    try {
        const res = await fetch(`/api/wallets/${id}/clear-history/`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || 'Đã xóa toàn bộ lịch sử lệnh của ví!', 'success');
            if (typeof refreshOverviewDeepData === 'function') refreshOverviewDeepData();
            if (typeof loadWalletsList === 'function') loadWalletsList(true);
        } else {
            showToast(data.error || 'Lỗi khi xóa lịch sử lệnh', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

async function testWalletConnection(e) {
    if (e) e.preventDefault();
    clearWalletModalError();
    const btn = document.getElementById('btn-test-connection');
    const login = document.getElementById('wallet-mt5-login').value.trim();
    const server = document.getElementById('wallet-mt5-server').value.trim();
    const pass = document.getElementById('wallet-mt5-password').value.trim();
    const id = document.getElementById('wallet-id').value;
    const isReal = document.getElementById('wallet-type-real') && document.getElementById('wallet-type-real').checked;
    const account_type = isReal ? 'REAL' : 'DEMO';

    if (!server) {
        displayWalletModalError('Vui lòng chọn Máy chủ Exness!');
        return;
    }
    if (!login) {
        displayWalletModalError('Vui lòng nhập Số tài khoản MT5!');
        return;
    }
    if (!id && !pass) {
        displayWalletModalError('Vui lòng nhập Mật khẩu MT5!');
        return;
    }

    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> Đang kết nối tới sàn Exness...`;
    }

    try {
        const res = await fetch('/api/admin/wallets/test-connection/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                wallet_id: id ? parseInt(id) : null,
                mt5_login: login,
                mt5_password: pass || 'existing_password',
                mt5_server: server,
                account_type: account_type
            })
        });
        const data = await res.json();
        if (data.success) {
            clearWalletModalError();
            const acc = data.account_info || {};
            const balStr = acc.balance !== undefined ? ` (Số dư thực tế từ sàn Exness: $${Number(acc.balance).toLocaleString('en-US', {minimumFractionDigits: 2})})` : '';
            showToast(`✅ ${data.message}${balStr}`, 'success');
            if (acc.balance !== undefined && !isReal) {
                const capIn = document.getElementById('wallet-capital');
                if (capIn && (!capIn.value || capIn.value === '1000')) {
                    capIn.value = acc.balance;
                }
            }
            if (typeof loadWalletsList === 'function') loadWalletsList(true);
            if (typeof refreshOverviewDeepData === 'function') refreshOverviewDeepData();
            if (typeof fetchLiveTicks === 'function') fetchLiveTicks();
        } else {
            displayWalletModalError(data.error || 'Kết nối tới sàn Exness thất bại. Vui lòng kiểm tra lại!');
        }
    } catch (err) {
        displayWalletModalError('Lỗi mạng hoặc không thể kết nối tới máy chủ Exness');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = origHtml;
        }
    }
}

async function saveWallet(e) {
    e.preventDefault();
    clearWalletModalError();
    const id = document.getElementById('wallet-id').value;
    
    const selectedSymbols = [];
    document.querySelectorAll('.symbol-checkbox:checked').forEach(chk => {
        selectedSymbols.push(chk.value);
    });

    if (selectedSymbols.length === 0) {
        displayWalletModalError('Vui lòng chọn ít nhất một cặp giao dịch cho ví!');
        return;
    }

    const name = document.getElementById('wallet-name').value.trim();
    const mt5_server = document.getElementById('wallet-mt5-server').value.trim();
    const mt5_login = document.getElementById('wallet-mt5-login').value.trim();
    const mt5_password = document.getElementById('wallet-mt5-password').value.trim();
    const isReal = document.getElementById('wallet-type-real') && document.getElementById('wallet-type-real').checked;
    const account_type = isReal ? 'REAL' : 'DEMO';

    if (!name || !mt5_server || !mt5_login) {
        displayWalletModalError('Vui lòng điền đầy đủ các trường thông tin bắt buộc (*)!');
        return;
    }
    if (!id && !mt5_password) {
        displayWalletModalError('Vui lòng nhập mật khẩu MT5 khi tạo ví mới!');
        return;
    }

    const wantActive = document.getElementById('wallet-is-active').checked;
    if (wantActive) {
        const others = (walletsData || []).filter(w => w.is_active && String(w.id) !== String(id || ''));
        if (others.length) {
            displayWalletModalError(
                `Đã có ví đang kích hoạt: ${others[0].name} (#${others[0].mt5_login}). MT5 chỉ chạy 1 tài khoản. Hãy tắt ví đó trước khi bật ví này.`
            );
            return;
        }
    }

    const saveBtn = document.getElementById('btn-save-wallet');
    const origSaveHtml = saveBtn ? saveBtn.innerHTML : '';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = wantActive
            ? `<span class="spinner-border spinner-border-sm me-1"></span> Đang kết nối và đồng bộ ví...`
            : `<span class="spinner-border spinner-border-sm me-1"></span> Đang lưu cấu hình...`;
    }

    const capInput = document.getElementById('wallet-capital') || document.getElementById('wallet-balance');
    const capitalVal = (capInput && capInput.value.trim() !== '') ? parseFloat(capInput.value) : (isReal ? null : 1000);

    const payload = {
        name: name,
        mt5_login: mt5_login,
        mt5_password: mt5_password,
        mt5_server: mt5_server,
        account_type: account_type,
        capital: capitalVal,
        default_lot_size: parseFloat(document.getElementById('wallet-default-lot')?.value || 0.01) || 0.01,
        max_open_trades: parseInt(document.getElementById('wallet-max-open-trades')?.value || '5', 10) || 5,
        min_take_profit_usd: parseFloat(document.getElementById('wallet-min-take-profit')?.value || 1) || 1,
        risk_percent: parseFloat(document.getElementById('wallet-risk-percent')?.value || 1.5) || 1.5,
        max_daily_loss_percent: parseFloat(document.getElementById('wallet-max-daily-loss')?.value || 4) || 4,
        allowed_symbols: selectedSymbols,
        bot_status: document.getElementById('wallet-bot-status').value,
        is_active: wantActive
    };

    const url = id ? `/api/admin/wallets/${id}/` : '/api/admin/wallets/';
    const method = id ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        let data = {};
        try {
            data = await res.json();
        } catch (parseErr) {
            data = { error: 'Máy chủ phản hồi mã lỗi: ' + res.status };
        }

        if (res.ok && data.success) {
            showToast(data.message || 'Lưu cấu hình ví thành công!', 'success');
            const modalEl = document.getElementById('modal-wallet');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
            if (typeof loadWalletsList === 'function') await loadWalletsList(true);
            if (typeof refreshOverviewDeepData === 'function') refreshOverviewDeepData();
            if (typeof fetchLiveTicks === 'function') fetchLiveTicks();
            if (typeof refreshAllData === 'function') refreshAllData();
            if (data.algo_required && typeof window.showMt5AlgoPopup === 'function') {
                window.showMt5AlgoPopup(true);
            } else if (typeof window.refreshMt5Status === 'function') {
                window.refreshMt5Status();
            }
        } else {
            displayWalletModalError(data.error || data.message || 'Có lỗi xảy ra khi kết nối ví');
        }
    } catch (err) {
        console.error('Lỗi khi lưu ví:', err);
        displayWalletModalError('Lỗi kết nối khi lưu ví: ' + (err.message || 'Không thể phản hồi từ máy chủ'));
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = origSaveHtml;
        }
    }
}

async function deleteWallet(id) {
    if (!confirm('Bạn có chắc chắn muốn ngắt kết nối máy chủ và xóa ví Exness này khỏi hệ thống?')) return;
    try {
        const res = await fetch(`/api/admin/wallets/${id}/`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message || 'Xóa ví thành công!', 'success');
            if (typeof loadWalletsList === 'function') await loadWalletsList(true);
            if (typeof refreshOverviewDeepData === 'function') refreshOverviewDeepData();
        } else {
            showToast(data.error || 'Lỗi khi xóa ví', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

/* ==================== SYMBOLS PAGE CRUD ==================== */
function initSymbolsPage() {
    if (!document.getElementById('table-symbols')) return;
    loadSymbolsList();
    document.getElementById('btn-refresh-symbols')?.addEventListener('click', loadSymbolsList);
    const filterCat = document.getElementById('filter-symbol-category');
    if (filterCat) {
        filterCat.addEventListener('change', filterMasterSymbolsTable);
        if (typeof jQuery !== 'undefined') {
            jQuery(filterCat).on('change select2:select', filterMasterSymbolsTable);
        }
    }
}

async function loadSymbolsList() {
    try {
        const res = await fetch('/api/admin/symbols/');
        symbolsData = await res.json();
        filterMasterSymbolsTable();
    } catch (e) {
        console.error("Error loading symbols:", e);
    }
}

let currentSymbolsPage = 1;
const SYMBOLS_PAGE_SIZE = 20;

function changeSymbolsPage(page) {
    currentSymbolsPage = page;
    filterMasterSymbolsTable(false);
}

let currentSymbolCatFilter = 'ALL';

function selectSymbolCatFilter(cat, btn) {
    currentSymbolCatFilter = cat;
    document.querySelectorAll('#symbol-filter-pills .filter-pill-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    filterMasterSymbolsTable(true);
}

function renderSymbolsTable(symbols) {
    const tbody = document.getElementById('tbody-symbols');
    if (!tbody) return;

    if (!symbols || symbols.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted"><i class="fa-solid fa-circle-info me-1"></i> Không tìm thấy cặp giao dịch phù hợp</td></tr>`;
        renderPaginationComponent('pagination-symbols', 0, 1, SYMBOLS_PAGE_SIZE, 'changeSymbolsPage');
        return;
    }

    const totalPages = Math.ceil(symbols.length / SYMBOLS_PAGE_SIZE) || 1;
    if (currentSymbolsPage > totalPages) currentSymbolsPage = totalPages;
    if (currentSymbolsPage < 1) currentSymbolsPage = 1;

    const startIndex = (currentSymbolsPage - 1) * SYMBOLS_PAGE_SIZE;
    const paged = symbols.slice(startIndex, startIndex + SYMBOLS_PAGE_SIZE);

    tbody.innerHTML = paged.map(s => {
        let avatarClass = 'symbol-avatar-forex';
        let badgeClass = 'badge-cat-forex';

        if (s.category === 'METALS') {
            avatarClass = 'symbol-avatar-metals';
            badgeClass = 'badge-cat-metals';
        } else if (s.category === 'CRYPTO') {
            avatarClass = 'symbol-avatar-crypto';
            badgeClass = 'badge-cat-crypto';
        } else if (s.category === 'INDICES') {
            avatarClass = 'symbol-avatar-indices';
            badgeClass = 'badge-cat-indices';
        } else if (s.category === 'ENERGY' || s.category === 'COMMODITIES') {
            avatarClass = 'symbol-avatar-energy';
            badgeClass = 'badge-cat-energy';
        }

        const stratDisplay = s.strategy_display || s.strategy;
        const curPrice = Number(s.current_price || 0);
        const priceStr = curPrice > 0 ? (s.digits > 2 ? curPrice.toFixed(s.digits) : curPrice.toLocaleString('en-US', {minimumFractionDigits: 2})) : '---';

        return `
            <tr>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <div class="symbol-avatar ${avatarClass}">
                            ${s.symbol.substring(0, 3)}
                        </div>
                        <div>
                            <div class="font-weight-bold text-dark font-monospace">${s.symbol}</div>
                            <small class="text-muted">${s.display_name || s.symbol}</small>
                        </div>
                    </div>
                </td>
                <td><span class="badge ${badgeClass}">${s.category_display || s.category}</span></td>
                <td><span class="badge bg-secondary font-monospace">${s.timeframe}</span></td>
                <td>
                    <div class="d-flex align-items-center gap-1">
                        <i class="fa-solid fa-bolt text-warning small"></i>
                        <span class="font-weight-bold small text-dark">${stratDisplay}</span>
                    </div>
                </td>
                <td>
                    <div class="font-weight-bold text-primary fs-6">$${priceStr}</div>
                </td>
                <td>
                    <div class="spread-tag">
                        <span>${s.current_spread_pips}</span>
                        <span class="text-muted small">/ max ${s.max_allowed_spread} pips</span>
                    </div>
                </td>
                <td>
                    <label class="modern-switch" title="${s.is_active ? 'Đang bật bot cho cặp này' : 'Đang tắt bot cho cặp này'}">
                        <input type="checkbox" ${s.is_active ? 'checked' : ''} onchange="toggleSymbolActive(${s.id}, this.checked)">
                        <span class="modern-switch-slider"></span>
                        <span class="modern-switch-label small ms-1">${s.is_active ? 'BẬT' : 'TẮT'}</span>
                    </label>
                </td>
                <td class="text-end text-nowrap">
                    <button class="btn btn-sm btn-outline-primary btn-action-icon" onclick="openEditSymbolModal(${s.id})" title="Chỉnh sửa cặp">
                        <i class="fa-solid fa-pen"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');

    renderPaginationComponent('pagination-symbols', symbols.length, currentSymbolsPage, SYMBOLS_PAGE_SIZE, 'changeSymbolsPage');
}

function filterMasterSymbolsTable(resetPage = true) {
    if (resetPage) currentSymbolsPage = 1;
    const query = (document.getElementById('search-master-symbols')?.value || '').toLowerCase().trim();
    const category = currentSymbolCatFilter || 'ALL';

    const filtered = (symbolsData || []).filter(s => {
        const matchCat = (category === 'ALL' || s.category === category);
        const matchText = !query || s.symbol.toLowerCase().includes(query) || (s.display_name && s.display_name.toLowerCase().includes(query));
        return matchCat && matchText;
    });

    renderSymbolsTable(filtered);
}

function openAddSymbolModal() {
    document.getElementById('symbol-id').value = '';
    document.getElementById('symbol-code').value = '';
    document.getElementById('symbol-display-name').value = '';
    document.getElementById('symbol-category').value = 'FOREX';
    document.getElementById('symbol-timeframe').value = 'M15';
    document.getElementById('symbol-strategy').value = 'SMC_TREND';
    document.getElementById('symbol-max-spread').value = '3.5';
    document.getElementById('symbol-is-active').checked = true;
    document.getElementById('symbol-code').disabled = false;

    if (typeof jQuery !== 'undefined') {
        jQuery('#modal-symbol select').trigger('change');
    }
}

function openEditSymbolModal(id) {
    const s = symbolsData.find(x => x.id === id);
    if (!s) return;

    document.getElementById('symbol-id').value = s.id;
    document.getElementById('symbol-code').value = s.symbol;
    document.getElementById('symbol-code').disabled = true;
    document.getElementById('symbol-display-name').value = s.display_name;
    document.getElementById('symbol-category').value = s.category;
    document.getElementById('symbol-timeframe').value = s.timeframe;
    document.getElementById('symbol-strategy').value = s.strategy;
    document.getElementById('symbol-max-spread').value = s.max_allowed_spread;
    document.getElementById('symbol-is-active').checked = s.is_active;

    if (typeof jQuery !== 'undefined') {
        jQuery('#modal-symbol select').trigger('change');
    }

    const modal = new bootstrap.Modal(document.getElementById('modal-symbol'));
    modal.show();
}

async function saveSymbol(e) {
    e.preventDefault();
    const id = document.getElementById('symbol-id').value;
    const payload = {
        symbol: document.getElementById('symbol-code').value,
        display_name: document.getElementById('symbol-display-name').value,
        category: document.getElementById('symbol-category').value,
        timeframe: document.getElementById('symbol-timeframe').value,
        strategy: document.getElementById('symbol-strategy').value,
        max_allowed_spread: parseFloat(document.getElementById('symbol-max-spread').value) || 3.5,
        is_active: document.getElementById('symbol-is-active').checked
    };

    const url = id ? `/api/admin/symbols/${id}/` : '/api/admin/symbols/';
    const method = id ? 'PUT' : 'POST';

    try {
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, 'success');
            bootstrap.Modal.getInstance(document.getElementById('modal-symbol')).hide();
            loadSymbolsList();
        } else {
            showToast(data.error || 'Có lỗi xảy ra', 'error');
        }
    } catch (e) {
        showToast('Lỗi lưu cặp tiền', 'error');
    }
}

async function toggleSymbolActive(id, isActive) {
    try {
        const res = await fetch(`/api/admin/symbols/${id}/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: isActive })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Đã ${isActive ? 'bật' : 'tắt'} cặp giao dịch!`, 'success');
        }
    } catch (e) {
        showToast('Lỗi cập nhật trạng thái cặp', 'error');
    }
}

/* ==================== ERROR MONITORING & BADGES ==================== */
let errorPollingTimer = null;

async function checkUnresolvedErrors() {
    try {
        const res = await fetch('/api/admin/logs/?level=ALL');
        const data = await res.json();
        const count = data.unresolved_errors || 0;

        const headerBadge = document.getElementById('header-error-badge');
        const headerBadgeMobile = document.getElementById('header-error-badge-mobile');
        const sidebarBadge = document.getElementById('sidebar-error-badge');
        const preview = document.getElementById('header-logs-preview');
        const previewMobile = document.getElementById('header-logs-preview-mobile');
        const summaryList = document.getElementById('logs-summary-list');

        if (headerBadge) {
            headerBadge.innerText = count > 99 ? '99+' : count;
            headerBadge.classList.toggle('d-none', count === 0);
        }
        if (headerBadgeMobile) {
            headerBadgeMobile.innerText = count > 99 ? '99+' : count;
            headerBadgeMobile.classList.toggle('d-none', count === 0);
        }
        if (sidebarBadge) {
            sidebarBadge.innerText = count > 99 ? '99+' : count;
            sidebarBadge.classList.toggle('d-none', count === 0);
        }

        const recentLogs = data.logs || [];
        if (recentLogs.length > 0) {
            const html = recentLogs.slice(0, 4).map(l => {
                let badgeClass = 'bg-secondary';
                if (l.level === 'ERROR') badgeClass = 'bg-danger';
                else if (l.level === 'CRITICAL') badgeClass = 'bg-dark text-white';
                else if (l.level === 'WARNING') badgeClass = 'bg-warning text-dark';
                else if (l.level === 'INFO') badgeClass = 'bg-info text-dark';

                return `
                    <a href="/admin-panel/logs/?tab=bot&log_id=${l.id}" class="header-noti-item-link d-block border-bottom pb-2 mb-2 p-2">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="badge ${badgeClass} small" style="font-size: 10px;">${l.level}</span>
                            <small class="text-muted font-monospace" style="font-size: 11px;">${l.created_at ? l.created_at.split(' ')[1] : ''}</small>
                        </div>
                        <div class="text-dark small font-weight-bold text-truncate" title="${l.message}">${l.message}</div>
                        ${l.wallet_name ? `<small class="text-secondary d-block" style="font-size: 10.5px;"><i class="fa-solid fa-wallet text-primary me-1"></i>Ví: ${l.wallet_name}</small>` : ''}
                    </a>
                `;
            }).join('');

            if (preview) preview.innerHTML = html;
            if (previewMobile) previewMobile.innerHTML = html;
        } else {
            const okHtml = `<div class="text-center py-3 text-muted"><i class="fa-solid fa-circle-check text-success me-1"></i> Hệ thống đang vận hành ổn định, không có lỗi.</div>`;
            if (preview) preview.innerHTML = okHtml;
            if (previewMobile) previewMobile.innerHTML = okHtml;
        }

        if (summaryList) {
            if (recentLogs.length === 0) {
                summaryList.innerHTML = `<div class="text-center py-3 text-muted"><i class="fa-solid fa-check text-success me-1"></i> Hệ thống hoạt động bình thường</div>`;
            } else {
                summaryList.innerHTML = recentLogs.slice(0, 6).map(l => `
                    <div class="p-2 bg-light rounded border mb-2">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <span class="badge ${l.level === 'ERROR' ? 'bg-danger' : 'bg-info'}">${l.level}</span>
                            <small class="text-muted font-monospace">${l.created_at}</small>
                        </div>
                        <div class="small text-dark font-weight-bold text-truncate" title="${l.message}">${l.message}</div>
                    </div>
                `).join('');
            }
        }
    } catch (e) {
        console.error("Error polling logs notification:", e);
    }
}

function refreshOverviewData() {
    if (typeof refreshOverviewDeepData === 'function') {
        refreshOverviewDeepData();
    }
}

function refreshAllData() {
    if (typeof refreshOverviewDeepData === 'function') refreshOverviewDeepData();
    if (typeof loadWalletsList === 'function') loadWalletsList();
    if (typeof loadSymbolsList === 'function') loadSymbolsList();
    if (typeof checkUnresolvedErrors === 'function') checkUnresolvedErrors();
}

/* ==================== CHANGE PASSWORD ==================== */
async function submitChangePassword(e) {
    e.preventDefault();
    const oldPass = document.getElementById('cp-old-pass').value;
    const newPass = document.getElementById('cp-new-pass').value;
    const confirmPass = document.getElementById('cp-confirm-pass').value;

    if (!oldPass) {
        showToast('Vui lòng nhập mật khẩu cũ hiện tại!', 'warning');
        return;
    }

    if (!newPass) {
        showToast('Vui lòng nhập mật khẩu mới!', 'warning');
        return;
    }

    if (newPass.length < 4) {
        showToast('Mật khẩu mới phải có tối thiểu 4 ký tự!', 'warning');
        return;
    }

    if (newPass === oldPass) {
        showToast('Mật khẩu mới không được trùng với mật khẩu cũ!', 'warning');
        return;
    }

    if (newPass !== confirmPass) {
        showToast('Xác nhận mật khẩu mới không khớp!', 'warning');
        return;
    }

    const btn = document.getElementById('btn-submit-change-pass');
    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang lưu...`;

    try {
        const res = await fetch('/api/admin/change-password/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken()
            },
            body: JSON.stringify({
                old_password: oldPass,
                new_password: newPass,
                confirm_password: confirmPass
            })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showToast(data.message || 'Đã đổi mật khẩu Admin thành công!', 'success');
            document.getElementById('form-change-password').reset();
            const modalEl = document.getElementById('modal-change-password');
            if (modalEl) {
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            }
        } else {
            const errorMsg = data.error || data.detail || data.message || 'Lỗi khi đổi mật khẩu (Mã: ' + res.status + ')';
            showToast(errorMsg, 'error');
        }
    } catch (err) {
        showToast('Lỗi kết nối máy chủ: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-check me-1"></i> Cập Nhật Mật Khẩu`;
    }
}

/* ==================== TOAST NOTIFICATIONS ==================== */
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `custom-toast ${type}`;
    
    let icon = 'fa-info-circle text-primary';
    if (type === 'success') icon = 'fa-circle-check text-success';
    else if (type === 'error') icon = 'fa-circle-exclamation text-danger';
    else if (type === 'warning') icon = 'fa-triangle-exclamation text-warning';

    toast.innerHTML = `
        <i class="fa-solid ${icon} fs-5"></i>
        <div class="flex-grow-1 font-weight-bold">${message}</div>
        <button type="button" class="btn-close btn-sm ms-2" onclick="this.parentElement.remove()"></button>
    `;

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

/* ==================== VIETNAM REALTIME CLOCK (GMT+7) ==================== */
function updateVietnamLiveClock() {
    try {
        const now = new Date();
        const options = {
            timeZone: 'Asia/Ho_Chi_Minh',
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            day: '2-digit',
            month: '2-digit',
            year: 'numeric'
        };
        const formatter = new Intl.DateTimeFormat('vi-VN', options);
        const parts = formatter.formatToParts(now);
        const timeObj = {};
        parts.forEach(p => timeObj[p.type] = p.value);
        const timeStr = `${timeObj.hour}:${timeObj.minute}:${timeObj.second} ${timeObj.day}/${timeObj.month}/${timeObj.year}`;
        
        const clockEl = document.getElementById('vn-live-clock');
        if (clockEl) clockEl.innerText = `${timeStr} (VN)`;

        const homeClockEl = document.getElementById('home-vn-live-clock');
        if (homeClockEl) homeClockEl.innerText = `${timeStr} (VN)`;
    } catch (e) {
        // Fallback
        const clockEl = document.getElementById('vn-live-clock');
        if (clockEl) clockEl.innerText = new Date().toLocaleTimeString('vi-VN') + ' (VN)';
    }
}
setInterval(updateVietnamLiveClock, 1000);
document.addEventListener('DOMContentLoaded', updateVietnamLiveClock);
updateVietnamLiveClock();

/* ==================== QUICK ORDER / MANUAL TRADE DIRECT TO MT5 ==================== */
let quickTradePriceTimer = null;
let lastTradeModalPrice = 0;

function setTradeLot(lot) {
    const input = document.getElementById('trade-volume');
    if (input) input.value = Number(lot).toFixed(2);
}

function adjustTradeLot(delta) {
    const input = document.getElementById('trade-volume');
    if (!input) return;
    let val = (parseFloat(input.value) || 0.01) + delta;
    if (val < 0.01) val = 0.01;
    if (val > 100.0) val = 100.0;
    input.value = val.toFixed(2);
}

async function openQuickTradeModal(symbol = 'XAUUSD') {
    const walletSelect = document.getElementById('trade-wallet-id');
    if (walletSelect) {
        walletSelect.innerHTML = '<option value="">Đang nạp ví Exness...</option>';
        try {
            const res = await apiFetch('/api/wallets/');
            const wList = unwrapApiList(await parseApiJson(res));
            const fallback = Array.isArray(walletsData) ? walletsData : [];
            const list = (wList.length > 0 ? wList : fallback).filter(w => w.is_active);
            if (list.length > 0) {
                walletsData = wList.length > 0 ? wList : fallback;
                walletSelect.innerHTML = list.map(w => {
                    const bal = (w.balance !== undefined && w.balance !== null) ? Number(w.balance).toFixed(2) : Number(w.balance_db || 0).toFixed(2);
                    return `<option value="${w.id}">Ví #${w.mt5_login} (${w.name}) - Số dư: $${bal}</option>`;
                }).join('');
                walletSelect.value = String(list[0].id);
                walletSelect.disabled = false;
            } else {
                walletSelect.innerHTML = '<option value="">Chưa có ví Exness nào được kích hoạt</option>';
            }
        } catch (e) {
            console.error("Error fetching wallets:", e);
            walletSelect.innerHTML = '<option value="">Lỗi tải danh sách ví</option>';
        }
    }

    try {
        const resTicks = await apiFetch('/api/live-ticks/');
        const ticksData = await parseApiJson(resTicks);
        const tickSymbols = Array.isArray(ticksData.symbols) ? ticksData.symbols.filter(s => s.is_active !== false) : [];
        if (tickSymbols.length > 0) {
            cachedSymbols = tickSymbols;
            const symbolSelect = document.getElementById('trade-symbol');
            if (symbolSelect) {
                symbolSelect.innerHTML = cachedSymbols.map(s => `
                    <option value="${s.symbol}">${s.symbol} (${s.display_name || s.symbol})</option>
                `).join('');
            }
        }
    } catch (e) {
        console.error("Error fetching live ticks on open:", e);
    }

    const symbolSelect = document.getElementById('trade-symbol');
    if (symbolSelect && symbol) {
        if (![...symbolSelect.options].some(o => o.value === symbol)) {
            symbolSelect.insertAdjacentHTML('afterbegin', `<option value="${symbol}">${symbol}</option>`);
        }
        symbolSelect.value = symbol;
    }

    lastTradeModalPrice = 0;
    await updateTradeModalPrice();

    if (quickTradePriceTimer) clearInterval(quickTradePriceTimer);
    quickTradePriceTimer = setInterval(updateTradeModalPrice, 600);

    const modalEl = document.getElementById('modal-quick-trade');
    if (modalEl && typeof bootstrap !== 'undefined') {
        bootstrap.Modal.getOrCreateInstance(modalEl).show();
        if (!modalEl._hasQuickTradeHideListener) {
            modalEl._hasQuickTradeHideListener = true;
            modalEl.addEventListener('hidden.bs.modal', () => {
                if (quickTradePriceTimer) {
                    clearInterval(quickTradePriceTimer);
                    quickTradePriceTimer = null;
                }
            });
        }
    }
}

function onTradeSymbolChange() {
    lastTradeModalPrice = 0;
    const livePriceEl = document.getElementById('trade-modal-live-price');
    const buyPriceEl = document.getElementById('btn-buy-price');
    const sellPriceEl = document.getElementById('btn-sell-price');
    if (livePriceEl) livePriceEl.innerText = '$---';
    if (buyPriceEl) buyPriceEl.innerText = 'Ask: ---';
    if (sellPriceEl) sellPriceEl.innerText = 'Bid: ---';
    updateTradeModalPrice();
}

async function updateTradeModalPrice() {
    const symSelect = document.getElementById('trade-symbol');
    if (!symSelect) return;
    const sym = symSelect.value;
    
    try {
        const res = await apiFetch('/api/live-ticks/');
        const data = await parseApiJson(res);
        if (data && Array.isArray(data.symbols)) {
            cachedSymbols = data.symbols;
        }
    } catch (e) {
        console.error("Error fetching live ticks:", e);
    }

    let symObj = (cachedSymbols || []).find(s => s.symbol === sym);
    const livePriceEl = document.getElementById('trade-modal-live-price');
    const buyPriceEl = document.getElementById('btn-buy-price');
    const sellPriceEl = document.getElementById('btn-sell-price');
    const spreadBadge = document.getElementById('trade-modal-spread-badge');

    if (symObj && (symObj.current_price || symObj.ask || symObj.bid)) {
        const digits = symObj.digits !== undefined ? symObj.digits : (sym.includes('JPY') ? 3 : (sym.includes('BTC') ? 2 : (sym.includes('XAU') ? 2 : 4)));
        const curNum = Number(symObj.current_price || symObj.ask || 0);
        const askNum = Number(symObj.ask || symObj.current_price || 0);
        const bidNum = Number(symObj.bid || symObj.current_price || 0);
        const spreadVal = symObj.spread !== undefined ? symObj.spread : 0;

        const curP = curNum.toFixed(digits);
        const askP = askNum.toFixed(digits);
        const bidP = bidNum.toFixed(digits);

        if (livePriceEl) {
            livePriceEl.innerText = `$${curP}`;
            if (lastTradeModalPrice > 0) {
                if (curNum > lastTradeModalPrice) {
                    livePriceEl.style.color = '#10b981';
                } else if (curNum < lastTradeModalPrice) {
                    livePriceEl.style.color = '#ef4444';
                }
            }
            lastTradeModalPrice = curNum;
        }
        if (buyPriceEl) buyPriceEl.innerText = `Ask: $${askP}`;
        if (sellPriceEl) sellPriceEl.innerText = `Bid: $${bidP}`;
        if (spreadBadge) spreadBadge.innerText = `Spread: ${spreadVal} pips`;
    }
}

let quickTradeChain = Promise.resolve();

async function executeManualTrade(orderType) {
    const walletId = parseInt(document.getElementById('trade-wallet-id')?.value || '0', 10);
    const symbol = document.getElementById('trade-symbol')?.value;
    const volume = parseFloat(document.getElementById('trade-volume')?.value || '0.01');
    const sl = parseFloat(document.getElementById('trade-sl')?.value || '0') || null;
    const tp = parseFloat(document.getElementById('trade-tp')?.value || '0') || null;

    if (!walletId) {
        alert('Vui lòng chọn ví Exness để đặt lệnh');
        return;
    }
    if (!symbol) {
        alert('Vui lòng chọn cặp giao dịch');
        return;
    }
    if (!volume || volume <= 0) {
        alert('Khối lượng lot không hợp lệ');
        return;
    }

    const payload = {
        wallet_id: walletId,
        symbol: symbol,
        order_type: orderType,
        volume: volume,
        sl: sl,
        tp: tp,
        comment: 'Web Direct Trade'
    };
    const job = quickTradeChain.then(() => sendManualTrade(payload, orderType));
    quickTradeChain = job.catch(() => {});
    return job;
}

async function sendManualTrade(payload, orderType) {
    showQuickTradeAlert(true, `Đang gửi lệnh ${orderType} ${payload.volume} ${payload.symbol}...`);
    try {
        const res = await apiFetch('/api/orders/send/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await parseApiJson(res);
        if (data.success) {
            const msg = data.message || `Đã gửi lệnh ${orderType} thành công`;
            showQuickTradeAlert(true, msg);
            showToast(msg, 'success');
            try {
                refreshAllData();
            } catch (e) {}
        } else {
            showQuickTradeAlert(false, data.error || 'Lỗi khi gửi lệnh lên MT5');
        }
    } catch (e) {
        showQuickTradeAlert(false, 'Lỗi kết nối tới máy chủ MT5');
    }
}


