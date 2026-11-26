const assert = require('assert');
const C = require('../src/calc');

// Sitedeki sayılar
assert.strictEqual(C.parseSiteNumber('50,7349'), 50.7349);
assert.strictEqual(C.parseSiteNumber('6.022,76'), 6022.76);
assert.strictEqual(C.parseSiteNumber('118.019,00'), 118019);
assert.ok(Number.isNaN(C.parseSiteNumber('')));
assert.ok(Number.isNaN(C.parseSiteNumber('-')));
assert.ok(Number.isNaN(C.parseSiteNumber('50,73,49')));

// Kullanıcı girişi
assert.strictEqual(C.parseAmount('240'), 240);
assert.strictEqual(C.parseAmount('240,5'), 240.5);
assert.strictEqual(C.parseAmount('240.50'), 240.5);
assert.strictEqual(C.parseAmount('1.240'), 1240);
assert.strictEqual(C.parseAmount('1.240,50'), 1240.5);
assert.strictEqual(C.parseAmount('1,240.50'), 1240.5);
assert.strictEqual(C.parseAmount('$ 240'), 240);
assert.ok(Number.isNaN(C.parseAmount('abc')));

// Ekran görüntüsündeki örnek değerler
const raw = {
  'USD/TL': { alis: '50,7349', satis: '50,7986' },
  'EUR/TL': { alis: '55,4238', satis: '55,6328' },
  'EUR/USD': { alis: '1,0941', satis: '1,0977' },
};
const v = C.validateRates(raw);
assert.ok(v.ok, v.error);
const s = { tlSide: 'alis', tlAdjust: 0, pariteSide: 'alis', pariteAdjust: -0.01 };
const c = C.convert(240, v.rates, s);
assert.strictEqual(c.parite, 1.0841);           // 1,0941 yerine 1,0841
assert.strictEqual(c.usdTl, 50.7349);
assert.strictEqual(c.tl, 12176.38);             // 240 × 50,7349 = 12.176,376
assert.strictEqual(c.eur, 221.38);              // 240 ÷ 1,0841 = 221,3818
assert.strictEqual(C.convert(240, v.rates, { ...s, tlSide: 'satis' }).tl, 12191.66);

// Hatalı / eksik veri reddedilmeli
assert.ok(!C.validateRates({ ...raw, 'EUR/USD': undefined }).ok);
assert.ok(!C.validateRates({ ...raw, 'USD/TL': { alis: '', satis: '' } }).ok);
assert.ok(!C.validateRates({ ...raw, 'EUR/USD': { alis: '1,5941', satis: '1,5977' } }).ok); // tutarsız
assert.ok(!C.validateRates({ ...raw, 'USD/TL': { alis: '50,80', satis: '50,70' } }).ok);
assert.ok(!C.validateRates(null).ok);

console.log('Tüm testler geçti.');
