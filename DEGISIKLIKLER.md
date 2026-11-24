# Değişiklikler

Sürüm numarası `panel.py` içindeki `SURUM` değeridir; panelde sol menünün altında ve Sistem sayfasında görünür.
Yeni sürüm ilk açıldığında veritabanı, güncellenmeden önce `data\yedekler` klasörüne otomatik yedeklenir.

## 1.10.0
- **Güncelleme sağlamlaştırıldı** (3 düzeltme):
  - Dosyalar artık dal adresinden değil, GitHub API'den (`guncelleme_api`, `{dal}` yer tutucusu) alınan **commit SHA** adresinden iner (`<taban>/<sha>/<dosya>`): raw CDN önbelleği yüzünden yeni panel.py + eski index.html gibi tutarsız set gelmez. SHA alınamazsa güncelleme yapılmaz (dal adresine düşülmez). Kurulu SHA `data/kurulu_sha.txt` ve `guncelleme.json` içinde saklanır, Sistem sayfasında gösterilir.
  - Güncelleme sırasında tutulan toplayıcı kilidi, yeniden başlatma başarısız olursa hemen, süreç hiç çıkmazsa 60 sn sonra bırakılır (cihaz okuması durmaz).
  - `install.ps1`, Görev Zamanlayıcı dışında kalmış (güncellemeyle yeniden başlatılmış) panel python süreçlerini komut satırından bulup sonlandırır (`-Kaldir` dahil).
- **Bugün sayfası yenilendi**: "Son 14 gün" ve "Bugün giriş saatleri" grafikleri kaldırıldı; yerine **Dikkat edilecekler** (dün çıkış okutmayanlar → Düzelt, gelmeyenler → İzin gir, adı bilinmeyen kartlar → Tanımla,
  bu hafta başlayan/biten izinler), **kategoriye göre içeride / beklenen** (tıklayınca liste süzülür), **Yaklaşanlar** (nöbet, izinler), **canlı okutma akışı** ve **bu ayın özeti** kartları geldi. Yeni uç noktalar: `GET /api/ana`, `GET /api/ara`.
