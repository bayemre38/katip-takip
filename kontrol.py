from datetime import datetime, timedelta, timezone
import json
import os
import sys
import time
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NTFY_KANALI = os.environ.get("NTFY_KANALI", "").strip()
TEST_MODU = os.environ.get("TEST_MODU", "false").lower() == "true"
KAYIT_DOSYASI = "gorulen_duyurular.json"

# Her 5 dakikalık çalışmada 5 kez (~1 dakikada bir) siteyi kontrol eder
KONTROL_SAYISI = 5
BEKLEME_SANIYE = 52

# Saatlik durum raporunun geleceği gündüz saat aralığı (Her gün)
GUNDUZ_BASLANGIC_SAAT = 8   # 08:00
GUNDUZ_BITIS_SAAT = 19      # 19:00

BASE_URL = "https://istanbulanadolu.adalet.gov.tr/"
TAKIP_URL_LERI = [
    "https://istanbulanadolu.adalet.gov.tr/",
    "https://istanbulanadolu.adalet.gov.tr/Arsiv/duyuru",
]

SONUC_KELIMELERI = (
    "mülakat", "mulakat", "sözlü", "sozlu",
    "nihai", "sonuç", "sonuc", "başarı", "basari",
    "kazanan", "asil", "yedek",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
}


def tr_simdi():
    return datetime.now(timezone(timedelta(hours=3)))


def rutin_bildirim_zamani_mi(simdi):
    """Her gün sadece gündüz saatlerinde True döner."""
    return GUNDUZ_BASLANGIC_SAAT <= simdi.hour < GUNDUZ_BITIS_SAAT


def normalize_tr(metin):
    donusum = {
        "İ": "i", "I": "ı", "Â": "a", "â": "a",
        "Î": "i", "î": "i", "Û": "u", "û": "u",
    }
    for eski, yeni in donusum.items():
        metin = metin.replace(eski, yeni)
    return " ".join(metin.lower().split())


def zabit_mulakat_mi(ham_baslik):
    """Sadece Zabıt Katipliği (veya toplu personel) mülakat/nihai sonuç duyurularını yakalar."""
    temiz = normalize_tr(ham_baslik)

    zabit_var = ("zabıt" in temiz) or ("zabit" in temiz)
    katip_var = (("katip" in temiz) or ("katib" in temiz)) and ("icra" not in temiz)
    toplu_personel_var = (
        ("sözleşmeli personel" in temiz) or ("sozlesmeli personel" in temiz)
    ) and ("icra" not in temiz)

    kadro_uygun = zabit_var or katip_var or toplu_personel_var
    sonuc_uygun = any(k in temiz for k in SONUC_KELIMELERI)

    return kadro_uygun and sonuc_uygun


