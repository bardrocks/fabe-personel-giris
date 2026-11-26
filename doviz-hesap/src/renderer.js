(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d) => new Intl.NumberFormat('tr-TR', { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
  const STALE_WARN_MS = 30000;  // 30 sn güncellenmezse uyar
  const STALE_BLOCK_MS = 90000; // 90 sn güncellenmezse sonuçları gösterme

  let settings = null;
  let state = null;

  // ---------- Kurlar ----------
  function renderRates() {
    const r = state && state.rates;
    const set = (id, v, d) => { $(id).textContent = v == null ? '…' : fmt(v, d); };
    if (r) {
      set('usdtl-alis', r['USD/TL'].alis, 4); set('usdtl-satis', r['USD/TL'].satis, 4);
      set('eurtl-alis', r['EUR/TL'].alis, 4); set('eurtl-satis', r['EUR/TL'].satis, 4);
      set('eurusd-alis', r['EUR/USD'].alis, 4); set('eurusd-satis', r['EUR/USD'].satis, 4);
    }
    // Hesapta kullanılan hücreleri vurgula
    document.querySelectorAll('.rates td.used').forEach((td) => td.classList.remove('used'));
    if (settings) {
      $('usdtl-' + settings.tlSide).classList.add('used');
      $('eurusd-' + settings.pariteSide).classList.add('used');
    }
    renderStatus();
    compute();
  }

  function ageMs() {
    return state && state.lastOkAt ? Date.now() - state.lastOkAt : Infinity;
  }

  function renderStatus() {
    const age = ageMs();
    const dot = $('dot');
    const warn = $('warn');
    dot.className = 'dot';
    warn.classList.add('hidden');
    if (!state || !state.lastOkAt) {
      dot.classList.add(state && !state.ok ? 'bad' : 'wait');
      $('status').textContent = state && !state.ok ? state.error : 'Kurlar yükleniyor…';
      return;
    }
    const t = new Date(state.lastOkAt).toLocaleTimeString('tr-TR');
    if (age < STALE_WARN_MS) {
      dot.classList.add('ok');
      $('status').textContent = `Canlı · son okuma ${t}`;
    } else {
      dot.classList.add(age < STALE_BLOCK_MS ? 'wait' : 'bad');
      $('status').textContent = `Son okuma ${t}`;
      warn.textContent = age < STALE_BLOCK_MS
        ? `Kurlar ${Math.round(age / 1000)} sn'dir güncellenmedi. ${state.ok ? '' : state.error || ''}`
        : `Kurlar güncel değil, hesap gösterilmiyor. ${state.ok ? '' : state.error || ''} "Yenile"ye basın.`;
      warn.classList.remove('hidden');
    }
  }

  // ---------- Hesap ----------
  function compute() {
    const usd = Calc.parseAmount($('amount').value);
    const r = state && state.rates;
    const blocked = ageMs() >= STALE_BLOCK_MS;
    $('resTl').dataset.copy = ''; $('resEur').dataset.copy = '';
    if (!r || !settings || blocked) {
      $('valTl').textContent = '–'; $('valEur').textContent = '–';
      $('subTl').innerHTML = '&nbsp;'; $('subEur').innerHTML = '&nbsp;';
      return;
    }
    let c;
    try { c = Calc.convert(Number.isFinite(usd) ? usd : 0, r, settings); } catch (_) { return; }
    $('subTl').textContent = `kur ${fmt(c.usdTl, 4)}`;
    $('subEur').textContent = `parite ${fmt(c.parite, 4)}`;
    if (!Number.isFinite(usd) || usd <= 0) {
      $('valTl').textContent = '–'; $('valEur').textContent = '–';
      return;
    }
    $('valTl').textContent = fmt(c.tl, 2);
    $('valEur').textContent = fmt(c.eur, 2);
    $('resTl').dataset.copy = fmt(c.tl, 2);
    $('resEur').dataset.copy = fmt(c.eur, 2);
  }

  let toastTimer = null;
  function copyResult(el) {
    if (!el.dataset.copy) return;
    window.api.copy(el.dataset.copy);
    const t = $('toast');
    t.textContent = `Kopyalandı: ${el.dataset.copy}`;
    t.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add('hidden'), 1400);
  }

  // ---------- Ayarlar ----------
  const numToField = (n) => fmt(Number(n) || 0, 4);
  function parseSigned(text) {
    const s = String(text).trim();
    const neg = /^[-−]/.test(s);
    const v = Calc.parseAmount(s.replace(/^[-+−]/, '') || '0');
    return Number.isFinite(v) ? (neg ? -v : v) : NaN;
  }
  function openSettings() {
    $('tlSide').value = settings.tlSide;
    $('tlAdjust').value = numToField(settings.tlAdjust);
    $('pariteSide').value = settings.pariteSide;
    $('pariteAdjust').value = numToField(settings.pariteAdjust);
    $('alwaysOnTop').checked = !!settings.alwaysOnTop;
    $('openAtLogin').checked = !!settings.openAtLogin;
    $('settingsErr').classList.add('hidden');
    $('viewMain').classList.add('hidden');
    $('viewSettings').classList.remove('hidden');
  }
  function closeSettings() {
    $('viewSettings').classList.add('hidden');
    $('viewMain').classList.remove('hidden');
    $('amount').focus();
  }
  async function saveSettings() {
    const tlAdjust = parseSigned($('tlAdjust').value);
    const pariteAdjust = parseSigned($('pariteAdjust').value);
    if (!Number.isFinite(tlAdjust) || !Number.isFinite(pariteAdjust) || Math.abs(pariteAdjust) >= 0.5 || Math.abs(tlAdjust) >= 10) {
      $('settingsErr').textContent = 'Düzeltme değerleri geçersiz.';
      $('settingsErr').classList.remove('hidden');
      return;
    }
    settings = await window.api.saveSettings({
      tlSide: $('tlSide').value,
      tlAdjust,
      pariteSide: $('pariteSide').value,
      pariteAdjust,
      alwaysOnTop: $('alwaysOnTop').checked,
      openAtLogin: $('openAtLogin').checked,
    });
    closeSettings();
    renderRates();
  }

  // ---------- Başlat ----------
  async function init() {
    const data = await window.api.getInitial();
    settings = data.settings;
    state = data.state;
    $('shortcut').textContent = data.shortcut;
    renderRates();

    window.api.onRates((s) => {
      // Hata durumunda son geçerli kurları koru, sadece durumu güncelle
      state = { ...s, rates: s.rates || (state && state.rates) };
      renderRates();
    });
    window.api.onFocusInput(() => { if (!$('viewMain').classList.contains('hidden')) { $('amount').focus(); $('amount').select(); } });

    $('amount').addEventListener('input', compute);
    $('amount').addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { $('amount').value = ''; compute(); }
      if (e.key === 'Enter') copyResult($('resTl'));
    });
    $('resTl').addEventListener('click', () => copyResult($('resTl')));
    $('resEur').addEventListener('click', () => copyResult($('resEur')));
    $('btnClose').addEventListener('click', () => window.api.hide());
    $('btnMin').addEventListener('click', () => window.api.minimize());
    $('btnRefresh').addEventListener('click', () => { window.api.refresh(); $('status').textContent = 'Yenileniyor…'; });
    $('btnSettings').addEventListener('click', () => ($('viewSettings').classList.contains('hidden') ? openSettings() : closeSettings()));
    $('btnCancel').addEventListener('click', closeSettings);
    $('btnSave').addEventListener('click', saveSettings);
    setInterval(() => { renderStatus(); compute(); }, 1000);
    $('amount').focus();
  }
  init();
})();
