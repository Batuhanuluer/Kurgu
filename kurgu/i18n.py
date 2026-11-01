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
    "Transition": "Geçiş", "Type": "Tür", "Delete Transition": "Geçişi Sil",
    "Tip: drag the transition's edges on the timeline to change its length.":
        "İpucu: süresini değiştirmek için zaman çizelgesinde geçişin kenarlarını sürükleyin.",
    "No effects yet. Drag one from the Effects panel or pick above.":
        "Henüz efekt yok. Efektler panelinden sürükleyin ya da yukarıdan seçin.",
    "Remove All Effects": "Tüm Efektleri Kaldır", "Remove": "Kaldır", "applied": "uygulandı",
    "Effects can only be applied to video or image clips":
        "Efektler yalnızca video ve görsel kliplerine uygulanabilir",
    "Preview error": "Önizleme hatası",
    "These two clips are a continuous shot cut with the razor, so both sides show the same picture at the cut. Wipes and irises replace the image with the same image and look like nothing happens (Premiere behaves the same).\n\nTo see the transition: delete a piece between the two clips (or use two different clips), then close the gap.":
        "Bu iki klip, jiletle kesilmiş kesintisiz tek bir çekim; kesme noktasında iki taraf aynı "
        "görüntüyü gösteriyor. Silme ve iris geçişleri görüntüyü yine aynı görüntüyle değiştirdiği "
        "için hiçbir şey olmuyormuş gibi görünür (Premiere'de de böyledir).\n\nGeçişi görmek için: "
        "iki klibin arasından bir parça silin (ya da iki farklı klip kullanın), sonra boşluğu kapatın.",
    "Toggle animation (stopwatch)": "Animasyonu aç/kapat (kronometre)",
    "Previous keyframe": "Önceki anahtar kare", "Next keyframe": "Sonraki anahtar kare",
    "Add / remove keyframe": "Anahtar kare ekle / kaldır",
    "Move the playhead over the clip to set keyframes":
        "Anahtar kare için oynatma kafasını klibin üzerine getirin",
    "Keyframe": "Anahtar Kare", "Linear": "Doğrusal", "Ease In/Out": "Yumuşak (Ease)",
    "Hold": "Sabit (Hold)", "Delete Keyframe": "Anahtar Kareyi Sil",
    "Previous / next keyframe": "Önceki / sonraki anahtar kare",
    "Drag to move, drag corners to scale": "Taşımak için sürükleyin, ölçeklemek için köşeleri çekin",
    "Render Effects": "Efektleri Render Et", "Delete Render Files": "Render Dosyalarını Sil",
    "Render": "Render", "Rendering effects…": "Efektler render ediliyor…",
    "Nothing to render: all effect sections are ready":
        "Render edilecek bir şey yok: tüm efektli bölümler hazır",
    "Render finished: effect sections play smoothly":
        "Render bitti: efektli bölümler artık akıcı oynar",
    "Render files deleted": "Render dosyaları silindi",
    "The preview cache can hold about {n} frames at this playback resolution; rendering the first part from the playhead. Lower the playback resolution to render more.":
        "Önizleme önbelleği bu oynatma çözünürlüğünde yaklaşık {n} kare tutabiliyor; oynatma "
        "kafasından itibaren ilk kısım render ediliyor. Daha fazlası için oynatma çözünürlüğünü düşürün.",
    "Playback": "Oynatma", "Full": "Tam",
    "Playback resolution: lower = smoother playback with heavy effects":
        "Oynatma çözünürlüğü: düşük = ağır efektlerde daha akıcı oynatma",
    "Unlinked: video and audio can now be selected separately":
        "Bağlantı kaldırıldı: görüntü ve ses artık ayrı ayrı seçilebilir",
    "Link": "Bağla",
    "Auto Save": "Otomatik Kaydetme", "Off": "Kapalı", "Every {n} min": "Her {n} dakikada",
    "Open Auto-Saved Project…": "Otomatik Kaydedilen Projeyi Aç…",
    "Auto saved": "Otomatik kaydedildi", "Auto save failed": "Otomatik kaydetme başarısız",
    "Recover project": "Projeyi kurtar", "Kurgu did not close properly last time.":
        "Kurgu geçen sefer düzgün kapanmadı.",
    "An auto-saved copy of “{name}” from {when} is available. Open it?":
        "“{name}” projesinin {when} tarihli otomatik kaydı bulundu. Açılsın mı?",
    "Recover": "Kurtar", "Not now": "Şimdi değil", "Untitled project": "Adsız proje",
    "recovered": "kurtarıldı",
    "Recovered project opened. Save it (Ctrl+S) to keep the changes.":
        "Kurtarılan proje açıldı. Değişiklikleri korumak için kaydedin (Ctrl+S).",
    "Check for Updates…": "Güncellemeleri Denetle…",
    "Check for updates at startup": "Açılışta güncellemeleri denetle",
    "Could not reach GitHub:": "GitHub'a ulaşılamadı:",
    "You are using the latest version ({v}).": "En son sürümü kullanıyorsunuz ({v}).",
    "New version: {v}": "Yeni sürüm: {v}", "Update available": "Güncelleme var",
    "Kurgu {new} is available (you have {old}).": "Kurgu {new} yayınlandı (sizdeki: {old}).",
    "Update Now": "Şimdi Güncelle", "Download Page": "İndirme Sayfası", "Later": "Sonra",
    "Downloading update…": "Güncelleme indiriliyor…",
    "Color Match": "Renk Eşleştirme", "LUT (.cube)": "LUT (.cube)", "LUT File": "LUT Dosyası",
    "Match Brightness": "Parlaklığı da Eşle", "Choose .cube file…": ".cube dosyası seç…",
    "Reference": "Referans", "none yet": "henüz seçilmedi", "Reference…": "Referans…",
    "Re-analyze": "Yeniden Ölç",
    "Pick an image or video from the project as the color reference":
        "Projeden renk referansı olacak bir görsel ya da video seçin",
    "Measure the clip again (e.g. after changing a LUT before this effect)":
        "Klibi yeniden ölç (ör. bu efektten önceki LUT değişince)",
    "Tip: right-click an image in the Project panel → Use as Color Reference, then right-click clips → Apply Color Palette.":
        "İpucu: Proje panelinde bir görsele sağ tık → Renk Referansı Olarak Kullan, sonra kliplere sağ tık → Renk Paletini Uygula.",
    "Use as Color Reference": "Renk Referansı Olarak Kullan",
    "Apply Color Palette": "Renk Paletini Uygula",
    "Color reference copied: {name}. Select clips → right-click → Apply Color Palette.":
        "Renk referansı kopyalandı: {name}. Klipleri seçin → sağ tık → Renk Paletini Uygula.",
    "Color palette applied to {n} clip(s)": "Renk paleti {n} klibe uygulandı",
    "Copied reference": "Kopyalanan referans",
    "Import an image first (drag it into the Project panel)":
        "Önce bir görsel içe aktarın (Proje paneline sürükleyin)",
    "Clip re-analyzed": "Klip yeniden ölçüldü",
    "Gap": "Boşluk", "Ripple Delete Gap": "Boşluğu Sil (Kaydırarak)", "Gap closed": "Boşluk kapatıldı",
    "Can't close the gap: linked clips on another track would overlap":
        "Boşluk kapatılamadı: başka izdeki bağlı klipler üst üste binecek",
    "no adjacent clip on this side, so the transition goes to/from black. Close the gap to blend two clips.":
        "bu tarafta bitişik klip yok, geçiş siyaha/siyahtan yapılacak. İki klibi karıştırmak "
        "için aradaki boşluğu kapatın.",
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
