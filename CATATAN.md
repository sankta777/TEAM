# Robot Arm RNV3 — Catatan Kerja

Semua file resmi dari developer (Ruskomponen / github `februandi`), ditata ulang
supaya gampang dinavigasi. Angka-angka teknis di dokumen ini **dibaca langsung
dari source code firmware**, bukan dari video — jadi bisa dipercaya.

---

## Peta folder

| Folder | Isi | Buat siapa |
|---|---|---|
| `python/rnv3.py` | Modul kontrol koordinat X/Y/Z | **jobdesk koordinat** |
| `python/kontak.py` | **Kontak pertama — verifikasi robot, lengan tidak bergerak** | **jalankan paling awal** |
| `python/jog.py` | **Gerakkan lengan pakai tombol panah, divalidasi** | **jobdesk koordinat** |
| `python/pick_place.py` | **Pick & place grid, versi yang sudah benar** | **jobdesk koordinat** |
| `python/aplikasi.py` | **Aplikasi kontrol di browser: jog, ukur, tandai titik, preset** (pengganti jog.py) | **jobdesk koordinat** |
| `python/setelan.py` | Satu berkas angka hasil ukur (`setelan-robot.json`) untuk jog, jalur, pick_place | semua |
| `python/preset.py` | Rekam/putar preset: simpan, validasi rute, sampah (logika murni, tanpa port) | **jobdesk koordinat** |
| `python/cek.py` | **Cek otomatis 428 item, tanpa robot** | semua |
| `python/simulasi.py` | Simulator firmware, buat tes offline | semua |
| `python/jalur.py` | **Middleware: belt + kamera + lengan jadi satu alur sortir** | **integrasi** |
| `python/kontrak_kamera.py` | Kontrak serah-terima dengan tim kamera (deteksi, piksel/mm) | **integrasi** |
| `python/kalibrasi.py` | Homografi piksel → mm. Dipakai lewat `jalur.py --kalibrasi FILE` | **integrasi** |
| `python/konveyor.py` | Kendali belt — mode MOSFET robot atau mode ESP32 | jobdesk sensor |
| `python/esp.py` | Protokol ESP32 (belt + watchdog). **ESP32-nya belum ada** | jobdesk sensor |
| `python/contoh/` | 23 contoh resmi (gripper, vakum, rel, laser, dance, lingkaran) | semua |
| `python/contoh-v2/` | Contoh serial paling minimal & bersih — mulai baca dari sini | semua |
| `python/contoh-pembina/` | 4 file dari pembina (pick&place, gambar, luma, dance) | **jobdesk koordinat** |
| `firmware/v2_02-pembina/` | **config.h kalibrasi robot kalian** | — |
| `python/contoh-sensor/` | 3 contoh baca sensor input | jobdesk sensor |
| `python/untuk-software/` | Contoh gerakan hasil export dari GUI | semua |
| `python/ai/` | Program AI kontrol suara (video #17) | jobdesk AI |
| `firmware/v2_01/` | Firmware Arduino, `RAIL=false`, servo 95/160 | — |
| `firmware/v2_02/` | **Terbaru**: tambah `M105` (baca sensor) + `SPEED_DEFAULT` | — |
| `firmware/v2_01-interface/` | Varian lain, `RAIL=true`, servo 27/115 | — |
| `interface-gui/` | Source GUI PyQt5 (`.py` + `.ui` Qt Designer) | kalau mau modif GUI |
| `software-robot/` | Installer GUI `Rus-setup-0.2.exe` + panduan install | **mulai dari sini** |
| `yat/` | Config YAT (terminal serial alternatif) | opsional |
| `docs/` | Buku panduan 27 hal. (pinout, dimensi) + Mounting Holes | semua |

**Kalau baru pertama pegang:** install `software-robot/Rus-setup-0.2.exe` di
Windows, colok robot, Sambungkan → Kalibrasi, lalu jog lengannya pakai slider.
Itu cara paling cepat paham gerakannya sebelum nyentuh Python.

---

## Aplikasi kontrol di browser — `aplikasi.py` (22 Okt 2026)

```bash
python.exe aplikasi.py          # robot asli (dari terminal WSL pakai python.exe)
python3   aplikasi.py --sim     # simulator, tanpa robot
```

Browser terbuka sendiri di `http://127.0.0.1:8765`. Tidak perlu install apa pun
selain `pyserial`: servernya pustaka bawaan Python, halamannya HTML/CSS/JS biasa
di `python/aplikasi_web/` (tanpa internet, tanpa CDN).

| Tab | Isi |
|---|---|
| Kontrol | Sambung → homing (tombol START) → peta tampak atas, vakum, gripper, Selesai, Parkir, Matikan robot |
| Ukur | Z belt, Z bak, bak koin, bak tutup botol, parkir: jog ke tempatnya lalu **Simpan posisi ini** (tampil juga posisi "sekarang"). **Kembalikan ke angka awal**. Bak/parkir saat rel bukan E=0 → dialog "Rel ke 0 dulu / Tetap simpan". Batas fisik (lipat). Titik tertanda |
| Preset | Rekam (Catat posisi = C, vakum/gripper tercatat otomatis, tunggu), daftar dengan status per langkah, Coba sekali (20 mm/s), Putar N kali / terus, Jalan/Jeda/Berhenti, hapus ke Sampah lalu pulihkan |
| Sortir | Belum dibuat: butuh kamera (TCP), ESP32, dan kalibrasi |

**Preset** (26 Okt 2026, `preset.py` + `cek.py` bagian 23): di dalam satu preset
lengan bergerak lurus seperti direkam; saat masuk ke preset (dan saat loop
kembali ke preset pertama) lengan naik ke tinggi aman dulu. Seluruh putaran
divalidasi SEBELUM bergerak; satu langkah tak terjangkau = tidak jalan sama
sekali. Jeda dan Berhenti berlaku di antara langkah (firmware tidak bisa
membatalkan G0). Berhenti = vakum OFF lalu pulang ke home, motor tetap
terkunci. Disimpan di `python/preset.json` (tidak di-commit, data kerja).

Panel jog dan posisi selalu tampil di kiri, jadi bisa jog sambil mengukur.
Keyboard sama dengan `jog.py` (W/S, A/D, I/K, J/L), plus **Spasi = Berhenti**,
P, M, V, dan `?` untuk panduan.

**Yang dijaga aplikasi** (semua dites di `cek.py` bagian 22, dua di antaranya
diuji mutasi: pengamannya dirusak sengaja dan tesnya terbukti gagal):

- Logika gerak **dipakai ulang dari `jog.Jog`**, tidak ditulis ulang: pengaman
  jangkauan, pembatas percepatan, dan sapuan saat ditahan sama persis.
- Satu thread pekerja memegang port serial; perintah lain ditolak selagi robot
  sibuk.
- Jog tahan butuh **detak** dari browser tiap 100 ms. Tab ditutup / browser hang
  → jog berhenti sendiri dalam 0,35 s + satu potongan.
- **Selesai** = pulang ke home, motor tetap terkunci. **Matikan robot** = parkir
  lalu M18, hanya setelah operator mencentang "sedang memegang lengan".
- Server hanya di `127.0.0.1` dan setiap POST wajib header `X-RNV3`, jadi
  perangkat lain di WiFi maupun halaman web lain tidak bisa menggerakkan robot.

**Belum diuji dengan robot asli** — baru simulator + Chrome headless (tahan,
ketuk, blur, dialog). Uji pertama di robot: jog pelan dengan langkah 0,5 mm.

### Satu berkas setelan — `setelan-robot.json`

Dulu titik parkir ditulis di tiga tempat (`jog.PARKIR`, `jalur.PARKIR_XY` +
`Z_PARKIR`, `pick_place.Z_PARKIR`). Sekarang Z belt, Z bak, posisi bak, dan
parkir dibaca dari `python/setelan-robot.json` oleh ketiganya. Berkas belum
ada = angka bawaan (sama dengan konstanta lama, jadi perilaku tidak berubah).
Diisi dari tab Ukur. `cek.py` men-set `RNV3_SETELAN=-` supaya tes tidak pernah
membaca atau menulis berkas ini.

---

## Jalan cepat (Python)

```bash
pip install pyserial
```

```python
from rnv3 import RNV3, check_reach, xy_limits

with RNV3("COM4") as bot:       # Linux/WSL: "/dev/ttyUSB0"
    bot.home()                  # G28 — WAJIB sebelum gerak apa pun
    bot.move(z=0, f=0)
    bot.goto_xy(-50, 200, f=80) # pindah di bidang XY, Z dipertahankan
    print(bot.position())        # baca koordinat balik dari robot
```

Validasi titik tanpa perlu robot nyala:

```python
check_reach(-50, 200, 0)   # -> (True, '')
check_reach(0, 400, 0)     # -> (False, 'terlalu jauh: radius 323.0 mm > R_MAX 263.8 mm')
```

---

## Sistem koordinat

Robot ini **kartesian**: kamu kirim titik X/Y/Z dalam mm, firmware yang hitung
sudut 3 stepper-nya (inverse kinematics di `firmware/*/robotGeometry.cpp`).

- **X** — kiri/kanan (rotasi badan). 0 = lurus ke depan, kanan positif.
- **Y** — depan/belakang (jauh dari badan).
- **Z** — atas/bawah. 0 = setinggi sumbu bahu.
- **E** — posisi di rel, 0–355 mm. Robot kita **pakai rel**.
- **F** — kecepatan, mm/s.

Posisi setelah `G28` (home): **X=0, Y=217, Z=138, E=0**

### Jangkauan — INI YANG PENTING

Jangkauan robot **tidak tetap**, berubah tergantung Z. Rumus batasnya
(dari `isAllowedPosition()` di `interpolation.cpp`):

```
R_MIN  <=  sqrt( (hypot(X,Y) - 77)^2 + Z^2 )  <=  R_MAX

R_MIN = 90.42 mm      R_MAX = 263.78 mm
Z: -140 .. 210 mm     E: 0 .. 355 mm
```

Radius XY yang valid per ketinggian:

```
  Z = 138  ->   77.0 .. 301.8 mm      (posisi home)
  Z = 100  ->   77.0 .. 321.1 mm
  Z =  50  ->  152.3 .. 336.0 mm
  Z =   0  ->  167.4 .. 340.8 mm      (paling jauh ke depan)
  Z = -50  ->  152.3 .. 336.0 mm
  Z = -95  ->   77.0 .. 323.1 mm      (meja, tanpa rel)
  Z = -131 ->   77.0 .. 305.9 mm      (meja, pakai rel)
```

Kalau titiknya di luar ini, robot **tidak gerak** dan balas
`ERROR: LIMIT REACHED: [...]`. Pakai `check_reach()` biar ketahan di Python dulu.

---

## Daftar perintah

Format: string ASCII, **diakhiri `\r`**. Robot balas beberapa baris info, lalu `ok`.

| Perintah | Fungsi |
|---|---|
| `G0 X.. Y.. Z.. E.. F..` | Gerak linear. Sumbu yang ditulis jalan bersamaan; urutan huruf bebas; sumbu yang tidak ditulis dibiarkan |
| `G4 S<detik>` | Tunggu. Balas `ok` setelah waktunya habis |
| `G28` | Homing / kalibrasi |
| `G90` / `G91` | Mode absolut (default) / relatif |
| `G92 X0 Y0 Z0 E0` | Jadikan posisi fisik sekarang sebagai nilai koordinat tertentu |
| `G100 B<sudut>` | **Gripper servo**, rentang 95–160 |
| `G100 A<sudut>` | Servo tambahan, 0–180 |
| `LG1 ON` / `LG1 OFF` | Output MOSFET 1 (alias `M1` / `M2`) |
| `LG2 ON` / `LG2 OFF` | Output MOSFET 2 (alias `M206` / `M207`) |
| `LG3 ON` / `LG3 OFF` | Output MOSFET 3 (alias `M6` / `M7`) |
| `VACUM ON` / `VACUM OFF` | Gripper vakum — pompa + katup + delay diurus firmware (alias `M209` / `M230`) |
| `M17` / `M18` | Stepper enable / disable |
| `M106` / `M107` | Kipas nyala / mati |
| `M114` | Lapor koordinat sekarang |
| `M119` | Status endstop tiap sumbu |
| `M105` | Status 3 sensor input — **hanya firmware v2_02** |

> **`M3` / `M5` sudah tidak ada.** Itu firmware generasi lama (muncul di video
> awal). Di v2_01 dan v2_02, gripper servo pakai **`G100 B<sudut>`**.

---

## Jebakan

Semua ini hasil baca source code, beberapa tidak disebut di video sama sekali.
Nomor 2 bisa merusak robot secara fisik (robot terguncang sampai bergeser).
Nomor 1, 9, dan 11 gagalnya tidak kelihatan: robot tetap menjawab `ok`,
tapi lengannya tidak sampai atau tidak bergerak sama sekali.

1. **Titik tujuan valid belum menjamin gerakannya berhasil — jalurnya juga
   harus valid.** Firmware memanggil `isAllowedPosition()` di **setiap langkah
   interpolasi** (`interpolation.cpp` ~baris 167), bukan sekali di tujuan.

   Kalau jalur lurusnya keluar jangkauan, firmware **membatalkan gerakan di
   tengah jalan**, membekukan posisi di titik terakhir yang valid, lapor
   `ERROR: LIMIT REACHED`, **tapi tetap kirim `ok`**. Jadi program kamu mengira
   sudah sampai padahal robot berhenti di tempat lain → posisi desync, dan
   semua gerakan sesudahnya ngawur.

   Kasus nyata yang gampang kena: **sapu kiri→kanan di Y dekat.**

   ```
   A = (-250, 120, 0)   valid
   B = ( 250, 120, 0)   valid
   tapi jalur A->B ditolak di t=0.27, di X=-116
   -> radius 90.1 mm < R_MIN 90.4 mm  (nembus lubang mati di dekat badan)
   ```

   Ada "lubang mati" berbentuk bola di sekitar badan robot (radius < 90.42 mm
   dari titik `hypot(X,Y)=77, Z=0`). Garis lurus yang menembusnya akan gagal.

   **Solusi**: pakai `check_path()` sebelum kirim — `rnv3.py` sudah otomatis
   melakukan ini di `move()`. Kalau ditolak, pecah gerakannya jadi beberapa
   langkah yang memutar lubang itu (misal naikkan Z dulu, geser, baru turun).

2. **Kecepatan tinggi pada gerakan PENDEK membuat robot terguncang hebat —
   sampai bergeser sendiri di meja.** Ini bukan soal motor kurang kuat.

   `config.h` memakai `SPEED_PROFILE 2` (kosinus). Artinya **setiap** `G0`
   melakukan ramp penuh: diam → cepat → diam lagi. Dari
   `progress = -cos(t*tmul*PI)/2 + 0.5` di `interpolation.cpp`, puncak
   percepatan satu gerakan adalah:

   ```
   a_puncak = PI^2 * v^2 / (2 * d)
   ```

   Percepatan naik dengan **kuadrat kecepatan** dan berbanding **terbalik
   dengan panjang gerakan**. Jadi `F` besar pada potongan pendek = hentakan
   brutal:

   | kondisi | puncak percepatan | |
   |---|---|---|
   | F20 pada 5 mm | 395 mm/s² | 0.04 g — terbukti mulus |
   | F80 pada 1 mm | 31.583 mm/s² | 3.2 g |
   | F150 pada 2 mm | 55.517 mm/s² | **5.7 g** |
   | F250 pada 1 mm | 308.425 mm/s² | **31 g** |

   Jog kontinu mengirim ~30 potongan per detik, jadi hentakan itu **berulang
   30x per detik**. Itulah yang menggetarkan robot sampai bergeser.

   **Tiga percobaan sebelum ketemu — supaya tidak terulang:**

   | Percobaan | Setelan | Hasil |
   |---|---|---|
   | 1 | kosinus, potongan 0.5–2 mm, F40–150 | `a` sampai **55.517 mm/s²** → getar keras, robot bergeser |
   | 2 | **datar**, potongan 15 mm, F25 | tiap potongan mulai dengan lonjakan `0→25 mm/s` mendadak → **hentakan tiap 0.6 detik** |
   | 3 | **kosinus, potongan panjang** | ramp halus, `a`=200 mm/s², 1.6 transisi/detik ✔ |

   Pelajarannya: **profil dan panjang potongan harus dipilih bersama.**
   Kosinus bagus untuk potongan panjang (rampnya melandai sendiri); datar bagus
   untuk potongan pendek yang beruntun (tidak ada ramp yang berosilasi). Salah
   pasangan menghasilkan dua gejala berbeda yang sama-sama tidak enak —
   percobaan 1 bergetar, percobaan 2 menghentak.

   **Menurunkan kecepatan saja TIDAK cukup.** Itu hanya memperkecil
   amplitudo getaran, bukan menghilangkannya — karena akar masalahnya
   kecepatan **berosilasi** `0 → puncak → 0` sebanyak ~30 kali per detik.

   **Solusi sebenarnya: ganti profil kecepatan jadi datar.**

   ```
   M205 S0
   ```

   `interpolator.setSpeedProfile()` bisa dipanggil **saat jalan**, tanpa upload
   firmware (`robotRNV3_v2_02.ino:315`). Pada profil 0, `progress = t * tmul`
   → kecepatan **konstan** sepanjang gerakan, jadi potongan beruntun pada
   kecepatan sama menyambung tanpa naik-turun. Osilasinya hilang.

   Profil ini ditandai penulis firmware sendiri sebagai *"COCOK UNTUK SOFTWARE
   KONTROL REAL-TIME"*, dan kembali ke bawaan (2) setiap kali port serial
   dibuka karena Arduino ter-reset dan `config.h` dibaca ulang.

   `jog.py` **tidak** memaksa profil tertentu. Bawaannya kosinus (sesuai
   `config.h`), dan tombol `0` menelusuri kosinus → arctan → datar supaya bisa
   dibandingkan langsung di robot. Profil dikembalikan ke kosinus saat keluar.

   **Syarat yang gampang terlewat:** pada profil datar, semua potongan saat
   ditahan harus memakai **kecepatan yang sama**. Kalau berbeda, muncul
   lonjakan kecepatan di sambungan antar potongan dan getarannya kembali.
   Karena itu `batas_speed()` memakai satu nilai tetap (`V_DATAR_MAKS`) untuk
   profil datar, bukan menurunkannya dari panjang potongan seperti pada profil
   kosinus.

   Kalau menulis skrip sendiri **tanpa** `jog.py`, hitung sendiri: jangan
   lewati ~400 mm/s². Rumus praktisnya `v_maks = sqrt(400 * d / 4.93)`.

   Perbaikan yang lebih mendasar: ganti `SPEED_PROFILE` jadi `0` (kurva datar),
   yang oleh penulis firmware sendiri ditandai *"COCOK UNTUK SOFTWARE KONTROL
   REAL-TIME"*. Dengan profil datar, gerakan beruntun menyambung mulus tanpa
   berhenti di antaranya. Tapi ini butuh upload ulang firmware.

3. **Perintah harus diakhiri `\r`, bukan `\n`.** `Command::handleGcode()` *abaikan*
   `\n` dan cuma eksekusi saat ketemu `\r`. Kalau pakai `\n`, robot diam saja
   tanpa error. Di Python: `ser.write(b"G0 X10 Y200\r")`.

4. **`F` satuannya mm/s.** Kalau `F < 5` (termasuk 0 atau tidak diisi), firmware
   hitung sendiri: `sqrt(jarak) * 10` di v2_01, `sqrt(jarak) * 8` di v2_02.
   Apa pun di bawah 5 mm/s dinaikkan jadi 5. Maksimal praktis ~500.

5. **Satu port serial, satu pemakai.** GUI, script Python, dan Serial Monitor
   Arduino IDE tidak bisa pegang port bareng. Putuskan dulu salah satu — ini
   penyebab paling umum "kok nggak konek".

6. **Jangan suruh gripper nutup penuh ke benda.** Servo bakal panas dan gearnya
   rontok. Set sudutnya sesuai ukuran benda; rasakan pas mulai mencekik, berhenti
   di situ.

7. **Rel + kecepatan tinggi = step hilang.** Jangan gabungkan gerakan rel (`E`)
   dengan `X/Y/Z` ber-`F` tinggi dalam satu baris — pisahkan jadi perintah sendiri
   dengan `F` rendah/normal. Kalau step sudah hilang, `G28` lagi.

8. **`config.h` beda-beda antar versi.** Sebelum upload firmware baru, cocokkan
   dulu `RAIL` dan semua `INVERSE_*_STEPPER` dengan config yang lama — kalau tidak,
   arah gerakan bisa kebalik. Yang berbeda antar varian:

   | Parameter | v2_01 | v2_02 | v2_01-interface |
   |---|---|---|---|
   | `MIN_SERVO` / `MAX_SERVO` | 95 / 160 | 95 / 160 | 27 / 115 |
   | `RAIL` | false | true | true |
   | `INVERSE_Z_STEPPER` | false | true | true |
   | `X_HOME_STEPS` | 850 | 850 | 900 |
   | `HOME_DWELL` | 700 | 700 | 1200 |
   | `VACUM_DELAY_OFF` | 500 | 500 | 300 |
   | `SPEED_DEFAULT` | — | 8 | — |

   `robotGeometry.cpp` **identik** di v2_01 dan v2_02, jadi perhitungan koordinat
   dan jangkauan sama di kedua versi.

9. **Setelah `M18`, posisi hilang — dan `G0` dijawab `ok` tapi lengan DIAM.**
   Wajib `G28` lagi sebelum gerak. Firmware tidak menyalakan stepper lagi untuk
   `G0` (`robotRNV3_v2_02.ino:215` cuma `fan.enable(true)`); yang menyalakan
   hanya `G28` dan `M17`. Hal yang sama berlaku **sebelum homing pertama**:
   `setup()` mematikan semua stepper. Sejak 21 Okt `rnv3.move()` menolak gerak
   sampai `home()`, dan `M114`/`M17` sengaja tidak membuka kuncinya.

10. **Sebelum kalibrasi, geser lengan sedikit ke depan pakai tangan.** Kalau mentok
   di endstop belakang, hasil homing kurang presisi.

11. **Dengan 12V mati, robot tetap menjawab `ok` tapi lengan TIDAK bergerak.**
   Ini kembaran jebakan nomor 1, tapi penyebabnya listrik, bukan jalur.

   Logic dan motor punya jalur daya **terpisah** (lihat "Arsitektur power" di bawah):
   Arduino hidup dari **USB 5V**, motor hidup dari **12V lewat saklar**. Jadi kalau
   USB tercolok tapi 12V mati:

   - Arduino boot normal, mencetak pesan setup, menjawab semua perintah
   - `kontak.py` melaporkan **semuanya cocok, exit code 0**
   - `M114` / `M119` / `M105` menjawab seperti biasa
   - tapi `VMOT` mati, jadi `G28` dan `G0` dijawab `ok` dan **lengan diam total**
   - rail 5V dari DC-DC juga mati, jadi servo dan sensor tidak berdaya

   **"Robot menjawab" BUKAN bukti robot siap bergerak.** Pastikan saklar ON dan lampu
   robot kedip sebelum mengirim gerakan apa pun.

---

## Robot tidak dibaut ke meja

Robot ini sering dipindah-pindah, jadi tidak dibaut. Yang perlu diketahui:

**Koordinat menempel pada base robot, bukan pada meja.** Memindahkan robot
tidak merusak koordinat apa pun — `X=0 Y=217 Z=138` tetap berarti titik yang
sama relatif terhadap badan robot. Yang berubah hanya posisi **benda di meja**
terhadap robot.

Konsekuensinya:

- Kalau robot **dan** area kerjanya dipindah bersama (misalnya keduanya di atas
  satu papan atau nampan), **tidak ada yang perlu diukur ulang.** Ini cara
  paling praktis.
- Kalau cuma robotnya yang pindah, `Y_ROW` / `X_CENTER` / `Z_PICK` di
  `pick_place.py` harus diukur ulang pakai `jog.py` (tekan `p`).
- Setelah dipindah, jalankan homing lagi (`jog.py` otomatis melakukannya tiap
  kali start).

Supaya tidak bergeser saat bergerak, tanpa perlu bor:

- **Alas karet atau silikon anti-selip** di bawah base — murah, bisa dilepas,
  dan cukup untuk gerakan yang sudah mulus.
- Yang paling berpengaruh justru **menghilangkan getarannya** (jebakan nomor
  2). Robot yang bergerak mulus hampir tidak punya gaya yang mendorongnya
  bergeser.

---

## Robot loyo saat dimatikan

Dari firmware:

```cpp
void RampsStepper::enable(bool value) { digitalWrite(enablePin, !value); }
```

Pin enable aktif-LOW, dan `Endstop::home()` menyalakan tiap sumbu saat homing.
Jadi **setelah homing stepper tetap berenergi dan lengan menahan posisinya**
selama robot menyala. Lengan jadi loyo hanya kalau:

- power dimatikan (ini fisika, tidak bisa dihindari),
- `M18` dikirim (tombol `R`),
- atau Arduino ter-reset (terjadi tiap kali port serial dibuka).

### Kenapa parkir rendah penting

Versi awal `jog.py` memarkir lengan di **home `Z=138` yang tinggi** sebelum
keluar. Begitu power dimatikan, lengan jatuh dari ketinggian itu — jadi
`jog.py` sendiri yang membuat masalahnya parah.

Sekarang keluar = **parkir rendah**, supaya saat power dicabut lengan hanya
turun beberapa milimeter. Urutannya X/Y dulu baru Z, supaya tidak menyeruduk
benda yang kebetulan ada di bawah lengan.

Nilai default sengaja konservatif karena ruang di bawah lengan belum diukur:

```python
PARKIR = (0.0, 217.0, -40.0, 0.0)       # X, Y, Z, E
```

Cara menetapkan sendiri: jog ke posisi yang pas, tekan **`Shift+X`**. Tercetak
baris `PARKIR = (...)` yang tinggal ditempel ke atas `jog.py` supaya permanen.

### Menggeser lengan dengan tangan

Tekan **`R`** untuk melepas motor (`M18`) — lengan jadi bisa digeser. Tekan
**`T`** untuk mengunci lagi (`M17`).

Setelah `R`, posisi lengan tidak lagi bisa dipercaya (baik oleh `jog.py` maupun
oleh firmware), jadi `rnv3.py` otomatis **menolak semua gerakan** sampai
dikalibrasi ulang dengan `C`. Kalau keluar dalam keadaan itu, `jog.py` **tidak
parkir diam-diam** — dia memberi tahu dan menyarankan menurunkan lengan dengan
tangan, atau kalibrasi dulu lalu parkir.

---

## Gerak-gerakin sendiri — `jog.py`

Kendali manual lengan pakai tombol panah. **Harus dijalankan di terminal kamu
sendiri** (butuh terminal interaktif untuk membaca tombol):

```bash
python.exe jog.py
```

Bedanya dengan GUI resmi: **setiap gerakan divalidasi SEBELUM dikirim** pakai
`check_reach()` + `check_path()`. Titik yang ditolak tidak pernah sampai ke
robot, jadi mustahil menabrak batas jangkauan. GUI resmi mengirim dulu, baru
ditolak firmware — dan kegagalannya diam-diam.

### Jog kontinu + akselerasi

Aktif secara default. **Tekan sekali = presisi, tahan = sapuan mulus:**

| Kondisi | Potongan | Kecepatan |
|---|---|---|
| tekan sekali | 0.5 mm (ubah dengan `1`-`5`) | sesuai `[` `]` |
| ditahan < 0.3 dtk | 3 mm | 25 mm/s |
| ditahan < 1.0 dtk | 8 mm | 25 mm/s |
| ditahan > 1.0 dtk | 15 mm | 25 mm/s |

**Kecepatannya sengaja sama di semua potongan** — itu kunci mulusnya. Lihat
jebakan nomor 2.

Lepas tombol atau ganti arah → kembali ke step manual 0.5 mm.

**Lepas tombol = lengan masih maju sampai 15 mm.** Firmware tidak punya
perintah abort, jadi potongan yang sedang jalan pasti selesai. Untuk mengukur
presisi, **tekan sekali-sekali** (0.5 mm), jangan ditahan.

**Kenapa ada sisa jalan setelah tombol dilepas:** firmware RNV3 **tidak punya
perintah stop/abort**. Sekali `G0` dikirim, gerakan itu pasti selesai. Jadi
"berhenti saat tombol dilepas" hanya bisa didekati dengan potongan pendek —
dan potongan pendek justru bikin getar (jebakan nomor 2). Kompromi yang
dipakai: tekan sekali 0.5 mm, sapuan saat ditahan maksimal 15 mm (ubah dengan
`-` `=`). Versi lama catatan ini menulis "dibatasi 2 mm" — itu angka dari
percobaan pertama yang sudah ditinggalkan.

Saat ditahan, penekanan yang menumpuk di buffer konsol **sengaja tidak
dikalikan** — satu potongan per putaran, sisanya dibuang. Kalau dikalikan,
lengan akan terus berjalan lama setelah tombol dilepas.

Kalau mau perilaku lama (satu penekanan = tepat satu step, tanpa akselerasi):

```bash
python.exe jog.py --diskret
```

### Terminal mana?

**Terminal Ubuntu/WSL yang biasa, tapi pakai `python.exe`** — sudah diuji,
`msvcrt` bisa membaca konsol dari sana:

```bash
cd "/home/nico/PROJECT GUESTPRO/robot-rnv3/python"
python.exe jog.py
```

`python3` tidak bisa: WSL tidak melihat USB tanpa usbipd. Kalau ternyata tombol
tidak terbaca di terminal kamu, jalankan dari PowerShell dengan path yang sama.

### Tombol

```
GERAK   tekan sekali = presisi, tahan = sapuan mulus
  W / S   Y maju / mundur          A / D   putar badan kiri / kanan
  I / K   Z naik / turun           J / L   rel kiri / kanan
  (panah dan PgUp/PgDn tetap jalan sebagai alias)

HALUSKAN   ubah sambil jalan, langsung terasa
  [  ]    kehalusan  - / +         -  =    panjang sapuan - / +
  0       ganti profil kecepatan   1-5     step tekan-sekali

ALAT
  G / B   gripper genggam / buka   V       vakum on-off

UKUR
  P       cetak posisi + baris config      M   tandai titik
  N       daftar titik tertandai           O   simpan titik ke berkas

POSISI & MOTOR
  H       pulang ke home           C       kalibrasi ulang (G28)
  X       parkir sekarang          Shift+X jadikan posisi ini titik parkir
  ;       rekam batas fisik arah terakhir (saat lengan hampir beradu badan)
  R       rilis motor (M18)        T       tahan motor (M17)

LAIN
  ?       bantuan                  Q / ESC keluar (parkir dulu)
```

Tekan `?` kapan saja untuk daftar ini.

### A/D memutar badan, bukan menggeser X (21 Okt 2026)

Dulu A/D menggeser **X kartesian dengan Y tetap**. Karena koordinatnya lurus,
jarak ujung tool ke pusat putar ikut berubah, sehingga lengan atas dan bawah
ikut menekuk/menjulur padahal yang dimaui cuma memutar badan. Dihitung dengan
`rnv3.sudut_sendi()`: X 0 -> 100 di Y=217, Z=138 membuat radius 217 -> 239 mm
dan sudut bahu 0 -> 9 deg.

Sekarang A/D (dan panah kiri/kanan) memakai sumbu `rot` di `jog.py`: titik
diputar mengelilingi pusat rotasi, **radius dan Z tetap**, jadi idealnya hanya
stepper putar base yang bergerak. Step/sapuan tetap dalam mm, diukur sebagai
panjang busur di ujung tool; pesan status menampilkan derajat putar.

Firmware menginterpolasi garis lurus, jadi tiap potongan putar adalah tali
busur yang sedikit melendut ke dalam. Potongan dibatasi supaya lendut
<= `LENDUT_MAKS` = 0.3 mm (`busur_maks()`).

Batas putar: yang kena duluan antara `rot_maks` atau `y_min` dari
`BATAS_FISIK`. Dengan tebakan default (`y_min`=100) dan radius home 217 mm,
putaran berhenti di ~62.5 deg, bukan 75 deg. Tombol `;` setelah A/D sekarang
merekam `rot_maks` (simetris kiri-kanan).

Bukti (`python3 cek.py`): tes "A/D: ..." mengecek radius, Z, sudut bahu, dan
siku sepanjang tali busur yang dijalani. Uji mutasi (A/D dikembalikan ke X)
membuat tes-tes itu GAGAL, misalnya bahu 30.00 -> 41.85 deg.
**Belum dicoba di robot asli.**

### Kalau masih terasa menghentak

Setelannya bisa diubah **sambil jalan**, tanpa restart. Tiga tombol ini yang
dipakai untuk mencari setelan paling halus:

| Tombol | Efek |
|---|---|
| `=` | perpanjang sapuan (5→10→15→20→30→40 mm). Makin panjang = transisi makin jarang = makin mulus, tapi lepas tombol makin jauh jalannya |
| `[` | lebih halus (percepatan 800→600→400→300→200→150→100 mm/s²). Ramp makin landai |
| `0` | bandingkan profil: kosinus → arctan → datar |

Default: profil **kosinus**, kehalusan **200 mm/s²**, sapuan **15 mm**.

### Bug yang sempat menyembunyikan sapuan

Selama beberapa percobaan, tangga akselerasi **tidak pernah naik** — potongannya
selang-seling `0.5 / 5.2 / 0.5 / 5.2 mm` dengan kecepatan selang-seling
`F5 / F14.5`. Lonjakan kecepatan tiap gerakan itu justru sumber hentakan yang
dikeluhkan.

Penyebabnya: ambang "tombol ditahan" diukur **antar penekanan**. Padahal saat
tombol ditahan, penekanan menumpuk di buffer konsol **selama robot bergerak**,
lalu terbaca persis setelah gerakan selesai. Potongan 5 mm di F14.5 makan
0.36 detik — lebih lama dari ambang 0.18 detik — jadi setiap selesai bergerak
dikira tombol sudah dilepas.

Perbaikannya: ambang diukur dari **selesainya gerakan sebelumnya**, bukan dari
penekanan sebelumnya. Ditambah satu sinyal lagi: kalau beberapa penekanan
menumpuk sekaligus (`ulang > 1`), itu pasti tombol ditahan.

Hasil di robot asli sesudah diperbaiki:

```
X +0.5   F5      <- tekan pertama, step manual
X +5.2   F14.5   <- mulai ditahan
X +10.5  F20.6
X +15    F24.7   <- sapuan penuh, kecepatan konstan
X +15    F24.7
```

`cek.py` punya tes regresi khusus untuk ini: menirukan gerakan yang makan
ratusan milidetik dan memastikan tangganya tetap naik sampai sapuan maksimum.

### Mematikan robot setelah parkir

**Sejak 22 Okt 2026: default-nya motor TIDAK dimatikan** (`MATIKAN_SAAT_KELUAR =
False` di `jog.py`). Di lapangan lengan jatuh dan terbentur setiap kali ESC,
karena titik `PARKIR` belum pernah diukur — "turun cuma beberapa milimeter"
tidak terjamin. Sekarang `Q`/`ESC` → lengan parkir → **motor tetap terkunci**,
lengan menahan posisi. Motor baru lepas saat saklar 12V dimatikan, atau saat
port serial dibuka lagi (Arduino reset, `setup()` memanggil
`setStepperEnable(false)`) — dan saat itu lengan sudah di parkir yang rendah.
Pegang lengannya waktu mematikan saklar.

Perilaku lama di bawah ini masih ada kalau flag diset `True`:

Tekan `Q` atau `ESC` → lengan parkir rendah → **motor dan kipas langsung
dimatikan** (`M18`). Robot jadi diam total dan saklar fisiknya aman ditekan.

```
membersihkan ...
  parkir di X0 Y217 Z-40
  motor & kipas DIMATIKAN (M18) -- robot diam total
  lengan sekarang loyo, tapi sudah parkir rendah
  jadi turunnya cuma beberapa milimeter.
  Saklar fisik aman ditekan sekarang.
```

Satu perintah mematikan keduanya sekaligus, karena di firmware
`setStepperEnable()` mematikan stepper **dan** kipas:

```cpp
void setStepperEnable(bool enable){
  stepperRotate.enable(enable); stepperLower.enable(enable);
  stepperHigher.enable(enable);
  #if RAIL
    stepperRail.enable(enable);
  #endif
  fan.enable(enable);          // kipas ikut
}
```

**Yang TIDAK bisa dilakukan dari software: memutus listrik 220V/12V.** Hanya
motor dan kipas yang bisa dimatikan. Saklar fisiknya tetap perlu ditekan
sendiri — tapi setelah `M18`, robot sudah tidak menarik arus tahan dan tidak
panas, jadi boleh juga dibiarkan menyala.

Servo gripper tetap bertenaga: firmware tidak pernah memanggil `detach()`,
jadi servo masih menahan posisinya.

**Pengecualian yang disengaja:** kalau motor pernah dirilis (`R`) sehingga
posisi lengan tidak diketahui, `M18` **tidak** dikirim saat keluar — lengan
bisa saja sedang tinggi, dan mematikan motor akan menjatuhkannya dari situ.
Pesannya menjelaskan hal ini, tidak diam-diam.

Kalau `batas-fisik.json` hasil kalibrasi terlalu sempit, gejalanya bisa
membingungkan: gerakan yang dulu bisa tiba-tiba ditolak. Hapus berkasnya untuk
kembali ke nilai bawaan. `cek.py` sengaja **mengabaikan** berkas ini saat
pengujian supaya hasil tes tidak ikut berubah oleh kalibrasi.

Mau lengan tetap menahan posisi setelah keluar? Ubah di atas `jog.py`:

```python
MATIKAN_SAAT_KELUAR = False
```

`pick_place.py` punya setelan setara: `MATIKAN_SAAT_SELESAI`. Sejak 22 Okt 2026
keduanya default `False` (lihat atas).

### Peringatan mendekati batas

Baris status menampilkan sisa jarak **ke arah yang sedang digerakkan** — bukan
sekadar radius sekarang. Ini yang berguna, karena ruang kerja berbentuk
cangkang bola: sisa jarak berbeda untuk tiap arah dan berubah terus.

```
... | Y+ maju 86                      masih longgar
... | >>> Y+ maju sisa 8.6            peringatan, di bawah 25 mm
... | !!! MENTOK Y+ maju              sudah di tepi
```

**Potongan mengecil sendiri saat mendekat.** Dulu kalau sisa 1.8 mm sementara
potongan 5 mm, gerakannya ditolak mentah-mentah dan ruang 1.8 mm itu terbuang.
Sekarang potongan dijepit ke sisa jarak, jadi lengan melandai berhenti **tepat
di tepi**:

```
Y +15    >>> sisa 23.6 mm ke batas Y+ maju
Y +15    >>> sisa 8.6 mm ke batas Y+ maju
Y +8.54  (dipendekkan, mepet batas Y+ maju)  !!! MENTOK ke Y+ maju
!!! MENTOK -- tidak bisa lebih jauh ke Y+ maju
```

Efek yang sama berlaku di **lubang mati** dekat badan robot. Dulu gerakan ke
arah sana ditolak dari jauh; sekarang lengan jalan sampai tepi lubang lalu
berhenti. Dalam satu uji, itu memulihkan **133 mm** ruang yang sebelumnya
terbuang.

Ambangnya di atas `jog.py`:

```python
DEKAT_BATAS = 25.0      # mulai memperingatkan
MENTOK = 0.3            # dianggap sudah benar-benar mentok
```

### Membaca baris status

Selama jogging layar cuma berisi **satu baris** yang ditimpa di tempat — yang
berganti hanya angkanya:

```
Posisi lengan  X    0.00  Y  220.00  Z  138.00  E   0.00 | Y+ maju sisa 82 <- Y +0.5
```

- `Y+ maju sisa 82` = masih 82 mm lagi ke arah yang terakhir digerakkan.
  Berubah jadi `>>> ... sisa 8.6` saat dekat, `!!! MENTOK` saat di tepi.
- `MOTOR RILIS` / `VAKUM` muncul kalau sedang berlaku.
- `<- ...` = pesan aksi terakhir, hanya kalau muat di lebar terminal.
- Setelan (step, sapuan, kehalusan, profil) tidak ikut di baris ini; tekan `?`.

Dua hal yang dulu membuat tiap tekan tombol menambah 3 baris baru (terukur di
terminal tiruan: 6× W = 18 baris, sekarang 0):

1. Lalu-lintas serial (`>> G0 ...`, `<< LINEAR MOVE ...`, `<< ok`) ikut tercetak.
   Sekarang disembunyikan selama jogging; `python.exe jog.py --ramai` untuk
   melihatnya lagi saat debug.
2. Baris status 120 karakter terlipat di terminal yang lebih sempit, jadi `\r`
   gagal menimpa. Sekarang baris dipotong ke lebar terminal.

Angka `sisa` itu cara paling cepat merasakan bentuk envelope robot — jog
sambil memperhatikan dia naik-turun.

### Mengukur angka untuk `pick_place.py`

Ini gunanya buat jobdesk koordinat:

1. Jog gripper sampai pas menyentuh permukaan tempat benda akan diambil.
2. Tekan `p` — tercetak baris siap-tempel:
   ```
   Y_ROW  = 240.0     # jarak grid dari badan robot
   Z_PICK = -78.0     # ketinggian saat menyentuh benda
   X_CENTER = 0.0     # titik tengah grid di sumbu X
   ```
3. Tekan `m` untuk menandai, `s` untuk menyimpan ke `python/titik-terukur.txt`.
4. Tempel angkanya ke blok konfigurasi `pick_place.py`, lalu
   `python3 pick_place.py --cek`.

### Pengaman di `jog.py`

- **Pra-terbang**: `kontak.verdict()` dijalankan dulu. Kalau config `rnv3.py`
  tidak cocok dengan robot, `jog.py` **menolak bergerak** sama sekali.
- **Homing tidak bisa dilewati.** Membuka port serial mereset Arduino, jadi
  posisi firmware selalu hilang di awal sesi — jogging tanpa homing tidak ada
  artinya. Ada prompt konfirmasi `[y/N]` sebelum `G28`.
- **Keluar selalu bersih**: vakum dimatikan, lengan diparkir rendah, lalu `M18`
  (lihat "Mematikan robot setelah parkir").

---

## GUI resmi

Install dari folder ini (27 MB, next-next):

```
software-robot/Rus-setup-0.2.exe
```

Isinya: slider X/Y/Z/E/speed, dua gripper servo, Calibration, Go Home, Cek
posisi, Cek endstop, LG1–3, status sensor, Motor on/off, Fan, program list
(new/delete/edit/up/down/next/start/pause/stop) + Save Point, dan input
perintah manual.

Paling enak buat eksplorasi bebas dan demo ke pembina. Dua hal yang perlu
diketahui:

1. **Tidak ada validasi jangkauan.** Titik di luar envelope baru ditolak
   firmware setelah dikirim.
2. **Slider-nya tidak realtime.** Dari source-nya
   (`interface-gui/Ruskomponen_BOT_BLK.py` baris 3123):
   ```python
   self.horizontalSlider_X.sliderReleased.connect(update_doubleSpinBox_and_send_gcode)
   ```
   Dia memakai `sliderReleased`, bukan `valueChanged` — jadi robot baru
   bergerak **setelah slider dilepas**, bukan sambil digeser. `jog.py` dengan
   jog kontinu lebih responsif untuk kerja halus.

GUI ini **tidak pernah dimodifikasi** — ini `Rus-setup-0.2.exe` asli dari
developer. Source-nya ada di `interface-gui/` sebagai referensi saja.

### ⚠️ Satu port, satu pemakai

Kalau GUI terhubung ke COM4, `jog.py` / `pick_place.py` / `kontak.py` **tidak
bisa** connect, dan sebaliknya. Putuskan dulu di salah satu. Ini penyebab
paling umum error "gagal membuka COM4".

---

## Hasil gerakan pertama (13 Okt 2026)

Robot bergerak untuk pertama kali. Semua lewat `jog.py` ke COM4.

**Homing (`G28`)** — robot melaporkan posisi berikut sesudah `G28`:

```
posisi setelah homing: X=0.00  Y=217.00  Z=138.00  E=0.00
```

> **Angka ini TIDAK membuktikan homing berhasil.** Bagian "M114 sebelum G28"
> di bawah menyatakan sendiri bahwa firmware melaporkan tepat nilai `INITIAL_*`
> yang sama **sebelum** `G28` dijalankan, dan itu "bukan posisi fisik lengan".
> Jadi keluaran di atas konsisten dengan homing yang berhasil MAUPUN dengan
> `G28` yang tidak melakukan apa-apa. Tidak ada log mentah yang disimpan, jadi
> ini tidak bisa diperiksa ulang. Perlakukan sebagai **belum terverifikasi**
> sampai diuji lagi sambil merekam transkrip serialnya.

**Uji arah tiap sumbu** — step 5 mm, F=20, keluar lalu balik:

```
X  0 -> +5 -> +10 -> +5 -> 0      diterima
Y  217 -> 222 -> 227 -> 222 -> 217 diterima
Z  138 -> 143 -> 148 -> 143 -> 138 diterima
E  0 -> +5 -> +10 -> +5 -> 0      diterima
```

16 gerakan, **nol penolakan, nol `LIMIT REACHED`**, dan `pulang (langsung)`
berhasil. Posisi yang dilacak Python tetap sinkron dengan robot sepanjang
sesi.

---

## Kontak pertama — `kontak.py`

**Nomor COM tidak perlu ditulis lagi.** Semua alat (`kontak.py`, `jog.py`,
`pick_place.py`) mencari robot sendiri lewat `rnv3.cari_port()`, yang
mengenali chip CH340 dari `VID_1A86&PID_7523`. Jadi `--port` opsional.

Ini penting karena nomor COM **berubah tiap robot dicolok ke lubang USB yang
berbeda** — pernah COM4, lalu jadi COM5 setelah pindah lubang. Kalau ada lebih
dari satu kandidat, yang pertama dipakai dan sisanya disebutkan supaya bisa
dipilih manual dengan `--port`.

Sebelum menggerakkan apa pun, jalankan ini dulu:

```bash
python.exe -m pip install pyserial      # sekali saja
python.exe kontak.py
```

`kontak.py` **tidak menggerakkan lengan**. Dia hanya mendengar pesan boot lalu
mengirim tiga perintah baca-saja:

| Perintah | Isi balasan |
|---|---|
| *(tidak ada)* | `SETUP GRIPPER SERVO : MIN .. MAX ..`, `RAIL ON/OFF`, `READY CALIBRATION` |
| `M114` | koordinat yang diasumsikan firmware |
| `M119` | status endstop tiap sumbu |
| `M105` | status 3 sensor (firmware v2_02 saja) |

`cek.py` memverifikasi klaim ini: dia membandingkan log perintah yang
`kontak.py` kirim ke simulator dan memastikan isinya cuma `M105`/`M114`/`M119`.

Gunanya: menjawab **firmware mana yang sebenarnya ter-upload**. Ada tiga varian
`config.h` dengan nilai berbeda, dan salah pilih bikin gripper tidak berfungsi
sama sekali. `kontak.py` langsung membandingkan laporan robot dengan konstanta
di `rnv3.py` dan memberi saran konkret kalau tidak cocok. Exit code 0 = cocok,
1 = ada yang perlu dibetulkan.

### Hasil kontak pertama ke robot asli (13 Okt 2026)

Dijalankan `python.exe kontak.py`. **Semua cocok, exit code 0.**

```
firmware         v2_02                      info
rentang servo    115/155                    COCOK   <- sama dgn rnv3.py
rel              ON                         COCOK
endstop lengan   X, Y, Z bebas              COCOK
endstop rel      tersentuh (parkir di E=0)  info
sensor input     S1=OFF S2=OFF S3=OFF       info
posisi           X=0 Y=217 Z=138 E=0        (asumsi firmware, belum homing)
```

Yang terjawab dari sini:

- **Robot memakai firmware v2_02 dengan config PEMBINA.** Rentang servo
  `115/155` — persis yang dipakai `rnv3.py`. Jadi pilihan mengikuti pembina
  memang benar; kalau tadi pakai 95/160 dari GitHub, gripper tidak akan jalan.
- **`M105` tersedia**, jadi kelompok sensor bisa membaca 3 sensor input.
- **Rel aktif**, sumbu E bisa dipakai 0–355 mm.
- Ketiga sensor `OFF` — tidak ada benda di depannya saat itu.

Satu hal yang sempat disalahpahami alat ini sendiri: `endstop rel` tersentuh
awalnya dilaporkan sebagai "TIDAK COCOK". Setelah dibaca ulang
`Endstop::home()` di `endstop.cpp:18`, ternyata homing dari posisi mentok
**tidak gagal** — loop pencarian `while (!bState)` dilewati, lalu `homeOffset()`
langsung mundur `step_offset`. Dan rel parkir di `E=0` itu posisi normalnya.
Jadi tingkat keparahannya diturunkan: endstop **lengan** tersentuh = perlu
perhatian (soal presisi), endstop **rel** tersentuh = sekadar info.

### ⚠️ Gripper membuka sendiri saat menyambung

Membuka port serial **mereset Arduino** (lewat jalur DTR), dan `setup()`
firmware menjalankan `servoB.write(MAX_SERVO)`. Jadi gripper akan membuka setiap
kali ada program menyambung. **Ini tidak bisa dihindari** — software GUI resmi
pun begitu. Pastikan tidak ada benda terjepit di gripper saat menyambung.

Lengannya sendiri aman: `HOME_ON_BOOT` bernilai `false`, dan firmware memanggil
`setStepperEnable(false)` saat boot — keempat stepper **mati total**, lengan
tidak bisa bergerak sebelum ada `G28`.

### Arti `M119`

Dari `Endstop::state()` di `endstop.cpp`: `!(digitalRead(min_pin) ^ switch_input)`,
dengan semua `*_MIN_INPUT = 0` di `config.h`:

| Nilai | Arti |
|---|---|
| `0` | endstop bebas |
| `1` | endstop **sedang tersentuh** |

Kalau X atau Y melaporkan `1` sebelum homing, lengan mentok di belakang.
**Geser maju sedikit pakai tangan dulu** — homing dari posisi mentok hasilnya
tidak presisi.

### `M114` sebelum `G28` bukan posisi sebenarnya

Sebelum homing, firmware cuma memakai nilai `INITIAL_X/Y/Z/E0` dari `config.h`
sebagai asumsi (`X=0 Y=217 Z=138 E=0`). Itu **bukan** posisi fisik lengan.
Posisi sesungguhnya baru diketahui setelah `G28`.

---

## Windows atau WSL? — pakai Windows

Diuji di mesin ini, bukan asumsi.

| | **Windows** | WSL Ubuntu |
|---|---|---|
| Setup | `python.exe -m pip install pyserial` — **1 perintah, tanpa admin** | usbipd-win (admin) → bind (admin) → **attach tiap sesi** → modprobe ch341 → usermod dialout → re-login |
| Python | 3.11.9 ✓ | 3.12.3 ✓ |
| pyserial | belum (1 pip) | 3.5 sudah ada |
| Driver CH340 | native | `ch341.ko` ada, tapi tak berguna tanpa usbipd |

WSL sebenarnya **bisa** — driver `ch341.ko` ada di kernel. Tapi `usbipd attach`
harus diulang **setiap kali** WSL restart atau robot dicabut-colok, dan kalau
lupa, `/dev/ttyUSB0` hilang tanpa penjelasan.

Penentunya: **`python.exe` bisa menjalankan file yang tersimpan di WSL.** Dia
melihat path sebagai `\\wsl.localhost\Ubuntu\home\nico\...` dan jalan normal.
Jadi file tidak perlu dipindah ke Windows.

```bash
# tetap di terminal WSL, file tetap di sini
python.exe -m pip install pyserial      # sekali saja
python.exe pick_place.py    # robot asli, Python Windows

python3 cek.py                          # tes offline, pakai Python WSL
python3 pick_place.py --sim             # simulator, pakai Python WSL
```

Aturannya sederhana: **`python.exe` kalau menyentuh robot, `python3` kalau tidak.**

---

## Menjalankan `pick_place.py`

Versi `contoh-pembina/pickup_and_place.py` yang sudah diperbaiki. 10 masalah
dibetulkan — lihat bagian "File dari pembina" di bawah.

```bash
python3 pick_place.py --cek              # validasi grid, tanpa robot
python3 pick_place.py --sim              # simulator, mode interaktif
python3 pick_place.py --sim --pindah 1 12
python.exe pick_place.py     # robot asli
python.exe pick_place.py --pick 7
python.exe pick_place.py --gui           # butuh PyQt5 (opsional)
```

Mode interaktif: `7` = pick slot 7, `p7` = place slot 7, `3-18` = pindah dari 3
ke 18, `k` = parkir, `q` = keluar.

### Konfigurasi — ukur dulu di robot nyata

Semua ada di blok paling atas `pick_place.py`, satuan mm:

| Nama | Default | Arti & cara mengukur |
|---|---|---|
| `COLS` / `ROWS` | 5 / 4 | Kolom di sumbu X, **baris di sumbu rel (E)** |
| `X_CENTER` | 0 | Titik tengah grid di sumbu X |
| `Y_ROW` | 260 | Jarak grid dari badan robot. **Makin kecil = grid makin lebar yang muat** |
| `PITCH_X` | 70 | Jarak antar kolom = lebar benda + celah (50 + 20) |
| `E_START` / `PITCH_E` | 0 / 100 | Posisi & jarak baris di rel. `(ROWS-1) × PITCH_E` ≤ 355 |
| `Z_SAFE` | 80 | Ketinggian aman saat bergerak menyilang |
| `Z_PICK` | −75 | Ketinggian saat menyentuh benda. **Ukur ini pelan-pelan** |
| `F_FAST` / `F_SLOW` / `F_RAIL` | 100 / 30 / 40 | Kecepatan mm/s. Rel jangan cepat |

**Setiap habis mengubah angka di atas, jalankan `--cek`.** Kalau ada slot yang
tak terjangkau, program **menolak jalan** dan mencetak tabel pilihan `Y_ROW` /
`PITCH_X` / `COLS` yang benar-benar muat. Itu pagar utama supaya bug grid
pembina tidak terulang.

Grid default (20 slot) sudah diverifikasi: semua slot aman di `Z_SAFE` **dan**
`Z_PICK`, jalur menyilang aman, dan 20 slot dijalankan penuh di simulator tanpa
sekali pun kena `LIMIT REACHED`.

---

## Cara tahu kodenya works — tanpa robot

Jalankan ini kapan saja:

```bash
cd python
python3 cek.py
```

318 pemeriksaan otomatis: konstanta geometri vs `config.h`, validasi titik,
validasi jalur, pemeriksaan pesan boot robot, geometri grid, pagar
`validasi_grid()`, lalu **menjalankan pick & place 20 slot penuh melawan
simulator firmware**, plus pagar yang membuktikan `kontak.py` tidak pernah
mengirim perintah gerakan. Kalau semua `[OK]`, logika kontrolnya benar.

`simulasi.py` adalah tiruan firmware yang dibuat dari source-nya: format balasan
sama, parsing perintah sama, batas gerak dicek per langkah jalur sama, bahkan
perilaku "batalkan gerakan tapi tetap kirim `ok`" juga ditiru. Jadi program yang
jalan di simulator punya peluang besar jalan di robot asli.

Pakai simulator di kode kamu sendiri:

```python
import simulasi
bot = simulasi.robot_palsu(verbose=True)   # ganti RNV3("COM4") dengan ini
bot.home()
bot.goto_xy(-50, 200, f=80)
print(bot.position())
print("kena limit:", bot.sim.jumlah_limit)   # harus 0
```

### Yang TIDAK bisa diuji tanpa robot

Jadi ya — tetap harus colok robot untuk memastikan 6 hal ini:

| Hal fisik | Kenapa simulator tak bisa |
|---|---|
| Kabel USB, driver CH340, nomor COM | murni hardware |
| Presisi homing | tergantung endstop & posisi awal lengan |
| Benturan ke meja/benda | simulator tak tahu isi dunia nyata |
| Kekuatan cengkeram gripper | perlu rasakan benda aslinya |
| Step hilang karena speed | batas mekanis motor |
| `END_EFFECTOR_OFFSET` ke dunia nyata | perlu diukur pakai penggaris |

---

## Checklist hari-H (urutan tes fisik)

Lakukan **berurutan**, jangan lompat. Setiap langkah punya tombol batal.

**Persiapan**
1. Pastikan robot **duduk stabil** dan tidak bergeser saat lengan bergerak. Robot
   ini **tidak dibaut** (lihat bagian "Robot tidak dibaut ke meja") karena sering
   dipindah — jadi tahan atau klem base-nya. Kalau mau permanen, baut ke meja
   (lubang M4; rel pakai M5). Robot yang goyang = koordinat ngawur.
2. Kosongkan area jangkauan — radius ~34 cm dari badan robot.
3. Colok power + USB. Nyalakan. Lampu robot kedip = mode standby, normal.
4. Satu tangan siap di tombol power, buat matikan kalau lengan menuju benturan.

**Sambungan**
5. Buka `software-robot/Rus-setup-0.2.exe` (install dulu kalau belum).
   Robot harus terdeteksi otomatis + muncul nomor COM. Catat nomornya.
6. Kalau tidak terdeteksi: cek Device Manager → Ports. Butuh driver CH340.
7. Klik Sambungkan. Harus muncul `RAIL ON` dan `READY CALIBRATION`.
   - `RAIL OFF` padahal robot pakai rel → firmware di-compile `RAIL=false`.
     Set `RAIL_ENABLED = False` di `rnv3.py`, atau upload ulang firmware.

**Kalibrasi**
8. **Geser lengan sedikit ke depan pakai tangan** sebelum kalibrasi. Kalau mentok
   di endstop belakang, homing jadi tidak presisi.
9. Klik Kalibrasi. Tunggu sampai `HOMING COMPLETE`.
10. Lihat posisi akhirnya: lengan bawah harus **tegak**, lengan atas **horizontal**.
    Kalau miring, `X_HOME_STEPS` / `Y_HOME_STEPS` di `config.h` perlu disetel.
11. Kalau ada sumbu yang gerak ke **arah berlawanan** saat kalibrasi → `INVERSE_*_STEPPER`
    salah. Jangan dipaksa, hentikan dan benahi `config.h` dulu.

**Tes gerak dari GUI (masih tanpa Python)**
12. Turunkan Z pelan-pelan pakai slider. Rasakan arah dan skalanya.
13. Geser X dan Y masing-masing. Pastikan arah sesuai dugaan.
14. Kalau pakai rel, geser E. **Jangan speed tinggi** di rel.
15. Tes gripper: buka/tutup di udara kosong dulu, tanpa benda.

**Pindah ke Python**
16. Putuskan sambungan di GUI dulu. **Satu port, satu pemakai.**
17. `pip install pyserial`
18. Edit `PORT` di script, isi nomor COM dari langkah 5.
19. Jalankan gerakan paling sederhana dulu:

    ```python
    from rnv3 import RNV3
    with RNV3("COM4") as bot:
        print(bot.endstops())      # belum menggerakkan apa pun
        print(bot.position())      # belum menggerakkan apa pun
        bot.home()
        bot.move(z=100, f=20)      # satu sumbu, pelan
    ```

20. Baru setelah itu coba gerakan XY, tetap `f` rendah (20–40) di percobaan awal.

**Kalibrasi koordinat ke dunia nyata (ini yang krusial buat jobdesk kamu)**
21. Suruh robot ke titik yang kamu tahu, misal `bot.move(x=0, y=250, z=-90)`.
22. Ukur pakai penggaris: ujung gripper benar-benar di 250 mm dari pusat rotasi?
23. Kalau melenceng, setel `END_EFFECTOR_OFFSET` di `config.h` (default 77 mm),
    upload ulang, kalibrasi lagi, ukur lagi. Ulangi sampai cocok.
24. Nilai offset berbeda untuk tiap jenis gripper (servo / vakum / pneumatik).

---

## Integrasi antar 3 kelompok

Jobdesk kamu (koordinat X/Y) ada di tengah — dua kelompok lain nyambung ke kamu.

### Kelompok kamera → kelompok kamu

Ini titik integrasi paling rawan. Kamera mendeteksi benda di koordinat *piksel*,
kode kamu butuh koordinat *mm* robot. Yang perlu disepakati:

- **Format serah-terima.** Paling sederhana: kamera kasih `(x_mm, y_mm)` dalam
  koordinat robot, bukan piksel. Jadi konversi piksel→mm tanggung jawab
  kelompok kamera, dan kamu cukup `bot.goto_xy(x, y)`.
- **`END_EFFECTOR_OFFSET` wajib benar.** Ini syarat mutlak. Kalau offset belum
  dikalibrasi (langkah 21–24 di atas), robot akan konsisten melenceng dari titik
  yang kamera laporkan — dan kelihatan seperti "kameranya salah" padahal bukan.
- **Validasi sebelum gerak.** Kamera bisa saja mendeteksi benda di luar jangkauan
  robot. Selalu `check_reach()` dulu, lalu kasih feedback ke kelompok kamera
  kalau titiknya tak terjangkau — jangan langsung dikirim ke robot.

### Kelompok konveyor → kelompok kamu

✅ **Sudah jelas (16 Okt 2026): yang dipakai konveyor belt sungguhan** — belt PVC
hijau dengan motor di ujung, benda yang berjalan. Bukan rel sumbu E. Tabel di
bawah tetap disimpan karena dua benda ini masih sering tertukar saat bicara
dengan kelompok lain.

| | **Rel (sumbu E)** | **Konveyor (belt)** |
|---|---|---|
| Apa | Track linear tempat robot duduk, bawaan paket RNV3 | Ban berjalan yang memindahkan benda, perangkat terpisah |
| Yang bergerak | **Robotnya** geser kiri-kanan | **Bendanya** yang jalan, robot diam |
| Kontrol | `G0 E<mm>`, 0–355 mm, bagian dari firmware | Motor eksternal lewat output MOSFET `LG1`/`LG2`/`LG3` |
| Di kode | `bot.move(e=150)` | `bot.output(1, True)` |
| Posisi diketahui? | Ya, ada endstop + step counting | Tidak, kecuali dipasang sensor sendiri |

Kalau yang dimaksud **rel**: itu sudah bagian dari jobdesk koordinat (sumbu E),
jadi sebenarnya nggak perlu kelompok terpisah — mungkin maksudnya mereka yang
mengurus mekanik/pemasangannya.

Kalau yang dimaksud **konveyor belt sungguhan**: mereka perlu
- motor belt disambung ke `LG1`/`LG2`/`LG3` (switching di sisi ground, maks 5 A;
  12 V ambil dari pin 10, 5 V dari pin 1 maks 2 A),
- sensor di ujung belt, masuk ke sensor input 1/2/3 (butuh firmware **v2_02**
  supaya bisa dibaca via `M105`),
- dan **kesepakatan protokol** dengan kamu: siapa yang menghentikan belt, kapan
  robot boleh mulai ambil, apa yang terjadi kalau benda datang saat robot masih
  sibuk.

Contoh pola kerja sama yang paling sederhana:

```python
# belt jalan sampai sensor 1 mendeteksi benda, lalu robot ambil
bot.output(1, True)                       # nyalakan motor belt
while bot.sensors()["S1"] == "ON":        # "ON" = KOSONG, tunggu selama kosong
    pass
bot.output(1, False)                      # STOP belt dulu
bot.goto_xy(x_dari_kamera, y_dari_kamera) # baru robot bergerak
```

### ⚠️ `S1 OFF` artinya ADA benda — terbalik dari dugaan

Versi lama contoh di atas menunggu `== "OFF"`, dan itu **salah**: hasilnya belt
baru berhenti setelah bendanya pergi.

Input `IO1`/`IO2`/`IO3` itu **active-LOW** (ada pull-up di board), dan firmware
melaporkan keadaan pin apa adanya (`robotRNV3_v2_02.ino:177-199`):

| Keadaan fisik | Pin | Dilaporkan |
|---|---|---|
| Tidak ada apa-apa di depan sensor | HIGH | `S1 ON` |
| Ada benda (sensor NPN menarik ke ground) | LOW | `S1 OFF` |

Contoh resmi `python/contoh-sensor/Program input.py` juga menunggu `"S1 OFF"`.
Konsekuensi belanja: **sensornya harus tipe NPN (sinking)** — PNP akan melawan
pull-up dan tidak pernah terbaca.

Di kode sekarang pakai konstanta saja, jangan menghafal:
`rnv3.SENSOR_ADA_BENDA` dan `rnv3.SENSOR_KOSONG`.

Kuncinya: **belt berhenti sebelum robot bergerak.** Kalau belt masih jalan saat
gripper turun, bendanya sudah pindah dari titik yang kamera laporkan.

---

## Integrasi — yang sudah dibangun (16 Okt 2026)

Kerangkanya sudah jadi dan bisa dijalankan tanpa hardware lewat `--sim`.

> **Sejauh mana "teruji" itu benar.** Versi pertama catatan ini mengklaim
> "sudah bisa dites penuh tanpa hardware apa pun", padahal saat itu `cek.py`
> hanya mengimpor `jog`, `kontak`, `pick_place`, `rnv3`, `simulasi` — lima modul
> (`jalur`, `kontrak_kamera`, `konveyor`, `kalibrasi`, `esp`, total 2.095 baris) nol
> cakupan, termasuk `jalur.py` yang justru satu-satunya pemberi perintah gerak.
> Sejak 17 Okt kelimanya sudah masuk `cek.py` (sekarang 318 pemeriksaan). Yang masih
> belum: sebagian besar pemeriksaan memvalidasi kode terhadap `simulasi.py`,
> yang ditulis dari bacaan firmware oleh penulis yang sama — jadi sebagian
> sirkular. Hanya tabel alias perintah teks yang sudah diuji langsung lawan
> `firmware/v2_02/command.cpp`.

| File | Isi |
|---|---|
| `python/kontrak_kamera.py` | Batas serah-terima kamera ↔ orkestrator. Dataclass `Deteksi`, client TCP, kamera tiruan, server tiruan |
| `python/konveyor.py` | Belt eksternal: `jalan()`, `berhenti()`, `maju_sampai_benda()`, `lewatkan_benda()` |
| `python/jalur.py` | Orkestrator — state machine belt → sensor → kamera → lengan |

```bash
python3 jalur.py --cek                       # validasi konfigurasi saja
python3 jalur.py --sim --siklus 20           # full loop, tanpa hardware
python3 jalur.py --sim --siklus 20 --kosong 0.25 --jauh 0.25   # uji cabang error
python3 kontrak_kamera.py --port 5555               # server kamera tiruan
```

### Arsitektur

Orkestrator memegang port serial **sendirian**, dan dia juga yang menyalakan
belt (karena motornya nempel di `LG1` board robot). Jadi kelompok konveyor
praktis **tidak punya kerjaan software** — tugas mereka mekanik + kelistrikan.
Satu-satunya batas software yang nyata: **kamera ↔ orkestrator**, lewat TCP
JSON Lines request/response:

```
→ {"cmd": "detect"}
← {"ok": true, "ts": 1792047037.1, "objects": [
     {"x_mm": -40.5, "y_mm": 265.0, "angle_deg": 12.0, "label": "tutup_botol", "conf": 0.92}]}
```

Aturan yang harus disepakati tertulis ada di docstring `kontrak_kamera.py`. Intinya:
koordinat dalam **mm koordinat robot** (bukan piksel), tidak ada benda =
`{"ok": true, "objects": []}` (bukan error), dan orkestrator tetap
`check_aman()` sebelum bergerak.

**Kalibrasi kamera itu kerjaan bersama**, bukan kelompok kamera sendirian:
robot harus disuruh ke ≥4 titik yang diketahui dengan marker di ujung gripper,
supaya kamera bisa membangun homography piksel→mm. Butuh robot menyala DAN
kamera sudah terpasang kaku.

### Bug yang diperbaiki: event sensor asinkron ketelan

Firmware memancarkan `S1 ON`/`S1 OFF` sendiri tiap ada perubahan tepi, tanpa
diminta. Tapi `RNV3.send()` dulu memasukkan **semua** baris non-`ok` ke balasan
perintah yang sedang jalan — dan `move()` membuang balasannya. Jadi sensor yang
kepicu saat lengan bergerak, eventnya **hilang permanen**.

Sekarang `send()` menyisihkan baris itu ke `bot.events_sensor`. Ambil pakai
`bot.ambil_event_sensor()`. Untuk *menunggu* benda tetap pakai
`bot.tunggu_sensor()` yang polling `M105` — event hanya ikut terbaca kalau ada
perintah lain yang sedang menunggu balasan, jadi tidak bisa diandalkan untuk
menunggu.

### Alat yang perlu disiapkan

**Konveyor**
- Belt PVC + motor DC geared 12 V (pilih torsi, bukan kecepatan; 5–15 cm/s cukup)
- **Relay module / modul MOSFET** — ⚠️ jangan sambung motor langsung ke `LG1`.
  `LG` cukup menyalakan coil relay, motor jalan dari PSU sendiri
- **Dioda flyback** (1N4007) paralel di terminal motor — tanpa ini MOSFET board bisa mati
- **PSU 12 V terpisah** — jangan ambil dari pin 10; rail 12 V dipakai bareng
  `VMOT` kelima driver A4988, motor menyentak bisa bikin stepper brownout

**Sensor**
- Sensor IR fotoelektrik **NPN** (E18-D80NK), bukan PNP
- **DB25 male + housing** atau breakout board — paling sering kelupaan
- Sinyal → pin 2, 5 V → pin 1 (maks 2 A), GND → pin 6

**Kamera**
- Webcam **fixed/manual focus** (autofocus geser = kalibrasi rusak)
- **Mounting overhead kaku**, dibaut ke meja yang sama dengan robot
- **Lampu LED + difuser** — bukan opsional; cahaya ruangan berubah = deteksi gagal sore hari
- Papan kalibrasi checkerboard/ArUco di foam board atau akrilik, **bukan kertas**

**Robot**
- Firmware **v2_02** wajib (`M105` tidak ada di v2_01)
- Gripper **vakum** paling cocok untuk benda di belt (ambil dari atas)

**Kelistrikan** — **ground board + ground sensor + ground PSU belt harus
tersambung semua.** Kalau mengambang, pembacaan sensor jadi acak dan kelihatan
seperti sensor rusak.

**Tata letak** — robot, belt, dan tiang kamera di satu permukaan kaku yang sama.
Zona ambil harus dalam jangkauan: di `Y=260, Z_SAFE=80`, X aman cuma −180..180.

---

## Perbaikan 21 Okt 2026 — hasil review, tanpa robot

Semua dikerjakan dan diuji di simulator (robot ada di tempat magang). Tiap
perbaikan punya tes di `cek.py` bagian 19, dan tesnya **sudah dibuktikan bisa
gagal**: keenam file kode yang diperbaiki (`rnv3`, `kontrak_kamera`, `jalur`,
`esp`, `pick_place`, `jog`) dikembalikan ke versi sebelumnya satu per satu, dan
setiap kali tes yang bersangkutan memang merah.

| # | Masalah | Perbaikan |
|---|---|---|
| 1 | Saringan deteksi kamera (`Deteksi.terjangkau`) cuma `check_reach`, sedangkan `move()` pakai `check_aman`. Benda di misalnya `(-250, 60)` lolos saringan lalu ditolak `move()`; cabang `ROBOT_ERROR` tidak menggeser benda, jadi `jalur.py` mengulang benda yang sama **selamanya** (terbukti: 10/10 siklus gagal) | Saringan, `validasi_konfigurasi()`, dan `validasi_grid()` sekarang `check_aman` — sama ketatnya dengan `move()` |
| 2 | Gerakan yang gagal tidak pernah ditangani: lini lanjut walau posisi lengan tidak diketahui atau benda sedang digenggam | `Jalur._gerakan_gagal()`: kalau ditolak sebelum dikirim dan benda belum terangkat → vakum off, gripper naik ke `Z_SAFE`, benda didorong lewat, lini lanjut. Selain itu → **lini dihentikan** |
| 3 | Setelah `M18` (dan sebelum homing pertama), `move()` tetap diterima Python padahal firmware tidak menyalakan stepper untuk `G0`. Contoh: `pick_place.py` interaktif, `k` lalu `7` → pick "berhasil", lengan diam | Flag `RNV3.butuh_home`: diset oleh `M18` dan saat tersambung, hanya dibuka `home()`. Simulator sekarang meniru ini (`sim.gerak_tanpa_motor`), makanya dulu `cek.py` tidak bisa menangkapnya |
| 4 | `selesai()` di `jalur.py` menurunkan lengan ke `Z=-40` **di tempat**. Setelah siklus sukses, tempat itu di atas bak — tepat di atas botol yang baru ditaruh (puncaknya `Z=+70`) | Parkir di `PARKIR_XY = (0, 217)`: naik ke `Z_SAFE`, geser XY, baru turun. Jalurnya ikut divalidasi |
| 5 | Label `"botol"`, padahal model kamera mengirim `"tutup_botol"` → botol diperlakukan sebagai koin (tinggi 2 mm, bak koin) | `JENIS["tutup_botol"]` |
| 6 | `esp.py` belum punya tambalan buffer basi yang sudah ada di `rnv3.py` | `ESP._timeout_error()` membersihkan buffer masuk saat timeout |
| 7 | `pick_place.py --gui` tidak parkir setelah jendela ditutup | Diparkir + `M18` seperti mode lain |
| 8 | `pick_place.py`: `parkir()` kedua kali (setelah `k`) gagal; tidak ada cara kalibrasi ulang | `parkir()` melewati diri kalau motor sudah mati; perintah baru `c` = kalibrasi |
| 9 | `jog.py`: setelah `R` lalu `C`, status tetap "MOTOR RILIS" | `C` mengembalikan status motor |

### Yang BELUM terbukti — cek di tempat magang

- **Perilaku `G0` setelah `M18`** disimpulkan dari **membaca** firmware, belum
  dicoba. Cara cek aman, pakai **GUI resmi saja** (jangan bareng `jog.py` —
  satu port satu pemakai): Kalibrasi, Motor off, lalu ketik `G0 Z130` di input
  perintah manual. Lengan harus **diam** walau dibalas `ok`. (`rnv3.py`
  sekarang menolak duluan, jadi tes ini cuma memastikan alasannya benar.)
- **`PARKIR_XY = (0, 217)`** harus titik yang di bawahnya **kosong** (bukan
  belt, bukan bak). Belum ada yang mengukur tata letak aslinya.
- **Tinggi `tutup_botol` = 150 mm** masih angka lama — ukur dari belt ke
  permukaan tutup botol yang berdiri.

### Catatan kecil firmware (tidak diubah)

`case 0: case 1:` di `executeCommand()` (`robotRNV3_v2_02.ino:213-221`) tidak
punya `break`, jadi setiap `G0` ikut menjalankan `cmdDwell()`. Tidak berbahaya:
`valueS` default 0 (`command.cpp:12`), jadi `delay(0)`. Baru berefek kalau
seseorang mengirim `G0 ... S2` — lengan baru mulai jalan setelah 2 detik.

---

## File dari pembina (ditambahkan 13 Okt 2026)

Dibandingkan dengan `C:\KULIAH\MAGANG KEDUA\File_Robot_RNV3`. Repo GitHub-nya
**commit sama** (`23c6b29`), jadi tidak ada update upstream yang terlewat — tapi
pembina punya perubahan lokal yang tidak ada di GitHub.

### Yang diambil

| File di sini | Asal | Kenapa penting |
|---|---|---|
| `firmware/v2_02-pembina/config.h` | config pembina | **Kalibrasi robot kalian yang sebenarnya** |
| `python/contoh-pembina/pickup_and_place.py` | baru | Pick & place grid + UI PyQt5 |
| `python/contoh-pembina/gambar.py` | baru | Menggambar pakai koordinat |
| `python/contoh-pembina/luma.py` | baru | Menulis huruf (plotter) |
| `python/contoh-pembina/dance.py` | diubah | Port `keyboard` → `pynput` (tak perlu root) |
| `software-robot/program-uji-coba/*.h` | diubah | 3 preset gerakan diperbarui |

### Beda config.h: GitHub vs pembina

```
                    GitHub     PEMBINA
X_HOME_STEPS          850   →    900
Z_HOME_STEPS         3520   →   3430
MIN_SERVO              95   →    115
MAX_SERVO             160   →    155
```

`rnv3.py` sekarang pakai **115/155** (nilai pembina), karena firmware hanya
menerima sudut di dalam rentang itu. Kirim `gripper(95)` saat `MIN_SERVO=115`
akan **ditolak** dengan pesan "Nilai cmd.valueB di luar rentang" dan gripper
tidak bergerak sama sekali.

Robot mencetak nilai aslinya tiap kali nyala:
```
SETUP GRIPPER SERVO : MIN 115 MAX 155
```
Kalau angkanya beda dari itu, samakan `SERVO_CLOSED`/`SERVO_OPEN` di `rnv3.py`.

### ⚠️ Catatan soal `pickup_and_place.py`

File ini **belum bisa dipakai apa adanya**. Tiga masalah:

1. **Semua 30 posisi grid-nya di luar jangkauan robot.** Diuji lawan batas
   firmware: `0/30` terjangkau. Penyebabnya `validate()` di file itu pakai
   **batas kotak**:
   ```python
   X_MIN, X_MAX = -260, 260
   Y_MIN, Y_MAX = 150, 340
   Z_MIN, Z_MAX = -100, 100
   ```
   Padahal ruang kerja robot berbentuk **cangkang bola**. Tiap batas itu benar
   sendiri-sendiri tapi tidak bisa dipakai bersamaan. `Y=340` memang bisa
   dicapai — tapi hanya di `Z=0` dan `X=0`. Di `Z_SAFE=100` yang dipakai script
   itu, `Y=340` mustahil untuk **semua** nilai X.

   Lebih berbahaya: **25 dari 30** posisi itu *lolos* `validate()` lalu dikirim
   ke robot, dan gagal diam-diam (lihat jebakan nomor 1).

2. **`pick()` tidak pernah menyalakan vakum.** Dia hanya `print("Vacuum ON")`
   lalu `sleep(1)` — perintah `VACUM ON` tidak pernah dikirim. Jadi benda tidak
   akan pernah terangkat.

3. **Port-nya macOS** (`/dev/cu.usbserial-1120`). Ganti ke `COM<n>` untuk Windows.
   Komentar `PITCH_X = WOOD_W + GAP   # 120` juga usang — nilai sebenarnya 70.

Strukturnya sendiri bagus dan layak dipakai: grid baris×kolom, urutan
naik → geser rel → gerak XY → turun.

**Sudah dibuatkan versi yang benar: `python/pick_place.py`.** Yang diperbaiki:

| | Masalah | Perbaikan |
|---|---|---|
| 1 | `validate()` pakai batas kotak | `check_aman()` + `check_path()` dari `rnv3.py` (sampai 21 Okt `check_reach()`, lebih longgar dari `move()`) |
| 2 | 30/30 slot di luar jangkauan | `validasi_grid()` menolak jalan + kasih saran konfigurasi |
| 3 | `pick()` tak pernah nyalakan vakum | `bot.vacuum(True)` beneran dikirim |
| 4 | Tidak ada `place()` | `place()`, `pindah()`, `parkir()` ditambahkan |
| 5 | Buffer serial tak pernah dikosongkan → `ok` lama dianggap jawaban baru | `RNV3.send()` membersihkan buffer masuk saat timeout (`_timeout_error()`). **Koreksi:** catatan versi pertama menulis "baca per baris" sebagai perbaikannya — itu tidak cukup dan tidak pernah ada di kode. Barisnya memang utuh, masalahnya barisnya milik perintah yang salah. Baru benar-benar ditambal 17 Okt |
| 6 | Retry mengirim ulang gerakan | tidak ada retry; pakai timeout + deteksi `ERROR:` |
| 7 | `G28` hanya `sleep(5)` tanpa tunggu `ok` | `bot.home()` menunggu `ok`, timeout 90 s |
| 8 | Port macOS | `--port COM4`, default Windows |
| 9 | Komentar `PITCH_X # 120` usang | nilai sebenarnya 70, dikomentari benar |
| 10 | Validasi hanya di `Z_SAFE` | diperiksa di `Z_SAFE` **dan** `Z_PICK` |

Pagar anti-regresi: `cek.py` memaksa konfigurasi ala pembina (`Y_ROW=340`,
`Z_SAFE=100`, `COLS=6`) dan memastikan `validasi_grid()` **menolaknya**.

### Grid yang benar-benar terjangkau

Dihitung ulang dengan syarat valid di `Z_SAFE` **dan** `Z_PICK=-75` sekaligus,
plus jalur antar kolom terjauh dicek aman:

```
    Y  Z_SAFE  pitch  kolom   X terkecil..terbesar   jalur
  300      60     90      4      -135 .. 135         aman
  280      60    100      4      -150 .. 150         aman
  260      80    120      4      -180 .. 180         aman
  240      80    120      4      -180 .. 180         aman
  220      80    120      5      -240 .. 240         aman
```

Pola umumnya: **makin dekat ke badan robot (Y kecil), makin lebar grid yang
bisa dipakai.** Jadi taruh papan kayunya lebih dekat ke robot daripada rencana
awal (Y=340), jangan di tepi jangkauan.

### Yang sengaja TIDAK diambil

| Di folder pembina | Alasan |
|---|---|
| `AI/Program/AI RNV3 v4.py` | **Ada API key asli di dalamnya.** Bukan jobdesk kamu. Kalau kelompok AI butuh, ambil langsung dari folder pembina dan **jangan** commit ke git |
| `Marlin Guide/` (149 MB) | Mode laser/plotter pakai **firmware Marlin**, bukan firmware RNV3. Kalau di-flash, semua perintah (`G100`, `VACUM ON`, `M105`) hilang |
| `Buku Panduan RNV3.pdf` (26 MB, 23 hal) | Dokumen berbeda, tapi kita sudah punya manual 27 halaman |
| `inkscape-0.92.5-x64.msi` (83 MB), `Printrun-win` (61 MB) | Installer, bisa diunduh ulang |
| `Ruskomponen_BOT_BLK.py` pembina | Lebih tua (5689 baris) dari punya kita (5723 baris) |

---

## Arsitektur power -- logic dan motor TERPISAH

Dibaca dari skematik resmi `docs/Buku panduan robot.pdf` halaman 26
(`TITLE: RNV3 REV 1.0`, Drawn By: februandi) plus source firmware.

### Koreksi: board ini BUKAN RAMPS 1.4

Skematiknya board custom: **Arduino Mega2560 Mini Pro** (U1) + **5 modul `MOD-A4988`
terpisah** (DX, DY, DZ, DE0, DE1), konektor **DB25 + DB15**, 4 MOSFET
(Q1 -> LG1/D8, Q2 -> LG2/D9, Q3 -> LG3/D10, Q4 -> FAN/D22).

Kata "RAMPS 1.4" cuma ada di **komentar source**, bukan di hardware:
`firmware/v2_02/pinout/pinout.h:5-8`, `config.h:41`, dan nama class `RampsStepper`.
Board RNV3 pin-compatible dengan peta pin RAMPS untuk stepper, tapi pin lain sudah
beda (FAN di D22; di RAMPS asli D9). **Jadi jumper dan dioda D1 khas RAMPS tidak
berlaku di sini.**

### Dua jalur daya

| Net | Sumber | Memberi makan |
|---|---|---|
| `VCC` | **USB 5V** (USB Type-B -> Mega) | Arduino + `VDD` logic kelima A4988, pull-up endstop |
| `12V` | `IN12V` **lewat saklar ON/OFF** | `VMOT` semua A4988, DC-DC U2, pin 12V DB25, kipas, drain MOSFET |
| `5V` | **DC-DC U2**, VIN-nya dari rail `12V` | pin 1 DB25 (max 2A) -- servo, sensor |

Saklar `ON/OFF` punya 3 terminal (`In`/`G`/`Ou`) dan posisinya **seri di jalur +12V
saja**: `IN12V` -> `In`, `Ou` -> rail `12V`. Jadi mematikan saklar **tidak** mematikan
Arduino kalau USB masih tercolok.

Rating yang tertulis (manual hal. 17): `5V max 2A`, `12V 5A`, tiap MOSFET 5A.
**Ampere PSU-nya sendiri tidak disebutkan di dokumen mana pun** -- lihat label adaptor
fisik kalau perlu tahu.

### Yang belum dipastikan

**Tipe saklarnya (latching/rocker vs momentary/tombol tekan) tidak disebutkan di
dokumen mana pun.** Kata "saklar" dan "switch" (dalam arti power) nol kemunculan di
manual. Petunjuk tidak langsung condong ke latching: dokumen ini konsisten menulis
"tangan di tombol power buat **mematikan**" -- kalau momentary, melepas tangan sudah
mematikan sendiri. Tapi ini harus **dilihat fisik**.

Kenapa penting: kalau latching, saklar bisa ditinggal ON permanen dan listriknya
dikontrol dari smart plug -- artinya robot bisa dinyalakan tanpa ada orang di sana.

### Tidak ada E-stop. Sama sekali.

`PS_ON_PIN 12` dan `KILL_PIN -1` didefinisikan di `firmware/v2_02/pinout/pinout.h:59-60`
tapi **tidak pernah dipakai** di satu pun `.cpp`/`.ino` di keempat varian firmware.

`interface-gui/DOCUMENTATION.md:287-296` menulis *"Emergency stop: immediate halt"* --
**itu tidak benar, jangan dipercaya.** Firmware juga tidak punya perintah abort.
Satu-satunya cara menghentikan robot adalah **saklar power fisik**.

---

## Bug `FAN_DELAY` di firmware developer -- DICATAT, SENGAJA TIDAK DIUBAH

- `fanControl.h:6` -- parameter `int aFanDelay`
- `fanControl.h:13` -- `long fan_delay`
- `fanControl.cpp:6` -- `fan_delay = aFanDelay * 1000;`

Perkaliannya terjadi di aritmetika `int` **sebelum** di-assign ke `long`. Di ATmega2560
`int` = 16-bit (-32.768..32.767):

```
FAN_DELAY 1000  ->  1.000.000  ->  wrap  ->  16.960 ms  =  ~17 detik
```

`FAN_DELAY` mengatur berapa lama kipas **tetap jalan setelah motor dimatikan**
(`disable()` set `nextShutdown = millis() + fan_delay`, `update()` menulis LOW sesudahnya).

| | Nilai |
|---|---|
| Niat developer (komentar `config.h`) | 1000 detik = 16,7 menit |
| Yang benar-benar jalan | **~17 detik** |
| Yang diklaim GUI (`Ruskomponen_BOT_BLK.py`) | 30 detik |

Tiga angka, tidak ada yang benar. Bug ini ada di **keempat varian** --
`fanControl.cpp` md5-identik (`199fd543a49f69e34f9a35a4794fff10`) dan `FAN_DELAY 1000`
sama di semuanya. Ini bug asli developer, bukan hasil kerja kita.

### Jebakan yang lebih berbahaya dari bug-nya

Tanpa memperbaiki cast, `FAN_DELAY` hanya aman sampai **32**. Di atas itu hasilnya bisa
**negatif**:

```
FAN_DELAY 40  ->  40.000  ->  wrap  ->  -25.536  ->  nextShutdown di MASA LALU
              ->  kipas mati SEKETIKA
```

Jadi mengganti nilainya saja ke angka yang kelihatan wajar (60, 120) justru bikin lebih
rusak daripada sekarang. Cast-nya harus dibetulkan dulu.

### Keputusan: TIDAK diubah

Firmware dibiarkan seperti aslinya (lihat **koreksi** di bagian "SENGAJA tidak
ditambal" di bawah — `fanControl.cpp` dan `config.h` ternyata tersentuh 15 Okt,
walau isi `fanControl.cpp` tidak berubah). Alasannya:

- Robot sekarang **jalan normal** dengan perilaku ~17 detik itu. Bug ini tidak pernah
  mengganggu siapa pun.
- Memperbaikinya justru **mengubah perilaku robot yang sudah bekerja**: dengan cast
  dibetulkan dan `FAN_DELAY 1000`, kipas jadi menyala 16,7 menit tiap kali motor mati.
  Itu perubahan yang lebih besar daripada bug-nya sendiri.
- Kipas bukan jobdesk koordinat. Mengubah firmware pembina tanpa perlu = risiko tanpa
  imbalan.

Kalau suatu saat memang perlu diubah (misalnya driver kepanasan), perbaikannya:

```cpp
// firmware/v2_02/fanControl.cpp:6
fan_delay = (long)aFanDelay * 1000;   // cast DULU, baru kali
```

dan **wajib** bareng dengan memilih `FAN_DELAY` yang masuk akal. Jangan ubah nilainya
saja tanpa cast -- lihat jebakan di atas, nilai 33 ke atas malah membuat kipas mati
seketika.

---

## ⚠️ Jebakan upload firmware -- config target compile BEDA dari robot

Ini bisa membuat gripper mati total. Belum pernah meledak karena firmware **belum
pernah di-upload ulang** sejak robot dipegang.

`firmware/v2_02/` adalah satu-satunya folder dengan source lengkap (`.ino` + semua
`.cpp`/`.h`), jadi itu target compile. `firmware/v2_02-pembina/` isinya **hanya
`config.h`**, tidak ada `.ino`.

Dan `v2_02/config.h` memegang nilai **GitHub**, bukan kalibrasi robot kalian:

| | `v2_02/config.h` (target compile) | `v2_02-pembina/config.h` (robot asli) |
|---|---|---|
| `X_HOME_STEPS` | 850 | **900** |
| `Z_HOME_STEPS` | 3520 | **3430** |
| `MAX_SERVO` | 160 | **155** |
| `MIN_SERVO` | 95 | **115** |

Compile & upload dari `v2_02/` apa adanya = robot di-flash servo 95/160 ->
**gripper berhenti berfungsi total** (firmware menolak sudut di luar rentang, dan
`rnv3.py` memakai 115/155), plus homing tidak presisi.

Nilai yang benar (115/155) dikonfirmasi robot sendiri saat `kontak.py` dijalankan
13 Okt: `SETUP GRIPPER SERVO : MIN 115 MAX 155`.

### SENGAJA tidak ditambal

File-nya **dibiarkan apa adanya**, supaya `firmware/v2_02/` tetap salinan murni versi
GitHub dan tidak membingungkan kalau nanti dibandingkan dengan upstream.
**Perlindungannya catatan ini, bukan tambalan.**

> **Koreksi 17 Okt — klaim "salinan murni" tidak lagi bisa dibuktikan.**
> 18 file di `firmware/v2_02/` ber-mtime `2026-10-09 14:48` (saat impor dari
> vendor), tapi dua file lebih baru:
>
> ```
> fanControl.cpp   2026-10-15 19:34
> config.h         2026-10-15 19:45
> ```
>
> Jadi kedua file itu **tersentuh pada 15 Okt**, bertentangan dengan kalimat
> "dibiarkan apa adanya" di atas dan "persis seperti aslinya" di bagian
> sebelumnya.
>
> **SUDAH DIVERIFIKASI 17 Okt — isinya BERSIH.** Kedua repo resmi di-clone
> ulang lalu di-diff file per file. Hasilnya:
>
> ```
> firmware/v2_02/            19/19 file byte-identik dengan upstream
> firmware/v2_01/            19/19 file byte-identik dengan upstream
> firmware/v2_01-interface/  19/19 identik (config.h-nya dari repo KEDUA)
> ```
>
> Termasuk kedua file bermtime 15 Okt itu: `fanControl.cpp` **identik** dan
> `config.h` **identik** dengan upstream. Jadi mtime-nya memang bergerak,
> tapi **tidak ada satu byte pun yang berubah** — kemungkinan dibuka lalu
> disimpan ulang, atau diedit lalu dikembalikan.
>
> Satu-satunya yang berbeda dari upstream: `v2_02-pembina/config.h`, dan itu
> memang seharusnya beda (kalibrasi robot kelompok, dari pembina).
>
> Pembandingnya:
>
> ```
> github.com/februandi/File_Robot_RNV3            63b22ef (2025-09-19)
> github.com/februandi/SourceCodeInterface-RNV3   (config.h v2_01-interface)
> ```
>
> **Jadi jaminan "salinan murni GitHub" sekarang terbukti, bukan diasumsikan.**
> Cara mengulangi pemeriksaan ini ada di bagian terakhir catatan.

Selama firmware tidak di-upload ulang, beda ini **tidak berefek apa pun** -- itu cuma
teks di disk. Robot jalan dengan apa yang sudah ter-flash di chip-nya.

Cara memeriksa:

```bash
diff firmware/v2_02/config.h firmware/v2_02-pembina/config.h
# NORMAL: 4 beda (850/3520/160/95  vs  900/3430/155/115)
```

### Urutan upload yang benar -- kalau suatu saat perlu

Misalnya untuk kalibrasi `END_EFFECTOR_OFFSET` (checklist langkah 21-24):

1. **Backup dulu:** `cp firmware/v2_02/config.h firmware/v2_02/config.h.bak`
2. Salin kalibrasi pembina:
   `cp firmware/v2_02-pembina/config.h firmware/v2_02/config.h`
3. `diff` keduanya -> sekarang harus **kosong**
4. **Compile dulu tanpa upload**: Arduino IDE tombol **Verify** (centang).
   Tidak perlu robot kecolok. Pastikan hijau sebelum berangkat.
5. Baru upload dari `firmware/v2_02/`
6. **Wajib** `python.exe kontak.py` sesudahnya -- harus lapor
   `SETUP GRIPPER SERVO : MIN 115 MAX 155`, `RAIL ON`, exit code 0

Langkah 6 adalah pagarnya. `kontak.py` tidak menggerakkan lengan, jadi aman untuk
memverifikasi hasil upload sebelum mengirim gerakan apa pun.

---


## Sumber

Folder ini hasil copy dari dua repo resmi, **tanpa riwayat git** (`.git` dibuang,
163 MB). Kalau nanti mau ambil update — README repo bilang file RNV3 akan terus
di-update di sana:

```bash
git clone --depth 1 https://github.com/februandi/File_Robot_RNV3.git
git clone --depth 1 https://github.com/februandi/SourceCodeInterface-RNV3.git
```

Yang **tidak** dibawa ke sini:
- `ffmpeg-master-latest-win64-gpl-shared.zip` (62 MB) — mesin ini sudah punya
  ffmpeg di WSL (7.0.2) dan Windows (8.0.1 full build), dua-duanya lebih baru.
  Dibutuhkan hanya oleh program AI buat putar audio TTS.
- `Buku panduan robot.pdf` duplikat (ada identik di kedua repo, md5 sama).