def bildirim_gonder(baslik_ascii, mesaj, link=None, oncelik="default", etiket="white_check_mark"):
    headers = {
        "Title": baslik_ascii,
        "Priority": oncelik,
        "Tags": etiket,
    }
    if link:
        headers["Click"] = link

    r = requests.post(
        f"https://ntfy.sh/{NTFY_KANALI}",
        data=mesaj.encode("utf-8"),
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    print(f"[BİLDİRİM GÖNDERİLDİ] {baslik_ascii} -> {mesaj}")


def sayfayi_indir(url):
    try:
        return requests.get(url, headers=HEADERS, timeout=25)
    except requests.exceptions.SSLError:
        return requests.get(url, headers=HEADERS, timeout=25, verify=False)


def duyurulari_cek():
    bulunanlar = {}
    erisim_basarili = False

    for url in TAKIP_URL_LERI:
        try:
            resp = sayfayi_indir(url)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            erisim_basarili = True
        except Exception as e:
            print(f"[ERİŞİM HATASI] {url} -> {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            ham_baslik = " ".join(a.get_text(" ", strip=True).split())
            if len(ham_baslik) < 10:
                continue

            if zabit_mulakat_mi(ham_baslik):
                tam_link = urljoin(BASE_URL, a["href"])
                bulunanlar[tam_link] = ham_baslik

    return bulunanlar, erisim_basarili


def kayit_yaz(gorulenler):
    with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(gorulenler, f, ensure_ascii=False, indent=2)


def main():
    if not NTFY_KANALI:
        print("HATA: NTFY_KANALI secret ayarı boş!")
        sys.exit(1)

    ilk_calisma = not os.path.exists(KAYIT_DOSYASI)
    gorulenler = {}
    if not ilk_calisma:
        with open(KAYIT_DOSYASI, "r", encoding="utf-8") as f:
            gorulenler = json.load(f)

    # Manuel test modunda bekletmeden tek seferde test bildirimi at
    if TEST_MODU:
        simdi = tr_simdi()
        saat = simdi.strftime("%H:%M")
        guncel_duyurular, basarili = duyurulari_cek()
        if not basarili:
            print(f"[{saat}] HATA: Test sırasında siteye erişilemedi.")
            sys.exit(1)

        ornek_link, ornek_baslik = next(
            iter(guncel_duyurular.items()),
            (BASE_URL, "Eşleşen eski Zabıt Katipliği duyurusu yok"),
        )
        bildirim_gonder(
            baslik_ascii="7/24 TAKIP TESTI BASARILI!",
            mesaj=f"[{saat}] Dakikalık takip ve saatlik rapor aktif ({len(guncel_duyurular)} eski kayıt).\nÖrnek: {ornek_baslik}",
            link=ornek_link,
            oncelik="urgent",
            etiket="rotating_light",
        )
        if ilk_calisma:
            guncel_duyurular["__SON_RAPOR_SAATI__"] = simdi.strftime("%Y-%m-%d %H")
            kayit_yaz(guncel_duyurular)
        return

    # İlk kez çalışıyorsa mevcut duyuruları kaydet
    if ilk_calisma:
        simdi = tr_simdi()
        saat = simdi.strftime("%H:%M")
        guncel_duyurular, basarili = duyurulari_cek()
        if not basarili:
            print(f"[{saat}] HATA: İlk kurulumda siteye erişilemedi.")
            sys.exit(1)

        kayit_sayisi = len(guncel_duyurular)
        guncel_duyurular["__SON_RAPOR_SAATI__"] = simdi.strftime("%Y-%m-%d %H")
        kayit_yaz(guncel_duyurular)
        bildirim_gonder(
            baslik_ascii="7/24 Bulut Takibi Aktif",
            mesaj=f"[{saat}] Dakikada bir takip başladı, saat başı rapor verilecek. ({kayit_sayisi} eski duyuru kayıtlı)",
            oncelik="default",
            etiket="white_check_mark",
        )
        return

    # Normal çalışma: 5 dakika boyunca yaklaşık her 1 dakikada bir kontrol et
    basarili_tur = 0

    for tur in range(KONTROL_SAYISI):
        simdi = tr_simdi()
        saat = simdi.strftime("%H:%M")
        saat_anahtari = simdi.strftime("%Y-%m-%d %H")
        rutin_acik = rutin_bildirim_zamani_mi(simdi)

        guncel_duyurular, basarili = duyurulari_cek()

        if basarili:
            basarili_tur += 1

            # Yeni eşleşen duyuru var mı bak
            alarmlar = gorulenler.get("__AKTIF_ALARMLAR__", [])
            for link, baslik in guncel_duyurular.items():
                if link not in gorulenler:
                    gorulenler[link] = baslik
                    alarmlar.append({
                        "baslik": baslik,
                        "link": link,
                        "ilk_saat": saat,
                        "tekrar": 0,
                    })

            # Sonuç açıklandıysa saat başını beklemeden ANINDA alarm çal ve kaydet
            if alarmlar:
                for alarm in alarmlar:
                    alarm["tekrar"] += 1
                    bildirim_gonder(
                        baslik_ascii=f"ACIKLANDI! (Uyari #{alarm['tekrar']})",
                        mesaj=(
                            f"[{saat}] ZABIT KÂTİPLİĞİ MÜLAKAT / SONUÇ AÇIKLANDI!\n\n"
                            f"{alarm['baslik']}\n"
                            f"(İlk tespit saati: {alarm['ilk_saat']})"
                        ),
                        link=alarm["link"],
                        oncelik="urgent",
                        etiket="rotating_light,tada",
                    )

                gorulenler["__AKTIF_ALARMLAR__"] = alarmlar
                kayit_yaz(gorulenler)
                return

            # Sonuç yoksa: sadece gündüz ve o saat için henüz rapor atılmadıysa 1 kez rapor gönder
            kayitli_sayi = sum(1 for k in gorulenler if not k.startswith("__"))
            if rutin_acik and gorulenler.get("__SON_RAPOR_SAATI__") != saat_anahtari:
                bildirim_gonder(
                    baslik_ascii="Saatlik Rapor - Yeni Duyuru Yok",
                    mesaj=f"[{saat}] Dakikada bir takip aktif, Zabıt Katipliği mülakat sonucu henüz açıklanmadı. (Kayıtlı: {kayitli_sayi})",
                    oncelik="default",
                    etiket="white_check_mark",
                )
                gorulenler["__SON_RAPOR_SAATI__"] = saat_anahtari
                kayit_yaz(gorulenler)
            else:
                print(f"[{saat}] Tur {tur + 1}/{KONTROL_SAYISI}: Yeni duyuru yok. (Kayıtlı: {kayitli_sayi})")
        else:
            print(f"[{saat}] Tur {tur + 1}/{KONTROL_SAYISI}: Siteye erişilemedi, sonraki dakika tekrar denenecek.")

        if tur < KONTROL_SAYISI - 1:
            time.sleep(BEKLEME_SANIYE)

    # 5 dakikanın tamamında siteye hiç ulaşılamadıysa gündüz uyar
    if basarili_tur == 0:
        simdi = tr_simdi()
        saat = simdi.strftime("%H:%M")
        if rutin_bildirim_zamani_mi(simdi):
            bildirim_gonder(
                baslik_ascii="Siteye Erisilemedi",
                mesaj=f"[{saat}] Son 5 dakikadır Anadolu Adliyesi sitesine ulaşılamadı, takip devam ediyor.",
                oncelik="low",
                etiket="warning",
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
