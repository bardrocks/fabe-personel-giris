# EnGoPer Panel – Personel Giriş-Çıkış

Parmak izi cihazındaki (TH600) okutmaları **EnGoPer'e dokunmadan** 5 dakikada bir okur, yerel SQLite'a
kaydeder ve LAN'da bir web panelde gösterir. Bulut yok, ek paket yok (sadece Python stdlib).

| Dosya | Görev |
|---|---|
| `collector.ps1` | 32-bit PowerShell + FP_CLOCK OCX ile cihazı **salt-okunur** okur, JSON yazar |
| `panel.py` | Zamanlayıcı + SQLite + web sunucu (`:8080`) + nöbet çizelgesi okuma |
| `index.html` | Panel arayüzü (mobil uyumlu) |
| `install.ps1` | `C:\EnGoPerPanel` kurulumu, güvenlik duvarı, açılışta otomatik başlatma |

## Başka bir PC'de deneme (demo modu)

Cihaz, EnGoPer ve Personel.DBF gerekmez; sahte personel ve son 45 günün sahte okutmaları üretilir.
Hiçbir şey kurulmaz, veriler sadece `demo_data\` klasörüne yazılır. Python 3.8+ yeterli.

```powershell
py panel.py --demo
```

Tarayıcıda `http://localhost:8080` açın. Durdurmak için konsolda `Ctrl+C`. Sıfırlamak için `demo_data`
klasörünü silin. (Demo personeli sahte olduğu için gerçek nöbet çizelgesindeki isimler önizlemede
"eşleşmedi" görünür; listeden demo personel seçerek kaydetmeyi deneyebilirsiniz.)

## Kurulum (KASA 1, yönetici PowerShell)

Repo klasörüne geçin (indirdiğiniz yer):

```powershell
cd "$env:USERPROFILE\Downloads\fabe-personel-giris"
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Kurulum bir kez cihazdan test okuması yapar, ardından panel `http://192.168.1.140:8080` adresinde açılır.
Açılışta SYSTEM olarak başlar. Log'da cihaz hatası (OCX/E_UNEXPECTED) görülürse oturum moduna geçin:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Tetik Oturum
```

Ayarlar: `C:\EnGoPerPanel\config.json` (mesai saati, tolerans, yönetim şifresi…). Değişiklikten sonra:

```powershell
Stop-ScheduledTask "EnGoPer Panel"; Start-ScheduledTask "EnGoPer Panel"
```

Geçmiş kayıtları bir kerelik içe aktarmak (EnGoPer `backup.txt`, salt okuma):

```powershell
py C:\EnGoPerPanel\panel.py --ayar C:\EnGoPerPanel\config.json --ice-aktar-backup
```

Elle tek okuma testi:

```powershell
py C:\EnGoPerPanel\panel.py --ayar C:\EnGoPerPanel\config.json --bir-kez
```

Log: `C:\EnGoPerPanel\data\panel.log` · Veritabanı: `C:\EnGoPerPanel\data\panel.db`

## Panel

- **Şimdi:** içerideki personel, bugünün giriş/çıkışları, geç kalanlar; cumartesi ise nöbetçilerin durumu.
- **Rapor:** tarih aralığı + kişi + durum filtresi (geç / çıkış yok), Excel ve CSV indirme.
- **Nöbet:** aylık nöbet çizelgesi yükleme ve her cumartesi için *GELDİ / GELMEDİ / BEKLENİYOR / PLANLI*.
- **Ham kayıt:** cihazdan gelen her okutma.
- **Ayarlar:** genel mesai, kategoriler (örn. Depo 08:00–20:00, Manken 09:00–20:00) ve personel bazında
  özel saat. Öncelik: **özel saat → kategori → genel mesai**; her kademede cumartesi saati ayrı girilebilir.
  Geç kalma ve erken çıkış bu saatlere göre hesaplanır. (`config.json`'daki mesai değerleri sadece ilk
  açılıştaki varsayılandır.)
- **Sistem:** son okuma turları, hatalar, elle okuma.

Cihaz giriş/çıkış ayırmadığı için günün ilk okutması giriş, son okutması çıkış sayılır;
3 dk içindeki tekrar okutmalar tek sayılır.

## Nöbet çizelgesi

Nöbet sekmesinden `.xlsx` ya da `.csv` yükleyin. Dosyadaki tarihler (`07.11.2026`, `7 Kasım`, Excel tarih
hücresi) ve isimler otomatik bulunur. İki düzen de tanınır:

- tarihler bir satırda, isimler altlarında (sütun düzeni), ya da
- tarihler bir sütunda, isimler yanlarında (satır düzeni).

İsimler Personel.DBF ile eşleştirilir (Türkçe karakter / büyük-küçük harf fark etmez; `Ali Y.`, kart no
`0111` de olur). Kaydetmeden önce önizlemede eşleşmeyen ya da belirsiz isimler listeden seçilir.
Aynı tarihler tekrar yüklenirse o günlerin eski planı değiştirilir. Eski `.xls` biçimi okunmaz; Excel'de
*Farklı Kaydet → .xlsx* yapın. Biçim tanınmazsa paneldeki boş şablonu kullanın.

Durumlar: **GELDİ** (giriş saati, geç kaldıysa dakika) · **GELMEDİ** · **BEKLENİYOR** (bugün, mesai +
tolerans dolmadı) · **PLANLI** (ileri tarih) · **VERİ YOK** (o gün cihazdan hiç kayıt alınamamış).
`yonetim_sifresi` ayarlanırsa çizelge yükleme şifre ister.

## Güvenlik kuralları

`CLAUDE.md` içindeki kesin kurallar geçerlidir: `C:\EnGoPer` altına yazılmaz; cihazda yalnızca okuma
metotları kullanılır; `EnableDevice(false)` sadece `ReadAllGLogData` süresince; EnGoPer veya M_Serisi
açıksa tur atlanır. Personel.DBF'den sadece KARTNO, ADI, SOYADI, SICILNO alınır.
