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
import hashlib
import hmac
import io
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from xml.sax.saxutils import escape

SURUM = "1.11.0"  # her yeni ozellikte artirilir; degisiklikler DEGISIKLIKLER.md'de
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
    # Otomatik guncelleme (GitHub raw): dal ileride "main" olacak; taban sahte sunucuya yonlendirilebilir (test)
    "guncelleme_dali": "claude/stoic-cori-vz2i9g",
    "guncelleme_taban": "https://raw.githubusercontent.com/bardrocks/fabe-personel-giris",
    # Dalin son commit SHA'sini verir ({dal} oldugu gibi, "/" ile yerlestirilir). Dosyalar <taban>/<sha>/<dosya> adresinden
    # inilir: raw dosya adresi dosya basina ~5 dk onbelleklendigi icin dal adresiyle tutarsiz (yeni+eski) set gelebilir.
    "guncelleme_api": "https://api.github.com/repos/bardrocks/fabe-personel-giris/commits/{dal}",
    "otomatik_guncelleme": True,
    "telegram_api": "https://api.telegram.org",  # test icin sahte sunucuya yonlendirilebilir
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
CREATE TABLE IF NOT EXISTS tatil(tarih TEXT PRIMARY KEY, ad TEXT, yarim INTEGER DEFAULT 0);  -- eski (artik okunmaz)
CREATE TABLE IF NOT EXISTS kapali_gun(tarih TEXT PRIMARY KEY, aciklama TEXT, grup TEXT, olusturma TEXT);
CREATE TABLE IF NOT EXISTS duzeltme_log(
  id INTEGER PRIMARY KEY, zaman TEXT, islem TEXT, kisi_no INTEGER, okutma_zaman TEXT, aciklama TEXT, istemci TEXT);
CREATE INDEX IF NOT EXISTS ix_duzeltme_log_kisi ON duzeltme_log(kisi_no, okutma_zaman);
CREATE TABLE IF NOT EXISTS kullanici(
  id INTEGER PRIMARY KEY, ad TEXT NOT NULL UNIQUE, sifre_hash TEXT NOT NULL, tuz TEXT NOT NULL, rol TEXT NOT NULL, olusturma TEXT);
CREATE TABLE IF NOT EXISTS personel_log(
  id INTEGER PRIMARY KEY, zaman TEXT, islem TEXT, kisi_no INTEGER, eski TEXT, yeni TEXT, istemci TEXT);
CREATE TABLE IF NOT EXISTS oturum(token TEXT PRIMARY KEY, kullanici_id INTEGER NOT NULL, bitis INTEGER NOT NULL);
"""

# Eski veritabanlarina eklenen sutunlar: (tablo, sutun tanimi). Yoksa ALTER TABLE ADD COLUMN.
YENI_SUTUNLAR = (
    ("nobet", "tam_anahtar TEXT"), ("nobet_eslesme", "metin TEXT"),
    ("okutma", "gizli INTEGER DEFAULT 0"), ("okutma", "not_ TEXT"),
    ("kategori", "calisma_gunleri TEXT"),
    ("personel_ayar", "calisma_gunleri TEXT"), ("personel_ayar", "ise_giris TEXT"), ("personel_ayar", "isten_cikis TEXT"),
    ("personel_ayar", "izin_devir REAL"),
    ("personel_ayar", "gorunen_ad TEXT"), ("personel_ayar", "silindi INTEGER DEFAULT 0"), ("personel", "kaynak TEXT"),
    ("personel_ayar", "uyari_erteleme TEXT"),
)


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
        for tablo, sutun in YENI_SUTUNLAR:
            mevcut = {r[1] for r in con.execute(f"PRAGMA table_info({tablo})")}
            if sutun.split()[0] not in mevcut:
                con.execute(f"ALTER TABLE {tablo} ADD COLUMN {sutun}")
        con.execute("UPDATE personel SET kaynak='dbf' WHERE kaynak IS NULL")  # simdiye kadarki personel EnGoPer'den gelir


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
            # personel_ayar'daki gorunen_ad / silindi bu tabloda degil: DBF yenilemesi onlari ezmez.
            # DBF'de olmayan (elle eklenen) kisiler silinmez: sadece DBF'dekiler upsert edilir.
            con.execute("INSERT INTO personel(kisi_no,kartno,adi,soyadi,sicilno,guncelleme,kaynak) VALUES(?,?,?,?,?,?,'dbf') "
                        "ON CONFLICT(kisi_no) DO UPDATE SET kartno=excluded.kartno, adi=excluded.adi, "
                        "soyadi=excluded.soyadi, sicilno=excluded.sicilno, guncelleme=excluded.guncelleme, kaynak='dbf'",
                        (int(s["KARTNO"]), s["KARTNO"], s["ADI"], s["SOYADI"], s["SICILNO"], ek))
            n += 1
    _personel_mtime = mt
    log.info("Personel yenilendi: %d kayit", n)


def personel_sozluk(con):
    """kisi_no -> satir (personel + personel_ayar.gorunen_ad / silindi). Isimler HER YERDE ad_soyad() ile gosterilir."""
    return {r["kisi_no"]: r for r in con.execute(
        "SELECT p.*, a.gorunen_ad AS gorunen_ad, a.silindi AS silindi FROM personel p LEFT JOIN personel_ayar a ON a.kisi_no = p.kisi_no")}


def kart_no(p, k):
    """Gosterilen kart no: Personel.DBF'deki KARTNO metni (or. '0081'); yoksa sayi 4 haneye sifirla doldurulur."""
    kn = ((p["kartno"] if p is not None else "") or "").strip()
    return kn or f"{k:04d}"


def ad_ham(p, k):
    """Gorunen ad (varsa) yoksa EnGoPer adi; '(silindi)' eki olmadan."""
    if p is None:
        return f"#{k} (tanımsız)"
    return (p["gorunen_ad"] or "").strip() or f'{p["adi"]} {p["soyadi"]}'.strip() or f"#{k}"


def ad_soyad(p, k):
    if p is None:
        return f"#{k} (tanımsız)"
    return ad_ham(p, k) + (" (silindi)" if p["silindi"] else "")


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
            bildirim_kontrol()  # kendi icinde hata yakalar
            try:
                gunluk_yedek()
            except Exception:
                log.exception("Gunluk yedek alinamadi")
            time.sleep(CFG["toplama_aralik_sn"])


TOPLAYICI = Toplayici()


# ----------------------------------------------------------------- raporlar
def okutmalar(con, bas, bit, kisi=None):
    q = "SELECT kisi_no, zaman, kaynak FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman <= ?"
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


# ---- ozellik anahtarlari
# ad: (varsayilan_acik, yakinda, baslik, aciklama). "yakinda" olanlar henuz degistirilemez.
OZELLIKLER = {
    "nobet": (True, False, "Nöbet takibi", "Cumartesi nöbet çizelgesi, nöbete geldi/gelmedi takibi ve Bugün'deki nöbet kartı."),
    "izin": (True, False, "İzin takibi", "Tam gün / saatlik izinler; izinli gün gelmedi sayılmaz, saatlik izin mesaiyi kaydırır."),
    "duzeltme": (True, False, "Okutma düzeltme", "Unutulan okutmayı elle ekleme, yanlış okutmayı gizleme (silinmez, kayıt tutulur)."),
    "puantaj": (True, False, "Puantaj ve fazla mesai", "Aylık puantaj ızgarası (X, İ, R, HT, RT, TÇ, G…), fazla mesai ve eksik süre, Excel çıktısı."),
    "izin_bakiye": (True, False, "Yıllık izin bakiyesi", "Kıdeme göre yıllık izin hakkı, kullanılan ve kalan gün (İzinler > Bakiye)."),
    "giris": (False, False, "Panel şifresi", "Panele kullanıcı adı ve şifreyle giriş; yönetici (tam yetki) ve izleyici (salt okunur) rolleri."),
    "bildirim": (False, False, "Telegram bildirimi", "Sabah/akşam özeti ve cihaz uyarısı Telegram'a gönderilir (Ayarlar > Bildirimler)."),
}


def ozellik_haritasi(con):
    kayit = dict(con.execute("SELECT anahtar, deger FROM ayar WHERE anahtar LIKE 'ozellik\\_%' ESCAPE '\\'").fetchall())
    return {ad: kayit.get("ozellik_" + ad, "1" if v[0] else "0") == "1" for ad, v in OZELLIKLER.items()}


def ozellik(ad):
    """Ozellik acik mi? Bilinmeyen ad -> False."""
    with db() as con:
        return ozellik_haritasi(con).get(ad, False)


def ozellik_ayarla(js):
    ad = str(js.get("ad") or "")
    if ad not in OZELLIKLER:
        raise ValueError("Bilinmeyen özellik")
    if OZELLIKLER[ad][1]:
        raise ValueError("Bu özellik henüz kullanılamıyor")
    acik = js.get("acik")
    if isinstance(acik, str):
        acik = acik.strip().lower() in ("1", "true", "evet")
    with db() as con:
        if ad == "giris" and acik and _yonetici_sayisi(con) < 1:
            raise ValueError("Giriş açılmadan önce en az bir yönetici kullanıcı oluşturulmalı")
        con.execute("INSERT INTO ayar(anahtar,deger) VALUES(?,?) ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger",
                    ("ozellik_" + ad, "1" if acik else "0"))
    log.info("Ozellik %s: %s", ad, "acik" if acik else "kapali")
    return {"ad": ad, "acik": bool(acik)}


# ----------------------------------------------------------------- calisma gunleri
GUN_KISA = ["Pt", "Sa", "Ça", "Pe", "Cu", "Ct"]


def gun_listesi(v, kati=False):
    """'0,1,2' / [0,1,2] -> sirali [0..5]. Bos/None -> None (ust kademeden devral).
    kati=True: gecersiz deger ValueError; degilse gecersizler yok sayilir (DB'den okurken)."""
    if v is None:
        return None
    parcalar = [x for x in str(v).replace(" ", "").split(",") if x] if isinstance(v, str) else list(v)
    out = set()
    for x in parcalar:
        try:
            n = int(x)
        except (TypeError, ValueError):
            if kati:
                raise ValueError(f"Geçersiz gün: {x}")
            continue
        if 0 <= n <= 5:
            out.add(n)
        elif kati:
            raise ValueError("Çalışma günü Pazartesi–Cumartesi arasında olmalı (Pazar her zaman kapalı)")
    return sorted(out) or None


def gun_yaz(liste):
    return ",".join(str(g) for g in liste) if liste else None


def _tarih(g):
    if isinstance(g, dt.datetime):
        return g.date()
    return g if isinstance(g, dt.date) else dt.date.fromisoformat(g)


def tarih_temiz(v):
    v = str(v or "").strip()
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError(f"Geçersiz tarih: {v} (YYYY-AA-GG olmalı)")


def genel_ayar(con):
    g = {"giris": CFG["mesai_baslangic"], "cikis": CFG["mesai_bitis"],
         "cts_giris": CFG["cumartesi_mesai_baslangic"], "cts_cikis": CFG["cumartesi_mesai_bitis"],
         "gec_tolerans_dk": CFG["gec_tolerans_dk"], "erken_tolerans_dk": CFG["erken_tolerans_dk"],
         "calisma_gunleri": gun_listesi(CFG["calisma_gunleri"]) or [0, 1, 2, 3, 4],
         "fm_yontem": "haftalik", "fm_haftalik_esik": 45.0, "mola_dk": 0, "uzun_okutmama_gun": 14}
    for a, d in con.execute("SELECT anahtar, deger FROM ayar"):
        if a == "calisma_gunleri":
            g[a] = gun_listesi(d) or g[a]
        elif a == "fm_yontem":
            g[a] = d if d in ("haftalik", "gunluk") else g[a]
        elif a == "fm_haftalik_esik":
            try:
                g[a] = float(d)
            except (TypeError, ValueError):
                pass
        elif a == "uzun_okutmama_gun":
            try:
                g[a] = max(3, min(365, int(d)))
            except (TypeError, ValueError):
                pass
        elif a in g:
            g[a] = int(d) if a.endswith("_dk") else d
    return g


class MesaiCozucu:
    def __init__(self, con):
        self.oz = ozellik_haritasi(con)
        self.genel = genel_ayar(con)
        self.kat = {r["id"]: dict(r) for r in con.execute("SELECT * FROM kategori")}
        self.per = {r["kisi_no"]: dict(r) for r in con.execute("SELECT * FROM personel_ayar")}
        # Kapali ozellik hesaplari etkilemez: izinler yok sayilir.
        self.izinler = ({(r["kisi_no"], r["tarih"]): dict(r) for r in con.execute("SELECT * FROM izin")}
                        if self.oz["izin"] else {})
        # Magaza kapali gunler: her zaman aktif (gun girilmezse etkisi yok)
        self.kapali = {r["tarih"]: r["aciklama"] or "Mağaza kapalı" for r in con.execute("SELECT tarih, aciklama FROM kapali_gun")}

    def izin(self, k, gun):
        return self.izinler.get((k, gun if isinstance(gun, str) else gun.isoformat()))

    def tam_izinli(self, k, gun):
        iz = self.izin(k, gun)
        return bool(iz and not iz["bas_saat"])

    def kapali_mi(self, gun):
        """Magaza kapali gun mu? -> aciklama ya da None"""
        return self.kapali.get(gun if isinstance(gun, str) else gun.isoformat())

    def mesai(self, k, gun):
        """Izin dikkate alinmis mesai: (giris, cikis, kaynak). Sabah izni girisi, aksam izni cikisi kaydirir."""
        gun = _tarih(gun)
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

    def calisma_gunleri(self, k):
        """-> ([0..5], kaynak). Oncelik: personel > kategori > genel."""
        pa = self.per.get(k) or {}
        kat = self.kat.get(pa.get("kategori_id")) or {}
        for kaynak, d in (("Özel", pa), (kat.get("ad", ""), kat)):
            v = gun_listesi(d.get("calisma_gunleri"))
            if v is not None:
                return v, kaynak
        return self.genel["calisma_gunleri"], "Genel"

    def istihdamda(self, k, gun):
        """Kisi o gun isyerinde calisan mi (ise giris - isten cikis araliginda)?"""
        pa = self.per.get(k) or {}
        if pa.get("silindi"):
            return False  # silinmis personel hicbir yerde "beklenen" sayilmaz
        iso = _tarih(gun).isoformat()
        return not ((pa.get("ise_giris") and iso < pa["ise_giris"]) or (pa.get("isten_cikis") and iso > pa["isten_cikis"]))

    def beklenen_gun_mu(self, k, gun, nobetli=False):
        """Kisi bu gun isyerinde beklenir mi? (gelmedi sayilabilir mi)
        nobetli: kisi o gun nobet listesinde (sadece nobet ozelligi aciksa ve cumartesi ise isler)."""
        gun = _tarih(gun)
        wd = gun.weekday()
        if wd == 6 or self.kapali_mi(gun) or self.tam_izinli(k, gun):
            return False
        if not self.istihdamda(k, gun):
            return False
        if wd in self.calisma_gunleri(k)[0]:
            return True
        return bool(wd == 5 and nobetli and self.oz["nobet"])

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


def orta_nokta_dk(m_giris, m_cikis):
    """Mesai baslangici ile bitisinin ortasi (dakika). Bitis tanimsiz (ya da baslangictan once) ise 14:00."""
    if m_cikis and dk(m_cikis) > dk(m_giris):
        return (dk(m_giris) + dk(m_cikis)) // 2
    return 14 * 60


