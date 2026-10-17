"""
MIDDLEWARE — penyambung konveyor + kamera + lengan RNV3 jadi satu alur sortir.

Inilah "middleware" yang dimaksud: program yang duduk di TENGAH tiga subsistem
yang tidak bisa saling bicara. ESP tidak paham G-code, robot tidak bisa bertanya
ke kamera, kamera cuma bisa melihat. File ini yang menerjemahkan dan mengatur
urutan. Dia tidak tahu apa-apa soal HTTP — itu urusan backend.py di atasnya.

    LAPIS 3  backend.py        API, WebSocket, database
    LAPIS 2  jalur.py          <-- INI
    LAPIS 1  rnv3 / esp / kontrak / kalibrasi
    LAPIS 0  robot, ESP+konveyor, kamera

Program ini yang memegang SEMUA port serial. Satu port satu pemakai — GUI resmi,
Serial Monitor, atau script lain tidak boleh jalan bersamaan. Robot dan ESP itu
dua port berbeda, dan keduanya harus dipegang thread yang sama.

ALUR SATU SIKLUS

    belt JALAN
      -> tunggu sensor kena benda
      -> belt BERHENTI            <- wajib, sebelum apa pun yang lain
      -> diamkan (benda masih goyang)
      -> minta deteksi ke kamera  <- baru SEKARANG, bukan sambil jalan
      -> saring yang terjangkau lengan
      -> turun ke tinggi sesuai JENIS benda, vakum ON, angkat
      -> pindah ke bak sesuai JENIS benda, vakum OFF
      -> ulang

KENAPA BELT HARUS BERHENTI DULU

Firmware RNV3 itu blocking: G0 yang sudah dikirim jalan sampai selesai, tidak
bisa dibatalkan, dan tidak ada E-stop (KILL_PIN = -1). Mengambil benda yang
masih berjalan butuh encoder di belt + trajectory real-time; firmware ini tidak
mampu. Jadi polanya stop-and-pick.

CARA PAKAI

    python3 jalur.py --cek                  # validasi konfigurasi saja
    python3 jalur.py --sim --siklus 20      # tanpa hardware, belt lewat LG
    python3 jalur.py --sim --esp            # tanpa hardware, belt lewat ESP
    python3 jalur.py --port COM4 --esp-port COM7     # hardware sungguhan
"""

import argparse
import sys
import threading
import time

import kontrak
import konveyor as mod_konveyor
import rnv3

# ---------------------------------------------------------------------------
# KONFIGURASI — UKUR DULU DI ROBOT NYATA, jangan percaya angka di bawah ini.
# Semua satuan milimeter, kecepatan mm/s.
# ---------------------------------------------------------------------------
PORT = "COM4"            # robot.  Windows: Device Manager > Ports
ESP_PORT = "COM7"        # ESP32.  port BERBEDA dari robot, gampang tertukar

# Permukaan tempat benda berada. UKUR dengan jog.py: turunkan gripper sampai
# menyentuh belt, catat Z-nya.
Z_BELT = -80.0           # permukaan atas belt konveyor
Z_BAK = -80.0            # permukaan dasar bak keluaran

# Zona ambil di belt. Jangkauan robot BERUBAH tergantung Z — divalidasi di
# validasi_konfigurasi(). Dari CATATAN.md: makin dekat ke badan robot (Y kecil),
# makin lebar X yang terjangkau.
BELT_Y = 260.0           # jarak zona ambil dari pusat rotasi robot
BELT_X_LEBAR = 150.0     # setengah lebar zona ambil yang dipakai kamera

# Tiap jenis benda punya tinggi dan bak sendiri. Tinggi dipakai menghitung
# sampai mana gripper harus turun — suction cup menempel di PERMUKAAN ATAS
# benda, bukan di permukaan belt.
JENIS = {
    "botol": {"tinggi": 150.0, "taruh": (-170.0, 215.0)},
    "koin":  {"tinggi": 2.0,   "taruh": (-55.0, 300.0)},
}
JENIS_DEFAULT = "koin"   # dipakai kalau label dari kamera tidak dikenal

