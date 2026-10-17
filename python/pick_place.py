"""
Pick & place grid untuk Robot Arm RNV3 — versi yang sudah diperbaiki.

Ditulis ulang dari contoh-pembina/pickup_and_place.py. Struktur gerakannya
dipertahankan (naik -> geser rel -> gerak XY -> turun), tapi 10 masalah
diperbaiki. Yang paling penting:

  * validate() di file asli memakai batas KOTAK (X +-260, Y 150..340, Z +-100),
    padahal ruang kerja robot berbentuk CANGKANG BOLA. Akibatnya SEMUA 30 slot
    di file asli di luar jangkauan, dan 25 di antaranya lolos validate() lalu
    dikirim ke robot dan gagal diam-diam.
  * pick() di file asli cuma print("Vacuum ON") -- perintah vakum tidak pernah
    dikirim, jadi benda tidak akan pernah terangkat.
  * tidak ada fungsi place() sama sekali.

Di sini validasi memakai check_reach() + check_path() dari rnv3.py, yang
mereplika isAllowedPosition() milik firmware. validasi_grid() dijalankan SEBELUM
menyambung ke robot dan MENOLAK jalan kalau ada satu slot saja yang tak
terjangkau.

CARA PAKAI (Windows -- WSL tidak melihat USB tanpa usbipd):
    python.exe -m pip install pyserial          # sekali saja
    python3 pick_place.py --cek                 # validasi grid, tanpa robot
    python3 pick_place.py --sim --pindah 1 12   # jalan penuh di simulator
    python.exe pick_place.py --port COM5        # robot asli, mode interaktif
    python.exe pick_place.py --port COM5 --pick 7
    python.exe pick_place.py --gui              # butuh PyQt5 (opsional)
"""

import argparse
import sys

import rnv3
from rnv3 import RNV3, RNV3Error, check_path, check_reach, xy_limits

# ===========================================================================
# KONFIGURASI  — ukur di robot nyata, lalu sesuaikan angka-angka di sini.
# Semua satuan milimeter. Jalankan `--cek` setiap kali habis mengubahnya.
# ===========================================================================
PORT = "COM4"        # Windows: Device Manager > Ports. Linux/WSL: /dev/ttyUSB0

# Bentuk grid. BARIS berjalan di sumbu rel (E), KOLOM di sumbu X.
COLS = 5             # jumlah kolom (sumbu X)
ROWS = 4             # jumlah baris (sumbu rel E)

X_CENTER = 0.0       # titik tengah grid pada sumbu X
Y_ROW = 260.0        # jarak grid dari badan robot.
                     #   makin KECIL = makin dekat badan = grid makin LEBAR
                     #   boleh dipakai. Y=340 hampir tidak bisa dipakai.
PITCH_X = 70.0       # jarak antar kolom = lebar benda + celah (50 + 20)

E_START = 0.0        # posisi rel untuk baris pertama (0 = ujung rel)
PITCH_E = 100.0      # jarak antar baris di rel. Rel cuma 355 mm, jadi
                     #   (ROWS-1) * PITCH_E harus <= 355

Z_SAFE = 80.0        # ketinggian aman saat bergerak menyilang
Z_PICK = -75.0       # ketinggian saat mengambil/meletakkan benda

F_FAST = 100.0       # kecepatan gerak biasa (mm/s)
F_SLOW = 30.0        # kecepatan saat turun/naik dekat benda — pelan = presisi
F_RAIL = 40.0        # rel tidak boleh terlalu cepat, nanti step hilang

Z_PARKIR = -40.0     # ketinggian saat selesai, SEBELUM robot dimatikan.
                     # Jangan berakhir di HOME_Z (138) yang tinggi: stepper
                     # kehilangan daya tahan begitu power dicabut, dan lengan
                     # jatuh dari ketinggian itu. Samakan dengan PARKIR di
                     # jog.py; tekan Shift+X di jog.py untuk mengukurnya.

MATIKAN_SAAT_SELESAI = True   # setelah parkir, kirim M18 (motor + kipas mati).
                              # Lengan jadi loyo, tapi sudah parkir rendah jadi
                              # turunnya cuma beberapa mm. Tidak memutus listrik.

VACUUM_SETTLE = 0.4  # jeda setelah vakum ON/OFF (firmware sudah punya delay
                     #   sendiri 300/500 ms; ini tambahan pengaman)


# ===========================================================================
# GEOMETRI GRID
# ===========================================================================
def jumlah_slot():
    return COLS * ROWS


