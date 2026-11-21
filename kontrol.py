import json
import os
import sys
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NTFY_KANALI = os.environ.get("NTFY_KANALI", "").strip()
TEST_MODU = os.environ.get("TEST_MODU", "false").lower() == "true"
KAYIT_DOSYASI = "gorulen_duyurular.json"

BASE_URL = "https://istanbulanadolu.adalet.gov.tr/"
TAKIP_URL_LERI = [
    "https://istanbulanadolu.adalet.gov.tr/",
    "https://istanbulanadolu.adalet.gov.tr/Arsiv/duyuru",
]

# Hiçbir başlık ihtimalini kaçırmamak için genişletilmiş anahtar kelimeler
DOGRUDAN_KELIMELER = (
    "zabıt", "zabit", "katip", "katib",
    "mülakat", "mulakat", "sözlü", "sozlu",
    "nihai başarı", "nihai basari", "nihai sonuç", "nihai sonuc",
    "sözleşmeli personel", "sozlesmeli personel",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9",
}


def normalize_tr(metin):
    donusum = {
        "İ": "i", "I": "ı", "Â": "a", "â": "a",
        "Î": "i", "î": "i", "Û": "u", "û": "u",
    }
    for eski, yeni in donusum.items():
        metin = metin.replace(eski, yeni)
    return " ".join(metin.lower().split())


def bildirim_gonder(baslik_ascii, mesaj, link=None, oncelik="urgent"):
    headers = {
        "Title": baslik_ascii,
        "Priority": oncelik,
        "Tags": "rotating_light",
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
        # Bazı kamu sitelerinin SSL zinciri Linux sunucularda hata verebiliyor
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

            temiz = normalize_tr(ham_baslik)
            if any(k in temiz for k in DOGRUDAN_KELIMELER):
                tam_link = urljoin(BASE_URL, a["href"])
                bulunanlar[tam_link] = ham_baslik

    return bulunanlar, erisim_basarili


def main():
    if not NTFY_KANALI:
        print("HATA: NTFY_KANALI secret ayarı boş!")
        sys.exit(1)

    guncel_duyurular, basarili = duyurulari_cek()

    if not basarili:
        print("HATA: Adliye sitesine erişilemedi.")
        if TEST_MODU:
            bildirim_gonder(
                baslik_ascii="TEST BASARISIZ",
                mesaj="GitHub sunucusu Anadolu Adliyesi sitesine erişemedi (yurt dışı IP engeli olabilir).",
                oncelik="high",
            )
        sys.exit(1)

    ilk_calisma = not os.path.exists(KAYIT_DOSYASI)
    gorulenler = {}
    if not ilk_calisma:
        with open(KAYIT_DOSYASI, "r", encoding="utf-8") as f:
            gorulenler = json.load(f)

    if TEST_MODU:
        ornek_link, ornek_baslik = next(iter(guncel_duyurular.items()), (BASE_URL, "Eşleşen eski duyuru yok"))
        bildirim_gonder(
            baslik_ascii="7/24 TAKIP TESTI BASARILI!",
            mesaj=f"Site okundu ({len(guncel_duyurular)} duyuru bulundu).\nÖrnek: {ornek_baslik}",
            link=ornek_link,
            oncelik="urgent",
        )
        if ilk_calisma:
            with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
                json.dump(guncel_duyurular, f, ensure_ascii=False, indent=2)
        return

    if ilk_calisma:
        with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(guncel_duyurular, f, ensure_ascii=False, indent=2)
        bildirim_gonder(
            baslik_ascii="7/24 Bulut Takibi Aktif",
            mesaj=f"Anadolu Adliyesi bulut takibi başladı. {len(guncel_duyurular)} eski duyuru kaydedildi, yenisi gelince anında haber vereceğim.",
            oncelik="default",
        )
        return

    yeni_bulundu = False
    for link, baslik in guncel_duyurular.items():
        if link not in gorulenler:
            yeni_bulundu = True
            gorulenler[link] = baslik
            bildirim_gonder(
                baslik_ascii="YENI ZABIT KATIBI DUYURUSU!",
                mesaj=f"Yeni duyuru yayınlandı:\n{baslik}",
                link=link,
                oncelik="urgent",
            )

    if yeni_bulundu:
        with open(KAYIT_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(gorulenler, f, ensure_ascii=False, indent=2)
    else:
        print(f"Kontrol tamamlandı, yeni duyuru yok. (Kayıtlı: {len(gorulenler)})")


if __name__ == "__main__":
    main()