LEPAS_CLEARANCE = 10.0   # lepas benda sedikit di atas dasar bak
JELAJAH_CLEARANCE = 30.0 # jarak minimum DASAR benda yang digenggam ke
                         # permukaan belt/bak saat menyeberang

Z_SAFE = 80.0            # ketinggian jelajah, bebas dari benda apa pun
Z_PARKIR = -40.0         # ketinggian SEBELUM robot dimatikan. Lengan jatuh
                         # karena gravitasi saat daya hilang — parkir rendah
                         # supaya tidak ada jarak jatuh.

F_FAST = 100.0           # gerak jelajah
F_SLOW = 30.0            # turun/naik dekat benda — pelan = presisi

VACUUM_SETTLE = 0.4      # jeda setelah vakum ON/OFF
BELT_SETTLE = 0.4        # jeda setelah belt berhenti, sebelum difoto
TIMEOUT_BENDA = 60.0     # berapa lama menunggu benda datang di belt
TIMEOUT_LEWAT = 30.0     # berapa lama menunggu benda lewat kalau di-skip

LG_BELT = 1              # output MOSFET yang menyalakan belt (mode KonveyorLG)
SENSOR_BELT = 1          # input sensor kedatangan benda   (mode KonveyorLG)


def profil(label):
    """Ambil tinggi + bak untuk satu label dari kamera.

    Label tak dikenal tidak bikin sistem berhenti — pakai JENIS_DEFAULT dan
    biarkan tercatat di log. Model kamera masih bisa berubah-ubah selama
    pengembangan, jangan sampai label baru menjatuhkan seluruh lini.
    """
    return JENIS.get(label, JENIS[JENIS_DEFAULT])


def z_ambil(label):
    """Tinggi saat menyentuh permukaan ATAS benda."""
    return Z_BELT + profil(label)["tinggi"]


def z_lepas(label):
    """Tinggi saat melepas benda di bak."""
    return Z_BAK + profil(label)["tinggi"] + LEPAS_CLEARANCE


def z_jelajah(label):
    """Tinggi jelajah saat MENGGENGGAM benda ini.

    Suction cup menempel di permukaan ATAS benda, jadi bendanya menggantung
    sepanjang `tinggi` di BAWAH gripper. Z_SAFE yang konstan mengabaikan itu:
    botol 150 mm diangkat ke Z_SAFE=80 menyisakan dasarnya di -70 sementara
    belt ada di -80 -- cuma 10 mm, lalu menyeberang di F_FAST.

    Tidak pernah lebih rendah dari Z_SAFE, supaya rintangan tetap (dinding bak,
    rangka belt) yang sudah tervalidasi di Z_SAFE tetap aman dilewati.
    """
    dasar = max(Z_BELT, Z_BAK) + profil(label)["tinggi"] + JELAJAH_CLEARANCE
    return max(Z_SAFE, dasar)


def clearance_jelajah(label):
    """Sisa jarak dasar benda ke permukaan tertinggi saat jelajah (mm)."""
    return z_jelajah(label) - profil(label)["tinggi"] - max(Z_BELT, Z_BAK)


# ---------------------------------------------------------------------------
# HASIL SATU SIKLUS
# ---------------------------------------------------------------------------
class Hasil:
    SUKSES = "sukses"
    TIDAK_ADA_BENDA = "tidak ada benda"       # belt kosong sampai timeout
    KAMERA_KOSONG = "kamera tidak melihat"    # ada benda tapi tak terdeteksi
    DILUAR_JANGKAUAN = "di luar jangkauan"    # terdeteksi tapi tak tercapai
    KAMERA_ERROR = "kamera error"             # mati / timeout / format salah
    ROBOT_ERROR = "robot error"               # gerakan ditolak / port putus