def slot_coord(n):
    """Koordinat slot nomor n (1-based) -> (x, y, e).

    Penomoran: kiri ke kanan dulu, lalu turun ke baris berikutnya.
        baris 1:  1  2  3  4  5
        baris 2:  6  7  8  9 10
    """
    total = jumlah_slot()
    if not (1 <= n <= total):
        raise ValueError(f"nomor slot harus 1..{total}, dapat {n}")
    i = n - 1
    kol = i % COLS
    bar = i // COLS
    span = (COLS - 1) * PITCH_X
    x = X_CENTER - span / 2 + kol * PITCH_X
    e = E_START + bar * PITCH_E
    return x, Y_ROW, e


# ===========================================================================
# VALIDASI GRID  — pagar utama. Dijalankan sebelum menyentuh robot.
# ===========================================================================
def validasi_grid(cetak=True):
    """Periksa semua slot terjangkau di Z_SAFE DAN Z_PICK, plus jalur antar slot.

    Return (True, []) kalau semua aman, atau (False, [daftar masalah]).
    """
    masalah = []

    # Rel tidak boleh kehabisan panjang.
    e_max = E_START + (ROWS - 1) * PITCH_E
    if E_START < 0:
        masalah.append(f"E_START={E_START} negatif, rel mulai dari 0")
    if e_max > rnv3.RAIL_LENGTH:
        masalah.append(
            f"baris terakhir butuh E={e_max:.0f} mm, rel cuma "
            f"{rnv3.RAIL_LENGTH:.0f} mm -> kecilkan PITCH_E atau ROWS")

    if cetak:
        print("=" * 72)
        print(f"VALIDASI GRID  {COLS} kolom x {ROWS} baris = {jumlah_slot()} slot")
        print(f"  Y_ROW={Y_ROW:.0f}  PITCH_X={PITCH_X:.0f}  PITCH_E={PITCH_E:.0f}"
              f"  Z_SAFE={Z_SAFE:.0f}  Z_PICK={Z_PICK:.0f}")
        print("=" * 72)
        print(f"{'slot':>5} {'X':>8} {'Y':>7} {'E':>7}   {'Z_SAFE':<10} {'Z_PICK':<10}")

    for n in range(1, jumlah_slot() + 1):
        x, y, e = slot_coord(n)
        ok_s, why_s = check_reach(x, y, Z_SAFE, e)
        ok_p, why_p = check_reach(x, y, Z_PICK, e)
        if not ok_s:
            masalah.append(f"slot {n} di Z_SAFE: {why_s}")
        if not ok_p:
            masalah.append(f"slot {n} di Z_PICK: {why_p}")
        if cetak:
            print(f"{n:>5} {x:>8.1f} {y:>7.1f} {e:>7.1f}   "
                  f"{('OK' if ok_s else 'TOLAK'):<10} {('OK' if ok_p else 'TOLAK'):<10}")

    # Jalur menyilang grid di ketinggian aman. Ini yang menangkap "lubang mati"
    # di dekat badan robot -- dua ujung valid belum berarti jalurnya valid.
    if COLS > 1:
        x0, _, _ = slot_coord(1)
        x1, _, _ = slot_coord(COLS)
        ok_j, why_j, _ = check_path((x0, Y_ROW, Z_SAFE), (x1, Y_ROW, Z_SAFE))
        if not ok_j:
            masalah.append(f"jalur kolom pertama -> terakhir di Z_SAFE: {why_j}")
        if cetak:
            print(f"\n  jalur X {x0:.0f} -> {x1:.0f} di Z_SAFE: "
                  f"{'aman' if ok_j else 'TOLAK'}")

    # Jalur naik-turun di tiap slot.
    for n in range(1, jumlah_slot() + 1):
        x, y, e = slot_coord(n)
        ok_t, why_t, _ = check_path((x, y, Z_SAFE), (x, y, Z_PICK))
        if not ok_t:
            masalah.append(f"jalur turun di slot {n}: {why_t}")

    if cetak:
        print()
        if masalah:
            print(f"GAGAL -- {len(masalah)} masalah:")
            for m in masalah[:12]:
                print(f"  - {m}")
            if len(masalah) > 12:
                print(f"  ... dan {len(masalah) - 12} lagi")
            _saran()
        else:
            print(f"SEMUA {jumlah_slot()} SLOT AMAN. Grid ini bisa dipakai.")
        print("=" * 72)

    return (not masalah), masalah


