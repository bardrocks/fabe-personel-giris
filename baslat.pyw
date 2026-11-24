# EnGoPer Panel baslatici (pythonw.exe ile calisir, konsol acmaz).
# Panel kapaliysa baslatir, hazir olunca Chrome'da (yoksa varsayilan tarayicida) acar.
# Sira: panel calisiyor mu -> "EnGoPer Panel" zamanlanmis gorevi -> panel.py dogrudan.
# C:\EnGoPer altina hicbir sey yazmaz. Sadece standart kutuphane.
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

KOK = os.path.dirname(os.path.abspath(__file__))
# Sistem proxy ayari 127.0.0.1 kontrolunu bozmasin
_ACICI = urllib.request.build_opener(urllib.request.ProxyHandler({}))
WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def port_oku():
    port = 8080
    try:
        yol = os.path.join(KOK, "port.txt")
        if os.path.exists(yol):
            with open(yol, encoding="utf-8-sig") as f:
                m = re.search(r"\d+", f.read())
            if m:
                port = int(m.group(0))
        else:
            yol = os.path.join(KOK, "config.json")
            if os.path.exists(yol):
                with open(yol, encoding="utf-8-sig", errors="replace") as f:
                    m = re.search(r'"http_port"\s*:\s*(\d+)', f.read())
                if m:
                    port = int(m.group(1))
    except Exception:
        pass
    return port if 1 <= port <= 65535 else 8080


def calisiyor(port):
    """200 ya da 401 (panel sifresi acik) = servis ayakta."""
    try:
        with _ACICI.open("http://127.0.0.1:%d/api/durum" % port, timeout=2) as r:
            return r.status in (200, 401)
    except urllib.error.HTTPError as e:
        return e.code in (200, 401)
    except Exception:
        return False


def bekle(port, saniye):
    for _ in range(saniye):
        time.sleep(1)
        if calisiyor(port):
            return True
    return False


def gorevi_calistir():
    """True: gorev tetiklendi. False: gorev yok/calismadi."""
    try:
        p = subprocess.run(["schtasks", "/Run", "/TN", "EnGoPer Panel"], creationflags=CREATE_NO_WINDOW,
                           capture_output=True, timeout=15)
        return p.returncode == 0
    except Exception:
        return False


def paneli_baslat(port):
    komut = [sys.executable, os.path.join(KOK, "panel.py"), "--ayar", os.path.join(KOK, "config.json"),
             "--port", str(port), "--tarayici-yok"]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, cwd=KOK)
    if WIN:
        kw["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
    subprocess.Popen(komut, **kw)


def tarayici_ac(port):
    url = "http://localhost:%d/" % port
    for ad in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        taban = os.environ.get(ad)
        if not taban:
            continue
        chrome = os.path.join(taban, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.exists(chrome):
            try:
                subprocess.Popen([chrome, url])
                return
            except Exception:
                pass
    import webbrowser
    webbrowser.open(url)


def hata_goster(mesaj):
    if WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, mesaj, "Personel Paneli", 0x10)
            return
        except Exception:
            pass
    try:
        sys.stderr.write(mesaj + "\n")
    except Exception:
        pass


def main():
    port = port_oku()
    ok = calisiyor(port)
    if not ok:
        time.sleep(0.5)
        ok = calisiyor(port)  # arka arkaya cift tiklamada ikinci baslatma olmasin
    if not ok and WIN and gorevi_calistir():
        ok = bekle(port, 12)
    if not ok:
        if calisiyor(port):  # baska bir baslatici araya girmis olabilir
            ok = True
        else:
            paneli_baslat(port)
            ok = bekle(port, 15)
    if not ok:
        hata_goster("Panel başlatılamadı. Log: %s. Yönetici olarak install.ps1'i tekrar çalıştırmayı deneyin."
                    % os.path.join(KOK, "data", "panel.log"))
        return 1
    tarayici_ac(port)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:
        hata_goster("Beklenmeyen hata: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
