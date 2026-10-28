"""
Kontak pertama dengan robot RNV3 — verifikasi TANPA menggerakkan lengan.

Dipakai saat pertama kali menyambung ke robot, atau kapan pun robot bertingkah
aneh. Tujuannya menjawab satu pertanyaan yang tidak bisa dijawab dari membaca
file: *firmware mana yang sebenarnya ter-upload ke robot ini?*

Ada tiga varian config.h di folder firmware/ dengan nilai berbeda, dan memilih
yang salah membuat gripper tidak berfungsi sama sekali (firmware menolak sudut
di luar rentangnya sendiri). Firmware mencetak jawabannya setiap kali nyala.

HANYA MENGIRIM PERINTAH BACA-SAJA:
    (tidak ada)  -> baca pesan boot
    M114         -> lapor koordinat
    M119         -> status endstop
    M105         -> status 3 sensor (firmware v2_02 saja)

Tidak ada G0 / G28 / G100 / VACUM / M17 / M18. Lengan tidak bergerak.
cek.py memverifikasi klaim ini dengan membandingkan log perintah ke simulator.

SATU PENGECUALIAN, harap diketahui: membuka port serial MERESET Arduino, dan
setup() firmware menjalankan servoB.write(MAX_SERVO). Jadi GRIPPER akan membuka
sendiri setiap kali menyambung. Tidak bisa dihindari — software GUI resmi pun
begitu. Lengannya aman: HOME_ON_BOOT=false dan setStepperEnable(false) saat
boot, jadi keempat stepper mati total.

CARA PAKAI:
    python3  kontak.py --sim            # simulator, tanpa robot
    python.exe kontak.py --port COM4    # robot asli (Windows)

Exit code 0 kalau semua cocok, 1 kalau ada yang tidak — jadi bisa dipakai
sebagai gerbang sebelum menjalankan pick_place.py.
"""

import argparse
import sys

import rnv3
from rnv3 import RNV3, RNV3Error


# ===========================================================================
# PEMBACAAN  — semua method di bawah ini hanya mengirim M114 / M119 / M105
# ===========================================================================
def baca_robot(bot):
    """Kumpulkan semua yang bisa dibaca tanpa menggerakkan robot."""
    hasil = {
        "boot": list(bot.boot_lines),
        "firmware": bot.firmware,
        "servo": bot.servo_range_reported,
        "rail": bot.rail_reported,
        "endstop": None,
        "posisi": None,
        "sensor": None,
        "sensor_error": None,
    }
    try:
        hasil["endstop"] = bot.endstops()
    except RNV3Error as err:
        hasil["endstop_error"] = str(err)
    try:
        hasil["posisi"] = bot.position()
    except RNV3Error as err:
        hasil["posisi_error"] = str(err)
    try:
        hasil["sensor"] = bot.sensors()
    except RNV3Error as err:
        # M105 tidak ada di firmware v2_01 — itu wajar, bukan kegagalan.
        hasil["sensor_error"] = str(err)
    return hasil


