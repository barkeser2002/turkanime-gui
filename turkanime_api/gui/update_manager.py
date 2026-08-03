import requests
import os
import subprocess
from tkinter import messagebox
import customtkinter as ctk
import toml
from turkanime_api.common.utils import get_platform, get_arch
from turkanime_api.common.ui_helpers import create_progress_section
from turkanime_api.cli.dosyalar import Dosyalar
from turkanime_api.version import __version__ as APP_VERSION


class UpdateManager:
    """GUI için otomatik güncelleme yönetim sistemi."""

    def __init__(self, parent_window, current_version=None, dosyalar=None):
        self.parent = parent_window
        
        # Eğer dışarıdan current_version verilmediyse dahili APP_VERSION'ı kullan
        if current_version is None:
            current_version = APP_VERSION
        
        # Geliştirme ortamında pyproject.toml'dan versiyonu çekmeyi dene
        # (exe içinde bu dosya olmayacağı için hata alırsa APP_VERSION kalır)
        try:
            pyproject_path = os.path.join(
                os.path.dirname(__file__),
                '..', '..',
                'pyproject.toml'
            )
            with open(pyproject_path, 'r', encoding='utf-8') as f:
                pyproject_data = toml.load(f)
            current_version = pyproject_data['tool']['poetry']['version']
        except Exception as e:
            print(f"Versiyon okunamadı: {e}")
            # APP_VERSION zaten yukarıda current_version'a atanmış durumda
            pass
        
        self.current_version = current_version
        self.dosyalar = dosyalar or Dosyalar()
        self.version_url = (
            "https://raw.githubusercontent.com/barkeser2002/"
            "turkanime-gui/main/docs/version.json"
        )
        self.platform = get_platform()
        self.arch = get_arch()

    def check_for_updates(self, silent=False):
        """Güncelleme kontrolü yap."""
        try:
            response = requests.get(self.version_url, timeout=10)
            response.raise_for_status()
            version_data = response.json()

            latest_version = version_data.get("version", "0.0.0")

            if self._is_newer_version(latest_version, self.current_version, version_data):
                if silent:
                    return True, version_data
                else:
                    return self._show_update_dialog(version_data)
            else:
                if not silent:
                    messagebox.showinfo("Güncelleme Kontrolü",
                                      "Uygulamanız güncel!")
                return False, None

        except requests.RequestException as e:
            if not silent:
                messagebox.showerror("Güncelleme Hatası",
                                   f"Güncelleme kontrolü yapılamadı:\n{str(e)}")
            return False, None

    def _is_newer_version(self, latest_version, current_version, version_data=None):
        """Versiyon karşılaştırması yap."""
        try:
            latest_parts = [int(x) for x in latest_version.split('.')]
            current_parts = [int(x) for x in current_version.split('.')]

            for i in range(max(len(latest_parts), len(current_parts))):
                latest_num = latest_parts[i] if i < len(latest_parts) else 0
                current_num = current_parts[i] if i < len(current_parts) else 0

                if latest_num > current_num:
                    return True
                elif latest_num < current_num:
                    return False

            # Versiyonlar tamamen aynıysa güncelleme yok
            return False
        except Exception:
            return False

    def _show_update_dialog(self, version_data):
        """Güncelleme dialog'u göster."""
        dialog = ctk.CTkToplevel(self.parent)
        dialog.title("Güncelleme Mevcut")
        dialog.geometry("500x400")
        dialog.transient(self.parent)
        dialog.grab_set()

        # Başlık
        title_label = ctk.CTkLabel(dialog, text="🚀 Güncelleme Mevcut!",
                                 font=ctk.CTkFont(size=20, weight="bold"))
        title_label.pack(pady=(20, 10))

        # Versiyon bilgileri
        version_text = f"Mevcut versiyon: {self.current_version}\n"
        version_text += f"Yeni versiyon: {version_data['version']}\n"
        version_text += f"Yayın tarihi: {version_data['release_date'][:10]}"

        version_label = ctk.CTkLabel(dialog, text=version_text)
        version_label.pack(pady=(0, 20))

        # Changelog
        changelog_label = ctk.CTkLabel(dialog, text="Değişiklikler:",
                                     font=ctk.CTkFont(weight="bold"))
        changelog_label.pack(anchor="w", padx=20)

        changelog_text = ctk.CTkTextbox(dialog, height=100)
        changelog_text.pack(fill="x", padx=20, pady=(5, 20))
        changelog_text.insert(
            "0.0",
            version_data.get("changelog", "Değişiklik bilgileri bulunamadı.")
        )
        changelog_text.configure(state="disabled")

        # Progress bar ve butonlar
        progress_label, progress_bar, buttons_frame = create_progress_section(dialog)
        
        update_successful = False

        def download_update():
            """Güncellemeyi indir."""
            download_btn.configure(state="disabled", text="İndiriliyor...")
            later_btn.configure(state="disabled")

            def download_worker():
                nonlocal update_successful
                try:
                    platform_data = version_data.get("platforms", {}).get(self.platform)
                    if not platform_data:
                        progress_label.configure(text="❌ Bu platform desteklenmiyor")
                        return

                    download_url = platform_data["url"]
                    expected_checksum = platform_data.get("checksum")

                    progress_label.configure(text="Güncelleme indiriliyor...")
                    progress_bar.set(0.3)

                    # Dosyayı indir
                    response = requests.get(download_url, stream=True, timeout=30)
                    response.raise_for_status()

                    filename = download_url.split("/")[-1]
                    
                    # Ayarlar.json'dan indirilenler klasörünü kullan
                    download_dir = self.dosyalar.ayarlar.get("indirilenler", os.path.join(os.path.expanduser("~"), "Downloads"))
                    
                    # Downloads klasörü yoksa oluştur
                    os.makedirs(download_dir, exist_ok=True)
                    
                    filepath = os.path.join(download_dir, filename)

                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0

                    with open(filepath, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if total_size > 0:
                                    progress = downloaded / total_size * 0.7
                                    progress_bar.set(0.3 + progress)

                    progress_label.configure(text="İndirme tamamlandı, doğrulama yapılıyor...")
                    progress_bar.set(1.0)

                    # Checksum kontrolü
                    if expected_checksum:
                        import hashlib
                        sha256 = hashlib.sha256()
                        with open(filepath, 'rb') as f:
                            for chunk in iter(lambda: f.read(4096), b""):
                                sha256.update(chunk)
                        actual_checksum = sha256.hexdigest()

                        if actual_checksum != expected_checksum:
                            progress_label.configure(text="❌ Dosya bozuk, tekrar deneyin")
                            os.remove(filepath)
                            return

                    progress_label.configure(text="✅ Güncelleme başarıyla indirildi!")
                    update_successful = True

                    # 2 saniye sonra dialog'u kapat
                    self.parent.after(2000, dialog.destroy)

                    # Kullanıcıya kurulum talimatı ver
                    self.parent.after(
                        2500,
                        lambda: self._show_install_instructions(filepath, filename)
                    )

                except requests.RequestException as e:
                    progress_label.configure(text=f"❌ Hata: {str(e)}")
                    download_btn.configure(state="normal", text="Tekrar Dene")

            import threading
            threading.Thread(target=download_worker, daemon=True).start()

        download_btn = ctk.CTkButton(buttons_frame, text="⬇️ Güncellemeyi İndir",
                                   command=download_update,
                                   fg_color="#4ecdc4", hover_color="#45b7aa")
        download_btn.pack(side="left", padx=(0, 10))

        def skip_update():
            """Güncellemeyi atla."""
            dialog.destroy()

        later_btn = ctk.CTkButton(buttons_frame, text="⏭️ Daha Sonra",
                                command=skip_update,
                                fg_color="#666666")
        later_btn.pack(side="left")

        # Dialog'u ortala
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        return update_successful, version_data

    def _show_install_instructions(self, filepath, filename):
        """Kurulum talimatlarını göster."""
        instructions = ctk.CTkToplevel(self.parent)
        instructions.title("Kurulum Talimatları")
        instructions.geometry("400x300")
        instructions.transient(self.parent)

        title_label = ctk.CTkLabel(instructions, text="📦 Güncelleme Kurulumu",
                                 font=ctk.CTkFont(size=16, weight="bold"))
        title_label.pack(pady=(20, 10))

        if self.platform == "windows":
            text = (
                "1. Mevcut uygulamayı kapatın\n"
                f"2. İndirilen dosya: Downloads/{filename}\n"
                "3. Eski uygulama dosyasını yedekleyin\n"
                "4. Yeni dosyayı eski dosyanın yerine kopyalayın\n"
                "5. Uygulamayı yeniden başlatın"
            )
        elif self.platform == "linux":
            text = (
                "1. Mevcut uygulamayı kapatın\n"
                "2. Terminal açın\n"
                f"3. chmod +x ~/Downloads/{filename}\n"
                f"4. ~/Downloads/{filename} komutu ile çalıştırın"
            )
        elif self.platform == "macos":
            text = (
                "1. Mevcut uygulamayı kapatın\n"
                "2. İndirilen dosyayı Applications klasörüne taşıyın\n"
                "3. Güvenlik ayarlarından uygulamaya izin verin"
            )
        else:
            text = f"Dosya Downloads klasörüne indirildi: {filename}\nPlatformunuz için manuel kurulum gerekebilir."

        text_label = ctk.CTkLabel(instructions, text=text, wraplength=350)
        text_label.pack(pady=(0, 20))

        def open_download_location():
            """İndirme konumunu aç."""
            try:
                download_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                if self.platform == "windows":
                    os.startfile(download_dir)
                elif self.platform == "linux":
                    subprocess.run(["xdg-open", download_dir], timeout=5, stderr=subprocess.DEVNULL)
                elif self.platform == "macos":
                    subprocess.run(["open", download_dir], timeout=5, stderr=subprocess.DEVNULL)
            except Exception:
                messagebox.showinfo("Bilgi", f"İndirme konumu açılamadı. Lütfen klasörü manuel olarak açınız:\n{download_dir}")

        open_btn = ctk.CTkButton(instructions, text="📂 İndirme Konumunu Aç",
                               command=open_download_location)
        open_btn.pack(pady=(0, 10))

        close_btn = ctk.CTkButton(instructions, text="Tamam",
                                command=instructions.destroy)
        close_btn.pack()

        # Dialog'u ortala
        instructions.update_idletasks()
        x = (instructions.winfo_screenwidth() - instructions.winfo_width()) // 2
        y = (instructions.winfo_screenheight() - instructions.winfo_height()) // 2
        instructions.geometry(f"+{x}+{y}")