- **Kart no**: arayüzde ve Excel başlıklarında "Cihaz no" yerine "Kart no" (DBF'deki KARTNO metni, örn. 0081). Sol menüde **kart no / isim hızlı arama** (mobilde büyüteç). Personel aramaları baştaki sıfırları yok sayar.
- **Resmi tatil özelliği kaldırıldı** (RT/TÇ kodları, 13:00 arife kuralı, tatil tohumu, `tatil`/`tatil_yarim` alanları, Ayarlar > Resmi tatiller). `tatil` tablosu silinmedi ama artık okunmaz.
  Yerine **Mağaza kapalı günler** (Ayarlar > Kapalı günler): kapalı günde kimse beklenmez, okutan olursa geç/erken sayılmaz, puantajda **K**, takvimde ayrı stil, Bugün başlığında açıklama, nöbette "KAPALI".
- **Otomatik güncelleme**: Sistem sayfasında Güncelleme kartı; masaüstü başlatıcı açılışta yeni sürümü indirip kurar ve panel yeni sürümle dönmezse eski sürüme geri alır. Kod ve DB yedeği alınır; `config.json`, `data\`, `port.txt`,
  kısayol ve görev değişmez. Yeni uç noktalar: `GET /api/guncelleme`, `POST /api/guncelleme/uygula` (127.0.0.1'den oturumsuz), `GET /api/surum`, `GET /api/yenilikler`, `POST /api/ayar/guncelleme_ayar`.
  Yeni ayarlar: `guncelleme_dali`, `guncelleme_taban`, `otomatik_guncelleme`. `install.ps1` `C:\EnGoPerPanel` için Users grubuna Modify izni verir.
- `kapali_gun` tablosu eklendi (1.9.1 veritabanından geçişte ayarlar, kategoriler, görünen adlar, izinler ve kullanıcılar aynen korunur).
- Tanımsız (personel listesinde olmayan) numaralar artık "Gelmeyenler"e ve ay özetine girmez.

## 1.9.1
- Personeli bulması kolaylaşsın diye: kişi sayfası başlığında "✎ Adı düzenle" ve "Personeli sil / Geri al" düğmeleri (silinmişse kırmızı "silinmiş" rozeti), Personel sayfasında Ayarlar > Personel listesi'ne giden ipucu satırı (`#ayarlar/personelliste` bağlantısı).

## 1.9.0
- **Toplu kategori atama**: Personel sayfasındaki tabloda (ve Ayarlar > Personel listesi'nde) onay kutuları ve "tümünü seç"; seçince yapışkan çubuk çıkar:
  "N kişi seçildi · Kategoriye ata: [liste / Kategorisiz yap] [Uygula] [Seçimi temizle]". Satıra tıklayınca kişi sayfası açılmaya devam eder, onay kutusu gezinmeyi tetiklemez.
- **Personel yönetimi** (Ayarlar > Personel listesi): görünen adı değiştir (EnGoPer adı korunur, altında "EnGoPer'de: …" yazar), kişiyi **sil / geri al**, EnGoPer'de olmayan kişiyi **ekle**,
  cihazda okutması olup adı bilinmeyen numaraları **tanımla**. Hiçbir şey Personel.DBF'e, EnGoPer'e ya da cihaza yazılmaz; okutmalar cihaz numarasıyla eşleşmeye devam eder.
  Görünen ad her yerde (rapor, puantaj, bugün, nöbet, izin, Excel, Telegram…) tek noktadan çözülür. Silinen kişi Personel/Puantaj/Gelmeyen/izin bakiyesi/seçim listelerinden çıkar,
  geçmiş raporlarda "(silindi)" ile görünür; cihazdan yeni okutması gelirse Bugün'de "silinmiş personel" uyarısı çıkar. DBF yenilemesi görünen adı ve silindi bayrağını ezmez, elle eklenen kişiyi silmez.
  Nöbet isim eşleştirmesi görünen adla da eşleşir. Değişiklikler `personel_log` tablosuna yazılır.
- Yeni API: `GET /api/personel_liste`, `GET /api/tanimsiz_numaralar`, `POST /api/ayar/personel_ekle|personel_duzenle|personel_sil|personel_geri_al`.
- 1.8.0'dan geçişte `personel_ayar.gorunen_ad`, `personel_ayar.silindi`, `personel.kaynak` sütunları ve `personel_log` tablosu otomatik eklenir (otomatik yedek alınır).

## 1.8.1
- **Tablolar pencereye sığar**: Personel sayfasında "En çok geç kalanlar / gelmeyenler" kartları tablonun üstüne (yan yana) alındı, tablo tam genişlik kullanır. Bugün tablosu da tam genişlik,
  grafikler altında. Tablolar sıkılaştırıldı (küçük dolgu, süreler gerekirse iki satıra sarılır). Ayarlar > Kategoriler ve Personel saatleri tabloları saat çiftleri ve çalışma günleri alt satırda olacak şekilde
  yeniden düzenlendi. Puantaj'da aylık toplamlar ızgaranın altında ayrı, kaydırmasız tabloda; ızgarada ad sütunu sabit. 1024 / 1280 / 1366 px'te tablo içi yatay kaydırma yoktur.
- **Kategoriye personel ekleme**: Ayarlar > Kategoriler'de her satırda "Personel (N)" düğmesi (arama, son 60 gün filtresi, onay kutulu liste, taşınacak kişi uyarısı); kişi sayfası > Düzenle'de Kategori seçimi;
  Personel saatleri'ndeki toplu atama çubuğu "Seçili kişileri kategoriye ata: [liste] [Uygula]" olarak belirginleştirildi.

## 1.8.0
- **Puantaj ve fazla mesai**: aylık ızgara (X, İ, R, Ü, HT, RT, TÇ, G), satır toplamları (çalışılan gün, çalışma süresi, fazla mesai, eksik süre, geç, izin/rapor/ücretsiz/gelmedi/tatil çalışması), Excel çıktısı.
  Fazla mesai yöntemi (haftalık eşik ya da günlük) ve günlük mola Genel mesai'den ayarlanır; ay sınırındaki haftalar haftanın tamamı üzerinden hesaplanır ve haftanın son gününün (Pazar) ayına yazılır.
  Kişi sayfasında dönem için fazla mesai ve eksik süre kartları.
- **Yıllık izin bakiyesi**: kıdeme göre hak (14/20/26 gün), kullanılan, elle girilen devir, kalan ve sonraki hak (İzinler > Bakiye, kişi sayfasında "Yıllık izin" kartı).
- **Panel şifresi**: kullanıcılar (yönetici / izleyici), oturum çerezi, giriş sayfası; izleyici salt okunur. `py panel.py --giris-kapat` kilitlenme önlemi. Giriş açıkken `yonetim_sifresi` devre dışıdır.
- **Telegram bildirimleri**: sabah/akşam özeti ve cihaz uyarısı, test mesajı, durum Sistem sayfasında; token maskelenir ve log'a yazılmaz.
- **Önceki dönemle karşılaştırma**: Personel sayfası özet kartlarında değişim yüzdesi.
- Tam gün resmi tatilde okutması olan kişide geç / erken / çıkış yok sayılmaz (tam gün izin gibi); satırda `tatil` alanı kalır, puantajda "tatil çalışması" olarak geçer.
- Ayarlar > Personel saatleri tablosu 1366 px genişlikte kaydırmasız sığar (çalışma günleri ad sütununun altında).
- Eski sürümden geçişte `izin_devir` sütunu ile `kullanici`, `oturum` tabloları otomatik eklenir (ilk açılışta otomatik yedek alınır).

## 1.7.0
- **Özellik anahtarları** (Ayarlar > Özellikler): nöbet, izin, okutma düzeltme ve resmi tatil tek tek kapatılabilir. Kapalı özellik menüden/kartlardan/düğmelerden
  kalkar ve hesaplara katılmaz (izin kapalıysa izinler gelmedi/mesai hesabını etkilemez, tatil kapalıysa tatiller yok sayılır, nöbet kapalıysa cumartesi nöbetten beklenmez).
  Puantaj, izin bakiyesi, panel şifresi ve Telegram bildirimi 1.8.0'da çalışır hale geldi.
- **Kişiye göre çalışma günleri**: genel, kategori ve personel bazında Pt–Ct günleri (öncelik personel > kategori > genel; boş = üst kademeden devral).
  "Gelmedi", Bugün'deki "Gelmeyenler", takvim ve toplu izin girişi artık kişinin kendi günlerine göre çalışır.
  Cumartesi çalışma günü değilse kişi sadece nöbet listesindeki cumartesilerde beklenir. Pazar her zaman kapalıdır.
- **Okutma düzeltme**: unutulan okutma elle eklenir (`cihaz=ELLE`), yanlış okutma silinmez gizlenir; açıklama zorunlu, her işlem `duzeltme_log` tablosuna (işlemi yapanın IP'siyle) yazılır
  ve Geri al ile eski haline döner. Kişi sayfasında "Düzelt" düğmesi, takvimde güne tıklayınca menü, Bugün'de "Çıkış ekle". Düzeltilen günler rapor ve tablolarda ✎ ile işaretlenir; Excel'e "Düzeltme" sütunu eklendi.
  Gizli okutmalar bütün rapor ve hesaplardan düşer; toplayıcı aynı kaydı tekrar okusa da gizli kalır.
- **Resmi tatiller** (Ayarlar > Resmi tatiller): 2026-2027 sabit tatilleri ve 2026 dini bayramları hazır gelir (2027 dini bayramlarını siz ekleyin). Tam gün tatilde kimse beklenmez,
  yarım günde (arife) mesai çıkışı 13:00 kabul edilir. Takvimde ayrı renk, Bugün başlığında tatil adı; tatilde çalışana "Tatil" rozeti.
- **İşe giriş / işten çıkış** tarihi (kişi sayfası > Düzenle): çıkıştan sonra ve girişten önce kişi beklenmez; ayrılanlar Gelmeyenler listesine girmez,
  Ayarlar > Personel saatleri'nde "Ayrılanları gizle" ile saklanır.
- Cihaz okuması toplama aralığının 3 katından uzun süredir yapılamıyorsa üstteki uyarı şeridi kırmızı olur ve Sistem sayfasına link verir.
- Takvimdeki gün adı kısaltması düzeltildi (Pazartesi artık "Pzt", Pazar "Paz").
- Veritabanı geriye uyumlu genişletildi (yeni sütunlar ve `tatil`, `duzeltme_log` tabloları otomatik eklenir; mevcut veri silinmez; ilk açılışta otomatik yedek alınır).

## 1.6.0
- İzin takibi: tam gün ve saatlik izin (yıllık, raporlu, mazeret, ücretsiz, diğer), tarih aralığıyla toplu giriş.
  Tam gün izinli gün "gelmedi" sayılmaz; saatlik izin beklenen giriş/çıkış saatini kaydırır.
- İzinler sayfası, personel takviminden tıklayarak izin girme, Bugün sayfasında "İzinli" kartı.
- Sürüm numarası ve otomatik yedekleme: her gün, her yeni sürümde ve her kurulumda; Sistem sayfasında yedek listesi/indirme.

## 1.5.0
- Süreler "1 saat 10 dakika" biçiminde (panel ve Excel).

## 1.4.0
- Dashboard arayüzü (açık/koyu tema, ikonlar), Personel sayfası, kişi detayı ve renkli takvim, grafikler.
- Demo modu kaldırıldı.

## 1.3.0
- Nöbet isim eşleştirmelerini düzenleme, kayıtlı nöbetleri düzeltme, tek tek nöbet ekleme/çıkarma.

## 1.2.0
- Ayarlar: genel mesai, kategoriler ve personel bazında çalışma saatleri; erken çıkış hesabı.

## 1.1.0
- Aylık nöbet çizelgesi yükleme (Excel/CSV) ve cumartesi nöbet takibi.

## 1.0.0
- Cihazdan salt-okunur toplama, SQLite, web panel, Excel/CSV dışa aktarma, kurulum betiği.
