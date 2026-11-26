/*
 * Nadir Döviz fiyat ekranını gizli bir pencerede açar ve ekranda görünen
 * USD/TL, EUR/TL, EUR/USD alış-satış değerlerini düzenli olarak okur.
 * Sayfa kendini canlı güncellediği için değerler sitedekiyle birebir aynıdır.
 */
const { BrowserWindow } = require('electron');
const { validateRates } = require('./calc');

const URL = 'https://www.nadirdoviz.com/fiyat-ekrani';
const READ_INTERVAL_MS = 2000;     // her 2 sn'de bir ekrandan oku
const STALE_RELOAD_MS = 45000;     // 45 sn boyunca geçerli okuma yoksa sayfayı yenile
const PERIODIC_RELOAD_MS = 10 * 60 * 1000; // bağlantı sessizce kopmasın diye 10 dk'da bir yenile

// Sayfa içinde çalışan okuma kodu: her satırda <h5 class="card-title">USD/TL</h5>
// ve ardından iki adet .rounded-pill (Alış, Satış) bulunuyor.
const EXTRACT_JS = `(() => {
  const want = ['USD/TL', 'EUR/TL', 'EUR/USD'];
  const out = {};
  for (const h of document.querySelectorAll('h5.card-title')) {
    const name = (h.textContent || '').trim();
    if (!want.includes(name) || out[name]) continue;
    const row = h.closest('.row');
    const pills = row ? row.querySelectorAll('.rounded-pill') : [];
    if (pills.length < 2) continue;
    out[name] = { alis: pills[0].textContent.trim(), satis: pills[1].textContent.trim() };
  }
  return out;
})()`;

class Scraper {
  constructor(onUpdate) {
    this.onUpdate = onUpdate; // ({ok, rates?, error?, lastOkAt})
    this.win = null;
    this.lastOkAt = 0;
    this.loadedAt = 0;
    this.lastKey = '';
    this.timer = null;
  }

  start() {
    this.win = new BrowserWindow({
      show: false,
      width: 1300,
      height: 900,
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        backgroundThrottling: false,
        partition: 'persist:nadirdoviz',
      },
    });
    this.win.webContents.setAudioMuted(true);
    this.win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    this.win.webContents.on('did-fail-load', (_e, code, desc, _url, isMain) => {
      if (!isMain || code === -3) return; // -3: iptal (yenileme sırasında normal)
      this.report({ ok: false, error: `Siteye bağlanılamadı (${desc}). Tekrar deneniyor…` });
      setTimeout(() => this.reload(), 5000);
    });
    this.win.webContents.on('render-process-gone', () => setTimeout(() => this.reload(), 2000));
    this.reload();
    this.timer = setInterval(() => this.tick(), READ_INTERVAL_MS);
  }

  reload() {
    if (!this.win || this.win.isDestroyed()) return;
    this.loadedAt = Date.now();
    this.win.loadURL(URL).catch(() => {});
  }

  report(state) {
    this.onUpdate({ ...state, lastOkAt: this.lastOkAt });
  }

  async tick() {
    if (!this.win || this.win.isDestroyed()) return;
    const now = Date.now();
    if (now - this.loadedAt > PERIODIC_RELOAD_MS) return this.reload();

    let raw = null;
    try {
      raw = await this.win.webContents.executeJavaScript(EXTRACT_JS, true);
    } catch (_) { /* sayfa yükleniyor */ }

    const res = validateRates(raw);
    if (res.ok) {
      this.lastOkAt = now;
      const key = JSON.stringify(res.rates);
      this.lastKey = key;
      this.report({ ok: true, rates: res.rates });
      return;
    }

    // Sayfa daha yükleniyorsa (ilk ~15 sn) hata gösterme
    const sinceLoad = now - this.loadedAt;
    if (sinceLoad > 15000) this.report({ ok: false, error: res.error });
    if (now - Math.max(this.lastOkAt, this.loadedAt) > STALE_RELOAD_MS) this.reload();
  }

  refreshNow() {
    this.reload();
  }

  stop() {
    clearInterval(this.timer);
    if (this.win && !this.win.isDestroyed()) this.win.destroy();
  }
}

module.exports = { Scraper, EXTRACT_JS, URL };
