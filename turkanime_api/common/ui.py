"""
UI bileşenleri için modül.
Optimize edilmiş bölüm listesi - tek liste, kaynak butonları ile.
"""

import customtkinter as ctk
from typing import List, Dict, Any, Callable, Optional
import threading
import re
from .adapters import AniListAdapter, TurkAnimeAdapter, AnimeciXAdapter, AnizleAdapter


def extract_episode_info(title: str) -> tuple[int, int]:
    """Bölüm başlığından sezon ve bölüm numarasını çıkar.
    
    Desteklenen formatlar:
    - "Anime İsmi 1. Bölüm" → (1, 1)  # sezon 1, bölüm 1
    - "1. Bölüm" → (1, 1)
    - "Bölüm 1" → (1, 1)
    - "Episode 1" → (1, 1)
    - "EP 1" → (1, 1)
    - "01" → (1, 1)
    - "S01E05" → (1, 5)  # sezon 1, bölüm 5
    - "S02E03" → (2, 3)  # sezon 2, bölüm 3
    - "1x05" → (1, 5)
    - "2x03" → (2, 3)
    - "Sezon 2 Bölüm 5" → (2, 5)
    - "2. Sezon 5. Bölüm" → (2, 5)
    
    Returns:
        tuple[int, int]: (sezon_numarası, bölüm_numarası)
    """
    if not title:
        return (1, 0)
    
    # Önce temizle
    title = title.strip()
    
    # S01E05 formatı (sezon ve bölüm)
    match = re.search(r'[Ss](\d+)[Ee](\d+)', title)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    
    # 1x05 formatı
    match = re.search(r'(\d+)[xX](\d+)', title)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    
    # "Sezon X Bölüm Y" veya "X. Sezon Y. Bölüm" formatı
    match = re.search(r'[Ss]ezon\s*(\d+).*?[Bb][öoÖO]l[üuÜU]m\s*(\d+)', title)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    
    match = re.search(r'(\d+)\s*\.\s*[Ss]ezon.*?(\d+)\s*\.\s*[Bb][öoÖO]l[üuÜU]m', title)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    
    # "X. Bölüm" formatı (en yaygın Türkçe format) - sezon 1 varsayılan
    match = re.search(r'(\d+)\s*\.\s*[Bb][öoÖO]l[üuÜU]m', title)
    if match:
        return (1, int(match.group(1)))
    
    # "Bölüm X" formatı
    match = re.search(r'[Bb][öoÖO]l[üuÜU]m\s*[:\-]?\s*(\d+)', title)
    if match:
        return (1, int(match.group(1)))
    
    # "Episode X" veya "EP X" formatı
    match = re.search(r'[Ee]pisode\s*[:\-]?\s*(\d+)', title)
    if match:
        return (1, int(match.group(1)))
    
    match = re.search(r'[Ee][Pp]\s*[:\-]?\s*(\d+)', title)
    if match:
        return (1, int(match.group(1)))
    
    # Sadece sayı (örn: "01", "1", "001")
    match = re.search(r'^(\d+)$', title)
    if match:
        return (1, int(match.group(1)))
    
    # Son çare: title'daki ilk sayıyı al
    match = re.search(r'(\d+)', title)
    if match:
        return (1, int(match.group(1)))
    
    return (1, 0)


def extract_episode_number(title: str) -> int:
    """Bölüm başlığından bölüm numarasını çıkar (geriye uyumluluk).
    
    Desteklenen formatlar:
    - "Anime İsmi 1. Bölüm" → 1
    - "1. Bölüm" → 1
    - "Bölüm 1" → 1
    - "Episode 1" → 1
    - "EP 1" → 1
    - "01" → 1
    - "S01E05" → 5
    - "1x05" → 5
    """
    _, episode = extract_episode_info(title)
    return episode


def normalize_episode_title(title: str, episode_num: int, season_num: int = 1) -> str:
    """Bölüm başlığını normalize et - anime ismini kaldır, sezon bilgisini koru.
    
    "Anime İsmi 1. Bölüm" → "1. Bölüm"
    "S02E05" → "S02E05"
    "2. Sezon 5. Bölüm" → "S02E05"
    """
    if not title:
        if season_num > 1:
            return f"S{season_num:02d}E{episode_num:02d}"
        return f"{episode_num}. Bölüm"
    
    # Sezon bilgisi varsa S0XE0Y formatında döndür
    if season_num > 1:
        return f"S{season_num:02d}E{episode_num:02d}"
    
    # Zaten normalize edilmişse (sadece bölüm bilgisi varsa)
    if re.match(r'^\d+\s*\.\s*[Bb][öÖ]l[üÜ]m', title.strip()):
        return title.strip()
    
    if re.match(r'^[Bb][öÖ]l[üÜ]m\s*\d+', title.strip()):
        return title.strip()
    
    if re.match(r'^[Ee]pisode\s*\d+', title.strip(), re.IGNORECASE):
        return title.strip()
    
    if re.match(r'^[Ss]\d+[Ee]\d+', title.strip()):
        return title.strip()
    
    # "Anime İsmi X. Bölüm" formatından "X. Bölüm"ü çıkar
    match = re.search(r'(\d+\s*\.\s*[Bb][öÖ]l[üÜ]m.*?)$', title)
    if match:
        return match.group(1).strip()
    
    # Varsayılan format
    return f"{episode_num}. Bölüm"


