#!/usr/bin/env python3
"""EnGoPer Panel - personel giris/cikis servisi (sadece Python stdlib).

  py panel.py                      servisi baslat (toplayici + web panel)
  py panel.py --bir-kez            tek okuma turu yap, sonucu yaz, cik
  py panel.py --ice-aktar-backup   EnGoPer backup.txt ham logunu bir kerelik ice aktar

KURAL: C:\\EnGoPer\\ altina hicbir sey yazilmaz; DBF/TXT yalnizca okunur.
"""
import argparse
import csv
import datetime as dt
import io
import json
import logging
import logging.handlers
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from xml.sax.saxutils import escape

SURUM = "1.6.0"  # her yeni ozellikte artirilir; degisiklikler DEGISIKLIKLER.md'de
KOK = os.path.dirname(os.path.abspath(__file__))
WIN = os.name == "nt"
ENGOPER_KLASOR = r"C:\EnGoPer"

VARSAYILAN = {
    "veri_klasoru": os.path.join(KOK, "data"),
    "cihaz_adi": "TH600-ZXNH10284793",
    "cihaz_ip": "192.168.1.224",
    "cihaz_port": 5005,
    "makine_no": 1,
    "toplama_aralik_sn": 300,
    "toplayici_zaman_asimi_sn": 180,
    "manuel_toplama_min_aralik_sn": 60,
    "engel_surecler": ["EnGoPer.exe", "M_Serisi.exe"],
    "personel_dbf": r"C:\EnGoPer\Data\Personel.DBF",
    "backup_txt": r"C:\EnGoPer\S300_Aktarim\backup.txt",
    "personel_yenileme_sn": 1800,
    "http_adres": "0.0.0.0",
    "http_port": 8080,
    # Ilk kurulumdaki genel mesai; sonra panelin Ayarlar sekmesinden degistirilir (DB'de saklanir).
    "mesai_baslangic": "09:00",
    "mesai_bitis": "20:00",
    "cumartesi_mesai_baslangic": "09:00",
    "cumartesi_mesai_bitis": "20:00",
    "gec_tolerans_dk": 5,
    "erken_tolerans_dk": 5,
    "tekrar_esik_dk": 3,
    # "Gelmedi" sayilacak gunler (0=Pazartesi ... 6=Pazar). Cumartesi nobet cizelgesinden takip edilir.
    "calisma_gunleri": [0, 1, 2, 3, 4],
    "yonetim_sifresi": "",
    # Test/gelistirme icin: toplayici yerine calisacak komut ("{cikti}" yer tutucusu).
    "toplayici_komut": None,
    # Yedekler veri_klasoru\\yedekler altinda; ek klasor (or. OneDrive) verilirse oraya da kopyalanir.
    "ek_yedek_klasoru": "",
    "gunluk_yedek_sayisi": 30,
    "tarayici_ac": True,  # panel baslayinca varsayilan tarayicida ac (servis/SYSTEM modunda acilmaz)
}

log = logging.getLogger("panel")
CFG = dict(VARSAYILAN)


# ----------------------------------------------------------------- yardimcilar
def engoper_icinde(yol):
    a = os.path.normcase(os.path.abspath(yol))
    b = os.path.normcase(ENGOPER_KLASOR)
    return a == b or a.startswith(b + os.sep)


def ayar_yukle(yol):
    cfg = dict(VARSAYILAN)
    if os.path.exists(yol):
        with open(yol, encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    if engoper_icinde(cfg["veri_klasoru"]) or engoper_icinde(KOK):
        raise SystemExit("HATA: panel/veri klasoru C:\\EnGoPer altinda olamaz.")
    return cfg


def simdi():
    return dt.datetime.now()


def zstr(t):
    return t.strftime("%Y-%m-%d %H:%M")


def dk(saat):  # "09:15" -> 555
    h, m = saat.split(":")[:2]
    return int(h) * 60 + int(m)


def sure_yaz(dakika):
    """70 -> '1 saat 10 dakika', 45 -> '45 dakika', 120 -> '2 saat', 0 -> ''"""
    dakika = int(dakika or 0)
    if dakika <= 0:
        return ""
    h, m = divmod(dakika, 60)
    return " ".join(p for p in (f"{h} saat" if h else "", f"{m} dakika" if m else "") if p)


def tr_upper(s):
    return s.replace("i", "İ").replace("ı", "I").upper()


_KATLA = str.maketrans("İIŞĞÜÖÇÂÎÛ", "IISGUOCAIU")


def katla(s):
    """Eslestirme anahtari: Turkce buyuk harf, aksansiz, tek bosluk."""
    s = tr_upper(str(s)).translate(_KATLA)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split())


# ----------------------------------------------------------------- DBF (salt okuma)
def dbf_oku(yol, alanlar):
    """dBase dosyasini tek seferde belleğe okur; yalnizca istenen alanlari cozer."""
    # Python open() Windows'ta paylasimli (deny-none) acar; yazma yok.
    with open(yol, "rb") as f:
        veri = f.read()
    n = struct.unpack_from("<I", veri, 4)[0]
    hl, rl = struct.unpack_from("<HH", veri, 8)
    tanim, pos, off = {}, 32, 1
    while pos < hl and veri[pos] != 0x0D:
        ad = veri[pos:pos + 11].split(b"\0")[0].decode("ascii", "replace").upper()
        uz = veri[pos + 16]
        tanim[ad] = (off, uz)
        off += uz
        pos += 32
    eksik = [a for a in alanlar if a not in tanim]
    if eksik:
        raise ValueError(f"DBF alan(lar)i yok: {eksik}")
    for i in range(n):
        r = veri[hl + i * rl: hl + (i + 1) * rl]
        if len(r) < rl or r[0] == 0x2A:
            continue
        yield {a: r[tanim[a][0]:tanim[a][0] + tanim[a][1]].decode("cp1254", "replace").strip() for a in alanlar}


# ----------------------------------------------------------------- veritabani
SEMA = """
CREATE TABLE IF NOT EXISTS okutma(
  id INTEGER PRIMARY KEY, cihaz TEXT NOT NULL, kisi_no INTEGER NOT NULL, zaman TEXT NOT NULL,
  vm INTEGER, kaynak TEXT, eklenme TEXT, UNIQUE(cihaz, kisi_no, zaman));
CREATE INDEX IF NOT EXISTS ix_okutma_zaman ON okutma(zaman);
CREATE TABLE IF NOT EXISTS personel(
  kisi_no INTEGER PRIMARY KEY, kartno TEXT, adi TEXT, soyadi TEXT, sicilno TEXT, guncelleme TEXT);
CREATE TABLE IF NOT EXISTS tur_log(
  id INTEGER PRIMARY KEY, zaman TEXT, kaynak TEXT, durum TEXT, sure_ms INTEGER,
  okunan INTEGER, yeni INTEGER, mesaj TEXT);
CREATE TABLE IF NOT EXISTS nobet(
  id INTEGER PRIMARY KEY, tarih TEXT NOT NULL, kisi_no INTEGER NOT NULL, metin TEXT,
  yukleme_id INTEGER, UNIQUE(tarih, kisi_no));
CREATE TABLE IF NOT EXISTS ayar(anahtar TEXT PRIMARY KEY, deger TEXT);
CREATE TABLE IF NOT EXISTS kategori(
  id INTEGER PRIMARY KEY, ad TEXT NOT NULL UNIQUE, giris TEXT, cikis TEXT, cts_giris TEXT, cts_cikis TEXT);
CREATE TABLE IF NOT EXISTS personel_ayar(
  kisi_no INTEGER PRIMARY KEY, kategori_id INTEGER, giris TEXT, cikis TEXT, cts_giris TEXT, cts_cikis TEXT);
CREATE TABLE IF NOT EXISTS nobet_eslesme(anahtar TEXT PRIMARY KEY, kisi_no INTEGER);
CREATE TABLE IF NOT EXISTS izin(
  id INTEGER PRIMARY KEY, kisi_no INTEGER NOT NULL, tarih TEXT NOT NULL, bas_saat TEXT, bit_saat TEXT,
  tur TEXT, aciklama TEXT, grup TEXT, olusturma TEXT, UNIQUE(kisi_no, tarih));
CREATE INDEX IF NOT EXISTS ix_izin_tarih ON izin(tarih);
CREATE TABLE IF NOT EXISTS nobet_yukleme(
  id INTEGER PRIMARY KEY, zaman TEXT, dosya TEXT, tarih_sayisi INTEGER, kayit_sayisi INTEGER);
"""


def db_yolu():
    return os.path.join(CFG["veri_klasoru"], "panel.db")


@contextmanager
def db():
    con = sqlite3.connect(db_yolu(), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def db_hazirla():
    os.makedirs(CFG["veri_klasoru"], exist_ok=True)
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SEMA)
        for tablo, sutun in (("nobet", "tam_anahtar TEXT"), ("nobet_eslesme", "metin TEXT")):
            mevcut = {r[1] for r in con.execute(f"PRAGMA table_info({tablo})")}
            if sutun.split()[0] not in mevcut:
                con.execute(f"ALTER TABLE {tablo} ADD COLUMN {sutun}")


# ----------------------------------------------------------------- yedekleme
_YEDEK_RE = re.compile(r"^panel_(\d{4}-\d{2}-\d{2})_(\d{6})_([\w.\-]+)\.db$")
_yedek_kilit = threading.Lock()


def yedek_klasoru():
    yol = os.path.join(CFG["veri_klasoru"], "yedekler")
    os.makedirs(yol, exist_ok=True)
    return yol


def yedek_al(neden):
    """Calisirken de tutarli kopya alir (sqlite backup API). -> dosya adi"""
    if not os.path.exists(db_yolu()):
        return None
    neden = re.sub(r"[^\w.\-]", "", neden)[:40] or "elle"
    with _yedek_kilit:
        ad = f"panel_{simdi():%Y-%m-%d_%H%M%S}_{neden}.db"
        hedef = os.path.join(yedek_klasoru(), ad)
        kaynak, kopya = sqlite3.connect(db_yolu(), timeout=30), sqlite3.connect(hedef)
        try:
            kaynak.backup(kopya)
        finally:
            kopya.close()
            kaynak.close()
        ek = CFG.get("ek_yedek_klasoru")
        if ek:
            try:
                if engoper_icinde(ek):
                    raise ValueError("ek yedek klasoru C:\\EnGoPer altinda olamaz")
                os.makedirs(ek, exist_ok=True)
                shutil.copy2(hedef, os.path.join(ek, ad))
                yedek_temizle(ek)
            except Exception as e:
                log.warning("Ek yedek klasorune kopyalanamadi (%s): %s", ek, e)
        yedek_temizle(yedek_klasoru())
    log.info("Yedek alindi: %s", ad)
    return ad


def yedek_temizle(klasor):
    """Gunluk yedeklerden son N tanesi, diger turlerden (surum, kurulum, elle) son 30'ar tanesi kalir."""
    gruplar = {}
    for ad in os.listdir(klasor):
        m = _YEDEK_RE.match(ad)
        if m:
            tur = m.group(3).split("-")[0]
            gruplar.setdefault(tur, []).append(ad)
    for tur, adlar in gruplar.items():
        tut = CFG["gunluk_yedek_sayisi"] if tur == "gunluk" else 30
        for ad in sorted(adlar, reverse=True)[tut:]:
            for ek in ("", "-wal", "-shm"):
                try:
                    os.remove(os.path.join(klasor, ad + ek))
                except FileNotFoundError:
                    pass


