# Fabe Döviz Hesap

Masaüstünde küçük bir pencere (pop-up) olarak çalışan döviz hesaplayıcı.
Kurları canlı olarak **https://www.nadirdoviz.com/fiyat-ekrani** sayfasından okur
ve yazılan **USD** tutarını **TL** ve **EUR** karşılığına çevirir.

## Nasıl çalışır?

- Uygulama Nadir Döviz fiyat ekranını arka planda görünmez bir pencerede açar ve
  ekranda gösterilen USD/TL, EUR/TL, EUR/USD **Alış/Satış** değerlerini 2 saniyede bir okur.
  Değerler sitede görünenle birebir aynıdır.
- **Hatasız** olması için her okuma kontrol edilir: sayı biçimi, makul aralık,
  satış ≥ alış ve EUR/TL ÷ USD/TL ≈ EUR/USD çapraz kontrolü. Tutmayan veri kullanılmaz.
- Kurlar 30 sn güncellenmezse sarı uyarı çıkar, 90 sn güncellenmezse hesap **gösterilmez**
  (eski kurla yanlış fiyat verilmesin diye). Bağlantı koparsa sayfa kendiliğinden yenilenir.

## Hesaplama (varsayılan ayarlar)

| Çeviri | Formül | Örnek (240 USD) |
|---|---|---|
| USD → TL | tutar × USD/TL **Alış** | 240 × 50,7349 = **12.176,38 ₺** |
| USD → EUR | tutar ÷ (EUR/USD **Alış** − 0,0100) | 240 ÷ 1,0841 = **221,38 €** |

⚙ **Ayarlar**'dan değiştirilebilir:
- TL için Alış mı Satış mı kullanılacağı ve kura düzeltme (ör. -0,0500)
- Parite için Alış mı Satış mı kullanılacağı ve pariteye düzeltme (varsayılan **-0,0100**)
- Her zaman üstte, bilgisayar açılınca başlat

## Kullanım

- **Ctrl+Shift+D**: her yerden pencereyi aç / gizle
- Tutarı yazın → TL ve EUR anında hesaplanır
- Sonuca tıklayın (veya **Enter** → TL) → panoya kopyalanır
- **Esc**: tutarı temizler
- ✕ pencereyi gizler, uygulama saatin yanındaki simgede çalışmaya devam eder
  (sağ tık → Çıkış)

## Kurulum (Windows)

GitHub'da **Actions → "Windows uygulamasını derle"** çalışmasından
`FabeDovizHesap-Windows` dosyasını indirin. İçinde iki seçenek var:
- `FabeDovizHesap-Kurulum-x.y.z.exe` – kurulum (masaüstü kısayolu oluşturur)
- `FabeDovizHesap-Portable-x.y.z.exe` – kurulumsuz, çift tıkla çalışır

Kendiniz derlemek için (Node.js 20+):

```bash
cd doviz-hesap
npm install
npm start          # geliştirme modunda çalıştır
npm test           # hesaplama testleri
npm run dist:win   # Windows .exe üret (dist/ klasörüne)
```
