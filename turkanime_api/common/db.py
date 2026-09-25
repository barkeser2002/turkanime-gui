"""
Sunucu API'si istemcisi: anime-kaynak eşleştirme kayıtları.

Masaüstü uygulamasının canlı tek çağrısı `APIManager.save_anime_match`
(detay sayfası). Eskiden burada kullanıcı bölüm durumu senkronu da vardı
(`save_user_episode_status`, `get_user_episode_status`, `generate_user_id`,
modül düzeyinde `api_manager`, `init_database`): son çağıran CustomTkinter
arayüzüydü (v9.4.3), v10'da hiçbir yerden çağrılmıyordu. Silindi; sunucudaki
`/user/...` uçları eski kurulumlar için duruyor.
"""

import requests
from requests.adapters import HTTPAdapter
import json
from typing import Optional, Dict, List
import threading


def _kanonik_kaynak(source: str) -> str:
    """Kayıtlı kaynak adının kanonik hâli; bilinmeyen ad aynen (bkz. `sources.kayit`)."""
    try:
        from ..sources.kayit import kanonik_ad
        return kanonik_ad(source)
    except Exception:
        return source            # ad çevirisi kaydı asla düşürmesin


def _kaynak_adlarini_cevir(kayitlar: List[Dict]) -> List[Dict]:
    """Sunucudan okunan eşleşmelerde eski kaynak adını (AnimeDepo) kanoniğe çevir.

    Sunucudaki eski kayıtlar "AnimeDepo" adını taşıyor; o ad artık ayrı bir
    kaynak değil ("TürkAnime" = arşiv). Çevrilmezse eşleşme hiçbir kaynağa
    bağlanamaz ya da aynı arşiv iki kaynakmış gibi görünür.
    """
    out: List[Dict] = []
    for kayit in kayitlar:
        if isinstance(kayit, dict) and isinstance(kayit.get("source"), str):
            kayit = {**kayit, "source": _kanonik_kaynak(kayit["source"])}
        out.append(kayit)
    return out


class APIManager:
    """REST API yöneticisi."""

    def __init__(self):
        self.base_url = "https://turkanimeapi.bariskeser.com"
        self.session = requests.Session()
        # Timeout adapter ile ayarla
        adapter = HTTPAdapter(max_retries=3)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """API isteği yapar."""
        try:
            url = f"{self.base_url}{endpoint}"
            headers = {'Content-Type': 'application/json'}

            if method.upper() == 'GET':
                response = self.session.get(url, headers=headers, timeout=10)
            elif method.upper() == 'POST':
                response = self.session.post(url, headers=headers, json=data, timeout=10)
            elif method.upper() == 'PUT':
                response = self.session.put(url, headers=headers, json=data, timeout=10)
            elif method.upper() == 'DELETE':
                response = self.session.delete(url, headers=headers, timeout=10)
            else:
                return None

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            print(f"API isteği hatası ({method} {endpoint}): {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"JSON parse hatası: {e}")
            return None

    def save_anime_match(self, source: str, anime_id: str, anime_title: str) -> bool:
        """Anime eşleştirmesini API'ye kaydeder.

        Kaynak adı kanonik hâliyle yazılır ("AnimeDepo" → "TürkAnime"): aynı
        arşiv bir süre iki ayrı adla listelendi; sunucuda aynı eşleşmenin iki
        adla birikmemesi için eski ad burada çevriliyor.
        """
        source = _kanonik_kaynak(source)

        def worker():
            data = {
                'source': source,
                'anime_id': anime_id,
                'anime_title': anime_title
            }

            result = self._make_request('POST', '/anime-matches', data)
            if result:
                print(f"Anime eşleştirmesi kaydedildi: {source} - {anime_id} - {anime_title}")
            else:
                print(f"Anime eşleştirmesi kaydetme hatası: {source} - {anime_id}")

        # Thread ile çalıştır
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return True

    def get_anime_matches(self, limit: int = 100) -> List[Dict]:
        """Anime eşleştirmelerini API'den getirir (kaynak adları kanonik)."""
        result = self._make_request('GET', f'/anime-matches?limit={limit}')
        if result and isinstance(result, list):
            return _kaynak_adlarini_cevir(result)
        return []

    def search_anime_matches(self, query: str) -> List[Dict]:
        """Anime eşleştirmelerinde API üzerinden arama yapar (kaynak adları kanonik)."""
        result = self._make_request('GET', f'/anime-matches/search?q={query}')
        if result and isinstance(result, list):
            return _kaynak_adlarini_cevir(result)
        return []