def gun_ozeti(gun, ts, bugun, mesai, genel):
    """ts: ayiklanmis okutma zamanlari (bos olamaz). mesai: (giris, cikis, kaynak)
    Tek okutmali gunde okutma mesainin ortasindan sonraysa o okutma CIKIS sayilir: durum 'GİRİŞ YOK' (giris bos)."""
    m_giris, m_cikis, m_kaynak = mesai
    giris = ts[0]
    cikis = ts[-1] if len(ts) > 1 else None
    giris_yok = len(ts) == 1 and (giris.hour * 60 + giris.minute) > orta_nokta_dk(m_giris, m_cikis)
    icerde = gun == bugun and len(ts) % 2 == 1 and not giris_yok
    if giris_yok:
        giris, cikis = None, ts[0]
    elif icerde:
        cikis = None
    gec_dk = 0
    if giris is not None:
        gec = (giris.hour * 60 + giris.minute) - dk(m_giris)
        gec_dk = gec if gec > genel["gec_tolerans_dk"] else 0
    erken_dk = 0
    if cikis and m_cikis:
        e = dk(m_cikis) - (cikis.hour * 60 + cikis.minute)
        erken_dk = e if e > genel["erken_tolerans_dk"] else 0
    if giris_yok:
        durum = "GİRİŞ YOK"
    elif icerde:
        durum = "İÇERİDE"
    elif cikis is None:
        durum = "ÇIKIŞ YOK"
    else:
        durum = "OK"
    sure, sure_dk = "", 0
    if cikis and giris is not None:
        sure_dk = int((cikis - giris).total_seconds() // 60)
        sure = sure_yaz(sure_dk)
    return {"giris": giris.strftime("%H:%M") if giris is not None else "", "cikis": cikis.strftime("%H:%M") if cikis else "",
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
        # Elle eklenen ya da gizlenen okutmasi olan gunler (duzeltildi rozeti)
        q = ("SELECT DISTINCT kisi_no, substr(zaman,1,10) FROM okutma WHERE zaman >= ? AND zaman <= ? "
             "AND (gizli=1 OR kaynak='elle')")
        a = [bas + " 00:00", bit + " 23:59"]
        if kisi:
            q += " AND kisi_no = ?"
            a.append(int(kisi))
        duzeltilen = {(r[0], r[1]) for r in con.execute(q, a)}
    satirlar = []
    for (k, g), zl in gruplar.items():
        gun = dt.date.fromisoformat(g)
        o = gun_ozeti(gun, ayikla(zl), bugun, mc.mesai(k, gun), mc.genel)
        p = per.get(k)
        o.update({"tarih": g, "kisi_no": k, "ad": ad_soyad(p, k), "sicil": p["sicilno"] if p else "",
                  "kategori": mc.kategori_adi(k), "izin": izin_yazi(mc.izin(k, gun)),
                  "duzeltildi": (k, g) in duzeltilen, "silindi": bool(p and p["silindi"]), "kart": kart_no(p, k), "kapali": mc.kapali_mi(gun) or ""})
        # tam gun izinli ya da magaza kapali gun: okutsa da geç/erken/çıkış yok sayilmaz (normal calismis sayilir)
        if mc.tam_izinli(k, gun) or mc.kapali_mi(gun):
            o["gec_dk"] = o["erken_dk"] = 0
            if o["durum"] == "GİRİŞ YOK":  # izinli/kapali gunde tek okutma: normal calismis, okutma giris sayilir
                o["giris"], o["cikis"], o["durum"] = o["cikis"], "", "OK"
            elif o["durum"] == "ÇIKIŞ YOK":
                o["durum"] = "OK"
        satirlar.append(o)
    if durum_filtre == "GEC":
        satirlar = [s for s in satirlar if s["gec_dk"]]
    elif durum_filtre == "ERKEN":
        satirlar = [s for s in satirlar if s["erken_dk"]]
    elif durum_filtre:
        satirlar = [s for s in satirlar if s["durum"] == durum_filtre]
    satirlar.sort(key=lambda s: (s["tarih"], s["giris"] or s["cikis"]), reverse=False)
    satirlar.sort(key=lambda s: s["tarih"], reverse=True)
    return satirlar


def ham_kayitlar(bas, bit, kisi=None):
    with db() as con:
        per = personel_sozluk(con)
        rows = okutmalar(con, bas, bit, kisi)
    out = [{"zaman": r["zaman"], "kisi_no": r["kisi_no"], "ad": ad_soyad(per.get(r["kisi_no"]), r["kisi_no"]),
            "kart": kart_no(per.get(r["kisi_no"]), r["kisi_no"]), "kaynak": "elle" if r["kaynak"] == "elle" else "cihaz"} for r in rows]
    out.sort(key=lambda s: s["zaman"], reverse=True)
    return out


def son_turlar(n=1, sadece_ok=False):
    with db() as con:
        q = "SELECT * FROM tur_log" + (" WHERE durum='OK'" if sadece_ok else "") + " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in con.execute(q, (n,))]


def durum_bilgisi():
    with db() as con:
        toplam = con.execute("SELECT COUNT(*) FROM okutma WHERE gizli=0").fetchone()[0]
        per = con.execute("SELECT COUNT(*) FROM personel").fetchone()[0]
        ilk_son = con.execute("SELECT MIN(zaman), MAX(zaman) FROM okutma WHERE gizli=0").fetchone()
    with db() as con:
        ozellikler = ozellik_haritasi(con)
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
            "sifre_gerekli": bool(CFG["yonetim_sifresi"]),
            # uyari doluysa cihaz okumasi toplama araliginin 3 katindan uzun suredir basarisiz: kirmizi serit
            "uyari_seviye": "kirmizi" if uyari else "",
            "bildirim": {"acik": ozellikler["bildirim"], "son": bildirim_ayarlari()["son_durum"]},
            "guncelleme_bildirim": (lambda k: k if k and k.get("durum") in ("basarili", "geri_alindi") else None)(_gunc_kayit()),
            "ozellikler": ozellikler, "ozellik_yakinda": [a for a, v in OZELLIKLER.items() if v[1]]}


AKTIF_PENCERE_GUN = 60


def aktif_kisiler(con, mc, per, gun=None, pencere=AKTIF_PENCERE_GUN):
    """AKTIF PERSONEL (tek tanim, her yerde bu kullanilir): silinmemis VE isten cikis tarihi gecmemis VE
    (bir kategoride VEYA son `pencere` gunde okutmasi var). Kategorisi olan kisi okutmasa da aktiftir. -> kisi_no kumesi"""
    bugun = _tarih(gun) if gun else simdi().date()
    iso = bugun.isoformat()
    sinir = (bugun - dt.timedelta(days=pencere)).isoformat()
    okuyan = {r[0] for r in con.execute("SELECT DISTINCT kisi_no FROM okutma WHERE gizli=0 AND zaman >= ?", (sinir,))}
    out = set()
    for k in set(per) | okuyan:
        p = per.get(k)
        pa = mc.per.get(k) or {}
        if (p is not None and p["silindi"]) or pa.get("silindi"):
            continue
        if pa.get("isten_cikis") and pa["isten_cikis"] < iso:
            continue
        if pa.get("kategori_id") in mc.kat or k in okuyan:
            out.add(k)
    return out


def son_okutmalar(con):
    return {r[0]: r[1] for r in con.execute("SELECT kisi_no, MAX(zaman) FROM okutma WHERE gizli=0 GROUP BY kisi_no")}


def uzun_okutmayanlar(erteleme_dahil=False):
    """Aktif kisilerden son okutmasi >= uzun_okutmama_gun gun once olanlar (kategorisi olup hic okutmayanlar dahil).
    Ertelenmis (uyari_erteleme > bugun) olanlar erteleme_dahil=False iken listelenmez. En uzun sureden kisaya."""
    bugun = simdi().date()
    iso = bugun.isoformat()
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        akt = aktif_kisiler(con, mc, per)
        son = son_okutmalar(con)
    esik = mc.genel["uzun_okutmama_gun"]
    out = []
    for k in akt:
        if k not in per:
            continue  # tanimsiz numara: personel olmadigi icin burada degil (ayri kart)
        pa = mc.per.get(k) or {}
        ise = pa.get("ise_giris") or ""
        if ise and ise > iso:
            continue  # henuz ise baslamamis
        z = son.get(k)
        if z:
            gun = (bugun - dt.date.fromisoformat(z[:10])).days
            if gun < esik:
                continue
        else:
            gun = None
            if ise and (bugun - dt.date.fromisoformat(ise)).days < esik:
                continue  # yeni ise baslamis, henuz okutmamis olabilir
        ert = pa.get("uyari_erteleme") or ""
        if ert > iso and not erteleme_dahil:
            continue
        out.append({"kisi_no": k, "kart": kart_no(per.get(k), k), "ad": ad_soyad(per.get(k), k), "kategori": mc.kategori_adi(k),
                    "son_okutma": z[:10] if z else "", "gun": gun, "hic": gun is None, "ertelendi": ert if ert > iso else "",
                    "esik": esik})
    out.sort(key=lambda x: (x["gun"] is not None, -(x["gun"] or 0), x["ad"]))
    return out


def bugun_ozeti():
    an = simdi()
    bugun = an.date()
    g = bugun.isoformat()
    satirlar = gunluk_rapor(g, g)
    satirlar.sort(key=lambda s: s["giris"] or s["cikis"])
    gelen = {s["kisi_no"] for s in satirlar}
    # Gelmeyen: son 14 gunde calismis (ya da bugun nobetci), bugun beklenen, mesai+tolerans gecmis ve okutma yok
    gelmeyen = []
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        son = (bugun - dt.timedelta(days=14)).isoformat()
        yakin = {r[0] for r in con.execute(
            "SELECT DISTINCT kisi_no FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman < ?", (son, g))}
        akt = aktif_kisiler(con, mc, per)
        sonlar = son_okutmalar(con)
        # beklenen adaylar: aktif kisilerden kategori uyeleri (14 gun sarti yok) ya da son 14 gunde okutanlar
        aktif = {k for k in akt if (mc.per.get(k) or {}).get("kategori_id") in mc.kat or k in yakin}
        nobetliler = ({r[0] for r in con.execute("SELECT kisi_no FROM nobet WHERE tarih=?", (g,))}
                      if mc.oz["nobet"] else set())
        izinli = []
        if mc.oz["izin"]:
            izinli = [dict(kisi_no=r["kisi_no"], kart=kart_no(per.get(r["kisi_no"]), r["kisi_no"]), kategori=mc.kategori_adi(r["kisi_no"]),
                           ad=ad_soyad(per.get(r["kisi_no"]), r["kisi_no"]), izin=izin_yazi(dict(r)),
                           aciklama=r["aciklama"] or "", geldi=r["kisi_no"] in gelen)
                      for r in con.execute("SELECT * FROM izin WHERE tarih=?", (g,))]
        kapali = mc.kapali_mi(bugun)
    simdi_dk = an.hour * 60 + an.minute
    for k in aktif | nobetliler:
        if k in gelen or k not in per or not mc.beklenen_gun_mu(k, bugun, k in nobetliler):
            continue  # tanimsiz (personel listesinde olmayan) numara "gelmeyen" sayilmaz
        m = mc.mesai(k, bugun)
        if simdi_dk > dk(m[0]) + mc.genel["gec_tolerans_dk"]:
            z = sonlar.get(k)
            uz = (bugun - dt.date.fromisoformat(z[:10])).days if z else None
            gelmeyen.append({"kisi_no": k, "kart": kart_no(per.get(k), k), "ad": ad_soyad(per.get(k), k), "kategori": mc.kategori_adi(k),
                             "mesai": f"{m[0]}-{m[1]}" if m[1] else m[0], "durum": "GELMEDİ",
                             "son_okutma": z[:10] if z else "", "okutmuyor_gun": uz if (uz is None or uz >= mc.genel["uzun_okutmama_gun"]) else 0})
    gelmeyen.sort(key=lambda x: x["ad"])
    izinli.sort(key=lambda x: x["ad"])
    return {"tarih": g, "satirlar": satirlar, "gelmeyen": gelmeyen, "izinli": izinli,
            "icerde": [s for s in satirlar if s["durum"] == "İÇERİDE"],
            "giris_yok": [s for s in satirlar if s["durum"] == "GİRİŞ YOK"],
            "gec": [s for s in satirlar if s["gec_dk"]],
            "erken": [s for s in satirlar if s["erken_dk"]],
            "nobet": nobet_durumu(g, g),
            "kapali": kapali}


# ----------------------------------------------------------------- personel bazli ozet
def _veri_gunleri(con, bas, bit):
    """Aralikta sistemde verisi olan (o gun en az bir kisi okutmus) gunler."""
    return {r[0] for r in con.execute(
        "SELECT DISTINCT substr(zaman,1,10) FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman <= ?",
        (bas + " 00:00", bit + " 23:59"))}


def _beklenen_gunler(mc, k, veri_gunleri):
    """Kisinin beklendigi (calisma gunu, kapali gun/izin degil, ise giris-isten cikis araliginda) ve sistemde verisi olan
    gecmis gunler. Nobet cumartesileri burada sayilmaz (nobet_gelmedi ayri izlenir)."""
    bugun = simdi().date().isoformat()
    return {g for g in veri_gunleri if g < bugun and mc.beklenen_gun_mu(k, g, False)}


def _istatistik(satirlar, beklenen, ilk_gun, nobet_gunleri, veri_gunleri, izinler=(), mc=None, kisi=None):
    gelinen = {s["tarih"] for s in satirlar}
    tam_izin = {iz["tarih"] for iz in izinler if not iz["bas_saat"]}
    bekl = {g for g in beklenen if g >= ilk_gun}
    nobet_gunleri = list(nobet_gunleri)
    # Nobet gunu olan tarihler nobet_gelmedi'de sayilir; ayni gun iki kez "gelmedi" yazilmaz
    gelmedi = sorted(bekl - gelinen - tam_izin - set(nobet_gunleri))
    bugun = simdi().date().isoformat()
    nobet_gelmedi = sorted(g for g in nobet_gunleri
                           if g < bugun and g in veri_gunleri and g not in gelinen and g not in tam_izin
                           and (mc is None or mc.beklenen_gun_mu(kisi, g, True)))
    girisler = [dk(s["giris"]) for s in satirlar if s["giris"]]
    ort = sum(girisler) // len(girisler) if girisler else None
    calisma = sum(s["sure_dk"] for s in satirlar)
    return {"gun": len(gelinen), "gec": sum(1 for s in satirlar if s["gec_dk"]),
            "gec_dk": sum(s["gec_dk"] for s in satirlar),
            "erken": sum(1 for s in satirlar if s["erken_dk"]), "erken_dk": sum(s["erken_dk"] for s in satirlar),
            "cikis_yok": sum(1 for s in satirlar if s["durum"] == "ÇIKIŞ YOK"),
            "giris_yok": sum(1 for s in satirlar if s["durum"] == "GİRİŞ YOK"),
            "gelmedi": len(gelmedi), "gelmedi_gunler": gelmedi,
            "nobet": len(nobet_gunleri), "nobet_gelmedi": len(nobet_gelmedi), "nobet_gelmedi_gunler": nobet_gelmedi,
            "ort_giris": f"{ort // 60:02d}:{ort % 60:02d}" if ort is not None else "",
            "calisma_dk": calisma, "calisma": sure_yaz(calisma) or "0 dakika",
            "hatali": sum(1 for s in satirlar if s["gec_dk"] or s["erken_dk"] or s["durum"] in ("ÇIKIŞ YOK", "GİRİŞ YOK")),
            "izinli_gun": len(tam_izin), "saatlik_izin": sum(1 for iz in izinler if iz["bas_saat"])}


def personel_ozeti(bas, bit, kisi=None):
    satirlar = gunluk_rapor(bas, bit, kisi)
    with db() as con:
        mc = MesaiCozucu(con)
        veri_gunleri = _veri_gunleri(con, bas, bit)
        ilk = {r[0]: r[1][:10] for r in con.execute("SELECT kisi_no, MIN(zaman) FROM okutma WHERE gizli=0 GROUP BY kisi_no")}
        nobet = {}
        if mc.oz["nobet"]:
            for r in con.execute("SELECT tarih, kisi_no FROM nobet WHERE tarih >= ? AND tarih <= ?", (bas, bit)):
                nobet.setdefault(r[1], []).append(r[0])
        izinler = {}
        if mc.oz["izin"]:
            for r in con.execute("SELECT * FROM izin WHERE tarih >= ? AND tarih <= ?", (bas, bit)):
                izinler.setdefault(r["kisi_no"], []).append(dict(r))
        per = personel_sozluk(con)
        akt = aktif_kisiler(con, mc, per) if not kisi else set()
    kisiye = {}
    for s in satirlar:
        kisiye.setdefault(s["kisi_no"], []).append(s)
    if kisi:
        kisiye.setdefault(int(kisi), [])
    else:
        for k in akt:  # aktif personel donemde hic okutmasa da satir olur (gun 0, gelmedi sayisiyla)
            if k in per:
                kisiye.setdefault(k, [])
    out = []
    for k, rows in kisiye.items():
        pa = mc.per.get(k) or {}
        if not kisi and ((pa.get("isten_cikis") and pa["isten_cikis"] < bas) or pa.get("silindi")):
            continue  # donem basindan once ayrilmis ya da silinmis
        ilk_gun = pa.get("ise_giris") or ilk.get(k, "0000-00-00")  # hic okutmamis aktif kisi: donemdeki beklenen gunlerin hepsi gelmedi
        st = _istatistik(rows, _beklenen_gunler(mc, k, veri_gunleri), ilk_gun, nobet.get(k, []), veri_gunleri,
                         izinler.get(k, []), mc, k)
        st.update({"kisi_no": k, "ad": rows[0]["ad"] if rows else ad_soyad(per.get(k), k), "kart": rows[0]["kart"] if rows else kart_no(per.get(k), k),
                   "kategori": mc.kategori_adi(k)})
        out.append(st)
    out.sort(key=lambda x: x["ad"])
    return out


def trend(gun_sayisi=14):
    """Son N gunun gunluk sayilari (grafik icin)."""
    bit = simdi().date()
    bas = bit - dt.timedelta(days=gun_sayisi - 1)
    gunluk = {}
    for s in gunluk_rapor(bas.isoformat(), bit.isoformat()):
        d = gunluk.setdefault(s["tarih"], {"gelen": 0, "gec": 0, "erken": 0, "cikis_yok": 0, "giris_yok": 0})
        d["gelen"] += 1
        d["gec"] += 1 if s["gec_dk"] else 0
        d["erken"] += 1 if s["erken_dk"] else 0
        d["cikis_yok"] += 1 if s["durum"] == "ÇIKIŞ YOK" else 0
        d["giris_yok"] += 1 if s["durum"] == "GİRİŞ YOK" else 0
    out = []
    g = bas
    while g <= bit:
        out.append(dict({"tarih": g.isoformat(), "gelen": 0, "gec": 0, "erken": 0, "cikis_yok": 0, "giris_yok": 0},
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
        son = con.execute("SELECT MAX(zaman) FROM okutma WHERE gizli=0 AND kisi_no=?", (k,)).fetchone()[0]
        veri_gunleri = _veri_gunleri(con, bas, bit)
        nobetler = ({r[0] for r in con.execute("SELECT tarih FROM nobet WHERE kisi_no=? AND tarih >= ? AND tarih <= ?", (k, bas, bit))}
                    if mc.oz["nobet"] else set())
    p = per.get(k)
    ozet["ad"] = ad_soyad(p, k)
    d_bas, d_bit = dt.date.fromisoformat(bas), dt.date.fromisoformat(bit)
    gen = {s["tarih"]: s for s in gunluk_rapor((d_bas - dt.timedelta(days=6)).isoformat(), (d_bit + dt.timedelta(days=6)).isoformat(), k)}
    ozet["fazla_mesai_dk"] = fazla_mesai_dk(mc, k, gen, d_bas, d_bit, "kesisen")
    ozet["fazla_mesai"] = sure_yaz(ozet["fazla_mesai_dk"]) or "0 dakika"
    ozet["eksik_dk"] = ozet["gec_dk"] + ozet["erken_dk"]
    ozet["eksik"] = sure_yaz(ozet["eksik_dk"]) or "0 dakika"
    ozet["fm_yontem"] = mc.genel["fm_yontem"]
    bugun = simdi().date()
    gelmedi, nobet_gelmedi = set(ozet["gelmedi_gunler"]), set(ozet["nobet_gelmedi_gunler"])
    takvim = []
    g = dt.date.fromisoformat(bas)
    while g <= dt.date.fromisoformat(bit):
        t = g.isoformat()
        s = gunler.get(t)
        kapali = mc.kapali_mi(t)
        if mc.tam_izinli(k, t) and not (s and s["durum"] == "İÇERİDE"):
            d = "izinli"
        elif s:
            if s["durum"] == "İÇERİDE":
                d = "icerde"
            elif s["durum"] == "ÇIKIŞ YOK":
                d = "cikis_yok"
            elif s["durum"] == "GİRİŞ YOK":
                d = "giris_yok"
            elif s["gec_dk"]:
                d = "gec"
            elif s["erken_dk"]:
                d = "erken"
            else:
                d = "zamaninda"
        elif t in nobet_gelmedi or t in gelmedi:
            d = "gelmedi"
        elif kapali:
            d = "kapali"
        elif g > bugun:
            d = "gelecek"
        elif not mc.beklenen_gun_mu(k, g, t in nobetler):
            d = "bos"  # beklenmeyen gun: Pazar, kisinin calismadigi gun, ise girmeden/ayrildiktan sonra
        elif t not in veri_gunleri:
            d = "veri_yok"
        else:
            d = "bos"
        takvim.append({"tarih": t, "durum": d, "giris": s["giris"] if s else "", "cikis": s["cikis"] if s else "",
                       "gec_dk": s["gec_dk"] if s else 0, "erken_dk": s["erken_dk"] if s else 0,
                       "izin": izin_yazi(mc.izin(k, t)), "kapali": kapali or "",
                       "duzeltildi": bool(s and s["duzeltildi"])})
        g += dt.timedelta(days=1)
    hi, cts = mc.al(k, dt.date(2024, 1, 1)), mc.al(k, dt.date(2024, 1, 6))
    cg, cg_kaynak = mc.calisma_gunleri(k)
    pa = mc.per.get(k) or {}
    return {"kisi": {"kisi_no": k, "ad": ad_soyad(p, k), "sicil": p["sicilno"] if p else "",
                     "kategori": mc.kategori_adi(k), "kategori_id": (mc.per.get(k) or {}).get("kategori_id"), "kart": kart_no(p, k), "son_okutma": son or "",
                     "mesai": f"{hi[0]}-{hi[1]} ({hi[2]})", "mesai_cts": f"{cts[0]}-{cts[1]} ({cts[2]})",
                     "calisma_gunleri": cg, "calisma_gunleri_kaynak": cg_kaynak,
                     "ise_giris": pa.get("ise_giris") or "", "isten_cikis": pa.get("isten_cikis") or ""},
            "ozet": ozet, "takvim": takvim, "izinler": izin_listesi(bas, bit, k),
            "uzun_uyari": next((u for u in uzun_okutmayanlar(True) if u["kisi_no"] == k), None),
            "izin_bakiye": (izin_bakiye(k) or [None])[0] if mc.oz["izin_bakiye"] else None,
            "gunler": sorted(gunler.values(), key=lambda s: s["tarih"], reverse=True)}


# ----------------------------------------------------------------- izinler
def izin_listesi(bas, bit, kisi=None):
    """Ayni seferde girilen gunler (grup) tek satirda: {grup, kisi, bas_tarih, bit_tarih, gun, ...}"""
    if not ozellik("izin"):
        return []
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
    if not ozellik("izin"):
        raise ValueError("İzin takibi kapalı (Ayarlar > Özellikler)")
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
        mc = MesaiCozucu(con)
        calisir = mc.calisma_gunleri(k)[0]
        nobetler = ({r[0] for r in con.execute("SELECT tarih FROM nobet WHERE kisi_no=? AND tarih>=? AND tarih<=?",
                                               (k, b.isoformat(), e.isoformat()))} if mc.oz["nobet"] else set())
        n, atlanan, g = 0, 0, b
        while g <= e:
            # Aralikta Pazar, kisinin calismadigi gunler (nobetsiz cumartesi dahil) ve magaza kapali gunler atlanir;
            # tek gun secildiyse yine yazilir.
            uygun = (g.weekday() != 6 and not mc.kapali_mi(g)
                     and (g.weekday() in calisir or (g.weekday() == 5 and g.isoformat() in nobetler)))
            if b == e or uygun:
                con.execute("INSERT INTO izin(kisi_no,tarih,bas_saat,bit_saat,tur,aciklama,grup,olusturma) "
                            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(kisi_no,tarih) DO UPDATE SET bas_saat=excluded.bas_saat, "
                            "bit_saat=excluded.bit_saat, tur=excluded.tur, aciklama=excluded.aciklama, grup=excluded.grup, "
                            "olusturma=excluded.olusturma",
                            (k, g.isoformat(), bs, bt, tur, aciklama, grup, zstr(simdi())))
                n += 1
            else:
                atlanan += 1
            g += dt.timedelta(days=1)
    if not n:
        raise ValueError("Seçilen aralıkta bu kişinin çalışma günü yok (Pazar, mağaza kapalı günler ve çalışılmayan günler atlanır)")
    log.info("Izin eklendi: kisi=%s %s..%s %s %s", k, b, e, tur, f"{bs}-{bt}" if bs else "tam gun")
    return {"gun": n, "atlanan": atlanan}


def izin_sil(js):
    ids = [int(i) for i in js.get("ids") or []]
    with db() as con:
        con.executemany("DELETE FROM izin WHERE id=?", [(i,) for i in ids])
    return {"silinen": len(ids)}


# ----------------------------------------------------------------- okutma duzeltme
def _duz_log(con, islem, kisi_no, okutma_zaman, aciklama, istemci):
    con.execute("INSERT INTO duzeltme_log(zaman,islem,kisi_no,okutma_zaman,aciklama,istemci) VALUES(?,?,?,?,?,?)",
                (zstr(simdi()), islem, kisi_no, okutma_zaman, aciklama, str(istemci or "")[:60]))


def _duz_hazir():
    if not ozellik("duzeltme"):
        raise ValueError("Okutma düzeltme kapalı (Ayarlar > Özellikler)")


def _aciklama_zorunlu(js):
    a = " ".join(str(js.get("aciklama") or "").split())[:200]
    if not a:
        raise ValueError("Açıklama zorunlu")
    return a


def okutma_gun(kisi, tarih):
    """Bir kisinin o gunku ham okutmalari (gizliler dahil) ve o gunun duzeltme logu."""
    k = int(kisi)
    tarih = dt.date.fromisoformat(tarih).isoformat()
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        rows = con.execute("SELECT id, cihaz, zaman, kaynak, gizli, not_ FROM okutma WHERE kisi_no=? AND zaman >= ? AND zaman <= ? "
                           "ORDER BY zaman, id", (k, tarih + " 00:00", tarih + " 23:59")).fetchall()
        loglar = con.execute("SELECT * FROM duzeltme_log WHERE kisi_no=? AND okutma_zaman >= ? AND okutma_zaman <= ? "
                             "ORDER BY id", (k, tarih + " 00:00", tarih + " 23:59")).fetchall()
        m = mc.mesai(k, dt.date.fromisoformat(tarih))
    return {"kisi_no": k, "ad": ad_soyad(per.get(k), k), "tarih": tarih, "mesai": f"{m[0]}-{m[1]}" if m[1] else m[0],
            "mesai_cikis": m[1],
            "okutmalar": [{"id": r["id"], "zaman": r["zaman"], "saat": r["zaman"][11:16], "cihaz": r["cihaz"],
                           "kaynak": "elle" if r["kaynak"] == "elle" else "cihaz", "gizli": bool(r["gizli"]),
                           "not": r["not_"] or ""} for r in rows],
            "log": [dict(r) for r in loglar]}


def duzeltme_ekle(js, istemci=""):
    """Elle okutma ekler: cihaz='ELLE', kaynak='elle'. Aciklama zorunlu."""
    _duz_hazir()
    try:
        k = int(js["kisi_no"])
        tarih = dt.date.fromisoformat(str(js["tarih"])).isoformat()
        saat = str(js["saat"]).strip()[:5]
    except (KeyError, TypeError, ValueError):
        raise ValueError("Kişi, tarih ve saat gerekli")
    if not _SAAT_RE.match(saat):
        raise ValueError("Geçersiz saat (SS:DD olmalı)")
    aciklama = _aciklama_zorunlu(js)
    zaman = f"{tarih} {saat}"
    z = dt.datetime.strptime(zaman, "%Y-%m-%d %H:%M")
    if z > simdi() + dt.timedelta(minutes=1):
        raise ValueError("Gelecekteki bir saate okutma eklenemez")
    with db() as con:
        if not con.execute("SELECT 1 FROM personel WHERE kisi_no=?", (k,)).fetchone():
            raise ValueError("Personel bulunamadı")
        mevcut = con.execute("SELECT id, gizli FROM okutma WHERE cihaz='ELLE' AND kisi_no=? AND zaman=?", (k, zaman)).fetchone()
        if mevcut and not mevcut["gizli"]:
            raise ValueError("Bu saatte zaten elle eklenmiş bir okutma var")
        esik = CFG["tekrar_esik_dk"] * 60
        for (zz,) in con.execute("SELECT zaman FROM okutma WHERE gizli=0 AND kisi_no=? AND zaman >= ? AND zaman <= ?",
                                 (k, tarih + " 00:00", tarih + " 23:59")):
            if abs((dt.datetime.strptime(zz, "%Y-%m-%d %H:%M") - z).total_seconds()) < esik:
                raise ValueError(f"{zz[11:]} okutması bu saate {CFG['tekrar_esik_dk']} dakikadan yakın; "
                                 "tekrar okutmalar tek sayıldığı için eklenemez")
        if mevcut:  # ayni saatte gizlenmis elle kayit: yeniden ac
            con.execute("UPDATE okutma SET gizli=0, not_=? WHERE id=?", (aciklama, mevcut["id"]))
            oid = mevcut["id"]
        else:
            oid = con.execute("INSERT INTO okutma(cihaz,kisi_no,zaman,vm,kaynak,eklenme,gizli,not_) VALUES('ELLE',?,?,NULL,'elle',?,0,?)",
                              (k, zaman, zstr(simdi()), aciklama)).lastrowid
        _duz_log(con, "ekle", k, zaman, aciklama, istemci)
    log.info("Duzeltme ekle: kisi=%s %s (%s) istemci=%s", k, zaman, aciklama, istemci)
    return {"id": oid, "zaman": zaman}


def duzeltme_gizle(js, istemci=""):
    """Okutmayi silmez, gizli=1 yapar (toplayici ayni kaydi tekrar okusa da INSERT OR IGNORE yuzunden gizli kalir)."""
    _duz_hazir()
    aciklama = _aciklama_zorunlu(js)
    with db() as con:
        r = con.execute("SELECT * FROM okutma WHERE id=?", (int(js.get("id") or 0),)).fetchone()
        if not r:
            raise ValueError("Okutma bulunamadı")
        if r["gizli"]:
            raise ValueError("Bu okutma zaten gizli")
        con.execute("UPDATE okutma SET gizli=1, not_=CASE WHEN kaynak='elle' THEN not_ ELSE ? END WHERE id=?", (aciklama, r["id"]))
        _duz_log(con, "gizle", r["kisi_no"], r["zaman"], aciklama, istemci)
    log.info("Duzeltme gizle: id=%s kisi=%s %s (%s) istemci=%s", r["id"], r["kisi_no"], r["zaman"], aciklama, istemci)
    return {"id": r["id"]}


def duzeltme_geri_al(js, istemci=""):
    """Gizliyi geri acar; elle ekleneni (gizli degilse) gizler."""
    _duz_hazir()
    with db() as con:
        r = con.execute("SELECT * FROM okutma WHERE id=?", (int(js.get("id") or 0),)).fetchone()
        if not r:
            raise ValueError("Okutma bulunamadı")
        aciklama = " ".join(str(js.get("aciklama") or "").split())[:200]
        if r["gizli"]:
            con.execute("UPDATE okutma SET gizli=0, not_=CASE WHEN kaynak='elle' THEN not_ ELSE NULL END WHERE id=?", (r["id"],))
            aciklama = aciklama or "Gizleme geri alındı"
            gizli = False
        elif r["kaynak"] == "elle":
            con.execute("UPDATE okutma SET gizli=1 WHERE id=?", (r["id"],))
            aciklama = aciklama or "Elle eklenen okutma geri alındı"
            gizli = True
        else:
            raise ValueError("Cihaz okutması gizli değil; geri alınacak bir düzeltme yok")
        _duz_log(con, "geri_al", r["kisi_no"], r["zaman"], aciklama, istemci)
    log.info("Duzeltme geri_al: id=%s kisi=%s %s istemci=%s", r["id"], r["kisi_no"], r["zaman"], istemci)
    return {"id": r["id"], "gizli": gizli}


# ----------------------------------------------------------------- magaza kapali gunler
def kapali_gun_listesi(yil):
    yil = int(yil)
    with db() as con:
        rows = con.execute("SELECT tarih, aciklama, COALESCE(grup, 'g' || tarih) AS grup FROM kapali_gun WHERE tarih >= ? AND tarih <= ? ORDER BY tarih",
                           (f"{yil}-01-01", f"{yil}-12-31")).fetchall()
    gruplar = {}
    for r in rows:
        g = gruplar.setdefault(r["grup"], {"grup": r["grup"], "bas": r["tarih"], "bit": r["tarih"], "gun": 0, "aciklama": r["aciklama"] or ""})
        g["bit"] = r["tarih"]
        g["gun"] += 1
    return {"yil": yil, "gruplar": sorted(gruplar.values(), key=lambda x: x["bas"])}


def kapali_gun_ekle(js):
    bas, bit = tarih_temiz(js.get("bas")), tarih_temiz(js.get("bit") or js.get("bas"))
    if not bas:
        raise ValueError("Başlangıç tarihi gerekli")
    if bit < bas:
        bas, bit = bit, bas
    b, e = dt.date.fromisoformat(bas), dt.date.fromisoformat(bit)
    if (e - b).days > 366:
        raise ValueError("En fazla 1 yıllık aralık girilebilir")
    aciklama = " ".join(str(js.get("aciklama") or "").split())[:80] or "Mağaza kapalı"
    grup = f"kg{int(time.time() * 1000)}"
    with db() as con:
        g = b
        while g <= e:
            con.execute("INSERT INTO kapali_gun(tarih,aciklama,grup,olusturma) VALUES(?,?,?,?) ON CONFLICT(tarih) DO UPDATE SET "
                        "aciklama=excluded.aciklama, grup=excluded.grup, olusturma=excluded.olusturma", (g.isoformat(), aciklama, grup, zstr(simdi())))
            g += dt.timedelta(days=1)
    return {"grup": grup, "gun": (e - b).days + 1}


def kapali_gun_sil(js):
    grup = str(js.get("grup") or "")
    if not grup:
        raise ValueError("Grup gerekli")
    with db() as con:
        if grup.startswith("g20"):  # grup bilgisi olmayan tek gun
            n = con.execute("DELETE FROM kapali_gun WHERE grup IS NULL AND tarih=?", (grup[1:],)).rowcount
        else:
            n = con.execute("DELETE FROM kapali_gun WHERE grup=?", (grup,)).rowcount
    return {"silinen": n}


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
            per = [r for r in personel_sozluk(con).values() if not r["silindi"]]
            self.aktif = aktif_kisiler(con, MesaiCozucu(con), personel_sozluk(con))
            self.hafiza = {a: k for a, k in con.execute("SELECT anahtar, kisi_no FROM nobet_eslesme")}
        self.per = {r["kisi_no"]: r for r in per}
        self.tam, self.bas_harf, self.ilk = {}, {}, {}
        for r in per:
            adaylar = [(katla(r["adi"]), katla(r["soyadi"]))]
            gor = katla(r["gorunen_ad"] or "").split()
            if gor:  # gorunen ad: son kelime soyad, oncesi ad
                adaylar.append((" ".join(gor[:-1]) or gor[0], gor[-1] if len(gor) > 1 else ""))
            for a, s in adaylar:
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
                    if all(w in katla(f'{r["adi"]} {r["soyadi"]} {r["gorunen_ad"] or ""}').split() for w in kel)]
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
    per = personel_sozluk(con)
    aktif = aktif_kisiler(con, MesaiCozucu(con), per)
    liste = [{"kisi_no": k, "ad": ad_soyad(p, k), "aktif": k in aktif} for k, p in per.items() if not p["silindi"]]
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
        if not mc.oz["nobet"]:
            return []
        plan = con.execute("SELECT tarih, kisi_no, metin FROM nobet WHERE tarih >= ? AND tarih <= ? ORDER BY tarih",
                           (bas, bit)).fetchall()
        if not plan:
            return []
        gunler = sorted({r["tarih"] for r in plan})
        okut = {}
        for r in con.execute("SELECT kisi_no, zaman FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman <= ? ORDER BY zaman",
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
            s = {"kisi_no": k, "kart": kart_no(per.get(k), k), "ad": ad_soyad(per.get(k), k), "giris": "", "cikis": "", "gec_dk": 0,
                 "mesai": f"{mesai[0]}-{mesai[1]}" if mesai[1] else mesai[0], "metin": yazi.get((g, k), "")}
            if k in gun_okut:
                s.update({x: y for x, y in gun_ozeti(gun, ayikla(gun_okut[k]), bugun, mesai, mc.genel).items()
                          if x in ("giris", "cikis", "gec_dk")})
                s["durum"] = "GELDİ"
            elif mc.tam_izinli(k, gun):
                s["durum"] = "İZİNLİ"
            elif mc.kapali_mi(gun):
                s["durum"] = "KAPALI"  # magaza kapali: gelmedi sayilmaz
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
        son = son_okutmalar(con)
        akt = aktif_kisiler(con, mc, per)
        sayi = {r[0]: r[1] for r in con.execute(
            "SELECT kategori_id, COUNT(*) FROM personel_ayar WHERE kategori_id IS NOT NULL GROUP BY kategori_id")}
    hi, cts = dt.date(2024, 1, 1), dt.date(2024, 1, 6)  # bir pazartesi ve bir cumartesi
    bugun_iso = simdi().date().isoformat()
    liste = []
    for k in sorted(set(per) | set(son)):
        if k in per and per[k]["silindi"]:
            continue
        pa = mc.per.get(k) or {}
        h, c = mc.al(k, hi), mc.al(k, cts)
        cg, cg_kaynak = mc.calisma_gunleri(k)
        liste.append({"kisi_no": k, "kart": kart_no(per.get(k), k), "ad": ad_soyad(per.get(k), k), "sicil": per[k]["sicilno"] if k in per else "",
                      "kategori_id": pa.get("kategori_id"), **{a: pa.get(a) or "" for a in SAAT_ALANLARI},
                      "son_okutma": son.get(k) or "", "aktif": k in akt,
                      "gecerli": f"{h[0]}-{h[1]} ({h[2]})", "gecerli_cts": f"{c[0]}-{c[1]} ({c[2]})",
                      "calisma_gunleri": cg, "calisma_gunleri_kaynak": cg_kaynak,
                      "calisma_gunleri_ozel": pa.get("calisma_gunleri") or "",
                      "ise_giris": pa.get("ise_giris") or "", "isten_cikis": pa.get("isten_cikis") or "",
                      "ayrildi": bool(pa.get("isten_cikis") and pa["isten_cikis"] < bugun_iso)})
    liste.sort(key=lambda x: x["ad"])
    kat = [dict(v, kisi_sayisi=sayi.get(v["id"], 0)) for v in sorted(mc.kat.values(), key=lambda x: x["ad"])]
    oz = mc.oz
    with db() as con:
        yonetici_var = _yonetici_sayisi(con) > 0
    return {"genel": mc.genel, "kategoriler": kat, "personel": liste, "yonetici_var": yonetici_var,
            "ozellikler": [{"ad": a, "baslik": v[2], "aciklama": v[3], "acik": oz[a], "yakinda": v[1]}
                           for a, v in OZELLIKLER.items()]}


def ayar_genel_kaydet(js):
    deger = {a: saat_temiz(js.get(a)) for a in SAAT_ALANLARI}
    if not deger["giris"]:
        raise ValueError("Genel giriş saati boş olamaz")
    for a in ("gec_tolerans_dk", "erken_tolerans_dk"):
        deger[a] = str(max(0, min(240, int(js.get(a) or 0))))
    if "fm_yontem" in js:
        if js["fm_yontem"] not in ("haftalik", "gunluk"):
            raise ValueError("Fazla mesai yöntemi haftalik ya da gunluk olmalı")
        deger["fm_yontem"] = js["fm_yontem"]
    if "fm_haftalik_esik" in js:
        try:
            esik = float(str(js["fm_haftalik_esik"]).replace(",", "."))
        except ValueError:
            raise ValueError("Haftalık eşik bir sayı olmalı")
        if not 1 <= esik <= 168:
            raise ValueError("Haftalık eşik 1-168 saat arasında olmalı")
        deger["fm_haftalik_esik"] = f"{esik:g}"
    if "mola_dk" in js:
        deger["mola_dk"] = str(max(0, min(240, int(js.get("mola_dk") or 0))))
    if "uzun_okutmama_gun" in js:
        try:
            deger["uzun_okutmama_gun"] = str(max(3, min(365, int(js.get("uzun_okutmama_gun")))))
        except (TypeError, ValueError):
            raise ValueError("Uzun süre okutmama uyarısı gün sayısı olmalı (3-365)")
    if "calisma_gunleri" in js:
        gl = gun_listesi(js["calisma_gunleri"], kati=True)
        if not gl:
            raise ValueError("En az bir çalışma günü seçin")
        deger["calisma_gunleri"] = gun_yaz(gl)
    with db() as con:
        con.executemany("INSERT INTO ayar(anahtar,deger) VALUES(?,?) ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger",
                        [(a, d or "") for a, d in deger.items()])


def ayar_kategori_kaydet(js):
    ad = " ".join(str(js.get("ad") or "").split())[:40]
    if not ad:
        raise ValueError("Kategori adı boş olamaz")
    saat = [saat_temiz(js.get(a)) for a in SAAT_ALANLARI]
    gunler = gun_yaz(gun_listesi(js.get("calisma_gunleri"), kati=True))  # None: genelden devral
    with db() as con:
        try:
            if js.get("id"):
                con.execute("UPDATE kategori SET ad=?, giris=?, cikis=?, cts_giris=?, cts_cikis=? WHERE id=?",
                            (ad, *saat, int(js["id"])))
                if "calisma_gunleri" in js:  # eski istemciler gondermezse mevcut deger korunur
                    con.execute("UPDATE kategori SET calisma_gunleri=? WHERE id=?", (gunler, int(js["id"])))
            else:
                con.execute("INSERT INTO kategori(ad,giris,cikis,cts_giris,cts_cikis,calisma_gunleri) VALUES(?,?,?,?,?,?)",
                            (ad, *saat, gunler))
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
    for a in ("ise_giris", "isten_cikis"):
        if a in dg:
            alan[a] = tarih_temiz(dg[a])
    if "calisma_gunleri" in dg:  # bos/None: kategoriden/genelden devral
        alan["calisma_gunleri"] = gun_yaz(gun_listesi(dg["calisma_gunleri"], kati=True))
    if not kisiler or not alan:
        raise ValueError("Değişiklik yok")
    with db() as con:
        if alan.get("kategori_id") and not con.execute("SELECT 1 FROM kategori WHERE id=?", (alan["kategori_id"],)).fetchone():
            raise ValueError("Kategori bulunamadı")
        set_ = ", ".join(f"{a}=?" for a in alan)
        for k in kisiler:
            con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
            con.execute(f"UPDATE personel_ayar SET {set_} WHERE kisi_no=?", (*alan.values(), k))
            if "ise_giris" in alan or "isten_cikis" in alan:
                ise, isten = con.execute("SELECT ise_giris, isten_cikis FROM personel_ayar WHERE kisi_no=?", (k,)).fetchone()
                if ise and isten and isten < ise:
                    raise ValueError("İşten çıkış tarihi işe girişten önce olamaz")
    return {"guncellenen": len(kisiler)}


# ----------------------------------------------------------------- puantaj ve fazla mesai
PUANTAJ_KODLARI = [
    ("X", "Geldi"), ("İ", "İzinli (tam gün; yıllık, mazeret, diğer)"), ("R", "Raporlu"), ("Ü", "Ücretsiz izin"),
    ("HT", "Hafta tatili (kişinin beklenmediği gün: Pazar, çalışmadığı gün, nöbetsiz cumartesi)"),
    ("K", "Mağaza kapalı (kapalı günlerde okutma yoksa; okutma varsa X)"), ("G", "Gelmedi (beklenen gün, okutma yok)"),
    ("", "Boş: gelecek gün, sistemde veri yok ya da kişi o gün işte değil (işe girmemiş / ayrılmış)")]
FM_KURALI = {
    "haftalik": "Haftalar Pazartesi başlar; haftalık toplam çalışma eşiği aşarsa aşan kısım fazla mesaidir. Ay sınırındaki haftalar haftanın tamamı "
                "üzerinden hesaplanır ve fazla mesai haftanın son gününün (Pazar) ayına yazılır.",
    "gunluk": "Her gün mesai bitişinden sonra çalışılan süre fazla mesaidir (erken gelme sayılmaz)."}


def _ay_araligi(ay):
    try:
        y, m = (int(x) for x in str(ay).split("-"))
        bas = dt.date(y, m, 1)
    except (ValueError, TypeError):
        raise ValueError("Ay YYYY-AA biçiminde olmalı")
    return bas, (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))


def calisma_dakika(satir, mola):
    """Cikisi olan gunun calisma suresi - mola (negatif olmaz). Cikissiz gun 0."""
    if not satir or not satir.get("cikis") or satir.get("sure_dk", 0) <= 0:
        return 0
    return max(0, satir["sure_dk"] - mola)


def fazla_mesai_dk(mc, k, gunler, bas, bit, kapsam="bitis"):
    """gunler: {'YYYY-MM-DD': gunluk_rapor satiri} (aralik +-6 gunu kapsamali). bas/bit: date.
    kapsam 'bitis': haftanin son gunu (Pazar) [bas, bit] icindeyse hafta sayilir (puantaj);
    'kesisen': donemle kesisen her hafta sayilir (kisi sayfasi)."""
    g = mc.genel
    if g["fm_yontem"] == "gunluk":
        t = 0
        for iso, s in gunler.items():
            if bas.isoformat() <= iso <= bit.isoformat() and s.get("cikis") and s.get("giris"):
                m = mc.mesai(k, iso)[1]
                if m:
                    t += max(0, dk(s["cikis"]) - dk(m))
        return t
    esik = int(round(g["fm_haftalik_esik"] * 60))
    pzt, t = bas - dt.timedelta(days=bas.weekday()), 0
    while pzt <= bit:
        if kapsam != "bitis" or bas <= pzt + dt.timedelta(days=6) <= bit:
            toplam = sum(calisma_dakika(gunler.get((pzt + dt.timedelta(days=i)).isoformat()), g["mola_dk"]) for i in range(7))
            t += max(0, toplam - esik)
        pzt += dt.timedelta(days=7)
    return t


def puantaj(ay):
    if not ozellik("puantaj"):
        raise ValueError("Puantaj kapalı (Ayarlar > Özellikler)")
    bas, bit = _ay_araligi(ay)
    bugun = simdi().date()
    satirlar = gunluk_rapor((bas - dt.timedelta(days=6)).isoformat(), (bit + dt.timedelta(days=6)).isoformat())
    gunluk = {}
    for s in satirlar:
        gunluk.setdefault(s["kisi_no"], {})[s["tarih"]] = s
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        veri = _veri_gunleri(con, bas.isoformat(), bit.isoformat())
        aktif = {r[0] for r in con.execute("SELECT DISTINCT kisi_no FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman <= ?",
                                           ((bas - dt.timedelta(days=60)).isoformat(), bit.isoformat() + " 23:59"))}
        aktif |= {k for k, pa in mc.per.items() if pa.get("kategori_id") in mc.kat and k in per}  # kategori uyeleri okutmasa da satir olur
        nobet = ({(r[0], r[1]) for r in con.execute("SELECT kisi_no, tarih FROM nobet WHERE tarih >= ? AND tarih <= ?",
                                                   (bas.isoformat(), bit.isoformat()))} if mc.oz["nobet"] else set())
    mola = mc.genel["mola_dk"]
    gun_listesi_ = [bas + dt.timedelta(days=i) for i in range((bit - bas).days + 1)]
    kisiler = set(aktif) | {k for k, g in gunluk.items() if any(bas.isoformat() <= t <= bit.isoformat() for t in g)}
    out = []
    for k in kisiler:
        pa = mc.per.get(k) or {}
        if pa.get("silindi") or (pa.get("isten_cikis") and pa["isten_cikis"] < bas.isoformat()) or (pa.get("ise_giris") and pa["ise_giris"] > bit.isoformat()):
            continue  # ay basindan once ayrilmis / ay sonundan sonra ise girecek
        rows = gunluk.get(k, {})
        hucre, tp = [], dict(calisilan=0, izin=0, rapor=0, ucretsiz=0, gelmedi=0, gec=0, calisma_dk=0, eksik_dk=0, giris_yok=0, cikis_yok=0)
        for g in gun_listesi_:
            iso = g.isoformat()
            s = rows.get(iso)
            iz = mc.izin(k, iso)
            if mc.tam_izinli(k, iso):
                tur = (iz.get("tur") or "")
                kod = "R" if tur == "Raporlu" else "Ü" if tur == "Ücretsiz" else "İ"
                tp["rapor" if kod == "R" else "ucretsiz" if kod == "Ü" else "izin"] += 1
            elif s:
                kod = "X"
                tp["calisilan"] += 1
            elif g > bugun or not mc.istihdamda(k, iso):
                kod = ""
            elif mc.kapali_mi(iso):
                kod = "K"
            elif not mc.beklenen_gun_mu(k, g, (k, iso) in nobet):
                kod = "HT"
            elif g == bugun or iso not in veri:
                kod = ""
            else:
                kod, tp["gelmedi"] = "G", tp["gelmedi"] + 1
            h = {"k": kod}
            if s:
                h.update(g=s["giris"], c=s["cikis"])
                tp["calisma_dk"] += calisma_dakika(s, mola)
                tp["eksik_dk"] += s["gec_dk"] + s["erken_dk"]
                tp["gec"] += 1 if s["gec_dk"] else 0
                if iz and iz["bas_saat"]:
                    h["s"] = 1  # saatlik izinli gelinen gun
                if s["duzeltildi"]:
                    h["d"] = 1
                if s["durum"] == "GİRİŞ YOK":
                    h["y"] = "g"
                    tp["giris_yok"] += 1
                elif s["durum"] == "ÇIKIŞ YOK":
                    h["y"] = "c"
                    tp["cikis_yok"] += 1
            if mc.kapali_mi(iso):
                h["t"] = mc.kapali_mi(iso)
            if iz and kod in ("İ", "R", "Ü", "X"):
                h["i"] = izin_yazi(iz)
            hucre.append(h)
        tp["fazla_mesai_dk"] = fazla_mesai_dk(mc, k, rows, bas, bit)
        p = per.get(k)
        out.append({"kisi_no": k, "ad": ad_soyad(p, k), "kategori": mc.kategori_adi(k), "hucreler": hucre, "toplam": tp})
    out.sort(key=lambda x: x["ad"])
    return {"ay": f"{bas.year}-{bas.month:02d}",
            "gunler": [{"tarih": g.isoformat(), "gun": g.day, "hafta_gunu": g.weekday(),
                        "kapali": mc.kapali_mi(g.isoformat()) or ""} for g in gun_listesi_],
            "satirlar": out, "kodlar": PUANTAJ_KODLARI,
            "fm": {"yontem": mc.genel["fm_yontem"], "esik": mc.genel["fm_haftalik_esik"], "mola_dk": mola,
                   "aciklama": FM_KURALI[mc.genel["fm_yontem"]]}}


def puantaj_tablo(ay):
    d = puantaj(ay)
    b = ["Ad Soyad", "Kategori"] + [str(g["gun"]) for g in d["gunler"]] + [
        "Çalışılan gün", "Toplam çalışma", "Fazla mesai", "Eksik süre", "Geç (adet)", "İzin gün", "Rapor gün", "Ücretsiz gün",
        "Gelmedi gün", "Giriş okutmadı (gün)", "Çıkış okutmadı (gün)"]
    rows = []
    for r in d["satirlar"]:
        t = r["toplam"]
        rows.append([r["ad"], r["kategori"]] + [h["k"] for h in r["hucreler"]] + [
            t["calisilan"], sure_yaz(t["calisma_dk"]) or "0", sure_yaz(t["fazla_mesai_dk"]) or "0", sure_yaz(t["eksik_dk"]) or "0",
            t["gec"], t["izin"], t["rapor"], t["ucretsiz"], t["gelmedi"], t["giris_yok"], t["cikis_yok"]])
    rows.append([])
    rows.append(["Kodlar"])
    rows += [[k or "(boş)", a] for k, a in d["kodlar"]]
    rows.append(["Fazla mesai", d["fm"]["aciklama"]])
    rows.append(["Ayarlar", f'Yöntem: {d["fm"]["yontem"]}; haftalık eşik: {d["fm"]["esik"]:g} saat; günlük mola: {d["fm"]["mola_dk"]} dk'])
    return b, rows


# ----------------------------------------------------------------- yillik izin bakiyesi
def _yil_ekle(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 Subat
        return d.replace(year=d.year + n, day=28)


def izin_hak_gun(kidem):
    """Tamamlanan hizmet yili basina yillik izin: 1-4. yil 14, 5-14. yil 20, 15+ 26 gun."""
    return 14 if kidem < 5 else 20 if kidem < 15 else 26


def izin_hak(ise, bugun):
    """-> (tamamlanan_yil, hak_edilen_gun, sonraki_tarih, sonraki_miktar)"""
    y = 0
    while _yil_ekle(ise, y + 1) <= bugun:
        y += 1
    return y, sum(izin_hak_gun(n) for n in range(1, y + 1)), _yil_ekle(ise, y + 1), izin_hak_gun(y + 1)


def izin_bakiye(kisi=None):
    if not ozellik("izin_bakiye"):
        raise ValueError("İzin bakiyesi kapalı (Ayarlar > Özellikler)")
    bugun = simdi().date()
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        son = aktif_kisiler(con, mc, per)
        kul, plan = {}, {}
        for r in con.execute("SELECT * FROM izin WHERE tur='Yıllık izin'"):
            k, gun = r["kisi_no"], dt.date.fromisoformat(r["tarih"])
            if r["bas_saat"]:
                g, c, _ = mc.al(k, gun)
                sure = (dk(c) - dk(g)) if c else 0
                x = min(1.0, (dk(r["bit_saat"]) - dk(r["bas_saat"])) / (sure if sure > 0 else 480))
            else:
                x = 1.0
            (kul if gun <= bugun else plan)[k] = (kul if gun <= bugun else plan).get(k, 0.0) + x
    out = []
    for k in sorted(set(per) | set(mc.per)):
        if kisi and k != int(kisi):
            continue
        pa = mc.per.get(k) or {}
        if pa.get("silindi") and not kisi:
            continue
        if not kisi and not (k in son or pa.get("ise_giris") or pa.get("izin_devir") is not None or k in kul):
            continue
        ise = pa.get("ise_giris")
        devir = float(pa.get("izin_devir") or 0)
        r = {"kisi_no": k, "ad": ad_soyad(per.get(k), k), "ise_giris": ise or "", "isten_cikis": pa.get("isten_cikis") or "",
             "ayrildi": bool(pa.get("isten_cikis") and pa["isten_cikis"] < bugun.isoformat()), "aktif": k in son,
             "devir": round(devir, 1), "kullanilan": round(kul.get(k, 0.0), 1), "planli": round(plan.get(k, 0.0), 1),
             "kidem_yil": 0, "kidem": "", "hak": 0, "sonraki_tarih": "", "sonraki_miktar": 0, "uyari": ""}
        if ise:
            d0 = dt.date.fromisoformat(ise)
            y, hak, sn_t, sn_m = izin_hak(d0, bugun)
            ay_ = (bugun.year - d0.year) * 12 + bugun.month - d0.month - (1 if bugun.day < d0.day else 0)
            r.update(kidem_yil=y, kidem=(f"{max(0, ay_) // 12} yıl {max(0, ay_) % 12} ay" if d0 <= bugun else "henüz başlamadı"),
                     hak=hak, sonraki_tarih=sn_t.isoformat(), sonraki_miktar=sn_m)
        else:
            r["uyari"] = "İşe giriş tarihi girin"
        r["kalan"] = round(r["devir"] + r["hak"] - r["kullanilan"], 1)
        out.append(r)
    out.sort(key=lambda x: x["ad"])
    return out


def ayar_izin_devir(js):
    k = int(js["kisi_no"])
    try:
        v = float(str(js.get("devir")).replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Devir bir sayı olmalı")
    if not -400 <= v <= 400:
        raise ValueError("Devir -400 ile 400 arasında olmalı")
    with db() as con:
        if not con.execute("SELECT 1 FROM personel WHERE kisi_no=?", (k,)).fetchone():
            raise ValueError("Personel bulunamadı")
        con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
        con.execute("UPDATE personel_ayar SET izin_devir=? WHERE kisi_no=?", (round(v, 1), k))
    return {"kisi_no": k, "devir": round(v, 1)}


# ----------------------------------------------------------------- onceki donemle karsilastirma
_KARS_ALANLAR = ("gec", "gec_dk", "erken", "erken_dk", "cikis_yok", "giris_yok", "gelmedi", "nobet_gelmedi")
_KARS_ETIKET = {"hafta": "geçen haftaya göre", "ay": "geçen aya göre", "gecen": "bir önceki aya göre", "ozel": "önceki döneme göre"}


def onceki_donem(tur, bas, bit):
    """Esdeger onceki donem: hafta -> gecen hafta (ayni gun sayisi), ay / gecen -> onceki ayin ayni gun araligi, ozel -> ayni uzunlukta onceki aralik."""
    b, e = dt.date.fromisoformat(bas), dt.date.fromisoformat(bit)
    if tur == "hafta":
        pb = b - dt.timedelta(days=7)
        return pb, pb + (e - b)
    if tur in ("ay", "gecen"):
        pb = (b.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        son_gun = (b.replace(day=1) - dt.timedelta(days=1))
        return pb, min(pb + (e - b), son_gun)
    uz = (e - b).days + 1
    pe = b - dt.timedelta(days=1)
    return pe - dt.timedelta(days=uz - 1), pe


def donem_toplam(bas, bit):
    t = dict.fromkeys(_KARS_ALANLAR, 0)
    for r in personel_ozeti(bas, bit):
        for a in _KARS_ALANLAR:
            t[a] += r[a]
    return t


def donem_karsilastir(tur, bas, bit):
    pb, pe = onceki_donem(tur, bas, bit)
    return {"etiket": _KARS_ETIKET.get(tur, _KARS_ETIKET["ozel"]), "onceki": {"bas": pb.isoformat(), "bit": pe.isoformat()},
            "simdi": donem_toplam(bas, bit), "onceki_toplam": donem_toplam(pb.isoformat(), pe.isoformat())}


# ----------------------------------------------------------------- kullanicilar / oturum (ozellik: giris)
OTURUM_SURE_SN = 12 * 3600
ROLLER = ("yonetici", "izleyici")
_PBKDF_TUR = 200000


def sifre_hashle(sifre, tuz_hex=None):
    tuz = bytes.fromhex(tuz_hex) if tuz_hex else secrets.token_bytes(16)
    return hashlib.pbkdf2_hmac("sha256", sifre.encode("utf-8"), tuz, _PBKDF_TUR).hex(), tuz.hex()


def _yonetici_sayisi(con):
    return con.execute("SELECT COUNT(*) FROM kullanici WHERE rol='yonetici'").fetchone()[0]


def kullanici_listesi():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT id, ad, rol, olusturma FROM kullanici ORDER BY ad COLLATE NOCASE")]


def kullanici_ekle(js):
    ad = " ".join(str(js.get("ad") or "").split())[:40]
    sifre, rol = str(js.get("sifre") or ""), js.get("rol") or "izleyici"
    if not ad:
        raise ValueError("Kullanıcı adı boş olamaz")
    if len(sifre) < 6:
        raise ValueError("Şifre en az 6 karakter olmalı")
    if rol not in ROLLER:
        raise ValueError("Rol yonetici ya da izleyici olmalı")
    h, tuz = sifre_hashle(sifre)
    with db() as con:
        if con.execute("SELECT 1 FROM kullanici WHERE lower(ad)=lower(?)", (ad,)).fetchone():
            raise ValueError(f"'{ad}' adında kullanıcı zaten var")
        kid = con.execute("INSERT INTO kullanici(ad,sifre_hash,tuz,rol,olusturma) VALUES(?,?,?,?,?)",
                          (ad, h, tuz, rol, zstr(simdi()))).lastrowid
    log.info("Kullanici eklendi: %s (%s)", ad, rol)
    return {"id": kid}


def _kullanici_getir(con, js):
    r = con.execute("SELECT * FROM kullanici WHERE id=?", (int(js.get("id") or 0),)).fetchone()
    if not r:
        raise ValueError("Kullanıcı bulunamadı")
    return r


def kullanici_sil(js):
    with db() as con:
        r = _kullanici_getir(con, js)
        if r["rol"] == "yonetici" and _yonetici_sayisi(con) <= 1:
            raise ValueError("Son yönetici silinemez")
        con.execute("DELETE FROM oturum WHERE kullanici_id=?", (r["id"],))
        con.execute("DELETE FROM kullanici WHERE id=?", (r["id"],))
    return {"ok": True}


def kullanici_sifre(js):
    sifre = str(js.get("sifre") or "")
    if len(sifre) < 6:
        raise ValueError("Şifre en az 6 karakter olmalı")
    h, tuz = sifre_hashle(sifre)
    with db() as con:
        r = _kullanici_getir(con, js)
        con.execute("UPDATE kullanici SET sifre_hash=?, tuz=? WHERE id=?", (h, tuz, r["id"]))
        con.execute("DELETE FROM oturum WHERE kullanici_id=?", (r["id"],))  # eski oturumlar kapanir
    return {"ok": True}


def kullanici_rol(js):
    rol = js.get("rol")
    if rol not in ROLLER:
        raise ValueError("Rol yonetici ya da izleyici olmalı")
    with db() as con:
        r = _kullanici_getir(con, js)
        if r["rol"] == "yonetici" and rol != "yonetici" and _yonetici_sayisi(con) <= 1:
            raise ValueError("Son yöneticinin rolü değiştirilemez")
        con.execute("UPDATE kullanici SET rol=? WHERE id=?", (rol, r["id"]))
    return {"ok": True}


def giris_yap(js):
    """-> (token, {ad, rol}). Basarisizsa 1 sn bekler (kaba kuvveti yavaslatir)."""
    ad, sifre = str(js.get("ad") or "").strip(), str(js.get("sifre") or "")
    with db() as con:
        r = con.execute("SELECT * FROM kullanici WHERE lower(ad)=lower(?)", (ad,)).fetchone()
    if r:
        h, _ = sifre_hashle(sifre, r["tuz"])
        if hmac.compare_digest(h, r["sifre_hash"]):
            token = secrets.token_urlsafe(32)
            simdi_s = int(time.time())
            with db() as con:
                con.execute("DELETE FROM oturum WHERE bitis < ?", (simdi_s,))
                con.execute("INSERT INTO oturum(token,kullanici_id,bitis) VALUES(?,?,?)", (token, r["id"], simdi_s + OTURUM_SURE_SN))
            log.info("Giris: %s", r["ad"])
            return token, {"ad": r["ad"], "rol": r["rol"]}
    else:
        sifre_hashle(sifre)  # kullanici var/yok zamanlamasi benzer olsun
    time.sleep(1)
    log.warning("Basarisiz giris denemesi: %s", ad[:40])
    raise ValueError("Kullanıcı adı veya şifre hatalı")


def oturum_kullanici(token):
    if not token:
        return None
    with db() as con:
        r = con.execute("SELECT k.id, k.ad, k.rol FROM oturum o JOIN kullanici k ON k.id=o.kullanici_id "
                        "WHERE o.token=? AND o.bitis >= ?", (token, int(time.time()))).fetchone()
    return dict(r) if r else None


def oturum_kapat(token):
    if token:
        with db() as con:
            con.execute("DELETE FROM oturum WHERE token=?", (token,))


def giris_kapat():
    """Kilitlenme onlemi: py panel.py --giris-kapat"""
    with db() as con:
        con.execute("INSERT INTO ayar(anahtar,deger) VALUES('ozellik_giris','0') ON CONFLICT(anahtar) DO UPDATE SET deger='0'")
        con.execute("DELETE FROM oturum")


GIRIS_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Personel Paneli - Giriş</title><style>
:root{--bg:#0d121c;--kart:#151c2a;--kart2:#1b2435;--cizgi:#26304a;--yazi:#e8ebf2;--soluk:#8e97ad;--ana:#5b8cff;--kirmizi:#f46a6a}
[data-tema=acik]{--bg:#eef1f7;--kart:#fff;--kart2:#f5f7fb;--cizgi:#e1e5ee;--yazi:#1c2333;--soluk:#6b7486;--ana:#3b6ef0;--kirmizi:#d64545}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;background:var(--bg);color:var(--yazi);font:14.5px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
form{width:min(360px,100%);background:var(--kart);border:1px solid var(--cizgi);border-radius:14px;padding:24px;display:grid;gap:12px}
h1{margin:0 0 4px;font-size:19px}label{display:grid;gap:4px;font-size:12px;color:var(--soluk)}
input{font:inherit;font-size:15px;padding:10px 12px;border:1px solid var(--cizgi);border-radius:8px;background:var(--kart2);color:var(--yazi);width:100%}
input:focus{outline:2px solid var(--ana);outline-offset:-1px}
button{font:inherit;font-weight:600;padding:11px;border:0;border-radius:8px;background:var(--ana);color:#fff;cursor:pointer}
#h{color:var(--kirmizi);min-height:1.3em;font-size:13px}</style></head><body>
<form id="f"><h1>Personel Paneli</h1><div style="color:var(--soluk)">Devam etmek için giriş yapın.</div>
<label>Kullanıcı adı<input id="ad" autocomplete="username" autofocus required></label>
<label>Şifre<input id="sifre" type="password" autocomplete="current-password" required></label>
<div id="h"></div><button>Giriş yap</button></form>
<script>try{document.documentElement.dataset.tema=localStorage.getItem("tema")||"koyu"}catch(e){document.documentElement.dataset.tema="koyu"}
document.getElementById("f").onsubmit=async e=>{e.preventDefault();const h=document.getElementById("h");h.textContent="";
try{const r=await fetch("/api/giris",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({ad:document.getElementById("ad").value,sifre:document.getElementById("sifre").value})});
const j=await r.json().catch(()=>({}));if(r.ok)location.reload();else h.textContent=j.hata||"Giriş yapılamadı"}catch(x){h.textContent="Sunucuya ulaşılamıyor"}};</script></body></html>"""


# ----------------------------------------------------------------- telegram bildirimleri (ozellik: bildirim)
TG_VARSAYILAN = {"tg_sabah_ac": "1", "tg_sabah_saat": "09:45", "tg_aksam_ac": "1", "tg_aksam_saat": "20:30",
                 "tg_cihaz_ac": "1", "tg_cihaz_dk": "60"}
TG_PENCERE_DK = 180  # ozet planli saatten sonra en fazla bu kadar gec gonderilir


def ayar_oku(con, anahtar, varsayilan=""):
    r = con.execute("SELECT deger FROM ayar WHERE anahtar=?", (anahtar,)).fetchone()
    return r[0] if r and r[0] is not None else varsayilan


def ayar_yaz(con, anahtar, deger):
    con.execute("INSERT INTO ayar(anahtar,deger) VALUES(?,?) ON CONFLICT(anahtar) DO UPDATE SET deger=excluded.deger", (anahtar, str(deger)))


def _maske(token):
    return ("•" * 8 + token[-4:]) if token else ""


def bildirim_ayarlari():
    """API'ye giden ayarlar: token ASLA geri donmez, sadece maskelenmis hali."""
    with db() as con:
        a = {k: ayar_oku(con, k, v) for k, v in TG_VARSAYILAN.items()}
        token, chat, son = ayar_oku(con, "tg_token"), ayar_oku(con, "tg_chat"), ayar_oku(con, "tg_son_durum")
    try:
        son = json.loads(son) if son else None
    except ValueError:
        son = None
    return {"token": _maske(token), "token_var": bool(token), "chat": chat,
            "sabah_ac": a["tg_sabah_ac"] == "1", "sabah_saat": a["tg_sabah_saat"],
            "aksam_ac": a["tg_aksam_ac"] == "1", "aksam_saat": a["tg_aksam_saat"],
            "cihaz_ac": a["tg_cihaz_ac"] == "1", "cihaz_dk": int(a["tg_cihaz_dk"]), "son_durum": son}


def bildirim_kaydet(js):
    yaz = {}
    tok = str(js.get("token") or "").strip()
    if js.get("token_sil"):
        yaz["tg_token"] = ""
    elif tok and not tok.startswith("•"):
        if not re.match(r"^\d+:[\w-]{20,}$", tok):
            raise ValueError("Bot token biçimi hatalı (örn. 123456789:ABC...)")
        yaz["tg_token"] = tok
    if "chat" in js:
        chats = [c.strip() for c in str(js["chat"]).split(",") if c.strip()]
        for c in chats:
            if not re.match(r"^(-?\d+|@\w+)$", c):
                raise ValueError(f"Geçersiz chat id: {c}")
        yaz["tg_chat"] = ",".join(chats)
    for ad in ("sabah", "aksam"):
        if f"{ad}_ac" in js:
            yaz[f"tg_{ad}_ac"] = "1" if js[f"{ad}_ac"] else "0"
        if f"{ad}_saat" in js:
            yaz[f"tg_{ad}_saat"] = saat_temiz(js[f"{ad}_saat"]) or TG_VARSAYILAN[f"tg_{ad}_saat"]
    if "cihaz_ac" in js:
        yaz["tg_cihaz_ac"] = "1" if js["cihaz_ac"] else "0"
    if "cihaz_dk" in js:
        dkv = int(js["cihaz_dk"] or 0)
        if not 5 <= dkv <= 1440:
            raise ValueError("Cihaz uyarı süresi 5-1440 dakika olmalı")
        yaz["tg_cihaz_dk"] = str(dkv)
    with db() as con:
        for k, v in yaz.items():
            ayar_yaz(con, k, v)
    return bildirim_ayarlari()


def telegram_gonder(metin, token, chatler):
    """-> (basarili_sayisi, [hata]). Token hicbir hata mesajina/loga yazilmaz."""
    api = str(CFG.get("telegram_api") or "https://api.telegram.org").rstrip("/")
    ok, hatalar = 0, []
    for chat in chatler:
        try:
            req = urllib.request.Request(f"{api}/bot{token}/sendMessage", method="POST",
                                         data=json.dumps({"chat_id": chat, "text": metin[:4000]}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                yanit = json.loads(r.read().decode("utf-8", "replace") or "{}")
            if yanit.get("ok") is False:
                raise RuntimeError(yanit.get("description") or "Telegram hata döndürdü")
            ok += 1
        except urllib.error.HTTPError as e:
            try:
                ayr = json.loads(e.read().decode("utf-8", "replace")).get("description", "")
            except Exception:
                ayr = ""
            hatalar.append(f"{chat}: HTTP {e.code} {ayr}".strip())
        except Exception as e:
            hatalar.append(f"{chat}: {e}")
    hatalar = [h.replace(token, "***") for h in hatalar]
    for h in hatalar:
        log.warning("Telegram gonderilemedi: %s", h[:300])
    return ok, hatalar


def _bildirim_hazir():
    with db() as con:
        token, chat = ayar_oku(con, "tg_token"), ayar_oku(con, "tg_chat")
        acik = ozellik_haritasi(con)["bildirim"]
    return acik, token, [c for c in chat.split(",") if c]


def _durum_kaydet(tur, ok, mesaj):
    with db() as con:
        ayar_yaz(con, "tg_son_durum", json.dumps({"zaman": zstr(simdi()), "tur": tur, "ok": bool(ok), "mesaj": mesaj[:300]}, ensure_ascii=False))


def _adlar(liste, f, sinir=40):
    parca = [f(x) for x in liste[:sinir]]
    return ", ".join(parca) + (f" (+{len(liste) - sinir} kişi daha)" if len(liste) > sinir else "")


def _tarih_baslik(g):
    return f'{g[8:10]}.{g[5:7]}.{g[:4]} {["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"][dt.date.fromisoformat(g).weekday()]}'


def sabah_ozeti():
    b = bugun_ozeti()
    s = [f'Sabah özeti - {_tarih_baslik(b["tarih"])}', f'Gelen: {len(b["satirlar"])}, içeride: {len(b["icerde"])}']
    s.append(f'Geç kalanlar ({len(b["gec"])}): ' + (_adlar(b["gec"], lambda x: f'{x["ad"]} ({sure_yaz(x["gec_dk"])})') or "yok"))
    s.append(f'Gelmeyenler ({len(b["gelmeyen"])}): ' + (_adlar(b["gelmeyen"], lambda x: x["ad"]) or "yok"))
    if b["izinli"]:
        s.append(f'Bugün izinli ({len(b["izinli"])}): ' + _adlar(b["izinli"], lambda x: f'{x["ad"]} ({x["izin"]})'))
    return "\n".join(s)


def aksam_ozeti():
    b = bugun_ozeti()
    cikissiz = [x for x in b["satirlar"] if x["durum"] in ("ÇIKIŞ YOK", "İÇERİDE")]
    s = [f'Akşam özeti - {_tarih_baslik(b["tarih"])}',
         f'Gelen: {len(b["satirlar"])}, geç kalan: {len(b["gec"])}, erken çıkan: {len(b["erken"])}, gelmeyen: {len(b["gelmeyen"])}',
         f'Çıkış okutmayanlar ({len(cikissiz)}): ' + (_adlar(cikissiz, lambda x: x["ad"]) or "yok"),
         f'Giriş okutmayanlar ({len(b["giris_yok"])}): ' + (_adlar(b["giris_yok"], lambda x: x["ad"]) or "yok")]
    for n in b["nobet"]:
        gel = [x["ad"] for x in n["satirlar"] if x["durum"] == "GELMEDİ"]
        s.append(f'Nöbet: {n["geldi"]}/{n["planli"]} geldi' + (f'; gelmeyen: {", ".join(gel)}' if gel else ""))
    return "\n".join(s)


def bildirim_test():
    acik, token, chatler = _bildirim_hazir()
    if not token or not chatler:
        raise ValueError("Önce bot token ve chat id kaydedin")
    ok, hatalar = telegram_gonder(f"Test mesajı - Personel Paneli v{SURUM} ({zstr(simdi())})", token, chatler)
    _durum_kaydet("test", ok and not hatalar, "; ".join(hatalar) or f"{ok} alıcıya gönderildi")
    if hatalar:
        raise ValueError("Gönderilemedi: " + "; ".join(hatalar))
    return {"ok": True, "gonderilen": ok}


def bildirim_kontrol():
    """Toplama dongusunde her turda cagrilir; asla exception firlatmaz."""
    try:
        acik, token, chatler = _bildirim_hazir()
        if not (acik and token and chatler):
            return
        an = simdi()
        bugun = an.date().isoformat()
        with db() as con:
            mc = MesaiCozucu(con)
            a = {k: ayar_oku(con, k, v) for k, v in TG_VARSAYILAN.items()}
            son = {t: ayar_oku(con, f"tg_son_{t}") for t in ("sabah", "aksam")}
        gun_uygun = an.weekday() != 6 and not mc.kapali_mi(an.date())
        simdi_dk = an.hour * 60 + an.minute
        for tur, yap in (("sabah", sabah_ozeti), ("aksam", aksam_ozeti)):
            if a[f"tg_{tur}_ac"] == "1" and gun_uygun and son[tur] != bugun and dk(a[f"tg_{tur}_saat"]) <= simdi_dk <= dk(a[f"tg_{tur}_saat"]) + TG_PENCERE_DK:
                ok, hatalar = telegram_gonder(yap(), token, chatler)
                _durum_kaydet(tur, ok and not hatalar, "; ".join(hatalar) or f"{ok} alıcıya gönderildi")
                if ok:  # en az bir alici aldiysa bugun icin tamam
                    with db() as con:
                        ayar_yaz(con, f"tg_son_{tur}", bugun)
        if a["tg_cihaz_ac"] == "1":
            son_ok = son_turlar(1, True)
            if son_ok:
                fark = (an - dt.datetime.strptime(son_ok[0]["zaman"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
                with db() as con:
                    durum, son_u = ayar_oku(con, "tg_cihaz_durum", "ok"), float(ayar_oku(con, "tg_cihaz_son", "0") or 0)
                if fark > int(a["tg_cihaz_dk"]):
                    if durum != "bozuk" or an.timestamp() - son_u >= 3 * 3600:
                        ok, hatalar = telegram_gonder(f'Cihaz uyarısı: son başarılı okuma {int(fark)} dakika önce ({son_ok[0]["zaman"][11:16]}). '
                                                      "Giriş-çıkışlar panelde görünmeyebilir.", token, chatler)
                        _durum_kaydet("cihaz", ok and not hatalar, "; ".join(hatalar) or "cihaz uyarısı gönderildi")
                        if ok:
                            with db() as con:
                                ayar_yaz(con, "tg_cihaz_durum", "bozuk")
                                ayar_yaz(con, "tg_cihaz_son", an.timestamp())
                elif durum == "bozuk":
                    ok, hatalar = telegram_gonder("Cihaz tekrar okunuyor.", token, chatler)
                    _durum_kaydet("cihaz", ok and not hatalar, "; ".join(hatalar) or "cihaz düzeldi mesajı gönderildi")
                    if ok:
                        with db() as con:
                            ayar_yaz(con, "tg_cihaz_durum", "ok")
    except Exception:
        log.exception("Bildirim kontrolu hatasi (devam ediliyor)")


# ----------------------------------------------------------------- personel yonetimi (isim, ekle, sil)
def _pers_log(con, islem, kisi_no, eski, yeni, istemci):
    con.execute("INSERT INTO personel_log(zaman,islem,kisi_no,eski,yeni,istemci) VALUES(?,?,?,?,?,?)",
                (zstr(simdi()), islem, kisi_no, str(eski or "")[:200], str(yeni or "")[:200], str(istemci or "")[:60]))


def _personel_getir(con, k):
    r = con.execute("SELECT p.*, a.gorunen_ad AS gorunen_ad, a.silindi AS silindi FROM personel p "
                    "LEFT JOIN personel_ayar a ON a.kisi_no = p.kisi_no WHERE p.kisi_no=?", (k,)).fetchone()
    if not r:
        raise ValueError("Personel bulunamadı")
    return r


def _kisi_no(js):
    try:
        k = int(str(js["kisi_no"]).strip())
    except (KeyError, TypeError, ValueError):
        raise ValueError("Kart no pozitif bir tam sayı olmalı")
    if k <= 0:
        raise ValueError("Kart no pozitif bir tam sayı olmalı")
    return k


def personel_liste(silinenler=False):
    bugun = simdi().date().isoformat()
    with db() as con:
        per = personel_sozluk(con)
        mc = MesaiCozucu(con)
        son = son_okutmalar(con)
        akt = aktif_kisiler(con, mc, per)
    out = []
    for k, p in per.items():
        if p["silindi"] and not silinenler:
            continue
        pa = mc.per.get(k) or {}
        dbf = f'{p["adi"]} {p["soyadi"]}'.strip()
        gor = (p["gorunen_ad"] or "").strip()
        kat = mc.kat.get(pa.get("kategori_id")) or {}
        out.append({"kisi_no": k, "kartno": p["kartno"], "kart": kart_no(p, k), "ad": ad_ham(p, k), "dbf_ad": dbf, "gorunen_ad": gor,
                    "farkli": bool(gor and p["kaynak"] != "elle" and gor != dbf), "kaynak": "elle" if p["kaynak"] == "elle" else "dbf",
                    "kategori_id": pa.get("kategori_id"), "kategori": kat.get("ad", ""), "son_okutma": son.get(k) or "",
                    "aktif": k in akt, "silindi": bool(p["silindi"]),
                    "ayrildi": bool(pa.get("isten_cikis") and pa["isten_cikis"] < bugun)})
    out.sort(key=lambda x: x["ad"])
    return out


def tanimsiz_numaralar():
    """Okutmasi olan ama personel tablosunda olmayan cihaz numaralari."""
    with db() as con:
        return [{"kisi_no": r[0], "kart": f"{r[0]:04d}", "sayi": r[1], "ilk": r[2], "son": r[3]} for r in con.execute(
            "SELECT kisi_no, COUNT(*), MIN(zaman), MAX(zaman) FROM okutma WHERE gizli=0 AND kisi_no NOT IN (SELECT kisi_no FROM personel) "
            "GROUP BY kisi_no ORDER BY MAX(zaman) DESC")]


def personel_ekle(js, istemci=""):
    k = _kisi_no(js)
    ad = " ".join(str(js.get("ad") or "").split())[:80]
    if not ad:
        raise ValueError("Ad soyad zorunlu")
    with db() as con:
        var = con.execute("SELECT p.*, a.gorunen_ad AS gorunen_ad, a.silindi AS silindi FROM personel p "
                          "LEFT JOIN personel_ayar a ON a.kisi_no = p.kisi_no WHERE p.kisi_no=?", (k,)).fetchone()
        if var:
            if var["silindi"]:
                raise ValueError(f"Bu numara silinmiş bir kişiye ait ({ad_ham(var, k)}). Listede \"Silinenleri göster\" ile bulup \"Geri al\" yapabilirsiniz.")
            raise ValueError(f"Bu numara {ad_ham(var, k)} kişisine ait")
        con.execute("INSERT INTO personel(kisi_no,kartno,adi,soyadi,sicilno,guncelleme,kaynak) VALUES(?,?,?,?,?,?,'elle')",
                    (k, f"{k:04d}", ad, "", "", zstr(simdi())))
        con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
        con.execute("UPDATE personel_ayar SET gorunen_ad=? WHERE kisi_no=?", (ad, k))  # DBF'ye sonradan eklense de bu ad gorunur
        _pers_log(con, "ekle", k, "", ad, istemci)
    log.info("Personel eklendi: %s %s istemci=%s", k, ad, istemci)
    return {"kisi_no": k}


def personel_duzenle(js, istemci=""):
    k = _kisi_no(js)
    with db() as con:
        p = _personel_getir(con, k)
        eski = ad_ham(p, k)
        con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
        if "gorunen_ad" in js:
            ad = " ".join(str(js.get("gorunen_ad") or "").split())[:80]
            con.execute("UPDATE personel_ayar SET gorunen_ad=? WHERE kisi_no=?", (ad or None, k))
        if "kategori_id" in js:
            kid = js["kategori_id"]
            kid = int(kid) if kid not in (None, "", 0) else None
            if kid and not con.execute("SELECT 1 FROM kategori WHERE id=?", (kid,)).fetchone():
                raise ValueError("Kategori bulunamadı")
            con.execute("UPDATE personel_ayar SET kategori_id=? WHERE kisi_no=?", (kid, k))
        yeni = ad_ham(_personel_getir(con, k), k)
        _pers_log(con, "duzenle", k, eski, yeni, istemci)
    return {"kisi_no": k}


def personel_isten_ayrildi(js, istemci=""):
    """Isten cikis tarihini yazar (varsayilan: son okutma gunu). Kisi aktiflikten cikar; kategori uyeligi kalir."""
    k = _kisi_no(js)
    tarih = tarih_temiz(js.get("tarih"))
    if not tarih:
        raise ValueError("Çıkış tarihi gerekli")
    with db() as con:
        p = _personel_getir(con, k)
        con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
        ise, eski = con.execute("SELECT ise_giris, isten_cikis FROM personel_ayar WHERE kisi_no=?", (k,)).fetchone()
        if ise and tarih < ise:
            raise ValueError("İşten çıkış tarihi işe girişten önce olamaz")
        con.execute("UPDATE personel_ayar SET isten_cikis=?, uyari_erteleme=NULL WHERE kisi_no=?", (tarih, k))
        _pers_log(con, "isten_cikis", k, eski or "", tarih, istemci)
    log.info("Isten cikis: %s %s istemci=%s", k, tarih, istemci)
    return {"kisi_no": k, "isten_cikis": tarih}


def personel_uyari_ertele(js, istemci=""):
    """'Uzun suredir okutma yok' uyarisini N gun (varsayilan 7) listeden gizler."""
    k = _kisi_no(js)
    try:
        n = max(1, min(90, int(js.get("gun") or 7)))
    except (TypeError, ValueError):
        raise ValueError("Gün sayısı geçersiz")
    bitis = (simdi().date() + dt.timedelta(days=n)).isoformat()
    with db() as con:
        p = _personel_getir(con, k)
        con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
        con.execute("UPDATE personel_ayar SET uyari_erteleme=? WHERE kisi_no=?", (bitis, k))
        _pers_log(con, "uyari_ertele", k, "", bitis, istemci)
    return {"kisi_no": k, "uyari_erteleme": bitis}


def _personel_bayrak(js, istemci, silindi):
    k = _kisi_no(js)
    with db() as con:
        p = _personel_getir(con, k)
        con.execute("INSERT OR IGNORE INTO personel_ayar(kisi_no) VALUES(?)", (k,))
        con.execute("UPDATE personel_ayar SET silindi=? WHERE kisi_no=?", (1 if silindi else 0, k))
        _pers_log(con, "sil" if silindi else "geri_al", k, ad_ham(p, k), ad_ham(p, k), istemci)
    return {"kisi_no": k, "silindi": silindi}


def personel_sil(js, istemci=""):
    return _personel_bayrak(js, istemci, True)


def personel_geri_al(js, istemci=""):
    return _personel_bayrak(js, istemci, False)


# ----------------------------------------------------------------- ana sayfa (Bugun) kartlari
_ONBELLEK = {}
_ONBELLEK_SN = 90


def _onbellekli(anahtar, f):
    t = time.time()
    v = _ONBELLEK.get(anahtar)
    if v and t - v[0] < _ONBELLEK_SN:
        return v[1]
    r = f()
    _ONBELLEK[anahtar] = (t, r)
    return r


def _ay_ozeti(g):
    with db() as con:
        per = personel_sozluk(con)
    ozet = [x for x in personel_ozeti(g[:8] + "01", g) if x["kisi_no"] in per]  # tanimsiz numaralar ayri kartta

    def ilk(a, n=5):
        return [{"kisi_no": x["kisi_no"], "kart": x["kart"], "ad": x["ad"], "adet": x[a], "dk": x[a + "_dk"] if a + "_dk" in x else 0}
                for x in sorted((x for x in ozet if x[a] > 0), key=lambda x: (-x[a], -x.get(a + "_dk", 0), x["ad"]))[:n]]
    for x in ozet:
        x["eksik"] = x["giris_yok"] + x["cikis_yok"]  # okutma eksigi: giris okutmayan + cikis okutmayan gun
    e = ilk("eksik")
    by = {x["kisi_no"]: x for x in ozet}
    for r in e:
        r["giris_yok"], r["cikis_yok"] = by[r["kisi_no"]]["giris_yok"], by[r["kisi_no"]]["cikis_yok"]
    return {"gec": ilk("gec"), "eksik": e}


def ana_ozet():
    """Bugun sayfasinin alt kartlari: dikkat edilecekler, kategoriye gore durum, yaklasanlar, canli akis, bu ayin ozeti."""
    an = simdi()
    bugun = an.date()
    g = bugun.isoformat()
    b = bugun_ozeti()
    with db() as con:
        mc = MesaiCozucu(con)
        per = personel_sozluk(con)
        son14 = (bugun - dt.timedelta(days=14)).isoformat()
        gorulen = {r[0] for r in con.execute("SELECT DISTINCT kisi_no FROM okutma WHERE gizli=0 AND zaman >= ?", (son14,))}
        akt = aktif_kisiler(con, mc, per)
        nobetliler = ({r[0] for r in con.execute("SELECT kisi_no FROM nobet WHERE tarih=?", (g,))} if mc.oz["nobet"] else set())
        son_gun = con.execute("SELECT MAX(substr(zaman,1,10)) FROM okutma WHERE gizli=0 AND zaman < ?", (g,)).fetchone()[0]
        izin_gruplari = []
        if mc.oz["izin"]:
            ileri = (bugun + dt.timedelta(days=7)).isoformat()
            izin_gruplari = [dict(r) for r in con.execute(
                "SELECT COALESCE(grup, 't' || id) AS gr, kisi_no, MIN(tarih) AS bas, MAX(tarih) AS bit, COUNT(*) AS gun, tur, bas_saat, bit_saat, aciklama "
                "FROM izin GROUP BY gr, kisi_no HAVING (MIN(tarih) BETWEEN ? AND ?) OR (MAX(tarih) BETWEEN ? AND ?) ORDER BY MIN(tarih)", (g, ileri, g, ileri))]
        cts_nobet = []
        cts_tarih = None
        if mc.oz["nobet"] and bugun.weekday() != 5:
            cts_tarih = (bugun + dt.timedelta(days=(5 - bugun.weekday()) % 7)).isoformat()
            cts_nobet = [r[0] for r in con.execute("SELECT kisi_no FROM nobet WHERE tarih=?", (cts_tarih,))]
    sil = lambda k: bool(per.get(k) and per[k]["silindi"])
    # --- dikkat edilecekler
    dun, dun_giris = [], []
    if son_gun:
        for r in gunluk_rapor(son_gun, son_gun):
            if r["silindi"] or r["kisi_no"] not in per:
                continue
            m = mc.mesai(r["kisi_no"], dt.date.fromisoformat(son_gun))
            if r["durum"] == "ÇIKIŞ YOK":
                dun.append({"kisi_no": r["kisi_no"], "kart": r["kart"], "ad": r["ad"], "tarih": son_gun, "giris": r["giris"], "mesai_cikis": m[1]})
            elif r["durum"] == "GİRİŞ YOK":
                dun_giris.append({"kisi_no": r["kisi_no"], "kart": r["kart"], "ad": r["ad"], "tarih": son_gun, "cikis": r["cikis"], "mesai_giris": m[0]})
        dun.sort(key=lambda x: x["ad"])
        dun_giris.sort(key=lambda x: x["ad"])
    uzun_hepsi = uzun_okutmayanlar(True)
    uzun = [u for u in uzun_hepsi if not u["ertelendi"]]
    tanimsiz = [t for t in tanimsiz_numaralar() if t["son"][:10] >= son14]
    izinler = []
    for r in izin_gruplari:
        if sil(r["kisi_no"]):
            continue
        izinler.append({"kisi_no": r["kisi_no"], "kart": kart_no(per.get(r["kisi_no"]), r["kisi_no"]), "ad": ad_soyad(per.get(r["kisi_no"]), r["kisi_no"]),
                        "bas": r["bas"], "bit": r["bit"], "gun": r["gun"], "tur": r["tur"] or "İzin",
                        "saatlik": f'{r["bas_saat"]}-{r["bit_saat"]}' if r["bas_saat"] else "",
                        "baslayan": g <= r["bas"] <= (bugun + dt.timedelta(days=7)).isoformat(),
                        "biten": g <= r["bit"] <= (bugun + dt.timedelta(days=7)).isoformat()})
    # --- kategoriye gore
    # beklenen: aktif kisilerden kategori uyeleri (14 gun sarti yok) + son 14 gunde okutanlar, bugun beklenen_gun_mu olanlar
    aday = {k for k in akt if k in per and ((mc.per.get(k) or {}).get("kategori_id") in mc.kat or k in gorulen)}
    beklenen = {k for k in aday if mc.beklenen_gun_mu(k, bugun, k in nobetliler)}
    gelen = {x["kisi_no"] for x in b["satirlar"] if not x["silindi"]}
    icerde = {x["kisi_no"] for x in b["icerde"] if not x["silindi"]}
    kat = {}
    for k in beklenen | gelen | {k for k in akt if k in per}:
        ad = mc.kategori_adi(k)
        d = kat.setdefault(ad, {"kategori": ad, "ad": ad or "Kategorisiz", "beklenen": 0, "gelen": 0, "icerde": 0, "toplam": 0,
                                "uzun": 0, "uzun_esik": mc.genel["uzun_okutmama_gun"]})
        d["beklenen"] += 1 if k in beklenen else 0
        d["gelen"] += 1 if k in gelen else 0
        d["icerde"] += 1 if k in icerde else 0
        d["toplam"] += 1 if k in akt else 0
    for u in uzun_hepsi:
        if u["kategori"] in kat:
            kat[u["kategori"]]["uzun"] += 1
    kategori = sorted(kat.values(), key=lambda x: (x["kategori"] == "", x["ad"]))
    # --- yaklasanlar
    nobet = None
    if cts_tarih:
        nobet = {"tarih": cts_tarih, "kisiler": sorted(({"kisi_no": k, "kart": kart_no(per.get(k), k), "ad": ad_soyad(per.get(k), k), "kategori": mc.kategori_adi(k)}
                                                       for k in cts_nobet if not sil(k)), key=lambda x: x["ad"])}
    # --- canli akis: son 10 okutma, giris/cikis ayiklanmis siradan
    with db() as con:
        gun = g if con.execute("SELECT 1 FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman <= ? LIMIT 1", (g + " 00:00", g + " 23:59")).fetchone() else (
            con.execute("SELECT MAX(substr(zaman,1,10)) FROM okutma WHERE gizli=0").fetchone()[0] or g)
        zl = {}
        for r in con.execute("SELECT kisi_no, zaman FROM okutma WHERE gizli=0 AND zaman >= ? AND zaman <= ? ORDER BY zaman", (gun + " 00:00", gun + " 23:59")):
            zl.setdefault(r[0], []).append(r[1])
    olaylar = []
    for k, z in zl.items():
        for i, t in enumerate(ayikla(z)):
            olaylar.append({"zaman": t.strftime("%Y-%m-%d %H:%M"), "saat": t.strftime("%H:%M"), "kisi_no": k, "kart": kart_no(per.get(k), k),
                            "ad": ad_soyad(per.get(k), k), "etiket": "giriş" if i % 2 == 0 else "çıkış"})
    olaylar.sort(key=lambda x: (x["zaman"], x["kisi_no"]), reverse=True)
    return {"tarih": g,
            "dikkat": {"dun_tarih": son_gun, "dun_cikis_yok": dun, "dun_giris_yok": dun_giris, "uzun": uzun, "uzun_esik": mc.genel["uzun_okutmama_gun"],
                       "gelmeyen": b["gelmeyen"], "tanimsiz": tanimsiz, "izinler": izinler},
            "kategori": kategori,
            "yaklasan": {"nobet": nobet, "izinler": [x for x in izinler if g <= x["bas"] <= (bugun + dt.timedelta(days=7)).isoformat() or x["bas"] < g <= x["bit"]]},
            "akis": {"tarih": gun, "olaylar": olaylar[:10]},
            "ay": _onbellekli(("ay", g), lambda: _ay_ozeti(g))}


def kisi_ara(q):
    """Hizli arama: kart no (81, 0081) ya da isim parcasi. En fazla 8 sonuc; tam kart no eslesmesi ilk sirada."""
    q = " ".join(str(q or "").split())[:40]
    if not q:
        return {"sonuc": [], "tanimsiz": None}
    rakam = q.isdigit()
    n = int(q) if rakam else 0
    qk = katla(q)
    with db() as con:
        per = personel_sozluk(con)
        mc = MesaiCozucu(con)
    sonuc = []
    for k, p in per.items():
        skor = None
        if rakam and n > 0:
            if k == n:
                skor = 0
            elif str(k).startswith(str(n)):
                skor = 1
        if skor is None and qk and qk in katla(f'{ad_ham(p, k)} {p["adi"]} {p["soyadi"]}'):
            skor = 2
        if skor is not None:
            sonuc.append((skor, bool(p["silindi"]), ad_ham(p, k), {"kisi_no": k, "kart": kart_no(p, k), "ad": ad_soyad(p, k),
                                                                "kategori": mc.kategori_adi(k), "silindi": bool(p["silindi"])}))
    sonuc.sort(key=lambda x: x[:3])
    return {"sonuc": [x[3] for x in sonuc[:8]], "tanimsiz": n if rakam and n > 0 and n not in per else None,
            "tanimsiz_kart": f"{n:04d}" if rakam and n > 0 and n not in per else None}


# ----------------------------------------------------------------- otomatik guncelleme
# Guncellenen dosyalar: sadece kod ve belgeler. config.json, data/, port.txt, kisayollar, gorev ve guvenlik duvari ASLA degismez.
GUNC_DOSYALAR = ("panel.py", "index.html", "collector.ps1", "baslat.pyw", "panel.ico", "DEGISIKLIKLER.md", "README.md")
_gunc_onbellek = {"zaman": 0.0, "veri": None}
_gunc_kilit = threading.Lock()
SRV = None  # calisan HTTP sunucusu (yeniden baslatma icin)


def surum_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


GUNC_KILIT_SN = 60  # guncelleme basarili dondukten sonra surec cikmazsa toplayici kilidi bu sure sonra birakilir
_gunc_kilit_tutuyor = False


def _gunc_sha():
    """Dalin su anki commit SHA'si (40 hane). Alinamazsa exception: dal adresine geri dusulmez."""
    url = str(CFG["guncelleme_api"]).replace("{dal}", str(CFG["guncelleme_dali"]))
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "EnGoPerPanel-guncelleme"})
    with urllib.request.urlopen(req, timeout=8) as r:
        sha = str(json.loads(r.read().decode("utf-8", "replace")).get("sha") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", sha):
        raise ValueError("Commit SHA okunamadı")
    return sha


def _gunc_indir(dosya, sha, zaman_asimi=8):
    url = f'{str(CFG["guncelleme_taban"]).rstrip("/")}/{sha}/{dosya}'
    req = urllib.request.Request(url, headers={"User-Agent": "EnGoPerPanel-guncelleme"})
    with urllib.request.urlopen(req, timeout=zaman_asimi) as r:
        return r.read()


def kurulu_sha():
    try:
        with open(os.path.join(CFG["veri_klasoru"], "kurulu_sha.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _gunc_kayit():
    try:
        with open(os.path.join(CFG["veri_klasoru"], "guncelleme.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _gunc_kayit_yaz(d):
    with open(os.path.join(CFG["veri_klasoru"], "guncelleme.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def otomatik_guncelleme_acik():
    with db() as con:
        v = ayar_oku(con, "otomatik_guncelleme", "")
    return (v == "1") if v != "" else bool(CFG.get("otomatik_guncelleme", True))


def _yenilik_bolumleri(metin, mevcut):
    """DEGISIKLIKLER.md: mevcut surumden yeni olan '## x.y.z' bolumleri."""
    out, al = [], False
    for satir in metin.splitlines():
        if satir.startswith("## "):
            al = surum_tuple(satir[3:]) > surum_tuple(mevcut)
        if al:
            out.append(satir)
    return "\n".join(out).strip()


def guncelleme_bilgi(zorla=False):
    t = time.time()
    if not zorla and _gunc_onbellek["veri"] and t - _gunc_onbellek["zaman"] < (600 if not _gunc_onbellek["veri"]["hata"] else 60):
        v = dict(_gunc_onbellek["veri"])
    else:
        v = {"mevcut": SURUM, "uzak": "", "var": False, "hata": "", "notlar": "", "sha": "", "sha_tam": ""}
        try:
            sha = _gunc_sha()
            v["sha_tam"], v["sha"] = sha, sha[:7]
            kod = _gunc_indir("panel.py", sha).decode("utf-8", "replace")
            m = re.search(r'^SURUM\s*=\s*"([^"]+)"', kod, re.M)
            if not m:
                raise ValueError("Uzak panel.py içinde SURUM bulunamadı")
            v["uzak"] = m.group(1)
            v["var"] = surum_tuple(v["uzak"]) > surum_tuple(SURUM)
            if v["var"]:
                try:
                    v["notlar"] = _yenilik_bolumleri(_gunc_indir("DEGISIKLIKLER.md", sha).decode("utf-8", "replace"), SURUM)
                except Exception:
                    v["notlar"] = ""
        except Exception as e:
            v["hata"] = f"Güncelleme sunucusuna ulaşılamadı: {e}"[:300]
            v["var"] = False
        _gunc_onbellek.update(zaman=t, veri=dict(v))
    v["otomatik"] = otomatik_guncelleme_acik()
    v["son"] = _gunc_kayit()
    v["kurulu_sha"] = kurulu_sha()[:7]
    return v


def ayar_guncelleme(js):
    with db() as con:
        ayar_yaz(con, "otomatik_guncelleme", "1" if js.get("otomatik") else "0")
    return {"otomatik": bool(js.get("otomatik"))}


def yenilikler():
    try:
        with open(os.path.join(KOK, "DEGISIKLIKLER.md"), encoding="utf-8") as f:
            satirlar = f.read().splitlines()
    except OSError:
        return {"surum": SURUM, "metin": ""}
    out, al = [], False
    for satir in satirlar:
        if satir.startswith("## "):
            al = surum_tuple(satir[3:]) == surum_tuple(SURUM)
        if al:
            out.append(satir)
    return {"surum": SURUM, "metin": "\n".join(out).strip()}


def guncelleme_uygula():
    """Uzak surum yeniyse: DB yedegi, indir+dogrula, kod yedegi, yerine koy. Hata olursa hicbir dosya degismez / eskisi geri yuklenir."""
    b = guncelleme_bilgi(zorla=True)
    if b["hata"]:
        raise ValueError(b["hata"])
    if not b["var"]:
        return {"guncel": True, "mevcut": SURUM, "uzak": b["uzak"]}
    if not _gunc_kilit.acquire(blocking=False):
        raise ValueError("Güncelleme zaten sürüyor")
    global _gunc_kilit_tutuyor
    tur_kilidi = False
    try:
        eski, yeni = SURUM, b["uzak"]
        # cihaz okumasi yarida kesilmesin
        tur_kilidi = TOPLAYICI.kilit.acquire(timeout=180)
        if not tur_kilidi:
            raise ValueError("Cihaz okuması 3 dakikada bitmedi; güncelleme ertelendi, tekrar deneyin")
        sha = b["sha_tam"]  # tum dosyalar AYNI commit'ten (degismez adres) inecek
        yedek_al(f"guncelleme-{eski}-{yeni}")
        tmp = os.path.join(CFG["veri_klasoru"], "guncelleme_tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        try:
            for ad in GUNC_DOSYALAR:
                try:
                    veri = _gunc_indir(ad, sha, 30)
                except Exception as e:
                    raise ValueError(f"{ad} indirilemedi: {e}")
                if not veri:
                    raise ValueError(f"{ad} boş geldi")
                if ad in ("panel.py", "baslat.pyw"):
                    try:
                        compile(veri.decode("utf-8"), ad, "exec")
                    except (SyntaxError, ValueError) as e:
                        raise ValueError(f"{ad} derlenemiyor: {e}")
                if ad == "panel.py":
                    m = re.search(r'^SURUM\s*=\s*"([^"]+)"', veri.decode("utf-8"), re.M)
                    if not m or m.group(1) != yeni:
                        raise ValueError(f"panel.py sürümü beklenenle ({yeni}) uyuşmuyor")
                if ad == "index.html" and b"<html" not in veri.lower():
                    raise ValueError("index.html geçerli görünmüyor")
                with open(os.path.join(tmp, ad), "wb") as f:
                    f.write(veri)
            yedek = os.path.join(yedek_klasoru(), f"kod_{eski}_{simdi():%Y%m%d_%H%M%S}")
            os.makedirs(yedek, exist_ok=True)
            for ad in GUNC_DOSYALAR:
                if os.path.exists(os.path.join(KOK, ad)):
                    shutil.copy2(os.path.join(KOK, ad), os.path.join(yedek, ad))
            try:
                for ad in GUNC_DOSYALAR:
                    os.replace(os.path.join(tmp, ad), os.path.join(KOK, ad))
            except Exception as e:
                for ad in os.listdir(yedek):  # yarim kalan degisiklikleri geri al
                    try:
                        shutil.copy2(os.path.join(yedek, ad), os.path.join(KOK, ad))
                    except Exception:
                        log.exception("Guncelleme geri yukleme hatasi: %s", ad)
                if isinstance(e, PermissionError):
                    raise ValueError("Dosyalar yazılamadı (izin yok). install.ps1'i yönetici olarak çalıştırın.")
                raise ValueError(f"Dosyalar yerine konamadı, eski sürüm geri yüklendi: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        _gunc_kayit_yaz({"eski": eski, "yeni": yeni, "sha": sha[:7], "sha_tam": sha, "kod_yedek": yedek, "zaman": zstr(simdi()), "durum": "bekliyor"})
        log.info("Guncelleme uygulandi: %s -> %s (%s) (kod yedegi: %s); yeniden baslatiliyor", eski, yeni, sha[:7], yedek)
        # Kilit yeniden baslatma bitene kadar tutulur; surec GUNC_KILIT_SN icinde cikmazsa guvenlik agi birakir
        _gunc_kilit_tutuyor = True
        zm = threading.Timer(GUNC_KILIT_SN, gunc_kilit_birak, args=("zaman asimi: surec cikmadi",))
        zm.daemon = True
        zm.start()
        return {"yeniden_baslatiliyor": True, "yeni": yeni, "sha": sha[:7], "kod_yedek": yedek}
    except Exception:
        if tur_kilidi:
            TOPLAYICI.kilit.release()
        raise
    finally:
        _gunc_kilit.release()


def gunc_kilit_birak(neden):
    """Guncelleme sirasinda tutulan toplayici tur kilidini birakir (yeniden baslatma olmazsa cihaz okumasi surmeli)."""
    global _gunc_kilit_tutuyor
    if _gunc_kilit_tutuyor:
        _gunc_kilit_tutuyor = False
        try:
            TOPLAYICI.kilit.release()
            log.warning("Guncelleme sonrasi toplayici kilidi birakildi (%s)", neden)
        except RuntimeError:
            pass


def yeniden_baslat():
    """Arka planda: ayni arguman ve yorumlayiciyla yeni surec baslat (port bosalana kadar kendisi bekler), sonra sunucuyu kapat ve cik.
    Yeni surec ONCE baslatilir: sunucu kapaninca ana is parcacigi biter ve surec kendiliginden sonlanabilir."""
    time.sleep(1.0)
    try:
        arg = [sys.executable, os.path.abspath(sys.argv[0])] + [a for a in sys.argv[1:] if a != "--yeniden-baslatma"] + ["--yeniden-baslatma"]
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, cwd=KOK)
        if WIN:
            # Gorev Zamanlayici is nesnesinden ayrilmak icin BREAKAWAY denenir (izin yoksa onsuz)
            temel = 0x00000008 | 0x00000200 | 0x08000000
            try:
                subprocess.Popen(arg, creationflags=temel | 0x01000000, **kw)
            except OSError:
                subprocess.Popen(arg, creationflags=temel, **kw)
        else:
            subprocess.Popen(arg, start_new_session=True, **kw)
    except Exception:
        log.exception("Yeniden baslatma basarisiz")
        gunc_kilit_birak("yeniden baslatma basarisiz")  # eski surec calismaya devam eder: cihaz okumasi durmasin
        return  # (dosyalar yeni; baslatici gerekirse geri alir)
    if SRV is not None:
        try:
            SRV.shutdown()
            SRV.server_close()
        except Exception:
            log.exception("Sunucu kapatilamadi")
    os._exit(0)


def guncelleme_sonucu_isaretle():
    k = _gunc_kayit()
    if k and k.get("durum") == "bekliyor":
        k["durum"] = "basarili" if k.get("yeni") == SURUM else "hatali"
        k["acilis"] = zstr(simdi())
        _gunc_kayit_yaz(k)
        if k["durum"] == "basarili" and k.get("sha_tam"):
            with open(os.path.join(CFG["veri_klasoru"], "kurulu_sha.txt"), "w", encoding="utf-8") as f:
                f.write(k["sha_tam"])


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
    if tur == "puantaj":
        return puantaj_tablo(q.get("ay") or simdi().strftime("%Y-%m"))
    bas, bit, kisi = q.get("bas"), q.get("bit"), q.get("kisi") or None
    if tur == "ham":
        b = ["Zaman", "Kart No", "Ad Soyad"]
        return b, [[r["zaman"], r["kart"], r["ad"]] for r in ham_kayitlar(bas, bit, kisi)]
    if tur == "nobet":
        b = ["Tarih", "Kart No", "Ad Soyad", "Mesai", "Durum", "Giriş", "Çıkış", "Geç"]
        out = []
        for g in nobet_durumu(bas, bit):
            for s in g["satirlar"]:
                out.append([g["tarih"], s["kart"], s["ad"], s["mesai"], s["durum"], s["giris"], s["cikis"], sure_yaz(s["gec_dk"])])
        return b, out
    b = ["Tarih", "Kart No", "Sicil", "Ad Soyad", "Kategori", "Mesai", "Giriş", "Çıkış", "Süre", "Okutma",
         "Geç", "Erken çıkış", "Durum", "İzin", "Düzeltme"]
    return b, [[r["tarih"], r["kart"], r["sicil"], r["ad"], r["kategori"], r["mesai"], r["giris"], r["cikis"],
                r["sure"], r["okutma"], sure_yaz(r["gec_dk"]), sure_yaz(r["erken_dk"]), r["durum"], r["izin"],
                "Düzeltildi" if r["duzeltildi"] else ""] for r in gunluk_rapor(bas, bit, kisi, q.get("durum") or None)]


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

    def _yerel(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _cerez(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return c["oturum"].value if "oturum" in c else None
        except Exception:
            return None

    def giris_kontrol(self, yol, post):
        """Giris ozelligi aciksa oturum ve rol denetimi. True: devam; False: yanit gonderildi."""
        self._giris, self._kul = ozellik("giris"), None
        if not self._giris:
            return True
        self._kul = oturum_kullanici(self._cerez())
        if post and yol == "/api/giris":
            return True
        if yol == "/api/surum" and not post:
            return True  # sadece surum numarasi: baslatici panelin donusunu bu uc nokta ile izler
        if self._yerel() and ((post and yol == "/api/guncelleme/uygula") or (not post and yol == "/api/guncelleme")):
            return True  # masaustu baslaticisi (ayni makine) oturumsuz guncelleyebilir
        if self._kul is None:
            if post or yol.startswith(("/api/", "/yedek/", "/disa")) or yol not in ("/", "/index.html"):
                self.gonder(401, {"hata": "Giriş gerekli", "giris_gerekli": True})
            else:
                self.gonder(200, GIRIS_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return False
        if post and self._kul["rol"] != "yonetici" and yol != "/api/cikis":
            self.gonder(403, {"hata": "İzleyici hesabı salt okunurdur", "salt_okunur": True})
            return False
        return True

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
            if not self.giris_kontrol(yol, False):
                return
            if yol in ("/", "/index.html"):
                with open(os.path.join(KOK, "index.html"), "rb") as f:
                    return self.gonder(200, f.read(), "text/html; charset=utf-8")
            if yol == "/api/durum":
                d = durum_bilgisi()
                d["giris_acik"] = self._giris
                d["kullanici"] = {"ad": self._kul["ad"], "rol": self._kul["rol"]} if self._kul else None
                return self.gonder(200, d)
            if yol == "/api/kullanici":
                if self._giris and self._kul["rol"] != "yonetici":
                    return self.gonder(403, {"hata": "Sadece yönetici"})
                if not self._giris and not self.yetkili():
                    return self.gonder(403, {"hata": "Yönetim şifresi hatalı"})
                return self.gonder(200, kullanici_listesi())
            if yol == "/api/bildirim":
                return self.gonder(200, bildirim_ayarlari())
            if yol == "/api/bugun":
                return self.gonder(200, bugun_ozeti())
            if yol == "/api/rapor":
                return self.gonder(200, gunluk_rapor(q["bas"], q["bit"], q.get("kisi"), q.get("durum")))
            if yol == "/api/ham":
                return self.gonder(200, ham_kayitlar(q["bas"], q["bit"], q.get("kisi")))
            if yol == "/api/personel":
                with db() as con:  # rapor filtresi: silinenler de (gecmis kayitlar icin), "(silindi)" ekiyle
                    liste = [{"kisi_no": k, "kart": kart_no(p, k), "adi": p["adi"], "soyadi": p["soyadi"], "sicilno": p["sicilno"], "ad": ad_soyad(p, k)}
                             for k, p in personel_sozluk(con).items()]
                    return self.gonder(200, sorted(liste, key=lambda x: x["ad"]))
            if yol == "/api/nobet":
                return self.gonder(200, nobet_durumu(q["bas"], q["bit"]))
            if yol == "/api/ayar":
                return self.gonder(200, ayar_getir())
            if yol == "/api/izin":
                return self.gonder(200, {"izinler": izin_listesi(q["bas"], q["bit"], q.get("kisi")), "turler": IZIN_TURLERI})
            if yol == "/api/personel_liste":
                return self.gonder(200, personel_liste(q.get("silinenler") in ("1", "true")))
            if yol == "/api/tanimsiz_numaralar":
                return self.gonder(200, tanimsiz_numaralar())
            if yol == "/api/surum":
                return self.gonder(200, {"surum": SURUM})
            if yol == "/api/guncelleme":
                return self.gonder(200, guncelleme_bilgi(q.get("yeni") == "1"))
            if yol == "/api/yenilikler":
                return self.gonder(200, yenilikler())
            if yol == "/api/ana":
                return self.gonder(200, ana_ozet())
            if yol == "/api/ara":
                return self.gonder(200, kisi_ara(q.get("q", "")))
            if yol == "/api/puantaj":
                return self.gonder(200, puantaj(q.get("ay") or simdi().strftime("%Y-%m")))
            if yol == "/api/izin_bakiye":
                return self.gonder(200, izin_bakiye(q.get("kisi")))
            if yol == "/api/donem_karsilastir":
                return self.gonder(200, donem_karsilastir(q.get("tur", "ozel"), q["bas"], q["bit"]))
            if yol == "/api/okutma_gun":
                if not q.get("kisi") or not _TARIH_RE.match(q.get("tarih", "")):
                    return self.gonder(400, {"hata": "kisi ve tarih (YYYY-AA-GG) gerekli"})
                return self.gonder(200, okutma_gun(q["kisi"], q["tarih"]))
            if yol == "/api/kapali_gun":
                yil = q.get("yil", "")
                return self.gonder(200, kapali_gun_listesi(int(yil) if yil.isdigit() and 2000 <= int(yil) <= 2100 else simdi().year))
            if yol == "/api/trend":
                return self.gonder(200, trend())
            if yol == "/api/personel_ozet":
                return self.gonder(200, personel_ozeti(q["bas"], q["bit"]))
            if yol == "/api/personel_detay" and q.get("kisi"):
                return self.gonder(200, personel_detay(q["kisi"], q["bas"], q["bit"]))
            if yol == "/api/eslesme":
                return self.gonder(200, eslesme_listesi())
            if yol == "/disa":
                tur = q.get("tur") if q.get("tur") in ("rapor", "ham", "nobet", "puantaj") else "rapor"
                b, s = disa_tablo(tur, q)
                ad = f'puantaj_{q.get("ay")}' if tur == "puantaj" else f'{tur}_{q["bas"]}_{q["bit"]}'
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
        except ValueError as e:  # kapali ozellik, gecersiz parametre vb.
            self.gonder(400, {"hata": str(e)})
        except Exception as e:
            log.exception("GET %s", self.path)
            self.gonder(500, {"hata": str(e)})

    def yetkili(self):
        if self._yerel() and urlparse(self.path).path == "/api/guncelleme/uygula":
            return True
        if getattr(self, "_giris", False):
            return True  # giris aciksa yonetici oturumu yeterli (giris_kontrol rolu denetledi); X-Sifre devre disi
        s = CFG["yonetim_sifresi"]
        return not s or unquote(self.headers.get("X-Sifre", "")) == s

    def do_POST(self):
        try:
            yol = urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10 * 1024 * 1024:
                return self.gonder(413, {"hata": "Dosya 10 MB'tan büyük olamaz"})
            govde = self.rfile.read(n) if n else b""
            if not self.giris_kontrol(yol, True):
                return
            if yol == "/api/giris":
                if not self._giris:
                    return self.gonder(400, {"hata": "Giriş özelliği kapalı"})
                try:
                    token, kul = giris_yap(json.loads(govde))
                except (ValueError, TypeError) as e:
                    return self.gonder(401, {"hata": str(e)})
                return self.gonder(200, {"ok": True, "kullanici": kul}, ek={
                    "Set-Cookie": f"oturum={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={OTURUM_SURE_SN}"})
            if yol == "/api/cikis":
                oturum_kapat(self._cerez())
                return self.gonder(200, {"ok": True}, ek={"Set-Cookie": "oturum=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})
            if yol == "/api/topla":
                return self.gonder(200, TOPLAYICI.manuel())
            if yol.startswith(("/api/nobet/", "/api/ayar/", "/api/izin/", "/api/yedek/", "/api/duzeltme/", "/api/kullanici/", "/api/guncelleme/")) and not self.yetkili():
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
            duzeltme_islem = {"/api/duzeltme/ekle": duzeltme_ekle, "/api/duzeltme/gizle": duzeltme_gizle,
                              "/api/duzeltme/geri_al": duzeltme_geri_al}
            if yol in duzeltme_islem:
                try:
                    return self.gonder(200, duzeltme_islem[yol](json.loads(govde), self.client_address[0]))
                except (ValueError, KeyError, TypeError) as e:
                    return self.gonder(400, {"hata": str(e)})
            personel_islem = {"/api/ayar/personel_ekle": personel_ekle, "/api/ayar/personel_duzenle": personel_duzenle,
                              "/api/ayar/personel_sil": personel_sil, "/api/ayar/personel_geri_al": personel_geri_al,
                              "/api/ayar/isten_ayrildi": personel_isten_ayrildi, "/api/ayar/uyari_ertele": personel_uyari_ertele}
            if yol in personel_islem:
                try:
                    return self.gonder(200, personel_islem[yol](json.loads(govde), self.client_address[0]))
                except (ValueError, KeyError, TypeError) as e:
                    return self.gonder(400, {"hata": str(e)})
            if yol == "/api/guncelleme/uygula":
                try:
                    r = guncelleme_uygula()
                except (ValueError, KeyError, TypeError) as e:
                    return self.gonder(400, {"hata": str(e)})
                self.gonder(200, r)
                if r.get("yeniden_baslatiliyor"):
                    threading.Thread(target=yeniden_baslat, daemon=True, name="yeniden-baslat").start()
                return
            ayar_islem = {"/api/yedek/al": lambda js: {"ad": yedek_al("elle")},
                          "/api/izin/ekle": izin_ekle, "/api/izin/sil": izin_sil,
                          "/api/nobet/eslesme": eslesme_kaydet, "/api/nobet/eslesme_sil": eslesme_sil,
                          "/api/nobet/satir": nobet_satir,
                          "/api/ayar/genel": ayar_genel_kaydet, "/api/ayar/kategori": ayar_kategori_kaydet,
                          "/api/ayar/kategori_sil": ayar_kategori_sil, "/api/ayar/personel": ayar_personel_kaydet,
                          "/api/kullanici/ekle": kullanici_ekle, "/api/kullanici/sil": kullanici_sil,
                          "/api/kullanici/sifre": kullanici_sifre, "/api/kullanici/rol": kullanici_rol,
                          "/api/ayar/bildirim": bildirim_kaydet, "/api/ayar/bildirim_test": lambda js: bildirim_test(),
                          "/api/ayar/ozellik": ozellik_ayarla, "/api/ayar/izin_devir": ayar_izin_devir, "/api/ayar/kapali_gun": kapali_gun_ekle, "/api/ayar/guncelleme_ayar": ayar_guncelleme,
                          "/api/ayar/kapali_gun_sil": kapali_gun_sil}
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
    ap.add_argument("--yeniden-baslatma", action="store_true", help=argparse.SUPPRESS)  # guncelleme sonrasi: port bosalana kadar bekle
    ap.add_argument("--giris-kapat", action="store_true", help="panel sifresi (giris) ozelligini kapat (kilitlenme onlemi) ve cik")
    ap.add_argument("--ice-aktar-backup", nargs="?", const="", metavar="YOL", help="backup.txt ice aktar")
    a = ap.parse_args()
    CFG = ayar_yukle(a.ayar)
    log_hazirla(konsol=True)
    log.info("EnGoPer Panel v%s", SURUM)
    surum_kontrol()  # yeni surumse once yedek
    db_hazirla()
    surum_yaz()

    if a.giris_kapat:
        giris_kapat()
        print("Giriş (panel şifresi) özelliği kapatıldı; açık oturumlar sonlandırıldı. Servisi yeniden başlatmanız gerekmez.")
        return
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
    srv = None
    for deneme in range(31 if a.yeniden_baslatma else 1):  # eski surec portu birakana kadar 30 sn dene
        try:
            srv = ThreadingHTTPServer((CFG["http_adres"], CFG["http_port"]), Istek)
            break
        except OSError as e:
            if deneme < (30 if a.yeniden_baslatma else 0):
                time.sleep(1)
                continue
            hata_bag = e
    if srv is None:
        e = hata_bag
        log.error("Port %s acilamadi (%s). Servis zaten calisiyor olabilir; baska port icin:  py panel.py --port 8090   "
                  "(Windows'un ayirdigi portlar: netsh interface ipv4 show excludedportrange protocol=tcp)", CFG["http_port"], e)
        sys.exit(1)
    global SRV
    SRV = srv
    srv.daemon_threads = True
    guncelleme_sonucu_isaretle()
    personel_yenile(zorla=True)
    threading.Thread(target=TOPLAYICI.dongu, daemon=True, name="toplayici").start()
    log.info("Panel acildi: http://localhost:%s  (LAN: http://<bu-pc-ip>:%s)", CFG["http_port"], CFG["http_port"])
    if CFG["tarayici_ac"] and not a.tarayici_yok and not servis_hesabi():
        threading.Timer(1.0, tarayici_ac, args=(f'http://localhost:{CFG["http_port"]}/',)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
