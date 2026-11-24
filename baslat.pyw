# EnGoPer Panel baslatici (pythonw.exe ile calisir, konsol acmaz).
# Panel kapaliysa baslatir, hazir olunca Chrome'da (yoksa varsayilan tarayicida) acar.
# Sira: panel calisiyor mu -> "EnGoPer Panel" zamanlanmis gorevi -> panel.py dogrudan.
# Panel hazir olunca (internet varsa ve otomatik guncelleme aciksa) yeni surum yuklenir; panel yeni surumle donmezse
# eski kod yedeginden geri yuklenir. C:\EnGoPer altina hicbir sey yazmaz. Sadece standart kutuphane.
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


def json_al(port, yol, zaman_asimi=10, veri=None):
    data = json.dumps(veri).encode("utf-8") if veri is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, yol), data=data, method="POST" if veri is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with _ACICI.open(req, timeout=zaman_asimi) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            ayr = json.loads(e.read().decode("utf-8", "replace")).get("hata", "")
        except Exception:
            ayr = ""
        raise RuntimeError(ayr or "HTTP %d" % e.code)


def surum_oku(port):
    try:
        return json_al(port, "/api/surum", 3).get("surum", "")
    except Exception:
        return ""


def surum_bekle(port, yeni, saniye, bilgi=None):
    for _ in range(int(saniye)):
        time.sleep(1)
        if bilgi:
            bilgi.tazele()
        if surum_oku(port) == yeni:
            return True
    return False


class Bilgi:
    """Kucuk 'yukleniyor' penceresi (tkinter yoksa ya da ekran yoksa sessizce hicbir sey gostermez)."""

    def __init__(self, metin):
        self.k = None
        try:
            import tkinter
            self.k = tkinter.Tk()
            self.k.title("Personel Paneli")
            self.k.resizable(False, False)
            self.k.attributes("-topmost", True)
            tkinter.Label(self.k, text=metin, padx=28, pady=22, font=("Segoe UI", 11)).pack()
            self.k.update()
        except Exception:
            self.k = None

    def tazele(self):
        try:
            if self.k:
                self.k.update()
        except Exception:
            self.k = None

    def kapat(self):
        try:
            if self.k:
                self.k.destroy()
        except Exception:
            pass
        self.k = None


def kod_geri_al(kok=None):
    """data/guncelleme.json'daki kod yedeginden dosyalari geri kopyalar. -> True/False"""
    kok = kok or KOK
    try:
        yol = os.path.join(kok, "data", "guncelleme.json")
        with open(yol, encoding="utf-8") as f:
            k = json.load(f)
        yedek = k["kod_yedek"]
        for ad in os.listdir(yedek):
            import shutil
            shutil.copy2(os.path.join(yedek, ad), os.path.join(kok, ad))
        k["durum"] = "geri_alindi"
        with open(yol, "w", encoding="utf-8") as f:
            json.dump(k, f, ensure_ascii=False)
        return True
    except Exception as e:
        try:
            sys.stderr.write("Kod geri alinamadi: %s\n" % e)
        except Exception:
            pass
        return False


def guncelle_dene(port):
    """Sessizce atlar: internet yok / guncel / otomatik kapali. Basarisizsa eski surume doner ve hata kutusu gosterir."""
    try:
        g = json_al(port, "/api/guncelleme", 10)
    except Exception:
        return
    if g.get("hata") or not g.get("var") or not g.get("otomatik"):
        return
    eski, yeni = g.get("mevcut", "?"), g.get("uzak", "?")
    bilgi = Bilgi("Yeni sürüm yükleniyor (%s → %s)…\nLütfen bekleyin." % (eski, yeni))
    log_yolu = os.path.join(KOK, "data", "panel.log")
    try:
        try:
            r = json_al(port, "/api/guncelleme/uygula", 180, {})
        except Exception as e:
            bilgi.kapat()
            msg = str(e)
            if "yönetici" in msg or "izin" in msg:
                hata_goster("Güncelleme yapılamadı: %s" % msg)
            else:
                hata_goster("Güncelleme başlatılamadı, mevcut sürümle devam ediliyor: %s\nLog: %s" % (msg, log_yolu))
            return
        if not r.get("yeniden_baslatiliyor"):
            return
        yeni = r.get("yeni", yeni)
        ok = surum_bekle(port, yeni, float(os.environ.get("PANEL_BEKLE_SN", "45")), bilgi)
        if not ok and not calisiyor(port):  # yeniden baslatma olmadiysa yeni kodla dogrudan baslat
            paneli_baslat(port)
            ok = surum_bekle(port, yeni, float(os.environ.get("PANEL_BEKLE2_SN", "15")), bilgi)
        if not ok:
            bilgi.kapat()
            geri = kod_geri_al()
            if not calisiyor(port) or surum_oku(port) != eski:
                paneli_baslat(port)
                bekle(port, 15)
            hata_goster("Güncelleme başarısız oldu, eski sürüme dönüldü%s. Log: %s" % ("" if geri else " (kod geri yüklenemedi!)", log_yolu))
    finally:
        bilgi.kapat()


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
    try:
        guncelle_dene(port)
    except Exception as e:  # guncelleme hicbir sekilde tarayicinin acilmasini engellemesin
        try:
            sys.stderr.write("Guncelleme hatasi: %s\n" % e)
        except Exception:
            pass
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
