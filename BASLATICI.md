# Personel Paneli başlatıcı

- Masaüstündeki (ve Başlat menüsündeki) **Personel Paneli** kısayoluna çift tıklayın: `baslat.vbs` konsol açmadan paneli kontrol eder, kapalıysa başlatır ve Chrome'da `http://localhost:8080/` sayfasını açar (Chrome yoksa varsayılan tarayıcı).
- Başlatma sırası: panel zaten çalışıyorsa doğrudan tarayıcı → `EnGoPer Panel` zamanlanmış görevi → `pythonw.exe panel.py` ile doğrudan başlatma. Port `port.txt` / `config.json` (`http_port`) dosyasından okunur.
- Panel açılmazsa: ekrandaki hata penceresinde yazan log'a bakın (`C:\EnGoPerPanel\data\panel.log`); sonra yönetici PowerShell'de `install.ps1`'i tekrar çalıştırın.
- Elle çalıştırmak için: `wscript "C:\EnGoPerPanel\baslat.vbs"` (ya da dosyaya çift tıklayın).
- Kısayolları kaldırmak için `install.ps1 -Kaldir`. Kısayollar `install.ps1` ile oluşur; ikon `panel.ico`.
