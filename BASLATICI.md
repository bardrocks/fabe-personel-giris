# Personel Paneli başlatıcı

- Masaüstündeki (ve Başlat menüsündeki) **Personel Paneli** kısayoluna çift tıklayın: `baslat.pyw` (pythonw ile, konsol açmadan) paneli kontrol eder, kapalıysa başlatır ve Chrome'da `http://localhost:8080/` sayfasını açar (Chrome yoksa varsayılan tarayıcı).
- Sıra: panel zaten çalışıyorsa doğrudan tarayıcı → `EnGoPer Panel` zamanlanmış görevi → `panel.py` doğrudan başlatma. Port `port.txt` / `config.json` (`http_port`) dosyasından okunur.
- Panel açılmazsa: hata penceresinde yazan log'a bakın (`C:\EnGoPerPanel\data\panel.log`); sonra yönetici PowerShell'de `install.ps1`'i tekrar çalıştırın.
- Elle çalıştırmak için: `baslat.pyw` dosyasına çift tıklayın ya da `pythonw C:\EnGoPerPanel\baslat.pyw`.
- Kısayollar `install.ps1` ile oluşur, `install.ps1 -Kaldir` ile silinir; ikon `panel.ico`.
- Not: Eski sürümde VBS kullanılıyordu; Defender (AMSI) engellediği için Python başlatıcıya geçildi. `install.ps1` eski `baslat.vbs`'i siler.
