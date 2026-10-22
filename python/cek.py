"""
Cek otomatis: apakah kode kontrol robot masih works?

Jalankan kapan saja, TANPA robot:   python3 cek.py

Kalau ada yang GAGAL, berarti ada yang berubah/rusak -- jangan colok robot dulu.

SEJAUH MANA INI BISA DIPERCAYA

Sebagian besar pemeriksaan di sini memvalidasi kode kontrol terhadap
simulasi.py. Simulator itu ditulis dari BACAAN source firmware oleh penulis
yang sama, jadi pemeriksaan semacam itu SEBAGIAN SIRKULAR: kalau bacaannya
salah, simulator dan tesnya akan salah bersama-sama dan tetap hijau.

Bagian "simulasi.py DIUJI LAWAN SOURCE FIRMWARE" memutus lingkaran itu untuk
tabel alias perintah teks: tabelnya dibaca langsung dari
../firmware/v2_02/command.cpp lalu dibandingkan. Bagian lain belum punya
pembanding independen semacam itu -- jangan baca "semua hijau" sebagai bukti
bahwa bacaan firmware-nya benar.
"""

import math
import os
import re as _re
import sys
import time as _t

# Tes TIDAK boleh membaca setelan-robot.json milik pengguna: jalur/jog/
# pick_place membacanya saat DIIMPOR, jadi ini wajib diset sebelum impor.
os.environ["RNV3_SETELAN"] = "-"
# Dicatat sebelum tes jalan, supaya bisa dibuktikan tes tidak membuatnya.
_ada_setelan_pengguna = os.path.exists(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "setelan-robot.json"))

import esp as mod_esp
import jalur
import jog as jg
import kalibrasi
import kontak
import kontrak_kamera
import konveyor
import pick_place as pp
import rnv3
import simulasi

lulus = gagal = 0

# Tes TIDAK boleh bergantung pada berkas kalibrasi milik pengguna.
# rnv3 memuat batas-fisik.json saat impor; kalau isinya menyempitkan ruang
# kerja (mis. e_maks hasil rekaman), tes yang memakai koordinat normal akan
# gagal karena lingkungan, bukan karena kodenya rusak. Jadi dipaksa ke nilai
# bawaan yang diketahui.
BATAS_BAWAAN = {"y_min": 100.0, "rot_maks": 75.0,
                "x_min": None, "x_maks": None, "y_maks": None,
                "z_min": None, "z_maks": None, "e_min": None, "e_maks": None}
# Rekam isi berkas kalibrasi apa adanya. Pengujian boleh saja berjalan di
# mesin yang SUDAH punya hasil kalibrasi -- yang dilarang adalah mengubahnya.
try:
    with open(rnv3.BERKAS_BATAS, "rb") as _f:
        _isi_berkas_awal = _f.read()
except OSError:
    _isi_berkas_awal = None
_punya_berkas = _isi_berkas_awal is not None
rnv3.BATAS_FISIK.clear()
rnv3.BATAS_FISIK.update(BATAS_BAWAAN)


def cek(nama, syarat, info=""):
    global lulus, gagal
    if syarat:
        lulus += 1
        print(f"  [OK   ] {nama}")
    else:
        gagal += 1
        print(f"  [GAGAL] {nama}  {info}")


print("=" * 68)
print("0. Lingkungan pengujian")
print("=" * 68)
cek("batas fisik dipaksa ke nilai bawaan (tes tidak ikut kalibrasi pengguna)",
    rnv3.BATAS_FISIK == BATAS_BAWAAN)
if _punya_berkas:
    print(f"  (catatan: {os.path.basename(rnv3.BERKAS_BATAS)} ada dan sengaja "
          "diabaikan selama pengujian)")

print()
print("=" * 68)
print("1. Konstanta geometri (harus cocok dengan ../firmware/*/config.h)")
print("=" * 68)
cek("panjang lengan 140/140 mm",
    rnv3.LOW_SHANK == 140.0 and rnv3.HIGH_SHANK == 140.0)
cek("end effector offset 77 mm", rnv3.EE_OFFSET == 77.0)
cek(f"R_MIN = 90.42 mm (dapat {rnv3.R_MIN:.2f})", abs(rnv3.R_MIN - 90.42) < 0.01)
cek(f"R_MAX = 263.78 mm (dapat {rnv3.R_MAX:.2f})", abs(rnv3.R_MAX - 263.78) < 0.01)
cek("Z: -140 .. 210 mm", rnv3.Z_MIN == -140.0 and rnv3.Z_MAX == 210.0)
cek("rel 355 mm & aktif", rnv3.RAIL_LENGTH == 355.0 and rnv3.RAIL_ENABLED)
cek("home X0 Y217 Z138 E0",
    (rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E) == (0.0, 217.0, 138.0, 0.0))
cek("gripper 115..155 (config.h PEMBINA, bukan 95/160 GitHub)",
    rnv3.SERVO_CLOSED == 115 and rnv3.SERVO_OPEN == 155)

print()
print("=" * 68)
print("2. Validasi titik tujuan")
print("=" * 68)
for titik, harap, label in [
    ((-50, 200, 0), True, "titik kerja normal"),
    ((0, 217, 138), True, "posisi home"),
    ((0, 400, 0), False, "terlalu jauh"),
    ((0, 100, 0), False, "masuk lubang mati dekat badan"),
    ((0, 217, -200), False, "Z di bawah lantai"),
    ((0, 217, 250), False, "Z di atas langit-langit"),
]:
    ok, why = rnv3.check_reach(*titik)
    cek(f"{label} {titik} -> {'terima' if harap else 'tolak'}", ok == harap, why)

ok, _ = rnv3.check_reach(0, 217, 0, 400)
cek("E=400 di luar rel -> tolak", not ok)

lim = rnv3.xy_limits(0)
cek(f"xy_limits(0) = (167.4, 340.8), dapat ({lim[0]:.1f}, {lim[1]:.1f})",
    abs(lim[0] - 167.4) < 0.1 and abs(lim[1] - 340.8) < 0.1)

print()
print("=" * 68)
print("3. Validasi JALUR (jebakan paling berbahaya)")
print("=" * 68)
# Dua ujung valid, tapi garis lurusnya menembus lubang mati di dekat badan.
A, B = (-250, 120, 0), (250, 120, 0)
cek("ujung A valid", rnv3.check_reach(*A)[0])
cek("ujung B valid", rnv3.check_reach(*B)[0])
ok, why, t = rnv3.check_path(A, B)
cek("tapi JALUR A->B ditolak (sapu kiri-kanan di Y dekat)", not ok, why)
cek(f"ditolak sekitar tengah jalur (t={t})", ok is False and 0.2 < t < 0.8)

ok, _, _ = rnv3.check_path((0, 217, 138), (0, 300, 0))
cek("jalur wajar home -> depan bawah: aman", ok)

print()
print("=" * 68)
print("4. Program jalan penuh lawan simulator firmware")
print("=" * 68)
bot = simulasi.robot_palsu(firmware="v2_02")
cek("firmware terdeteksi v2_02 otomatis", bot.firmware == "v2_02", bot.firmware)

bot.home()
p = bot.position()
cek("setelah home, posisi = X0 Y217 Z138 E0",
    (p["x"], p["y"], p["z"], p["e"]) == (0.0, 217.0, 138.0, 0.0), str(p))

bot.move(z=0, f=0)
for x, y in [(-50, 200), (50, 200), (50, 250), (-50, 250)]:
    bot.goto_xy(x, y, f=80)
p = bot.position()
cek("kotak 100x100 di bidang XY selesai di (-50, 250)",
    (p["x"], p["y"]) == (-50.0, 250.0), str(p))
cek("robot simulasi tidak pernah kena LIMIT", bot.sim.jumlah_limit == 0)

bot.move(e=150, f=30)
cek("rel pindah ke E=150", bot.position()["e"] == 150.0)

bot.gripper(0)
cek("gripper di-clamp ke 115 (bukan 0)", bot.sim.servo_b == 115)
bot.gripper(999)
cek("gripper di-clamp ke 155 (bukan 999)", bot.sim.servo_b == 155)

bot.vacuum(True)
cek("vakum ON", bot.sim.vacuum_on)
bot.vacuum(False)
cek("vakum OFF", not bot.sim.vacuum_on)

bot.output(2, True)
cek("output LG2 ON", bot.sim.lg[2])

s = bot.sensors()
cek("baca 3 sensor", s == {"S1": "ON", "S2": "OFF", "S3": "ON"}, str(s))

es = bot.endstops()
cek("endstop memuat E (artinya rel aktif)", "E" in es, str(es))

print()
print("=" * 68)
print("5. Pengaman: gerakan berbahaya harus ditolak SEBELUM dikirim")
print("=" * 68)
sebelum = bot.sim.jumlah_perintah
try:
    bot.move(x=0, y=400, z=0)
    cek("titik di luar jangkauan ditolak", False, "malah diterima!")
except rnv3.RNV3Error:
    cek("titik di luar jangkauan ditolak", True)
cek("dan tidak ada apa pun dikirim ke robot",
    bot.sim.jumlah_perintah == sebelum)

bot.home()
bot.move(z=0, f=0)
bot.goto_xy(-250, 120)
sebelum = bot.sim.jumlah_perintah
try:
    bot.goto_xy(250, 120)
    cek("sapu menembus lubang mati ditolak", False, "malah diterima!")
except rnv3.RNV3Error:
    cek("sapu menembus lubang mati ditolak", True)
cek("dan tidak ada apa pun dikirim ke robot",
    bot.sim.jumlah_perintah == sebelum)

print()
print("=" * 68)
print("6. Firmware v2_01: M105 harus ditolak dengan pesan jelas")
print("=" * 68)
tua = simulasi.robot_palsu(firmware="v2_01")
cek("firmware terdeteksi v2_01", tua.firmware == "v2_01", tua.firmware)
try:
    tua.sensors()
    cek("sensors() ditolak di v2_01", False, "malah jalan")
except rnv3.RNV3Error as e:
    cek("sensors() ditolak + saran upload v2_02", "v2_02" in str(e))

print()
print("=" * 68)
print("7. Pesan boot robot diperiksa kecocokannya")
print("=" * 68)
b = simulasi.robot_palsu()
cek("boot_lines memuat SETUP GRIPPER SERVO",
    any("SETUP GRIPPER SERVO" in l for l in b.boot_lines), str(b.boot_lines))
cek("rentang servo robot terbaca = (115, 155)",
    b.servo_range_reported == (115, 155), str(b.servo_range_reported))
cek("status rel robot terbaca = True", b.rail_reported is True)

import io as _io, contextlib as _ctx
buf = _io.StringIO()
with _ctx.redirect_stdout(buf):
    simulasi.robot_palsu(servo=(95, 160))
out = buf.getvalue()
cek("servo beda -> peringatan muncul", "TIDAK COCOK" in out and "95" in out)

buf = _io.StringIO()
with _ctx.redirect_stdout(buf):
    simulasi.robot_palsu(rail=False)
cek("rel beda -> peringatan muncul", "status rel TIDAK COCOK" in buf.getvalue())

print()
print("=" * 68)
print("8. Geometri grid pick_place.py")
print("=" * 68)
cek(f"jumlah slot = COLS*ROWS = {pp.jumlah_slot()}",
    pp.jumlah_slot() == pp.COLS * pp.ROWS)
x1, y1, e1 = pp.slot_coord(1)
cek("slot 1 di kolom paling kiri, baris pertama",
    abs(x1 - (pp.X_CENTER - (pp.COLS - 1) * pp.PITCH_X / 2)) < 0.01
    and e1 == pp.E_START, f"{x1},{e1}")
xl, _, el = pp.slot_coord(pp.jumlah_slot())
cek("slot terakhir di kolom paling kanan, baris terakhir",
    abs(xl - (pp.X_CENTER + (pp.COLS - 1) * pp.PITCH_X / 2)) < 0.01
    and abs(el - (pp.E_START + (pp.ROWS - 1) * pp.PITCH_E)) < 0.01, f"{xl},{el}")
try:
    pp.slot_coord(0); cek("slot 0 ditolak", False)
except ValueError: cek("slot 0 ditolak", True)
try:
    pp.slot_coord(pp.jumlah_slot() + 1); cek("slot di luar jangkauan ditolak", False)
except ValueError: cek("slot di luar jangkauan ditolak", True)

print()
print("=" * 68)
print("9. validasi_grid(): pagar utama anti bug pembina")
print("=" * 68)
aman, masalah = pp.validasi_grid(cetak=False)
cek("konfigurasi default LOLOS", aman, "; ".join(masalah[:2]))

# simpan, paksa konfigurasi pembina, pastikan DITOLAK, lalu pulihkan
_asli = (pp.COLS, pp.ROWS, pp.Y_ROW, pp.PITCH_X, pp.Z_SAFE, pp.PITCH_E, pp.X_CENTER)
pp.COLS, pp.ROWS = 6, 5
pp.Y_ROW, pp.PITCH_X, pp.Z_SAFE, pp.PITCH_E = 340.0, 70.0, 100.0, 40.0
pp.X_CENTER = -250 + (6 - 1) * 70 / 2
aman_p, masalah_p = pp.validasi_grid(cetak=False)
cek("konfigurasi ala PEMBINA (Y=340, Z_SAFE=100) DITOLAK", not aman_p)
cek("alasannya disebut spesifik per slot", len(masalah_p) > 20, f"{len(masalah_p)}")
cek("slot 1 pembina memang di luar jangkauan",
    not rnv3.check_reach(*pp.slot_coord(1)[:2], 100.0)[0])

# rel kehabisan panjang juga harus ketangkap
pp.COLS, pp.ROWS, pp.Y_ROW, pp.PITCH_X, pp.Z_SAFE = 5, 4, 260.0, 70.0, 80.0
pp.X_CENTER, pp.PITCH_E = 0.0, 200.0
_, m_rel = pp.validasi_grid(cetak=False)
cek("PITCH_E kebablasan panjang rel -> ditolak",
    any("rel cuma" in m for m in m_rel), "; ".join(m_rel[:1]))