def yedek_listesi():
    out = []
    for ad in os.listdir(yedek_klasoru()):
        m = _YEDEK_RE.match(ad)
        if m:
            t = m.group(2)
            out.append({"ad": ad, "zaman": f"{m.group(1)} {t[:2]}:{t[2:4]}", "tur": m.group(3),
                        "boyut_kb": os.path.getsize(os.path.join(yedek_klasoru(), ad)) // 1024})
    out.sort(key=lambda x: x["ad"], reverse=True)
    return out


def gunluk_yedek():
    bugun = simdi().strftime("%Y-%m-%d")
    if not any(a.startswith(f"panel_{bugun}_") and a.endswith("_gunluk.db") for a in os.listdir(yedek_klasoru())):
        yedek_al("gunluk")


def surum_kontrol():
    """Yeni surum ilk kez aciliyorsa, veritabani guncellenmeden ONCE yedek alir."""
    if not os.path.exists(db_yolu()):
        return None
    eski = "eski"
    con = sqlite3.connect(db_yolu(), timeout=30)
    try:
        row = con.execute("SELECT deger FROM ayar WHERE anahtar='db_surum'").fetchone()
        eski = row[0] if row else "eski"
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
    if eski != SURUM:
        log.info("Surum degisti: %s -> %s, yedek aliniyor", eski, SURUM)
        yedek_al(f"surum-{eski}-{SURUM}")
    return eski


def surum_yaz():
    with db() as con:
        con.execute("INSERT INTO ayar(anahtar,deger) VALUES('db_surum',?) "
                    "ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger", (SURUM,))


_ZAMAN_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")


def okutma_ekle(kayitlar, kaynak):
    """[(kisi_no, 'YYYY-MM-DD HH:MM', vm)] -> (gecerli, yeni)."""
    ust = simdi() + dt.timedelta(days=2)
    gecerli, yeni, ek = 0, 0, zstr(simdi())
    with db() as con:
        once = con.total_changes
        for k, t, vm in kayitlar:
            try:
                k = int(k)
                if k <= 0 or not _ZAMAN_RE.match(t):
                    continue
                z = dt.datetime.strptime(t, "%Y-%m-%d %H:%M")
                if z.year < 2000 or z > ust:
                    continue
            except (TypeError, ValueError):
                continue
            gecerli += 1
            con.execute("INSERT OR IGNORE INTO okutma(cihaz,kisi_no,zaman,vm,kaynak,eklenme) VALUES(?,?,?,?,?,?)",
                        (CFG["cihaz_adi"], k, t, vm, kaynak, ek))
        yeni = con.total_changes - once
    return gecerli, yeni


# ----------------------------------------------------------------- personel
_personel_mtime = None


def personel_yenile(zorla=False):
    global _personel_mtime
    yol = CFG["personel_dbf"]
    try:
        mt = os.path.getmtime(yol)
        if not zorla and mt == _personel_mtime:
            return
        satirlar = list(dbf_oku(yol, ["KARTNO", "ADI", "SOYADI", "SICILNO"]))
    except Exception as e:
        log.warning("Personel.DBF okunamadi: %s", e)
        return
    ek, n = zstr(simdi()), 0
    with db() as con:
        for s in satirlar:
            if not s["KARTNO"].isdigit():
                continue
            con.execute("INSERT INTO personel(kisi_no,kartno,adi,soyadi,sicilno,guncelleme) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(kisi_no) DO UPDATE SET kartno=excluded.kartno, adi=excluded.adi, "
                        "soyadi=excluded.soyadi, sicilno=excluded.sicilno, guncelleme=excluded.guncelleme",
                        (int(s["KARTNO"]), s["KARTNO"], s["ADI"], s["SOYADI"], s["SICILNO"], ek))
            n += 1
    _personel_mtime = mt
    log.info("Personel yenilendi: %d kayit", n)


def personel_sozluk(con):
    return {r["kisi_no"]: r for r in con.execute("SELECT * FROM personel")}


def ad_soyad(p, k):
    if p is None:
        return f"#{k} (tanımsız)"
    return f'{p["adi"]} {p["soyadi"]}'.strip() or f"#{k}"


# ----------------------------------------------------------------- toplayici
def engel_surec():
    """Calisan engel surecin adini dondurur. Kontrol yapilamazsa da guvenli tarafta kalir."""
    if not WIN:
        return None
    try:
        p = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=30,
                           creationflags=0x08000000)
        cikti = p.stdout.decode("cp857", "replace").lower()
    except Exception as e:
        return f"surec listesi alinamadi ({e})"
    adlar = {satir.split('","')[0].strip('"') for satir in cikti.splitlines() if satir}
    for e in CFG["engel_surecler"]:
        if e.lower() in adlar:
            return e
    return None


class Toplayici:
    def __init__(self):
        self.kilit = threading.Lock()
        self.son_manuel = 0.0

    def komut(self, cikti):
        if CFG["toplayici_komut"]:
            return [str(a).replace("{cikti}", cikti) for a in CFG["toplayici_komut"]]
        ps = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "SysWOW64", "WindowsPowerShell", "v1.0",
                          "powershell.exe")
        return [ps, "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass",
                "-File", os.path.join(KOK, "collector.ps1"), "-IP", CFG["cihaz_ip"],
                "-Port", str(CFG["cihaz_port"]), "-MachineNo", str(CFG["makine_no"]), "-OutFile", cikti]

    def tur(self, kaynak="zamanlayici"):
        if not self.kilit.acquire(blocking=False):
            return {"durum": "MESGUL", "mesaj": "Başka bir okuma sürüyor"}
        bas = time.time()
        durum, okunan, yeni, mesaj = "HATA", 0, 0, ""
        try:
            engel = engel_surec()
            if engel:
                durum, mesaj = "ATLANDI", f"{engel} çalışıyor"
            else:
                cikti = os.path.join(CFG["veri_klasoru"], "son_okuma.json")
                if os.path.exists(cikti):
                    os.remove(cikti)
                p = subprocess.run(self.komut(cikti), capture_output=True,
                                   timeout=CFG["toplayici_zaman_asimi_sn"],
                                   creationflags=0x08000000 if WIN else 0)
                if not os.path.exists(cikti):
                    hata = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip()[-400:]
                    raise RuntimeError(f"toplayıcı çıktı üretmedi (kod {p.returncode}) {hata}")
                with open(cikti, encoding="utf-8-sig") as f:
                    js = json.load(f)
                if js.get("atlandi"):
                    durum, mesaj = "ATLANDI", js.get("hata") or ""
                elif not js.get("ok"):
                    raise RuntimeError(js.get("hata") or "bilinmeyen hata")
                else:
                    kayitlar = js.get("kayitlar") or []
                    if isinstance(kayitlar, dict):  # PS tek elemani dizi yapmayabilir
                        kayitlar = [kayitlar]
                    okunan, yeni = okutma_ekle([(r.get("k"), r.get("t"), r.get("vm")) for r in kayitlar], "cihaz")
                    durum = "OK"
                    mesaj = f'cihaz log sayısı {js.get("cihaz_log_sayisi")}, toplayıcı {js.get("sure_ms")} ms'
        except subprocess.TimeoutExpired:
            mesaj = f'zaman aşımı ({CFG["toplayici_zaman_asimi_sn"]} sn)'
        except Exception as e:
            mesaj = str(e)[:500]
        finally:
            sure = int((time.time() - bas) * 1000)
            try:
                with db() as con:
                    con.execute("INSERT INTO tur_log(zaman,kaynak,durum,sure_ms,okunan,yeni,mesaj) VALUES(?,?,?,?,?,?,?)",
                                (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kaynak, durum, sure, okunan, yeni, mesaj))
                    con.execute("DELETE FROM tur_log WHERE id < (SELECT MAX(id) - 5000 FROM tur_log)")
            except Exception:
                log.exception("tur_log yazilamadi")
            self.kilit.release()
        (log.info if durum != "HATA" else log.warning)("Tur %s: %s okunan=%d yeni=%d %s", kaynak, durum, okunan, yeni, mesaj)
        return {"durum": durum, "okunan": okunan, "yeni": yeni, "mesaj": mesaj}

    def manuel(self):
        if time.time() - self.son_manuel < CFG["manuel_toplama_min_aralik_sn"]:
            return {"durum": "BEKLE", "mesaj": f'En fazla {CFG["manuel_toplama_min_aralik_sn"]} sn\'de bir'}
        self.son_manuel = time.time()
        return self.tur("manuel")

    def dongu(self):
        son_personel = 0.0
        while True:
            try:
                if time.time() - son_personel > CFG["personel_yenileme_sn"]:
                    personel_yenile()
                    son_personel = time.time()
                self.tur()
            except Exception:
                log.exception("Dongu hatasi (devam ediliyor)")
            try:
                gunluk_yedek()
            except Exception:
                log.exception("Gunluk yedek alinamadi")
            time.sleep(CFG["toplama_aralik_sn"])


TOPLAYICI = Toplayici()


# ----------------------------------------------------------------- raporlar
def okutmalar(con, bas, bit, kisi=None):
    q = "SELECT kisi_no, zaman FROM okutma WHERE zaman >= ? AND zaman <= ?"
    a = [bas + " 00:00", bit + " 23:59"]
    if kisi:
        q += " AND kisi_no = ?"
        a.append(int(kisi))
    return con.execute(q + " ORDER BY kisi_no, zaman", a).fetchall()


def ayikla(zamanlar):
    """Esik dakika icindeki tekrar okutmalari tek sayar."""
    esik = CFG["tekrar_esik_dk"]
    out = []
    for z in zamanlar:
        t = dt.datetime.strptime(z, "%Y-%m-%d %H:%M")
        if not out or (t - out[-1]).total_seconds() >= esik * 60:
            out.append(t)
    return out


# ----------------------------------------------------------------- mesai saatleri
# Oncelik: personel ozel saati > kategori saati > genel mesai. Her kademede cumartesi ayri olabilir.
_SAAT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SAAT_ALANLARI = ("giris", "cikis", "cts_giris", "cts_cikis")


def saat_temiz(v):
    v = (v or "").strip()[:5]
    if v and not _SAAT_RE.match(v):
        raise ValueError(f"Geçersiz saat: {v} (SS:DD olmalı)")
    return v or None


def genel_ayar(con):
    g = {"giris": CFG["mesai_baslangic"], "cikis": CFG["mesai_bitis"],
         "cts_giris": CFG["cumartesi_mesai_baslangic"], "cts_cikis": CFG["cumartesi_mesai_bitis"],
         "gec_tolerans_dk": CFG["gec_tolerans_dk"], "erken_tolerans_dk": CFG["erken_tolerans_dk"]}
    for a, d in con.execute("SELECT anahtar, deger FROM ayar"):
        if a in g:
            g[a] = int(d) if a.endswith("_dk") else d
    return g