def _saran():
    """Cetak pilihan Y_ROW/PITCH_X yang benar-benar muat."""
    print("\n  Jangkauan robot BUKAN kotak -- dia cangkang bola, dan lebarnya")
    print("  berubah tergantung Z. Makin dekat ke badan robot (Y kecil), makin")
    print("  lebar grid yang muat. Pilihan yang sudah dihitung:\n")
    print(f"  {'Y_ROW':>6} {'Z_SAFE':>7} {'PITCH_X':>8} {'COLS':>5}   X terkecil..terbesar")
    for y in (300, 280, 260, 240, 220):
        for zs in (60, 80):
            for pitch in (120, 100, 90, 80, 70):
                for cols in (6, 5, 4):
                    span = (cols - 1) * pitch
                    xs = [-span / 2 + i * pitch for i in range(cols)]
                    if all(check_reach(x, y, zs)[0] and check_reach(x, y, Z_PICK)[0]
                           for x in xs):
                        jp, _, _ = check_path((xs[0], y, zs), (xs[-1], y, zs))
                        if jp:
                            print(f"  {y:>6} {zs:>7} {pitch:>8} {cols:>5}   "
                                  f"{xs[0]:>7.0f} .. {xs[-1]:<7.0f}")
                            break
                else:
                    continue
                break
            else:
                continue
            break
    lim = xy_limits(Z_SAFE)
    if lim:
        print(f"\n  Di Z_SAFE={Z_SAFE:.0f}, radius XY yang valid: "
              f"{lim[0]:.0f} .. {lim[1]:.0f} mm")


# ===========================================================================
# GERAKAN
# ===========================================================================
def _ke_slot_aman(bot, n):
    """Pindah ke atas slot n pada ketinggian aman. Urutan dijaga agar tidak
    menyeret benda: naik dulu, baru rel, baru XY."""
    x, y, e = slot_coord(n)
    bot.move(z=Z_SAFE, f=F_FAST)
    bot.move(e=e, f=F_RAIL)
    bot.move(x=x, y=y, f=F_FAST)


def pick(bot, n):
    """Ambil benda dari slot n dan angkat ke ketinggian aman."""
    print(f"\n>>> PICK slot {n}  ->  X{slot_coord(n)[0]:.0f} "
          f"Y{slot_coord(n)[1]:.0f} E{slot_coord(n)[2]:.0f}")
    _ke_slot_aman(bot, n)
    bot.move(z=Z_PICK, f=F_SLOW)
    bot.vacuum(True)                 # <- file asli cuma print, tidak pernah kirim
    bot.dwell(VACUUM_SETTLE)
    bot.move(z=Z_SAFE, f=F_SLOW)


def place(bot, n):
    """Letakkan benda yang sedang dipegang ke slot n."""
    print(f"\n>>> PLACE slot {n}  ->  X{slot_coord(n)[0]:.0f} "
          f"Y{slot_coord(n)[1]:.0f} E{slot_coord(n)[2]:.0f}")
    _ke_slot_aman(bot, n)
    bot.move(z=Z_PICK, f=F_SLOW)
    bot.vacuum(False)
    bot.dwell(VACUUM_SETTLE)
    bot.move(z=Z_SAFE, f=F_SLOW)


def pindah(bot, dari, ke):
    """Pindahkan benda dari satu slot ke slot lain."""
    if dari == ke:
        raise ValueError("slot asal dan tujuan sama")
    pick(bot, dari)
    place(bot, ke)


def parkir(bot):
    """Matikan vakum dan turunkan lengan ke ketinggian parkir.

    Urutan: naik dulu supaya tidak menyeret benda, geser rel dan X/Y ke posisi
    netral, BARU turun. Berakhir di Z_PARKIR yang rendah -- bukan di HOME_Z --
    supaya saat power dimatikan lengan hanya jatuh beberapa milimeter.
    """
    print("\n>>> PARKIR")
    bot.vacuum(False)
    bot.move(z=Z_SAFE, f=F_FAST)
    bot.move(e=rnv3.HOME_E, f=F_RAIL)
    bot.move(x=rnv3.HOME_X, y=rnv3.HOME_Y, f=F_FAST)
    bot.move(z=Z_PARKIR, f=F_SLOW)
    if MATIKAN_SAAT_SELESAI:
        bot.steppers(False)       # M18: motor + kipas mati, robot diam total
        print("  motor & kipas dimatikan -- saklar fisik aman ditekan")