pp.COLS, pp.ROWS, pp.Y_ROW, pp.PITCH_X, pp.Z_SAFE, pp.PITCH_E, pp.X_CENTER = _asli
cek("konfigurasi default pulih setelah diuji", pp.validasi_grid(cetak=False)[0])

print()
print("=" * 68)
print("10. pick / place / pindah di simulator")
print("=" * 68)
bot = simulasi.robot_palsu()
bot.home()
_hening = _ctx.redirect_stdout   # pick/place cetak banyak; dibungkam di sini
with _hening(_io.StringIO()):
    pp.pick(bot, 1)
cek("pick() benar-benar MENYALAKAN vakum (bug utama file pembina)",
    bot.sim.vacuum_on is True)
cek("setelah pick, robot ada di Z_SAFE", bot.z == pp.Z_SAFE, str(bot.z))
with _hening(_io.StringIO()):
    pp.place(bot, 12)
cek("place() mematikan vakum", bot.sim.vacuum_on is False)
x12, y12, e12 = pp.slot_coord(12)
cek("setelah place, posisi = koordinat slot 12",
    (bot.x, bot.y, bot.e) == (x12, y12, e12), f"{bot.x},{bot.y},{bot.e}")
cek("tidak pernah kena LIMIT sepanjang pick+place", bot.sim.jumlah_limit == 0)

bot2 = simulasi.robot_palsu()
bot2.home()
with _hening(_io.StringIO()):
    pp.pindah(bot2, 3, 18)
x18, y18, e18 = pp.slot_coord(18)
cek("pindah(3, 18) berakhir di slot 18",
    (bot2.x, bot2.y, bot2.e) == (x18, y18, e18))
cek("pindah() mengakhiri dengan vakum mati", bot2.sim.vacuum_on is False)
try:
    with _hening(_io.StringIO()):
        pp.pindah(bot2, 5, 5)
    cek("pindah ke slot yang sama ditolak", False)
except ValueError: cek("pindah ke slot yang sama ditolak", True)

bot3 = simulasi.robot_palsu()
bot3.home()
with _hening(_io.StringIO()):
    for n in range(1, pp.jumlah_slot() + 1):
        pp.pick(bot3, n)
        pp.place(bot3, n)
    pp.parkir(bot3)
cek(f"seluruh {pp.jumlah_slot()} slot dilalui tanpa sekali pun kena LIMIT",
    bot3.sim.jumlah_limit == 0, f"limit={bot3.sim.jumlah_limit}")
cek("parkir() berakhir di X/Y netral dengan Z RENDAH (bukan home yang tinggi)",
    (bot3.x, bot3.y, bot3.z, bot3.e) == (rnv3.HOME_X, rnv3.HOME_Y,
                                         pp.Z_PARKIR, rnv3.HOME_E),
    f"{bot3.x},{bot3.y},{bot3.z},{bot3.e}")
cek("Z parkir pick_place lebih rendah dari home (biar jatuhnya sedikit)",
    pp.Z_PARKIR < rnv3.HOME_Z, f"{pp.Z_PARKIR} vs {rnv3.HOME_Z}")
cek("Z parkir pick_place sama dengan titik parkir jog.py (konsisten)",
    pp.Z_PARKIR == jg.PARKIR[2], f"{pp.Z_PARKIR} vs {jg.PARKIR[2]}")

print()
print("=" * 68)
print("11. kontak.py — verifikasi robot tanpa menggerakkan lengan")
print("=" * 68)

bk = simulasi.robot_palsu()
h = kontak.baca_robot(bk)
cek("baca_robot() dapat firmware", h["firmware"] == "v2_02", str(h["firmware"]))
cek("baca_robot() dapat rentang servo", h["servo"] == (115, 155), str(h["servo"]))
cek("baca_robot() dapat status rel", h["rail"] is True)
cek("baca_robot() dapat endstop", h["endstop"] == {"X": "0", "Y": "0", "Z": "0", "E": "0"},
    str(h["endstop"]))
cek("baca_robot() dapat 3 sensor",
    h["sensor"] == {"S1": "ON", "S2": "OFF", "S3": "ON"}, str(h["sensor"]))

# ---- PAGAR KESELAMATAN: hanya perintah baca-saja yang boleh dikirim ----
BOLEH = {"M105", "M114", "M119"}
dikirim = set(bk.sim.log_perintah)
cek(f"kontak.py cuma kirim perintah baca-saja {sorted(dikirim)}",
    dikirim <= BOLEH, f"ada yang terlarang: {sorted(dikirim - BOLEH)}")
# Cocokkan KODE PERSIS, bukan awalan -- "M114".startswith("M1") itu True,
# jadi pengecekan awalan akan salah menandai perintah baca-saja.
TERLARANG = {"G0", "G1", "G4", "G28", "G90", "G91", "G92", "G100",
             "M1", "M2", "M6", "M7", "M17", "M18", "M106", "M107",
             "M205", "M206", "M207", "M208", "M209", "M230"}

def _kode(c):
    c = _re.sub(r"\s+", "", c.upper())
    m = _re.match(r"^([GM]\d+)", c)
    return m.group(1) if m else c

_terkirim_kode = [_kode(c) for c in bk.sim.log_perintah]
cek("tidak ada satu pun KODE gerakan terkirim",
    not (set(_terkirim_kode) & TERLARANG), str(sorted(set(_terkirim_kode))))
cek("tidak ada perintah teks VACUM/LG terkirim",
    not any(t in _re.sub(r"\s+", "", c.upper())
            for c in bk.sim.log_perintah for t in ("VACUM", "LG1", "LG2", "LG3")),
    str(bk.sim.log_perintah))
cek("lengan simulator tidak bergeser sedikit pun",
    (bk.sim.x, bk.sim.y, bk.sim.z, bk.sim.e) ==
    (rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E))

def _nilai(baris, nama):
    for n, robot, kode, cocok, saran in baris:
        if n == nama:
            return robot, kode, cocok, saran
    return None

v = kontak.verdict(h)
cek("verdict(): servo 115/155 -> cocok", _nilai(v, "rentang servo")[2] == "cocok")
cek("verdict(): rel ON -> cocok", _nilai(v, "rel")[2] == "cocok")
cek("verdict(): endstop lengan bebas -> cocok",
    _nilai(v, "endstop lengan")[2] == "cocok")

b95 = simulasi.robot_palsu(servo=(95, 160))
v95 = kontak.verdict(kontak.baca_robot(b95))
rb, _, ck, sr = _nilai(v95, "rentang servo")
cek("verdict(): servo 95/160 -> beda", ck == "beda", str(rb))
cek("saran menyebut angka 95 dan 160", "95" in sr and "160" in sr, sr)

brail = simulasi.robot_palsu(rail=False)
vr = kontak.verdict(kontak.baca_robot(brail))
_, _, ckr, srr = _nilai(vr, "rel")
cek("verdict(): rel OFF padahal kode ON -> beda", ckr == "beda")
cek("saran menyebut RAIL_ENABLED = False", "RAIL_ENABLED = False" in srr, srr)

# Endstop LENGAN tersentuh = perlu perhatian (presisi), bukan kegagalan:
# Endstop::home() melewati loop pencarian lalu langsung homeOffset().
bes = simulasi.robot_palsu(endstop=(1, 1, 0, 0))
ve = kontak.verdict(kontak.baca_robot(bes))
rbe, _, cke, sre = _nilai(ve, "endstop lengan")
cek("verdict(): endstop lengan X,Y tersentuh -> perhatian (bukan gagal)",
    cke == "perhatian", str(rbe))
cek("saran menyuruh geser lengan pakai tangan", "PAKAI TANGAN" in sre, sre)
cek("endstop lengan tersentuh TIDAK bikin exit code 1",
    not any(c == "beda" for _, _, _, c, _ in ve))

# Endstop REL tersentuh = posisi parkir normal, cuma info. Ini kondisi
# robot sungguhan saat kontak pertama (ENDSTOP: [X:0 Y:0 Z:0 E:1]).
brel = simulasi.robot_palsu(endstop=(0, 0, 0, 1))
vrel = kontak.verdict(kontak.baca_robot(brel))
cek("verdict(): endstop rel tersentuh -> info saja",
    _nilai(vrel, "endstop rel")[2] == "info")
cek("endstop rel tersentuh TIDAK bikin exit code 1",
    not any(c == "beda" for _, _, _, c, _ in vrel))
cek("kondisi robot sungguhan (E:1, sisanya bebas) -> lolos penuh",
    all(c in ("cocok", "info") for _, _, _, c, _ in vrel))

b01 = simulasi.robot_palsu(firmware="v2_01")
h01 = kontak.baca_robot(b01)
cek("firmware v2_01: sensors() ditolak rapi tanpa exception",
    h01["sensor"] is None and h01["sensor_error"] is not None)
cek("firmware v2_01: pembacaan lain tetap jalan",
    h01["servo"] == (115, 155) and h01["rail"] is True)
v01 = kontak.verdict(h01)
cek("firmware v2_01: M105 tak tersedia dianggap info, bukan kegagalan",
    _nilai(v01, "sensor input")[2] == "info")
cek("firmware v2_01 tetap lolos keseluruhan",
    not any(c == "beda" for _, _, _, c, _ in v01))

print()
print("=" * 68)
print("12. jog.py — kendali manual, setiap gerakan divalidasi dulu")
print("=" * 68)

cek("kumpulkan() menggabungkan tekanan berurutan",
    jg.kumpulkan(["KANAN", "KANAN", "KANAN", "ATAS", "KANAN"]) ==
    [("KANAN", 3), ("ATAS", 1), ("KANAN", 1)])
cek("kumpulkan() daftar kosong -> kosong", jg.kumpulkan([]) == [])

cek("WASD: A/D = PUTAR badan kiri/kanan, W/S = Y maju/mundur",
    jg.ARAH["a"] == ("rot", -1) and jg.ARAH["d"] == ("rot", 1) and
    jg.ARAH["w"] == ("y", 1) and jg.ARAH["s"] == ("y", -1))
cek("IJKL: I/K = Z naik/turun, J/L = rel kiri/kanan",
    jg.ARAH["i"] == ("z", 1) and jg.ARAH["k"] == ("z", -1) and
    jg.ARAH["j"] == ("e", -1) and jg.ARAH["l"] == ("e", 1))
cek("alias panah & PgUp/PgDn masih menunjuk sumbu yang sama",
    jg.ARAH["KIRI"] == jg.ARAH["a"] and jg.ARAH["KANAN"] == jg.ARAH["d"] and
    jg.ARAH["ATAS"] == jg.ARAH["w"] and jg.ARAH["BAWAH"] == jg.ARAH["s"] and
    jg.ARAH["PGUP"] == jg.ARAH["i"] and jg.ARAH["PGDN"] == jg.ARAH["k"])

# ---- A/D memutar badan, lengan TIDAK ikut menekuk ----
# Keluhan di robot asli: tekan A/D, lengan atas & bawah ikut bergerak. Dulu A/D
# menggeser X dengan Y tetap, jadi radius dan sudut bahu/siku berubah.
_sd_x0 = rnv3.sudut_sendi(0, 217, 138)
_sd_x100 = rnv3.sudut_sendi(100, 217, 138)
cek("[bukti masalah lama] geser X 0->100 di Y217 mengubah sudut bahu",
    abs(_sd_x100[1] - _sd_x0[1]) > 5.0,
    f"bahu {_sd_x0[1]:.1f} -> {_sd_x100[1]:.1f}")

bpt = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bpt.home()
    bpt.move(z=0, f=20)
jpt = jg.Jog(bpt)
jpt.kontinu = False
jpt.i_step = 4                                   # 25 mm per tekan
_r0, _z0 = math.hypot(bpt.x, bpt.y), bpt.z
_sd0 = rnv3.sudut_sendi(bpt.x, bpt.y, bpt.z)
_r_lintas, _bahu_lintas, _siku_lintas = [], [], []
with _hening(_io.StringIO()):
    for _tok in ["d"] * 6 + ["a"] * 12:
        _n_sebelum = len(bpt.sim.log_perintah)
        _awal = (bpt.x, bpt.y, bpt.z)
        if not jpt.tangani(_tok):
            break
        # periksa SEPANJANG tali busur yang benar-benar dijalani firmware
        for _i in range(21):
            _t_ = _i / 20
            _px = _awal[0] + _t_ * (bpt.x - _awal[0])
            _py = _awal[1] + _t_ * (bpt.y - _awal[1])
            _r_lintas.append(math.hypot(_px, _py))
            _sd = rnv3.sudut_sendi(_px, _py, bpt.z)
            _bahu_lintas.append(_sd[1]); _siku_lintas.append(_sd[2])
_rot_akhir = rnv3.sudut_sendi(bpt.x, bpt.y, bpt.z)[0]
cek("A/D: badan benar-benar berputar (bukan diam)",
    abs(_rot_akhir - _sd0[0]) > 10.0, f"rot {_sd0[0]:.1f} -> {_rot_akhir:.1f}")
cek("A/D: radius ujung tool tetap (maks melenceng <= 0.35 mm sepanjang jalur)",
    max(abs(r - _r0) for r in _r_lintas) <= 0.35,
    f"r0={_r0:.2f} rentang {min(_r_lintas):.2f}..{max(_r_lintas):.2f}")
cek("A/D: Z tidak berubah", abs(bpt.z - _z0) < 1e-9, f"{_z0} -> {bpt.z}")
cek("A/D: sudut bahu nyaris tetap (lengan bawah tidak ikut jalan)",
    max(abs(b - _sd0[1]) for b in _bahu_lintas) < 0.3,
    f"{min(_bahu_lintas):.2f}..{max(_bahu_lintas):.2f} vs {_sd0[1]:.2f}")
cek("A/D: sudut siku nyaris tetap (lengan atas tidak ikut jalan)",
    max(abs(k - _sd0[2]) for k in _siku_lintas) < 0.3,
    f"{min(_siku_lintas):.2f}..{max(_siku_lintas):.2f} vs {_sd0[2]:.2f}")
cek("A/D: pesan status dalam derajat putar", "putar" in jpt.pesan, jpt.pesan)

