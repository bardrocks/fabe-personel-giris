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

## Personel listesi, kategoriler ve toplu atama

- **Kategoriye atama (3 yol):** (1) Personel sayfasında kişileri onay kutusuyla seçin, çıkan çubuktan kategoriyi seçip **Uygula**'ya basın (Kategorisiz yap da var). (2) Ayarlar > Kategoriler'de kategori satırındaki
  **Personel (N)** düğmesiyle kişi ekleyip çıkarın. (3) Kişi sayfası > **Düzenle**'de Kategori seçin.
- **Ayarlar > Personel listesi:** tüm personel (EnGoPer'den gelen ve elle eklenen), arama, "Silinenleri göster", "Son 60 günde okutanlar". Satır işlemleri: **Düzenle** (görünen ad, kategori; cihaz no salt okunur),
  **Sil / Geri al**. **+ Personel ekle**: cihaz no ve ad soyad zorunlu; numara başka kişide ya da silinmiş birindeyse hata verir. **Cihazda olup adı bilinmeyen numaralar** kartında **Tanımla** ile ad verilir;
  numaranın geçmiş okutmaları o isimle görünür (okutmalar zaten cihaz numarasıyla bağlıdır).
- Adlar EnGoPer'in Personel.DBF'inden **salt okunur** alınır; görünen ad, silme ve elle ekleme panelin kendi veritabanında tutulur (DBF'e, EnGoPer'e ya da cihaza yazılmaz). DBF yenilemesi bunları ezmez;
  elle eklenen kişi DBF'ye sonradan eklenirse DBF adı kaydedilir ama görünen ad geçerli kalır. Her değişiklik `personel_log` tablosuna (zaman, işlem, kişi, eski/yeni ad, istemci IP) yazılır.
- **Silinen kişi**: Personel sayfası, Puantaj, Gelmeyenler, izin bakiyesi ve kişi seçim listelerinden çıkar; geçmiş raporlar, ham kayıtlar ve kişi sayfası çalışır, ad "(silindi)" ekiyle görünür.
  Cihazdan yeni okutması gelirse kayıt alınır ve Bugün listesinde "silinmiş personel" uyarısıyla görünür. Geri alınabilir.

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
nöbet kapalıysa cumartesi beklentisi nöbetten gelmez). Veri silinmez, tekrar açınca geri gelir. Puantaj, izin bakiyesi, panel şifresi (giriş) ve Telegram bildirimi de bu sekmeden açılıp kapatılır (giriş ve bildirim varsayılan olarak kapalıdır).

## İşe giriş / işten çıkış

Personel sayfasında **Düzenle** ile girilir. İşten çıkış tarihinden sonraki günlerde kişi beklenmez, Gelmeyenler listesine ve (çıkış tarihi dönem başından önceyse) Personel sayfasına girmez.
Ayarlar > Personel saatleri'nde ayrılanlar varsayılan olarak gizlenir.

## Puantaj ve fazla mesai

**Puantaj** menüsü (ay seçimi): satırlar personel, sütunlar ayın günleri, en solda (sabit) ad. Hücre kodları:
**X** geldi · **İ** izinli (tam gün; Raporlu ve Ücretsiz hariç türler) · **R** raporlu · **Ü** ücretsiz izin · **HT** hafta tatili (kişinin beklenmediği gün: Pazar, çalışmadığı gün, nöbetsiz cumartesi) ·
**RT** resmi tatil · **TÇ** tatilde çalıştı (tam gün resmi tatilde okutma var) · **G** gelmedi (beklenen gün, okutma yok) · boş: gelecek gün, sistemde veri yok ya da kişi o gün işte değil.
Saatlik izinli gelinen gün X'tir, küçük nokta ile işaretlenir; okutması düzeltilmiş günde ✎ görünür. Hücre üzerine gelince giriş-çıkış saatleri, izin ve tatil adı çıkar.
Satır sonunda: çalışılan gün, toplam çalışma, **fazla mesai**, **eksik süre** (geç + erken çıkış), geç (adet), izin / rapor / ücretsiz / gelmedi gün ve tatil çalışması gün.
Listelenenler: o ay okutması olan ya da son 60 günde okutmuş kişiler (ay başından önce işten ayrılanlar hariç). **Excel** düğmesi aynı ızgarayı toplamlar ve kod açıklamalarıyla indirir
(`/disa?tur=puantaj&ay=2026-11&bicim=xlsx`). Kişi sayfasında da dönem için fazla mesai ve eksik süre kartları vardır.

**Ayarlar > Genel mesai > Fazla mesai ve mola** (`ayar` tablosunda `fm_yontem`, `fm_haftalik_esik`, `mola_dk`):

- **Haftalık** (varsayılan, İş Kanunu): haftalar Pazartesi başlar; haftalık toplam çalışma eşiği (`fm_haftalik_esik`, varsayılan 45 saat) aşarsa aşan kısım fazla mesaidir.
  **Ay sınırındaki haftalar haftanın tamamı üzerinden hesaplanır ve fazla mesai haftanın son gününün (Pazar) ayına yazılır.**