class AccordionSourceEpisodeList:
    """Optimize edilmiş bölüm listesi - tek liste, kaynak butonları ile.
    
    Performans iyileştirmeleri:
    - Her kaynak için ayrı liste yerine tek birleşik liste
    - Bölüm satırlarında kaynak butonları
    - Lazy loading (sayfalama)
    - Batch rendering (widget'ları gruplar halinde oluştur)
    """

    def __init__(self, parent, sources_data: Dict[str, List[Dict[str, Any]]],
                 max_episodes_per_source: int = 50,
                 on_play: Optional[Callable] = None, on_download: Optional[Callable] = None,
                 on_match: Optional[Callable] = None, db_matches: Optional[Dict[str, Dict]] = None,
                 user_id: Optional[str] = None, anime_name: Optional[str] = None,
                 main_window=None):
        self.parent = parent
        self.sources_data = sources_data
        self.max_episodes_per_source = max_episodes_per_source
        self.on_play = on_play
        self.on_download = on_download
        self.on_match = on_match
        self.db_matches = db_matches or {}
        self.user_id = user_id
        self.anime_name = anime_name or "unknown"
        self.main_window = main_window  # Ana pencere referansı

        # Seçim yönetimi - main_window ile paylaşılan
        self.episode_checkboxes = {}  # {ep_key: (var, checkbox_widget, episode_obj)}
        self.selected_episodes = set()
        
        # Birleştirilmiş bölüm listesi
        self.merged_episodes = self._merge_episodes()
        
        # Sayfalama
        self.current_page = 0
        self.episodes_per_page = 15
        self.loaded_count = 0
        
        # Bölüm durumları
        self.episode_status = {}
        if self.user_id:
            self._load_user_episode_status()

        # Adapter'lar
        self.adapters = {
            "AniList": AniListAdapter(),
            "TürkAnime": TurkAnimeAdapter(),
            "AnimeciX": AnimeciXAdapter(),
            "Anizle": AnizleAdapter()
        }

        # Ana frame
        self.main_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True)

        # Header ve kontroller
        self._create_header()
        
        # Bölüm listesi
        self._create_episode_list()

    def _merge_episodes(self) -> List[Dict[str, Any]]:
        """Tüm kaynaklardan bölümleri birleştir ve sezon+bölüm numarasına göre grupla.
        
        Farklı kaynaklardan gelen bölüm isimleri farklı formatta olabilir:
        - TürkAnime: "Anime İsmi 1. Bölüm"
        - AnimeciX: "1. Bölüm"
        - Anizle: "Bölüm 1"
        - Çoklu sezon: "S02E05", "2x05", "2. Sezon 5. Bölüm"
        
        Bu metod sezon ve bölüm numarasını akıllıca çıkarır ve eşleştirir.
        """
        # (sezon, bölüm) tuple'ına göre gruplama
        episode_map = {}  # {(season, ep_number): {'title': str, 'sources': {source_name: episode_obj}}}
        
        for source_name, episodes in self.sources_data.items():
            if not episodes:
                continue
            for idx, ep in enumerate(episodes):
                # Önce mevcut episode_number ve season_number'ı kontrol et
                ep_num = ep.get('episode_number', ep.get('number', 0))
                season_num = ep.get('season_number', ep.get('season', 1)) or 1
                ep_title = ep.get('title', '')
                
                # Eğer episode_number yoksa veya 0 ise, title'dan çıkar
                if not ep_num or ep_num == 0:
                    season_num, ep_num = extract_episode_info(ep_title)
                elif season_num == 1:
                    # Episode number var ama sezon yok, title'dan sezon bilgisini al
                    title_season, _ = extract_episode_info(ep_title)
                    if title_season > 1:
                        season_num = title_season
                
                # Hala bulunamadıysa, index'i kullan (1'den başlayarak)
                if not ep_num or ep_num == 0:
                    ep_num = idx + 1
                
                # Unique key: (sezon, bölüm)
                key = (season_num, ep_num)
                
                # Bölüm başlığını normalize et
                normalized_title = normalize_episode_title(ep_title, ep_num, season_num)
                
                if key not in episode_map:
                    episode_map[key] = {
                        'season': season_num,
                        'number': ep_num,
                        'title': normalized_title,
                        'sources': {}
                    }
                episode_map[key]['sources'][source_name] = ep
        
        # Sıralı liste oluştur (önce sezon, sonra bölüm numarasına göre)
        merged = sorted(episode_map.values(), key=lambda x: (x['season'], x['number']))
        return merged

    def _load_user_episode_status(self):
        """Kullanıcının episode status'lerini API'den yükler."""
        if not self.user_id:
            return
        try:
            from .db import api_manager
            status_data = api_manager.get_user_episode_status(self.user_id)
            if status_data:
                self.episode_status = status_data
        except Exception as e:
            print(f"Episode status yükleme hatası: {e}")

    def _create_header(self):
        """Başlık, Tümünü Seç, arama ve aksiyon butonları."""
        header_frame = ctk.CTkFrame(self.main_frame, fg_color="#2a2a2a", corner_radius=8)
        header_frame.pack(fill="x", pady=(0, 10), padx=5)

        # Üst satır - Başlık ve butonlar
        top_row = ctk.CTkFrame(header_frame, fg_color="transparent")
        top_row.pack(fill="x", padx=10, pady=(8, 4))

        # Sol taraf - Başlık ve kaynak bilgisi
        left_frame = ctk.CTkFrame(top_row, fg_color="transparent")
        left_frame.pack(side="left", fill="x", expand=True)

        title_label = ctk.CTkLabel(left_frame, text=f"📺 {len(self.merged_episodes)} Bölüm",
                                 font=ctk.CTkFont(size=16, weight="bold"),
                                 text_color="#ffffff")
        title_label.pack(side="left")

        # Kaynak sayısı
        active_sources = [s for s, eps in self.sources_data.items() if eps]
        source_text = f" • {len(active_sources)} kaynak"
        source_label = ctk.CTkLabel(left_frame, text=source_text,
                                  font=ctk.CTkFont(size=12),
                                  text_color="#888888")
        source_label.pack(side="left", padx=(10, 0))

        # Sağ taraf - Butonlar
        right_frame = ctk.CTkFrame(top_row, fg_color="transparent")
        right_frame.pack(side="right")

        # Tümünü Seç / Seçimi Kaldır butonu
        self.select_all_var = ctk.BooleanVar(value=False)
        self.select_all_btn = ctk.CTkButton(right_frame, text="☑️ Tümünü Seç",
                                          width=120, height=30,
                                          fg_color="#4ecdc4", hover_color="#45b7aa",
                                          command=self._toggle_select_all)
        self.select_all_btn.pack(side="left", padx=(0, 8))

        # Seçili sayısı etiketi
        self.selected_count_label = ctk.CTkLabel(right_frame, text="0 seçili",
                                                font=ctk.CTkFont(size=12),
                                                text_color="#888888")
        self.selected_count_label.pack(side="left", padx=(0, 8))

        # Eşleştirme butonu
        if self.on_match:
            self.match_btn = ctk.CTkButton(right_frame, text="🔗 Eşleştir",
                                         width=100, height=30,
                                         fg_color="#9b59b6", hover_color="#8e44ad",
                                         command=self._show_match_dialog)
            self.match_btn.pack(side="left")

        # Alt satır - Arama kutusu
        search_row = ctk.CTkFrame(header_frame, fg_color="transparent")
        search_row.pack(fill="x", padx=10, pady=(4, 8))

        # Arama kutusu
        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(search_row, 
                                        placeholder_text="🔍 Bölüm ara (numara veya isim)...",
                                        textvariable=self.search_var,
                                        width=250, height=32)
        self.search_entry.pack(side="left")
        self.search_var.trace_add("write", self._on_search_change)

        # Arama sonuç sayısı
        self.search_result_label = ctk.CTkLabel(search_row, text="",
                                               font=ctk.CTkFont(size=11),
                                               text_color="#888888")
        self.search_result_label.pack(side="left", padx=(10, 0))

        # Temizle butonu
        self.clear_search_btn = ctk.CTkButton(search_row, text="✕",
                                             width=32, height=32,
                                             fg_color="#444444", hover_color="#555555",
                                             command=self._clear_search)
        self.clear_search_btn.pack(side="left", padx=(5, 0))

    def _on_search_change(self, *args):
        """Arama kutusu değiştiğinde çağrılır."""
        self._refresh_episode_list()

    def _clear_search(self):
        """Arama kutusunu temizle."""
        self.search_var.set("")
        self._refresh_episode_list()

    def _get_filtered_episodes(self) -> List[Dict[str, Any]]:
        """Arama filtresine göre bölümleri döndür."""
        query = self.search_var.get().strip().lower()
        
        if not query:
            return self.merged_episodes
        
        filtered = []
        for ep in self.merged_episodes:
            ep_num = ep['number']
            ep_title = ep['title'].lower()
            
            # Numara ile arama
            if query.isdigit():
                if str(ep_num) == query or str(ep_num).startswith(query):
                    filtered.append(ep)
            # Metin ile arama
            elif query in ep_title or query in str(ep_num):
                filtered.append(ep)
        
        return filtered

    def _refresh_episode_list(self):
        """Bölüm listesini yenile (arama sonuçları için)."""
        # Mevcut widget'ları temizle
        for widget in self.episodes_frame.winfo_children():
            widget.destroy()
        
        # Checkbox referanslarını temizle (sadece görüntülenenler için)
        self.episode_checkboxes.clear()
        self.loaded_count = 0
        
        # Yeni listeyi yükle
        self._load_more_episodes()

    def _create_episode_list(self):
        """Bölüm listesini oluştur - içsel scroll olmadan, ana scroll kullanılır."""
        # Normal frame (scroll yok - ana sayfa scroll'u kullanılır)
        self.episodes_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.episodes_frame.pack(fill="both", expand=True, padx=5)
        
        # İlk sayfayı yükle
        self._load_more_episodes()

    def _load_more_episodes(self):
        """Daha fazla bölüm yükle (sayfalama) - maksimum 30 bölüm göster."""
        # Filtrelenmiş bölümleri al
        filtered_episodes = self._get_filtered_episodes()
        
        start_idx = self.loaded_count
        # Maksimum 30 bölüm göster (arama yoksa)
        max_display = self.episodes_per_page  # 30
        end_idx = min(start_idx + max_display, len(filtered_episodes))
        
        if start_idx >= len(filtered_episodes):
            # Arama sonucu yok mesajı
            if len(filtered_episodes) == 0 and self.search_var.get().strip():
                no_result = ctk.CTkLabel(self.episodes_frame, 
                                        text="🔍 Arama sonucu bulunamadı",
                                        font=ctk.CTkFont(size=14),
                                        text_color="#888888")
                no_result.pack(pady=20)
            return
        
        # Batch halinde widget oluştur (performans için)
        episodes_to_load = filtered_episodes[start_idx:end_idx]
        self._create_episode_rows(episodes_to_load, start_idx)
        
        self.loaded_count = end_idx
        
        # Arama sonuç sayısını güncelle
        query = self.search_var.get().strip()
        if query:
            self.search_result_label.configure(text=f"{len(filtered_episodes)} sonuç")
        else:
            self.search_result_label.configure(text="")
        
        # Daha fazla yükle butonu - sadece arama varsa veya çok fazla bölüm varsa
        remaining = len(filtered_episodes) - self.loaded_count
        if remaining > 0:
            load_more_btn = ctk.CTkButton(self.episodes_frame, 
                                        text=f"📥 Daha Fazla Yükle ({remaining} bölüm kaldı)",
                                        height=35, fg_color="#4ecdc4", hover_color="#45b7aa",
                                        command=self._on_load_more_click)
            load_more_btn.pack(fill="x", pady=10, padx=10)
            self.load_more_btn = load_more_btn

    def _on_load_more_click(self):
        """Daha fazla yükle butonuna tıklandığında."""
        # Butonu kaldır
        if hasattr(self, 'load_more_btn'):
            self.load_more_btn.destroy()
        # Daha fazla yükle

        self._load_more_episodes()

    def _create_episode_rows(self, episodes: List[Dict], start_number: int):
        """Bölüm satırlarını oluştur - batch rendering ile optimize edilmiş."""
        self._batch_render_episodes(episodes, 0, start_number)

    def _batch_render_episodes(self, episodes: List[Dict], batch_start: int, start_number: int):
        """Bölüm satırlarını 10'luk batch'ler halinde after_idle ile render et."""
        BATCH_SIZE = 10
        batch_end = min(batch_start + BATCH_SIZE, len(episodes))

        for idx in range(batch_start, batch_end):
            merged_ep = episodes[idx]
            ep_num = merged_ep['number']
            ep_title = merged_ep['title']
            sources = merged_ep['sources']
            
            # Bölüm frame'i
            ep_frame = ctk.CTkFrame(self.episodes_frame, fg_color="#1e1e1e",
                                  corner_radius=6, height=50)
            ep_frame.pack(fill="x", pady=2, padx=5)
            ep_frame.pack_propagate(False)
            
            # Sol taraf - Checkbox ve bölüm adı
            left_frame = ctk.CTkFrame(ep_frame, fg_color="transparent")
            left_frame.pack(side="left", fill="y", padx=(10, 5))
            
            # Checkbox
            ep_key = f"{ep_num}_{ep_title}"
            var = ctk.BooleanVar(value=False)
            
            checkbox = ctk.CTkCheckBox(left_frame, text="",
                                     variable=var, width=24,
                                     command=lambda v=var, k=ep_key: self._on_episode_toggle(v, k))
            checkbox.pack(side="left", pady=12)
            
            # Bölüm numarası ve adı
            ep_label = ctk.CTkLabel(left_frame, 
                                  text=f"{ep_num:02d}. {ep_title}",
                                  font=ctk.CTkFont(size=13),
                                  text_color="#ffffff")
            ep_label.pack(side="left", padx=(5, 0), pady=12)
            
            # Kaynak butonları frame
            sources_frame = ctk.CTkFrame(ep_frame, fg_color="transparent")
            sources_frame.pack(side="right", padx=10, pady=8)
            
            # Her kaynak için buton - dinamik renk ve kısaltma haritası
            source_colors = {
                "TürkAnime": ("#ffd93d", "#e6c235"),
                "AnimeciX": ("#ff6b6b", "#e65c5c"),
                "Anizle": ("#9b59b6", "#8e44ad"),
                "TRAnimeİzle": ("#e84393", "#c0392b"),
                "OpenAnime": ("#4ecdc4", "#45b7aa"),
            }
            
            # Kaynak kısaltmaları
            source_short_names = {
                "TürkAnime": "TA",
                "AnimeciX": "CX", 
                "Anizle": "AZ",
                "TRAnimeİzle": "TR",
                "OpenAnime": "OA",
            }
            
            # Dinamik: mevcut tüm kaynaklar için buton oluştur
            for source_name in sorted(sources.keys(), reverse=True):
                if source_name in sources:
                    ep_obj = sources[source_name].get('obj')
                    colors = source_colors.get(source_name, ("#666666", "#555555"))
                    short_name: str = source_short_names.get(source_name) or source_name[:2]
                    
                    # Kaynak butonu frame
                    source_btn_frame = ctk.CTkFrame(sources_frame, fg_color=colors[0], corner_radius=4)
                    source_btn_frame.pack(side="right", padx=(4, 0))
                    
                    # Kaynak adı etiketi (kısa)
                    src_label = ctk.CTkLabel(source_btn_frame, text=short_name,
                                           font=ctk.CTkFont(size=9, weight="bold"),
                                           text_color="#000000", width=24)
                    src_label.pack(side="left", padx=(4, 0), pady=2)
                    
                    # Oynat butonu
                    if self.on_play:
                        play_btn = ctk.CTkButton(source_btn_frame, text="▶",
                                               width=26, height=22,
                                               fg_color="#333333", hover_color="#444444",
                                               text_color="#ffffff",
                                               font=ctk.CTkFont(size=10),
                                               corner_radius=3,
                                               command=lambda e=ep_obj, s=source_name: self._safe_play(e, s))
                        play_btn.pack(side="left", padx=1, pady=2)
                    
                    # İndir butonu
                    if self.on_download:
                        dl_btn = ctk.CTkButton(source_btn_frame, text="⬇",
                                             width=26, height=22,
                                             fg_color="#333333", hover_color="#444444",
                                             text_color="#ffffff",
                                             font=ctk.CTkFont(size=10),
                                             corner_radius=3,
                                             command=lambda e=ep_obj, s=source_name: self._safe_download(e, s))
                        dl_btn.pack(side="left", padx=(1, 4), pady=2)
            
            # Episode referansını sakla (seçim için) - TÜM kaynakları sakla
            self.episode_checkboxes[ep_key] = (var, checkbox, sources)

        # Sonraki batch varsa after_idle ile planla
        if batch_end < len(episodes):
            self.episodes_frame.after_idle(
                lambda: self._batch_render_episodes(episodes, batch_end, start_number)
            )

    def _on_episode_toggle(self, var: ctk.BooleanVar, ep_key: str):
        """Bölüm checkbox'ı değiştiğinde."""
        if var.get():
            self.selected_episodes.add(ep_key)
        else:
            self.selected_episodes.discard(ep_key)
        
        # Seçili sayısını güncelle
        self._update_selected_count()
        
        # Main window'a bildir
        if self.main_window and hasattr(self.main_window, 'episodes_vars'):
            self._sync_with_main_window()

    def _update_selected_count(self):
        """Seçili bölüm sayısını güncelle."""
        count = len(self.selected_episodes)
        filtered = self._get_filtered_episodes()
        self.selected_count_label.configure(text=f"{count} seçili")
        
        # Tümünü Seç butonunu güncelle (filtrelenmiş listeye göre)
        displayed_keys = {f"{ep['number']}_{ep['title']}" for ep in filtered}
        selected_in_view = self.selected_episodes & displayed_keys
        all_selected = len(selected_in_view) == len(filtered) and len(filtered) > 0
        
        if all_selected:
            self.select_all_btn.configure(text="☐ Seçimi Kaldır")
            self.select_all_var.set(True)
        else:
            self.select_all_btn.configure(text="☑️ Tümünü Seç")
            self.select_all_var.set(False)

    def _toggle_select_all(self):
        """Tümünü seç / seçimi kaldır - sadece görüntülenen bölümler için."""
        select_all = not self.select_all_var.get()
        filtered = self._get_filtered_episodes()
        
        # Görüntülenen bölümler için key oluştur
        for ep in filtered:
            ep_key = f"{ep['number']}_{ep['title']}"
            
            # Checkbox'ı güncelle (eğer yüklenmişse)
            if ep_key in self.episode_checkboxes:
                var, checkbox, _ = self.episode_checkboxes[ep_key]
                var.set(select_all)
            
            # Seçim setini güncelle
            if select_all:
                self.selected_episodes.add(ep_key)
            else:
                self.selected_episodes.discard(ep_key)
        
        self._update_selected_count()
        self._sync_with_main_window()

    def _sync_with_main_window(self):
        """Ana pencere ile seçimleri senkronize et."""
        if not self.main_window:
            return
        
        # episodes_vars listesini güncelle
        self.main_window.episodes_vars = []
        self.main_window.episodes_objs = []
        
        for ep_key, (var, checkbox, sources) in self.episode_checkboxes.items():
            if sources:
                # İlk mevcut kaynaktaki episode objesini kullan
                for src_name, ep_data in sources.items():
                    if ep_data and ep_data.get('obj'):
                        self.main_window.episodes_vars.append((var, ep_data['obj']))
                        if var.get():
                            self.main_window.episodes_objs.append(ep_data['obj'])
                        break

    def get_selected_episodes(self, source_name: Optional[str] = None) -> List[Any]:
        """Seçili bölümlerin objelerini döndür.
        
        Args:
            source_name: Belirli bir kaynaktan bölümleri al. None ise ilk mevcut kaynağı kullan.
        
        Returns:
            Episode objelerinin listesi
        """
        selected = []
        for ep_key in self.selected_episodes:
            # Önce episode_checkboxes'tan dene (yüklenmiş bölümler)
            if ep_key in self.episode_checkboxes:
                _, _, sources = self.episode_checkboxes[ep_key]
                if sources:
                    if source_name and source_name in sources:
                        ep_data = sources[source_name]
                        if ep_data and ep_data.get('obj'):
                            selected.append(ep_data['obj'])
                    elif source_name is None:
                        for src_name, ep_data in sources.items():
                            if ep_data and ep_data.get('obj'):
                                selected.append(ep_data['obj'])
                                break
            else:
                # Yüklenmemiş bölümler için merged_episodes'dan al
                for ep in self.merged_episodes:
                    if f"{ep['number']}_{ep['title']}" == ep_key:
                        sources = ep['sources']
                        if sources:
                            if source_name and source_name in sources:
                                ep_data = sources[source_name]
                                if ep_data and ep_data.get('obj'):
                                    selected.append(ep_data['obj'])
                            elif source_name is None:
                                for src_name, ep_data in sources.items():
                                    if ep_data and ep_data.get('obj'):
                                        selected.append(ep_data['obj'])
                                        break
                        break
        return selected

    def get_selected_episodes_with_sources(self) -> List[Dict[str, Any]]:
        """Seçili bölümleri TÜM kaynaklarıyla birlikte döndür.
        
        Returns:
            [{"ep_key": str, "title": str, "sources": {source_name: episode_obj}}]
        """
        selected = []
        for ep_key in self.selected_episodes:
            if ep_key in self.episode_checkboxes:
                _, _, sources = self.episode_checkboxes[ep_key]
                if sources:
                    # ep_key'den bölüm numarası ve başlığı çıkar
                    parts = ep_key.split('_', 1)
                    ep_num = parts[0] if parts else "?"
                    ep_title = parts[1] if len(parts) > 1 else ep_key
                    
                    source_objs = {}
                    for src_name, ep_data in sources.items():
                        if ep_data and ep_data.get('obj'):
                            source_objs[src_name] = ep_data['obj']
                    
                    if source_objs:
                        selected.append({
                            "ep_key": ep_key,
                            "number": ep_num,
                            "title": ep_title,
                            "sources": source_objs
                        })
        return selected

    def get_available_sources(self) -> List[str]:
        """Seçili bölümlerde mevcut olan kaynakları döndür."""
        available = set()
        for ep_key in self.selected_episodes:
            # Önce episode_checkboxes'tan dene
            if ep_key in self.episode_checkboxes:
                _, _, sources = self.episode_checkboxes[ep_key]
                if sources:
                    for src_name in sources.keys():
                        available.add(src_name)
            else:
                # Yüklenmemiş bölümler için merged_episodes'dan al
                for ep in self.merged_episodes:
                    if f"{ep['number']}_{ep['title']}" == ep_key:
                        sources = ep['sources']
                        if sources:
                            for src_name in sources.keys():
                                available.add(src_name)
                        break
        return list(available)

    def show_source_selection_dialog(self, callback: Callable[[str], None]):
        """Toplu indirme için kaynak seçim dialogu göster."""
        available_sources = self.get_available_sources()
        
        if not available_sources:
            return
        
        if len(available_sources) == 1:
            # Tek kaynak varsa direkt callback'i çağır
            callback(available_sources[0])
            return
        
        # Dialog penceresi
        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("📥 Kaynak Seçimi")
        dialog.geometry("400x300")
        dialog.transient(self.parent)
        dialog.grab_set()
        
        # Başlık
        title_label = ctk.CTkLabel(dialog, text="Hangi kaynaktan indirilsin?",
                                 font=ctk.CTkFont(size=16, weight="bold"))
        title_label.pack(pady=15)
        
        # Seçili bölüm sayısı
        count_label = ctk.CTkLabel(dialog, text=f"{len(self.selected_episodes)} bölüm seçili",
                                  font=ctk.CTkFont(size=12),
                                  text_color="#888888")
        count_label.pack(pady=(0, 15))
        
        # Kaynak renkleri
        source_colors = {
            "TürkAnime": ("#ffd93d", "#e6c235"),
            "AnimeciX": ("#ff6b6b", "#e65c5c"),
            "Anizle": ("#9b59b6", "#8e44ad"),
            "OpenAnime": ("#4ecdc4", "#45b7aa")
        }
        
        # Kaynak butonları
        for source_name in available_sources:
            colors = source_colors.get(source_name, ("#666666", "#555555"))
            
            # Bu kaynakta kaç bölüm var?
            ep_count = 0
            for ep_key in self.selected_episodes:
                if ep_key in self.episode_checkboxes:
                    _, _, sources = self.episode_checkboxes[ep_key]
                    if sources and source_name in sources:
                        ep_count += 1
            
            btn = ctk.CTkButton(dialog, 
                              text=f"📥 {source_name}\n({ep_count} bölüm mevcut)",
                              width=300, height=50,
                              fg_color=colors[0], hover_color=colors[1],
                              text_color="#000000",
                              font=ctk.CTkFont(size=14, weight="bold"),
                              command=lambda s=source_name: [dialog.destroy(), callback(s)])
            btn.pack(pady=5)
        
        # İptal butonu
        cancel_btn = ctk.CTkButton(dialog, text="❌ İptal", 
                                  width=300, height=35,
                                  fg_color="#444444", hover_color="#555555",
                                  command=dialog.destroy)
        cancel_btn.pack(pady=15)

    def _safe_play(self, episode_obj, source_name: Optional[str] = None):
        """Güvenli oynatma."""
        if self.on_play and episode_obj:
            try:
                self.on_play(episode_obj)
            except Exception as e:
                print(f"Oynatma hatası: {e}")

    def _safe_download(self, episode_obj, source_name: Optional[str] = None):
        """Güvenli indirme - tek bölüm."""
        if self.on_download and episode_obj:
            try:
                self.on_download(episode_obj)
            except Exception as e:
                print(f"İndirme hatası: {e}")

    def _search_source_anime(self, source_name, search_entry, combo_box, selected_anime):
        """Kaynakta anime ara."""
        if not combo_box:
            return

        query = search_entry.get().strip()
        if not query:
            return

        try:
            # Arama butonunu devre dışı bırak
            search_entry.configure(state="disabled")

            # Arama sonuçlarını getir
            results = self._perform_source_search(source_name, query)

            if results:
                combo_box.configure(values=results)
                combo_box.set(results[0])
                # Arama sonucu seçildiğinde selected_anime'yi güncelle
                selected_anime[source_name] = results[0]
            else:
                combo_box.configure(values=["Sonuç bulunamadı"])
                combo_box.set("Sonuç bulunamadı")
                selected_anime[source_name] = "Sonuç bulunamadı"

        except Exception as e:
            print(f"Arama hatası ({source_name}): {e}")
            combo_box.configure(values=["Arama hatası"])
            combo_box.set("Arama hatası")
            selected_anime[source_name] = "Arama hatası"
        finally:
            # Arama girişini tekrar etkinleştir
            search_entry.configure(state="normal")

    def _perform_source_search(self, source_name, query):
        """Kaynakta arama yap."""
        try:
            adapter = self.adapters.get(source_name)
            if not adapter:
                return [f"{source_name} adaptörü bulunamadı"]

            # Adapter ile arama yap
            results = adapter.search_anime(query, limit=10)

            if results:
                # Sadece başlıkları döndür
                return [title for _, title in results]
            else:
                return ["Sonuç bulunamadı"]

        except Exception as e:
            print(f"Arama hatası ({source_name}): {e}")
            return ["Arama hatası"]

    def _show_match_dialog(self):
        """Eşleştirme dialog'unu göster."""
        if not self.on_match:
            return

        # Dialog penceresi
        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("🎯 Anime Eşleştirme")
        dialog.geometry("700x600")
        dialog.transient(self.parent)
        dialog.grab_set()

        # Başlık
        title_label = ctk.CTkLabel(dialog, text="2 Kaynaktan 1'er Anime Seçin",
                                 font=ctk.CTkFont(size=16, weight="bold"))
        title_label.pack(pady=10)

        # Kaynak seçim frame'leri
        selections_frame = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        selections_frame.pack(fill="both", expand=True, padx=20, pady=10)

        selected_anime = {}
        search_entries = {}
        search_buttons = {}
        combo_boxes = {}

        # Sadece eşleştirme için kullanılacak kaynaklar
        matching_sources = {k: v for k, v in self.sources_data.items() if k in ["TürkAnime", "AnimeciX", "OpenAnime"]}

        for source_name, episodes in matching_sources.items():
            if not episodes:
                continue

            # Kaynak frame
            source_frame = ctk.CTkFrame(selections_frame, fg_color="#2a2a2a")
            source_frame.pack(fill="x", pady=5)

            source_label = ctk.CTkLabel(source_frame, text=f"{source_name}",
                                      font=ctk.CTkFont(size=14, weight="bold"))
            source_label.pack(pady=5)

            # Arama kutusu ve butonu
            search_frame = ctk.CTkFrame(source_frame, fg_color="transparent")
            search_frame.pack(fill="x", padx=10, pady=5)

            search_entry = ctk.CTkEntry(search_frame, placeholder_text=f"{source_name}'ta ara...",
                                      width=200)
            search_entry.pack(side="left", padx=(0, 10))

            search_btn = ctk.CTkButton(search_frame, text="🔍 Ara", width=80,
                                     command=lambda src=source_name, entry=search_entry: self._search_source_anime(src, entry, combo_boxes.get(src), selected_anime))
            search_btn.pack(side="left")

            # Mevcut bölümlerden anime seçenekleri
            current_options = list(set([ep.get('anime_title', ep['title']) for ep in episodes]))

            # DB'den önceki eşleşmeleri de ekle
            if source_name in self.db_matches:
                db_anime_title = self.db_matches[source_name].get('anime_title', '')
                if db_anime_title and db_anime_title not in current_options:
                    current_options.insert(0, f"📚 {db_anime_title}")  # DB'den gelenleri başa ekle

            # Anime seçimi için combobox
            combo = ctk.CTkComboBox(source_frame, values=current_options if current_options else ["Arama yapın..."],
                                  command=lambda value, src=source_name: selected_anime.update({src: value}))
            combo.pack(pady=5)

            if current_options:
                # DB'den gelen eşleşmeyi varsayılan olarak seç
                if source_name in self.db_matches:
                    db_anime_title = self.db_matches[source_name].get('anime_title', '')
                    if db_anime_title in current_options:
                        combo.set(f"📚 {db_anime_title}")
                        selected_anime[source_name] = f"📚 {db_anime_title}"
                    else:
                        combo.set(current_options[0])
                        selected_anime[source_name] = current_options[0]
                else:
                    combo.set(current_options[0])
                    selected_anime[source_name] = current_options[0]
            else:
                combo.set("Arama yapın...")
                # Varsayılan değer ata ki seçim zorunlu olmasın
                selected_anime[source_name] = "Arama yapın..."

            # Referansları sakla
            search_entries[source_name] = search_entry
            search_buttons[source_name] = search_btn
            combo_boxes[source_name] = combo

        # Butonlar
        buttons_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons_frame.pack(fill="x", pady=10)

        def on_confirm():
            try:
                print("DEBUG: on_confirm başladı")
                # Geçerli seçimleri kontrol et
                valid_selections = {}
                for source, anime_title in selected_anime.items():
                    if anime_title and anime_title not in ["Arama yapın...", "Sonuç bulunamadı", "Arama hatası"]:
                        # DB'den gelen eşleşmeler için prefix'i kaldır
                        clean_title = anime_title.replace("📚 ", "") if anime_title.startswith("📚 ") else anime_title
                        valid_selections[source] = clean_title

                print(f"DEBUG: selected_anime = {selected_anime}")
                print(f"DEBUG: valid_selections = {valid_selections}")
                print(f"DEBUG: len(valid_selections) = {len(valid_selections)}")

                if len(valid_selections) >= 2:
                    print("DEBUG: on_match çağrılıyor")
                    self._safe_call(self.on_match, valid_selections)
                    print("DEBUG: dialog kapatılıyor")
                    dialog.destroy()
                else:
                    # Hata mesajı
                    error_label = ctk.CTkLabel(buttons_frame, text="❌ Tüm kaynaklardan geçerli anime seçmelisiniz!",
                                             text_color="#ff6b6b")
                    error_label.pack(pady=5)
            except Exception as e:
                print(f"DEBUG: on_confirm exception: {e}")
                import traceback
                traceback.print_exc()

        confirm_btn = ctk.CTkButton(buttons_frame, text="✅ Eşleştir", command=on_confirm)
        confirm_btn.pack(side="right", padx=10)

        cancel_btn = ctk.CTkButton(buttons_frame, text="❌ İptal", command=dialog.destroy)
        cancel_btn.pack(side="right")

    def _safe_call(self, func, *args):
        """Güvenli fonksiyon çağrısı."""
        if func:
            try:
                func(*args)
            except Exception as e:
                print(f"Fonksiyon çağrısı hatası: {e}")

    def destroy(self):
        """Accordion listesini temizle."""
        if hasattr(self, 'main_frame'):
            self.main_frame.destroy()

    def _toggle_watched(self, episode_id, label):
        """Bölüm izlenme durumunu değiştir."""
        current_status = self.episode_status.get(episode_id, {}).get('watched', False)
        new_status = not current_status

        # Durumu güncelle
        if episode_id not in self.episode_status:
            self.episode_status[episode_id] = {'watched': False, 'downloaded': False}
        self.episode_status[episode_id]['watched'] = new_status

        # İkonu güncelle
        if new_status:
            label.configure(text="👁️", text_color="#4ecdc4")
        else:
            label.configure(text="○", text_color="#666666")

        # API'ye kaydet
        if self.user_id:
            try:
                from .db import api_manager
                api_manager.save_user_episode_status(
                    self.user_id, episode_id, new_status,
                    self.episode_status[episode_id].get('downloaded', False)
                )
            except Exception as e:
                print(f"Episode status API kaydetme hatası: {e}")

    def _toggle_downloaded(self, episode_id, label):
        """Bölüm indirme durumunu değiştir."""
        current_status = self.episode_status.get(episode_id, {}).get('downloaded', False)
        new_status = not current_status

        # Durumu güncelle
        if episode_id not in self.episode_status:
            self.episode_status[episode_id] = {'watched': False, 'downloaded': False}
        self.episode_status[episode_id]['downloaded'] = new_status

        # İkonu güncelle
        if new_status:
            label.configure(text="💾", text_color="#ff6b6b")
        else:
            label.configure(text="○", text_color="#666666")

        # API'ye kaydet
        if self.user_id:
            try:
                from .db import api_manager
                api_manager.save_user_episode_status(
                    self.user_id, episode_id,
                    self.episode_status[episode_id].get('watched', False), new_status
                )
            except Exception as e:
                print(f"Episode status API kaydetme hatası: {e}")




class CollapsibleFrame(ctk.CTkFrame):
    """Daraltılabilir/expand edilebilir frame."""

    def __init__(self, parent, title="", **kwargs):
        super().__init__(parent, **kwargs)

        self.is_expanded = True

        # Başlık butonu
        self.title_button = ctk.CTkButton(self, text=f"▶️ {title}",
                                        command=self.toggle,
                                        fg_color="transparent",
                                        text_color="#ffffff",
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        anchor="w", height=35)
        self.title_button.pack(fill="x", padx=10, pady=5)

        # İçerik frame'i
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="x", padx=10, pady=(0, 10))

    def toggle(self):
        """Frame'i aç/kapat."""
        current_text = self.title_button.cget("text")
        if self.is_expanded:
            # Kapat
            self.content_frame.pack_forget()
            self.title_button.configure(text=current_text.replace("🔽", "▶️"))
            self.is_expanded = False
        else:
            # Aç
            self.content_frame.pack(fill="x", padx=10, pady=(0, 10))
            self.title_button.configure(text=current_text.replace("▶️", "🔽"))
            self.is_expanded = True