class MesaiCozucu:
    def __init__(self, con):
        self.genel = genel_ayar(con)
        self.kat = {r["id"]: dict(r) for r in con.execute("SELECT * FROM kategori")}
        self.per = {r["kisi_no"]: dict(r) for r in con.execute("SELECT * FROM personel_ayar")}
        self.izinler = {(r["kisi_no"], r["tarih"]): dict(r) for r in con.execute("SELECT * FROM izin")}

    def izin(self, k, gun):
        return self.izinler.get((k, gun if isinstance(gun, str) else gun.isoformat()))

    def tam_izinli(self, k, gun):
        iz = self.izin(k, gun)
        return bool(iz and not iz["bas_saat"])

    def mesai(self, k, gun):
        """Izin dikkate alinmis mesai: (giris, cikis, kaynak). Sabah izni girisi, aksam izni cikisi kaydirir."""
        g, c, kaynak = self.al(k, gun)
        iz = self.izin(k, gun)
        if iz and iz["bas_saat"] and iz["bit_saat"]:
            bs, bt = dk(iz["bas_saat"]), dk(iz["bit_saat"])
            if bs <= dk(g) < bt:
                g = iz["bit_saat"]
            if c and bs < dk(c) <= bt:
                c = iz["bas_saat"]
        return g, c, kaynak

    def kategori_adi(self, k):
        return (self.kat.get((self.per.get(k) or {}).get("kategori_id")) or {}).get("ad", "")

    def al(self, k, gun):
        """-> (giris, cikis, kaynak). cikis bos olabilir."""
        cts = gun.weekday() == 5
        pa = self.per.get(k) or {}
        kat = self.kat.get(pa.get("kategori_id")) or {}
        for kaynak, d in (("Özel", pa), (kat.get("ad", ""), kat), ("Genel", self.genel)):
            if cts and d.get("cts_giris"):
                return d["cts_giris"], d.get("cts_cikis") or d.get("cikis") or "", kaynak
            if d.get("giris"):
                return d["giris"], d.get("cikis") or "", kaynak
        return "09:00", "", "Genel"


IZIN_TURLERI = ["Yıllık izin", "Raporlu", "Mazeret", "Ücretsiz", "Diğer"]


def izin_yazi(iz):
    if not iz:
        return ""
    tur = iz.get("tur") or "İzin"
    return f'{tur} {iz["bas_saat"]}-{iz["bit_saat"]}' if iz.get("bas_saat") else f"{tur} (tam gün)"


