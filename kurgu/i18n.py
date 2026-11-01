"""Basit çeviri sistemi. Anahtarlar İngilizce; sistem dili Türkçe ise Türkçe gösterilir.

Yeni bir dil eklemek için LANGS sözlüğüne bir sözlük eklemek yeterli.
KURGU_LANG ortam değişkeni ile dil zorlanabilir (ör. KURGU_LANG=en).
"""
import locale
import os

TR = {
    "File": "Dosya", "Edit": "Düzen", "Sequence": "Sekans", "View": "Görünüm", "Help": "Yardım",
    "New Project": "Yeni Proje", "Open Project…": "Proje Aç…", "Save": "Kaydet",
    "Save As…": "Farklı Kaydet…", "Import Media…": "Medya İçe Aktar…", "Export…": "Dışa Aktar…",
    "Quit": "Çıkış", "Undo": "Geri Al", "Redo": "Yinele", "Delete": "Sil",
    "Ripple Delete": "Boşluğu Kapatarak Sil", "Split at Playhead": "Oynatma Kafasında Böl",
    "Select All": "Tümünü Seç", "Add Title": "Başlık Ekle", "Add Video Track": "Video İzi Ekle",
    "Add Audio Track": "Ses İzi Ekle", "Add Marker": "İşaretçi Ekle",
    "Project Settings…": "Proje Ayarları…", "Zoom In": "Yakınlaştır", "Zoom Out": "Uzaklaştır",
    "Zoom to Fit": "Sığdır", "Snapping": "Mıknatıs", "About Kurgu": "Kurgu Hakkında",
    "Keyboard Shortcuts": "Klavye Kısayolları",
    "Selection Tool": "Seçim Aracı", "Razor Tool": "Jilet Aracı",
    "Project": "Proje", "Program": "Program", "Inspector": "Denetçi", "Timeline": "Zaman Çizelgesi",
    "Play / Pause": "Oynat / Duraklat", "Previous Frame": "Önceki Kare", "Next Frame": "Sonraki Kare",
    "Go to Start": "Başa Git", "Go to End": "Sona Git",
    "No clip selected": "Seçili klip yok", "Motion": "Hareket", "Color": "Renk", "Audio": "Ses",
    "Transitions": "Geçişler", "Text": "Metin", "Scale": "Ölçek", "Position X": "Konum X",
    "Position Y": "Konum Y", "Rotation": "Döndürme", "Opacity": "Opaklık", "Speed": "Hız",
    "Brightness": "Parlaklık", "Contrast": "Kontrast", "Saturation": "Doygunluk",
    "Volume": "Ses Düzeyi", "Fade In": "Giriş Geçişi", "Fade Out": "Çıkış Geçişi",
    "Font Size": "Yazı Boyutu", "Text Color…": "Yazı Rengi…", "Reset": "Sıfırla",
    "Drop media here or use File → Import": "Medyayı buraya sürükleyin ya da Dosya → İçe Aktar",
    "Media files": "Medya dosyaları", "Kurgu project": "Kurgu projesi",
    "Export Video": "Videoyu Dışa Aktar", "Resolution": "Çözünürlük", "Quality": "Kalite",
    "High": "Yüksek", "Medium": "Orta", "Small file": "Küçük dosya",
    "Exporting…": "Dışa aktarılıyor…", "Cancel": "İptal", "Export finished": "Dışa aktarma bitti",
    "Export failed": "Dışa aktarma başarısız", "Nothing to export": "Dışa aktarılacak bir şey yok",
    "The timeline is empty.": "Zaman çizelgesi boş.",
    "Unsaved changes": "Kaydedilmemiş değişiklikler",
    "Save changes to the project?": "Projedeki değişiklikler kaydedilsin mi?",
    "Could not import": "İçe aktarılamadı", "Width": "Genişlik", "Height": "Yükseklik",
    "Frame rate": "Kare hızı", "Title": "Başlık", "Enter title text:": "Başlık metnini yazın:",
    "Your Title": "Başlığınız", "Ready": "Hazır", "Saved": "Kaydedildi",
    "Missing media": "Eksik medya", "Mute": "Sessiz", "Hide": "Gizle", "Lock": "Kilitle",
    "Remove from Project": "Projeden Kaldır", "Add to Timeline": "Zaman Çizelgesine Ekle",
    "Free, open-source video editor.": "Özgür ve açık kaynak video düzenleyici.",
    "Saved to": "Kaydedildi:", "Duration": "Süre", "Unlink": "Bağlantıyı Kaldır",
    "Duplicate": "Çoğalt", "Open Containing Folder": "Bulunduğu Klasörü Aç",
    # Efektler ve geçişler
    "Effects": "Efektler", "Video Effects": "Video Efektleri",
    "Video Transitions": "Video Geçişleri", "Search effects…": "Efekt ara…",
    "Drag onto a clip, or double-click to apply to the selection":
        "Bir klibin üzerine sürükleyin ya da seçili kliplere uygulamak için çift tıklayın",
    "Keying": "Anahtarlama", "Color Correction": "Renk Düzeltme",
    "Blur & Sharpen": "Bulanıklaştır ve Keskinleştir", "Stylize": "Stilize",
    "Transform": "Dönüştür", "Chroma Key": "Chroma Key (Yeşil Perde)", "Luma Key": "Luma Key",
    "Key Color": "Anahtar Renk", "Tolerance": "Tolerans", "Edge Softness": "Kenar Yumuşaklığı",
    "Spill Suppression": "Renk Taşması Giderme", "Choke": "Daraltma",
    "Threshold": "Eşik", "Invert": "Ters Çevir", "Black & White": "Siyah Beyaz",
    "Sepia": "Sepya", "Amount": "Miktar", "Hue Shift": "Ton Kaydırma", "Hue": "Ton",
    "Temperature & Tint": "Sıcaklık ve Renk Tonu", "Temperature": "Sıcaklık", "Tint": "Renk Tonu",
    "Levels / Gamma": "Seviyeler / Gama", "Black Point": "Siyah Noktası",
    "White Point": "Beyaz Noktası", "Gamma": "Gama", "Color Overlay": "Renk Kaplaması",
    "Posterize": "Posterleştir", "Levels": "Seviye", "Gaussian Blur": "Gauss Bulanıklığı",
    "Blurriness": "Bulanıklık", "Sharpen": "Keskinleştir", "Vignette": "Vinyet",
    "Size": "Boyut", "Mosaic": "Mozaik", "Block Size": "Blok Boyutu",
    "Film Grain": "Film Greni", "Crop": "Kırp", "Left": "Sol", "Right": "Sağ", "Top": "Üst",
    "Bottom": "Alt", "Horizontal Flip": "Yatay Çevir", "Vertical Flip": "Dikey Çevir",
    "Dissolve": "Çözülme", "Wipe": "Silme", "Slide": "Kaydırma", "Iris": "İris", "Zoom": "Yakınlaştırma",
    "Cross Dissolve": "Çapraz Çözülme", "Dip to Black": "Siyaha Geçiş",
    "Dip to White": "Beyaza Geçiş", "Wipe Right": "Sağa Silme", "Wipe Left": "Sola Silme",
    "Wipe Down": "Aşağı Silme", "Wipe Up": "Yukarı Silme", "Barn Door": "Ahır Kapısı",
    "Clock Wipe": "Saat Silmesi", "Push Left": "Sola İt", "Push Right": "Sağa İt",
    "Slide Left": "Sola Kaydır", "Slide Right": "Sağa Kaydır", "Iris Round": "Yuvarlak İris",
    "Iris Box": "Kutu İris", "Cross Zoom": "Çapraz Yakınlaştırma",
    "Fades": "Kararma", "Start": "Başlangıç", "End": "Bitiş", "None": "Yok",
    "Add effect…": "Efekt ekle…", "Enabled": "Etkin", "Move Up": "Yukarı Taşı",
    "Move Down": "Aşağı Taşı", "Remove Effect": "Efekti Kaldır",
    "Eyedropper: click on the Program monitor": "Damlalık: Program monitörüne tıklayın",
    "Select a clip first": "Önce bir klip seçin", "Copy": "Kopyala", "Cut": "Kes",
    "Paste": "Yapıştır", "Paste Attributes": "Özellikleri Yapıştır",
    "Apply Default Transition": "Varsayılan Geçişi Uygula", "clip(s) copied": "klip kopyalandı",
}

LANGS = {"tr": TR}


def _detect() -> str:
    env = os.environ.get("KURGU_LANG")
    if env:
        return env[:2].lower()
    try:
        from PySide6.QtCore import QLocale
        return QLocale.system().name()[:2].lower()
    except Exception:
        loc = locale.getlocale()[0] or ""
        return loc[:2].lower()


LANG = _detect()
_TABLE = LANGS.get(LANG, {})


def _(text: str) -> str:
    return _TABLE.get(text, text)
