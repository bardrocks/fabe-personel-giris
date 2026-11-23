# Değişiklikler

Sürüm numarası `panel.py` içindeki `SURUM` değeridir; panelde sol menünün altında ve Sistem sayfasında görünür.
Yeni sürüm ilk açıldığında veritabanı, güncellenmeden önce `data\yedekler` klasörüne otomatik yedeklenir.

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