# ===========================================================================
# ANTARMUKA
# ===========================================================================
def interaktif(bot):
    total = jumlah_slot()
    print(f"\nPerintah:  <n> pick   p<n> place   <a>-<b> pindah   "
          f"k parkir   q keluar")
    while True:
        try:
            jwb = input(f"slot (1-{total}) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not jwb:
            continue
        try:
            if jwb == "q":
                break
            if jwb == "k":
                parkir(bot)
            elif "-" in jwb:
                a, b = (int(v) for v in jwb.split("-", 1))
                pindah(bot, a, b)
            elif jwb.startswith("p"):
                place(bot, int(jwb[1:]))
            else:
                pick(bot, int(jwb))
        except (ValueError, RNV3Error) as err:
            print(f"  !! {err}")


def jalankan_gui(bot):
    try:
        from PyQt5 import QtWidgets
    except ImportError:
        print("PyQt5 belum terpasang. Pilihan:")
        print("  python.exe -m pip install PyQt5      # lalu ulangi dengan --gui")
        print("  atau jalankan tanpa --gui (mode terminal, tidak butuh PyQt5)")
        return 1

    total = jumlah_slot()

    class Jendela(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("RNV3 Pick & Place")
            lay = QtWidgets.QVBoxLayout()
            lay.addWidget(QtWidgets.QLabel(f"Slot (1 - {total})"))
            self.a = QtWidgets.QSpinBox(); self.a.setRange(1, total)
            self.b = QtWidgets.QSpinBox(); self.b.setRange(1, total)
            self.b.setValue(min(2, total))
            baris = QtWidgets.QHBoxLayout()
            baris.addWidget(QtWidgets.QLabel("dari")); baris.addWidget(self.a)
            baris.addWidget(QtWidgets.QLabel("ke")); baris.addWidget(self.b)
            lay.addLayout(baris)
            for teks, fn in (("PICK", lambda: pick(bot, self.a.value())),
                             ("PLACE", lambda: place(bot, self.a.value())),
                             ("PINDAH", lambda: pindah(bot, self.a.value(),
                                                       self.b.value())),
                             ("PARKIR", lambda: parkir(bot))):
                tb = QtWidgets.QPushButton(teks)
                tb.clicked.connect(self._bungkus(fn))
                lay.addWidget(tb)
            self.setLayout(lay)

        def _bungkus(self, fn):
            def jalan():
                try:
                    fn()
                except (ValueError, RNV3Error) as err:
                    QtWidgets.QMessageBox.critical(self, "Gagal", str(err))
            return jalan

    app = QtWidgets.QApplication(sys.argv[:1])
    win = Jendela()
    win.show()
    return app.exec_()


# ===========================================================================
# MAIN
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=None,
                    help="port serial; kosongkan untuk deteksi otomatis")
    ap.add_argument("--sim", action="store_true",
                    help="pakai simulator firmware, tanpa robot")
    ap.add_argument("--cek", action="store_true",
                    help="hanya validasi grid lalu keluar, tanpa menyambung")
    ap.add_argument("--gui", action="store_true", help="antarmuka PyQt5")
    ap.add_argument("--pick", type=int, metavar="N")
    ap.add_argument("--place", type=int, metavar="N")
    ap.add_argument("--pindah", type=int, nargs=2, metavar=("DARI", "KE"))
    ap.add_argument("--diam", action="store_true",
                    help="jangan cetak perintah serial")
    a = ap.parse_args(argv)

    # Pagar: grid wajib lolos sebelum apa pun menyentuh robot.
    aman, _ = validasi_grid(cetak=True)
    if not aman:
        print("\nDibatalkan: perbaiki KONFIGURASI di atas pick_place.py dulu.")
        return 2
    if a.cek:
        return 0

    if a.sim:
        import simulasi
        bot = simulasi.robot_palsu(verbose=not a.diam)
    else:
        if not a.port:
            a.port = rnv3.cari_port()
            if not a.port:
                return 1
            print(f"robot ditemukan di {a.port}")
        try:
            bot = RNV3(a.port, verbose=not a.diam)
        except Exception as err:
            print(f"\nGagal membuka {a.port}: {err}")
            print("  - Windows: cek nomor COM di Device Manager > Ports")
            print("  - pastikan software GUI robot sudah DIPUTUS (satu port, satu pemakai)")
            print("  - jalankan dengan --sim untuk mencoba tanpa robot")
            return 1

    try:
        bot.home()
        if a.gui:
            return jalankan_gui(bot)
        if a.pick is not None:
            pick(bot, a.pick)
        elif a.place is not None:
            place(bot, a.place)
        elif a.pindah:
            pindah(bot, *a.pindah)
        else:
            interaktif(bot)
        parkir(bot)
    except RNV3Error as err:
        print(f"\nGAGAL: {err}")
        return 1
    except KeyboardInterrupt:
        print("\ndihentikan pengguna")
    finally:
        bot.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
