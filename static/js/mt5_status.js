(function () {
    const API_STATUS = '/api/mt5/status/';
    const API_LAUNCH = '/api/mt5/launch/';
    const POLL_MS = 5000;

    let algoDismissedWhileOff = false;
    let lastAlgoOff = false;
    let hidingBecauseReady = false;

    function injectStyles() {
        if (document.getElementById('mt5-setup-banner-css')) return;
        const css = document.createElement('style');
        css.id = 'mt5-setup-banner-css';
        css.textContent = `
            .mt5-setup-banner { margin: 0 0 1.25rem; border-radius: 12px; border: 1px solid #f59e0b; background: linear-gradient(180deg,#fffbeb 0%,#fef3c7 100%); color: #78350f; box-shadow: 0 8px 24px rgba(245,158,11,.18); }
            .mt5-setup-banner__inner { display: flex; gap: 14px; padding: 16px 18px; align-items: flex-start; }
            .mt5-setup-banner__icon { font-size: 1.6rem; color: #d97706; line-height: 1; padding-top: 2px; }
            .mt5-setup-banner__title { display: block; font-size: 1.05rem; margin-bottom: .35rem; }
            .mt5-setup-banner__lead { font-size: .9rem; color: #92400e; }
            .mt5-setup-banner__steps { margin: 0 0 .75rem 1.1rem; padding: 0; font-size: .88rem; }
            .mt5-setup-banner__steps li { margin-bottom: .28rem; }
            .mt5-setup-banner__actions { display: flex; flex-wrap: wrap; gap: 8px; }
            .login-content .mt5-setup-banner { text-align: left; margin-bottom: 1rem; }
            .mt5-algo-modal .modal-content { border: 0; }
            .mt5-algo-modal .algo-step { background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px; padding: .75rem 1rem; margin-bottom: .6rem; }
        `;
        document.head.appendChild(css);
    }

    function ensureBanner() {
        let el = document.getElementById('mt5-setup-banner');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'mt5-setup-banner';
        el.className = 'mt5-setup-banner d-none';
        el.setAttribute('role', 'alert');
        const host = document.querySelector('#main-content .container-fluid')
            || document.querySelector('.login-content')
            || document.body;
        host.insertBefore(el, host.firstChild);
        return el;
    }

    function ensureAlgoModal() {
        let el = document.getElementById('modal-mt5-algo');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'modal-mt5-algo';
        el.className = 'modal fade mt5-algo-modal';
        el.tabIndex = -1;
        el.setAttribute('aria-labelledby', 'modalMt5AlgoLabel');
        el.setAttribute('aria-hidden', 'true');
        el.innerHTML = `
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content rounded-4 border-0 shadow-lg overflow-hidden">
                    <div class="modal-header border-0 px-4 py-3 text-white" style="background: linear-gradient(135deg,#d97706,#b45309);">
                        <h5 class="modal-title font-weight-bold" id="modalMt5AlgoLabel">
                            <i class="fa-solid fa-bolt me-2"></i>Cần bật Algo Trading
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body p-4">
                        <p class="mb-3 text-dark">
                            MetaTrader 5 đang mở và ví đang bật, nhưng nút <b>Algo Trading đang tắt</b>.
                            Bot không gửi / đóng lệnh được cho đến khi bạn bật Algo.
                        </p>
                        <div class="algo-step">
                            <b>1.</b> Trên thanh công cụ MT5, bấm <b>Algo Trading</b> cho đến khi nút chuyển <span class="text-success">màu xanh</span>.
                        </div>
                        <div class="algo-step mb-0">
                            <b>2.</b> Tools → Options → Expert Advisors: tích <b>Allow algorithmic trading</b>.
                        </div>
                    </div>
                    <div class="modal-footer border-0 px-4 py-3 bg-light">
                        <button type="button" class="btn btn-secondary btn-sm rounded-pill px-3" data-bs-dismiss="modal">Đã hiểu</button>
                        <button type="button" class="btn btn-warning btn-sm rounded-pill px-3" id="mt5-algo-recheck-btn">
                            <i class="fa-solid fa-rotate me-1"></i> Kiểm tra lại
                        </button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(el);
        el.addEventListener('hidden.bs.modal', function () {
            if (!hidingBecauseReady) algoDismissedWhileOff = true;
            hidingBecauseReady = false;
        });
        el.addEventListener('click', function (e) {
            if (e.target && e.target.id === 'mt5-algo-recheck-btn') {
                refresh();
            }
        });
        return el;
    }

    function showAlgoModal(force) {
        if (!force && algoDismissedWhileOff) return;
        const el = ensureAlgoModal();
        if (window.bootstrap && bootstrap.Modal) {
            const modal = bootstrap.Modal.getOrCreateInstance(el);
            modal.show();
            return;
        }
        window.alert('Cần bật Algo Trading trên MetaTrader 5 (nút phải màu xanh) thì ví/bot mới hoạt động được.');
    }

    function hideAlgoModal() {
        const el = document.getElementById('modal-mt5-algo');
        if (!el) return;
        hidingBecauseReady = true;
        if (window.bootstrap && bootstrap.Modal) {
            const inst = bootstrap.Modal.getInstance(el);
            if (inst) inst.hide();
        }
    }

    function walletIsOn(data) {
        return !!(data && (data.wallet_active || data.wallet_running));
    }

    function render(data) {
        const el = ensureBanner();
        const algoOff = data && data.code === 'algo_off';
        const needAlgoPopup = algoOff && walletIsOn(data);

        if (needAlgoPopup) {
            if (!lastAlgoOff) algoDismissedWhileOff = false;
            showAlgoModal(false);
        } else {
            hideAlgoModal();
            if (!algoOff) algoDismissedWhileOff = false;
        }
        lastAlgoOff = !!algoOff;

        if (!data || data.ok || (algoOff && !walletIsOn(data))) {
            el.classList.add('d-none');
            el.innerHTML = '';
            return;
        }

        const steps = (data.steps || []).map((s) => `<li>${s}</li>`).join('');
        const isAlgo = data.code === 'algo_off';
        el.classList.remove('d-none');
        el.innerHTML = `
            <div class="mt5-setup-banner__inner">
                <div class="mt5-setup-banner__icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
                <div class="mt5-setup-banner__body">
                    <strong class="mt5-setup-banner__title">${data.title || 'Cần bật MetaTrader 5'}</strong>
                    <p class="mt5-setup-banner__lead mb-2">${isAlgo
                        ? 'Ví đang bật nhưng Algo Trading trên MT5 đang tắt — bot không khớp lệnh được.'
                        : 'Bot không lấy giá live và không khớp lệnh được cho đến khi MT5 mở và đã login tài khoản Exness.'}</p>
                    <ol class="mt5-setup-banner__steps">${steps}</ol>
                    <div class="mt5-setup-banner__actions">
                        ${isAlgo ? '' : `<button type="button" class="btn btn-warning btn-sm rounded-pill px-3" id="mt5-launch-btn">
                            <i class="fa-solid fa-play me-1"></i> Mở MetaTrader 5
                        </button>`}
                        <a class="btn btn-outline-dark btn-sm rounded-pill px-3" href="/admin-panel/wallets/">
                            Tới trang Ví
                        </a>
                    </div>
                </div>
            </div>
        `;
        const btn = document.getElementById('mt5-launch-btn');
        if (btn) {
            btn.addEventListener('click', async function () {
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang mở...';
                try {
                    await fetch(API_LAUNCH, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                    });
                } catch (e) { /* ignore */ }
                setTimeout(refresh, 1500);
            });
        }
    }

    async function refresh() {
        try {
            const res = await fetch(API_STATUS, { cache: 'no-store' });
            if (!res.ok) return;
            render(await res.json());
        } catch (e) { /* server chưa sẵn sàng */ }
    }

    window.refreshMt5Status = refresh;
    window.showMt5AlgoPopup = function (force) {
        if (force) algoDismissedWhileOff = false;
        showAlgoModal(!!force);
        refresh();
    };

    injectStyles();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', refresh);
    } else {
        refresh();
    }
    setInterval(refresh, POLL_MS);
})();