# ===========================================================================
# PENILAIAN
# ===========================================================================
def verdict(hasil):
    """Bandingkan apa yang robot laporkan dengan konstanta di rnv3.py.

    Return list of (nama, dari_robot, dari_kode, status, saran) dengan status:
        "cocok"     -> sesuai
        "beda"      -> TIDAK cocok, harus dibetulkan (bikin exit code 1)
        "perhatian" -> perlu diperhatikan tapi bukan penghalang
        "info"      -> sekadar keterangan
    """
    baris = []

    baris.append(("firmware", hasil["firmware"], "-", "info",
                  "v2_02 punya M105 (baca sensor); v2_01 tidak"))

    s = hasil["servo"]
    if s is None:
        baris.append(("rentang servo", "tidak terbaca",
                      f"{rnv3.SERVO_CLOSED}/{rnv3.SERVO_OPEN}", "beda",
                      "pesan boot tidak tertangkap -- coba cabut-colok USB, "
                      "lalu jalankan ulang"))
    else:
        cocok = s == (rnv3.SERVO_CLOSED, rnv3.SERVO_OPEN)
        baris.append(("rentang servo", f"{s[0]}/{s[1]}",
                      f"{rnv3.SERVO_CLOSED}/{rnv3.SERVO_OPEN}",
                      "cocok" if cocok else "beda",
                      "" if cocok else
                      f"ubah di rnv3.py:  SERVO_CLOSED = {s[0]}  "
                      f"SERVO_OPEN = {s[1]}"))

    r = hasil["rail"]
    if r is None:
        baris.append(("rel", "tidak terbaca", str(rnv3.RAIL_ENABLED), "beda",
                      "pesan boot tidak tertangkap"))
    else:
        cocok = r == rnv3.RAIL_ENABLED
        baris.append(("rel", "ON" if r else "OFF",
                      "ON" if rnv3.RAIL_ENABLED else "OFF",
                      "cocok" if cocok else "beda",
                      "" if cocok else
                      f"ubah di rnv3.py:  RAIL_ENABLED = {r}"))

    es = hasil["endstop"]
    if es:
        # Endstop yang sudah tersentuh TIDAK menggagalkan homing: di
        # Endstop::home() (endstop.cpp:18) loop pencarian `while (!bState)`
        # dilewati, lalu homeOffset() langsung mundur step_offset. Jadi ini
        # soal presisi, bukan kegagalan -- dan khusus rel, mentok di ujung nol
        # memang posisi parkir normalnya.
        lengan = [k for k in ("X", "Y", "Z") if es.get(k) == "1"]
        rel_kena = es.get("E") == "1"

        if lengan:
            baris.append(("endstop lengan", "tersentuh: " + ", ".join(lengan),
                          "idealnya bebas", "perhatian",
                          "geser lengan maju sedikit PAKAI TANGAN sebelum G28. "
                          "Homing tetap jalan, tapi titik nolnya kurang "
                          "berulang kalau start dari posisi mentok."))
        else:
            baris.append(("endstop lengan", "X, Y, Z bebas", "bebas", "cocok", ""))

        if "E" in es:
            baris.append(("endstop rel", "tersentuh (parkir di E=0)"
                          if rel_kena else "bebas", "-", "info",
                          "normal -- rel memang parkir di E=0. Homing dari sini "
                          "aman: pencarian dilewati, langsung mundur offset."))

    if hasil["sensor"]:
        isi = " ".join(f"{k}={v}" for k, v in sorted(hasil["sensor"].items()))
        baris.append(("sensor input", isi, "-", "info",
                      "ON/OFF tergantung ada benda di depan sensor atau tidak"))
    elif hasil["sensor_error"]:
        baris.append(("sensor input", "tidak tersedia", "-", "info",
                      "butuh firmware v2_02; upload firmware/v2_02 kalau "
                      "kelompok sensor memerlukannya"))

    return baris