# ditahan lama: pembulatan 2 desimal di G0 tidak boleh menumpuk jadi drift
bdr = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bdr.home()
jdr = jg.Jog(bdr)
jdr.kontinu = False
jdr.i_step = 0                                   # 0.5 mm, potongan terbanyak
_rd0 = math.hypot(bdr.x, bdr.y)
with _hening(_io.StringIO()):
    for _ in range(300):
        jdr.tangani("d")
    for _ in range(300):
        jdr.tangani("a")
cek("A/D 600x bolak-balik: radius tidak hanyut (< 0.2 mm)",
    abs(math.hypot(bdr.x, bdr.y) - _rd0) < 0.2,
    f"{_rd0:.3f} -> {math.hypot(bdr.x, bdr.y):.3f}")

# putar sampai mentok: berhenti di batas putar fisik, bukan lewat
bmp = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bmp.home()
jmp = jg.Jog(bmp)
jmp.kontinu = False
jmp.i_step = 4
with _hening(_io.StringIO()):
    for _ in range(80):
        if not jmp.tangani("d") or "MENTOK" in jmp.pesan:
            break
# Batas mana yang kena duluan tergantung radius (rot_maks atau y_min), jadi
# yang dicek: titik berhenti masih aman, dan 0.5 mm busur lagi sudah tidak.
cek("putar terus ke kanan: berhenti TEPAT di tepi batas fisik",
    rnv3.check_aman(bmp.x, bmp.y, bmp.z, bmp.e)[0] and not rnv3.check_aman(
        *jg.geser(bmp.x, bmp.y, bmp.z, bmp.e, "rot", +1, 0.5))[0],
    f"X{bmp.x:.2f} Y{bmp.y:.2f}")
cek("dan sisa ke arah itu nol", jg.jarak_tersisa(
    bmp.x, bmp.y, bmp.z, bmp.e, "rot", +1) < jg.MENTOK)

# ; setelah memutar -> merekam rot_maks, bukan crash
import tempfile as _tmp_rot
_berkas_rot = os.path.join(_tmp_rot.gettempdir(), "uji-batas-rot-rnv3.json")
_asli_rot = dict(rnv3.BATAS_FISIK)
_berkas_asli_rot = rnv3.BERKAS_BATAS
rnv3.BERKAS_BATAS = _berkas_rot
brr = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    brr.home()
    jrr = jg.Jog(brr)
    jrr.kontinu = False
    jrr.i_step = 4
    jrr.tangani("a"); jrr.tangani("a")
    _ok_rot = jrr.rekam_batas()
rnv3.BERKAS_BATAS = _berkas_asli_rot
_rot_rr = abs(rnv3.sudut_sendi(brr.x, brr.y, brr.z)[0])
cek("; setelah A/D merekam rot_maks = sudut putar sekarang",
    _ok_rot and rnv3.BATAS_FISIK.get("rot_maks") == round(_rot_rr, 1),
    f"rot_maks={rnv3.BATAS_FISIK.get('rot_maks')} vs {_rot_rr:.1f}")
rnv3.BATAS_FISIK.clear(); rnv3.BATAS_FISIK.update(_asli_rot)
try: os.remove(_berkas_rot)
except OSError: pass

# PAGAR: tombol aksi tidak boleh bentrok dengan tombol gerak.
AKSI = set("12345[]-=0gbvpmnohxXrtcq?")
cek("tidak ada tombol aksi yang bentrok dengan tombol gerak",
    not (AKSI & set(jg.ARAH)), str(sorted(AKSI & set(jg.ARAH))))

bj = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bj.home()
j = jg.Jog(bj)
cek("default aman: step 0.5 mm (presisi), speed 20 mm/s",
    j.step == 0.5 and j.speed == 20)

j.tangani("3")
cek("tombol 3 -> step 5 mm", j.step == 5.0)
j.tangani("1")
cek("tombol 1 -> step 0.5 mm", j.step == 0.5)
for _ in range(9):
    j.tangani("[")
cek("[ menurunkan kehalusan, di-clamp di batas bawah",
    j.a_target == jg.A_PILIHAN[0], f"{j.a_target}")
for _ in range(99):
    j.tangani("]")
cek("] menaikkan, di-clamp di batas atas",
    j.a_target == jg.A_PILIHAN[-1], f"{j.a_target}")
j.i_accel = jg.A_DEFAULT

for _ in range(9):
    j.tangani("-")
cek("- memperpendek sapuan, di-clamp di batas bawah",
    j.sapuan == jg.SAPUAN_PILIHAN[0], f"{j.sapuan}")
for _ in range(99):
    j.tangani("=")
cek("= memperpanjang sapuan, di-clamp di batas atas",
    j.sapuan == jg.SAPUAN_PILIHAN[-1], f"{j.sapuan}")
cek("tangga ikut bergeser saat sapuan diubah",
    j.tangga[-1][1] == j.sapuan and j.tangga[0][1] < j.sapuan,
    str([st for _, st in j.tangga]))
j.i_sapuan = jg.SAPUAN_DEFAULT

# gerakan normal benar-benar menggerakkan
j.i_step, j.i_speed = 1, 1       # paksa 1.0 mm supaya angkanya bulat
x0 = bj.x
with _hening(_io.StringIO()):
    j.tangani("d", 3)
# D sekarang memutar badan: 3 mm itu panjang BUSUR di ujung tool, jadi X
# bertambah sedikit kurang dari 3 dan Y sedikit turun (radius tetap).
cek("D x3 dengan step 1 mm -> berputar sejauh busur 3 mm",
    abs(bj.x - (x0 + 3.0)) < 0.01, f"{x0} -> {bj.x}")
cek("dan dikirim sebagai SATU perintah G0, bukan tiga",
    bj.sim.log_perintah[-1].startswith("G0") and
    "X" + f"{x0 + 3.0:.2f}" in bj.sim.log_perintah[-1],
    bj.sim.log_perintah[-1])

# gerakan di luar jangkauan: TIDAK mengirim apa pun.
# Mode diskret dipakai di sini supaya step manual (25 mm) yang berlaku --
# kalau kontinu, loop rapat ini terdeteksi "ditahan" dan step mengecil.
j.kontinu = False
with _hening(_io.StringIO()):
    j.tangani("h")
    j.i_step = 4                      # 25 mm
    for _ in range(20):               # dorong Y sampai mentok
        j.tangani("w")
sebelum = bj.sim.jumlah_perintah
pos_sebelum = (bj.x, bj.y, bj.z, bj.e)
with _hening(_io.StringIO()):
    bergerak = j.gerak("y", +1, 1)
cek("gerakan di luar jangkauan: tidak bergerak", bergerak is False)
cek("dan TIDAK mengirim apa pun ke robot",
    bj.sim.jumlah_perintah == sebelum, f"{sebelum} -> {bj.sim.jumlah_perintah}")
cek("posisi tetap sinkron setelah ditolak",
    (bj.x, bj.y, bj.z, bj.e) == pos_sebelum)
cek("alasan penolakan disebut", "MENTOK" in j.pesan, j.pesan)

# jalur menembus lubang mati juga ditolak tanpa kirim
bj2 = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bj2.home()
    bj2.move(z=0, f=20)
    bj2.move(x=-250, y=120, f=20)
j2 = jg.Jog(bj2)
j2.kontinu = False
j2.i_step = 4                          # 25 mm, cukup besar untuk menembus
# Dulu gerakan ke arah lubang mati DITOLAK mentah-mentah, membuang ruang yang
# sebenarnya masih bisa dipakai. Sekarang potongan dijepit ke sisa jarak, jadi
# lengan melandai sampai TEPAT di tepi lubang lalu berhenti dengan pesan jelas.
with _hening(_io.StringIO()):
    for _ in range(12):
        if not j2.gerak("x", +1, 1):
            break
cek("mendekati lubang mati: berhenti TEPAT di tepinya, bukan ditolak dari jauh",
    jg.jarak_tersisa(bj2.x, bj2.y, bj2.z, bj2.e, "x", +1) < 0.5,
    f"X={bj2.x:.2f}")
cek("titik berhenti masih valid", rnv3.check_reach(bj2.x, bj2.y, bj2.z, bj2.e)[0])
cek("satu milimeter lagi sudah masuk lubang mati (jadi memang tepi)",
    not rnv3.check_reach(bj2.x + 1.0, bj2.y, bj2.z, bj2.e)[0])
cek("dan pesannya menyebut MENTOK + arahnya",
    "MENTOK" in j2.pesan and "X+" in j2.pesan, j2.pesan)

# pulang
bj3 = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bj3.home()
    bj3.move(z=0, f=20)
    bj3.move(x=100, y=250, f=20)
    j3 = jg.Jog(bj3)
    j3.tangani("h")
cek("h (pulang) berakhir tepat di home",
    (bj3.x, bj3.y, bj3.z, bj3.e) ==
    (rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E),
    f"{bj3.x},{bj3.y},{bj3.z},{bj3.e}")

# q menandai selesai, lalu bersih_bersih mematikan vakum
bj4 = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bj4.home()
    j4 = jg.Jog(bj4)
    j4.tangani("v")
cek("v menyalakan vakum", bj4.sim.vacuum_on is True)
cek("q menandai sesi selesai",
    j4.tangani("q") is False and j4.selesai is True)
with _hening(_io.StringIO()):
    j4.bersih_bersih()
cek("keluar selalu mematikan vakum", bj4.sim.vacuum_on is False)
cek("keluar PARKIR rendah, bukan pulang ke home yang tinggi",
    (bj4.x, bj4.y, bj4.z, bj4.e) == j4.parkir_titik,
    f"{bj4.x},{bj4.y},{bj4.z} vs {j4.parkir_titik}")
cek("titik parkir lebih rendah dari home (biar jatuhnya sedikit)",
    j4.parkir_titik[2] < rnv3.HOME_Z,
    f"Z parkir {j4.parkir_titik[2]} vs home {rnv3.HOME_Z}")
cek("keluar (default) TIDAK mengirim M18 -- lengan tidak jatuh",
    "M18" not in [c.upper().replace(" ", "") for c in bj4.sim.log_perintah])
cek("keluar (default) motor tetap terkunci", bj4.sim.steppers_on is True)

# Perilaku lama tetap benar kalau flag dinyalakan lagi.
bj5 = simulasi.robot_palsu()
_flag_lama = jg.MATIKAN_SAAT_KELUAR
jg.MATIKAN_SAAT_KELUAR = True
try:
    with _hening(_io.StringIO()):
        bj5.home()
        j5 = jg.Jog(bj5)
        j5.bersih_bersih()
finally:
    jg.MATIKAN_SAAT_KELUAR = _flag_lama
cek("flag True: keluar MEMATIKAN motor & kipas (M18) setelah parkir",
    "M18" in [c.upper().replace(" ", "") for c in bj5.sim.log_perintah])
cek("flag True: M18 dikirim SETELAH parkir, bukan sebelumnya",
    [c.upper().replace(" ", "") for c in bj5.sim.log_perintah].index("M18") >
    max(i for i, c in enumerate(bj5.sim.log_perintah) if c.startswith("G0")))
cek("flag True: simulator mencatat stepper mati", bj5.sim.steppers_on is False)

# gripper memakai rentang robot
bj5 = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bj5.home()
    j5 = jg.Jog(bj5)
    j5.tangani("g")
cek("g -> gripper ke SERVO_CLOSED", bj5.sim.servo_b == rnv3.SERVO_CLOSED)
with _hening(_io.StringIO()):
    j5.tangani("b")
cek("b -> gripper ke SERVO_OPEN", bj5.sim.servo_b == rnv3.SERVO_OPEN)

# pra-terbang menolak config yang tidak cocok
b95 = simulasi.robot_palsu(servo=(95, 160))
buf = _io.StringIO()
with _ctx.redirect_stdout(buf):
    lolos = jg.praterbang(b95)
cek("praterbang() MENOLAK kalau config tidak cocok", lolos is False)
cek("alasannya dicetak", "TIDAK COCOK" in buf.getvalue())
bok = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    lolos_ok = jg.praterbang(bok)
cek("praterbang() lolos kalau config cocok", lolos_ok is True)

# ---- jog kontinu + akselerasi ----
cek("sapuan default 15 mm (lepas tombol -> maju <=15 mm)",
    jg.SAPUAN_PILIHAN[jg.SAPUAN_DEFAULT] == 15.0)
cek("tangga potongan naik monoton",
    [st for _, st in jg.tangga_step(15.0)] ==
    sorted(st for _, st in jg.tangga_step(15.0)))

bja = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bja.home()
ja = jg.Jog(bja)
cek("mode kontinu aktif secara default", ja.kontinu is True)
cek("profil bawaan = KOSINUS (bukan datar yang bikin hentakan)",
    ja.profil == jg.PROFIL_KOSINUS)

st0, sp0, tahan0 = ja.step_efektif("d")
cek("penekanan pertama pakai step MANUAL, belum akselerasi",
    (st0, sp0, tahan0) == (ja.step, ja.speed, False), f"{st0},{sp0},{tahan0}")

# tiru gerakan pertama baru saja selesai, lalu tombol masih ditahan
ja._selesai_terakhir = _t.monotonic()
st1, sp1, tahan1 = ja.step_efektif("d")
cek("penekanan beruntun terdeteksi sebagai DITAHAN", tahan1 is True)
cek("tangga pertama saat ditahan lebih kecil dari sapuan maks",
    0 < st1 < ja.sapuan, f"{st1}")

ja._tahan_mulai -= 0.5                            # pura-pura sudah ditahan 0.5s
ja._selesai_terakhir = _t.monotonic()
st2, sp2, _ = ja.step_efektif("d")
cek("ditahan ~0.5 s -> potongan membesar", st2 > st1, f"{st1}->{st2}")

ja._tahan_mulai -= 1.0                            # total ~1.5s
ja._selesai_terakhir = _t.monotonic()
st3, sp3, _ = ja.step_efektif("d")
cek("ditahan ~1.5 s -> potongan = sapuan maks",
    st3 == ja.sapuan, f"{st3}")

st4, sp4, tahan4 = ja.step_efektif("a")        # ganti arah
cek("ganti arah mereset akselerasi", tahan4 is False and st4 == ja.step)

