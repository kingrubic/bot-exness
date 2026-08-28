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

// Global State
let walletsData = [];
let symbolsData = [];
let countdownSeconds = 5;
let pollingInterval = null;

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
    if (!confirm('CẢNH BÁO KHẨN CẤP: Bạn có chắc chắn muốn đóng TẤT CẢ các lệnh đang mở trên toàn bộ các ví Exness?')) {
        return;
    }
    try {
        const res = await fetch('/api/positions/close-all/', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, 'success');
            refreshAllData();
        } else {
            showToast(data.error || 'Lỗi đóng lệnh khẩn cấp', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

// Number & Currency formatting helpers
function formatMoney(amount) {
    if (amount === undefined || amount === null || isNaN(amount)) return '$0.00';
    const num = Number(amount);
    return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
    const arrowIcon = isUp ? '<i class="fa-solid fa-caret-up text-success ms-1"></i>' : '<i class="fa-solid fa-caret-down text-danger ms-1"></i>';

    return `<span class="font-weight-bold ${flashClass}">$${currFormatted}${arrowIcon}</span>`;
}

/* ==================== REALTIME WEBSOCKET & SSE HOOK FEED ==================== */
let liveWebSocket = null;
let wsReconnectTimer = null;

function initOverviewLiveFeed() {
    if (!document.getElementById('kpi-balance') && !document.getElementById('live-symbols-container') && !document.getElementById('tbody-wallets') && !document.getElementById('tbody-positions')) return;

    // 1. Snapshot khởi tạo
    fetchLiveTicks();

    // 2. Kết nối Socket Hook trực tiếp (0 request lặp lại)
    connectLiveWebSocket();

    // 3. Cập nhật kế hoạch & lịch sử định kỳ mỗi 3.5s
    setInterval(refreshOverviewDeepData, 3500);
}

function connectLiveWebSocket() {
    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsHost = window.location.hostname || 'localhost';
        const wsUrl = `${protocol}//${wsHost}:8889`;

        if (liveWebSocket) {
            try { liveWebSocket.close(); } catch(e) {}
        }

        liveWebSocket = new WebSocket(wsUrl);

        liveWebSocket.onopen = function() {
            console.log("🔌 [WEBSOCKET CONNECTED] Đã kết nối Socket Hook thời gian thực thành công (0 request lặp lại)");
            if (pollingInterval) {
                clearInterval(pollingInterval);
                pollingInterval = null;
            }
        };

        liveWebSocket.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                handleLiveTicksData(data);
            } catch (err) {
                console.error("WebSocket message parse error", err);
            }
        };

        liveWebSocket.onerror = function(err) {
            console.warn("⚠️ WebSocket connection error, using polling fallback");
            startPollingFallback();
        };

        liveWebSocket.onclose = function() {
            startPollingFallback();
            if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
            wsReconnectTimer = setTimeout(connectLiveWebSocket, 3000);
        };
    } catch (e) {
        startPollingFallback();
    }
}

function startPollingFallback() {
    if (!pollingInterval) {
        pollingInterval = setInterval(fetchLiveTicks, 800);
    }
}

