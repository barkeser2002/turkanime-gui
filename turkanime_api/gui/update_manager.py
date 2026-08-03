"""Eski CustomTkinter GUI'sinin güncelleme diyaloğu.

Sürüm karşılaştırma, indirme, SHA-256 doğrulama ve klasör açma mantığı artık
`turkanime_api.common.updater`'da: Qt tarafı da aynı çekirdeği kullanıyor, iki
arayüzde iki farklı davranış oluşmasın diye. Burada kalan yalnızca CTk arayüzü.
"""
import os
import threading
from tkinter import messagebox

import customtkinter as ctk
import requests

from turkanime_api.common import updater
from turkanime_api.common.ui_helpers import create_progress_section
from turkanime_api.common.utils import get_arch, get_os
from turkanime_api.cli.dosyalar import Dosyalar


class UpdateManager:
    """GUI için otomatik güncelleme yönetim sistemi."""

    def __init__(self, parent_window, current_version=None, dosyalar=None):
        self.parent = parent_window
        self.current_version = updater.mevcut_surum(current_version)
        self.dosyalar = dosyalar or Dosyalar()
        self.version_url = updater.VERSION_URL
        # Paket anahtarları "windows"/"linux"/"macos"; `get_platform()`
        # ("windows_x64") ile aranınca hiçbir zaman eşleşme bulunmuyordu.
        self.platform = get_os()
        self.arch = get_arch()

    def indirme_dizini(self):
        """Paketin ineceği klasör: `ayarlar.json`'daki "indirilenler".

        Sabit `~/Downloads` değil — kullanıcı klasörü değiştirdiyse dosya orada
        olmuyor ve "İndirme Konumunu Aç" yanlış klasörü açıyordu.
        """
        try:
            hedef = self.dosyalar.ayarlar.get("indirilenler") or ""
        except Exception:
            hedef = ""
        return hedef or os.path.join(os.path.expanduser("~"), "Downloads")

    def check_for_updates(self, silent=False):
        """Güncelleme kontrolü yap."""
        try:
            version_data = updater.surum_bilgisi_getir(self.version_url)
        except (requests.RequestException, ValueError) as exc:
            if not silent:
                messagebox.showerror("Güncelleme Hatası",
                                     f"Güncelleme kontrolü yapılamadı:\n{exc}")
            return False, None

        if not updater.guncelleme_var_mi(version_data, self.current_version):
            if not silent:
                messagebox.showinfo("Güncelleme Kontrolü", "Uygulamanız güncel!")
            return False, None
        if silent:
            return True, version_data
        return self._show_update_dialog(version_data)

    def _is_newer_version(self, latest_version, current_version, version_data=None):
        """Versiyon karşılaştırması yap (bkz. `common.updater.yeni_mi`)."""
        return updater.yeni_mi(latest_version, current_version)

    def _show_update_dialog(self, version_data):
        """Güncelleme dialog'u göster."""
        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("Güncelleme Mevcut")
        dialog.geometry("500x400")
        dialog.transient(self.parent)
        dialog.grab_set()

        title_label = ctk.CTkLabel(dialog, text="🚀 Güncelleme Mevcut!",
                                   font=ctk.CTkFont(size=20, weight="bold"))
        title_label.pack(pady=(20, 10))

        version_text = (f"Mevcut versiyon: {self.current_version}\n"
                        f"Yeni versiyon: {version_data.get('version', '?')}\n"
                        f"Yayın tarihi: {str(version_data.get('release_date', ''))[:10]}")
        ctk.CTkLabel(dialog, text=version_text).pack(pady=(0, 20))

        changelog_label = ctk.CTkLabel(dialog, text="Değişiklikler:",
                                       font=ctk.CTkFont(weight="bold"))
        changelog_label.pack(anchor="w", padx=20)

        changelog_text = ctk.CTkTextbox(dialog, height=100)
        changelog_text.pack(fill="x", padx=20, pady=(5, 20))
        changelog_text.insert(
            "0.0", version_data.get("changelog", "Değişiklik bilgileri bulunamadı."))
        changelog_text.configure(state="disabled")

        progress_label, progress_bar, buttons_frame = create_progress_section(dialog)

        update_successful = False

        def download_update():
            """Güncellemeyi indir."""
            download_btn.configure(state="disabled", text="İndiriliyor...")
            later_btn.configure(state="disabled")

            def download_worker():
                nonlocal update_successful
                paket = updater.platform_paketi(version_data, self.platform)
                if paket is None:
                    progress_label.configure(text="❌ Bu platform desteklenmiyor")
                    download_btn.configure(state="normal", text="Tekrar Dene")
                    return

                progress_label.configure(text="Güncelleme indiriliyor...")
                progress_bar.set(0.05)

                def ilerleme(inen, toplam):
                    if toplam:
                        progress_bar.set(min(1.0, inen / toplam))

                try:
                    # Doğrulama çekirdekte: özet uyuşmazsa dosya silinir ve
                    # kurulum talimatı HİÇ gösterilmez.
                    filepath = updater.indir_ve_dogrula(
                        paket["url"], self.indirme_dizini(), paket["checksum"],
                        ilerleme)
                except Exception as exc:
                    progress_label.configure(text=f"❌ Hata: {exc}")
                    download_btn.configure(state="normal", text="Tekrar Dene")
                    return

                progress_bar.set(1.0)
                progress_label.configure(text="✅ Güncelleme başarıyla indirildi!")
                update_successful = True

                self.parent.after(2000, dialog.destroy)
                self.parent.after(
                    2500,
                    lambda: self._show_install_instructions(
                        filepath, os.path.basename(filepath)))

            threading.Thread(target=download_worker, daemon=True).start()

        download_btn = ctk.CTkButton(buttons_frame, text="⬇️ Güncellemeyi İndir",
                                     command=download_update,
                                     fg_color="#4ecdc4", hover_color="#45b7aa")
        download_btn.pack(side="left", padx=(0, 10))

        later_btn = ctk.CTkButton(buttons_frame, text="⏭️ Daha Sonra",
                                  command=dialog.destroy, fg_color="#666666")
        later_btn.pack(side="left")

        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        return update_successful, version_data

    def _show_install_instructions(self, filepath, filename=None):
        """Kurulum talimatlarını göster."""
        instructions = ctk.CTkToplevel(self.parent)
        instructions.title("Kurulum Talimatları")
        instructions.geometry("400x300")
        instructions.transient(self.parent)

        title_label = ctk.CTkLabel(instructions, text="📦 Güncelleme Kurulumu",
                                   font=ctk.CTkFont(size=16, weight="bold"))
        title_label.pack(pady=(20, 10))

        text = updater.kurulum_talimati(filepath, self.platform)
        ctk.CTkLabel(instructions, text=text, wraplength=350).pack(pady=(0, 20))

        open_btn = ctk.CTkButton(
            instructions, text="📂 İndirme Konumunu Aç",
            command=lambda: self.open_download_location(filepath))
        open_btn.pack(pady=(0, 10))

        ctk.CTkButton(instructions, text="Tamam",
                      command=instructions.destroy).pack()

        instructions.update_idletasks()
        x = (instructions.winfo_screenwidth() - instructions.winfo_width()) // 2
        y = (instructions.winfo_screenheight() - instructions.winfo_height()) // 2
        instructions.geometry(f"+{x}+{y}")

    def open_download_location(self, filepath=None):
        """İndirilen dosyanın klasörünü aç (sabit `~/Downloads` DEĞİL)."""
        dizin = os.path.dirname(filepath) if filepath else ""
        dizin = dizin or self.indirme_dizini()
        if not updater.konumu_ac(dizin, self.platform):
            messagebox.showinfo(
                "Bilgi",
                f"İndirme konumu açılamadı. Klasörü elle açın:\n{dizin}")
            return False
        return True
