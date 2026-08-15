"""
AniList API Client for TurkAnime GUI
Provides authentication, trending anime, and user tracking features.

**Gizli anahtar burada DURMAZ.** `client_id` OAuth2'de tasarımı gereği
publictir (yetkilendirme URL'i zaten kullanıcının adres çubuğunda taşır), ama
`client_secret` sırdır: depo herkese açık ve değer derlenmiş EXE'ye de gömülür.
Bu yüzden secret yalnızca çalışma anında çözülür — bkz. `cozumlenmis_secret`.
Secret yoksa giriş, secret istemeyen Implicit akışa düşer.
"""

import requests
import hashlib
import json
import http.server
import socketserver
import threading
import os
from typing import List, Dict, Optional, Any, Callable
from urllib.parse import parse_qs, urlparse

# Uygulamanın public kimliği ve yerel geri dönüş adresi.
VARSAYILAN_CLIENT_ID = "29745"
VARSAYILAN_REDIRECT_URI = "http://localhost:9921/anilist-login"

# Secret'ın okunduğu ortam değişkeni. Kendi AniList uygulamasını Authorization
# Code akışıyla kullanmak isteyenler bunu tanımlar; kaynağa yazılmaz.
SECRET_ORTAM_DEGISKENI = "ANILIST_CLIENT_SECRET"

# Implicit akışta jeton URL *fragment*'ında gelir (`#access_token=...`) ve
# fragment tarayıcıdan sunucuya HİÇ gönderilmez. Bu uç, aşağıdaki köprü
# sayfasının hash'i okuyup jetonu geri yollaması için var.
TOKEN_UCU = "/anilist-token"


# Depoya bir dönem sızmış, V10'da kaynaktan çıkarılan client secret'ın PARMAK
# İZİ. Değerin kendisi hiçbir yerde durmaz: yalnızca uzunluğu ve SHA-256
# özetinin ilk haneleri tutuluyor. Özet bilerek kısaltıldı — bu dosyada 24+
# karakterlik anahtar biçimli sabitler yasak (bkz.
# `test_anilist_client_uzun_sabit_dize_icermiyor`) ve 64 bitlik bir önek, tek
# bir bilinen değeri tanımak için fazlasıyla yeterli.
SIZAN_SECRET_UZUNLUK = 40
SIZAN_SECRET_OZET_ONEKI = "7168ad9f2bd03ba1"


def sizan_secret_mi(deger: Any) -> bool:
    """`deger`, depodan çıkarılmış sızmış secret mi?

    V10 öncesi bir sürümde Ayarlar'da bir kez "Kaydet"e basmış kullanıcıların
    `anilist_config.json`'ında bu değer duruyor. Sır AniList panelinden
    döndürüldüğü anda Authorization Code akışı sessizce bozulur (tarayıcı
    açılır, sonra hata) — bu yüzden diskte bulunursa temizlenir.
    """
    metin = str(deger or "").strip()
    if len(metin) != SIZAN_SECRET_UZUNLUK:
        return False
    ozet = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    return ozet.startswith(SIZAN_SECRET_OZET_ONEKI)


def cozumlenmis_secret() -> str:
    """Ortam değişkenindeki client secret; tanımsızsa boş dize.

    Kaynak kodda sabit bir değer YOK; öncelik sırası ortam değişkeni →
    kullanıcının yapılandırma dosyası (`_load_config`) → boş.
    """
    return str(os.environ.get(SECRET_ORTAM_DEGISKENI, "") or "").strip()