async function fetchLiveTicks() {
    try {
        const res = await fetch('/api/live-ticks/');
        if (!res.ok) return;
        const data = await res.json();
        handleLiveTicksData(data);
    } catch (e) {}
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

        const kpiToday = document.getElementById('kpi-today');
        if (kpiToday) kpiToday.innerHTML = formatPnl(ov.total_today_pnl);

        const kpiWallets = document.getElementById('kpi-wallets-count');
        if (kpiWallets) kpiWallets.innerText = ov.active_wallets_count || ov.total_wallets_count;

        const kpiPos = document.getElementById('kpi-positions-count');
        if (kpiPos) kpiPos.innerText = ov.active_positions_count;

        const kpiWr = document.getElementById('kpi-winrate');
        if (kpiWr) kpiWr.innerText = `${ov.overall_winrate}%`;
    }

    // 2. Update Live Market Ticker Cards (Clean separated Price and Diff Badge)
    if (data.symbols) {
        data.symbols.forEach(s => {
            const priceEl = document.getElementById(`sym-price-${s.symbol}`);
            const diffEl = document.getElementById(`sym-diff-${s.symbol}`);
            const spreadEl = document.getElementById(`sym-spread-${s.symbol}`);

            const prev = prevPriceStore['card_' + s.symbol];
            const curr = Number(s.current_price);
            prevPriceStore['card_' + s.symbol] = curr;

            let decimals = 2;
            if (s.symbol.includes('EUR') || s.symbol.includes('GBP') || s.symbol.includes('USDJPY')) {
                decimals = 5;
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

            if (spreadEl) spreadEl.innerText = s.spread;
        });
    }

    // 3. Update Active Positions
    if (data.positions) {
        renderOverviewPositions(data.positions);
    }

    // 4. Update Wallets if on Wallets Page
    if (data.wallets && document.getElementById('table-wallets')) {
        renderWalletsTable(data.wallets);
    }

    const timerEl = document.getElementById('countdown-timer');
    if (timerEl) {
        timerEl.innerHTML = `<span class="realtime-live-dot"></span> <span class="text-success fw-bold">SOCKET LIVE</span>`;
    }
}

