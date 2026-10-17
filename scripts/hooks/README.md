# Git hooks project ini

`pre-commit` menjalankan `python/cek.py` (273 pemeriksaan, ~4 detik) dan
**menolak commit kalau ada yang gagal**.

Diaktifkan lewat `git config core.hooksPath scripts/hooks`. Konfigurasi itu
bersifat per-salinan, jadi kalau repo ini di-copy ke mesin lain, jalankan
perintah itu sekali lagi di sana.

Kenapa ada: kode rusak pernah tersimpan di project ini berhari-hari tanpa
ada yang menyadarinya. Hook ini satu-satunya penjaga di sini yang tidak
butuh siapa pun sadar lebih dulu.

Lewati sekali (hanya kalau benar-benar perlu): `git commit --no-verify`
