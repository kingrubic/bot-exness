// Logic cho Trang Chủ (Home Dashboard)

async function fetchGlobalOverview() {
  try {
    const res = await fetch('/api/overview/');
    const data = await res.json();
    
    document.getElementById('stat-total-balance').innerText = formatCurrency(data.total_balance);
    document.getElementById('stat-total-equity').innerText = formatCurrency(data.total_equity);
    
    const floatingEl = document.getElementById('stat-total-floating');
    floatingEl.innerText = (data.total_floating_pnl >= 0 ? '+' : '') + formatCurrency(data.total_floating_pnl);
    floatingEl.className = 'stat-value ' + (data.total_floating_pnl >= 0 ? 'profit' : 'loss');

    const todayEl = document.getElementById('stat-total-today');
    todayEl.innerText = (data.total_today_pnl >= 0 ? '+' : '') + formatCurrency(data.total_today_pnl);
    todayEl.className = 'stat-value ' + (data.total_today_pnl >= 0 ? 'profit' : 'loss');

    document.getElementById('stat-overall-winrate').innerText = data.overall_winrate + '%';
    document.getElementById('stat-total-trades').innerText = data.total_trades;
    document.getElementById('stat-wallets-count').innerText = data.total_wallets_count;
    document.getElementById('stat-active-positions-count').innerText = data.active_positions_count;
    
    if (document.getElementById('last-updated-text')) {
      document.getElementById('last-updated-text').innerText = 'Cập nhật: ' + data.updated_at;
    }
  } catch (err) {
    console.error('Lỗi khi tải tổng quan:', err);
  }
}

async function fetchWalletsList() {
  try {
    const res = await fetch('/api/wallets/');
    const wallets = await res.json();
    const container = document.getElementById('wallets-grid-container');

    if (!wallets || wallets.length === 0) {
      container.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 4rem 1rem;" class="card">
          <p style="color: var(--text-muted); font-size: 1.1rem; margin-bottom: 1rem;">Chưa có ví Exness nào được kết nối.</p>
          <a href="/admin-panel/" class="btn btn-primary">+ Kết Nối Ví Đầu Tiên</a>
        </div>
      `;
      return;
    }

    container.innerHTML = wallets.map(w => {
      const typeClass = w.account_type === 'REAL' ? 'real-account' : (w.account_type === 'DEMO' ? 'demo-account' : 'sim-account');
      const badgeClass = w.account_type === 'REAL' ? 'badge-real' : (w.account_type === 'DEMO' ? 'badge-demo' : 'badge-sim');
      const floatingClass = w.floating_pnl >= 0 ? 'profit-text' : 'loss-text';
      const todayClass = w.today_pnl >= 0 ? 'profit-text' : 'loss-text';

      const symbolsBadges = (w.allowed_symbols || ['XAUUSD']).map(s => `<span class="badge" style="background: rgba(255,255,255,0.08); font-size: 0.68rem;">${s}</span>`).join(' ');

      return `
        <div class="wallet-card ${typeClass}">
          <div class="wallet-header-top">
            <div>
              <div class="wallet-name">${escapeHtml(w.name)}</div>
              <div class="wallet-meta">MT5 #${w.mt5_login} &bull; ${w.mt5_server}</div>
            </div>
            <span class="badge ${badgeClass}">${w.account_type_display}</span>
          </div>

          <div style="display: flex; gap: 0.4rem; flex-wrap: wrap;">
            ${symbolsBadges}
          </div>

          <div class="wallet-stats-row">
            <div class="stat-box">
              <span class="title">Số Dư (Balance)</span>
              <span class="val">${formatCurrency(w.balance)}</span>
            </div>
            <div class="stat-box">
              <span class="title">Vốn (Equity)</span>
              <span class="val">${formatCurrency(w.equity)}</span>
            </div>
            <div class="stat-box">
              <span class="title">Lãi Tạm Tính</span>
              <span class="val ${floatingClass}">${(w.floating_pnl >= 0 ? '+' : '')}${formatCurrency(w.floating_pnl)}</span>
            </div>
            <div class="stat-box">
              <span class="title">Lãi Hôm Nay</span>
              <span class="val ${todayClass}">${(w.today_pnl >= 0 ? '+' : '')}${formatCurrency(w.today_pnl)}</span>
            </div>
          </div>

          <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; color: var(--text-muted);">
            <div>Winrate: <strong style="color: var(--accent-green);">${w.win_rate}%</strong> (${w.total_trades} lệnh)</div>
            <div>Đang chạy: <strong style="color: var(--accent-cyan);">${w.active_trades_count}</strong> lệnh</div>
          </div>

          <div style="display: flex; gap: 0.6rem; align-items: center;">
            <a href="/wallet/${w.id}/" class="btn btn-primary" style="flex: 1;">
              Xem Chi Tiết Ví & Lệnh &rarr;
            </a>
            <span class="badge ${w.bot_status === 'RUNNING' ? 'badge-running' : 'badge-paused'}">
              ${w.bot_status === 'RUNNING' ? '● Bot Chạy' : '❚❚ Tạm Dừng'}
            </span>
          </div>
        </div>
      `;
    }).join('');

  } catch (err) {
    console.error('Lỗi khi tải danh sách ví:', err);
  }
}

function formatCurrency(val) {
  const num = parseFloat(val) || 0;
  return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(text) {
  if (!text) return '';
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function refreshData() {
  fetchGlobalOverview();
  fetchWalletsList();
}

// Initial load
document.addEventListener('DOMContentLoaded', () => {
  refreshData();
});
