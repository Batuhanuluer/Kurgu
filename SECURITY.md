# Güvenlik

## Açık bildirmek

Bir güvenlik açığı bulduysanız lütfen herkese açık bir issue açmayın; GitHub'daki
**Security → Report a vulnerability** (özel bildirim) bağlantısını kullanın.

## Tehdit modeli ve korumalar

Kurgu, başkalarından gelen dosyaları açar: `.kurgu` projeleri, videolar, görseller,
`.cube` LUT'lar. Bu dosyalar güvenilmeyen girdi olarak ele alınır.

| Risk | Koruma |
|---|---|
| Proje dosyası, kaydetme yolunu başka yere yönlendirir (ör. Başlangıç klasörüne `.bat`) | Otomatik kayıt bilgisi yalnızca dosya gerçekten `~/.kurgu/autosave/` içindeyse dikkate alınır; kurtarılan projede Ctrl+S her zaman Farklı Kaydet sorar; projeler yalnızca `.kurgu` uzantısıyla kaydedilir |
| Projedeki `\\sunucu\paylaşım` (UNC/WebDAV) yolları Windows'ta NTLM kimliğini sızdırır | Ağ yolları dosya sistemine hiç dokunulmadan tespit edilir; kullanıcıya sorulur, varsayılan "onlarsız aç"tır. Komut satırında `--allow-network-paths` gerekir |
| Dosyadaki adların HTML olarak yorumlanması (`<img src=\\sunucu\…>`) | Dosyadan gelen metinler düz metin olarak gösterilir |
| Sahte güncelleme | Kendini güncelleme yalnızca çevrim dışı Ed25519 anahtarıyla imzalı `SHA256SUMS.txt` ile; özet uyuşmazsa kurulum silinir. Yalnızca GitHub adreslerinden HTTPS ile indirilir |
| Bozuk/kötü niyetli LUT | Boyut sınırı (en çok 256³) |
| Tedarik zinciri (CI) | Action'lar commit SHA'sıyla sabit, yazma izni yalnızca yayın işinde, bağımlılıklar ve Inno Setup sürümü sabit |

Proje dosyaları JSON'dur; `pickle`, `eval` ya da `exec` kullanılmaz. Alt süreçler kabuk
olmadan (liste argümanla) çalıştırılır.

## Bilinen sınırlar

- **FFmpeg (PyAV):** Video çözme FFmpeg ile yapılır; kötü niyetli bir video FFmpeg'deki bir
  açığı hedefleyebilir. Paketlenmiş sürüm, içindeki FFmpeg ile birlikte güncellenir — güncel
  sürümü kullanın. `packaging/requirements-release.txt` içindeki `av` sürümü düzenli
  yükseltilmelidir.
- **Kod imzalama:** Windows kurulumu henüz Authenticode ile imzalı değildir (SmartScreen
  uyarısı görülebilir). Güncelleme bütünlüğü yukarıdaki Ed25519 imzasıyla sağlanır.
- **Geliştirici hesabı:** GitHub hesabında iki adımlı doğrulama açık olmalı, `v*`
  etiketleri için etiket koruma kuralı tanımlanmalıdır.