ja.step_efektif("d"); ja.step_efektif("d")
ja.lepas_tahan()
st5, _, tahan5 = ja.step_efektif("d")
cek("lepas_tahan() mengembalikan ke step manual",
    tahan5 is False and st5 == ja.step)

# saat ditahan, tekanan menumpuk TIDAK dikalikan -- kalau dikalikan, lengan
# akan terus jalan lama setelah tombol dilepas
bjb = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bjb.home()
jb = jg.Jog(bjb)
jb.step_efektif("d")                          # tekan sekali dulu
x_awal = bjb.x
with _hening(_io.StringIO()):
    jb.tangani("d", ulang=10)                 # 10 menumpuk, tapi ditahan
geser = abs(bjb.x - x_awal)
cek("saat ditahan, 10 tekanan menumpuk tetap SATU potongan <= sapuan maks",
    geser <= jb.sapuan + 1e-9, f"bergeser {geser} mm")

# gerakan ditolak mereset akselerasi
bjc = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bjc.home()
jc = jg.Jog(bjc)
jc.kontinu = False
jc.i_step = 4
with _hening(_io.StringIO()):
    for _ in range(20):
        jc.gerak("y", +1, 1)
jc.kontinu = True
jc.step_efektif("w")
jc.step_efektif("w")
with _hening(_io.StringIO()):
    jc.gerak("y", +1, 1, step=25.0, speed=40)     # pasti ditolak
cek("gerakan yang ditolak mereset akselerasi", jc._tahan_token is None)

# ---- pembatas percepatan (penyebab robot bergetar sampai bergeser) ----
cek("rumus percepatan sesuai SPEED_PROFILE 2: a = PI^2*v^2/(2d)",
    abs(jg.accel_puncak(20, 5) - 394.8) < 1.0, f"{jg.accel_puncak(20,5):.1f}")
cek("acuan terbukti mulus (F20 @ 5mm) ada di dalam rentang kehalusan",
    jg.A_PILIHAN[0] <= jg.accel_puncak(20, 5) <= jg.A_PILIHAN[-1])
cek("kehalusan default 200 mm/s2 (lebih lembut dari 400 yang lama)",
    jg.A_PILIHAN[jg.A_DEFAULT] == 200.0)
for _a in jg.A_PILIHAN:
    for _sp in jg.SAPUAN_PILIHAN:
        _ok = all(jg.accel_puncak(jg.batas_speed(st, jg.PROFIL_KOSINUS, _a), st)
                  <= _a * 1.02 for _, st in jg.tangga_step(_sp))
        if not _ok:
            break
cek("SEMUA kombinasi kehalusan x sapuan menjaga percepatan di bawah batas", _ok)
cek("speed_halus() tidak pernah di bawah 5 mm/s (batas firmware)",
    jg.speed_halus(0.01, 200) >= 5.0)

bjd = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bjd.home(); bjd.move(z=0, f=20)
jd = jg.Jog(bjd); jd.kontinu = False
jd.i_step, jd.i_speed = 1, 5            # step 1 mm, minta F250
with _hening(_io.StringIO()):
    jd.gerak("x", +1, 1)
cek("F250 pada step 1 mm DIBATASI, bukan dikirim apa adanya",
    jd.sp_dipakai < jd.sp_diminta and jd.sp_dipakai <= jg.speed_halus(1.0, 200) + 0.05,
    f"diminta {jd.sp_diminta} dipakai {jd.sp_dipakai}")
cek("hasil percepatannya tetap di bawah batas",
    jg.accel_puncak(jd.sp_dipakai, 1.0) <= jd.a_target * 1.02)

# tanpa pembatas, setting yang sama menghasilkan percepatan brutal
cek("tanpa pembatas, F250 @ 1mm = lebih dari 100x batas kehalusan",
    jg.accel_puncak(250, 1.0) > jd.a_target * 100,
    f"{jg.accel_puncak(250,1.0):.0f}")

jd.kasar = True
with _hening(_io.StringIO()):
    jd.gerak("x", -1, 1)
cek("--kasar melewati pembatas (hanya untuk uji)",
    jd.sp_dipakai == jd.sp_diminta)

# ---- profil kecepatan datar: inti penghilang getaran ----
cek("M205 bisa ganti profil tanpa upload firmware (ada di firmware v2_01 & v2_02)",
    jg.PROFIL_DATAR == 0 and jg.PROFIL_KOSINUS == 2)

bp = simulasi.robot_palsu()
cek("robot boot dengan profil kosinus (bawaan config.h)",
    bp.sim.speed_profile == 2)
with _hening(_io.StringIO()):
    bp.home()
    jp = jg.Jog(bp)
    ok_profil = jp.pakai_profil(jg.PROFIL_DATAR)
cek("pakai_profil(DATAR) berhasil", ok_profil is True)
cek("M205 S0 benar-benar terkirim",
    "M205 S0" in bp.sim.log_perintah, str(bp.sim.log_perintah[-3:]))
cek("robot sekarang pakai profil datar", bp.sim.speed_profile == 0)
cek("Jog mencatat profil aktif", jp.profil == jg.PROFIL_DATAR)

with _hening(_io.StringIO()):
    jp.bersih_bersih()
cek("keluar MENGEMBALIKAN profil ke kosinus",
    bp.sim.speed_profile == 2 and "M205 S2" in bp.sim.log_perintah)

# INI KUNCI KEMULUSAN: pada profil datar semua potongan harus kecepatan SAMA.
kec_datar = [jg.batas_speed(st, jg.PROFIL_DATAR, 200) for _, st in jg.tangga_step(15.0)]
cek("profil DATAR: semua potongan kecepatan SAMA (tapi mulai melonjak)",
    len(set(kec_datar)) == 1, str(kec_datar))
cek("kecepatan datar itu = V_DATAR_MAKS", kec_datar[0] == jg.V_DATAR_MAKS)

kec_kos = [jg.batas_speed(st, jg.PROFIL_KOSINUS, 200) for _, st in jg.tangga_step(15.0)]
cek("profil KOSINUS: kecepatan berbeda-beda (karena itu ada lonjakan)",
    len(set(kec_kos)) == len(kec_kos),
    str([round(v, 1) for v in kec_kos]))

# batas_speed memilih rumus yang benar per profil
cek("batas_speed() profil datar = batas tetap, tidak tergantung panjang",
    jg.batas_speed(0.5, jg.PROFIL_DATAR, 200) == jg.batas_speed(15.0, jg.PROFIL_DATAR, 200))
cek("batas_speed() profil kosinus = naik dengan panjang potongan",
    jg.batas_speed(15.0, jg.PROFIL_KOSINUS, 200) > jg.batas_speed(0.5, jg.PROFIL_KOSINUS, 200))

# firmware menolak M205 -> jog tetap jalan, tidak meledak
class _TanpaM205(simulasi.SerialPalsu):
    def _proses(self, msg):
        if msg.upper().replace(" ", "").startswith("M205"):
            self.log_perintah.append(msg)
            self._say("ERROR: COMMAND NOT RECOGNIZED")
            self._say("ok")
            return
        super()._proses(msg)

bt = simulasi.robot_palsu()
bt.ser = _TanpaM205()
bt.sim = bt.ser
with _hening(_io.StringIO()):
    bt._drain_boot(quiet=0.0, limit=1.0)
    jt = jg.Jog(bt)
    hasil_tolak = jt.pakai_profil(jg.PROFIL_DATAR)
cek("firmware menolak M205 -> return False, bukan exception",
    hasil_tolak is False)
cek("dan profil tetap kosinus, jog masih bisa dipakai",
    jt.profil == jg.PROFIL_KOSINUS)

# tekan sekali tetap presisi
bq = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bq.home()
jq = jg.Jog(bq)
cek("step manual default 0.5 mm (tekan sekali = presisi)", jq.step == 0.5)
st_tap, _, tahan_tap = jq.step_efektif("d")
cek("tekan sekali memakai step manual, bukan potongan sapuan",
    st_tap == 0.5 and tahan_tap is False, f"{st_tap}")

# ---- parkir & kendali motor (robot loyo saat dimatikan) ----
bpk = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bpk.home()
    bpk.move(z=0, f=20)
    bpk.move(x=100, y=250, f=20)
jpk = jg.Jog(bpk)
n_awal = len(bpk.sim.log_perintah)
with _hening(_io.StringIO()):
    jpk.parkir()
cek("parkir() berakhir tepat di titik parkir",
    (bpk.x, bpk.y, bpk.z, bpk.e) == jpk.parkir_titik,
    f"{bpk.x},{bpk.y},{bpk.z},{bpk.e}")

# URUTAN PENTING: X/Y dulu baru Z, supaya tidak menyeruduk benda saat turun
_urut = [c for c in bpk.sim.log_perintah[n_awal:] if c.startswith("G0")]
_i_xy = next(i for i, c in enumerate(_urut) if "X" in c or "Y" in c)
_i_z = next(i for i, c in enumerate(_urut) if "Z" in c)
cek("parkir menggerakkan X/Y DULU, baru menurunkan Z",
    _i_xy < _i_z, str(_urut))

bsp = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bsp.home()
    bsp.move(z=-30, f=20)
jsp = jg.Jog(bsp)
lama_titik = jsp.parkir_titik
with _hening(_io.StringIO()):
    jsp.set_parkir()
cek("Shift+X menjadikan posisi sekarang sebagai titik parkir",
    jsp.parkir_titik == (bsp.x, bsp.y, bsp.z, bsp.e) and
    jsp.parkir_titik != lama_titik)

bmo = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bmo.home()
jmo = jg.Jog(bmo)
cek("setelah homing motor dianggap terkunci", jmo.motor_terkunci is True)
with _hening(_io.StringIO()):
    jmo.tangani("r")
cek("R mengirim M18", "M18" in bmo.sim.log_perintah)
cek("R menandai motor dirilis", jmo.motor_terkunci is False)
cek("R membuat posisi TIDAK dipercaya (lengan bisa digeser tangan)",
    bmo.position_known is False)
n_sblm = bmo.sim.jumlah_perintah
with _hening(_io.StringIO()):
    bergerak_stlh_rilis = jmo.gerak("x", +1, 1)
cek("setelah rilis, gerakan DITOLAK sampai dikalibrasi ulang",
    bergerak_stlh_rilis is False)
cek("dan tidak ada perintah gerak terkirim",
    bmo.sim.jumlah_perintah == n_sblm)
with _hening(_io.StringIO()):
    jmo.tangani("t")
cek("T mengirim M17", "M17" in bmo.sim.log_perintah)
cek("T menandai motor terkunci lagi", jmo.motor_terkunci is True)

# keluar setelah rilis: jangan parkir diam-diam, beri tahu apa adanya
bko = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bko.home()
    jko = jg.Jog(bko)
    jko.tangani("r")
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    jko.bersih_bersih()
cek("keluar setelah rilis: TIDAK parkir diam-diam, dijelaskan ke pengguna",
    "TIDAK parkir" in _buf.getvalue() and "TANGAN" in _buf.getvalue())

# ---- REGRESI: deteksi tahan-tombol tidak boleh patah oleh lamanya gerakan ----
# Bug yang pernah terjadi: ambang "ditahan" diukur antar PENEKANAN, padahal
# penekanan menumpuk di buffer SELAMA robot bergerak. Potongan 5 mm di F14.5
# makan 0.36 s -- lebih lama dari ambang -- jadi tiap selesai gerak dikira
# tombol dilepas, dan tangga akselerasi tidak pernah naik. Gejalanya potongan
# selang-seling 0.5/5.2 mm, yang justru BIKIN menghentak.
bhd = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bhd.home()
jhd = jg.Jog(bhd)
_urut = []
for _ in range(8):
    _st, _sp, _tahan = jhd.step_efektif("d", ulang=1)
    _urut.append((_st, _tahan))
    _t.sleep(min(_st / _sp, 0.45))        # tiru lamanya gerakan asli
    jhd._selesai_terakhir = _t.monotonic()

cek("penekanan pertama = step manual", _urut[0][1] is False)
cek("sisanya SEMUA terdeteksi ditahan meski tiap gerakan makan ratusan ms",
    all(t for _, t in _urut[1:]), str([t for _, t in _urut]))
cek("tangga NAIK sampai sapuan maks (bukan selang-seling seperti bug lama)",
    _urut[-1][0] == jhd.sapuan, str([st for st, _ in _urut]))
cek("potongan tidak pernah mengecil lagi setelah naik",
    all(_urut[i][0] <= _urut[i + 1][0] for i in range(1, len(_urut) - 1)),
    str([st for st, _ in _urut]))

jhd._selesai_terakhir = _t.monotonic() - 2.0      # tiru tombol dilepas lama
_st_lepas, _, _tahan_lepas = jhd.step_efektif("d", ulang=1)
cek("setelah dilepas, kembali ke step manual",
    _tahan_lepas is False and _st_lepas == jhd.step)

# sinyal kedua: penekanan menumpuk (ulang>1) langsung dianggap ditahan
bhu = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bhu.home()
jhu = jg.Jog(bhu)
jhu.step_efektif("d", ulang=1)                    # penekanan pertama
jhu._selesai_terakhir = _t.monotonic() - 5.0      # jeda panjang
_, _, _tahan_ulang = jhu.step_efektif("d", ulang=4)
cek("ulang>1 (penekanan menumpuk) tetap dianggap ditahan walau jeda panjang",
    _tahan_ulang is True)

# ---- peringatan mendekati batas + melandai berhenti ----
cek("jarak_tersisa() di titik valid > 0",
    jg.jarak_tersisa(0, 240, 0, 0, "y", +1) > 0)
cek("jarak_tersisa() BERBEDA tiap arah (ruang kerja bukan kotak)",
    len({jg.jarak_tersisa(0, 240, 0, 0, sb, ar)
         for sb, ar in (("x", 1), ("y", 1), ("z", 1), ("z", -1))}) > 1)
cek("rel di E=0 -> sisa ke kiri NOL",
    jg.jarak_tersisa(0, 240, 0, 0.0, "e", -1) == 0.0)
cek("rel di E=0 -> sisa ke kanan = panjang rel",
    abs(jg.jarak_tersisa(0, 240, 0, 0.0, "e", +1) - rnv3.RAIL_LENGTH) < 1.0)

