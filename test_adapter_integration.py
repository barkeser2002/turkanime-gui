import sys
from turkanime_api.gui.main import get_openani_episodes, get_openani_streams
from turkanime_api.sources.adapter import AdapterAnime, AdapterBolum
from turkanime_api.sources.openani import get_episode_streams

def make_stream_provider(es):
    def provider(url):
        try:
            print(f"DEBUG: Calling get_episode_streams with {es}")
            res = get_episode_streams(es)
            print(f"DEBUG: Results: {res}")
            return res
        except Exception as ex:
            print(f"ERROR inside provider: {ex}")
            return []
    return provider

ada = AdapterAnime(slug="oshi-no-ko", title="Oshi no Ko")
ep_slug = "oshi-no-ko/1/1"
ep_title = "1. Bölüm"

ab = AdapterBolum(
    url=f"https://openani.me/anime/{ep_slug}",
    title=ep_title,
    anime=ada,
    stream_provider=make_stream_provider(ep_slug),
    player_name="OPENANI"
)

print("Starting best_video()...")
def my_callback(data):
    print(f"Callback: {data}")

vid = ab.best_video(callback=my_callback)
if vid:
    print(f"Success! Best video url: {vid.url}")
else:
    print("Failed to get best video!")
