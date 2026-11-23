# EnGoPer Panel – Personel Giriş-Çıkış

Parmak izi cihazındaki (TH600) okutmaları **EnGoPer'e dokunmadan** 5 dakikada bir okur, yerel SQLite'a
kaydeder ve LAN'da bir web panelde gösterir. Bulut yok, ek paket yok (sadece Python stdlib).

| Dosya | Görev |
|---|---|
| `collector.ps1` | 32-bit PowerShell + FP_CLOCK OCX ile cihazı **salt-okunur** okur, JSON yazar |
| `panel.py` | Zamanlayıcı + SQLite + web sunucu (`:8080`) + nöbet çizelgesi okuma |
| `index.html` | Panel arayüzü (mobil uyumlu) |
| `install.ps1` | `C:\EnGoPerPanel` kurulumu, güvenlik duvarı, açılışta otomatik başlatma |

## Kurulum (KASA 1, yönetici PowerShell)

Repo klasörüne geçin (indirdiğiniz yer):

```powershell
cd "$env:USERPROFILE\Downloads\fabe-personel-giris"
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Kurulum bir kez cihazdan test okuması yapar, ardından panel `http://192.168.1.140:8080` adresinde açılır.
Açılışta SYSTEM olarak başlar. Masaüstüne **Personel Paneli** kısayolu eklenir ve oturum açılınca panel
tarayıcıda kendiliğinden açılır (istemiyorsanız `-OturumdaAcma`). Log'da cihaz hatası (OCX/E_UNEXPECTED) görülürse oturum moduna geçin:

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

- **Şimdi:** içerideki personel, bugünün giriş/çıkışları, geç kalanlar; cumartesi ise nöbetçilerin durumu. Tatil günü başlıkta yazar;
  "ÇIKIŞ YOK" (ya da mesai bitmiş ve hâlâ içeride) satırlarında hızlı **Çıkış ekle** düğmesi vardır.
- **Rapor:** tarih aralığı + kişi + durum filtresi (geç / çıkış yok), Excel ve CSV indirme.
- **Nöbet:** aylık nöbet çizelgesi yükleme ve her cumartesi için *GELDİ / GELMEDİ / BEKLENİYOR / PLANLI*.
- **Ham kayıt:** cihazdan gelen her okutma.
- **Ayarlar:** genel mesai, kategoriler (örn. Depo 08:00–20:00, Manken 09:00–20:00) ve personel bazında
  özel saat. Öncelik: **özel saat → kategori → genel mesai**; her kademede cumartesi saati ayrı girilebilir.
  Geç kalma ve erken çıkış bu saatlere göre hesaplanır. (`config.json`'daki mesai değerleri sadece ilk
  açılıştaki varsayılandır.)
- **Sistem:** son okuma turları, hatalar, elle okuma. Cihazdan okuma toplama aralığının 3 katından uzun süredir yapılamıyorsa
  panelin üstünde kırmızı uyarı şeridi çıkar.

Cihaz giriş/çıkış ayırmadığı için günün ilk okutması giriş, son okutması çıkış sayılır;
3 dk içindeki tekrar okutmalar tek sayılır.

## Çalışma günleri

Mağaza Pazar kapalıdır; diğer günler kişiden kişiye değişebilir (kimi Cumartesi hiç çalışmaz, kimi ayda bir nöbetle, kimi her Cumartesi çalışır;
hafta içi izin günü olan da olabilir). **Ayarlar > Genel mesai** genel günleri, **Kategoriler** ve **Personel saatleri** tabloları kategori/kişi bazında günleri (Pt Sa Ça Pe Cu Ct) belirler.
Öncelik: **personel → kategori → genel**; "devral" işaretliyse üst kademe geçerlidir ve kaynağı yanında yazar.
Bir gün şu durumlarda kişi için **beklenmez** (gelmedi sayılmaz): Pazar, tam gün resmi tatil, tam gün izin, işten çıkış tarihinden sonra, işe giriş tarihinden önce,
kişinin çalışma günü olmayan gün. Cumartesi işaretli değilse kişi **sadece nöbet listesindeki cumartesilerde** beklenir (nöbet takibi açıkken).
Toplu izin girişinde de kişinin çalışmadığı günler (ve tatiller) atlanır.

## Okutma düzeltme

Okutmayı unutan ya da yanlış okutan personel için (Ayarlar > Özellikler > *Okutma düzeltme* açıkken):

