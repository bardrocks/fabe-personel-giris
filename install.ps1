# EnGoPer Panel kurulumu - YONETICI PowerShell'de, bu klasorden calistirin:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
# Secenekler:
#   -Tetik Baslangic   (varsayilan) Windows acilisinda SYSTEM olarak, oturum gerekmez
#   -Tetik Oturum      Kullanici oturum acinca (Baslangic modunda cihaz okunamazsa bunu kullanin)
#   -TestAtla          Kurulumdaki tek seferlik cihaz okuma testini atla
#   -OturumdaAcma      Oturum acilinca paneli tarayicida otomatik acma (masaustu kisayolu yine eklenir)
#   -Kaldir            Gorevi ve guvenlik duvari kuralini kaldir (veriler kalir)
# EnGoPer klasorune (C:\EnGoPer) hicbir sey yazilmaz.
param(
    [ValidateSet('Baslangic', 'Oturum')][string]$Tetik = 'Baslangic',
    [string]$Hedef = 'C:\EnGoPerPanel',
    [int]$Port = 8080,
    [switch]$TestAtla,
    [switch]$OturumdaAcma,
    [switch]$Kaldir
)
$ErrorActionPreference = 'Stop'
$GorevAdi = 'EnGoPer Panel'
$KuralAdi = 'EnGoPer Panel (TCP ' + $Port + ')'

$kimlik = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $kimlik.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Yonetici PowerShell gerekli (sag tik > Yonetici olarak calistir).'
}

if ($Kaldir) {
    Stop-ScheduledTask -TaskName $GorevAdi -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $GorevAdi -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetFirewallRule -DisplayName $KuralAdi -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    foreach ($klasor in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Startup')) {
        foreach ($ad in 'Personel Paneli.lnk', 'Personel Paneli.url') {
            Remove-Item -LiteralPath (Join-Path $klasor $ad) -ErrorAction SilentlyContinue
        }
    }
    Write-Host "Kaldirildi. Veriler duruyor: $Hedef\data" -ForegroundColor Green
    return
}

# --- guvenlik: hedef EnGoPer klasorunun icinde olamaz
$hedefTam = [IO.Path]::GetFullPath($Hedef).TrimEnd('\') + '\'
if ($hedefTam.StartsWith('C:\EnGoPer\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Hedef klasor C:\EnGoPer altinda olamaz.'
}

# --- calisan servisi durdur ve mevcut veritabanini yedekle (guncellemede veri kaybi olmasin)
Stop-ScheduledTask -TaskName $GorevAdi -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$db = Join-Path $Hedef 'data\panel.db'
if (Test-Path $db) {
    $yedekKlasor = Join-Path $Hedef 'data\yedekler'
    New-Item -ItemType Directory -Force -Path $yedekKlasor | Out-Null
    $yedekAd = 'panel_' + (Get-Date -Format 'yyyy-MM-dd_HHmmss') + '_kurulum.db'
    Copy-Item -LiteralPath $db -Destination (Join-Path $yedekKlasor $yedekAd)
    foreach ($ek in '-wal', '-shm') {
        if (Test-Path ($db + $ek)) { Copy-Item -LiteralPath ($db + $ek) -Destination (Join-Path $yedekKlasor ($yedekAd + $ek)) }
    }
    Write-Host "Mevcut veritabani yedeklendi: $yedekKlasor\$yedekAd" -ForegroundColor Green
}

# --- dosyalari kopyala (data klasoru ve config.json korunur)
$kaynak = $PSScriptRoot
New-Item -ItemType Directory -Force -Path $Hedef, (Join-Path $Hedef 'data') | Out-Null
foreach ($dosya in 'panel.py', 'index.html', 'collector.ps1', 'baslat.vbs', 'panel.ico') {
    Copy-Item -LiteralPath (Join-Path $kaynak $dosya) -Destination $Hedef -Force
}
$ayar = Join-Path $Hedef 'config.json'
if (-not (Test-Path $ayar)) {
    Copy-Item -LiteralPath (Join-Path $kaynak 'config.example.json') -Destination $ayar
    Write-Host "Ayar dosyasi olusturuldu: $ayar"
}
else {
    Write-Host "Mevcut ayar korundu: $ayar"
}

# --- Python
$py = $null
try { $py = (& py -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1) } catch { }
if (-not $py) { try { $py = (Get-Command python.exe -ErrorAction Stop).Source } catch { } }
if (-not $py -or -not (Test-Path $py)) { throw 'Python bulunamadi (py / python.exe).' }
$ver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]'3.8') { throw "Python 3.8+ gerekli, bulunan: $ver" }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
Write-Host "Python: $py ($ver)"
# baslat.vbs pythonw yolunu ve portu buradan okur
[IO.File]::WriteAllText((Join-Path $Hedef 'python_yolu.txt'), $pyw + "`r`n")
[IO.File]::WriteAllText((Join-Path $Hedef 'port.txt'), [string]$Port + "`r`n")


