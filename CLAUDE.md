# EnGoPer Panel – Proje Bağlamı

Fabe mağazasının personel giriş-çıkış verisini, mevcut EnGoPer sistemine HİÇ dokunmadan
parmak izi cihazından okuyup yerel bir web panelde gösteren proje. Bulut/Supabase YOK.

## ⛔ KESİN KURALLAR (ihlal edilemez)
- `C:\EnGoPer\` altındaki HİÇBİR dosyaya yazma, taşıma, silme, kilitleme yok. DBF'ler yalnızca
  `FileShare.ReadWrite` + `FileAccess.Read` ile açılır.
- Cihazda ASLA çağrılmayacak metotlar: `EmptyGeneralLogData`, `EmptySuperLogData`, `EmptyEnrollData`,
  `ClearKeeperData`, `DeleteEnrollData`, `ReadGeneralLogData` (yeni logları "okundu" işaretler →
  EnGoPer kayıt kaybeder), `SetDeviceTime`, `SetDeviceInfo`, `PowerOffDevice`, `Set*` her şey.
- `EnableDevice(mno, false)` sadece `ReadAllGLogData` süresince; `finally` içinde HER DURUMDA
  `EnableDevice(mno, true)`. Cihaz parmak okutmaya en fazla 1-2 sn kapalı kalmalı.
- `EnGoPer.exe` veya `M_Serisi.exe` çalışıyorsa o tur ATLANIR (cihaz tek bağlantı kabul ediyor).
- Kullanıcıya açıklama kısa; terminale hazır komut ve modüler kod tercih ediliyor.

## Ortam
- Makine: KASA 1 (Windows, kullanıcı `C:\Users\KASA 1`), IP `192.168.1.140/24`
- Masaüstü OneDrive'a yönlendirilmiş → yol için `[Environment]::GetFolderPath('Desktop')`
- Python kurulu (`py`). Yönetici PowerShell mevcut.
- Konsola yapıştırılan kodda `$_` bazen kayboluyor → `$_` yerine `foreach` / adlı değişken kullan.

## Cihaz
- Model TH600 (firmware V9.9), SN `ZXNH10284793`, IP `192.168.1.224`, Machine No `1`, port `5005`
- ICMP ping'e cevap vermez (normal). TCP 4370/5005/5010/80 açık. ZKTeco/pyzk ÇALIŞMAZ.
- Çalışan yöntem: **FP_CLOCK ActiveX OCX** (`FP_CLOCK.FP_CLOCKCtrl.1`, CLSID registry'den),
  **32-bit PowerShell + `-STA`**, OCX görünmez WinForms formu içinde `AxHost` ile host edilir
  (host etmeden E_UNEXPECTED verir). SBXPC OCX bağlanır ama `ReadAllGLogData` hata 3 verir → kullanma.
- Sıra: `SetIPAddress([ref]ip, 5005, 0)` → `OpenCommPort(1)` → `EnableDevice(1,false)` →
  `ReadAllGLogData(1)` → `EnableDevice(1,true)` → `GetAllGLogData(1, tm,en,em,vm,y,mo,d,h,mi)` döngüsü
  (10 parametre, saniye YOK; `GetAllGLogDataWithSecond` desteklenmiyor) → `CloseCommPort()`
- `GetDeviceStatus(1, 6, [ref]v)` = log sayısı; `2` = kullanıcı sayısı (153).
- Verify mode her zaman 0 → cihaz giriş/çıkış ayırmıyor: günün ilk okutması = Giriş, son = Çıkış.
- Cihazda sadece son EnGoPer aktarımından bu yana kayıt var (EnGoPer muhtemelen aktarım sonrası
  temizliyor) → sık okuma (5 dk) şart, kalıcı kayıt bizim DB'de.
- Okumanın yıkıcı olmadığı test edildi: art arda iki okuma 445 → 445.

## Çalışan referans script
`C:\Users\KASA 1\fp_read.ps1` — cihazı okur, Personel.DBF ile eşler, masaüstüne
`cihaz_okutmalar.csv` + `cihaz_gunluk.csv` yazar. Çalıştırma:
```
& "$env:windir\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -STA -ExecutionPolicy Bypass -File "$env:USERPROFILE\fp_read.ps1"
```

## EnGoPer verisi (salt-okunur)
- Delphi + BDE + dBase IV (`.DBF` v0x03, kod sayfası 1254). Header: kayıt sayısı @4 (uint32),
  header uzunluğu @8, kayıt uzunluğu @10, alan tanımları 32'şer bayt @32, 0x0D ile biter.
  İlk bayt `*` (0x2A) = silinmiş kayıt.
- `C:\EnGoPer\Data\Personel.DBF` (458 kayıt): `KARTNO` (C10, ör. "0111"), `ADI`, `SOYADI`, `SICILNO`.
  **Cihaz kişi no (EnrollNumber) == int(KARTNO)** → eşleşme %100 doğrulandı.
  Hassas alanlar da var (TC, maaş, adres) → panele/DB'ye SADECE KARTNO, ADI, SOYADI, SICILNO alınır.
- `C:\EnGoPer\Data\GCDATA.DBF` (~72k): EnGoPer'ın işlenmiş günlük tablosu (TARIH, GIRIS, CIKIS,
  GIRISNUM/CIKISNUM dakika, BILGI "OK"/"CIKIS YOK", ADI, SOYADI, SICILNO). Geçmiş veri için
  bir kerelik içe aktarma kaynağı olabilir.
- `C:\EnGoPer\S300_Aktarim\backup.txt`: ham log, format `001,000094,01,2026/11/18,09:15:00`.

## Hedef mimari (onaylandı)
- Tek Python servisi (sadece stdlib: sqlite3, http.server, subprocess, json, threading):
  1. 5 dk'da bir: EnGoPer/M_Serisi çalışmıyorsa 32-bit PS toplayıcıyı çalıştır (JSON çıktı),
     sonuçları SQLite'a `UNIQUE(cihaz, kisi_no, zaman)` ile tekilleştirerek ekle.
     Timeout, bağlantı hatası, yarım çıktı → logla ve sonraki turu bekle; servis asla çökmesin.
  2. Personel adlarını Personel.DBF'den periyodik yenile.
  3. Web panel `http://0.0.0.0:8080` (LAN): şu an içeride kim var, bugünün giriş-çıkışları,
     geç kalan / çıkış yapmayan, tarih aralığı + kişi filtresi, CSV/Excel dışa aktarma. Mobil uyumlu.
- Güvenlik duvarı: 8080 için gelen kural (sadece Private/Domain profil).
- Windows açılışında otomatik başlatma (Görev Zamanlayıcı, en yüksek yetki, kullanıcı oturumu
  gerekmeden; OCX STA + form gerektirdiği için gerekirse oturum açılışında başlat).
- Kodlar `C:\EnGoPerPanel\` altında (EnGoPer klasörünün DIŞINDA).
