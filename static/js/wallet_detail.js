// Logic cho Trang Chi Tiết Ví (Wallet Detail View)

async function fetchWalletDetail() {
  if (typeof CURRENT_WALLET_ID === 'undefined') return;

  try {
    const res = await fetch(`/api/wallets/${CURRENT_WALLET_ID}/`);
    if (!res.ok) throw new Error('Không thể tải dữ liệu ví');
    const data = await res.json();

    renderWalletReport(data.report);
    renderForecasts(data.forecasts);
    renderPlansTable(data.plans);
    renderPositionsTable(data.positions);
    renderHistoryTable(data.history);
  } catch (err) {
    console.error('Lỗi khi tải chi tiết ví:', err);
  }
}

function renderWalletReport(report) {
  document.getElementById('wallet-header-name').innerText = report.name;
  document.getElementById('wallet-balance').innerText = formatCurrency(report.balance);
  document.getElementById('wallet-equity').innerText = formatCurrency(report.equity);
  
  const floatingEl = document.getElementById('wallet-floating');
  floatingEl.innerText = (report.floating_pnl >= 0 ? '+' : '') + formatCurrency(report.floating_pnl);
  floatingEl.className = 'stat-value ' + (report.floating_pnl >= 0 ? 'profit' : 'loss');

  const todayEl = document.getElementById('wallet-today');
  todayEl.innerText = (report.today_pnl >= 0 ? '+' : '') + formatCurrency(report.today_pnl);
  todayEl.className = 'stat-value ' + (report.today_pnl >= 0 ? 'profit' : 'loss');

  document.getElementById('wallet-winrate').innerText = report.win_rate + '%';
}