def validasi_konfigurasi(cetak=True):
    """Cek semua titik di konfigurasi terjangkau SEBELUM apa pun bergerak.

    Lebih baik gagal di sini daripada robot berhenti di tengah jalan karena
    LIMIT REACHED — kalau firmware membatalkan gerakan, posisi yang dilacak
    Python jadi desync dan semua gerakan berikutnya salah.

    Return list masalah (kosong = aman).
    """
    masalah = []
    titik = [
        ("zona ambil kiri @aman",  -BELT_X_LEBAR, BELT_Y, Z_SAFE),
        ("zona ambil kanan @aman",  BELT_X_LEBAR, BELT_Y, Z_SAFE),
        ("parkir",                  0.0, BELT_Y, Z_PARKIR),
    ]
    for label in JENIS:
        tx, ty = profil(label)["taruh"]
        titik += [
            (f"ambil {label} kiri",  -BELT_X_LEBAR, BELT_Y, z_ambil(label)),
            (f"ambil {label} kanan",  BELT_X_LEBAR, BELT_Y, z_ambil(label)),
            (f"ambil {label} tengah", 0.0,          BELT_Y, z_ambil(label)),
            (f"bak {label}",          tx, ty, z_lepas(label)),
            (f"bak {label} @aman",    tx, ty, Z_SAFE),
            (f"ambil {label} @jelajah kiri",  -BELT_X_LEBAR, BELT_Y, z_jelajah(label)),
            (f"ambil {label} @jelajah kanan",  BELT_X_LEBAR, BELT_Y, z_jelajah(label)),
            (f"bak {label} @jelajah",          tx, ty, z_jelajah(label)),
        ]

    for nama, x, y, z in titik:
        ok, alasan = rnv3.check_reach(x, y, z)
        if not ok:
            masalah.append(f"{nama} (X{x:.0f} Y{y:.0f} Z{z:.0f}): {alasan}")

    # Jalur menyilang terjauh: ujung belt -> tiap bak, pada tinggi genggaman
    # yang BENAR-BENAR dipakai (z_jelajah, bukan Z_SAFE). check_path
    # meniru pengecekan per-langkah firmware, jadi "lubang mati" di dekat
    # badan robot ikut ketahuan.
    for label in JENIS:
        tx, ty = profil(label)["taruh"]
        for sisi, x in (("kanan", BELT_X_LEBAR), ("kiri", -BELT_X_LEBAR)):
            zj = z_jelajah(label)
            ok, alasan, _ = rnv3.check_path((x, BELT_Y, zj), (tx, ty, zj))
            if not ok:
                masalah.append(f"jalur belt {sisi} -> bak {label}: {alasan}")

    # Clearance benda yang digenggam. Validasi lama hanya memeriksa titik
    # gripper, bukan volume sapuan benda -- itu yang meloloskan botol dengan
    # sisa 10 mm di atas belt.
    for label in JENIS:
        sisa = clearance_jelajah(label)
        if sisa < JELAJAH_CLEARANCE - 1e-6:
            masalah.append(
                f"clearance {label} saat jelajah cuma {sisa:.0f} mm "
                f"(minimum {JELAJAH_CLEARANCE:.0f} mm)")

    if cetak:
        if masalah:
            print("KONFIGURASI BERMASALAH:")
            for m in masalah:
                print(f"  - {m}")
            print()
            for label in JENIS:
                r = rnv3.xy_limits(z_ambil(label))
                if r:
                    print(f"  radius XY valid saat ambil {label} "
                          f"(Z={z_ambil(label):.0f}): {r[0]:.0f}..{r[1]:.0f} mm")
        else:
            print("konfigurasi OK — semua titik dan jalur terjangkau")
            for label in JENIS:
                print(f"  {label:6s} ambil di Z={z_ambil(label):+.0f}  "
                      f"lepas di Z={z_lepas(label):+.0f}  "
                      f"bak {profil(label)['taruh']}")
    return masalah