def cetak_laporan(hasil, baris):
    print()
    print("=" * 74)
    print("PESAN BOOT DARI ROBOT")
    print("=" * 74)
    if hasil["boot"]:
        for b in hasil["boot"]:
            print(f"  {b}")
    else:
        print("  (tidak ada -- lihat catatan di bawah)")

    print()
    print("=" * 74)
    print("PERBANDINGAN: ROBOT vs KODE KITA")
    print("=" * 74)
    print(f"  {'apa':<16} {'kata robot':<26} {'kata rnv3.py':<14} hasil")
    print("  " + "-" * 70)
    gagal = perhatian = 0
    label = {"cocok": "COCOK", "beda": "TIDAK COCOK",
             "perhatian": "perhatian", "info": "info"}
    for nama, dari_robot, dari_kode, status, _ in baris:
        if status == "beda":
            gagal += 1
        elif status == "perhatian":
            perhatian += 1
        print(f"  {nama:<16} {str(dari_robot):<26} {str(dari_kode):<14} "
              f"{label.get(status, status)}")

    saran = [(n, s) for n, _, _, c, s in baris if s and c != "cocok"]
    if saran:
        print()
        print("=" * 74)
        print("CATATAN & SARAN")
        print("=" * 74)
        for nama, s in saran:
            print(f"  [{nama}]")
            for potong in _bungkus(s, 68):
                print(f"    {potong}")

    p = hasil["posisi"]
    if p:
        print()
        print("=" * 74)
        print("POSISI YANG DILAPORKAN ROBOT")
        print("=" * 74)
        print(f"  X={p['x']:.2f}  Y={p['y']:.2f}  Z={p['z']:.2f}  E={p['e']:.2f}")
        default = (rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E)
        if (p["x"], p["y"], p["z"], p["e"]) == default:
            print()
            print("  PENTING: angka ini BUKAN posisi fisik lengan sebenarnya.")
            print("  Sebelum G28, firmware hanya memakai nilai INITIAL_X/Y/Z/E0")
            print("  dari config.h sebagai asumsi. Posisi sesungguhnya baru")
            print("  diketahui setelah homing.")

    print()
    print("=" * 74)
    if gagal == 0:
        if perhatian:
            print(f"KESIMPULAN: kode sudah sesuai dengan robot ini. "
                  f"{perhatian} hal perlu diperhatikan")
            print("(lihat bagian saran), tapi bukan penghalang.")
        else:
            print("KESIMPULAN: semua cocok. Kode sudah sesuai dengan robot ini.")
        print()
        print("Langkah berikutnya (BUKAN sekarang -- lengan akan bergerak):")
        print("  1. berdiri di sebelah robot, tangan di tombol power")
        print("  2. geser lengan maju sedikit pakai tangan")
        print("  3. kirim G28 untuk homing")
        print("  Urutan lengkapnya ada di CATATAN.md -> 'Checklist hari-H'")
    else:
        print(f"KESIMPULAN: {gagal} hal TIDAK COCOK. Perbaiki dulu sesuai saran")
        print("di atas, lalu jalankan `python3 cek.py` dan ulangi kontak.py.")
        print("Jangan lanjut ke gerakan sebelum semuanya cocok.")
    print("=" * 74)
    return gagal


def _bungkus(teks, lebar):
    kata, baris, kini = teks.split(), [], ""
    for k in kata:
        if len(kini) + len(k) + 1 > lebar:
            baris.append(kini)
            kini = k
        else:
            kini = f"{kini} {k}".strip()
    if kini:
        baris.append(kini)
    return baris


# ===========================================================================
# MAIN
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Kontak pertama dengan robot RNV3, tanpa menggerakkan lengan.")
    ap.add_argument("--port", default=None,
                    help="port serial; kosongkan untuk deteksi otomatis")
    ap.add_argument("--sim", action="store_true",
                    help="pakai simulator firmware, tanpa robot")
    ap.add_argument("--diam", action="store_true",
                    help="jangan cetak lalu-lintas serial")
    a = ap.parse_args(argv)

    if a.sim:
        import simulasi
        bot = simulasi.robot_palsu(verbose=not a.diam)
    else:
        if not a.port:
            a.port = rnv3.cari_port()
            if not a.port:
                return 1
            print(f"robot ditemukan di {a.port}")
        print(f"Menyambung ke {a.port} ...")
        print("  Catatan: membuka port mereset Arduino, jadi GRIPPER akan")
        print("  membuka sendiri. Lengan tidak bergerak (stepper mati saat boot).")
        try:
            bot = RNV3(a.port, verbose=not a.diam)
        except Exception as err:
            print(f"\nGagal membuka {a.port}: {err}")
            print("  - cek nomor COM di Device Manager > Ports")
            print("  - pastikan software GUI robot / Arduino IDE sudah DIPUTUS")
            print("    (satu port serial hanya boleh dipegang satu program)")
            print("  - di WSL pakai `python.exe`, bukan `python3` -- WSL tidak")
            print("    melihat USB tanpa usbipd")
            print("  - coba `python3 kontak.py --sim` untuk menguji tanpa robot")
            return 1

    try:
        hasil = baca_robot(bot)
        gagal = cetak_laporan(hasil, verdict(hasil))
    finally:
        bot.close()
    return 1 if gagal else 0


if __name__ == "__main__":
    sys.exit(main())