- **Günlük**: her gün kişinin mesai bitişinden sonra çalıştığı süre fazla mesaidir (erken gelme sayılmaz).
- **Mola** (`mola_dk`, varsayılan 0): çıkışı olan günlerin çalışma süresinden düşülür (negatif olmaz); haftalık yöntemde fazla mesaiyi azaltır, günlük yöntemde fazla mesaiyi etkilemez.
- Çıkış okutulmamış gün çalışma süresine 0 dakika katılır (önce Düzelt ile çıkış ekleyin). Tatilde çalışılan saatler de toplam çalışmaya ve haftalık toplama dahildir.

## Yıllık izin bakiyesi

**İzinler > Bakiye** (kişi sayfasında "Yıllık izin" kartı): işe giriş tarihinden itibaren her **tamamlanan** hizmet yılı için hak eklenir: kıdem 1-4. yıl **14** gün, 5-14. yıl **20** gün, 15+ yıl **26** gün.
Kullanılan: türü "Yıllık izin" olan, tarihi geçmiş tam gün izinler; saatlik yıllık izin, o günkü mesai süresine oranla kesir (1 ondalık). **Kalan = devir + hak − kullanılan.**
Sonraki hak tarihi ve miktarı gösterilir; işe giriş tarihi yoksa "işe giriş tarihi girin" uyarısı çıkar (kişi sayfası > Düzenle).
**Devir** sisteme geçmeden önceki kalan günlerdir; tabloda hücreden elle girilir, negatif olabilir. Yaş kuralı (18 yaş altı / 50 yaş üstü için en az 20 gün) bilinmediği için uygulanmaz; gerekirse devir alanından düzeltin.

## Panel şifresi (giriş)

**Ayarlar > Özellikler > Panel şifresi**. Açmadan önce en az bir **yönetici** oluşturulur (ekranda "ilk yönetici" formu çıkar; şifre en az 6 karakter). Giriş açıkken:

- `/` dahil her şey oturum ister; oturum yoksa giriş sayfası gelir, `/api/*`, `/yedek/` ve `/disa` oturumsuz 401 döner. Oturum çerezi HttpOnly + SameSite=Lax, 12 saat sürer; yanlış girişte 1 sn beklenir.
- **Yönetici** her şeyi yapar. **İzleyici** sadece görüntüler: bütün POST istekleri (okutma düzeltme, ayar, izin, nöbet yükleme, "Şimdi oku") 403 döner.
- Eski `yonetim_sifresi` / X-Sifre mekanizması giriş açıkken devre dışıdır (yönetici oturumu yeterli); giriş kapalıyken eskisi gibi çalışır.
- Ayarlar > **Kullanıcılar** (sadece yönetici): ekle, sil, şifre değiştir, rol değiştir. Son yönetici silinemez ya da izleyici yapılamaz. Şifreler PBKDF2-SHA256 (200000 tur, kullanıcıya özel tuz) ile saklanır.
- Cihaz toplayıcı ve zamanlayıcı giriş açıkken de aynen çalışır.
- **Kilitlenirseniz** (şifreyi unuttuysanız) KASA 1'de:

```powershell
py C:\EnGoPerPanel\panel.py --ayar C:\EnGoPerPanel\config.json --giris-kapat
```

Bu komut girişi kapatır ve açık oturumları siler (servisi yeniden başlatmak gerekmez); sonra Özellikler'den yeniden açabilirsiniz.

## Telegram bildirimleri

**Ayarlar > Bildirimler** (özellik: *Telegram bildirimi*, Özellikler'den açılır):

1. Telegram'da **@BotFather**'a `/newbot` yazıp botu oluşturun; verdiği **token**'ı panele yapıştırın.
2. Bildirim alacak kişi (ya da grup) bota bir mesaj yazsın (grupsa botu gruba ekleyip bir mesaj yazın). Tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` adresini açın;
   çıkan `"chat":{"id":...}` değeri **chat id**'dir (gruplarda eksi ile başlar). Birden çok chat id virgülle ayrılır.
3. **Test mesajı gönder** ile deneyin. Seçenekler: **sabah özeti** (varsayılan 09:45: geç kalanlar, gelmeyenler, bugün izinliler), **akşam özeti** (20:30: günün özeti, çıkış okutmayanlar, nöbet durumu),
   **cihaz uyarısı** (son başarılı okuma 60 dakikadan eskiyse; en fazla 3 saatte bir tekrarlanır, düzelince "cihaz tekrar okunuyor" gelir).

Özetler aynı gün bir kez, planlı saatten en geç 3 saat sonrasına kadar gönderilir; Pazar ve tam gün resmi tatilde gönderilmez. Kontrol her toplama turunda (5 dk) yapılır.
**Mesajlarda personel isimleri Telegram'a gönderilir.** Token panelde ve API'de yalnızca son 4 hanesiyle görünür, log'a yazılmaz (ama `panel.db` ve yedeklerinde durur; yedekleri paylaşmayın).
Gönderim hataları log'a ve Sistem sayfasındaki "Telegram bildirimi" satırına yazılır; servis hatadan etkilenmez. (Test için gizli `telegram_api` ayarı `config.json`'dan başka bir adrese yönlendirilebilir.)

## Önceki dönemle karşılaştırma

Personel sayfasındaki özet kartlarında önceki eşdeğer döneme göre değişim görünür (artış kötüyse kırmızı, azalış yeşil): *Bu hafta* → geçen hafta (aynı gün sayısı), *Bu ay* → geçen ayın aynı gün aralığı,
*Geçen ay* → ondan önceki ay, *Özel* → aynı uzunlukta önceki aralık.

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