# berjalan ke arah batas: sisa harus MENGECIL terus
_sisa = []
_y = 240.0
for _ in range(6):
    _sisa.append(jg.jarak_tersisa(0, _y, 0, 0, "y", +1))
    _y += 10
cek("sisa mengecil saat mendekati batas",
    all(_sisa[i] > _sisa[i + 1] for i in range(len(_sisa) - 1)), str(_sisa))

# melandai: potongan dipendekkan supaya mendarat tepat di tepi
bbt = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bbt.home()
    bbt.move(z=0, f=20)
jbt = jg.Jog(bbt)
jbt.kontinu = False
jbt.i_step = 4                      # 25 mm, lebih besar dari sisa nanti
with _hening(_io.StringIO()):
    for _ in range(40):
        if not jbt.gerak("y", +1, 1):
            break
sisa_akhir = jg.jarak_tersisa(bbt.x, bbt.y, bbt.z, bbt.e, "y", +1)
cek("berhenti TEPAT di tepi jangkauan (sisa < 0.5 mm)",
    sisa_akhir < 0.5, f"sisa {sisa_akhir}")
cek("titik akhir masih VALID (tidak menembus batas)",
    rnv3.check_reach(bbt.x, bbt.y, bbt.z, bbt.e)[0])
cek("pesan terakhir menyatakan MENTOK dengan nama arahnya",
    "MENTOK" in jbt.pesan and "Y+" in jbt.pesan, jbt.pesan)

# peringatan muncul sebelum mentok, bukan setelah
bwr = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bwr.home()
    bwr.move(z=0, f=20)
jwr = jg.Jog(bwr)
jwr.kontinu = False
jwr.i_step = 1                      # 1 mm
_peringatan = []
with _hening(_io.StringIO()):
    for _ in range(200):
        if not jwr.gerak("y", +1, 1):
            break
        if ">>>" in jwr.pesan or "MENTOK" in jwr.pesan:
            _peringatan.append(jwr.pesan)
cek("ada peringatan DULU sebelum benar-benar mentok",
    len(_peringatan) > 5 and ">>>" in _peringatan[0],
    f"{len(_peringatan)} peringatan")
cek("peringatan menyebutkan sisa jarak dalam mm",
    "sisa" in _peringatan[0] and "mm" in _peringatan[0], _peringatan[0])

# status line menampilkan arah + sisa
cek("baris status memuat arah terakhir dan sisanya",
    "Y+" in jwr.status(), jwr.status()[-40:])

print()
print("=" * 68)
print("13. batas FISIK -- yang tidak dicek firmware (lengan beradu badan)")
print("=" * 68)

cek("sudut_sendi() cocok dengan rumus robotGeometry.cpp di posisi home",
    rnv3.sudut_sendi(0, 217, 138) is not None and
    abs(rnv3.sudut_sendi(0, 217, 138)[0]) < 0.01)

# INTI MASALAHNYA: asin() tidak bisa membedakan depan dan belakang
_dpn = rnv3.sudut_sendi(0, 150, 0)
_blk = rnv3.sudut_sendi(0, -150, 0)
cek("titik DEPAN dan BELAKANG menghasilkan sudut sendi IDENTIK (celah firmware)",
    _dpn is not None and _blk is not None and
    all(abs(a - b) < 0.01 for a, b in zip(_dpn, _blk)),
    f"{_dpn} vs {_blk}")

cek("check_reach (replika firmware) MENERIMA titik di belakang robot",
    rnv3.check_reach(0.0, -150.0, 138.0, 0.0)[0] is True)
cek("check_fisik MENOLAKnya", rnv3.check_fisik(0.0, -150.0, 138.0, 0.0)[0] is False)
cek("alasannya menyebut beradu dengan badan",
    "beradu" in rnv3.check_fisik(0.0, -150.0, 138.0, 0.0)[1])
cek("check_aman = gabungan keduanya",
    rnv3.check_aman(0.0, -150.0, 138.0, 0.0)[0] is False and
    rnv3.check_aman(0.0, 217.0, 138.0, 0.0)[0] is True)

cek("Y di bawah y_min ditolak",
    rnv3.check_fisik(0.0, rnv3.BATAS_FISIK["y_min"] - 10, 138.0, 0.0)[0] is False)
cek("Y tepat di atas y_min diterima",
    rnv3.check_fisik(0.0, rnv3.BATAS_FISIK["y_min"] + 10, 138.0, 0.0)[0] is True)

# putar base di luar rentang mekanis
_lama_rot = rnv3.BATAS_FISIK["rot_maks"]
rnv3.BATAS_FISIK["rot_maks"] = 30.0
cek("putar base melebihi rot_maks ditolak",
    rnv3.check_fisik(200.0, 150.0, 0.0, 0.0)[0] is False)
cek("alasannya menyebut putar base",
    "putar base" in rnv3.check_fisik(200.0, 150.0, 0.0, 0.0)[1])
rnv3.BATAS_FISIK["rot_maks"] = _lama_rot

# move() benar-benar memakai batas fisik
bfs = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bfs.home()
_n = bfs.sim.jumlah_perintah
try:
    with _hening(_io.StringIO()):
        bfs.move(y=-150.0, f=20)
    cek("move() menolak titik di belakang robot", False, "malah diterima")
except rnv3.RNV3Error:
    cek("move() menolak titik di belakang robot", True)
cek("dan tidak mengirim apa pun ke robot", bfs.sim.jumlah_perintah == _n)

# jarak_tersisa ikut jujur
_sisa_blk = jg.jarak_tersisa(0.0, 211.2, 138.0, 0.0, "y", -1)
cek("jarak_tersisa ke arah badan robot TIDAK lagi melaporkan ruang palsu",
    _sisa_blk < 150.0, f"{_sisa_blk} mm")
cek("dan berhenti di sekitar y_min",
    abs((211.2 - _sisa_blk) - rnv3.BATAS_FISIK["y_min"]) < 1.0,
    f"berhenti di Y={211.2 - _sisa_blk:.1f}")

# pemindaian 0.5 mm tidak melompati daerah terlarang tipis
cek("pemindai tidak melompati garis Y=0 yang setipis satu titik",
    jg.jarak_tersisa(0.0, 50.0, 138.0, 0.0, "y", -1) < 50.0)

# rekam batas lalu simpan/muat -- SEMUA lewat berkas sementara
import tempfile as _tmp, os as _os
_berkas = _os.path.join(_tmp.gettempdir(), "uji-batas-rnv3.json")
_asli = dict(rnv3.BATAS_FISIK)
brk = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    brk.home()
    brk.move(z=0, f=20)
    brk.move(y=180, f=20)
jrk = jg.Jog(brk)
# Arahkan penyimpanan ke berkas sementara: rekam_batas() menulis sungguhan,
# dan kalau mengenai berkas default, run berikutnya akan memuatnya saat impor
# lalu mencemari tes lain.
_berkas_asli = rnv3.BERKAS_BATAS
rnv3.BERKAS_BATAS = _berkas
with _hening(_io.StringIO()):
    jrk.gerak("y", -1, 1)          # tetapkan arah terakhir
    jrk.rekam_batas()
rnv3.BERKAS_BATAS = _berkas_asli
cek("; merekam batas untuk arah terakhir dijog",
    rnv3.BATAS_FISIK.get("y_min") == round(brk.y, 1),
    f"y_min={rnv3.BATAS_FISIK.get('y_min')} vs Y={brk.y:.1f}")
rnv3.simpan_batas(_berkas)
rnv3.BATAS_FISIK.update(_asli)
cek("batas tersimpan dan bisa dimuat ulang (bertahan antar sesi)",
    rnv3.muat_batas(_berkas) and rnv3.BATAS_FISIK["y_min"] == round(brk.y, 1))
rnv3.BATAS_FISIK.clear(); rnv3.BATAS_FISIK.update(_asli)
try: _os.remove(_berkas)
except OSError: pass
cek("batas dipulihkan setelah pengujian",
    rnv3.BATAS_FISIK["y_min"] == _asli["y_min"])
try:
    with open(rnv3.BERKAS_BATAS, "rb") as _f:
        _isi_berkas_akhir = _f.read()
except OSError:
    _isi_berkas_akhir = None
cek("tes tidak MENGUBAH berkas kalibrasi sungguhan (isolasi)",
    _isi_berkas_akhir == _isi_berkas_awal,
    "berkas kalibrasi berubah / dibuat oleh pengujian")

# ---- matikan robot setelah parkir ----
cek("MATIKAN_SAAT_KELUAR mati secara default (lengan tetap terkunci saat ESC)",
    jg.MATIKAN_SAAT_KELUAR is False)
cek("pick_place juga TIDAK mematikan motor saat selesai (default)",
    pp.MATIKAN_SAAT_SELESAI is False)

# posisi tak diketahui -> JANGAN matikan (lengan bisa sedang tinggi)
bmt = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bmt.home()
    bmt.move(z=120, f=20)          # lengan di posisi TINGGI
    jmt = jg.Jog(bmt)
    jmt.tangani("r")               # rilis -> posisi tidak dipercaya
_n18 = [c.upper().replace(" ", "") for c in bmt.sim.log_perintah].count("M18")
with _hening(_io.StringIO()):
    jmt.bersih_bersih()
_n18_akhir = [c.upper().replace(" ", "") for c in bmt.sim.log_perintah].count("M18")
cek("posisi tak diketahui -> TIDAK mengirim M18 lagi saat keluar",
    _n18_akhir == _n18, f"{_n18} -> {_n18_akhir}")
cek("alasannya dijelaskan, bukan diam-diam",
    bmt.z == 120.0, f"lengan tetap di Z={bmt.z}")

# pick_place (default): parkir, motor TETAP terkunci
bpd = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    bpd.home()
    bpd.move(z=0, f=20)
    pp.parkir(bpd)
cek("pick_place.parkir() (default) TIDAK mengirim M18",
    "M18" not in [c.upper().replace(" ", "") for c in bpd.sim.log_perintah])
cek("pick_place.parkir() (default) motor tetap terkunci di Z_PARKIR",
    bpd.sim.steppers_on is True and bpd.z == pp.Z_PARKIR, f"Z={bpd.z}")

# pick_place, flag True: M18 setelah mendarat di Z_PARKIR
bpp = simulasi.robot_palsu()
_flag_pp = pp.MATIKAN_SAAT_SELESAI
pp.MATIKAN_SAAT_SELESAI = True
try:
    with _hening(_io.StringIO()):
        bpp.home()
        bpp.move(z=0, f=20)
        pp.parkir(bpp)
finally:
    pp.MATIKAN_SAAT_SELESAI = _flag_pp
cek("flag True: pick_place.parkir() mengirim M18 di akhir",
    "M18" in [c.upper().replace(" ", "") for c in bpp.sim.log_perintah])
cek("flag True: dan lengan berada di Z_PARKIR saat motor dimatikan",
    bpp.z == pp.Z_PARKIR, f"Z={bpp.z}")


# ===========================================================================
print()
print("=" * 68)
print("14. jalur.py — geometri benda yang digenggam")
print("=" * 68)

for _L in sorted(jalur.JENIS):
    _h = jalur.profil(_L)["tinggi"]
    cek(f"z_ambil({_L}) = permukaan belt + tinggi benda",
        abs(jalur.z_ambil(_L) - (jalur.Z_BELT + _h)) < 1e-9,
        f"{jalur.z_ambil(_L)}")
    cek(f"z_jelajah({_L}) tidak pernah di bawah Z_SAFE",
        jalur.z_jelajah(_L) >= jalur.Z_SAFE,
        f"{jalur.z_jelajah(_L)} vs {jalur.Z_SAFE}")
    cek(f"clearance {_L} saat jelajah >= JELAJAH_CLEARANCE",
        jalur.clearance_jelajah(_L) >= jalur.JELAJAH_CLEARANCE - 1e-9,
        f"{jalur.clearance_jelajah(_L):.1f} mm")
    cek(f"z_jelajah({_L}) masih di dalam Z_MAX robot",
        jalur.z_jelajah(_L) <= rnv3.Z_MAX,
        f"{jalur.z_jelajah(_L)} > {rnv3.Z_MAX}")

cek("validasi_konfigurasi() bersih dengan konfigurasi sekarang",
    jalur.validasi_konfigurasi(cetak=False) == [])

# Invarian clearance diuji untuk rentang tinggi yang luas, bukan cuma dua
# angka yang kebetulan dipakai hari ini.
_simpan_jenis = {k: dict(v) for k, v in jalur.JENIS.items()}
try:
    _rusak = []
    for _t in (0.0, 1.0, 50.0, 129.0, 130.0, 131.0, 200.0, 250.0):
        jalur.JENIS["_uji"] = {"tinggi": _t, "taruh": (-170.0, 215.0)}
        if jalur.clearance_jelajah("_uji") < jalur.JELAJAH_CLEARANCE - 1e-9:
            _rusak.append(_t)
    cek("invarian clearance berlaku untuk tinggi benda 0..250 mm",
        _rusak == [], f"gagal di tinggi {_rusak}")
finally:
    jalur.JENIS.clear(); jalur.JENIS.update(_simpan_jenis)
cek("JENIS dipulihkan setelah pengujian", "_uji" not in jalur.JENIS)

# Pemeriksa clearance di validasi_konfigurasi() adalah PENJAGA REGRESI:
# dengan rumus z_jelajah sekarang dia tidak bisa menyala (rumusnya sendiri
# menjamin clearance). Jadi yang diuji di sini: apakah dia MENYALA kalau
# z_jelajah dikembalikan ke perilaku lama yang cacat (konstanta Z_SAFE).
_zj_asli = jalur.z_jelajah
try:
    jalur.z_jelajah = lambda label: jalur.Z_SAFE
    _m = jalur.validasi_konfigurasi(cetak=False)
    cek("validasi MENANGKAP kalau tinggi jelajah dikembalikan ke konstanta",
        any("clearance" in x for x in _m), str(_m))
finally:
    jalur.z_jelajah = _zj_asli
cek("z_jelajah dipulihkan setelah pengujian", jalur.z_jelajah is _zj_asli)

