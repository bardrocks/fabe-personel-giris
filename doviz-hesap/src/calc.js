/*
 * Hesaplama ve sayı ayrıştırma yardımcıları.
 * Hem arayüzde (window.Calc) hem ana süreçte / testlerde (require) kullanılır.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.Calc = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Sitedeki Türkçe biçimli sayıyı çevirir: "50,7349" -> 50.7349, "6.022,76" -> 6022.76
  // Biçim beklenenden farklıysa NaN döner (yanlış sayı göstermektense hiç göstermemek için).
  function parseSiteNumber(text) {
    if (typeof text !== 'string') return NaN;
    const s = text.replace(/[\s ]/g, '');
    if (!/^(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$/.test(s)) return NaN;
    return Number(s.replace(/\./g, '').replace(',', '.'));
  }

  // Kullanıcının yazdığı tutarı çevirir: "240", "240,5", "1.240,50", "240.50", "1,240.50"
  function parseAmount(text) {
    if (typeof text === 'number') return text;
    if (typeof text !== 'string') return NaN;
    let s = text.replace(/[\s $€₺]/g, '');
    if (s === '') return NaN;
    const hasDot = s.includes('.');
    const hasComma = s.includes(',');
    if (hasDot && hasComma) {
      // Sonda hangisi varsa ondalık ayracı odur
      if (s.lastIndexOf(',') > s.lastIndexOf('.')) s = s.replace(/\./g, '').replace(',', '.');
      else s = s.replace(/,/g, '');
    } else if (hasComma) {
      // Tek virgül her zaman ondalıktır (TR alışkanlığı): "240,5" -> 240.5
      const parts = s.split(',');
      s = parts.length === 2 ? parts.join('.') : parts.join('');
    } else if (hasDot) {
      const parts = s.split('.');
      // "1.250" veya "1.250.000" -> binlik ayraç; "240.5" / "240.50" -> ondalık
      if (parts.length > 2 || (parts.length === 2 && parts[1].length === 3)) s = parts.join('');
    }
    if (!/^\d+(\.\d+)?$/.test(s)) return NaN;
    return Number(s);
  }

  function round(value, digits) {
    const f = Math.pow(10, digits);
    return Math.round((value + Number.EPSILON) * f) / f;
  }

  /*
   * rates: { 'USD/TL': {alis, satis}, 'EUR/TL': {...}, 'EUR/USD': {...} } (sayılar)
   * settings: { tlSide, tlAdjust, pariteSide, pariteAdjust }
   */
  function convert(usd, rates, settings) {
    const usdTl = rates['USD/TL'][settings.tlSide] + Number(settings.tlAdjust || 0);
    const parite = rates['EUR/USD'][settings.pariteSide] + Number(settings.pariteAdjust || 0);
    if (!(usdTl > 0) || !(parite > 0)) throw new Error('Geçersiz kur');
    return {
      usdTl: round(usdTl, 4),
      parite: round(parite, 4),
      tl: round(usd * usdTl, 2),
      eur: round(usd / parite, 2),
    };
  }

  /*
   * Siteden okunan ham değerleri doğrular. Hata varsa açıklamasını döner.
   */
  function validateRates(raw) {
    const names = ['USD/TL', 'EUR/TL', 'EUR/USD'];
    const ranges = { 'USD/TL': [1, 10000], 'EUR/TL': [1, 10000], 'EUR/USD': [0.5, 2] };
    const rates = {};
    for (const n of names) {
      const r = raw && raw[n];
      if (!r) return { ok: false, error: `${n} sayfada bulunamadı` };
      const alis = parseSiteNumber(r.alis);
      const satis = parseSiteNumber(r.satis);
      if (!Number.isFinite(alis) || !Number.isFinite(satis)) {
        return { ok: false, error: `${n} değeri okunamadı ("${r.alis}" / "${r.satis}")` };
      }
      const [min, max] = ranges[n];
      if (alis < min || alis > max || satis < min || satis > max) {
        return { ok: false, error: `${n} değeri olağan dışı (${alis} / ${satis})` };
      }
      if (satis < alis) return { ok: false, error: `${n} satış alıştan küçük görünüyor` };
      rates[n] = { alis, satis };
    }
    // Çapraz kontrol: EUR/TL ÷ USD/TL ≈ EUR/USD olmalı (sayfa yarım yüklenmişse yakalar)
    const cross = rates['EUR/TL'].alis / rates['USD/TL'].alis;
    const diff = Math.abs(cross - rates['EUR/USD'].alis) / rates['EUR/USD'].alis;
    if (diff > 0.03) return { ok: false, error: 'Kurlar birbiriyle tutarsız, tekrar okunuyor' };
    return { ok: true, rates };
  }

  return { parseSiteNumber, parseAmount, round, convert, validateRates };
});