def gun_ozeti(gun, ts, bugun, mesai, genel):
    """ts: ayiklanmis okutma zamanlari (bos olamaz). mesai: (giris, cikis, kaynak)"""
    m_giris, m_cikis, m_kaynak = mesai
    giris = ts[0]
    cikis = ts[-1] if len(ts) > 1 else None
    icerde = gun == bugun and len(ts) % 2 == 1
    if icerde:
        cikis = None
    gec = (giris.hour * 60 + giris.minute) - dk(m_giris)
    gec_dk = gec if gec > genel["gec_tolerans_dk"] else 0
    erken_dk = 0
    if cikis and m_cikis:
        e = dk(m_cikis) - (cikis.hour * 60 + cikis.minute)
        erken_dk = e if e > genel["erken_tolerans_dk"] else 0
    if icerde:
        durum = "İÇERİDE"
    elif cikis is None:
        durum = "ÇIKIŞ YOK"
    else:
        durum = "OK"
    sure, sure_dk = "", 0
    if cikis:
        sure_dk = int((cikis - giris).total_seconds() // 60)
        sure = sure_yaz(sure_dk)
    return {"giris": giris.strftime("%H:%M"), "cikis": cikis.strftime("%H:%M") if cikis else "",
            "sure": sure, "sure_dk": sure_dk, "okutma": len(ts), "gec_dk": gec_dk, "erken_dk": erken_dk, "durum": durum,
            "mesai": f"{m_giris}-{m_cikis}" if m_cikis else m_giris, "mesai_kaynak": m_kaynak}


def gunluk_rapor(bas, bit, kisi=None, durum_filtre=None):
    bugun = simdi().date()
    with db() as con:
        per = personel_sozluk(con)
        mc = MesaiCozucu(con)
        gruplar = {}
        for r in okutmalar(con, bas, bit, kisi):
            gruplar.setdefault((r["kisi_no"], r["zaman"][:10]), []).append(r["zaman"])
    satirlar = []
    for (k, g), zl in gruplar.items():
        gun = dt.date.fromisoformat(g)
        o = gun_ozeti(gun, ayikla(zl), bugun, mc.mesai(k, gun), mc.genel)
        p = per.get(k)
        o.update({"tarih": g, "kisi_no": k, "ad": ad_soyad(p, k), "sicil": p["sicilno"] if p else "",
                  "kategori": mc.kategori_adi(k), "izin": izin_yazi(mc.izin(k, gun))})
        if mc.tam_izinli(k, gun):  # tam gun izinli: okutsa da geç/erken/çıkış yok sayilmaz
            o["gec_dk"] = o["erken_dk"] = 0
            if o["durum"] == "ÇIKIŞ YOK":
                o["durum"] = "OK"
        satirlar.append(o)
    if durum_filtre == "GEC":
        satirlar = [s for s in satirlar if s["gec_dk"]]
    elif durum_filtre == "ERKEN":
        satirlar = [s for s in satirlar if s["erken_dk"]]
    elif durum_filtre:
        satirlar = [s for s in satirlar if s["durum"] == durum_filtre]
    satirlar.sort(key=lambda s: (s["tarih"], s["giris"]), reverse=False)
    satirlar.sort(key=lambda s: s["tarih"], reverse=True)
    return satirlar


def ham_kayitlar(bas, bit, kisi=None):
    with db() as con:
        per = personel_sozluk(con)
        rows = okutmalar(con, bas, bit, kisi)
    out = [{"zaman": r["zaman"], "kisi_no": r["kisi_no"], "ad": ad_soyad(per.get(r["kisi_no"]), r["kisi_no"])}
           for r in rows]
    out.sort(key=lambda s: s["zaman"], reverse=True)
    return out


def son_turlar(n=1, sadece_ok=False):
    with db() as con:
        q = "SELECT * FROM tur_log" + (" WHERE durum='OK'" if sadece_ok else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in con.execute(q, (n,))]


def durum_bilgisi():
    with db() as con:
        toplam = con.execute("SELECT COUNT(*) FROM okutma").fetchone()[0]
        per = con.execute("SELECT COUNT(*) FROM personel").fetchone()[0]
        ilk_son = con.execute("SELECT MIN(zaman), MAX(zaman) FROM okutma").fetchone()
    son = son_turlar(1)
    son_ok = son_turlar(1, True)
    uyari = ""
    if not son_ok:
        uyari = "Henüz başarılı okuma yok."
    else:
        fark = (simdi() - dt.datetime.strptime(son_ok[0]["zaman"], "%Y-%m-%d %H:%M:%S")).total_seconds()
        if fark > CFG["toplama_aralik_sn"] * 3 + 60:
            uyari = f"Son başarılı okuma {int(fark // 60)} dk önce."
    return {"simdi": simdi().strftime("%Y-%m-%d %H:%M:%S"), "toplam_okutma": toplam, "personel": per,
            "ilk_kayit": ilk_son[0], "son_kayit": ilk_son[1], "son_tur": son[0] if son else None,
            "son_basarili_tur": son_ok[0] if son_ok else None, "uyari": uyari,
            "turlar": son_turlar(30), "surum": SURUM, "aralik_sn": CFG["toplama_aralik_sn"],
            "sifre_gerekli": bool(CFG["yonetim_sifresi"])}


def bugun_ozeti():
    an = simdi()
    bugun = an.date()
    g = bugun.isoformat()
    satirlar = gunluk_rapor(g, g)
    satirlar.sort(key=lambda s: s["giris"])
    gelen = {s["kisi_no"] for s in satirlar}
    # Gelmeyen: son 14 gunde calismis, bugun calisma gunu, mesai+tolerans gecmis ve okutma yok
    gelmeyen = []
    if bugun.weekday() in CFG["calisma_gunleri"]:
        with db() as con:
            mc = MesaiCozucu(con)
            per = personel_sozluk(con)
            son = (bugun - dt.timedelta(days=14)).isoformat()
            aktif = [r[0] for r in con.execute(
                "SELECT DISTINCT kisi_no FROM okutma WHERE zaman >= ? AND zaman < ?", (son, g))]
        simdi_dk = an.hour * 60 + an.minute
        for k in aktif:
            if k in gelen or mc.tam_izinli(k, bugun):
                continue
            m = mc.mesai(k, bugun)
            if simdi_dk > dk(m[0]) + mc.genel["gec_tolerans_dk"]:
                gelmeyen.append({"kisi_no": k, "ad": ad_soyad(per.get(k), k), "kategori": mc.kategori_adi(k),
                                 "mesai": f"{m[0]}-{m[1]}" if m[1] else m[0], "durum": "GELMEDİ"})
        gelmeyen.sort(key=lambda x: x["ad"])
    with db() as con:
        per = personel_sozluk(con)
        izinli = [dict(kisi_no=r["kisi_no"], ad=ad_soyad(per.get(r["kisi_no"]), r["kisi_no"]), izin=izin_yazi(dict(r)),
                       aciklama=r["aciklama"] or "", geldi=r["kisi_no"] in gelen)
                  for r in con.execute("SELECT * FROM izin WHERE tarih=?", (g,))]
    izinli.sort(key=lambda x: x["ad"])
    return {"tarih": g, "satirlar": satirlar, "gelmeyen": gelmeyen, "izinli": izinli,
            "icerde": [s for s in satirlar if s["durum"] == "İÇERİDE"],
            "gec": [s for s in satirlar if s["gec_dk"]],
            "erken": [s for s in satirlar if s["erken_dk"]],
            "nobet": nobet_durumu(g, g)}


# ----------------------------------------------------------------- personel bazli ozet
def _beklenen_gunler(con, bas, bit):
    """Calisma gunu olup sistemde verisi olan (o gun en az bir kisi okutmus) gecmis gunler."""
    bugun = simdi().date().isoformat()
    veri = {r[0] for r in con.execute(
        "SELECT DISTINCT substr(zaman,1,10) FROM okutma WHERE zaman >= ? AND zaman <= ?", (bas + " 00:00", bit + " 23:59"))}
    return {g for g in veri if g < bugun and dt.date.fromisoformat(g).weekday() in CFG["calisma_gunleri"]}


def _istatistik(satirlar, beklenen, ilk_gun, nobet_gunleri, veri_gunleri, izinler=()):
    gelinen = {s["tarih"] for s in satirlar}
    tam_izin = {iz["tarih"] for iz in izinler if not iz["bas_saat"]}
    bekl = {g for g in beklenen if g >= ilk_gun}
    gelmedi = sorted(bekl - gelinen - tam_izin)
    bugun = simdi().date().isoformat()
    nobet_gelmedi = sorted(g for g in nobet_gunleri
                           if g < bugun and g in veri_gunleri and g not in gelinen and g not in tam_izin)
    girisler = [dk(s["giris"]) for s in satirlar]
    ort = sum(girisler) // len(girisler) if girisler else None
    calisma = sum(s["sure_dk"] for s in satirlar)
    return {"gun": len(gelinen), "gec": sum(1 for s in satirlar if s["gec_dk"]),
            "gec_dk": sum(s["gec_dk"] for s in satirlar),
            "erken": sum(1 for s in satirlar if s["erken_dk"]), "erken_dk": sum(s["erken_dk"] for s in satirlar),
            "cikis_yok": sum(1 for s in satirlar if s["durum"] == "ÇIKIŞ YOK"),
            "gelmedi": len(gelmedi), "gelmedi_gunler": gelmedi,
            "nobet": len(nobet_gunleri), "nobet_gelmedi": len(nobet_gelmedi), "nobet_gelmedi_gunler": nobet_gelmedi,
            "ort_giris": f"{ort // 60:02d}:{ort % 60:02d}" if ort is not None else "",
            "calisma_dk": calisma, "calisma": sure_yaz(calisma) or "0 dakika",
            "hatali": sum(1 for s in satirlar if s["gec_dk"] or s["erken_dk"] or s["durum"] == "ÇIKIŞ YOK"),
            "izinli_gun": len(tam_izin), "saatlik_izin": sum(1 for iz in izinler if iz["bas_saat"])}


def personel_ozeti(bas, bit, kisi=None):
    satirlar = gunluk_rapor(bas, bit, kisi)
    with db() as con:
        mc = MesaiCozucu(con)
        beklenen = _beklenen_gunler(con, bas, bit)
        veri_gunleri = {r[0] for r in con.execute(
            "SELECT DISTINCT substr(zaman,1,10) FROM okutma WHERE zaman >= ? AND zaman <= ?", (bas + " 00:00", bit + " 23:59"))}
        ilk = {r[0]: r[1][:10] for r in con.execute("SELECT kisi_no, MIN(zaman) FROM okutma GROUP BY kisi_no")}
        nobet = {}
        for r in con.execute("SELECT tarih, kisi_no FROM nobet WHERE tarih >= ? AND tarih <= ?", (bas, bit)):
            nobet.setdefault(r[1], []).append(r[0])
        izinler = {}
        for r in con.execute("SELECT * FROM izin WHERE tarih >= ? AND tarih <= ?", (bas, bit)):
            izinler.setdefault(r["kisi_no"], []).append(dict(r))
    kisiye = {}
    for s in satirlar:
        kisiye.setdefault(s["kisi_no"], []).append(s)
    if kisi:
        kisiye.setdefault(int(kisi), [])
    out = []
    for k, rows in kisiye.items():
        st = _istatistik(rows, beklenen, ilk.get(k, "9999"), nobet.get(k, []), veri_gunleri, izinler.get(k, []))
        st.update({"kisi_no": k, "ad": rows[0]["ad"] if rows else "", "kategori": mc.kategori_adi(k)})
        out.append(st)
    out.sort(key=lambda x: x["ad"])
    return out


def trend(gun_sayisi=14):
    """Son N gunun gunluk sayilari (grafik icin)."""
    bit = simdi().date()
    bas = bit - dt.timedelta(days=gun_sayisi - 1)
    gunluk = {}
    for s in gunluk_rapor(bas.isoformat(), bit.isoformat()):
        d = gunluk.setdefault(s["tarih"], {"gelen": 0, "gec": 0, "erken": 0, "cikis_yok": 0})
        d["gelen"] += 1
        d["gec"] += 1 if s["gec_dk"] else 0
        d["erken"] += 1 if s["erken_dk"] else 0
        d["cikis_yok"] += 1 if s["durum"] == "ÇIKIŞ YOK" else 0
    out = []
    g = bas
    while g <= bit:
        out.append(dict({"tarih": g.isoformat(), "gelen": 0, "gec": 0, "erken": 0, "cikis_yok": 0},
                        **gunluk.get(g.isoformat(), {})))
        g += dt.timedelta(days=1)
    return out


def personel_detay(kisi, bas, bit):
    k = int(kisi)
    ozet = personel_ozeti(bas, bit, k)[0]
    gunler = {s["tarih"]: s for s in gunluk_rapor(bas, bit, k)}
    with db() as con:
        per = personel_sozluk(con)
        mc = MesaiCozucu(con)
        son = con.execute("SELECT MAX(zaman) FROM okutma WHERE kisi_no=?", (k,)).fetchone()[0]
        veri_gunleri = {r[0] for r in con.execute(
            "SELECT DISTINCT substr(zaman,1,10) FROM okutma WHERE zaman >= ? AND zaman <= ?", (bas + " 00:00", bit + " 23:59"))}
    p = per.get(k)
    ozet["ad"] = ad_soyad(p, k)
    bugun = simdi().date()
    gelmedi, nobet_gelmedi = set(ozet["gelmedi_gunler"]), set(ozet["nobet_gelmedi_gunler"])
    takvim = []
    g = dt.date.fromisoformat(bas)
    while g <= dt.date.fromisoformat(bit):
        t = g.isoformat()
        s = gunler.get(t)
        if mc.tam_izinli(k, t) and not (s and s["durum"] == "İÇERİDE"):
            d = "izinli"
        elif s:
            if s["durum"] == "İÇERİDE":
                d = "icerde"
            elif s["durum"] == "ÇIKIŞ YOK":
                d = "cikis_yok"
            elif s["gec_dk"]:
                d = "gec"
            elif s["erken_dk"]:
                d = "erken"
            else:
                d = "zamaninda"
        elif t in nobet_gelmedi or t in gelmedi:
            d = "gelmedi"
        elif g > bugun:
            d = "gelecek"
        elif t not in veri_gunleri:
            d = "veri_yok"
        else:
            d = "bos"
        takvim.append({"tarih": t, "durum": d, "giris": s["giris"] if s else "", "cikis": s["cikis"] if s else "",
                       "gec_dk": s["gec_dk"] if s else 0, "erken_dk": s["erken_dk"] if s else 0,
                       "izin": izin_yazi(mc.izin(k, t))})
        g += dt.timedelta(days=1)
    hi, cts = mc.al(k, dt.date(2024, 1, 1)), mc.al(k, dt.date(2024, 1, 6))
    return {"kisi": {"kisi_no": k, "ad": ad_soyad(p, k), "sicil": p["sicilno"] if p else "",
                     "kategori": mc.kategori_adi(k), "son_okutma": son or "",
                     "mesai": f"{hi[0]}-{hi[1]} ({hi[2]})", "mesai_cts": f"{cts[0]}-{cts[1]} ({cts[2]})"},
            "ozet": ozet, "takvim": takvim, "izinler": izin_listesi(bas, bit, k),
            "gunler": sorted(gunler.values(), key=lambda s: s["tarih"], reverse=True)}


# ----------------------------------------------------------------- izinler
def izin_listesi(bas, bit, kisi=None):
    """Ayni seferde girilen gunler (grup) tek satirda: {grup, kisi, bas_tarih, bit_tarih, gun, ...}"""
    q, a = "SELECT * FROM izin WHERE tarih >= ? AND tarih <= ?", [bas, bit]
    if kisi:
        q += " AND kisi_no = ?"
        a.append(int(kisi))
    with db() as con:
        per = personel_sozluk(con)
        rows = [dict(r) for r in con.execute(q + " ORDER BY tarih", a)]
    gruplar = {}
    for r in rows:
        g = gruplar.setdefault(r["grup"] or f"t{r['id']}", dict(
            grup=r["grup"] or f"t{r['id']}", ids=[], kisi_no=r["kisi_no"], ad=ad_soyad(per.get(r["kisi_no"]), r["kisi_no"]),
            bas_tarih=r["tarih"], bit_tarih=r["tarih"], bas_saat=r["bas_saat"] or "", bit_saat=r["bit_saat"] or "",
            tur=r["tur"] or "", aciklama=r["aciklama"] or "", olusturma=r["olusturma"] or ""))
        g["ids"].append(r["id"])
        g["bit_tarih"] = max(g["bit_tarih"], r["tarih"])
    out = list(gruplar.values())
    for g in out:
        g["gun"] = len(g["ids"])
        g["yazi"] = izin_yazi(g)
    out.sort(key=lambda g: (g["bas_tarih"], g["ad"]), reverse=True)
    return out


def izin_ekle(js):
    k = int(js["kisi_no"])
    b = dt.date.fromisoformat(js["bas_tarih"])
    e = dt.date.fromisoformat(js.get("bit_tarih") or js["bas_tarih"])
    if e < b:
        b, e = e, b
    if (e - b).days > 366:
        raise ValueError("En fazla 1 yıllık aralık girilebilir")
    bs, bt = saat_temiz(js.get("bas_saat")), saat_temiz(js.get("bit_saat"))
    if bool(bs) != bool(bt):
        raise ValueError("Saatlik izin için başlangıç ve bitiş saatini birlikte girin")
    if bs and dk(bs) >= dk(bt):
        raise ValueError("Bitiş saati başlangıçtan sonra olmalı")
    tur = str(js.get("tur") or "Diğer")[:30]
    aciklama = str(js.get("aciklama") or "").strip()[:200]
    grup = f"{k}-{int(time.time() * 1000)}"
    with db() as con:
        if not con.execute("SELECT 1 FROM personel WHERE kisi_no=?", (k,)).fetchone():
            raise ValueError("Personel bulunamadı")
        n, g = 0, b
        while g <= e:
            # Pazar ve calisma gunu disi gunler aralikta atlanir (tek gun secildiyse yine yazilir)
            if b == e or g.weekday() in CFG["calisma_gunleri"] or g.weekday() == 5:
                con.execute("INSERT INTO izin(kisi_no,tarih,bas_saat,bit_saat,tur,aciklama,grup,olusturma) "
                            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(kisi_no,tarih) DO UPDATE SET bas_saat=excluded.bas_saat, "
                            "bit_saat=excluded.bit_saat, tur=excluded.tur, aciklama=excluded.aciklama, grup=excluded.grup, "
                            "olusturma=excluded.olusturma",
                            (k, g.isoformat(), bs, bt, tur, aciklama, grup, zstr(simdi())))
                n += 1
            g += dt.timedelta(days=1)
    log.info("Izin eklendi: kisi=%s %s..%s %s %s", k, b, e, tur, f"{bs}-{bt}" if bs else "tam gun")
    return {"gun": n}


def izin_sil(js):
    ids = [int(i) for i in js.get("ids") or []]
    with db() as con:
        con.executemany("DELETE FROM izin WHERE id=?", [(i,) for i in ids])
    return {"silinen": len(ids)}


# ----------------------------------------------------------------- nobet: dosya okuma
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_TARIH_FMT_ID = set(range(14, 23)) | {45, 46, 47}


def _hucre_ref(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref)
    c = 0
    for ch in m.group(1):
        c = c * 26 + ord(ch) - 64
    return int(m.group(2)), c


def _sutun_adi(c):
    s = ""
    while c:
        c, r = divmod(c - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx_oku(veri):
    """-> [(sayfa_adi, {(satir, sutun): str | dt.date | float})]"""
    z = zipfile.ZipFile(io.BytesIO(veri))
    if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
        raise ValueError("Dosya çok büyük")
    adlar = set(z.namelist())

    def xml(ad):
        return ET.fromstring(z.read(ad))

    paylasimli = []
    if "xl/sharedStrings.xml" in adlar:
        for si in xml("xl/sharedStrings.xml"):
            paylasimli.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    tarih_stil = set()
    if "xl/styles.xml" in adlar:
        st = xml("xl/styles.xml")
        ozel = {}
        nf = st.find(_NS + "numFmts")
        if nf is not None:
            for f in nf:
                kod = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", f.get("formatCode", "").lower())
                ozel[int(f.get("numFmtId"))] = ("d" in kod or "y" in kod or "mmm" in kod)
        xfs = st.find(_NS + "cellXfs")
        if xfs is not None:
            for i, xf in enumerate(xfs):
                fid = int(xf.get("numFmtId", 0))
                if fid in _TARIH_FMT_ID or ozel.get(fid):
                    tarih_stil.add(i)
    wb = xml("xl/workbook.xml")
    pr = wb.find(_NS + "workbookPr")
    taban = dt.date(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") else dt.date(1899, 12, 30)
    rels = {r.get("Id"): r.get("Target") for r in xml("xl/_rels/workbook.xml.rels")}
    sonuc = []
    for sh in wb.find(_NS + "sheets"):
        hedef = rels.get(sh.get(_RNS + "id"), "")
        yol = hedef.lstrip("/") if hedef.startswith("/") else "xl/" + hedef
        if yol not in adlar:
            continue
        ws = xml(yol)
        hucreler = {}
        for c in ws.iter(_NS + "c"):
            if not c.get("r"):
                continue
            rc = _hucre_ref(c.get("r"))
            t = c.get("t")
            v = c.find(_NS + "v")
            if t == "inlineStr":
                deger = "".join(x.text or "" for x in c.iter(_NS + "t"))
            elif v is None or v.text is None:
                continue
            elif t == "s":
                deger = paylasimli[int(v.text)]
            elif t in ("str", "e"):
                deger = v.text
            elif t == "b":
                continue
            else:
                try:
                    n = float(v.text)
                except ValueError:
                    continue
                if int(c.get("s", 0)) in tarih_stil and 20000 < n < 80000:
                    deger = taban + dt.timedelta(days=int(n))
                else:
                    deger = n
            if isinstance(deger, str) and not deger.strip():
                continue
            hucreler[rc] = deger
        mc = ws.find(_NS + "mergeCells")
        if mc is not None:  # birlesik hucre degerini tum araliga yay
            for m in mc:
                a, _, b = m.get("ref", "").partition(":")
                if not b:
                    continue
                (r1, c1), (r2, c2) = _hucre_ref(a), _hucre_ref(b)
                if (r1, c1) in hucreler and (r2 - r1 + 1) * (c2 - c1 + 1) <= 400:
                    for r in range(r1, r2 + 1):
                        for c in range(c1, c2 + 1):
                            hucreler.setdefault((r, c), hucreler[(r1, c1)])
        sonuc.append((sh.get("name"), hucreler))
    return sonuc


def csv_oku(veri):
    for kod in ("utf-8-sig", "cp1254"):
        try:
            metin = veri.decode(kod)
            break
        except UnicodeDecodeError:
            continue
    ayr = ";" if metin.count(";") >= metin.count(",") else ","
    hucreler = {}
    for r, satir in enumerate(csv.reader(io.StringIO(metin), delimiter=ayr), 1):
        for c, v in enumerate(satir, 1):
            if v.strip():
                hucreler[(r, c)] = v
    return [("CSV", hucreler)]


# ----------------------------------------------------------------- nobet: cozumleme
_AYLAR = {"OCAK": 1, "SUBAT": 2, "MART": 3, "NISAN": 4, "MAYIS": 5, "HAZIRAN": 6, "TEMMUZ": 7,
          "AGUSTOS": 8, "EYLUL": 9, "EKIM": 10, "KASIM": 11, "ARALIK": 12}
_DURAK = set(_AYLAR) | {
    "PAZARTESI", "SALI", "CARSAMBA", "PERSEMBE", "CUMA", "CUMARTESI", "PAZAR", "CTS", "CMT", "NOBET", "NOBETCI",
    "NOBETCILER", "NOBETI", "LISTE", "LISTESI", "CIZELGE", "CIZELGESI", "TARIH", "TARIHI", "GUN", "GUNU",
    "PERSONEL", "PERSONELLER", "AD", "ADI", "SOYAD", "SOYADI", "ADSOYAD", "ISIM", "KART", "KARTNO", "SICIL",
    "NO", "HAFTA", "HAFTASI", "AY", "AYI", "YIL", "IZIN", "IZINLI", "IZINLILER", "MAGAZA", "FABE", "VE",
    "CALISACAK", "CALISACAKLAR", "CALISAN", "CALISANLAR", "MESAI", "SAAT", "NOT", "ACIKLAMA", "YEDEK",
    "SORUMLU", "OFF", "TATIL", "DEPO", "KASA", "SATIS", "REYON", "MUDUR", "SEF", "BOLUM", "GOREV", "VARDIYA",
    "VEYA", "SABAH", "AKSAM", "OGLE", "TAM", "YARIM", "GUNLUK", "AYLIK", "HAFTALIK", "PROGRAM", "PROGRAMI", "PLAN",
    "SATICI", "SATICILAR", "MANKEN", "MANKENLER", "TEKSTIL", "KAT", "ZEMIN", "STUDYO"}
_GUN_KELIME = {"PAZARTESI", "SALI", "CARSAMBA", "PERSEMBE", "CUMA", "CUMARTESI", "PAZAR", "CTS", "CMT"}


def _yil_tahmin(ay):
    b = simdi().date()
    y = b.year
    if ay - b.month > 6:
        y -= 1
    elif b.month - ay > 6:
        y += 1
    return y


def _tarih_yap(y, m, d):
    try:
        if y < 100:
            y += 2000
        return dt.date(y, m, d)
    except ValueError:
        return None


def metinden_tarih(s):
    """Hucre metninde tarih arar -> (tarih|None, kalan_metin)."""
    k = katla(s.replace(".", "/").replace("-", "/"))
    ham = s
    desen = [
        (r"(\d{4})/(\d{1,2})/(\d{1,2})", lambda g: _tarih_yap(int(g[0]), int(g[1]), int(g[2]))),
        (r"(\d{1,2})/(\d{1,2})/(\d{2,4})", lambda g: _tarih_yap(int(g[2]), int(g[1]), int(g[0]))),
        (r"(\d{1,2}) (" + "|".join(_AYLAR) + r")(?: (\d{4}))?",
         lambda g: _tarih_yap(int(g[2]) if g[2] else _yil_tahmin(_AYLAR[g[1]]), _AYLAR[g[1]], int(g[0]))),
    ]
    # katla() '/' isaretini bosluga cevirir; tarih desenleri icin ayri bir anahtar kullan
    k2 = tr_upper(ham).translate(_KATLA).replace(".", "/").replace("-", "/")
    k2 = " ".join(re.sub(r"[^A-Z0-9/ ]+", " ", k2).split())
    for d, f in desen:
        m = re.search(r"(?<!\d)" + d + r"(?!\d)", k2)
        if m:
            t = f(m.groups())
            if t:
                kalan = (k2[:m.start()] + " " + k2[m.end():]).replace("/", " ")
                return t, kalan
    # yilsiz gun/ay: 07/11
    m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?![\d/])", k2)
    if m:
        t = _tarih_yap(_yil_tahmin(int(m.group(2))), int(m.group(2)), int(m.group(1)))
        if t:
            return t, (k2[:m.start()] + " " + k2[m.end():]).replace("/", " ")
    return None, k


_KAT_EKI = re.compile(r"\s*-\s*[A-ZÇĞİÖŞÜ0-9]{1,2}\s*$")


def _isim_parcalari(metin):
    """-> [(gosterim, ad_anahtari, tam_anahtar)].
    'NASTYA (DAL)-Z' -> ad 'NASTYA', tam 'NASTYA DAL': parantez ici (marka/reyon) ve -Z/-1 (kat) eki
    addan ayiklanir; tam anahtar ayni ilk ada sahip kisileri ayirt etmek icin hatirlanir."""
    out = []
    for parca in re.split(r"[,;/\n+&]| VE ", " " + tr_upper(str(metin)) + " "):
        gos = " ".join(parca.split())
        if not gos:
            continue
        not_ = katla(" ".join(re.findall(r"\(([^)]*)\)", gos)))
        govde = _KAT_EKI.sub("", re.sub(r"\([^)]*\)", " ", gos).strip())
        isimler = re.split(r"\s+-\s+", govde)
        for isim in isimler:
            kel = [w for w in katla(isim).split() if w not in _DURAK and not re.fullmatch(r"\d{1,2}", w)]
            if not kel or (len(kel) == 1 and len(kel[0]) == 1):
                continue
            ad = " ".join(kel)
            out.append((gos if len(isimler) == 1 else isim.strip(), ad, f"{ad} {not_}".strip()))
    return out


class Eslestirici:
    def __init__(self):
        with db() as con:
            per = list(con.execute("SELECT * FROM personel"))
            son = (simdi() - dt.timedelta(days=60)).strftime("%Y-%m-%d")
            self.aktif = {r[0] for r in con.execute("SELECT DISTINCT kisi_no FROM okutma WHERE zaman >= ?", (son,))}
            self.hafiza = {a: k for a, k in con.execute("SELECT anahtar, kisi_no FROM nobet_eslesme")}
        self.per = {r["kisi_no"]: r for r in per}
        self.tam, self.bas_harf, self.ilk = {}, {}, {}
        for r in per:
            a, s = katla(r["adi"]), katla(r["soyadi"])
            if not a:
                continue
            for anahtar in {f"{a} {s}".strip(), f"{s} {a}".strip()}:
                self.tam.setdefault(anahtar, set()).add(r["kisi_no"])
            if s:
                self.bas_harf.setdefault(f"{a} {s[0]}", set()).add(r["kisi_no"])
                self.bas_harf.setdefault(f"{a.split()[0]} {s}", set()).add(r["kisi_no"])
            self.ilk.setdefault(a.split()[0], set()).add(r["kisi_no"])
            self.ilk.setdefault(a, set()).add(r["kisi_no"])

    def _sec(self, aday):
        aday = set(aday)
        if len(aday) > 1:
            akt = aday & self.aktif
            if len(akt) == 1:
                return akt, True
        return aday, len(aday) == 1

    def bul(self, anahtar, tam_anahtar=None):
        """-> (kisi_no|None, durum, adaylar)"""
        # Daha once onizlemede onaylanmis eslesme ("NASTYA DAL" -> 123)
        k = self.hafiza.get(tam_anahtar or anahtar)
        if k in self.per:
            return k, "kayıtlı", [k]
        k = self.hafiza.get(anahtar)
        if k in self.per:  # sadece ilk ad hatirlaniyor: kontrol edilsin
            return k, "tahmini", [k]
        if anahtar.isdigit():
            k = int(anahtar)
            return (k, "tam", [k]) if k in self.per else (None, "yok", [])
        for sozluk, seviye in ((self.tam, "tam"), (self.bas_harf, "tahmini"), (self.ilk, "tahmini")):
            if anahtar in sozluk:
                aday, tek = self._sec(sozluk[anahtar])
                if tek:
                    return next(iter(aday)), seviye, list(aday)
                return None, "belirsiz", sorted(aday)
        # son care: tum kelimeleri iceren tek kisi
        kel = anahtar.split()
        if len(kel) >= 2:
            aday = [k for k, r in self.per.items()
                    if all(w in katla(f'{r["adi"]} {r["soyadi"]}').split() for w in kel)]
            if aday:
                aday, tek = self._sec(aday)
                if tek:
                    return next(iter(aday)), "tahmini", list(aday)
                return None, "belirsiz", sorted(aday)
        return None, "yok", []

    def ad(self, k):
        return ad_soyad(self.per.get(k), k)


def nobet_cozumle(dosya_adi, veri):
    if veri[:2] == b"PK":
        sayfalar = xlsx_oku(veri)
    elif veri[:4] == b"\xd0\xcf\x11\xe0":
        raise ValueError("Eski .xls biçimi okunamıyor. Excel'de 'Farklı Kaydet → Excel Çalışma Kitabı (.xlsx)' yapın.")
    else:
        sayfalar = csv_oku(veri)
    es = Eslestirici()
    girdiler, uyarilar = [], []
    for sayfa, h in sayfalar:
        tarihler, adaylar = {}, {}
        for rc, v in h.items():
            if isinstance(v, dt.date):
                tarihler[rc] = v
                continue
            if isinstance(v, float):
                if v.is_integer() and int(v) in es.per:
                    adaylar[rc] = [(str(int(v)), str(int(v)), str(int(v)))]
                continue
            t, kalan = metinden_tarih(v)
            if t:
                tarihler[rc] = t
                parca = _isim_parcalari(kalan)
                if parca:  # "07.11 Ali, Veli" gibi ayni hucrede tarih + isim
                    adaylar[rc] = parca
                continue
            parca = _isim_parcalari(v)
            if parca:
                adaylar[rc] = parca
        if not tarihler or not adaylar:
            continue
        # Yon tespiti: isimler tarihin sagina mi (satir) altina mi (sutun) diziliyor?
        def sol(rc):
            c = [cc for (rr, cc) in tarihler if rr == rc[0] and cc < rc[1]]
            return (rc[0], max(c)) if c else None

        def ust(rc):
            r = [rr for (rr, cc) in tarihler if cc == rc[1] and rr < rc[0]]
            return (max(r), rc[1]) if r else None

        satir_puan = sum(1 for rc in adaylar if rc not in tarihler and sol(rc))
        sutun_puan = sum(1 for rc in adaylar if rc not in tarihler and ust(rc))
        bul = sol if satir_puan > sutun_puan else ust
        for rc, parcalar in adaylar.items():
            hedef = rc if rc in tarihler else bul(rc)
            if not hedef:
                continue
            tarih = tarihler[hedef]
            for metin, anahtar, tam in parcalar:
                k, seviye, aday = es.bul(anahtar, tam)
                if k is None and anahtar.replace(" ", "").isdigit():
                    continue  # yil, adet vb. sayi; personel degil
                girdiler.append({"tarih": tarih.isoformat(), "metin": metin, "anahtar": anahtar, "tam_anahtar": tam,
                                 "kisi_no": k, "ad": es.ad(k) if k else "",
                                 "durum": seviye, "adaylar": [{"kisi_no": a, "ad": es.ad(a)} for a in aday[:8]],
                                 "hucre": f"{sayfa}!{_sutun_adi(rc[1])}{rc[0]}", "_sira": (sayfa, rc[1], rc[0])})
    # ayni tarihte ayni yazi tekrarini birlestir (farkli yazilar ayni kisiye gidiyorsa uyar, gizleme)
    gor, tekil, kisi_yazi = set(), [], {}
    for g in girdiler:
        anahtar = (g["tarih"], g["tam_anahtar"] or g["metin"])
        if anahtar not in gor:
            gor.add(anahtar)
            tekil.append(g)
            if g["kisi_no"]:
                kisi_yazi.setdefault((g["tarih"], g["kisi_no"]), []).append(g["metin"])
    for (t, k), yazilar in sorted(kisi_yazi.items()):
        if len(yazilar) > 1:
            uyarilar.append(f"{t}: {es.ad(k)} birden fazla isimle eşleşti ({', '.join(yazilar)}). Kontrol edin.")
    tekil.sort(key=lambda g: (g["tarih"], g["_sira"]))
    for g in tekil:
        del g["_sira"]
    tarihler = sorted({g["tarih"] for g in tekil})
    for t in tarihler:
        if dt.date.fromisoformat(t).weekday() != 5:
            uyarilar.append(f"{t} cumartesi değil.")
    if not tekil:
        uyarilar.append("Dosyada tarih + personel eşleşmesi bulunamadı. Şablonu kullanmayı deneyin.")
    return {"dosya": dosya_adi, "tarihler": tarihler, "girdiler": tekil, "uyarilar": uyarilar,
            "personel": [{"kisi_no": k, "ad": es.ad(k), "aktif": k in es.aktif}
                         for k in sorted(es.per, key=lambda x: es.ad(x))]}


def nobet_kaydet(dosya, tarihler, kayitlar):
    tarihler = sorted({dt.date.fromisoformat(t).isoformat() for t in tarihler})
    temiz, hafiza = {}, []
    for r in kayitlar:
        t = dt.date.fromisoformat(r["tarih"]).isoformat()
        k = int(r["kisi_no"])
        metin = str(r.get("metin", ""))[:100]
        tam = str(r.get("tam_anahtar") or "")[:100]
        temiz[(t, k)] = (metin, tam or None)
        hafiza.append((str(r.get("anahtar") or "")[:100], tam, k, metin))
        if t not in tarihler:
            tarihler.append(t)
    with db() as con:
        # Onaylanan eslesmeleri hatirla: tam anahtar her zaman; sadece ilk ad, farkli kisilere
        # gidiyorsa NULL (cakisma) yapilir ve artik kullanilmaz.
        for ad, tam, k, metin in hafiza:
            if tam and not tam.isdigit():
                con.execute("INSERT INTO nobet_eslesme(anahtar,kisi_no,metin) VALUES(?,?,?) "
                            "ON CONFLICT(anahtar) DO UPDATE SET kisi_no=excluded.kisi_no, metin=excluded.metin",
                            (tam, k, metin))
            if ad and ad != tam and not ad.isdigit():
                eski = con.execute("SELECT kisi_no FROM nobet_eslesme WHERE anahtar=?", (ad,)).fetchone()
                if eski is None:
                    goster = _KAT_EKI.sub("", re.sub(r"\([^)]*\)", " ", tr_upper(metin)).strip()).strip() or ad
                    con.execute("INSERT INTO nobet_eslesme(anahtar,kisi_no,metin) VALUES(?,?,?)", (ad, k, goster))
                elif eski[0] is not None and eski[0] != k:
                    con.execute("UPDATE nobet_eslesme SET kisi_no=NULL WHERE anahtar=?", (ad,))
        cur = con.execute("INSERT INTO nobet_yukleme(zaman,dosya,tarih_sayisi,kayit_sayisi) VALUES(?,?,?,?)",
                          (zstr(simdi()), str(dosya)[:200], len(tarihler), len(temiz)))
        yid = cur.lastrowid
        con.executemany("DELETE FROM nobet WHERE tarih = ?", [(t,) for t in tarihler])
        con.executemany("INSERT OR IGNORE INTO nobet(tarih,kisi_no,metin,tam_anahtar,yukleme_id) VALUES(?,?,?,?,?)",
                        [(t, k, m, ta, yid) for (t, k), (m, ta) in temiz.items()])
    log.info("Nobet kaydedildi: %s, %d tarih, %d kayit", dosya, len(tarihler), len(temiz))
    return {"tarih_sayisi": len(tarihler), "kayit_sayisi": len(temiz)}


def personel_secenekleri(con):
    son = (simdi() - dt.timedelta(days=60)).strftime("%Y-%m-%d")
    aktif = {r[0] for r in con.execute("SELECT DISTINCT kisi_no FROM okutma WHERE zaman >= ?", (son,))}
    liste = [{"kisi_no": k, "ad": ad_soyad(p, k), "aktif": k in aktif} for k, p in personel_sozluk(con).items()]
    return sorted(liste, key=lambda x: x["ad"])


def _ad_kismi(anahtar_veya_metin):
    p = _isim_parcalari(anahtar_veya_metin)
    return p[0][1] if p else ""


def eslesme_listesi():
    with db() as con:
        per = personel_sozluk(con)
        say = {r[0]: r[1] for r in con.execute(
            "SELECT tam_anahtar, COUNT(*) FROM nobet WHERE tam_anahtar IS NOT NULL GROUP BY tam_anahtar")}
        hepsi = [dict(r) for r in con.execute("SELECT * FROM nobet_eslesme")]
        # "SEHRIZADE" gibi sadece-ilk-ad yedegi, ayni kisiye giden tam kayit ("ŞEHRİZADE (LP)") varsa gizlenir
        tam_kayit = {(_ad_kismi(e["metin"] or ""), e["kisi_no"]) for e in hepsi
                     if _ad_kismi(e["metin"] or "") not in ("", e["anahtar"])}
        rows = [{"anahtar": e["anahtar"], "metin": e["metin"] or e["anahtar"], "kisi_no": e["kisi_no"],
                 "ad": ad_soyad(per.get(e["kisi_no"]), e["kisi_no"]) if e["kisi_no"] else "",
                 "nobet_sayisi": say.get(e["anahtar"], 0)}
                for e in hepsi if not (e["kisi_no"] and (e["anahtar"], e["kisi_no"]) in tam_kayit)]
        rows.sort(key=lambda x: katla(x["metin"]))
        return {"eslesmeler": rows, "personel": personel_secenekleri(con)}


def eslesme_kaydet(js):
    """{anahtar?: mevcut kayit, metin?: yeni (cizelgedeki yazi), kisi_no, uygula: kayitli nobetleri de duzelt}"""
    k = int(js["kisi_no"])
    anahtar, metin = js.get("anahtar"), tr_upper(str(js.get("metin") or "").strip())[:100]
    if not anahtar:
        parca = _isim_parcalari(metin)
        if not parca:
            raise ValueError("Geçerli bir isim yazın")
        anahtar = parca[0][2]
    with db() as con:
        if not con.execute("SELECT 1 FROM personel WHERE kisi_no=?", (k,)).fetchone():
            raise ValueError("Personel bulunamadı")
        eski = con.execute("SELECT kisi_no, metin FROM nobet_eslesme WHERE anahtar=?", (anahtar,)).fetchone()
        ad = _ad_kismi((eski[1] if eski else None) or metin)
        if eski and ad and ad != anahtar:  # gizli ilk-ad yedegini de ayni kisiye tasi
            con.execute("UPDATE nobet_eslesme SET kisi_no=? WHERE anahtar=? AND kisi_no IS ?", (k, ad, eski[0]))
        con.execute("INSERT INTO nobet_eslesme(anahtar,kisi_no,metin) VALUES(?,?,?) ON CONFLICT(anahtar) DO UPDATE "
                    "SET kisi_no=excluded.kisi_no, metin=COALESCE(NULLIF(excluded.metin,''), nobet_eslesme.metin)",
                    (anahtar, k, metin or None))
        duzeltilen = 0
        if js.get("uygula"):
            # Bu yaziyla kaydedilmis nobetleri yeni kisiye tasi (o gun zaten varsa eski satir silinir)
            once = con.total_changes
            con.execute("UPDATE OR IGNORE nobet SET kisi_no=? WHERE tam_anahtar=? AND kisi_no<>?", (k, anahtar, k))
            duzeltilen = con.total_changes - once
            con.execute("DELETE FROM nobet WHERE tam_anahtar=? AND kisi_no<>?", (anahtar, k))
    return {"anahtar": anahtar, "duzeltilen": duzeltilen}


def eslesme_sil(js):
    with db() as con:
        con.execute("DELETE FROM nobet_eslesme WHERE anahtar=?", (str(js["anahtar"]),))
    return {"ok": True}


def nobet_satir(js):
    """Tek nobet kaydi ekle/sil: {tarih, kisi_no, sil?}"""
    t = dt.date.fromisoformat(js["tarih"]).isoformat()
    k = int(js["kisi_no"])
    with db() as con:
        if js.get("sil"):
            con.execute("DELETE FROM nobet WHERE tarih=? AND kisi_no=?", (t, k))
        else:
            if not con.execute("SELECT 1 FROM personel WHERE kisi_no=?", (k,)).fetchone():
                raise ValueError("Personel bulunamadı")
            con.execute("INSERT OR IGNORE INTO nobet(tarih,kisi_no,metin) VALUES(?,?,'elle')", (t, k))
    return {"ok": True}


def nobet_sil(tarihler):
    with db() as con:
        con.executemany("DELETE FROM nobet WHERE tarih = ?",
                        [(dt.date.fromisoformat(t).isoformat(),) for t in tarihler])


def nobet_durumu(bas, bit):
    """Nobetci personelin o gun gelip gelmedigi."""
    bugun, an = simdi().date(), simdi()
    with db() as con:
        per = personel_sozluk(con)
        mc = MesaiCozucu(con)
        plan = con.execute("SELECT tarih, kisi_no, metin FROM nobet WHERE tarih >= ? AND tarih <= ? ORDER BY tarih",
                           (bas, bit)).fetchall()
        if not plan:
            return []
        gunler = sorted({r["tarih"] for r in plan})
        okut = {}
        for r in con.execute("SELECT kisi_no, zaman FROM okutma WHERE zaman >= ? AND zaman <= ? ORDER BY zaman",
                             (gunler[0] + " 00:00", gunler[-1] + " 23:59")):
            okut.setdefault(r["zaman"][:10], {}).setdefault(r["kisi_no"], []).append(r["zaman"])
    planli, yazi = {}, {}
    for r in plan:
        planli.setdefault(r["tarih"], []).append(r["kisi_no"])
        yazi[(r["tarih"], r["kisi_no"])] = r["metin"] if r["metin"] not in (None, "demo", "elle") else ""
    sonuc = []
    for g in gunler:
        gun = dt.date.fromisoformat(g)
        gun_okut = okut.get(g, {})
        satirlar = []
        for k in planli[g]:
            mesai = mc.mesai(k, gun)
            bas_dk = dk(mesai[0]) + mc.genel["gec_tolerans_dk"]
            s = {"kisi_no": k, "ad": ad_soyad(per.get(k), k), "giris": "", "cikis": "", "gec_dk": 0,
                 "mesai": f"{mesai[0]}-{mesai[1]}" if mesai[1] else mesai[0], "metin": yazi.get((g, k), "")}
            if k in gun_okut:
                s.update({x: y for x, y in gun_ozeti(gun, ayikla(gun_okut[k]), bugun, mesai, mc.genel).items()
                          if x in ("giris", "cikis", "gec_dk")})
                s["durum"] = "GELDİ"
            elif mc.tam_izinli(k, gun):
                s["durum"] = "İZİNLİ"
            elif gun > bugun:
                s["durum"] = "PLANLI"
            elif gun == bugun and an.hour * 60 + an.minute <= bas_dk:
                s["durum"] = "BEKLENİYOR"
            elif not gun_okut:
                s["durum"] = "VERİ YOK"  # o gun kimseden okutma yok: cihaz/okuma sorunu olabilir
            else:
                s["durum"] = "GELMEDİ"
            satirlar.append(s)
        satirlar.sort(key=lambda s: (s["durum"] != "GELMEDİ", s["ad"]))
        fazla = sorted((ad_soyad(per.get(k), k) for k in gun_okut if k not in planli[g]))
        say = {d: sum(1 for s in satirlar if s["durum"] == d) for d in ("GELDİ", "GELMEDİ")}
        sonuc.append({"tarih": g, "cumartesi": gun.weekday() == 5, "satirlar": satirlar,
                      "planli": len(satirlar), "geldi": say["GELDİ"], "gelmedi": say["GELMEDİ"],
                      "nobetsiz_gelen": fazla})
    return sonuc


# ----------------------------------------------------------------- ayarlar (panelden)
def ayar_getir():
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        son = {r[0]: r[1] for r in con.execute("SELECT kisi_no, MAX(zaman) FROM okutma GROUP BY kisi_no")}
        sayi = {r[0]: r[1] for r in con.execute(
            "SELECT kategori_id, COUNT(*) FROM personel_ayar WHERE kategori_id IS NOT NULL GROUP BY kategori_id")}
    hi, cts = dt.date(2024, 1, 1), dt.date(2024, 1, 6)  # bir pazartesi ve bir cumartesi
    sinir = (simdi() - dt.timedelta(days=60)).strftime("%Y-%m-%d")
    liste = []
    for k in sorted(set(per) | set(son)):
        pa = mc.per.get(k) or {}
        h, c = mc.al(k, hi), mc.al(k, cts)
        liste.append({"kisi_no": k, "ad": ad_soyad(per.get(k), k), "sicil": per[k]["sicilno"] if k in per else "",
                      "kategori_id": pa.get("kategori_id"), **{a: pa.get(a) or "" for a in SAAT_ALANLARI},
                      "son_okutma": son.get(k) or "", "aktif": (son.get(k) or "") >= sinir,
                      "gecerli": f"{h[0]}-{h[1]} ({h[2]})", "gecerli_cts": f"{c[0]}-{c[1]} ({c[2]})"})
    liste.sort(key=lambda x: x["ad"])
    kat = [dict(v, kisi_sayisi=sayi.get(v["id"], 0)) for v in sorted(mc.kat.values(), key=lambda x: x["ad"])]
    return {"genel": mc.genel, "kategoriler": kat, "personel": liste}


def ayar_genel_kaydet(js):
    deger = {a: saat_temiz(js.get(a)) for a in SAAT_ALANLARI}
    if not deger["giris"]:
        raise ValueError("Genel giriş saati boş olamaz")
    for a in ("gec_tolerans_dk", "erken_tolerans_dk"):
        deger[a] = str(max(0, min(240, int(js.get(a) or 0))))
    with db() as con:
        con.executemany("INSERT INTO ayar(anahtar,deger) VALUES(?,?) ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger",
                        [(a, d or "") for a, d in deger.items()])


def ayar_kategori_kaydet(js):
    ad = " ".join(str(js.get("ad") or "").split())[:40]
    if not ad:
        raise ValueError("Kategori adı boş olamaz")
    saat = [saat_temiz(js.get(a)) for a in SAAT_ALANLARI]
    with db() as con:
        try:
            if js.get("id"):
                con.execute("UPDATE kategori SET ad=?, giris=?, cikis=?, cts_giris=?, cts_cikis=? WHERE id=?",
                            (ad, *saat, int(js["id"])))
            else:
                con.execute("INSERT INTO kategori(ad,giris,cikis,cts_giris,cts_cikis) VALUES(?,?,?,?,?)", (ad, *saat))
        except sqlite3.IntegrityError:
            raise ValueError(f"'{ad}' adında kategori zaten var")


def ayar_kategori_sil(js):
    with db() as con:
        con.execute("UPDATE personel_ayar SET kategori_id=NULL WHERE kategori_id=?", (int(js["id"]),))
        con.execute("DELETE FROM kategori WHERE id=?", (int(js["id"]),))


def ayar_personel_kaydet(js):
    """{kisi_nolar: [...], degisiklik: {kategori_id?, giris?, cikis?, cts_giris?, cts_cikis?}}"""
    kisiler = [int(k) for k in js.get("kisi_nolar") or []]
    dg = js.get("degisiklik") or {}
    alan = {}
    if "kategori_id" in dg:
        alan["kategori_id"] = int(dg["kategori_id"]) if dg["kategori_id"] not in (None, "", 0) else None
    for a in SAAT_ALANLARI:
        if a in dg:
            alan[a] = saat_temiz(dg[a])
    if not kisiler or not alan:
        raise ValueError("Değişiklik yok")
    with db() as con:
        if alan.get("kategori_id") and not con.execute("SELECT 1 FROM kategori WHERE id=?", (alan["kategori_id"],)).fetchone():
            raise ValueError("Kategori bulunamadı")
        set_ = ", ".join(f"{a}=?" for a in alan)
        for k in kisiler:
            con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
            con.execute(f"UPDATE personel_ayar SET {set_} WHERE kisi_no=?", (*alan.values(), k))
    return {"guncellenen": len(kisiler)}


# ----------------------------------------------------------------- disa aktarma
def xlsx_yaz(baslik, satirlar, sayfa="Rapor"):
    def hucre(r, c, v):
        ref = f"{_sutun_adi(c)}{r}"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}"><v>{v}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'

    rows = []
    for r, satir in enumerate([baslik] + satirlar, 1):
        rows.append(f'<row r="{r}">' + "".join(hucre(r, c, v) for c, v in enumerate(satir, 1)) + "</row>")
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews>'
             '<cols><col min="1" max="%d" width="18" customWidth="1"/></cols><sheetData>%s</sheetData></worksheet>'
             % (max(1, len(baslik)), "".join(rows)))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets><sheet name="{escape(sayfa[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '</Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return b.getvalue()


