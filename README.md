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

- **Şimdi (Bugün):** içerideki personel, bugünün giriş/çıkışları, geç kalanlar; cumartesi ise nöbetçilerin durumu. Mağaza kapalı günse başlıkta yazar;
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

## Bugün sayfası, kart no ve hızlı arama

- **Gün seçici (1.11.1):** Bugün başlığında [‹] [Bugün] [Dün] [tarih] [›]; `#bugun/YYYY-AA-GG` adresi geçmiş günü açar (gelmeyenler, izinliler, gelen/beklenen kategori kartı; Dikkat/Yaklaşanlar/Canlı akış yok). Kişi sayfası Günlük kayıtlar ve Raporlar ("Gelmeyen günleri de göster") gelinmeyen günleri GELMEDİ / NÖBETE GELMEDİ / İZİNLİ / KAPALI satırı olarak gösterir.
- **Bugün (1.11.0 sırası):** KPI kartları (tıklayınca liste süzülür ve sayfa listeye kayar; başlıkta "Süzgeci kaldır"), üst ızgara (solda geniş **Dikkat edilecekler**, sağda kategori ve yaklaşanlar), bugünün listesi, canlı akış | ay özeti.
  **Dikkat edilecekler** en üstte **Uzun süredir okutma yok · işten mi ayrıldı?** (aktif kişi N gündür okutmuyor: İşten ayrıldı / İzin gir / Personeli sil / Şimdilik gizle; N: Ayarlar > Genel mesai), ardından dün çıkış okutmayanlar ve
  **dün giriş okutmayanlar** [tek okutma mesai ortasından sonraysa "GİRİŞ YOK": o okutma çıkış sayılır; Düzelt'te mesai giriş saati önden yazılır]. Diğer gruplar:
  dün çıkış okutmayanlar [Düzelt: saat alanına kişinin mesai çıkışı önden yazılır],
  mesai saati geçtiği halde gelmeyenler [İzin gir], son 14 günde okutması olan adı bilinmeyen kart numaraları [Tanımla], bu hafta başlayan/biten izinler; boş gruplar gizlenir, her grupta ilk 8 satır görünür),
  **Şu an içeride · kategoriye göre** (içeride / bugün beklenen, ilerleme çubuğu; satıra tıklayınca liste o kategoriye süzülür), **Yaklaşanlar** (bu/gelecek cumartesi nöbet listesi, önümüzdeki 7 günün izinleri),
  **Canlı okutma akışı** (son 10 okutma, ayıklanmış sıradan giriş/çıkış etiketi) ve **Bu ayın özeti** (en çok geç kalan / çıkış okutmayan 5 kişi). Kartlar özellik anahtarlarına uyar (izin kapalıysa izin kartları yok, vb.).
  Sayfa 60 sn'de bir yenilenir.
- **Kart no:** Personel giriş-çıkışı kişiye kayıtlı kartla yapılır; cihazın gönderdiği numara Personel.DBF'deki KARTNO'ya (int) eşittir. Arayüzde her yerde "Kart no" yazar ve DBF'deki metin gösterilir (örn. 0081);
  elle eklenen kişide 4 haneye sıfırla doldurulur. Excel başlıkları da "Kart No"dur.
- **Hızlı arama:** sol menüde logonun altındaki kutu (mobilde büyüteç ikonu): `81`, `0081` ya da isim parçası yazın; en fazla 8 sonuç (tam kart no eşleşmesi önce, silinenler "(silindi)"); Enter ya da tıklama kişi sayfasını açar.
  Kayıtlı olmayan kart numarası için "Bu kart kimseye tanımlı değil: Tanımla" seçeneği çıkar. Personel sayfası ve Personel listesi aramaları da baştaki sıfırları yok sayar (81 → 0081).

## Otomatik güncelleme