async function refreshOverviewDeepData() {
    try {
        const walletsRes = await fetch('/api/wallets/');
        const wallets = await walletsRes.json();
        
        let allPlans = [];
        let allHistory = [];

        for (const w of wallets) {
            try {
                const detailRes = await fetch(`/api/wallets/${w.id}/`);
                const detail = await detailRes.json();
                (detail.plans || []).forEach(pl => allPlans.push({...pl, wallet_name: w.name, wallet_id: w.id}));
                (detail.history || []).forEach(h => allHistory.push({...h, wallet_name: w.name, wallet_id: w.id}));
            } catch (e) {}
        }

        renderOverviewPlans(allPlans);
        renderOverviewHistory(allHistory);
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

function renderOverviewPositions(positions) {
    cachedOverviewPositions = positions || [];
    const tbody = document.getElementById('tbody-positions');
    const badgeCount = document.getElementById('badge-open-count');
    if (!tbody) return;

    if (badgeCount) badgeCount.innerText = cachedOverviewPositions.length;

    if (cachedOverviewPositions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted"><i class="fa-solid fa-circle-check text-success me-2"></i> Không có vị thế mở nào đang chạy</td></tr>`;
        renderPaginationComponent('pagination-overview-positions', 0, 1, ADMIN_PAGE_SIZE, 'changeOverviewPosPage');
        return;
    }

    const totalPages = Math.ceil(cachedOverviewPositions.length / ADMIN_PAGE_SIZE) || 1;
    if (currentOverviewPosPage > totalPages) currentOverviewPosPage = totalPages;
    if (currentOverviewPosPage < 1) currentOverviewPosPage = 1;

    const startIndex = (currentOverviewPosPage - 1) * ADMIN_PAGE_SIZE;
    const paged = cachedOverviewPositions.slice(startIndex, startIndex + ADMIN_PAGE_SIZE);

    tbody.innerHTML = paged.map(p => `
        <tr>
            <td class="font-monospace font-weight-bold">#${p.ticket}</td>
            <td><span class="badge bg-light text-dark border font-weight-bold">${p.wallet_name}</span></td>
            <td><span class="font-weight-bold text-dark">${p.symbol}</span></td>
            <td><span class="badge ${p.position_type === 'BUY' ? 'badge-buy' : 'badge-sell'}">${p.position_type}</span></td>
            <td class="font-weight-bold">${p.lot_size} Lot</td>
            <td>$${p.open_price}</td>
            <td class="font-weight-bold">${renderLivePriceWithTick('pos_' + p.id, p.current_price, p.symbol)}</td>
            <td><small class="text-muted">$${p.stop_loss} / $${p.take_profit}</small></td>
            <td>
                ${formatPnl(p.floating_pnl, { asBadge: true })}
            </td>
            <td>
                ${p.is_breakeven_set ? '<span class="badge bg-info text-dark me-1">BE</span>' : ''}
                ${p.is_trailing ? '<span class="badge bg-warning text-dark">Trailing</span>' : ''}
            </td>
            <td class="text-end text-nowrap">
                <button class="btn btn-sm btn-outline-danger btn-action-icon" onclick="closeSinglePosition(${p.id}, '${p.ticket}')" title="Đóng Lệnh">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </td>
        </tr>
    `).join('');

    renderPaginationComponent('pagination-overview-positions', cachedOverviewPositions.length, currentOverviewPosPage, ADMIN_PAGE_SIZE, 'changeOverviewPosPage');
}

function renderOverviewPlans(plans) {
    cachedOverviewPlans = plans || [];
    const tbody = document.getElementById('tbody-plans');
    if (!tbody) return;

    if (cachedOverviewPlans.length === 0) {
        tbody.innerHTML = `<tr><td colspan="11" class="text-center py-4 text-muted">Chưa có kế hoạch AI nào được khởi tạo</td></tr>`;
        renderPaginationComponent('pagination-overview-plans', 0, 1, ADMIN_PAGE_SIZE, 'changeOverviewPlansPage');
        return;
    }

    const totalPages = Math.ceil(cachedOverviewPlans.length / ADMIN_PAGE_SIZE) || 1;
    if (currentOverviewPlansPage > totalPages) currentOverviewPlansPage = totalPages;
    if (currentOverviewPlansPage < 1) currentOverviewPlansPage = 1;

    const startIndex = (currentOverviewPlansPage - 1) * ADMIN_PAGE_SIZE;
    const paged = cachedOverviewPlans.slice(startIndex, startIndex + ADMIN_PAGE_SIZE);

    tbody.innerHTML = paged.map(pl => {
        let statusBadge = 'bg-secondary';
        if (pl.status === 'PENDING_TRIGGER') statusBadge = 'bg-warning text-dark';
        else if (pl.status === 'EXECUTING') statusBadge = 'bg-success';
        else if (pl.status === 'COMPLETED') statusBadge = 'bg-info';

        return `
            <tr>
                <td class="font-monospace">#${pl.id}</td>
                <td><small class="font-weight-bold">${pl.wallet_name}</small></td>
                <td><b>${pl.symbol}</b> <small class="text-muted">[${pl.timeframe}]</small></td>
                <td><span class="badge ${pl.direction === 'BUY' ? 'badge-buy' : 'badge-sell'}">${pl.direction}</span></td>
                <td class="font-weight-bold text-primary">$${pl.entry_price}</td>
                <td class="text-danger small">$${pl.stop_loss}</td>
                <td class="text-success small">$${pl.take_profit_1} / $${pl.take_profit_2}</td>
                <td><span class="badge bg-light text-dark border">1:${pl.rr_ratio}</span></td>
                <td><b>${pl.calculated_lot}</b></td>
                <td><span class="badge ${statusBadge}">${pl.status_display || pl.status}</span></td>
                <td><small class="text-muted text-truncate d-inline-block" style="max-width: 250px;" title="${pl.rationale}">${pl.rationale}</small></td>
            </tr>
        `;
    }).join('');

    renderPaginationComponent('pagination-overview-plans', cachedOverviewPlans.length, currentOverviewPlansPage, ADMIN_PAGE_SIZE, 'changeOverviewPlansPage');
}

function renderOverviewHistory(history) {
    cachedOverviewHistory = history || [];
    const tbody = document.getElementById('tbody-history-summary');
    if (!tbody) return;

    if (cachedOverviewHistory.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-3 text-muted">Chưa có lệnh nào đã đóng</td></tr>`;
        renderPaginationComponent('pagination-overview-history', 0, 1, ADMIN_PAGE_SIZE, 'changeOverviewHistPage');
        return;
    }

    const totalPages = Math.ceil(cachedOverviewHistory.length / ADMIN_PAGE_SIZE) || 1;
    if (currentOverviewHistPage > totalPages) currentOverviewHistPage = totalPages;
    if (currentOverviewHistPage < 1) currentOverviewHistPage = 1;

    const startIndex = (currentOverviewHistPage - 1) * ADMIN_PAGE_SIZE;
    const paged = cachedOverviewHistory.slice(startIndex, startIndex + ADMIN_PAGE_SIZE);

    tbody.innerHTML = paged.map(h => `
        <tr>
            <td class="font-monospace small">#${h.ticket}</td>
            <td><small class="font-weight-bold">${h.wallet_name}</small></td>
            <td><b>${h.symbol}</b></td>
            <td><span class="badge ${h.position_type === 'BUY' ? 'badge-buy' : 'badge-sell'}">${h.position_type}</span></td>
            <td>${h.lot_size}</td>
            <td><small>$${h.open_price} &rarr; $${h.close_price}</small></td>
            <td>${formatPnl(h.pnl, { asBadge: true })}</td>
            <td><span class="badge bg-light text-secondary border small">${h.close_reason_display || h.close_reason}</span></td>
        </tr>
    `).join('');

    renderPaginationComponent('pagination-overview-history', cachedOverviewHistory.length, currentOverviewHistPage, ADMIN_PAGE_SIZE, 'changeOverviewHistPage');
}

async function closeSinglePosition(positionId, ticket) {
    if (!confirm(`Bạn có chắc chắn muốn đóng lệnh #${ticket}?`)) return;
    try {
        const res = await fetch(`/api/positions/${positionId}/close/`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(data.message, 'success');
            refreshAllData();
        } else {
            showToast(data.error || 'Lỗi khi đóng vị thế', 'error');
        }
    } catch (e) {
        showToast('Lỗi kết nối máy chủ', 'error');
    }
}

/* ==================== WALLETS PAGE CRUD ==================== */
function initWalletsPage() {
    if (!document.getElementById('table-wallets')) return;
    loadWalletsList();
    document.getElementById('btn-refresh-wallets')?.addEventListener('click', loadWalletsList);

    // Continuous Realtime Polling for Wallets
    setInterval(loadWalletsList, 3000);
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
    if (alertBox && alertMsg) {
        alertMsg.innerText = msg;
        alertBox.classList.remove('d-none');
        alertBox.classList.add('d-flex');
        alertBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
        tbody.innerHTML = `<tr><td colspan="12" class="text-center py-4 text-muted">Chưa có ví Exness nào được kết nối</td></tr>`;
        renderPaginationComponent('pagination-wallets', 0, 1, WALLETS_PAGE_SIZE, 'changeWalletsPage');
        return;
    }

    walletsData = wallets;

    const totalPages = Math.ceil(wallets.length / WALLETS_PAGE_SIZE) || 1;
    if (currentWalletsPage > totalPages) currentWalletsPage = totalPages;
    if (currentWalletsPage < 1) currentWalletsPage = 1;

    const startIndex = (currentWalletsPage - 1) * WALLETS_PAGE_SIZE;
    const paged = wallets.slice(startIndex, startIndex + WALLETS_PAGE_SIZE);

    // 1. Kiểm tra xem các dòng đã tồn tại chưa để cập nhật từng ô theo thời gian thực (không giật lag)
    let allRowsExist = paged.every(w => document.getElementById(`wallet-row-${w.id}`));
    const existingRowCount = tbody.querySelectorAll('tr[id^="wallet-row-"]').length;

    if (!forceFullRender && allRowsExist && existingRowCount === paged.length) {
        paged.forEach(w => {
            const row = document.getElementById(`wallet-row-${w.id}`);
            if (!row) return;

            const capEl = row.querySelector('.wallet-cell-capital');
            const balEl = row.querySelector('.wallet-cell-balance');
            const eqEl = row.querySelector('.wallet-cell-equity');
            const floatEl = row.querySelector('.wallet-cell-floating');
            const todayEl = row.querySelector('.wallet-cell-today');
            const statusEl = row.querySelector('.wallet-cell-status');
            const wrEl = row.querySelector('.wallet-cell-wr');

            const newCap = Number(w.capital !== undefined ? w.capital : w.balance);
            const newBal = Number(w.balance);
            const newEq = Number(w.equity);

            if (capEl) {
                capEl.innerHTML = w.account_type === 'DEMO' ? `<span class="text-muted small">---</span>` : formatMoney(newCap);
            }

            if (balEl) {
                const prevBal = Number(balEl.dataset.val);
                balEl.innerText = formatMoney(newBal);
                if (prevBal !== undefined && prevBal !== newBal) {
                    balEl.classList.remove('flash-price-up', 'flash-price-down');
                    void balEl.offsetWidth;
                    balEl.classList.add(newBal > prevBal ? 'flash-price-up' : 'flash-price-down');
                }
                balEl.dataset.val = newBal;
            }

            if (eqEl) {
                const prevEq = Number(eqEl.dataset.val);
                eqEl.innerText = formatMoney(newEq);
                if (prevEq !== undefined && prevEq !== newEq) {
                    eqEl.classList.remove('flash-price-up', 'flash-price-down');
                    void eqEl.offsetWidth;
                    eqEl.classList.add(newEq > prevEq ? 'flash-price-up' : 'flash-price-down');
                }
                eqEl.dataset.val = newEq;
            }

            if (floatEl) floatEl.innerHTML = formatPnl(w.floating_pnl, { asBadge: true });
            if (todayEl) todayEl.innerHTML = formatPnl(w.today_pnl, { asBadge: true });
            if (wrEl) wrEl.innerHTML = `<div><b>${w.win_rate}%</b></div><small class="text-muted">${w.total_trades} lệnh</small>`;

            if (statusEl) {
                const badgeClass = w.bot_status === 'RUNNING' ? 'bg-success' : (w.bot_status === 'PAUSED' ? 'bg-warning text-dark' : 'bg-danger');
                statusEl.innerHTML = `<span class="badge ${badgeClass}">${w.bot_status_display}</span>`;
            }
        });
    } else {
        // 2. Render toàn bộ bảng nếu cấu trúc danh sách thay đổi hoặc khi lưu xong
        tbody.innerHTML = paged.map(w => {
            let typeBadge = 'badge-demo';
            if (w.account_type === 'REAL') typeBadge = 'badge-real';
            else if (w.account_type === 'SIMULATION') typeBadge = 'badge-sim';

            const capVal = w.capital !== undefined ? w.capital : w.balance;
            const capHtml = w.account_type === 'DEMO' ? `<span class="text-muted small">---</span>` : formatMoney(capVal);

            return `
                <tr id="wallet-row-${w.id}">
                    <td>
                        <div class="font-weight-bold text-dark">${w.name}</div>
                        <small class="text-muted">${(w.allowed_symbols || []).join(', ')}</small>
                    </td>
                    <td><span class="badge ${typeBadge}">${w.account_type_display}</span></td>
                    <td class="font-monospace font-weight-bold text-primary">#${w.mt5_login}</td>
                    <td><span class="badge bg-light text-dark border font-monospace">${w.mt5_server}</span></td>
                    <td class="font-weight-bold text-secondary wallet-cell-capital" data-val="${capVal}">${capHtml}</td>
                    <td class="font-weight-bold text-success wallet-cell-balance" data-val="${w.balance}">${formatMoney(w.balance)}</td>
                    <td class="font-weight-bold text-primary wallet-cell-equity" data-val="${w.equity}">${formatMoney(w.equity)}</td>
                    <td class="wallet-cell-floating">${formatPnl(w.floating_pnl, { asBadge: true })}</td>
                    <td class="wallet-cell-today">${formatPnl(w.today_pnl, { asBadge: true })}</td>
                    <td class="wallet-cell-wr">
                        <div><b>${w.win_rate}%</b></div>
                        <small class="text-muted">${w.total_trades} lệnh</small>
                    </td>
                    <td class="wallet-cell-status">
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

function selectGoldOnly() {
    document.querySelectorAll('.symbol-checkbox').forEach(chk => {
        chk.checked = (chk.value === 'XAUUSD');
    });
    updateSelectedSymbolsCount();
}

function onAccountTypeChanged() {
    const isDemo = document.getElementById('wallet-type-demo') ? document.getElementById('wallet-type-demo').checked : true;
    const serverSelect = document.getElementById('wallet-mt5-server');
    const capContainer = document.getElementById('wallet-capital-container');
    const nameCol = document.getElementById('wallet-name-col');
    const capInput = document.getElementById('wallet-capital');

    if (isDemo) {
        if (capContainer) capContainer.classList.add('d-none');
        if (nameCol) {
            nameCol.classList.remove('col-md-7');
            nameCol.classList.add('col-12');
        }
        if (capInput) capInput.required = false;
    } else {
        if (capContainer) capContainer.classList.remove('d-none');
        if (nameCol) {
            nameCol.classList.remove('col-12');
            nameCol.classList.add('col-md-7');
        }
        if (capInput) capInput.required = true;
    }

    if (serverSelect && masterDataCache && masterDataCache.servers) {
        const currentVal = serverSelect.value;
        const filteredServers = masterDataCache.servers.filter(s => {
            if (isDemo) return s.server_type === 'DEMO' || s.server_name.includes('Trial') || s.server_name.includes('Demo');
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
    
    // Default to Demo mode
    const demoRadio = document.getElementById('wallet-type-demo');
    if (demoRadio) demoRadio.checked = true;
    const capInput = document.getElementById('wallet-capital') || document.getElementById('wallet-balance');
    if (capInput) capInput.value = '1000';
    onAccountTypeChanged();

    const passInput = document.getElementById('wallet-mt5-password');
    passInput.value = '';
    passInput.required = true;
    const passHelp = document.getElementById('wallet-password-help');
    if (passHelp) passHelp.innerText = 'Mật khẩu MT5 giao dịch';
    document.getElementById('wallet-risk-percent').value = '1.5';
    document.getElementById('wallet-max-daily-loss').value = '4.0';
    document.getElementById('wallet-max-open-trades').value = '5';
    document.getElementById('wallet-bot-status').value = 'RUNNING';
    document.getElementById('wallet-is-active').checked = true;

    // Default to XAUUSD
    selectGoldOnly();

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
    const capInput = document.getElementById('wallet-capital') || document.getElementById('wallet-balance');
    if (capInput) capInput.value = w.capital !== undefined ? w.capital : (w.balance !== undefined ? w.balance : 1000);
    
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
    document.getElementById('wallet-risk-percent').value = w.risk_percent;
    document.getElementById('wallet-max-daily-loss').value = w.max_daily_loss_percent || 4.0;
    document.getElementById('wallet-max-open-trades').value = w.max_open_trades || 5;
    document.getElementById('wallet-bot-status').value = w.bot_status;
    document.getElementById('wallet-is-active').checked = w.is_active;

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
            const balStr = acc.balance !== undefined ? ` (Số dư thực tế: $${Number(acc.balance).toLocaleString('en-US', {minimumFractionDigits: 2})})` : '';
            showToast(`✅ ${data.message}${balStr}`, 'success');
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

    const saveBtn = document.getElementById('btn-save-wallet');
    const origSaveHtml = saveBtn ? saveBtn.innerHTML : '';
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span> Đang xác thực thông tin...`;
    }

    const capInput = document.getElementById('wallet-capital') || document.getElementById('wallet-balance');
    const capitalVal = capInput && capInput.value.trim() !== '' ? parseFloat(capInput.value) : 1000;

    const payload = {
        name: name,
        mt5_login: mt5_login,
        mt5_password: mt5_password,
        mt5_server: mt5_server,
        account_type: account_type,
        capital: capitalVal,
        risk_percent: parseFloat(document.getElementById('wallet-risk-percent').value) || 1.5,
        max_daily_loss_percent: parseFloat(document.getElementById('wallet-max-daily-loss').value) || 4.0,
        max_open_trades: parseInt(document.getElementById('wallet-max-open-trades').value) || 5,
        allowed_symbols: selectedSymbols,
        bot_status: document.getElementById('wallet-bot-status').value,
        is_active: document.getElementById('wallet-is-active').checked
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