# --- tek seferlik test okumasi
if (-not $TestAtla) {
    Write-Host 'Test: cihazdan bir kez okunuyor...' -ForegroundColor Cyan
    & $py (Join-Path $Hedef 'panel.py') --ayar $ayar --bir-kez
}

# --- guvenlik duvari (sadece Private/Domain)
Get-NetFirewallRule -DisplayName $KuralAdi -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $KuralAdi -Direction Inbound -Protocol TCP -LocalPort $Port `
    -Action Allow -Profile Private, Domain | Out-Null
$profiller = Get-NetConnectionProfile | Select-Object -ExpandProperty NetworkCategory
if ($profiller -contains 'Public') {
    Write-Warning 'Ag profili "Public". Diger cihazlar panele erisemez. Duzeltmek icin:'
    Write-Host '  Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private'
}

# --- zamanlanmis gorev
$eylem = New-ScheduledTaskAction -Execute $pyw -Argument ('"' + (Join-Path $Hedef 'panel.py') + '" --ayar "' + $ayar + '" --port ' + $Port + ' --tarayici-yok') -WorkingDirectory $Hedef
if ($Tetik -eq 'Baslangic') {
    $tetikleyici = New-ScheduledTaskTrigger -AtStartup
    $sorumlu = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
else {
    $kullanici = "$env:USERDOMAIN\$env:USERNAME"
    $tetikleyici = New-ScheduledTaskTrigger -AtLogOn -User $kullanici
    $sorumlu = New-ScheduledTaskPrincipal -UserId $kullanici -LogonType Interactive -RunLevel Highest
}
$tetikleyici.Delay = 'PT30S'
$ayarlar = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $GorevAdi -Action $eylem -Trigger $tetikleyici -Principal $sorumlu `
    -Settings $ayarlar -Force | Out-Null
Start-ScheduledTask -TaskName $GorevAdi

# --- kisayollar: masaustu + Baslat menusu + (istege bagli) oturum acilinca baslatici
$kisayolAdi = 'Personel Paneli.lnk'
$wsh = New-Object -ComObject WScript.Shell
function Yeni-Kisayol($yol) {
    $k = $wsh.CreateShortcut($yol)
    $k.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $k.Arguments = '"' + (Join-Path $Hedef 'baslat.vbs') + '"'
    $k.WorkingDirectory = $Hedef
    $k.IconLocation = (Join-Path $Hedef 'panel.ico') + ',0'
    $k.Description = 'Personel giri' + [char]0x15F + '-' + [char]0x00E7 + [char]0x0131 + 'k' + [char]0x0131 + [char]0x015F + ' paneli'   # script ASCII kalsin
    $k.Save()
}
$masaustu = [Environment]::GetFolderPath('Desktop')   # OneDrive'a yonlendirilmis olabilir
$programlar = [Environment]::GetFolderPath('Programs')
$baslangicKlasor = [Environment]::GetFolderPath('Startup')
# eski internet kisayollarini sil
foreach ($klasor in $masaustu, $programlar, $baslangicKlasor) {
    Remove-Item -LiteralPath (Join-Path $klasor 'Personel Paneli.url') -ErrorAction SilentlyContinue
}
Yeni-Kisayol (Join-Path $masaustu $kisayolAdi)
Yeni-Kisayol (Join-Path $programlar $kisayolAdi)
$baslangic = Join-Path $baslangicKlasor $kisayolAdi
if ($OturumdaAcma) {
    Remove-Item -LiteralPath $baslangic -ErrorAction SilentlyContinue
}
else {
    Yeni-Kisayol $baslangic
    Write-Host 'Oturum acilinca panel tarayicida acilacak.'
}
Write-Host "Masaustu kisayolu: $masaustu\$kisayolAdi (cift tiklayinca panel acilir)"

# --- kontrol
Start-Sleep -Seconds 5
try {
    $d = Invoke-RestMethod -Uri "http://localhost:$Port/api/durum" -TimeoutSec 10
    Write-Host "Panel calisiyor. Toplam okutma: $($d.toplam_okutma), personel: $($d.personel)" -ForegroundColor Green
}
catch {
    Write-Warning "Panel henuz yanit vermedi. Log: $Hedef\data\panel.log"
}
foreach ($ip in (Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress)) {
    if ($ip -notlike '127.*' -and $ip -notlike '169.254.*') { Write-Host "  http://${ip}:$Port" }
}
