"""Demo modu: cihaz / EnGoPer olmadan paneli denemek icin sahte veri.

  py panel.py --demo

Veriler panel klasorundeki demo_data\\ altina yazilir; C:\\EnGoPer'e ve cihaza hic dokunulmaz,
gorev/guvenlik duvari kurulmaz. Silmek icin demo_data klasorunu silmeniz yeterli.
"""
import datetime as dt
import os
import random

ADLAR = ["ALİ", "AYŞE", "MEHMET", "FATMA", "MUSTAFA", "EMİNE", "AHMET", "HATİCE", "HÜSEYİN", "ZEYNEP",
         "HASAN", "ELİF", "İBRAHİM", "MERVE", "MURAT", "BÜŞRA", "ÖMER", "ŞEYMA", "YUSUF", "SEDA",
         "EMRE", "GİZEM", "BURAK", "AYŞE NUR", "MEHMET ALİ"]
SOYADLAR = ["YILMAZ", "KAYA", "DEMİR", "ŞAHİN", "ÇELİK", "YILDIZ", "YILDIRIM", "ÖZTÜRK", "AYDIN", "ÖZDEMİR",
            "ARSLAN", "DOĞAN", "KILIÇ", "ASLAN", "ÇETİN", "KARA", "KOÇ", "KURT", "ÖZKAN", "ŞİMŞEK",
            "POLAT", "ÖZCAN", "KORKMAZ", "ERDOĞAN", "GÜNEŞ"]


def ayarla(cfg, kok):
    cfg["demo"] = True
    cfg["veri_klasoru"] = os.path.join(kok, "demo_data")
    cfg["personel_dbf"] = ""
    cfg["toplama_aralik_sn"] = min(cfg["toplama_aralik_sn"], 120)


def tohumla(p):
    """Bos demo veritabanina sahte personel, okutma ve nobet ekler."""
    with p.db() as con:
        if con.execute("SELECT COUNT(*) FROM personel").fetchone()[0]:
            return
    rnd = random.Random(42)
    simdi = p.simdi()
    kisiler = []
    with p.db() as con:
        for i, (a, s) in enumerate(zip(ADLAR, SOYADLAR)):
            k = 100 + i * 7
            kisiler.append(k)
            con.execute("INSERT INTO personel(kisi_no,kartno,adi,soyadi,sicilno,guncelleme) VALUES(?,?,?,?,?,?)",
                        (k, f"{k:04d}", a, s, f"S{k}", p.zstr(simdi)))
    kayitlar = []
    for gun_geri in range(45, -1, -1):
        gun = simdi.date() - dt.timedelta(days=gun_geri)
        if gun.weekday() == 6:
            continue
        cumartesi = gun.weekday() == 5
        calisanlar = rnd.sample(kisiler, 6) if cumartesi else [k for k in kisiler if rnd.random() < 0.85]
        for k in calisanlar:
            giris = dt.datetime.combine(gun, dt.time(8, 40)) + dt.timedelta(minutes=rnd.randint(0, 45))
            cikis = giris + dt.timedelta(hours=rnd.choice([5, 8, 9]), minutes=rnd.randint(0, 40))
            for t in (giris, cikis):
                if t <= simdi and not (t is cikis and rnd.random() < 0.05):
                    kayitlar.append((k, p.zstr(t), 0))
    p.okutma_ekle(kayitlar, "demo")
    # bu ayin cumartesileri icin ornek nobet: gecmis cumartesilerde gelenlerin cogu + 1 gelmeyen
    ay_basi = simdi.date().replace(day=1)
    gun = ay_basi + dt.timedelta(days=(5 - ay_basi.weekday()) % 7)
    with p.db() as con:
        while gun.month == ay_basi.month:
            g = gun.isoformat()
            gelen = [r[0] for r in con.execute("SELECT DISTINCT kisi_no FROM okutma WHERE zaman LIKE ?", (g + "%",))]
            plan = gelen[:4] + rnd.sample([k for k in kisiler if k not in gelen], 1) if gelen else rnd.sample(kisiler, 5)
            for k in plan:
                con.execute("INSERT OR IGNORE INTO nobet(tarih,kisi_no,metin) VALUES(?,?,?)", (g, k, "demo"))
            gun += dt.timedelta(days=7)
    p.log.info("Demo verisi olusturuldu: %d personel, %d okutma", len(kisiler), len(kayitlar))


def sahte_okuma(p):
    """Toplayici yerine: o anda 0-2 rastgele okutma 'okur'."""
    with p.db() as con:
        kisiler = [r[0] for r in con.execute("SELECT kisi_no FROM personel")]
    t = p.zstr(p.simdi())
    secilen = random.sample(kisiler, random.randint(0, 2)) if kisiler else []
    return [{"k": k, "t": t, "vm": 0} for k in secilen]
