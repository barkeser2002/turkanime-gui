"""gunicorn yapılandırması — konteynerde API'yi bu çalıştırır.

Eskiden `CMD python app.py` Werkzeug'un geliştirme sunucusunu açıyordu: tek
süreç, üretim için değil. `python app.py` yerel geliştirme için duruyor.

    gunicorn -c gunicorn.conf.py app:app      (çalışma dizini: turkanime_server/)

Çevre: PORT (34665), WORKERS (2), THREADS (4).
"""
import os
import sys

# `app` modülü bu dosyanın yanında; gunicorn hangi dizinden çağrılırsa
# çağrılsın `on_starting` içindeki import bulabilsin.
_BURASI = os.path.dirname(os.path.abspath(__file__))
if _BURASI not in sys.path:
    sys.path.insert(0, _BURASI)

bind = "0.0.0.0:" + os.environ.get("PORT", "34665")
workers = int(os.environ.get("WORKERS", "2"))
threads = int(os.environ.get("THREADS", "4"))
# Kaynak siteler yavaş: tek bir arama/bölüm isteği onlarca saniye sürebiliyor.
timeout = 90
accesslog = "-"
errorlog = "-"


def on_starting(server):  # pylint: disable=unused-argument
    """Ana süreçte bir kez: DB ayarını doğrula, şemayı kur.

    `python app.py` bunu `__main__` altında yapıyordu; gunicorn modülü import
    ettiği için o blok hiç koşmaz. DB ayarı eksikse `SystemExit` gunicorn'u
    açılmadan durdurur.
    """
    from app import _bootstrap
    _bootstrap()