class AniListClient:
    """AniList API client with OAuth2 authentication."""

    BASE_URL = "https://graphql.anilist.co"
    AUTH_URL = "https://anilist.co/api/v2/oauth/authorize"
    TOKEN_URL = "https://anilist.co/api/v2/oauth/token"

    def __init__(self, client_id: str, client_secret: str = "", redirect_uri: str = ""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = None
        self.refresh_token = None
        self.user_data = None
        # Yapılandırma dosyasından gelen secret ayrıca tutulur: ortamdan gelen
        # değeri diske kopyalamamak için (bkz. `_save_config`).
        self._dosya_secret = ""
        self._secret_ortamdan = False
        # Diskteki sızmış sır bu oturumda temizlendi mi (arayüz bunu duyurur).
        self.sizan_secret_temizlendi = False
        # Try load tokens from disk
        try:
            self._load_tokens()
        except Exception:
            pass
        # Try load persisted OAuth client config if present
        try:
            self._load_config()
        except Exception:
            pass
        # Ortam değişkeni en son uygulanır: dosyadaki değeri de ezmeli, çünkü
        # onu tanımlayan kullanıcı bilinçli olarak o oturumu seçiyor.
        ortam = cozumlenmis_secret()
        if ortam:
            self.client_secret = ortam
            self._secret_ortamdan = True

    def secret_var_mi(self) -> bool:
        """Authorization Code akışını sürdürecek bir secret var mı?"""
        return bool(str(self.client_secret or "").strip())

    def akis_turu(self) -> str:
        """Kullanılacak OAuth akışı: secret varsa ``code``, yoksa ``token``.

        Masaüstü uygulaması sır saklayamaz; varsayılan bu yüzden Implicit.
        Secret'ı olan (kendi uygulamasını tanımlamış) kullanıcı eski akışta
        kalır — kaydettiği yapılandırma bozulmasın.
        """
        return "code" if self.secret_var_mi() else "token"

    def get_auth_url(self, response_type: Optional[str] = None,
                     state: Optional[str] = None) -> str:
        """OAuth2 yetkilendirme URL'ini üret.

        response_type:
          - None     -> otomatik seçim (`akis_turu`)
          - "code"   -> Authorization Code flow (requires client_secret)
          - "token"  -> Implicit flow (no client_secret, access_token in fragment)
        """
        response_type = str(response_type or "").strip() or self.akis_turu()
        params = [
            ("client_id", self.client_id),
            ("redirect_uri", self.redirect_uri),
            ("response_type", response_type),
        ]
        # AniList API scope desteklemiyor — parametre eklemeye gerek yok.
        if state:
            params.append(("state", state))

        # Build URL manually to avoid importing urllib just for urlencode
        query = "&".join([f"{k}={v}" for k, v in params])
        return f"{self.AUTH_URL}?{query}"

    def set_access_token(self, token: str, expires_in: Optional[int] = None):
        """Set access token directly (implicit flow)."""
        self.access_token = token
        # We don't currently track expiry, but could store if needed
        try:
            self._save_tokens()
        except Exception:
            pass

    def exchange_code_for_token(self, code: str) -> bool:
        """Exchange authorization code for access token.

        Yalnızca Authorization Code akışında çağrılır; secret yoksa AniList
        isteği zaten reddeder, boş secret'la ağa çıkmanın anlamı yok.
        """
        if not self.secret_var_mi():
            print("[AniList] client_secret tanımlı değil; Implicit akış kullanılmalı.")
            return False
        data = {
            'grant_type': 'authorization_code',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': code
        }

        try:
            # AniList token endpoint form-encoded bekler; JSON gönderimi unsupported_grant_type hatası döndürebilir.
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = requests.post(self.TOKEN_URL, data=data, headers=headers, timeout=10)
            response.raise_for_status()
            token_data = response.json()

            self.access_token = token_data.get('access_token')
            # AniList refresh token desteklemiyor — sadece access_token kaydedilir
            try:
                self._save_tokens()
            except Exception:
                pass
            return True
        except Exception as e:
            # Yalnızca istisna metni basılır; `data` sözlüğü client_secret
            # taşıyor ve hiçbir log satırına girmemeli.
            print(f"Token exchange failed: {e}")
            return False

    def refresh_access_token(self) -> bool:
        """AniList refresh token desteklemiyor. Token süresi dolunca yeniden giriş gerekir.

        Not: AniList token'ları 1 yıl geçerlidir. Süresi dolduğunda
        kullanıcı tekrar OAuth2 ile giriş yapmalıdır.
        """
        # AniList API refresh token'ları desteklemiyor
        # https://docs.anilist.co/guide/auth/
        print("[AniList] Token yenileme desteklenmiyor. Yeniden giriş yapın.")
        return False

    def _make_request(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make authenticated GraphQL request."""
        headers = {'Content-Type': 'application/json'}
        if self.access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'

        data: Dict[str, Any] = {'query': query}
        if variables:
            data['variables'] = variables

        try:
            response = requests.post(self.BASE_URL, headers=headers, json=data, timeout=10)
        except Exception as e:
            print(f"API request failed: {e}")
            return None

        # Token süresi dolmuşsa (1 yıl) kullanıcıyı bilgilendir
        if response.status_code == 401:
            print("[AniList] Token geçersiz veya süresi dolmuş. Yeniden giriş gerekli.")
            self.access_token = None
            try:
                self._save_tokens()
            except Exception:
                pass
            return None

        try:
            response.raise_for_status()
            return response.json()
        except Exception as e:
            # Show server-provided error if any
            try:
                print(f"API request failed: {response.status_code} {response.text}")
            except Exception:
                print(f"API request failed: {e}")
            return None

    def get_current_user(self) -> Optional[Dict]:
        """Get current authenticated user information."""
        query = """
        query {
            Viewer {
                id
                name
                avatar {
                    large
                }
                statistics {
                    anime {
                        count
                        meanScore
                        minutesWatched
                    }
                }
            }
        }
        """

        result = self._make_request(query)
        if result and 'data' in result and result['data']['Viewer']:
            self.user_data = result['data']['Viewer']
            return self.user_data
        return None

    def get_trending_anime(self, page: int = 1, per_page: int = 20) -> List[Dict]:
        """Get trending anime list."""
        query = """
        query ($page: Int, $perPage: Int) {
            Page(page: $page, perPage: $perPage) {
                media(sort: TRENDING_DESC, type: ANIME) {
                    id
                    title {
                        romaji
                        english
                        native
                    }
                    coverImage {
                        large
                        medium
                    }
                    description
                    episodes
                    duration
                    genres
                    averageScore
                    popularity
                    status
                    season
                    seasonYear
                    studios {
                        nodes {
                            name
                        }
                    }
                }
            }
        }
        """

        variables = {'page': page, 'perPage': per_page}
        result = self._make_request(query, variables)

        if result and 'data' in result and 'Page' in result['data']:
            return result['data']['Page']['media']
        return []

    def search_anime(self, query: str, page: int = 1, per_page: int = 20) -> List[Dict]:
        """Search anime by title."""
        search_query = """
        query ($search: String, $page: Int, $perPage: Int) {
            Page(page: $page, perPage: $perPage) {
                media(search: $search, type: ANIME) {
                    id
                    title {
                        romaji
                        english
                        native
                    }
                    coverImage {
                        large
                        medium
                    }
                    description
                    episodes
                    duration
                    genres
                    averageScore
                    popularity
                    status
                    season
                    seasonYear
                    studios {
                        nodes {
                            name
                        }
                    }
                }
            }
        }
        """

        variables = {'search': query, 'page': page, 'perPage': per_page}
        result = self._make_request(search_query, variables)

        if result and 'data' in result and 'Page' in result['data']:
            return result['data']['Page']['media']
        return []

    def get_user_anime_list(self, user_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get user's anime list."""
        list_query = """
        query ($userId: Int, $status: MediaListStatus) {
            MediaListCollection(userId: $userId, type: ANIME, status: $status) {
                lists {
                    name
                    entries {
                        media {
                            id
                            title {
                                romaji
                                english
                                native
                            }
                            coverImage {
                                large
                                medium
                            }
                            episodes
                        }
                        progress
                        score
                        status
                        updatedAt
                    }
                }
            }
        }
        """

        variables: Dict[str, Any] = {'userId': user_id}
        if status:
            variables['status'] = status

        result = self._make_request(list_query, variables)

        if result and 'data' in result and 'MediaListCollection' in result['data']:
            return result['data']['MediaListCollection']['lists']
        return []

    def update_anime_progress(self, media_id: int, progress: int, status: Optional[str] = None) -> bool:
        """Update anime progress in user's list."""
        mutation = """
        mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
            SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
                id
                progress
                status
            }
        }
        """

        variables: Dict[str, Any] = {'mediaId': media_id, 'progress': progress}
        if status:
            variables['status'] = status

        result = self._make_request(mutation, variables)
        return result is not None and 'data' in result and result['data']['SaveMediaListEntry'] is not None

    def get_anime_by_id(self, anime_id: int) -> Optional[Dict]:
        """Get anime details by ID."""
        query = """
        query ($id: Int) {
            Media(id: $id, type: ANIME) {
                id
                title {
                    romaji
                    english
                    native
                }
                coverImage {
                    large
                    medium
                }
                description
                episodes
                duration
                genres
                averageScore
                popularity
                status
                season
                seasonYear
                studios {
                    nodes {
                        name
                    }
                }
            }
        }
        """

        variables = {'id': anime_id}
        result = self._make_request(query, variables)

        if result and 'data' in result and result['data']['Media']:
            return result['data']['Media']
        return None

    def get_anime_by_ids(self, anime_ids: list) -> Dict[int, Dict]:
        """Get multiple anime details by IDs in a single request."""
        if not anime_ids:
            return {}
        
        # AniList API'de batch query için Page kullanıyoruz
        # Maksimum 50 anime çekebiliriz
        anime_ids = anime_ids[:50]
        
        query = """
        query ($ids: [Int]) {
            Page(page: 1, perPage: 50) {
                media(id_in: $ids, type: ANIME) {
                    id
                    title {
                        romaji
                        english
                        native
                    }
                    coverImage {
                        large
                        medium
                    }
                    description
                    episodes
                    duration
                    genres
                    averageScore
                    popularity
                    status
                    season
                    seasonYear
                    studios {
                        nodes {
                            name
                        }
                    }
                }
            }
        }
        """

        variables = {'ids': anime_ids}
        result = self._make_request(query, variables)

        anime_map = {}
        if result and 'data' in result and result['data']['Page']:
            media_list = result['data']['Page'].get('media', [])
            for media in media_list:
                if media and media.get('id'):
                    anime_map[media['id']] = media
        
        return anime_map

    # --- token persistence ---
    def _tokens_path(self) -> str:
        try:
            import appdirs
            data_dir = appdirs.user_data_dir("TurkAnime", "Barkeser")
        except Exception:
            data_dir = os.path.join(os.path.expanduser("~"), ".turkanime")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "anilist_tokens.json")

    def _save_tokens(self) -> None:
        path = self._tokens_path()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({
                'access_token': self.access_token,
            }, f)

    def _load_tokens(self) -> None:
        path = self._tokens_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.access_token = data.get('access_token')

    # --- config persistence (client_id, client_secret, redirect_uri) ---
    def _config_path(self) -> str:
        # Reuse tokens directory
        base_dir = os.path.dirname(self._tokens_path())
        return os.path.join(base_dir, "anilist_config.json")

    def _load_config(self) -> None:
        path = self._config_path()
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.client_id = data.get('client_id', self.client_id)
        dosya_secret = str(data.get('client_secret', '') or '')
        if sizan_secret_mi(dosya_secret):
            # Sızmış sır KULLANILMAZ ve diskte de bırakılmaz: panelden
            # döndürüldüğü an giriş sessizce bozulurdu. Implicit akış zaten
            # secret istemiyor, kullanıcı hiçbir şey kaybetmez.
            dosya_secret = ''
            self.sizan_secret_temizlendi = True
            self._sizan_secreti_diskten_sil(path, data)
            print("[AniList] Yapılandırmanızda paylaşılmış (artık geçersiz) "
                  "bir client secret bulundu ve silindi. Giriş bundan sonra "
                  "secret gerektirmeyen Implicit akışla yapılacak.")
        self._dosya_secret = dosya_secret
        if dosya_secret:
            self.client_secret = dosya_secret
        self.redirect_uri = data.get('redirect_uri', self.redirect_uri)

    @staticmethod
    def _sizan_secreti_diskten_sil(path: str, data: Dict[str, Any]) -> None:
        """Yapılandırmayı sırsız hâliyle geri yaz; diğer alanlara dokunma.

        `_save_config` çağrılmıyor: o, o anki bellek durumunu yazar ve bu
        noktada `client_secret` henüz çözümlenmemiş olabilir.
        """
        try:
            temiz = dict(data)
            temiz['client_secret'] = ''
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(temiz, f)
        except Exception:
            pass

    def _save_config(self) -> None:
        path = self._config_path()
        # Ortamdan gelen secret diske KOPYALANMAZ: kullanıcı onu bilerek süreç
        # ömrüyle sınırlamış olabilir, "Kaydet"e basmak bu tercihi sessizce
        # bozmamalı. Dosyadaki mevcut değer olduğu gibi korunur.
        secret = self._dosya_secret if self._secret_ortamdan else self.client_secret
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({
                'client_id': self.client_id,
                'client_secret': secret,
                'redirect_uri': self.redirect_uri,
            }, f)

    def set_oauth_config(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        """Update AniList OAuth client configuration and persist it.

        `client_secret` opsiyoneldir: boş bırakılırsa Implicit akış kullanılır.
        """
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        if sizan_secret_mi(client_secret):
            # Sızmış sır elle yapıştırılsa da geri alınmaz; diskten temizlediğimiz
            # değeri aynı çalıştırmada geri yazmanın anlamı yok.
            client_secret = ""
        # Ortam değişkeni etkinken alan değişmeden kaydedildiyse (ayar sayfası
        # etkin değeri gösteriyor) secret'a dokunulmaz; kullanıcı gerçekten
        # farklı bir değer yazdıysa artık kaynak odur.
        if not self._secret_ortamdan or client_secret != self.client_secret:
            self._secret_ortamdan = False
            self._dosya_secret = client_secret
            self.client_secret = client_secret
        try:
            self._save_config()
        except Exception:
            pass

    def clear_tokens(self) -> None:
        """Clear saved tokens and in-memory auth."""
        self.access_token = None
        self.refresh_token = None
        # delete tokens file if exists
        try:
            path = self._tokens_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def fragment_koprusu_html(token_ucu: str = TOKEN_UCU) -> str:
    """Implicit akışta jetonu yerel sunucuya taşıyan köprü sayfası.

    Fragment tarayıcıda kalır — `#access_token=...` HTTP isteğine HİÇ girmez,
    dolayısıyla `AniListAuthServer` onu doğrudan okuyamaz. Tek yol, redirect'te
    bu sayfayı servis edip JS'in `location.hash`'i okuyup jetonu geri
    göndermesi. `fetch` engellenirse aynı jeton sorgu dizesiyle GET olarak
    yollanır (yalnızca localhost'a gider, ağa çıkmaz).
    """
    return """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><title>AniList girisi</title></head>
<body style="background:#0f0f0f;color:#e6e6e6;font-family:Segoe UI,Arial,sans-serif;padding:32px;">
<h2 id="baslik">AniList girisi tamamlaniyor...</h2>
<p id="mesaj">Bu sekmeyi kapatmayin, birkac saniye surebilir.</p>
<script>
(function () {
  var yaz = function (baslik, mesaj) {
    document.getElementById('baslik').textContent = baslik;
    document.getElementById('mesaj').textContent = mesaj;
  };
  var hash = window.location.hash || '';
  if (hash.charAt(0) === '#') { hash = hash.substring(1); }
  var params = new URLSearchParams(hash);
  var token = params.get('access_token');
  var hata = params.get('error') ||
             new URLSearchParams(window.location.search || '').get('error');
  if (!token) {
    yaz('Giris tamamlanamadi',
        hata ? ('AniList yaniti: ' + hata)
             : 'Jeton alinamadi. Uygulamadan tekrar deneyin.');
    return;
  }
  var bitti = function () {
    yaz('Giris basarili', 'Bu sekmeyi kapatabilirsiniz.');
    setTimeout(function () { window.close(); }, 1500);
  };
  var yedek = function () {
    window.location.replace('__UC__?access_token=' + encodeURIComponent(token));
  };
  try {
    fetch('__UC__', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: token })
    }).then(function (r) { if (r && r.ok) { bitti(); } else { yedek(); } })
      .catch(yedek);
  } catch (e) { yedek(); }
})();
</script>
</body></html>
""".replace("__UC__", token_ucu)


class AniListAuthServer:
    """Local HTTP server for OAuth2 callback handling."""

    def __init__(self, client: AniListClient):
        self.client = client
        self.auth_code = None
        self.server = None
        self.on_success: Optional[Callable[[], None]] = None  # optional callback when auth succeeds

    def register_on_success(self, cb: Callable[[], None]) -> None:
        """Register a callback to be invoked after successful auth."""
        self.on_success = cb

    def start_server(self, port: int = 9921):
        """Start local server to handle OAuth callback."""

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def __init__(self, *args, anilist_client=None, auth_server=None, **kwargs):
                self.anilist_client = anilist_client
                self._auth_server = auth_server
                super().__init__(*args, **kwargs)

            def do_GET(self):
                parsed_path = urlparse(self.path)
                query_params = parse_qs(parsed_path.query)

                if parsed_path.path.endswith("/anilist-login"):
                    # Authorization Code akışı: code parametresi geldiyse önce bunu işle
                    if 'code' in query_params:
                        code = query_params['code'][0]
                        success = self.anilist_client.exchange_code_for_token(code) if self.anilist_client else False

                        if success:
                            self.send_response(200)
                            self.send_header('Content-type', 'text/html')
                            self.end_headers()
                            self.wfile.write(b"""
                            <html>
                            <body>
                            <h2>AniList Authentication Successful!</h2>
                            <p>You can close this window and return to the application.</p>
                            <script>
                                window.close();
                            </script>
                            </body>
                            </html>
                            """)
                            try:
                                if self.anilist_client:
                                    self.anilist_client.get_current_user()
                                if self._auth_server and self._auth_server.server:
                                    threading.Thread(target=self._auth_server.server.shutdown, daemon=True).start()
                                if self._auth_server and self._auth_server.on_success:
                                    try:
                                        self._auth_server.on_success()
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            return

                    # Implicit akış: jeton fragment'ta, yani bu istekte YOK.
                    # Hash'i okuyup geri gönderen köprü sayfasını sun.
                    govde = fragment_koprusu_html().encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(govde)))
                    self.end_headers()
                    self.wfile.write(govde)
                    return
                if parsed_path.path.endswith(TOKEN_UCU) and self.command == 'GET':
                    # Köprü sayfasının `fetch` yedeği: jeton sorgu dizesinde.
                    token = query_params.get('access_token', [None])[0]
                    if token:
                        self._handle_received_token(token)
                        return

                if 'code' in query_params:
                    code = query_params['code'][0]
                    # Use the client from the auth server
                    success = self.anilist_client.exchange_code_for_token(code) if self.anilist_client else False

                    if success:
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(b"""
                        <html>
                        <body>
                        <h2>AniList Authentication Successful!</h2>
                        <p>You can close this window and return to the application.</p>
                        <script>
                            window.close();
                        </script>
                        </body>
                        </html>
                        """)
                        # try fetch user and shutdown server
                        try:
                            if self.anilist_client:
                                self.anilist_client.get_current_user()
                            if self._auth_server and self._auth_server.server:
                                # shutdown in a separate thread to avoid deadlock
                                threading.Thread(target=self._auth_server.server.shutdown, daemon=True).start()
                            if self._auth_server and self._auth_server.on_success:
                                try:
                                    self._auth_server.on_success()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    else:
                        self.send_response(400)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(b"""
                        <html>
                        <body style='background:#0f0f0f;color:#ff6b6b;font-family:Segoe UI,Arial,sans-serif;'>
                        <h2>Giris Basarisiz</h2>
                        <p>Lutfen tekrar deneyin.</p>
                        <p>Not: Redirect URI uygulama ayarlarindaki ile birebir ayni olmali.</p>
                        </body>
                        </html>
                        """)
                else:
                    self.send_response(400)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b"<html><body style='background:#0f0f0f;color:#ff6b6b;font-family:Segoe UI,Arial,sans-serif;'><h2>Gecersiz Istek</h2><p>Parametreler eksik.</p></body></html>")

            def log_message(self, format, *args):
                # Suppress server logs
                pass

            def do_POST(self):
                parsed_path = urlparse(self.path)
                if parsed_path.path.endswith(TOKEN_UCU):
                    length = int(self.headers.get('Content-Length', '0') or 0)
                    body = self.rfile.read(length) if length > 0 else b""
                    try:
                        payload = json.loads(body.decode('utf-8') or '{}')
                    except Exception:
                        payload = {}
                    token = payload.get('access_token')
                    if token:
                        self._handle_received_token(token)
                        return
                    self.send_response(400)
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def _handle_received_token(self, token: str):
                try:
                    if self.anilist_client:
                        self.anilist_client.set_access_token(token)
                        # Optionally fetch user to validate
                        self.anilist_client.get_current_user()
                    govde = ("<html><head><meta charset='utf-8'></head>"
                             "<body style='background:#0f0f0f;color:#e6e6e6;"
                             "font-family:Segoe UI,Arial,sans-serif;padding:32px;'>"
                             "<h2>Giris basarili</h2>"
                             "<p>Bu sekmeyi kapatabilirsiniz.</p>"
                             "<script>setTimeout(function(){window.close();},1500);</script>"
                             "</body></html>").encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(govde)))
                    self.end_headers()
                    self.wfile.write(govde)
                    # shutdown + callback
                    if self._auth_server and self._auth_server.server:
                        threading.Thread(target=self._auth_server.server.shutdown, daemon=True).start()
                    if self._auth_server and self._auth_server.on_success:
                        try:
                            self._auth_server.on_success()
                        except Exception:
                            pass
                except Exception:
                    self.send_response(500)
                    self.end_headers()

        try:
            # Allow address reuse and threaded handling to reduce port lock issues on Windows
            class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
                allow_reuse_address = True
                daemon_threads = True

            # Create handler with client and auth_server reference
            def handler_factory(*args, **kwargs):
                return CallbackHandler(*args, anilist_client=self.client, auth_server=self, **kwargs)

            self.server = ThreadedTCPServer(("", port), handler_factory)
            # İstenen port 0 olabilir (işletim sistemi seçer); gerçekten
            # bağlanılan portu basmak sorun ararken tek işe yarayan bilgi.
            print(f"Starting auth server on port {self.server.server_address[1]}")
            self.server.serve_forever()
        except Exception as e:
            print(f"Server error: {e}")
# Global AniList client instance
#
# `client_secret` bilerek BOŞ: değer kaynağa girerse hem herkese açık depoda
# hem derlenmiş EXE'de okunabilir olur. Secret'ı `__init__` çalışma anında
# çözer (ortam değişkeni → kullanıcı yapılandırması → boş); bulunamazsa giriş
# secret istemeyen Implicit akışa düşer.
anilist_client = AniListClient(
    client_id=VARSAYILAN_CLIENT_ID,
    client_secret="",
    redirect_uri=VARSAYILAN_REDIRECT_URI,
)
