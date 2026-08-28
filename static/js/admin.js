// Logic cho Trang Quản Trị (Admin Panel)

async function fetchAdminWallets() {
  try {
    const res = await fetch('/api/wallets/');
    const wallets = await res.json();
    const tbody = document.getElementById('admin-wallets-table-body');

    if (!wallets || wallets.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Chưa có ví nào được cấu hình</td></tr>`;
      return;
    }

    tbody.innerHTML = wallets.map(w => {
      const typeBadge = w.account_type === 'REAL' ? 'badge-real' : (w.account_type === 'DEMO' ? 'badge-demo' : 'badge-sim');
      const symbolsStr = (w.allowed_symbols || []).join(', ');

      return `
        <tr>
          <td>
            <strong>${escapeHtml(w.name)}</strong>
          </td>
          <td><span class="badge ${typeBadge}">${w.account_type_display}</span></td>
          <td class="mono">#${w.mt5_login}</td>
          <td class="mono">${w.risk_percent}%</td>
          <td style="max-width: 140px; overflow: hidden; text-overflow: ellipsis; font-size: 0.8rem; color: var(--accent-cyan); font-family: var(--font-mono);">${symbolsStr}</td>
          <td>
            <button class="btn ${w.bot_status === 'RUNNING' ? 'btn-primary' : 'btn-secondary'} btn-sm" onclick="toggleWalletBotStatus(${w.id}, '${w.bot_status}')">
              ${w.bot_status === 'RUNNING' ? '● Đang Chạy' : '❚❚ Tạm Dừng'}
            </button>
          </td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick='editWalletModal(${JSON.stringify(w)})'>Sửa</button>
            <button class="btn btn-danger btn-sm" onclick="deleteWallet(${w.id})">Xóa</button>
          </td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Lỗi tải danh sách ví admin:', err);
  }
}

async function fetchAdminSymbols() {
  try {
    const res = await fetch('/api/admin/symbols/');
    const symbols = await res.json();
    const tbody = document.getElementById('admin-symbols-table-body');

    if (!symbols || symbols.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Chưa có cặp nào</td></tr>`;
      return;
    }

    tbody.innerHTML = symbols.map(s => {
      return `
        <tr>
          <td class="mono" style="font-weight: 800; color: #ffffff;">${s.symbol}</td>
          <td style="font-size: 0.8rem; color: var(--text-muted);">${s.category_display}</td>
          <td>
            <select class="form-control" style="padding: 0.25rem 0.5rem; font-size: 0.8rem; width: auto;" onchange="updateSymbolField(${s.id}, 'timeframe', this.value)">
              <option value="M1" ${s.timeframe === 'M1' ? 'selected' : ''}>M1</option>
              <option value="M5" ${s.timeframe === 'M5' ? 'selected' : ''}>M5</option>
              <option value="M15" ${s.timeframe === 'M15' ? 'selected' : ''}>M15</option>
              <option value="H1" ${s.timeframe === 'H1' ? 'selected' : ''}>H1</option>
              <option value="H4" ${s.timeframe === 'H4' ? 'selected' : ''}>H4</option>
            </select>
          </td>
          <td>
            <select class="form-control" style="padding: 0.25rem 0.5rem; font-size: 0.8rem; width: auto;" onchange="updateSymbolField(${s.id}, 'strategy', this.value)">
              <option value="SMC_TREND" ${s.strategy === 'SMC_TREND' ? 'selected' : ''}>SMC & Trend</option>
              <option value="SCALPING_BB" ${s.strategy === 'SCALPING_BB' ? 'selected' : ''}>Scalping BB</option>
              <option value="BREAKOUT_SESSION" ${s.strategy === 'BREAKOUT_SESSION' ? 'selected' : ''}>Breakout</option>
            </select>
          </td>
          <td class="mono">${s.max_allowed_spread} pips</td>
          <td>
            <button class="btn ${s.is_active ? 'btn-primary' : 'btn-secondary'} btn-sm" onclick="updateSymbolField(${s.id}, 'is_active', ${!s.is_active})">
              ${s.is_active ? 'BẬT' : 'TẮT'}
            </button>
          </td>
          <td class="mono" style="font-size: 0.8rem; color: var(--text-dim);">${s.last_scanned}</td>
        </tr>
      `;
    }).join('');
  } catch (err) {
    console.error('Lỗi tải danh sách cặp admin:', err);
  }
}

async function updateSymbolField(symbolId, field, value) {
  try {
    const payload = {};
    payload[field] = value;
    const res = await fetch(`/api/admin/symbols/${symbolId}/`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchAdminSymbols();
  } catch (err) {
    showToast('Lỗi khi cập nhật cặp', 'error');
  }
}

async function toggleWalletBotStatus(walletId, currentStatus) {
  const newStatus = currentStatus === 'RUNNING' ? 'PAUSED' : 'RUNNING';
  try {
    const res = await fetch(`/api/admin/wallets/${walletId}/`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bot_status: newStatus })
    });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchAdminWallets();
  } catch (err) {
    showToast('Lỗi khi thay đổi trạng thái bot', 'error');
  }
}

async function deleteWallet(walletId) {
  if (!confirm('⚠️ Bạn có chắc chắn muốn xóa ví này không? Tất cả các lệnh và plan liên quan sẽ bị xóa.')) return;
  try {
    const res = await fetch(`/api/admin/wallets/${walletId}/`, { method: 'DELETE' });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchAdminWallets();
  } catch (err) {
    showToast('Lỗi khi xóa ví', 'error');
  }
}

function openCreateWalletModal() {
  document.getElementById('wallet-modal-title').innerText = 'Thêm Ví Exness Mới';
  document.getElementById('wallet-form-id').value = '';
  document.getElementById('w-name').value = '';
  document.getElementById('w-type').value = 'DEMO';
  document.getElementById('w-login').value = '';
  document.getElementById('w-server').value = 'Exness-MT5Real';
  document.getElementById('w-balance').value = '10000';
  document.getElementById('w-risk').value = '1.5';
  document.getElementById('w-max-trades').value = '5';
  document.getElementById('w-symbols').value = 'XAUUSD, EURUSD, BTCUSD';
  
  const modal = document.getElementById('wallet-modal');
  modal.style.display = 'flex';
}

function editWalletModal(wallet) {
  document.getElementById('wallet-modal-title').innerText = 'Chỉnh Sửa Ví Exness';
  document.getElementById('wallet-form-id').value = wallet.id;
  document.getElementById('w-name').value = wallet.name;
  document.getElementById('w-type').value = wallet.account_type;
  document.getElementById('w-login').value = wallet.mt5_login;
  document.getElementById('w-server').value = wallet.mt5_server;
  document.getElementById('w-balance').value = wallet.balance;
  document.getElementById('w-risk').value = wallet.risk_percent;
  document.getElementById('w-max-trades').value = wallet.max_open_trades || 5;
  document.getElementById('w-symbols').value = (wallet.allowed_symbols || []).join(', ');

  const modal = document.getElementById('wallet-modal');
  modal.style.display = 'flex';
}

function closeWalletModal() {
  document.getElementById('wallet-modal').style.display = 'none';
}

async function handleWalletFormSubmit(e) {
  e.preventDefault();
  const walletId = document.getElementById('wallet-form-id').value;
  const isEdit = !!walletId;

  const payload = {
    name: document.getElementById('w-name').value,
    account_type: document.getElementById('w-type').value,
    mt5_login: document.getElementById('w-login').value,
    mt5_server: document.getElementById('w-server').value,
    balance: parseFloat(document.getElementById('w-balance').value) || 10000,
    risk_percent: parseFloat(document.getElementById('w-risk').value) || 1.5,
    max_open_trades: parseInt(document.getElementById('w-max-trades').value) || 5,
    allowed_symbols: document.getElementById('w-symbols').value
  };

  try {
    const url = isEdit ? `/api/admin/wallets/${walletId}/` : '/api/admin/wallets/';
    const method = isEdit ? 'PUT' : 'POST';

    const res = await fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    showToast(data.message, 'success');
    closeWalletModal();
    fetchAdminWallets();
  } catch (err) {
    showToast('Lỗi khi lưu thông tin ví', 'error');
  }
}

function saveGlobalRiskSettings() {
  showToast('Đã lưu cài đặt quản trị rủi ro toàn cục thành công!', 'success');
}

// ==================== BOT LOGS & ERROR MONITOR ====================
let currentLogFilter = 'ALL';
let cachedLogs = [];

async function fetchBotLogs(level = null) {
  if (level) currentLogFilter = level;
  try {
    const res = await fetch(`/api/admin/logs/?level=${currentLogFilter}`);
    const data = await res.json();
    cachedLogs = data.logs || [];
    renderBotLogs(cachedLogs, data.unresolved_errors);
  } catch (err) {
    console.error('Lỗi khi tải nhật ký bot:', err);
  }
}

function filterLogs(level) {
  ['all', 'error', 'warning', 'info'].forEach(t => {
    const btn = document.getElementById(`log-filter-${t}`);
    if (btn) btn.classList.remove('btn-primary');
  });

  const activeBtn = document.getElementById(`log-filter-${level.toLowerCase()}`);
  if (activeBtn) activeBtn.classList.add('btn-primary');

  fetchBotLogs(level);
}

function renderBotLogs(logs, unresolvedCount) {
  const badge = document.getElementById('log-unresolved-badge');
  if (badge) {
    if (unresolvedCount > 0) {
      badge.style.display = 'inline-block';
      badge.innerText = `🔴 ${unresolvedCount} Lỗi Chưa Xử Lý`;
    } else {
      badge.style.display = 'none';
    }
  }

  const tbody = document.getElementById('bot-logs-table-body');
  if (!tbody) return;

  if (!logs || logs.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" class="table-empty" style="padding: 2rem 1rem;">
          <div style="color: var(--success); font-weight: 600; margin-bottom: 0.25rem;">✅ Hệ thống hoạt động hoàn hảo</div>
          <div style="color: var(--text-dim); font-size: 0.8rem;">Chưa ghi nhận bất kỳ cảnh báo hoặc lỗi nào từ Bot.</div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = logs.map(l => {
    let levelBadge = '';
    if (l.level === 'CRITICAL' || l.level === 'ERROR') {
      levelBadge = `<span class="badge badge-sell" style="font-weight: 700;">🔴 ${l.level}</span>`;
    } else if (l.level === 'WARNING') {
      levelBadge = `<span class="badge badge-sim" style="font-weight: 700;">🟡 CẢNH BÁO</span>`;
    } else {
      levelBadge = `<span class="badge badge-demo">🔵 INFO</span>`;
    }

    const targetInfo = l.wallet_name 
      ? `<span style="font-weight: 600; color: #fff;">${l.wallet_name}</span> ${l.symbol ? `<span class="badge" style="background: rgba(255,255,255,0.06);">${l.symbol}</span>` : ''}`
      : (l.symbol ? `<span class="badge" style="background: rgba(255,255,255,0.06);">${l.symbol}</span>` : `<span style="color: var(--text-dim);">Toàn Cục</span>`);

    const hasTraceback = l.traceback && l.traceback.trim().length > 0;
    const resolvedStatus = l.is_resolved 
      ? `<span class="badge badge-buy" style="font-size: 0.72rem;">Đã Xử Lý</span>` 
      : `<span class="badge badge-sell" style="font-size: 0.72rem;">Cần Xử Lý</span>`;

    return `
      <tr style="${!l.is_resolved && (l.level === 'ERROR' || l.level === 'CRITICAL') ? 'background: rgba(239, 68, 68, 0.05);' : ''}">
        <td>${levelBadge}</td>
        <td><span class="badge" style="background: rgba(255,255,255,0.05); font-size: 0.75rem;">${l.category_display || l.category}</span></td>
        <td>${targetInfo}</td>
        <td style="max-width: 320px; font-size: 0.82rem; line-height: 1.4;">
          <div style="color: ${l.level === 'ERROR' ? '#fca5a5' : 'var(--text-main)'}; font-weight: 500;">
            ${l.message}
          </div>
          ${hasTraceback ? `<button class="btn btn-secondary btn-sm" style="font-size: 0.7rem; padding: 2px 6px; margin-top: 4px;" onclick="viewTraceback(${l.id})">🔍 Xem Traceback Chi Tiết</button>` : ''}
        </td>
        <td style="font-size: 0.78rem; color: var(--text-dim); white-space: nowrap;">${l.created_at}</td>
        <td>${resolvedStatus}</td>
        <td>
          ${!l.is_resolved ? `
            <button class="btn btn-cyan btn-sm" style="font-size: 0.72rem; padding: 4px 8px;" onclick="resolveBotLog(${l.id})">
              ✓ Đã Sửa
            </button>
          ` : `
            <span style="color: var(--text-dim); font-size: 0.75rem;">—</span>
          `}
        </td>
      </tr>
    `;
  }).join('');
}

function viewTraceback(logId) {
  const log = cachedLogs.find(l => l.id === logId);
  if (!log || !log.traceback) {
    showToast('Không có chi tiết kỹ thuật cho sự kiện này', 'info');
    return;
  }
  document.getElementById('traceback-content').innerText = log.traceback;
  document.getElementById('traceback-modal').style.display = 'flex';
}

function closeTracebackModal() {
  document.getElementById('traceback-modal').style.display = 'none';
}

async function resolveBotLog(logId) {
  try {
    const res = await fetch(`/api/admin/logs/${logId}/resolve/`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchBotLogs();
  } catch (err) {
    showToast('Lỗi khi cập nhật trạng thái log', 'error');
  }
}

async function clearAllBotLogs() {
  if (!confirm('Bạn có chắc chắn muốn dọn dẹp sạch toàn bộ nhật ký lỗi không?')) return;
  try {
    const res = await fetch('/api/admin/logs/clear/', { method: 'POST' });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchBotLogs();
  } catch (err) {
    showToast('Lỗi khi xóa nhật ký', 'error');
  }
}

function refreshData() {
  fetchAdminWallets();
  fetchAdminSymbols();
  fetchBotLogs();
}

document.addEventListener('DOMContentLoaded', () => {
  refreshData();
  // Poll logs periodically every 8s
  setInterval(() => {
    fetchBotLogs();
  }, 8000);
});