function renderForecasts(forecasts) {
  const container = document.getElementById('forecasts-container');
  if (!forecasts || forecasts.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 2.5rem 1rem;" class="card">
        <p style="color: var(--text-muted);">Chưa có dữ liệu phân tích cho các cặp của ví này.</p>
        <button class="btn btn-cyan btn-sm" style="margin-top: 0.8rem;" onclick="triggerManualScan()">Quét Phân Tích Ngay</button>
      </div>
    `;
    return;
  }

  container.innerHTML = forecasts.map(f => {
    const isBullish = f.trend_bias === 'BULLISH';
    const isBearish = f.trend_bias === 'BEARISH';
    const trendBadge = isBullish ? 'badge-buy' : (isBearish ? 'badge-sell' : 'badge-paused');
    const ind = f.indicators || {};

    return `
      <div class="forecast-card">
        <div class="forecast-header">
          <div class="forecast-symbol-tag">
            <h3>${f.symbol}</h3>
            <span class="badge" style="background: rgba(255,255,255,0.08); font-size: 0.75rem;">Khung ${f.timeframe}</span>
            <span class="badge ${trendBadge}">
              ${f.trend_bias_display} &bull; Tin Cậy: ${f.confidence_score}%
            </span>
          </div>
          <div style="font-family: var(--font-mono); font-size: 0.95rem; color: #ffffff;">
            Giá Hiện Tại: <strong style="color: var(--accent-cyan); font-size: 1.15rem;">${f.current_price}</strong>
          </div>
        </div>

        <div class="forecast-grid">
          <div class="forecast-box" style="border-left: 3px solid var(--accent-cyan);">
            <div class="lbl">🎯 VÙNG MỤC TIÊU TIẾP THEO</div>
            <div class="val" style="color: var(--accent-cyan);">${f.projected_target_zone}</div>
          </div>

          <div class="forecast-box" style="border-left: 3px solid var(--accent-red);">
            <div class="lbl">🛑 KHÁNG CỰ KẾ TIẾP (R1 / R2)</div>
            <div class="val" style="color: var(--accent-red); font-size: 0.95rem;">${f.next_resistance_1} / ${f.next_resistance_2}</div>
          </div>

          <div class="forecast-box" style="border-left: 3px solid var(--accent-green);">
            <div class="lbl">🛡️ HỖ TRỢ KẾ TIẾP (S1 / S2)</div>
            <div class="val" style="color: var(--accent-green); font-size: 0.95rem;">${f.next_support_1} / ${f.next_support_2}</div>
          </div>

          <div class="forecast-box">
            <div class="lbl">⚡ ĐIỀU KIỆN KÍCH HOẠT TIẾP THEO</div>
            <div class="val" style="font-size: 0.85rem; font-weight: 500;">${escapeHtml(f.trigger_condition)}</div>
          </div>
        </div>

        <div style="display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.75rem; font-size: 0.78rem; font-family: var(--font-mono); color: var(--text-muted);">
          <span>RSI: <strong style="color:#fff;">${ind.rsi || '--'}</strong></span> &bull;
          <span>EMA50: <strong style="color:#fff;">${ind.ema50 || '--'}</strong></span> &bull;
          <span>EMA200: <strong style="color:#fff;">${ind.ema200 || '--'}</strong></span> &bull;
          <span>MACD Hist: <strong style="color:#fff;">${ind.macd_hist || '--'}</strong></span> &bull;
          <span>ATR: <strong style="color:#fff;">${ind.atr || '--'}</strong></span> &bull;
          <span>Spread: <strong style="color: var(--accent-cyan);">${ind.spread_pips || '--'} pips</strong></span>
        </div>

        <div class="forecast-rationale-box">
          <strong>Cấu trúc SMC / Price Action:</strong> ${escapeHtml(f.smc_structure)}<br>
          <strong>Lý do phân tích của Bot:</strong> ${escapeHtml(f.analysis_rationale)}
        </div>
      </div>
    `;
  }).join('');
}

function renderPlansTable(plans) {
  const tbody = document.getElementById('plans-table-body');
  if (!plans || plans.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" class="table-empty">Chưa có kế hoạch giao dịch nào cho ví này</td></tr>`;
    return;
  }

  tbody.innerHTML = plans.map(p => {
    const isBuy = p.direction === 'BUY';
    const dirBadge = isBuy ? 'badge-buy' : 'badge-sell';
    
    let statusBadge = 'badge-paused';
    if (p.status === 'EXECUTING') statusBadge = 'badge-running';
    else if (p.status === 'COMPLETED') statusBadge = 'badge-buy';
    else if (p.status === 'CANCELLED') statusBadge = 'badge-sell';

    return `
      <tr>
        <td class="mono">#PLN-${p.id}</td>
        <td class="symbol-cell">${p.symbol} <span class="badge" style="font-size: 0.65rem;">${p.timeframe}</span></td>
        <td><span class="badge ${dirBadge}">${p.direction}</span></td>
        <td class="mono" style="color: var(--accent-cyan); font-weight: 700;">${p.entry_price}</td>
        <td class="mono" style="color: var(--accent-red);">${p.stop_loss}</td>
        <td class="mono" style="color: var(--accent-green);">${p.take_profit_1} / ${p.take_profit_2}</td>
        <td class="mono" style="font-weight: 700;">1:${p.rr_ratio}</td>
        <td class="mono">${p.calculated_lot} Lot ($${p.risk_amount_usd})</td>
        <td><span class="badge ${statusBadge}">${p.status_display}</span></td>
        <td style="max-width: 250px; white-space: normal; font-size: 0.8rem; color: var(--text-muted);">${escapeHtml(p.rationale)}</td>
        <td style="font-size: 0.78rem; color: var(--text-dim);">${p.created_at}</td>
      </tr>
    `;
  }).join('');
}