cek("Z_SAFE jalur.py dan pick_place.py tidak melenceng",
    jalur.Z_SAFE == pp.Z_SAFE, f"{jalur.Z_SAFE} vs {pp.Z_SAFE}")


# ===========================================================================
print()
print("=" * 68)
print("15. rnv3.py — buffer serial tidak boleh basi setelah timeout")
print("=" * 68)

class _SerialUji:
    """Serial palsu: antrean baris + pencatat reset_input_buffer()."""

    def __init__(self, antre=()):
        self.antre = list(antre)
        self.ditulis = []
        self.dibersihkan = 0

    def write(self, b):
        self.ditulis.append(b)

    def readline(self):
        return self.antre.pop(0) if self.antre else b""

    def reset_input_buffer(self):
        self.dibersihkan += 1
        self.antre.clear()


def _bot_uji(antre=()):
    b = rnv3.RNV3.__new__(rnv3.RNV3)
    b.ser = _SerialUji(antre)
    b.verbose = False
    b.events_sensor = []
    return b

# Mekanisme desync-nya: 'ok' yang nyangkut di buffer DITERIMA sebagai balasan
# perintah berikutnya. Ini yang membuat pergeseran satu-perintah tidak terlihat.
_b = _bot_uji([b"ok\r\n"])
cek("'ok' basi di buffer memang diterima sebagai balasan (sumber desync)",
    _b.send("M114", timeout=0.5) == [])

# Perbaikannya: timeout WAJIB membersihkan buffer, supaya sisa balasan
# perintah lama tidak dipanggil balasan perintah baru.
_b = _bot_uji()
try:
    _b.send("G0 X0", timeout=0.05)
    _sisa = "tidak melempar"
except rnv3.RNV3Error as _x:
    _sisa = str(_x)
cek("send() timeout -> RNV3Error", "timeout" in _sisa, _sisa)
cek("dan buffer masuk DIBERSIHKAN tepat sekali",
    _b.ser.dibersihkan == 1, f"{_b.ser.dibersihkan}x")

_b2 = _bot_uji([b"SENSOR STATUS: [1 0 1]\r\n"])
try:
    _b2.send("M105", timeout=0.05)
except rnv3.RNV3Error:
    pass
cek("sisa balasan yang belum terbaca ikut terbuang saat timeout",
    _b2.ser.antre == [], str(_b2.ser.antre))
cek("helper _timeout_error() ada (bukan cuma komentar seperti sebelumnya)",
    callable(getattr(rnv3.RNV3, "_timeout_error", None)))



# ===========================================================================
print()
print("=" * 68)
print("16. kontrak_kamera.py — kontrak kamera & jembatan piksel->mm")
print("=" * 68)

# Peta piksel->mm yang SUDAH DIKETAHUI: mm_x = 0.5u - 160, mm_y = -0.5v + 360
def _mm_asli(u, v):
    return (0.5 * u - 160.0, -0.5 * v + 360.0)

_pasangan = [((u, v), _mm_asli(u, v)) for u, v in
             ((100, 100), (540, 100), (540, 400), (100, 400), (320, 250), (200, 300))]
_kal = kalibrasi.Kalibrasi.dari_titik(_pasangan, catatan="cek.py")

cek("solver kalibrasi menemukan kembali homografi yang diketahui",
    _kal.galat_maks(_pasangan) < 1e-6, f"galat maks {_kal.galat_maks(_pasangan):.2e}")

_d_px = kontrak_kamera.Deteksi(u_px=300.0, v_px=200.0, label="koin")
cek("Deteksi berisi piksel saja -> punya_mm False", not _d_px.punya_mm)
_d_mm = _d_px.ke_mm(_kal)
_want = _mm_asli(300.0, 200.0)
cek("Deteksi.ke_mm() mengubah piksel jadi milimeter", _d_mm.punya_mm)
cek("dan nilainya benar terhadap peta yang diketahui",
    abs(_d_mm.x_mm - _want[0]) < 1e-6 and abs(_d_mm.y_mm - _want[1]) < 1e-6,
    f"({_d_mm.x_mm:.3f},{_d_mm.y_mm:.3f}) vs {_want}")

_d_sudah = kontrak_kamera.Deteksi(x_mm=10.0, y_mm=260.0, label="koin")
cek("Deteksi yang sudah milimeter -> punya_mm True", _d_sudah.punya_mm)

_bisa, _ditolak = kontrak_kamera.yang_terjangkau(
    [kontrak_kamera.Deteksi(x_mm=9999.0, y_mm=9999.0),
     kontrak_kamera.Deteksi(x_mm=0.0, y_mm=jalur.BELT_Y)], jalur.z_ambil("koin"))
cek("yang_terjangkau(): benda di luar jangkauan masuk daftar ditolak",
    len(_ditolak) == 1, f"{len(_ditolak)}")
cek("yang_terjangkau(): benda di zona ambil lolos", len(_bisa) == 1, f"{len(_bisa)}")
cek("dan alasan penolakannya dijelaskan, bukan diam-diam",
    "terlalu jauh" in _ditolak[0][1], str(_ditolak[0][1]))

# Deteksi yang masih piksel TIDAK BOLEH diam-diam dianggap milimeter.
try:
    kontrak_kamera.Deteksi(u_px=1.0, v_px=2.0).terjangkau(0.0)
    _px_jaga = "tidak melempar"
except kontrak_kamera.KameraError as _x:
    _px_jaga = str(_x)
cek("Deteksi piksel menolak dicek jangkauan tanpa kalibrasi",
    "piksel" in _px_jaga, _px_jaga)
_dekat = kontrak_kamera.paling_dekat(
    [kontrak_kamera.Deteksi(x_mm=120.0, y_mm=260.0), kontrak_kamera.Deteksi(x_mm=5.0, y_mm=260.0)],
    x=0.0, y=260.0)
cek("paling_dekat() memilih yang terdekat", abs(_dekat.x_mm - 5.0) < 1e-9)

# Penjaga regresi untuk penyambungan kalibrasi: sebelum ini kalibrasi.py
# adalah kode mati dan jalur.py tidak punya cara memakainya sama sekali.
import inspect as _insp
cek("jalur.rakit() menerima kalibrasi_file (jembatan tersambung)",
    "kalibrasi_file" in _insp.signature(jalur.rakit).parameters)
cek("kontrak_kamera.SumberTCP menerima kalibrasi",
    "kalibrasi" in _insp.signature(kontrak_kamera.SumberTCP.__init__).parameters)
cek("jalur.py benar-benar mengimpor kalibrasi",
    "kalibrasi" in [m.__name__ for m in (kalibrasi,)] and
    getattr(jalur, "mod_kalibrasi", None) is kalibrasi)


# ===========================================================================
print()
print("=" * 68)
print("17. konveyor.py & esp.py — lapisan belt")
print("=" * 68)

_bot_belt = simulasi.robot_palsu()
_belt_lg = konveyor.KonveyorLG(_bot_belt, channel=jalur.LG_BELT,
                               sensor=jalur.SENSOR_BELT, settle=0.0, verbose=False)
try:
    _belt_lg.jalan(); _belt_lg.berhenti(); _belt_lg.detak()
    _lg_ok, _lg_err = True, ""
except Exception as _x:
    _lg_ok, _lg_err = False, f"{type(_x).__name__}: {_x}"
cek("KonveyorLG: jalan/berhenti/detak tidak melempar", _lg_ok, _lg_err)
cek("KonveyorLG.ada_benda() mengembalikan bool",
    isinstance(_belt_lg.ada_benda(), bool))

_e = mod_esp.esp_palsu()
try:
    _e.jalan(); _e.berhenti(); _e.detak(); _e.ping()
    _esp_ok, _esp_err = True, ""
except Exception as _x:
    _esp_ok, _esp_err = False, f"{type(_x).__name__}: {_x}"
cek("esp_palsu(): jalan/berhenti/detak/ping tidak melempar", _esp_ok, _esp_err)

_belt_esp = konveyor.KonveyorESP(_e, settle=0.0, verbose=False)
try:
    _belt_esp.jalan(); _belt_esp.berhenti(); _belt_esp.detak()
    _be_ok, _be_err = True, ""
except Exception as _x:
    _be_ok, _be_err = False, f"{type(_x).__name__}: {_x}"
cek("KonveyorESP: jalan/berhenti/detak tidak melempar", _be_ok, _be_err)
cek("KonveyorESP.ada_benda() mengembalikan bool",
    isinstance(_belt_esp.ada_benda(), bool))
try:
    _e.close()
except Exception:
    pass


# ===========================================================================
print()
print("=" * 68)
print("18. simulasi.py DIUJI LAWAN SOURCE FIRMWARE, bukan lawan dirinya")
print("=" * 68)
# cek.py memvalidasi banyak hal terhadap simulasi.py -- tapi simulasi.py
# ditulis dari BACAAN firmware oleh penulis yang sama, jadi tes semacam itu
# sebagian sirkular. Bagian ini memutus lingkaran itu: tabel alias dibaca
# langsung dari firmware/v2_02/command.cpp dan dibandingkan.

_fw = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "firmware", "v2_02", "command.cpp")
cek("source firmware command.cpp bisa dibaca", os.path.exists(_fw), _fw)

if os.path.exists(_fw):
    with open(_fw, encoding="utf-8", errors="replace") as _f:
        _isi = _f.read()
    _alias_fw = {}
    for _teks, _kode_m in _re.findall(
            r'equalsIgnoreCase\(\s*"([^"]+)"\s*\)\s*\)\s*\{\s*'
            r'msg\s*=\s*"([^"]+)"', _isi):
        _alias_fw[_re.sub(r"\s+", "", _teks.upper())] = _kode_m.upper()
    cek("tabel alias firmware berhasil diurai (8 pasangan)",
        len(_alias_fw) == 8, f"{len(_alias_fw)}: {sorted(_alias_fw)}")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "simulasi.py"), encoding="utf-8") as _f:
        _src_sim = _f.read()
    _m_sim = _re.search(r"alias\s*=\s*\{(.*?)\}", _src_sim, _re.S)
    cek("tabel alias simulasi.py ditemukan", _m_sim is not None)
    if _m_sim:
        import ast as _ast
        _alias_sim = {k.upper(): v.upper() for k, v in
                      _ast.literal_eval("{" + _m_sim.group(1) + "}").items()}
        for _t in sorted(_alias_fw):
            cek(f"alias {_t} -> {_alias_fw[_t]} sama dengan firmware",
                _alias_sim.get(_t) == _alias_fw[_t],
                f"simulasi bilang {_alias_sim.get(_t)}")
        cek("simulasi.py tidak menambah alias yang tidak ada di firmware",
            set(_alias_sim) <= set(_alias_fw),
            str(sorted(set(_alias_sim) - set(_alias_fw))))


# ===========================================================================
print()
print("=" * 68)
print("19. REGRESI review 21 Okt -- stepper mati, siklus macet, parkir")
print("=" * 68)

# ---- G0 dengan stepper mati: firmware menjawab 'ok', lengan diam ----
# Sumbernya robotRNV3_v2_02.ino: case 0/1 cuma fan.enable(true); stepper
# hanya dinyalakan Endstop::home() (G28) atau M17.
_fwino = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "firmware", "v2_02", "robotRNV3_v2_02.ino")
with open(_fwino, encoding="utf-8", errors="replace") as _f:
    _src_ino = _f.read()
_blok_g0 = _re.search(r"case 0:\s*case 1:(.*?)case 4:", _src_ino, _re.S)
cek("firmware: blok G0 tidak menyalakan stepper (dasar perbaikan ini)",
    _blok_g0 is not None and "setStepperEnable" not in _blok_g0.group(1)
    and "fan.enable(true)" in _blok_g0.group(1))

_bm = simulasi.robot_palsu()
cek("robot baru tersambung -> butuh_home (stepper mati saat boot)",
    _bm.butuh_home is True)
_n = len(_bm.sim.log_perintah)
try:
    _bm.move(z=100, f=20)
    _pesan = "diterima"
except rnv3.RNV3Error as _x:
    _pesan = str(_x)
cek("move() sebelum homing DITOLAK", "home()" in _pesan, _pesan)
cek("dan tidak ada G0 terkirim", len(_bm.sim.log_perintah) == _n)

with _hening(_io.StringIO()):
    _bm.home()
cek("home() -> butuh_home False & stepper simulator menyala",
    _bm.butuh_home is False and _bm.sim.steppers_on is True)

with _hening(_io.StringIO()):
    _bm.steppers(False)
cek("steppers(False) / M18 -> butuh_home True", _bm.butuh_home is True)
_n = len(_bm.sim.log_perintah)
try:
    _bm.move(z=50, f=20)
    _pesan = "diterima"
except rnv3.RNV3Error as _x:
    _pesan = str(_x)
cek("move() setelah M18 DITOLAK (dulu diterima, lengan diam)",
    "stepper mati" in _pesan, _pesan)
cek("dan tidak ada G0 terkirim", len(_bm.sim.log_perintah) == _n)
_bm.position()
cek("M114 TIDAK memulihkan (cuma melapor anggapan firmware)",
    _bm.butuh_home is True)
_bm.steppers(True)
cek("M17 TIDAK memulihkan (lengan sudah loyo, posisi tak dipercaya)",
    _bm.butuh_home is True)

# Simulator sekarang meniru firmware: G0 tanpa motor dihitung, bukan diam.
_bm.send("M18")
_bm.send("G0 Z120")
cek("simulator mencatat G0 yang dikirim saat stepper mati",
    _bm.sim.gerak_tanpa_motor == 1, f"{_bm.sim.gerak_tanpa_motor}")