def csv_yaz(baslik, satirlar):
    b = io.StringIO()
    w = csv.writer(b, delimiter=";")  # Turkce Excel ';' bekler
    w.writerow(baslik)
    w.writerows(satirlar)
    return ("\ufeff" + b.getvalue()).encode("utf-8")


def disa_tablo(tur, q):
    bas, bit, kisi = q.get("bas"), q.get("bit"), q.get("kisi") or None
    if tur == "ham":
        b = ["Zaman", "Kişi No", "Ad Soyad"]
        return b, [[r["zaman"], r["kisi_no"], r["ad"]] for r in ham_kayitlar(bas, bit, kisi)]
    if tur == "nobet":
        b = ["Tarih", "Kişi No", "Ad Soyad", "Mesai", "Durum", "Giriş", "Çıkış", "Geç"]
        out = []
        for g in nobet_durumu(bas, bit):
            for s in g["satirlar"]:
                out.append([g["tarih"], s["kisi_no"], s["ad"], s["mesai"], s["durum"], s["giris"], s["cikis"], sure_yaz(s["gec_dk"])])
        return b, out
    b = ["Tarih", "Kişi No", "Sicil", "Ad Soyad", "Kategori", "Mesai", "Giriş", "Çıkış", "Süre", "Okutma",
         "Geç", "Erken çıkış", "Durum", "İzin"]
    return b, [[r["tarih"], r["kisi_no"], r["sicil"], r["ad"], r["kategori"], r["mesai"], r["giris"], r["cikis"],
                r["sure"], r["okutma"], sure_yaz(r["gec_dk"]), sure_yaz(r["erken_dk"]), r["durum"], r["izin"]] for r in gunluk_rapor(bas, bit, kisi, q.get("durum") or None)]