Panel kendini GitHub'daki depodan güncelleyebilir (`guncelleme_taban` + `guncelleme_api` + `guncelleme_dali` (dosyalar dalın commit SHA adresinden iner, tutarlı set); varsayılan dal şimdilik `claude/stoic-cori-vz2i9g`, ileride `main`; üçü de `config.json`'dan değiştirilebilir).
**Güncellenen dosyalar:** `panel.py`, `index.html`, `collector.ps1`, `baslat.pyw`, `panel.ico`, `DEGISIKLIKLER.md`, `README.md`. **Asla dokunulmayanlar:** `config.json`, `data\` (veritabanı), `port.txt`, kısayollar, zamanlanmış görev, güvenlik duvarı.

- **Masaüstü simgesinden:** Panel açıldıktan sonra başlatıcı uzak sürümü kontrol eder (internet yoksa sessizce atlar). Yeni sürüm varsa ve "otomatik güncelle" açıksa küçük bir "Yeni sürüm yükleniyor…" penceresi çıkar,
  güncelleme uygulanır, panel yeniden başlar ve tarayıcı açılır. Panel 45 sn içinde yeni sürümle dönmezse başlatıcı eski kod yedeğinden **otomatik geri alır** ve hata kutusu gösterir.
- **Sistem sayfası > Güncelleme:** mevcut ve uzak sürüm, yenilik notları, **Kontrol et**, **Şimdi güncelle** ve "Masaüstü simgesinden açılırken otomatik güncelle" anahtarı (`ayar` tablosunda `otomatik_guncelleme`, config varsayılanını ezer).
- **Uygulama adımları** (`POST /api/guncelleme/uygula`): sürüm kontrolü → veritabanı yedeği (`guncelleme-<eski>-<yeni>`) → tüm dosyalar `data\guncelleme_tmp\`'ye iner (boş olmamalı, `panel.py` derlenmeli ve sürümü uzak sürüme eşit olmalı,
  `index.html` geçerli olmalı; biri başarısızsa hiçbir dosyaya dokunulmaz) → eski dosyalar `data\yedekler\kod_<eski>_<zaman>\` altına kopyalanır → dosyalar yerine konur (hata olursa yedekten geri yüklenir) →
  `data\guncelleme.json` yazılır → panel kendini aynı komutla yeniden başlatır (cihaz okuması sürüyorsa en çok 3 dk beklenir). Yeni sürüm açılınca arayüz bir kerelik "vX sürümüne güncellendi · Yenilikler" gösterir.
- **Yetki:** `127.0.0.1`'den gelen güncelleme isteği (masaüstü başlatıcı) oturum istemez; LAN'dan gelen istek giriş açıkken yönetici oturumu, kapalıyken `yonetim_sifresi` ister (izleyici 403, oturumsuz 401).
- Elle güncelleme gerekirse: yeni dosyaları kopyalayıp `install.ps1`'i tekrar çalıştırın. Yazma izni hatası alırsanız `install.ps1`'i yönetici olarak çalıştırın (`C:\EnGoPerPanel` için Users grubuna Modify verir).

## Personel listesi, kategoriler ve toplu atama

- **Kategoriye atama (3 yol):** (1) Personel sayfasında kişileri onay kutusuyla seçin, çıkan çubuktan kategoriyi seçip **Uygula**'ya basın (Kategorisiz yap da var). (2) Ayarlar > Kategoriler'de kategori satırındaki
  **Personel (N)** düğmesiyle kişi ekleyip çıkarın. (3) Kişi sayfası > **Düzenle**'de Kategori seçin.
- **Ayarlar > Personel listesi:** tüm personel (EnGoPer'den gelen ve elle eklenen), arama, "Silinenleri göster", "Sadece aktif personel" (aktif: silinmemiş, ayrılmamış ve kategorisi olan ya da son 60 günde okutan). Satır işlemleri: **Düzenle** (görünen ad, kategori; cihaz no salt okunur),
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
Bir gün şu durumlarda kişi için **beklenmez** (gelmedi sayılmaz): Pazar, mağaza kapalı gün, tam gün izin, işten çıkış tarihinden sonra, işe giriş tarihinden önce,
kişinin çalışma günü olmayan gün. Cumartesi işaretli değilse kişi **sadece nöbet listesindeki cumartesilerde** beklenir (nöbet takibi açıkken).
Toplu izin girişinde de kişinin çalışmadığı günler (ve mağaza kapalı günler) atlanır.

## Okutma düzeltme

Okutmayı unutan ya da yanlış okutan personel için (Ayarlar > Özellikler > *Okutma düzeltme* açıkken):

- **Personel sayfası > Günlük kayıtlar > Düzelt** ya da takvimde güne tıklayıp **Okutmaları düzelt**: o günün okutmaları (cihaz/elle, gizliler üstü çizili) listelenir;
  yanlış kayıt **Gizle**, unutulan okutma **Saat ekle** ile eklenir, **Geri al** işlemi geri çevirir. Açıklama zorunludur.
- Okutma silinmez: yanlışlar `gizli=1` olur (cihaz tekrar okusa da gizli kalır), eklenenler `cihaz=ELLE`, `kaynak=elle` olarak yazılır.
  Gizli okutmalar bütün rapor ve hesaplardan düşer. Her işlem `duzeltme_log` tablosuna zaman, kişi, saat, açıklama ve istemci IP'siyle kaydedilir.
- Düzeltilen günler rapor ve tablolarda ✎ ile işaretlenir; Excel'de "Düzeltme" sütunu vardır. `yonetim_sifresi` ayarlıysa düzeltme de şifre ister.
- Düzeltme özelliği kapatılırsa düğmeler kalkar, ama yapılmış düzeltmeler hesaplarda geçerli kalır.

## Mağaza kapalı günler

Resmi tatil özelliği kaldırıldı (mağaza resmi tatillerde de çalışabiliyor). Yerine **Ayarlar > Kapalı günler**: başlangıç, bitiş ve açıklama (örn. "Yılbaşı tatili") girilir; aralıktaki her gün kaydedilir,
aynı seferde girilenler tek satırda listelenir ve **Sil** ile topluca silinir; yıl filtresi vardır. **Hiç gün girilmezse hiçbir etkisi yoktur.** Kapalı günde:
kimse beklenmez (gelmedi / nöbete gelmedi sayılmaz, Gelmeyenler listesine girmez); o gün okutan olursa normal çalışmış sayılır ama geç / erken / çıkış yok sayılmaz;
puantajda okutmasız kapalı gün **K**, okutma varsa X; kişi takviminde ayrı stil ("Mağaza kapalı"); Bugün başlığında "Mağaza kapalı: açıklama"; nöbet satırları "KAPALI" görünür; toplu izin aralıklarında kapalı günler atlanır;
Telegram özetleri gönderilmez. Eski `tatil` tablosu ve `ozellik_resmi_tatil` ayarı veritabanında durur ama hiçbir yerde okunmaz.

## Özellik anahtarları

**Ayarlar > Özellikler**: nöbet takibi, izin takibi, okutma düzeltme, puantaj, izin bakiyesi, giriş ve Telegram tek tek kapatılabilir/açılabilir (`ayar` tablosunda `ozellik_<ad>` = 1/0).
Kapalı özellik menü, kart ve düğmelerden kalkar **ve hesaplara katılmaz** (izin kapalıysa izinler mesai/gelmedi hesabını etkilemez,
nöbet kapalıysa cumartesi beklentisi nöbetten gelmez). Veri silinmez, tekrar açınca geri gelir. Puantaj, izin bakiyesi, panel şifresi (giriş) ve Telegram bildirimi de bu sekmeden açılıp kapatılır (giriş ve bildirim varsayılan olarak kapalıdır).

## İşe giriş / işten çıkış

Personel sayfasında **Düzenle** ile girilir. İşten çıkış tarihinden sonraki günlerde kişi beklenmez, Gelmeyenler listesine ve (çıkış tarihi dönem başından önceyse) Personel sayfasına girmez.
Ayarlar > Personel saatleri'nde ayrılanlar varsayılan olarak gizlenir.

## Puantaj ve fazla mesai

**Puantaj** menüsü (ay seçimi): satırlar personel, sütunlar ayın günleri, en solda (sabit) ad. Hücre kodları:
**X** geldi · **İ** izinli (tam gün; Raporlu ve Ücretsiz hariç türler) · **R** raporlu · **Ü** ücretsiz izin · **HT** hafta tatili (kişinin beklenmediği gün: Pazar, çalışmadığı gün, nöbetsiz cumartesi) ·
**K** mağaza kapalı (kapalı günde okutma yoksa; okutma varsa X) · **G** gelmedi (beklenen gün, okutma yok) · boş: gelecek gün, sistemde veri yok ya da kişi o gün işte değil.
Saatlik izinli gelinen gün X'tir, küçük nokta ile işaretlenir; okutması düzeltilmiş günde ✎ görünür. Hücre üzerine gelince giriş-çıkış saatleri, izin ve kapalı gün açıklaması çıkar.
Satır sonunda: çalışılan gün, toplam çalışma, **fazla mesai**, **eksik süre** (geç + erken çıkış), geç (adet), izin / rapor / ücretsiz / gelmedi gün. Toplamlar ızgaranın altında ayrı tablodadır.
Listelenenler: o ay okutması olan, aktif personel (kategorisi olan ya da son 60 günde okutan) kişiler (ay başından önce işten ayrılanlar hariç). Giriş/çıkış okutulmamış günde hücrede küçük g / ç işareti vardır. **Excel** düğmesi aynı ızgarayı toplamlar ve kod açıklamalarıyla indirir
(`/disa?tur=puantaj&ay=2026-11&bicim=xlsx`). Kişi sayfasında da dönem için fazla mesai ve eksik süre kartları vardır.

**Ayarlar > Genel mesai > Fazla mesai ve mola** (`ayar` tablosunda `fm_yontem`, `fm_haftalik_esik`, `mola_dk`):

- **Haftalık** (varsayılan, İş Kanunu): haftalar Pazartesi başlar; haftalık toplam çalışma eşiği (`fm_haftalik_esik`, varsayılan 45 saat) aşarsa aşan kısım fazla mesaidir.
  **Ay sınırındaki haftalar haftanın tamamı üzerinden hesaplanır ve fazla mesai haftanın son gününün (Pazar) ayına yazılır.**
- **Günlük**: her gün kişinin mesai bitişinden sonra çalıştığı süre fazla mesaidir (erken gelme sayılmaz).
- **Mola** (`mola_dk`, varsayılan 0): çıkışı olan günlerin çalışma süresinden düşülür (negatif olmaz); haftalık yöntemde fazla mesaiyi azaltır, günlük yöntemde fazla mesaiyi etkilemez.
- Çıkış okutulmamış gün çalışma süresine 0 dakika katılır (önce Düzelt ile çıkış ekleyin). Kapalı günde çalışılan saatler de toplam çalışmaya ve haftalık toplama dahildir.

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

Özetler aynı gün bir kez, planlı saatten en geç 3 saat sonrasına kadar gönderilir; Pazar ve mağaza kapalı günlerde gönderilmez. Kontrol her toplama turunda (5 dk) yapılır.
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