# pick_place interaktif: 'k' (parkir + M18) lalu pick -> dulu "berhasil".
# Keadaan "motor sudah mati" sekarang cuma terjadi kalau flag dinyalakan.
_bk = simulasi.robot_palsu()
_flag_pp = pp.MATIKAN_SAAT_SELESAI
pp.MATIKAN_SAAT_SELESAI = True
try:
    with _hening(_io.StringIO()):
        _bk.home()
        pp.parkir(_bk)
    try:
        with _hening(_io.StringIO()):
            pp.pick(_bk, 1)
        _pesan = "diterima"
    except rnv3.RNV3Error as _x:
        _pesan = str(_x)
    cek("flag True: pick setelah parkir+M18 DITOLAK, tidak diam-diam",
        "stepper mati" in _pesan, _pesan)
    _buf = _io.StringIO()
    try:
        with _ctx.redirect_stdout(_buf):
            pp.parkir(_bk)                # main() memanggil parkir lagi di akhir
        _pesan = ""
    except rnv3.RNV3Error as _x:
        _pesan = str(_x)
finally:
    pp.MATIKAN_SAAT_SELESAI = _flag_pp
cek("parkir() kedua kali tidak gagal, menjelaskan motor sudah mati",
    _pesan == "" and "sudah mati" in _buf.getvalue(), _pesan)
with _hening(_io.StringIO()):
    _bk.home()
    pp.pick(_bk, 1)
cek("setelah kalibrasi ulang, pick jalan lagi", _bk.sim.vacuum_on is True)
cek("sepanjang itu tidak ada G0 yang terkirim tanpa motor",
    _bk.sim.gerak_tanpa_motor == 0, f"{_bk.sim.gerak_tanpa_motor}")

# pick_place --gui: dulu `return` sebelum parkir()
_tangkap = []
_gui_asli = pp.jalankan_gui
pp.jalankan_gui = lambda bot: (_tangkap.append(bot), 0)[1]
try:
    with _hening(_io.StringIO()):
        _kode_gui = pp.main(["--sim", "--gui", "--diam"])
finally:
    pp.jalankan_gui = _gui_asli
cek("--gui: setelah jendela ditutup lengan TETAP diparkir (motor terkunci)",
    _kode_gui == 0 and _tangkap and _tangkap[0].z == pp.Z_PARKIR
    and "M18" not in [c.upper().replace(" ", "") for c in _tangkap[0].sim.log_perintah],
    f"kode={_kode_gui}")

# jog: C setelah R mengembalikan status motor
_bj = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    _bj.home()
    _jj = jg.Jog(_bj)
    _jj.tangani("r")
    _jj.tangani("c")
cek("jog: C setelah R -> motor_terkunci True dan bisa gerak lagi",
    _jj.motor_terkunci is True and _jj.gerak("x", +1, 1) is True)

# ---- label kamera ----
cek("JENIS memakai label kamera 'tutup_botol', bukan 'botol'",
    "tutup_botol" in jalur.JENIS and "botol" not in jalur.JENIS,
    str(sorted(jalur.JENIS)))
cek("'tutup_botol' tidak jatuh ke JENIS_DEFAULT",
    jalur.profil("tutup_botol") is jalur.JENIS["tutup_botol"])

# ---- validasi sama ketatnya dengan move() ----
_d60 = kontrak_kamera.Deteksi(x_mm=-250.0, y_mm=60.0, label="koin")
cek("titik uji (-250, 60) lolos batas FIRMWARE ...",
    rnv3.check_reach(-250.0, 60.0, jalur.z_ambil("koin"))[0] is True)
cek("... tapi Deteksi.terjangkau() menolaknya, sama seperti move()",
    _d60.terjangkau(jalur.z_ambil("koin"))[0] is False)

_simpan_jenis = {k: dict(v) for k, v in jalur.JENIS.items()}
try:
    jalur.JENIS["koin"]["taruh"] = (-250.0, 60.0)
    _m = jalur.validasi_konfigurasi(cetak=False)
finally:
    jalur.JENIS.clear(); jalur.JENIS.update(_simpan_jenis)
cek("validasi_konfigurasi() menolak bak yang melanggar batas FISIK",
    any("batas fisik" in x for x in _m), str(_m))

_rot = rnv3.BATAS_FISIK["rot_maks"]
try:
    rnv3.BATAS_FISIK["rot_maks"] = 20.0       # slot ujung grid ~28 derajat
    _ok_grid, _ = pp.validasi_grid(cetak=False)
finally:
    rnv3.BATAS_FISIK["rot_maks"] = _rot
cek("validasi_grid() menolak grid yang melanggar batas FISIK",
    _ok_grid is False)
cek("batas fisik dipulihkan", rnv3.BATAS_FISIK == BATAS_BAWAAN)


# ---- jalur.py: siklus tidak boleh macet di benda yang sama ----
class _KameraTetap(kontrak_kamera.SumberDeteksi):
    """Selalu melihat benda yang sama, persis benda yang tidak pernah pergi."""

    def __init__(self, d):
        self.d, self.n = d, 0

    def deteksi(self):
        self.n += 1
        return [self.d]


def _jalur_uji(kamera=None):
    _j, _tutup = jalur.rakit(sim=True, verbose=False, seed=7)
    if kamera is not None:
        _j.kamera = kamera
    with _hening(_io.StringIO()):
        _j.siap()
    return _j


def _n_belt(j):
    return sum(1 for c in j.bot.sim.log_perintah if c.upper() == "LG1 ON")


# Skenario yang dulu berputar selamanya: lolos saringan lama, ditolak move().
_jt = _jalur_uji(_KameraTetap(_d60))
with _hening(_io.StringIO()):
    _st = _jt.jalankan(siklus=3)
cek("benda melanggar batas fisik -> 'di luar jangkauan', bukan robot error",
    _st == {jalur.Hasil.DILUAR_JANGKAUAN: 3}, str(_st))
cek("dan bendanya DIDORONG lewat tiap siklus (belt jalan 2x per siklus)",
    _n_belt(_jt) == 6, f"belt nyala {_n_belt(_jt)}x")


def _gagal_sekali(j, ke, posisi_hilang=False):
    """Buat goto_xy ke-`ke` gagal sekali. posisi_hilang meniru firmware
    yang membatalkan gerakan di tengah jalan."""
    asli, hit = j.bot.goto_xy, [0]

    def palsu(*a, **kw):
        hit[0] += 1
        if hit[0] == ke:
            if posisi_hilang:
                j.bot.position_known = False
            raise rnv3.RNV3Error("uji: gerakan ditolak")
        return asli(*a, **kw)
    j.bot.goto_xy = palsu


# Ditolak sebelum dikirim, benda belum terangkat -> dorong lewat, lanjut.
_jg = _jalur_uji()
_gagal_sekali(_jg, ke=1)
with _hening(_io.StringIO()):
    _st = _jg.jalankan(siklus=3)
cek("gerakan ditolak sebelum mengambil -> siklus itu dilewatkan, lini lanjut",
    _st.get(jalur.Hasil.ROBOT_ERROR) == 1 and _st.get(jalur.Hasil.SUKSES) == 2,
    str(_st))
cek("vakum mati dan lengan di Z aman sebelum belt didorong",
    any(c.upper().replace(" ", "") == "VACUMOFF"
        for c in _jg.bot.sim.log_perintah))

# Firmware membatalkan di tengah jalan -> posisi hilang -> HENTIKAN.
_jh = _jalur_uji()
_gagal_sekali(_jh, ke=1, posisi_hilang=True)
with _hening(_io.StringIO()):
    _st = _jh.jalankan(siklus=5)
cek("posisi hilang -> lini BERHENTI setelah 1 siklus, tidak mengulang",
    _st == {jalur.Hasil.ROBOT_ERROR: 1}, str(_st))
cek("dan tidak parkir dengan posisi yang tidak diketahui",
    _jh.snapshot()["state"] == "berhenti")

# Gagal saat benda sedang digenggam (goto_xy ke-2 = menuju bak) -> HENTIKAN.
_jd = _jalur_uji()
_gagal_sekali(_jd, ke=2)
with _hening(_io.StringIO()):
    _st = _jd.jalankan(siklus=5)
cek("gagal saat menggenggam benda -> lini BERHENTI, tidak lanjut siklus",
    _st == {jalur.Hasil.ROBOT_ERROR: 1}, str(_st))

# ---- jalur.py: parkir di PARKIR_XY, bukan di atas bak ----
_jp = _jalur_uji()
with _hening(_io.StringIO()):
    _st = _jp.jalankan(siklus=6)
cek("simulasi 6 siklus sukses semua, kedua jenis benda kena",
    _st == {jalur.Hasil.SUKSES: 6} and all(_jp.hitungan.values()),
    f"{_st} {_jp.hitungan}")
cek("selesai() berakhir di PARKIR_XY + Z_PARKIR",
    (round(_jp.bot.x, 2), round(_jp.bot.y, 2), _jp.bot.z)
    == (*jalur.PARKIR_XY, jalur.Z_PARKIR),
    f"X{_jp.bot.x} Y{_jp.bot.y} Z{_jp.bot.z}")
_g0 = [c for c in _jp.bot.sim.log_perintah if c.startswith("G0")]
cek("urutan parkir: geser XY dulu (tanpa Z), baru turun Z saja",
    "Z" not in _g0[-2] and "X" in _g0[-2] and _g0[-1].split()[1].startswith("Z"),
    str(_g0[-2:]))
cek("seluruh run tanpa satu pun G0 saat stepper mati",
    _jp.bot.sim.gerak_tanpa_motor == 0 and _jp.bot.sim.jumlah_limit == 0)

# ---- esp.py: buffer dibersihkan saat timeout ----
_eu = mod_esp.ESP("UJI", ser=_SerialUji([b"OK\n"]), tunggu_boot=False)
cek("'OK' basi di buffer ESP memang diterima sebagai balasan (sumber desync)",
    _eu.send("STATUS", timeout=0.05) == "")
_eu = mod_esp.ESP("UJI", ser=_SerialUji(), tunggu_boot=False)
try:
    _eu.send("PING", timeout=0.05)
    _pesan = "tidak melempar"
except mod_esp.ESPError as _x:
    _pesan = str(_x)
cek("ESP.send() timeout -> ESPError", "timeout" in _pesan, _pesan)
cek("dan buffer masuk ESP DIBERSIHKAN", _eu.ser.dibersihkan == 1,
    f"{_eu.ser.dibersihkan}x")


# ===========================================================================
print()
print("=" * 68)
print("20. jog.py -- SATU baris status, cuma angkanya yang berganti")
print("=" * 68)

_bs = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    _bs.home()
_js = jg.Jog(_bs)
with _hening(_io.StringIO()):
    _js.gerak("y", +1, 1)
_s = _js.status()
cek("baris status diawali koordinat", _s.startswith("Posisi lengan  X"), _s)
cek("setelan (step/sapuan/profil) tidak ikut di tiap gerakan",
    "step" not in _s and "sapuan" not in _s and "kosinus" not in _s, _s)
cek("setelan tetap bisa dilihat lewat setelan()",
    "step" in _js.setelan() and "profil" in _js.setelan())

# Terminal sempit: baris tidak boleh melebihi lebar (kalau terlipat, '\r'
# gagal menimpa dan setiap gerakan tampak jadi baris baru).
_lebar_asli = jg.lebar_terminal
jg.lebar_terminal = lambda: 59
try:
    _js.pesan = "pesan yang sengaja dibuat panjang sekali " * 3
    _buf = _io.StringIO()
    with _ctx.redirect_stdout(_buf):
        _js.cetak_status(di_tempat=True)
        _js.cetak_status(di_tempat=True)
finally:
    jg.lebar_terminal = _lebar_asli
_cetak = _buf.getvalue()
_potong = [p for p in _cetak.split("\r") if p]
cek("cetak di tempat tidak pernah membuat baris baru", "\n" not in _cetak,
    repr(_cetak[:80]))
cek("tiap tulisan pas selebar terminal (59), tidak terlipat",
    _potong and all(len(p) == 59 for p in _potong),
    str([len(p) for p in _potong]))


def _jalankan_jog(*arg):
    _buf = _io.StringIO()
    with _ctx.redirect_stdout(_buf):
        _kode = jg.main(["--sim", "--tanpa-tanya", "--urutan", "w w w q", *arg])
    _isi = _buf.getvalue()
    # Bagian setelah homing: di sinilah jogging terjadi.
    return _kode, _isi[_isi.index("selesai homing"):]


_kode, _isi = _jalankan_jog()
cek("jogging bawaan: lalu-lintas serial TIDAK tercetak di sela gerakan",
    _kode == 0 and ">> G0" not in _isi and "<< ok" not in _isi,
    f"kode={_kode}")
_kode, _isi = _jalankan_jog("--ramai")
cek("--ramai: lalu-lintas serial tetap tercetak untuk debug",
    _kode == 0 and ">> G0" in _isi and "<< ok" in _isi, f"kode={_kode}")


# ===========================================================================
print()
print("=" * 68)
print("21. setelan.py -- satu berkas angka hasil ukur untuk semua program")
print("=" * 68)
import json as _json
import tempfile as _tempfile
import warnings as _warnings

import setelan

cek("RNV3_SETELAN=- : tes tidak membaca berkas milik pengguna",
    setelan.berkas_aktif() is None)
cek("jalur/jog/pick_place memakai angka BAWAAN selama tes",
    jalur.Z_BELT == setelan.BAWAAN["z_belt"]
    and jalur.PARKIR_XY == tuple(setelan.BAWAAN["parkir"][:2])
    and jg.PARKIR[:3] == tuple(setelan.BAWAAN["parkir"])
    and pp.Z_PARKIR == setelan.BAWAAN["parkir"][2]
    and jalur.JENIS["koin"]["taruh"] == tuple(setelan.BAWAAN["bak"]["koin"]))
cek("BAWAAN sama dengan konstanta lama (perilaku tidak berubah sebelum diukur)",
    setelan.BAWAAN["parkir"] == [0.0, 217.0, -40.0]
    and setelan.BAWAAN["z_belt"] == -80.0
    and setelan.BAWAAN["bak"]["tutup_botol"] == [-170.0, 215.0])

_tmp_setelan = _tempfile.mkdtemp(prefix="cek-setelan-")
_berkas_s = os.path.join(_tmp_setelan, "s.json")
_isi = setelan.isi_dari_posisi(setelan.muat(_berkas_s), "parkir", 12.345, 230.0, -35.5,
                               waktu="2026-10-22 10:00")