# ---------------------------------------------------------------------------
# MIDDLEWARE
# ---------------------------------------------------------------------------
class Jalur:
    """State machine konveyor -> kamera -> lengan.

    Bisa dijalankan langsung (blocking) atau dari thread lain lewat
    jalankan() + minta_berhenti(). Setiap perpindahan state dilaporkan ke
    on_status — itulah sumber data dashboard nanti.

    PENTING soal thread: objek ini memegang port serial. Semua pemanggilannya
    harus dari SATU thread. Backend boleh membaca snapshot(), tapi tidak boleh
    memanggil siklus()/jalankan() dari thread HTTP.
    """

    def __init__(self, bot, kamera, belt, verbose=True, on_status=None):
        self.bot = bot
        self.kamera = kamera
        self.belt = belt
        self.verbose = verbose
        self.on_status = on_status

        self.stop_event = threading.Event()
        self._kunci = threading.Lock()
        self.statistik = {}
        self.hitungan = {label: 0 for label in JENIS}
        self._status = {
            "state": "mati",
            "siklus": 0,
            "hasil_terakhir": None,
            "benda_terakhir": None,
            "waktu": time.time(),
        }

    # -- status --------------------------------------------------------------
    def snapshot(self):
        """Salinan status terkini. AMAN dipanggil dari thread lain."""
        with self._kunci:
            s = dict(self._status)
        s["statistik"] = dict(self.statistik)
        s["hitungan"] = dict(self.hitungan)
        s["berjalan"] = not self.stop_event.is_set()
        return s

    def _lapor(self, state, **tambahan):
        with self._kunci:
            self._status["state"] = state
            self._status["waktu"] = time.time()
            self._status.update(tambahan)
        if self.on_status:
            # Callback milik lapisan atas; jangan biarkan errornya
            # menjatuhkan lini produksi.
            try:
                self.on_status(self.snapshot())
            except Exception as e:                       # noqa: BLE001
                self._kata(f"on_status melempar {type(e).__name__}: {e}")
        self._kata(f"  [{state}]")

    # -- hook, dioverride JalurSimulasi --------------------------------------
    def _sebelum_tunggu(self):
        """Dipanggil tepat sebelum belt mulai menunggu benda."""

    def _setelah_angkat(self):
        """Dipanggil setelah benda terangkat dari belt."""

    def _sebelum_lewatkan(self):
        """Dipanggil sebelum benda didorong lewat tanpa diambil."""

    # -- pre-flight ----------------------------------------------------------
    def siap(self):
        """Validasi konfigurasi, cek semua koneksi, home, naik ke Z aman."""
        self._lapor("pre-flight")
        masalah = validasi_konfigurasi(cetak=self.verbose)
        if masalah:
            raise rnv3.RNV3Error(
                f"{len(masalah)} titik di konfigurasi tidak terjangkau — "
                f"perbaiki dulu, jangan dijalankan")
        self.belt.periksa()
        if not self.kamera.ping():
            self._kata("PERINGATAN: kamera tidak menjawab ping — siklus "
                       "pertama kemungkinan besar gagal")
        self._kata("homing...")
        self._lapor("homing")
        self.bot.home()
        self.bot.move(z=Z_SAFE, f=F_SLOW)
        self.stop_event.clear()
        self._lapor("siap")

    # -- gerakan -------------------------------------------------------------
    def _ambil(self, d):
        """Turun ke benda, hisap, angkat kembali ke Z aman."""
        z = z_ambil(d.label)
        self._lapor("mengambil", benda_terakhir=str(d))
        self._kata(f"ambil {d} di Z={z:+.0f}")
        self.bot.move(z=Z_SAFE, f=F_FAST)
        self.bot.goto_xy(d.x_mm, d.y_mm, f=F_FAST)
        self.bot.move(z=z, f=F_SLOW)
        self.bot.vacuum(True)
        time.sleep(VACUUM_SETTLE)
        # Benda sekarang menggantung di bawah gripper -> tinggi jelajah harus
        # ikut tinggi benda. Z ini juga yang terbawa saat _taruh() menyeberang.
        self.bot.move(z=z_jelajah(d.label), f=F_SLOW)
        self.belt.detak()        # lengan tadi lama bergerak, jaga watchdog ESP

    def _taruh(self, d):
        """Pindah ke bak sesuai jenis benda, lepas, naik lagi."""
        tx, ty = profil(d.label)["taruh"]
        z = z_lepas(d.label)
        self._lapor("menaruh")
        self._kata(f"taruh {d.label} di ({tx:.0f}, {ty:.0f}) Z={z:+.0f}")
        self.bot.goto_xy(tx, ty, f=F_FAST)
        self.bot.move(z=z, f=F_SLOW)
        self.bot.vacuum(False)
        time.sleep(VACUUM_SETTLE)
        self.bot.move(z=Z_SAFE, f=F_SLOW)
        self.belt.detak()

    def _buang_siklus(self, hasil, pesan):
        """Benda tidak jadi diambil: dorong lewat supaya lini tidak macet."""
        self._kata(f"SKIP — {pesan}")
        self._lapor("melewatkan", hasil_terakhir=hasil)
        self._sebelum_lewatkan()
        self.belt.lewatkan_benda(timeout=TIMEOUT_LEWAT)
        return hasil

    def siklus(self):
        """Satu putaran penuh. Return salah satu konstanta Hasil."""
        self._lapor("menunggu benda")
        self._sebelum_tunggu()
        if not self.belt.maju_sampai_benda(timeout=TIMEOUT_BENDA):
            return Hasil.TIDAK_ADA_BENDA

        self._lapor("memotret")
        try:
            deteksi = self.kamera.deteksi()
        except kontrak.KameraError as e:
            return self._buang_siklus(Hasil.KAMERA_ERROR, f"kamera: {e}")

        if not deteksi:
            return self._buang_siklus(
                Hasil.KAMERA_KOSONG,
                "sensor bilang ada benda tapi kamera tidak melihat apa pun "
                "(cek pencahayaan / posisi kamera)")

        # Jangkauan dicek di tinggi ambil MASING-MASING jenis — koin dan botol
        # turun ke Z yang jauh berbeda, jadi satu angka tidak cukup.
        bisa, ditolak = [], []
        for d in deteksi:
            ok, alasan = d.terjangkau(z_ambil(d.label))
            (bisa if ok else ditolak).append(d if ok else (d, alasan))

        if not bisa:
            rincian = "; ".join(f"{d} -> {a}" for d, a in ditolak)
            return self._buang_siklus(Hasil.DILUAR_JANGKAUAN, rincian)
        if ditolak:
            self._kata(f"{len(ditolak)} deteksi diabaikan karena tak terjangkau")

        target = kontrak.paling_dekat(bisa, x=0.0, y=BELT_Y)
        try:
            self._ambil(target)
            self._setelah_angkat()
            self._taruh(target)
        except rnv3.RNV3Error as e:
            self._kata(f"GERAKAN GAGAL: {e}")
            self._lapor("robot error", hasil_terakhir=Hasil.ROBOT_ERROR)
            return Hasil.ROBOT_ERROR

        self.hitungan[target.label] = self.hitungan.get(target.label, 0) + 1
        return Hasil.SUKSES

    # -- loop ----------------------------------------------------------------
    def minta_berhenti(self):
        """Minta loop berhenti di AKHIR siklus sekarang.

        Bukan berhenti seketika: firmware tidak punya abort, G0 yang sudah
        dikirim pasti jalan sampai selesai. Untuk berhenti seketika tidak ada
        cara lain selain saklar power fisik.
        """
        self.stop_event.set()

    def jalankan(self, siklus=0):
        """Loop. siklus=0 berarti terus sampai minta_berhenti() atau Ctrl-C.

        Return dict statistik {hasil: jumlah}.
        """
        n = 0
        try:
            while siklus == 0 or n < siklus:
                if self.stop_event.is_set():
                    self._kata("diminta berhenti")
                    break
                n += 1
                self._kata(f"\n--- siklus {n}"
                           f"{'/' + str(siklus) if siklus else ''} ---")
                with self._kunci:
                    self._status["siklus"] = n
                hasil = self.siklus()
                self.statistik[hasil] = self.statistik.get(hasil, 0) + 1
                self._lapor("selesai siklus", hasil_terakhir=hasil)
                self._kata(f"hasil: {hasil}")
                # Benda yang lewat saat lengan sibuk tetap tercatat di sini.
                # Berguna buat tahu apakah belt mengirim lebih cepat dari
                # kemampuan lengan.
                tertinggal = self.bot.ambil_event_sensor()
                if tertinggal:
                    self._kata(f"({len(tertinggal)} event sensor terjadi "
                               f"saat lengan sibuk)")
        except KeyboardInterrupt:
            self._kata("\ndihentikan pemakai")
        finally:
            self.selesai()
        return self.statistik

    def selesai(self):
        """Matikan belt, lepas vakum, parkir rendah. Aman dipanggil kapan saja."""
        self._lapor("merapikan")
        try:
            self.belt.berhenti()
            self.bot.vacuum(False)
            if self.bot.position_known:
                self._kata(f"parkir di Z={Z_PARKIR:.0f}")
                self.bot.move(z=Z_PARKIR, f=F_SLOW)
            else:
                self._kata("posisi tidak diketahui — TIDAK parkir otomatis. "
                           "Home dulu sebelum mematikan daya.")
        except rnv3.RNV3Error as e:
            self._kata(f"gagal merapikan: {e}")
        self._lapor("berhenti")

    def _kata(self, msg):
        if self.verbose:
            print(msg)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.selesai()