def nobet_sablon():
    b = simdi().date()
    ilk = (b.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    gun = ilk + dt.timedelta(days=(5 - ilk.weekday()) % 7)
    satirlar = []
    while gun.month == ilk.month:
        satirlar.append([gun.strftime("%d.%m.%Y"), "", "", ""])
        gun += dt.timedelta(days=7)
    # Hucrelere ad soyad (orn. Ali Yilmaz) ya da kart no (orn. 0111) yazilir.
    return xlsx_yaz(["Tarih", "Personel 1", "Personel 2", "Personel 3"], satirlar, "Nöbet")


# ----------------------------------------------------------------- HTTP
_TARIH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Istek(BaseHTTPRequestHandler):
    server_version = "EnGoPerPanel/1.0"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def gonder(self, kod, govde, tip="application/json; charset=utf-8", ek=None):
        if not isinstance(govde, (bytes, bytearray)):
            govde = json.dumps(govde, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(kod)
        self.send_header("Content-Type", tip)
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (ek or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(govde)

    def sorgu(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        bugun = simdi().date().isoformat()
        for a in ("bas", "bit"):
            if not _TARIH_RE.match(q.get(a, "")):
                q[a] = bugun
        if q["bas"] > q["bit"]:
            q["bas"], q["bit"] = q["bit"], q["bas"]
        if q.get("kisi") and not q["kisi"].isdigit():
            q["kisi"] = ""
        return u.path, q

    def do_GET(self):
        try:
            yol, q = self.sorgu()
            if yol in ("/", "/index.html"):
                with open(os.path.join(KOK, "index.html"), "rb") as f:
                    return self.gonder(200, f.read(), "text/html; charset=utf-8")
            if yol == "/api/durum":
                return self.gonder(200, durum_bilgisi())
            if yol == "/api/bugun":
                return self.gonder(200, bugun_ozeti())
            if yol == "/api/rapor":
                return self.gonder(200, gunluk_rapor(q["bas"], q["bit"], q.get("kisi"), q.get("durum")))
            if yol == "/api/ham":
                return self.gonder(200, ham_kayitlar(q["bas"], q["bit"], q.get("kisi")))
            if yol == "/api/personel":
                with db() as con:
                    rows = con.execute("SELECT kisi_no, adi, soyadi, sicilno FROM personel ORDER BY adi, soyadi")
                    return self.gonder(200, [dict(r) for r in rows])
            if yol == "/api/nobet":
                return self.gonder(200, nobet_durumu(q["bas"], q["bit"]))
            if yol == "/api/ayar":
                return self.gonder(200, ayar_getir())
            if yol == "/api/izin":
                return self.gonder(200, {"izinler": izin_listesi(q["bas"], q["bit"], q.get("kisi")), "turler": IZIN_TURLERI})
            if yol == "/api/trend":
                return self.gonder(200, trend())
            if yol == "/api/personel_ozet":
                return self.gonder(200, personel_ozeti(q["bas"], q["bit"]))
            if yol == "/api/personel_detay" and q.get("kisi"):
                return self.gonder(200, personel_detay(q["kisi"], q["bas"], q["bit"]))
            if yol == "/api/eslesme":
                return self.gonder(200, eslesme_listesi())
            if yol == "/disa":
                tur = q.get("tur") if q.get("tur") in ("rapor", "ham", "nobet") else "rapor"
                b, s = disa_tablo(tur, q)
                ad = f'{tur}_{q["bas"]}_{q["bit"]}'
                if q.get("bicim") == "csv":
                    veri, tip, uz = csv_yaz(b, s), "text/csv; charset=utf-8", "csv"
                else:
                    veri = xlsx_yaz(b, s, tur)
                    tip, uz = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
                return self.gonder(200, veri, tip, {"Content-Disposition": f'attachment; filename="{ad}.{uz}"'})
            if yol == "/api/yedekler":
                return self.gonder(200, {"surum": SURUM, "klasor": yedek_klasoru(),
                                         "ek_klasor": CFG.get("ek_yedek_klasoru") or "", "yedekler": yedek_listesi()})
            if yol.startswith("/yedek/"):
                ad = unquote(yol[len("/yedek/"):])
                if not _YEDEK_RE.match(ad) or not os.path.exists(os.path.join(yedek_klasoru(), ad)):
                    return self.gonder(404, {"hata": "yedek bulunamadı"})
                with open(os.path.join(yedek_klasoru(), ad), "rb") as f:
                    return self.gonder(200, f.read(), "application/octet-stream",
                                       {"Content-Disposition": f'attachment; filename="{ad}"'})
            if yol == "/nobet_sablon.xlsx":
                return self.gonder(200, nobet_sablon(),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   {"Content-Disposition": 'attachment; filename="nobet_sablon.xlsx"'})
            self.gonder(404, {"hata": "bulunamadı"})
        except Exception as e:
            log.exception("GET %s", self.path)
            self.gonder(500, {"hata": str(e)})

    def yetkili(self):
        s = CFG["yonetim_sifresi"]
        return not s or unquote(self.headers.get("X-Sifre", "")) == s

    def do_POST(self):
        try:
            yol = urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10 * 1024 * 1024:
                return self.gonder(413, {"hata": "Dosya 10 MB'tan büyük olamaz"})
            govde = self.rfile.read(n) if n else b""
            if yol == "/api/topla":
                return self.gonder(200, TOPLAYICI.manuel())
            if yol.startswith(("/api/nobet/", "/api/ayar/", "/api/izin/", "/api/yedek/")) and not self.yetkili():
                return self.gonder(403, {"hata": "Yönetim şifresi hatalı"})
            if yol == "/api/nobet/onizle":
                ad = unquote(self.headers.get("X-Dosya-Adi", "dosya"))
                try:
                    return self.gonder(200, nobet_cozumle(ad, govde))
                except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as e:
                    return self.gonder(400, {"hata": f"Dosya okunamadı: {e}"})
            if yol == "/api/nobet/kaydet":
                js = json.loads(govde)
                return self.gonder(200, nobet_kaydet(js.get("dosya", ""), js.get("tarihler", []), js.get("kayitlar", [])))
            ayar_islem = {"/api/yedek/al": lambda js: {"ad": yedek_al("elle")},
                          "/api/izin/ekle": izin_ekle, "/api/izin/sil": izin_sil,
                          "/api/nobet/eslesme": eslesme_kaydet, "/api/nobet/eslesme_sil": eslesme_sil,
                          "/api/nobet/satir": nobet_satir,
                          "/api/ayar/genel": ayar_genel_kaydet, "/api/ayar/kategori": ayar_kategori_kaydet,
                          "/api/ayar/kategori_sil": ayar_kategori_sil, "/api/ayar/personel": ayar_personel_kaydet}
            if yol in ayar_islem:
                try:
                    return self.gonder(200, ayar_islem[yol](json.loads(govde)) or {"ok": True})
                except (ValueError, KeyError, TypeError) as e:
                    return self.gonder(400, {"hata": str(e)})
            if yol == "/api/nobet/sil":
                nobet_sil(json.loads(govde).get("tarihler", []))
                return self.gonder(200, {"ok": True})
            self.gonder(404, {"hata": "bulunamadı"})
        except Exception as e:
            log.exception("POST %s", self.path)
            self.gonder(500, {"hata": str(e)})


# ----------------------------------------------------------------- backup.txt ice aktarma
def backup_ice_aktar(yol):
    """Format: 001,000094,01,2026/11/18,09:15:00  (salt okuma)"""
    kayitlar = []
    with open(yol, "rb") as f:
        for satir in f.read().decode("cp1254", "replace").splitlines():
            p = [x.strip() for x in satir.split(",")]
            if len(p) < 5:
                continue
            try:
                z = dt.datetime.strptime(f"{p[3]} {p[4][:5]}", "%Y/%m/%d %H:%M")
                kayitlar.append((int(p[1]), zstr(z), None))
            except ValueError:
                continue
    return okutma_ekle(kayitlar, "backup.txt")


# ----------------------------------------------------------------- ana
def log_hazirla(konsol):
    os.makedirs(CFG["veri_klasoru"], exist_ok=True)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h = logging.handlers.RotatingFileHandler(os.path.join(CFG["veri_klasoru"], "panel.log"),
                                             maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(fmt)
    log.addHandler(h)
    if konsol and sys.stderr:
        k = logging.StreamHandler()
        k.setFormatter(fmt)
        log.addHandler(k)


def servis_hesabi():
    """SYSTEM (oturumsuz) calisiyorsa tarayici acilacak bir masaustu yoktur."""
    if not WIN:
        return not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return os.environ.get("USERNAME", "").upper() in ("SYSTEM", "") or os.environ.get("USERNAME", "").endswith("$")


def tarayici_ac(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception as e:
        log.warning("Tarayici acilamadi: %s", e)


def main():
    global CFG
    ap = argparse.ArgumentParser(description="EnGoPer Panel")
    ap.add_argument("--ayar", default=os.path.join(KOK, "config.json"))
    ap.add_argument("--bir-kez", action="store_true", help="tek okuma turu yap ve cik")
    ap.add_argument("--port", type=int, help="web panel portu (varsayilan config/8080)")
    ap.add_argument("--tarayici-yok", action="store_true", help="baslarken tarayiciyi acma (servis modu)")
    ap.add_argument("--ice-aktar-backup", nargs="?", const="", metavar="YOL", help="backup.txt ice aktar")
    a = ap.parse_args()
    CFG = ayar_yukle(a.ayar)
    log_hazirla(konsol=True)
    log.info("EnGoPer Panel v%s", SURUM)
    surum_kontrol()  # yeni surumse once yedek
    db_hazirla()
    surum_yaz()

    if a.ice_aktar_backup is not None:
        yol = a.ice_aktar_backup or CFG["backup_txt"]
        g, y = backup_ice_aktar(yol)
        print(f"backup.txt: {g} geçerli satır, {y} yeni kayıt eklendi.")
        return
    if a.bir_kez:
        personel_yenile(zorla=True)
        print(json.dumps(TOPLAYICI.tur("elle"), ensure_ascii=False))
        return

    if a.port:
        CFG["http_port"] = a.port
    try:
        srv = ThreadingHTTPServer((CFG["http_adres"], CFG["http_port"]), Istek)
    except OSError as e:
        log.error("Port %s acilamadi (%s). Servis zaten calisiyor olabilir; baska port icin:  py panel.py --port 8090   "
                  "(Windows'un ayirdigi portlar: netsh interface ipv4 show excludedportrange protocol=tcp)", CFG["http_port"], e)
        sys.exit(1)
    srv.daemon_threads = True
    personel_yenile(zorla=True)
    threading.Thread(target=TOPLAYICI.dongu, daemon=True, name="toplayici").start()
    log.info("Panel acildi: http://localhost:%s  (LAN: http://<bu-pc-ip>:%s)", CFG["http_port"], CFG["http_port"])
    if CFG["tarayici_ac"] and not a.tarayici_yok and not servis_hesabi():
        threading.Timer(1.0, tarayici_ac, args=(f'http://localhost:{CFG["http_port"]}/',)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