setelan.simpan(_isi, _berkas_s)
_balik = setelan.muat(_berkas_s)
cek("simpan -> muat bolak-balik utuh",
    _balik["parkir"] == [12.35, 230.0, -35.5]
    and _balik["diukur"] == {"parkir": "2026-10-22 10:00"}
    and _balik["z_belt"] == setelan.BAWAAN["z_belt"], str(_balik))
cek("simpan atomik: tidak ada berkas .tmp tertinggal",
    not os.path.exists(_berkas_s + ".tmp"))

for _nama, _rusak in [("parkir 2 angka", {"parkir": [0, 217]}),
                      ("z_belt teks", {"z_belt": "-80"}),
                      ("z_bak boolean", {"z_bak": True}),
                      ("bak bukan objek", {"bak": [1, 2]})]:
    try:
        setelan.rapikan(_rusak)
        cek(f"setelan rusak ditolak: {_nama}", False)
    except ValueError:
        cek(f"setelan rusak ditolak: {_nama}", True)

with open(_berkas_s, "w", encoding="utf-8") as _f:
    _f.write("{ini bukan json")
with _warnings.catch_warnings(record=True) as _w:
    _warnings.simplefilter("always")
    _hasil_rusak = setelan.muat(_berkas_s)
cek("berkas rusak -> BAWAAN + peringatan, bukan crash",
    _hasil_rusak == setelan.BAWAAN and any("bawaan" in str(x.message) for x in _w))
cek("berkas tidak ada -> BAWAAN tanpa ribut",
    setelan.muat(os.path.join(_tmp_setelan, "tidak-ada.json")) == setelan.BAWAAN)

setelan.terapkan(setelan.rapikan({"z_belt": -70.0, "parkir": [50.0, 230.0, -30.0],
                                  "bak": {"koin": [-60.0, 290.0]}}))
cek("terapkan() memasang angka baru ke modul yang sudah diimpor",
    jalur.Z_BELT == -70.0 and jalur.PARKIR_XY == (50.0, 230.0)
    and jalur.Z_PARKIR == -30.0 and jalur.JENIS["koin"]["taruh"] == (-60.0, 290.0)
    and jg.PARKIR == (50.0, 230.0, -30.0, 0.0)
    and pp.PARKIR_XY == (50.0, 230.0) and pp.Z_PARKIR == -30.0)
_bps = simulasi.robot_palsu()
with _hening(_io.StringIO()):
    _bps.home()
    pp.parkir(_bps)
cek("pick_place.parkir() memakai XY parkir dari setelan, bukan HOME",
    (_bps.x, _bps.y, _bps.z) == (50.0, 230.0, -30.0), f"{_bps.x},{_bps.y},{_bps.z}")
setelan.terapkan(setelan.BAWAAN)
cek("terapkan(BAWAAN) memulihkan semua modul",
    jalur.Z_BELT == -80.0 and jalur.PARKIR_XY == (0.0, 217.0)
    and jg.PARKIR == (0.0, 217.0, -40.0, 0.0) and pp.PARKIR_XY == (0.0, 217.0))
cek("validasi_konfigurasi tetap bersih dengan BAWAAN",
    jalur.validasi_konfigurasi(cetak=False) == [])
try:
    setelan.simpan(setelan.BAWAAN)
    cek("simpan tanpa berkas saat RNV3_SETELAN=- ditolak", False)
except RuntimeError:
    cek("simpan tanpa berkas saat RNV3_SETELAN=- ditolak", True)
cek("berkas setelan milik pengguna tidak dibuat oleh tes",
    _ada_setelan_pengguna == os.path.exists(setelan.BERKAS_DEFAULT))


# ===========================================================================
print()
print("=" * 68)
print("22. aplikasi.py -- kontrol lewat browser (simulator)")
print("=" * 68)
import http.client as _http
import time as _waktu   # _t ditimpa jadi teks di bagian lain
import aplikasi


def _app_baru():
    d = _tempfile.mkdtemp(prefix="cek-aplikasi-")
    return aplikasi.Pengendali(
        buat_robot=lambda port, sim: simulasi.robot_palsu(),
        berkas_setelan=os.path.join(d, "setelan.json"),
        # jeda_sim: simulator menjawab seketika; tanpa jeda, "tahan 0,8 s"
        # menyapu ratusan mm dan tes jarak jadi tidak bermakna.
        berkas_titik=os.path.join(d, "titik.json"), jeda_sim=True), d


def _log(p):
    return " | ".join(b["teks"] for b in p.log.sejak(0))


def _tidur(detik):
    _waktu.sleep(detik)


app, _dir_app = _app_baru()
app.kirim("jog", {"sumbu": "y", "arah": 1, "token": "x"})
app.tunggu_selesai()
cek("jog sebelum tersambung ditolak dengan alasan", "belum tersambung" in _log(app))

app.kirim("sambung", {"sim": True})
app.tunggu_selesai()
_s = app.status()
cek("sambung: tersambung tapi wajib homing dulu",
    _s["tersambung"] and _s["perlu_homing"] and "PRA-TERBANG" in _log(app))
app.kirim("jog", {"sumbu": "y", "arah": 1, "token": "x"})
app.tunggu_selesai()
cek("jog sebelum homing ditolak", "homing dulu" in _log(app)
    and not any(c.startswith("G0") for c in app.bot.sim.log_perintah))

app.kirim("homing")
app.tunggu_selesai()
_s = app.status()
cek("homing: siap gerak, motor terkunci",
    not _s["perlu_homing"] and _s["motor_terkunci"] and _s["posisi_dipercaya"])
cek("status membawa peta jangkauan di Z sekarang",
    _s["peta"]["rmax"][len(_s["peta"]["sudut"]) // 2] is not None)

_y0 = app.bot.y
app.kirim("jog", {"sumbu": "y", "arah": 1, "token": "ketuk"})
app.kirim("jog_lepas", {"token": "ketuk"})
app.tunggu_selesai()
cek("ketuk = tepat satu langkah presisi (0,5 mm)",
    abs(app.bot.y - _y0 - jg.STEPS[0]) < 1e-6, f"{app.bot.y - _y0:.3f}")

# "lepas" menyalip "jog": tetap satu langkah walau detak terus datang
_y0 = app.bot.y
app.kirim("jog_lepas", {"token": "salip"})
app.kirim("jog", {"sumbu": "y", "arah": 1, "token": "salip"})
_akhir = _waktu.monotonic() + 0.7
while _waktu.monotonic() < _akhir:
    app.kirim("jog_detak", {"token": "salip"})
    _tidur(0.05)
app.tunggu_selesai()
cek("lepas yang tiba duluan tetap dihormati (cuma satu langkah)",
    abs(app.bot.y - _y0 - jg.STEPS[0]) < 1e-6, f"{app.bot.y - _y0:.3f}")

# tahan dengan detak -> sapuan; lepas -> berhenti
_z0 = app.bot.z
app.kirim("jog", {"sumbu": "z", "arah": -1, "token": "tahan"})
_akhir = _waktu.monotonic() + 0.8
while _waktu.monotonic() < _akhir:
    app.kirim("jog_detak", {"token": "tahan"})
    _tidur(0.05)
try:
    app.kirim("homing")
    cek("perintah lain ditolak selagi jog berjalan", False)
except aplikasi.AplikasiError:
    cek("perintah lain ditolak selagi jog berjalan", True)
app.kirim("jog_lepas", {"token": "tahan"})
app.tunggu_selesai()
_z1 = app.bot.z
_tidur(0.3)
cek("tahan = sapuan (lebih dari satu langkah)", _z0 - _z1 > 5.0, f"{_z0 - _z1:.1f}")
cek("setelah lepas tidak ada gerakan lagi", app.bot.z == _z1)

# tahan TANPA detak (tab ditutup / browser hang) -> berhenti sendiri
app.kirim("jog", {"sumbu": "z", "arah": -1, "token": "yatim"})
_ok_henti = app.tunggu_selesai(3.0)
_z2 = app.bot.z
_tidur(0.3)
cek("tanpa detak dari browser, jog berhenti sendiri", _ok_henti and app.bot.z == _z2,
    f"selesai={_ok_henti}")
cek("...dan tidak jauh (paling banyak satu sapuan + langkah)",
    _z1 - _z2 <= jg.SAPUAN_PILIHAN[-1] + jg.STEPS[0] + 1e-6, f"{_z1 - _z2:.1f}")

# berhenti di tengah tahan
app.kirim("jog", {"sumbu": "z", "arah": 1, "token": "henti"})
app.kirim("jog_detak", {"token": "henti"})
app.kirim("berhenti")
cek("tombol Berhenti menghentikan jog yang ditahan", app.tunggu_selesai(2.0))

# matikan butuh konfirmasi, dan ditolak SEBELUM masuk antrean
try:
    app.kirim("matikan", {})
    cek("matikan tanpa konfirmasi 'dipegang' ditolak langsung", False)
except aplikasi.AplikasiError:
    cek("matikan tanpa konfirmasi 'dipegang' ditolak langsung",
        app.bot.sim.steppers_on is True)

# selesai = home + motor tetap terkunci
app.kirim("selesai")
app.tunggu_selesai()
cek("selesai: lengan di home",
    (app.bot.x, app.bot.y, app.bot.z, app.bot.e)
    == (rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E))
cek("selesai: motor TETAP terkunci, tidak ada M18",
    app.bot.sim.steppers_on is True
    and "M18" not in [c.upper().replace(" ", "") for c in app.bot.sim.log_perintah])

# ukur: simpan ke berkas sendiri, langsung dipakai modul lain
app.kirim("jog", {"sumbu": "z", "arah": -1, "token": "u"})
app.kirim("jog_lepas", {"token": "u"})
app.tunggu_selesai()
app.kirim("ukur_simpan", {"kunci": "z_belt"})
app.tunggu_selesai()
with open(app.berkas_setelan, encoding="utf-8") as _f:
    _tersimpan = _json.load(_f)
cek("ukur: Z belt tersimpan dari posisi lengan",
    _tersimpan["z_belt"] == round(app.bot.z, 2) and "z_belt" in _tersimpan["diukur"])
cek("ukur: jalur.Z_BELT langsung ikut berubah", jalur.Z_BELT == round(app.bot.z, 2))
cek("ukur: masalah konfigurasi dilaporkan (Z belt setinggi ini tidak masuk akal)",
    len(app.status()["masalah"]) > 0)
setelan.terapkan(setelan.BAWAAN)

# titik tertanda: tambah, hapus nomor tertentu, hapus semua
for _ in range(4):
    app.kirim("tandai")
app.kirim("hapus_titik", {"nomor": [2, 4]})
with open(app.berkas_titik, encoding="utf-8") as _f:
    _titik_berkas = _json.load(_f)
cek("hapus nomor 2 dan 4 dari 4 titik -> sisa 2, tersimpan ke berkas",
    len(app.titik) == 2 and len(_titik_berkas) == 2)
try:
    app.kirim("hapus_titik", {"nomor": [9]})
    cek("hapus nomor yang tidak ada ditolak", False)
except aplikasi.AplikasiError:
    cek("hapus nomor yang tidak ada ditolak", len(app.titik) == 2)
app.kirim("hapus_titik", {"semua": True})
cek("hapus semua -> 0", app.titik == [])

# matikan dengan konfirmasi: parkir dulu, baru M18
app.kirim("matikan", {"dipegang": True})
app.tunggu_selesai()
cek("matikan: parkir di titik setelan, lalu motor dilepas",
    app.bot.z == setelan.BAWAAN["parkir"][2] and app.bot.sim.steppers_on is False
    and app.status()["perlu_homing"])

# HTTP
_srv = aplikasi.buat_server(app, 0)
_port = _srv.server_address[1]
_th = __import__("threading").Thread(target=_srv.serve_forever, daemon=True)
_th.start()


def _minta(metode, path, isi=None, header=None):
    k = _http.HTTPConnection("127.0.0.1", _port, timeout=5)
    k.request(metode, path, body=None if isi is None else _json.dumps(isi),
              headers=header or {})
    r = k.getresponse()
    data = r.read()
    k.close()
    return r.status, r.getheader("Content-Type") or "", data


_kode, _jenis, _data = _minta("GET", "/api/status")
cek("HTTP: /api/status menjawab JSON", _kode == 200 and _json.loads(_data)["tersambung"])
for _nama in ("index.html", "app.css", "app.js"):
    _kode, _jenis, _ = _minta("GET", "/" + _nama)
    cek(f"HTTP: {_nama} tersaji", _kode == 200 and _jenis.split(";")[0] in
        ("text/html", "text/css", "text/javascript"))
_kode, _, _ = _minta("POST", "/api/perintah", {"aksi": "homing"})
cek("HTTP: POST tanpa header X-RNV3 ditolak (403)", _kode == 403)
_kode, _, _ = _minta("POST", "/api/perintah", {"aksi": "hancurkan"}, {"X-RNV3": "1"})
cek("HTTP: aksi tak dikenal ditolak (409)", _kode == 409)
for _p in ("/..%2Faplikasi.py", "/../aplikasi.py", "/sub/app.js", "/.git"):
    _kode, _, _ = _minta("GET", _p)
    cek(f"HTTP: {_p} tidak disajikan", _kode == 404)
cek("HTTP: server hanya mendengar di 127.0.0.1", _srv.server_address[0] == "127.0.0.1")
_srv.shutdown()
_srv.server_close()
app.tutup()


print()
print("=" * 68)
print(f"HASIL: {lulus} lulus, {gagal} gagal")
print("=" * 68)
if gagal:
    print("\nAda yang GAGAL -- perbaiki dulu sebelum colok robot.")
else:
    print("\nSemua logika kontrol works. Yang belum teruji cuma hal fisik:")
    print("  - kabel USB / driver CH340 / nomor COM port")
    print("  - presisi homing (endstop)")
    print("  - benturan ke meja atau benda di sekitar")
    print("  - kekuatan cengkeram gripper ke benda nyata")
    print("  - step hilang kalau speed terlalu tinggi")
    print("  - kalibrasi END_EFFECTOR_OFFSET ke koordinat dunia nyata")
    print("\nLihat CATATAN.md bagian 'Checklist hari-H' untuk urutan tes fisik.")
sys.exit(1 if gagal else 0)
