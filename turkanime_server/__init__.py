"""TurkAnime sunucu tarafı.

İki bileşen:
    ``app.py``   — istemcilerin canlı sorguladığı Flask API'si
    ``crawler/`` — kaynakları yavaşça gezip AnimeDepo şemasında arşiv üreten
                   arka plan tarayıcısı (Faz 11)

Paket olarak işaretlenmesi `python -m turkanime_server.crawler` çağrısının
namespace-paket keşfine bağlı kalmaması içindir.
"""