- **Personel sayfası > Günlük kayıtlar > Düzelt** ya da takvimde güne tıklayıp **Okutmaları düzelt**: o günün okutmaları (cihaz/elle, gizliler üstü çizili) listelenir;
  yanlış kayıt **Gizle**, unutulan okutma **Saat ekle** ile eklenir, **Geri al** işlemi geri çevirir. Açıklama zorunludur.
- Okutma silinmez: yanlışlar `gizli=1` olur (cihaz tekrar okusa da gizli kalır), eklenenler `cihaz=ELLE`, `kaynak=elle` olarak yazılır.
  Gizli okutmalar bütün rapor ve hesaplardan düşer. Her işlem `duzeltme_log` tablosuna zaman, kişi, saat, açıklama ve istemci IP'siyle kaydedilir.
- Düzeltilen günler rapor ve tablolarda ✎ ile işaretlenir; Excel'de "Düzeltme" sütunu vardır. `yonetim_sifresi` ayarlıysa düzeltme de şifre ister.
- Düzeltme özelliği kapatılırsa düğmeler kalkar, ama yapılmış düzeltmeler hesaplarda geçerli kalır.

## Resmi tatiller

**Ayarlar > Resmi tatiller**: yıl seçimi, liste, ekleme (tarih, ad, yarım gün) ve silme. İlk kurulumda 2026-2027 sabit tatilleri ve 2026 dini bayramları yüklenir;
**2027 dini bayramlarını Diyanet takviminden kontrol edip kendiniz ekleyin** (sayfada uyarı çıkar). Tam gün tatilde kimse beklenmez; yarım günde (arife) öğleden sonra tatildir,
mesai çıkışı 13:00 kabul edilir. Tatilde çalışan kişi o günü normal çalışmış sayılır ve satırda "Tatil" rozeti görür (ileride puantajda "tatil çalışması" olacak).

## Özellik anahtarları

**Ayarlar > Özellikler**: nöbet takibi, izin takibi, okutma düzeltme ve resmi tatiller tek tek kapatılabilir (`ayar` tablosunda `ozellik_<ad>` = 1/0).
Kapalı özellik menü, kart ve düğmelerden kalkar **ve hesaplara katılmaz** (izin kapalıysa izinler mesai/gelmedi hesabını etkilemez, tatil kapalıysa tatiller yok sayılır,
nöbet kapalıysa cumartesi beklentisi nöbetten gelmez). Veri silinmez, tekrar açınca geri gelir. Puantaj, izin bakiyesi, panel şifresi ve Telegram bildirimi "yakında" olarak görünür, henüz açılamaz.

## İşe giriş / işten çıkış

Personel sayfasında **Düzenle** ile girilir. İşten çıkış tarihinden sonraki günlerde kişi beklenmez, Gelmeyenler listesine ve (çıkış tarihi dönem başından önceyse) Personel sayfasına girmez.
Ayarlar > Personel saatleri'nde ayrılanlar varsayılan olarak gizlenir.

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

## Sürüm ve yedekler

Güncel sürüm panelin sol alt köşesinde yazar; değişiklikler `DEGISIKLIKLER.md` dosyasında.
Veritabanı (`data\panel.db`) otomatik yedeklenir: her gün, yeni sürüm ilk açıldığında (güncellemeden önce) ve
`install.ps1` her çalıştığında. Yedekler `data\yedekler` klasöründe; Sistem sayfasından indirilebilir. İkinci bir
kopya için `config.json` içine `"ek_yedek_klasoru": "C:\\Users\\KASA 1\\OneDrive\\PanelYedek"` gibi bir klasör yazın.

**Güncelleme:** yeni ZIP'i açıp `install.ps1`'i tekrar çalıştırın. `C:\EnGoPerPanel\data` ve `config.json` korunur.

**Geri yükleme:** `Stop-ScheduledTask "EnGoPer Panel"` → istenen yedeği `data\panel.db` adıyla kopyalayın →
`Start-ScheduledTask "EnGoPer Panel"`.

## Güvenlik kuralları

`CLAUDE.md` içindeki kesin kurallar geçerlidir: `C:\EnGoPer` altına yazılmaz; cihazda yalnızca okuma
metotları kullanılır; `EnableDevice(false)` sadece `ReadAllGLogData` süresince; EnGoPer veya M_Serisi
açıksa tur atlanır. Personel.DBF'den sadece KARTNO, ADI, SOYADI, SICILNO alınır.
