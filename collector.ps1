# EnGoPer Panel - cihaz toplayici (SALT OKUMA)
# 32-bit PowerShell + -STA ile calistirilir. Sonucu -OutFile'a JSON yazar.
#   & "$env:windir\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -STA -ExecutionPolicy Bypass `
#     -File C:\EnGoPerPanel\collector.ps1 -OutFile "$env:TEMP\okuma.json"
#
# KURALLAR: Cihazda sadece okuma metotlari. ReadGeneralLogData / Empty* / Delete* / Set* ASLA.
# EnableDevice(false) sadece ReadAllGLogData suresince, finally icinde her durumda true.
param(
    [string]$IP = '192.168.1.224',
    [int]$Port = 5005,
    [int]$MachineNo = 1,
    [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = 'Stop'
$baslangic = Get-Date
$sonuc = [ordered]@{
    ok               = $false
    atlandi          = $false
    hata             = $null
    cihaz_log_sayisi = $null
    kayit_sayisi     = 0
    baslangic        = $baslangic.ToString('yyyy-MM-dd HH:mm:ss')
    sure_ms          = 0
    kayitlar         = @()
}

function Sonuc-Yaz {
    $sonuc.sure_ms = [int]((Get-Date) - $baslangic).TotalMilliseconds
    $json = $sonuc | ConvertTo-Json -Depth 4 -Compress
    $tmp = "$OutFile.tmp"
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
    Move-Item -LiteralPath $tmp -Destination $OutFile -Force
}

$ocx = $null
$form = $null
try {
    if ([IntPtr]::Size -ne 4) { throw '32-bit PowerShell gerekli (SysWOW64).' }
    if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') { throw '-STA gerekli.' }

    # Cihaz tek baglanti kabul ediyor: EnGoPer / M_Serisi aciksa bu tur atlanir.
    $engel = Get-Process -Name 'EnGoPer', 'M_Serisi' -ErrorAction SilentlyContinue
    if ($engel) {
        $sonuc.atlandi = $true
        $sonuc.hata = 'ATLANDI: ' + (($engel | Select-Object -ExpandProperty ProcessName -Unique) -join ', ') + ' calisiyor'
        Sonuc-Yaz
        exit 0
    }

    Add-Type -AssemblyName System.Windows.Forms
    if (-not ('FpAxHost' -as [type])) {
        Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
using System.Windows.Forms;
public class FpAxHost : AxHost {
    public FpAxHost(string clsid) : base(clsid) {}
    public object Ocx { get { return this.GetOcx(); } }
}
'@
    }

    $clsid = (Get-ItemProperty -LiteralPath 'Registry::HKEY_CLASSES_ROOT\FP_CLOCK.FP_CLOCKCtrl.1\CLSID').'(default)'
    if (-not $clsid) { throw 'FP_CLOCK.FP_CLOCKCtrl.1 CLSID registry''de yok (OCX kayitli degil).' }
    $clsid = $clsid.Trim('{', '}')

    # OCX gorunmez form icinde host edilmeli (aksi halde E_UNEXPECTED).
    $form = New-Object System.Windows.Forms.Form
    $form.ShowInTaskbar = $false
    $form.WindowState = 'Minimized'
    $form.Opacity = 0
    $axh = New-Object FpAxHost($clsid)
    ([System.ComponentModel.ISupportInitialize]$axh).BeginInit()
    $form.Controls.Add($axh)
    ([System.ComponentModel.ISupportInitialize]$axh).EndInit()
    $null = $form.Handle
    $null = $axh.Handle
    $ocx = $axh.Ocx
    if ($null -eq $ocx) { throw 'OCX nesnesi olusturulamadi.' }

    $ipRef = $IP
    if (-not $ocx.SetIPAddress([ref]$ipRef, $Port, 0)) { throw "SetIPAddress basarisiz ($IP`:$Port)" }
    if (-not $ocx.OpenCommPort($MachineNo)) {
        $e = 0; try { $null = $ocx.GetLastError([ref]$e) } catch { }
        throw "OpenCommPort basarisiz (hata $e)"
    }

    try {
        $adet = 0
        if ($ocx.GetDeviceStatus($MachineNo, 6, [ref]$adet)) { $sonuc.cihaz_log_sayisi = $adet }

        # --- Cihaz kilidi: yalnizca ReadAllGLogData suresince ---
        $okundu = $false
        $null = $ocx.EnableDevice($MachineNo, $false)
        try {
            $okundu = $ocx.ReadAllGLogData($MachineNo)
        }
        finally {
            $null = $ocx.EnableDevice($MachineNo, $true)
        }
        # ---------------------------------------------------------

        if (-not $okundu) {
            $e = 0; try { $null = $ocx.GetLastError([ref]$e) } catch { }
            # Bos log da false donebilir; log sayisi 0 ise hata sayma.
            if ($sonuc.cihaz_log_sayisi -eq 0) { $sonuc.ok = $true } else { throw "ReadAllGLogData basarisiz (hata $e)" }
        }
        else {
            $liste = New-Object System.Collections.ArrayList
            $tm = 0; $en = 0; $em = 0; $vm = 0; $y = 0; $mo = 0; $d = 0; $h = 0; $mi = 0
            $sinir = 500000
            while ($sinir -gt 0 -and $ocx.GetAllGLogData($MachineNo, [ref]$tm, [ref]$en, [ref]$em, [ref]$vm,
                    [ref]$y, [ref]$mo, [ref]$d, [ref]$h, [ref]$mi)) {
                $sinir--
                $t = '{0:D4}-{1:D2}-{2:D2} {3:D2}:{4:D2}' -f [int]$y, [int]$mo, [int]$d, [int]$h, [int]$mi
                $null = $liste.Add([pscustomobject]@{ k = [int]$en; t = $t; vm = [int]$vm; em = [int]$em })
            }
            $sonuc.kayitlar = $liste.ToArray()
            $sonuc.kayit_sayisi = $liste.Count
            $sonuc.ok = $true
        }
    }
    finally {
        try { $ocx.CloseCommPort() } catch { }
    }
}
catch {
    $sonuc.ok = $false
    $sonuc.hata = $_.Exception.Message
}
finally {
    if ($form) { try { $form.Dispose() } catch { } }
}

Sonuc-Yaz
if ($sonuc.ok -or $sonuc.atlandi) { exit 0 } else { exit 1 }