class JalurSimulasi(Jalur):
    """Jalur di atas simulator: benda 'datang' dan 'pergi' secara terjadwal.

    Semua logika yang diuji tetap logika sebenarnya — yang dipalsukan cuma
    kedatangan benda di sensor, lewat penjadwalan deterministik (tanpa thread,
    jadi hasil tes selalu sama). Bekerja untuk kedua transport konveyor.
    """

    POLL_DATANG = 3          # benda tiba setelah 3 kali poll
    POLL_LEWAT = 2           # zona kosong lagi setelah 2 kali poll

    def _jadwalkan(self, ada, poll):
        b = self.belt
        if isinstance(b, mod_konveyor.KonveyorESP):
            b.esp.sim.jadwalkan_benda(ada, poll)
        else:
            # Simulator robot pakai keadaan PIN: True = HIGH = "ON" = KOSONG.
            b.bot.sim.jadwalkan_sensor(b.sensor, not ada, poll)

    def _set_benda(self, ada):
        b = self.belt
        if isinstance(b, mod_konveyor.KonveyorESP):
            b.esp.sim.set_benda(ada)
        else:
            b.bot.sim.set_sensor(b.sensor, not ada)

    def _sebelum_tunggu(self):
        self._jadwalkan(True, self.POLL_DATANG)

    def _setelah_angkat(self):
        # Lengan sudah mengangkat benda dari belt -> sensor bebas seketika.
        self._set_benda(False)

    def _sebelum_lewatkan(self):
        self._jadwalkan(False, self.POLL_LEWAT)