function renderPositionsTable(positions) {
  const tbody = document.getElementById('positions-table-body');
  document.getElementById('wallet-open-count').innerText = positions ? positions.length : 0;

  if (!positions || positions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="13" class="table-empty">Không có vị thế nào đang mở</td></tr>`;
    return;
  }

  tbody.innerHTML = positions.map(pos => {
    const isBuy = pos.position_type === 'BUY';
    const dirBadge = isBuy ? 'badge-buy' : 'badge-sell';
    const pnlClass = pos.floating_pnl >= 0 ? 'profit-text' : 'loss-text';

    return `
      <tr>
        <td class="mono">#${pos.ticket}</td>
        <td class="symbol-cell">${pos.symbol}</td>
        <td><span class="badge ${dirBadge}">${pos.position_type}</span></td>
        <td class="mono">${pos.lot_size} Lot</td>
        <td class="mono">${pos.open_price}</td>
        <td class="mono" style="font-weight: 700;">${pos.current_price}</td>
        <td class="mono" style="color: var(--accent-red);">${pos.stop_loss}</td>
        <td class="mono" style="color: var(--accent-green);">${pos.take_profit}</td>
        <td class="mono ${pnlClass}" style="font-size: 1rem;">
          ${(pos.floating_pnl >= 0 ? '+' : '')}${formatCurrency(pos.floating_pnl)}
        </td>
        <td class="mono ${pnlClass}">${(pos.floating_pips >= 0 ? '+' : '')}${pos.floating_pips} p</td>
        <td>
          <span class="badge" style="background: rgba(0, 229, 255, 0.1); color: var(--accent-cyan); font-size: 0.68rem;">
            ${pos.is_trailing ? 'Trailing ON' : 'OFF'}
          </span>
        </td>
        <td style="font-size: 0.78rem; color: var(--text-dim);">${pos.opened_at}</td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="closeSinglePosition(${pos.id})">
            Đóng Lệnh
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

function renderHistoryTable(history) {
  const tbody = document.getElementById('history-table-body');
  if (!history || history.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="table-empty">Chưa có lịch sử giao dịch</td></tr>`;
    return;
  }

  tbody.innerHTML = history.map(h => {
    const isBuy = h.position_type === 'BUY';
    const dirBadge = isBuy ? 'badge-buy' : 'badge-sell';
    const pnlClass = h.pnl >= 0 ? 'profit-text' : 'loss-text';

    return `
      <tr>
        <td class="mono">#${h.ticket}</td>
        <td class="symbol-cell">${h.symbol}</td>
        <td><span class="badge ${dirBadge}">${h.position_type}</span></td>
        <td class="mono">${h.lot_size} Lot</td>
        <td class="mono">${h.open_price}</td>
        <td class="mono" style="font-weight: 700;">${h.close_price}</td>
        <td class="mono" style="font-size: 0.8rem; color: var(--text-dim);">${h.stop_loss} / ${h.take_profit}</td>
        <td class="mono ${pnlClass}" style="font-size: 0.95rem;">
          ${(h.pnl >= 0 ? '+' : '')}${formatCurrency(h.pnl)}
        </td>
        <td class="mono ${pnlClass}">${(h.pips >= 0 ? '+' : '')}${h.pips} p</td>
        <td>
          <span class="badge" style="background: rgba(255,255,255,0.06); font-size: 0.72rem;">
            ${h.close_reason_display}
          </span>
        </td>
        <td style="font-size: 0.78rem; color: var(--text-dim);">${h.opened_at}</td>
        <td style="font-size: 0.78rem; color: var(--text-dim);">${h.closed_at}</td>
      </tr>
    `;
  }).join('');
}

async function closeSinglePosition(positionId) {
  if (!confirm('Bạn có muốn đóng vị thế này ngay lập tức không?')) return;
  try {
    const res = await fetch(`/api/positions/${positionId}/close/`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchWalletDetail();
  } catch (err) {
    showToast('Lỗi khi đóng vị thế', 'error');
  }
}

async function closeAllWalletPositions(walletId) {
  if (!confirm('⚠️ Đóng toàn bộ các vị thế của ví này?')) return;
  try {
    const res = await fetch(`/api/positions/close-all/${walletId}/`, { method: 'POST' });
    const data = await res.json();
    showToast(data.message, 'success');
    fetchWalletDetail();
  } catch (err) {
    showToast('Lỗi khi đóng lệnh', 'error');
  }
}

function exportHistoryCSV() {
  const rows = document.querySelectorAll('#history-table-body tr');
  if (!rows || rows.length === 0 || rows[0].querySelector('.table-empty')) {
    showToast('Không có dữ liệu lịch sử để xuất CSV', 'error');
    return;
  }

  let csvContent = "Ticket,Symbol,Type,Lot,OpenPrice,ClosePrice,PnL,Pips,Reason,OpenedAt,ClosedAt\n";
  
  rows.forEach(r => {
    const cols = r.querySelectorAll('td');
    if (cols.length >= 12) {
      const ticket = cols[0].innerText.replace('#', '').trim();
      const symbol = cols[1].innerText.trim();
      const type = cols[2].innerText.trim();
      const lot = cols[3].innerText.replace('Lot', '').trim();
      const openPrice = cols[4].innerText.trim();
      const closePrice = cols[5].innerText.trim();
      const pnl = cols[7].innerText.replace('$', '').replace('+', '').trim();
      const pips = cols[8].innerText.replace('p', '').replace('+', '').trim();
      const reason = cols[9].innerText.trim();
      const openedAt = cols[10].innerText.trim();
      const closedAt = cols[11].innerText.trim();
      
      csvContent += `"${ticket}","${symbol}","${type}","${lot}","${openPrice}","${closePrice}","${pnl}","${pips}","${reason}","${openedAt}","${closedAt}"\n`;
    }
  });

  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", `exness_wallet_${CURRENT_WALLET_ID}_history.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showToast('Đã xuất báo cáo CSV thành công!', 'success');
}

function refreshData() {
  fetchWalletDetail();
}

document.addEventListener('DOMContentLoaded', () => {
  fetchWalletDetail();
});