# ---------------------------------------------------------------------------
# PERAKITAN — satu tempat yang tahu cara menyusun semua lapisan
# ---------------------------------------------------------------------------
def rakit(sim=False, pakai_esp=False, port=PORT, esp_port=ESP_PORT,
          kamera_host="127.0.0.1", kamera_port=kontrak.PORT_DEFAULT,
          verbose=True, on_status=None, kosong=0.0, jauh=0.0, seed=None):
    """Bangun Jalur lengkap. Dipakai CLI di bawah DAN backend.py nanti.

    Return (jalur, penutup) — panggil penutup() untuk menutup port.
    """
    penutup = []

    if sim:
        import simulasi
        bot = simulasi.robot_palsu(verbose=False)
        kamera = kontrak.SumberPalsu(
            z_pick=z_ambil(JENIS_DEFAULT),
            x=(-BELT_X_LEBAR, BELT_X_LEBAR), y=(BELT_Y - 15, BELT_Y + 15),
            peluang_kosong=kosong, peluang_jauh=jauh, seed=seed,
            label=list(JENIS))
        if pakai_esp:
            import esp as mod_esp
            e = mod_esp.esp_palsu()
            belt = mod_konveyor.KonveyorESP(e, settle=BELT_SETTLE, verbose=verbose)
        else:
            belt = mod_konveyor.KonveyorLG(
                bot, channel=LG_BELT, sensor=SENSOR_BELT,
                settle=BELT_SETTLE, verbose=verbose)
        kelas = JalurSimulasi
    else:
        bot = rnv3.RNV3(port, verbose=False)
        penutup.append(bot.close)
        kamera = kontrak.SumberTCP(host=kamera_host, port=kamera_port)
        if pakai_esp:
            import esp as mod_esp
            e = mod_esp.ESP(esp_port)
            penutup.append(e.close)
            belt = mod_konveyor.KonveyorESP(e, settle=BELT_SETTLE, verbose=verbose)
        else:
            belt = mod_konveyor.KonveyorLG(
                bot, channel=LG_BELT, sensor=SENSOR_BELT,
                settle=BELT_SETTLE, verbose=verbose)
        kelas = Jalur

    jalur = kelas(bot, kamera, belt, verbose=verbose, on_status=on_status)

    def tutup():
        for fn in reversed(penutup):
            try:
                fn()
            except Exception:                            # noqa: BLE001
                pass

    return jalur, tutup


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(
        description="Middleware konveyor + kamera + lengan RNV3.")
    p.add_argument("--sim", action="store_true",
                   help="pakai simulator robot + kamera palsu, tanpa hardware")
    p.add_argument("--esp", action="store_true",
                   help="belt dikontrol ESP32, bukan output LG board robot")
    p.add_argument("--port", default=PORT, help=f"port serial robot (default {PORT})")
    p.add_argument("--esp-port", default=ESP_PORT,
                   help=f"port serial ESP32 (default {ESP_PORT})")
    p.add_argument("--kamera-host", default="127.0.0.1")
    p.add_argument("--kamera-port", type=int, default=kontrak.PORT_DEFAULT)
    p.add_argument("--siklus", type=int, default=0,
                   help="berapa siklus; 0 = terus sampai Ctrl-C")
    p.add_argument("--diam", action="store_true", help="kurangi cetakan")
    p.add_argument("--cek", action="store_true",
                   help="cuma validasi konfigurasi, tidak menyentuh alat apa pun")
    # khusus --sim, buat menguji cabang penanganan error
    p.add_argument("--kosong", type=float, default=0.0,
                   help="[sim] peluang kamera tidak melihat apa pun")
    p.add_argument("--jauh", type=float, default=0.0,
                   help="[sim] peluang kamera melapor titik di luar jangkauan")
    p.add_argument("--seed", type=int, default=None, help="[sim] seed acak")
    a = p.parse_args(argv)

    if a.cek:
        return 1 if validasi_konfigurasi() else 0

    jalur, tutup = rakit(
        sim=a.sim, pakai_esp=a.esp, port=a.port, esp_port=a.esp_port,
        kamera_host=a.kamera_host, kamera_port=a.kamera_port,
        verbose=not a.diam, kosong=a.kosong, jauh=a.jauh, seed=a.seed)

    try:
        jalur.siap()
        statistik = jalur.jalankan(siklus=a.siklus)
    finally:
        tutup()

    print("\n--- ringkasan ---")
    total = sum(statistik.values())
    for nama, jumlah in sorted(statistik.items(), key=lambda kv: -kv[1]):
        print(f"  {jumlah:4d}  {nama}")
    print(f"  {total:4d}  total")
    if any(jalur.hitungan.values()):
        print("  tersortir:", ", ".join(
            f"{k}={v}" for k, v in jalur.hitungan.items() if v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
