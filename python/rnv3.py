"""
Kontrol Robot Arm RNV3 lewat serial — fokus gerakan titik koordinat kartesian X/Y/Z.

Semua konstanta & rumus di file ini diambil langsung dari firmware resmi, bukan
dari tebakan atau dari video:
    ../firmware/v2_01/config.h            (parameter mesin)
    ../firmware/v2_01/robotGeometry.cpp   (inverse kinematics)
    ../firmware/v2_01/interpolation.cpp   (batas jangkauan + profil kecepatan)
    ../firmware/v2_01/command.cpp         (format perintah serial)

robotGeometry.cpp IDENTIK di firmware v2_01 dan v2_02, jadi semua perhitungan
koordinat & jangkauan di file ini valid untuk kedua versi.

Butuh: pip install pyserial

Menjalankan di Windows (disarankan -- WSL tidak melihat USB tanpa usbipd):
    python.exe -m pip install pyserial      # sekali saja
    python.exe pick_place.py                # file boleh tetap di filesystem WSL
"""

import collections
import math
import re
import time
import warnings

import serial

# ---------------------------------------------------------------------------
# GEOMETRI ROBOT (config.h)
# Bagian ini identik di semua varian config.h yang ada, jadi aman.
# ---------------------------------------------------------------------------
LOW_SHANK = 140.0          # panjang lengan bawah (mm)
HIGH_SHANK = 140.0         # panjang lengan atas (mm)
EE_OFFSET = 77.0           # bearing lengan atas -> tengah end effector (mm)

Z_MIN = -140.0             # ujung tool paling bawah
Z_MAX = LOW_SHANK + 70.0   # = 210.0
RAIL_LENGTH = 355.0        # panjang rel; 0 = ujung kiri

# Robot kelompok ini PAKAI rel, jadi sumbu E aktif.
# Cara verifikasi di robot sungguhan: kirim M119, lihat balasannya.
#   ENDSTOP: [X:0 Y:0 Z:0 E:0]  -> ada "E:", berarti rel aktif
#   ENDSTOP: [X:0 Y:0 Z:0]      -> tidak ada "E:", firmware di-compile RAIL=false
RAIL_ENABLED = True

# Batas sudut antar-lengan -> jadi batas radius kerja.
_COS_MIN = 0.791436948
_COS_MAX = -0.774944489
R_MIN = math.sqrt(LOW_SHANK**2 + HIGH_SHANK**2 - 2 * LOW_SHANK * HIGH_SHANK * _COS_MIN)
R_MAX = math.sqrt(LOW_SHANK**2 + HIGH_SHANK**2 - 2 * LOW_SHANK * HIGH_SHANK * _COS_MAX)
# -> R_MIN = 90.42 mm, R_MAX = 263.78 mm

# Posisi home setelah G28 (INITIAL_X/Y/Z/E0 di config.h)
HOME_X = 0.0
HOME_Y = HIGH_SHANK + EE_OFFSET   # = 217.0
HOME_Z = 138.0
HOME_E = 0.0

# Rentang servo gripper (MIN_SERVO/MAX_SERVO di config.h).
# MIN = menutup, MAX = membuka.
#
# PENTING: nilai ini BERBEDA antar varian firmware!
#   firmware/v2_02-pembina/config.h    -> 115 / 155  <- DIPAKAI (robot kalian)
#   firmware/v2_01/config.h            -> 95 / 160
#   firmware/v2_02/config.h            -> 95 / 160
#   firmware/v2_01-interface/config.h  -> 27 / 115
#
# Dipakai nilai PEMBINA karena dia yang punya robot fisiknya dan nilai itu
# hasil kalibrasi dia. Firmware hanya menerima sudut di dalam rentang ini --
# kirim 95 saat MIN_SERVO=115 akan DITOLAK dengan pesan "Nilai cmd.valueB di
# luar rentang", dan gripper tidak bergerak sama sekali.
#
# Robot juga mencetak nilai aslinya tiap kali nyala:
#     SETUP GRIPPER SERVO : MIN 115 MAX 155
# Cocokkan dua angka di bawah dengan pesan itu kalau firmware diganti.
SERVO_CLOSED = 115
SERVO_OPEN = 155

# Faktor kecepatan otomatis saat F < 5, dari interpolation.cpp:
#   v2_01: v = sqrt(jarak) * 10          (hardcoded)
#   v2_02: v = sqrt(jarak) * SPEED_DEFAULT, dengan SPEED_DEFAULT = 8
SPEED_AUTO_FACTOR = {"v2_01": 10.0, "v2_02": 8.0}
SPEED_FLOOR = 5.0          # firmware menaikkan apa pun di bawah ini jadi 5 mm/s


# ---------------------------------------------------------------------------
# MENEMUKAN PORT ROBOT
# Nomor COM berubah tiap robot dicolok ke lubang USB yang berbeda, jadi lebih
# andal mencari chipnya daripada menuliskan nomornya.
# ---------------------------------------------------------------------------
# CH340 pada board robot. VID 1A86 = QinHeng, PID 7523 = CH340.
VID_PID_ROBOT = [(0x1A86, 0x7523)]


def cari_port(diam=False):
    """Cari port robot otomatis. Return nama port, atau None kalau tidak ada.

    Kalau ada lebih dari satu kandidat, yang pertama dipakai dan sisanya
    disebutkan supaya bisa dipilih manual dengan --port.
    """
    try:
        import serial.tools.list_ports as lp
    except ImportError:
        return None

    semua = list(lp.comports())
    cocok = [p for p in semua
             if (p.vid, p.pid) in VID_PID_ROBOT
             or "CH340" in (p.description or "").upper()]
    if not cocok:
        if not diam:
            print("  tidak menemukan robot (chip CH340) di port mana pun.")
            if semua:
                print("  port yang terlihat: "
                      + ", ".join(f"{p.device} ({p.description})" for p in semua))
            print("  - pastikan kabel USB-nya kabel DATA, bukan kabel cas")
            print("  - pastikan tombol power robot sudah ON")
        return None
    if len(cocok) > 1 and not diam:
        print(f"  ada {len(cocok)} kandidat: "
              + ", ".join(p.device for p in cocok)
              + f" -- dipakai {cocok[0].device}, pakai --port untuk memilih")
    return cocok[0].device


# ---------------------------------------------------------------------------
# VALIDASI JANGKAUAN
# Replika persis Interpolation::isAllowedPosition() di interpolation.cpp.
# Pakai ini SEBELUM kirim perintah, biar ketahuan titiknya valid atau tidak
# tanpa harus nunggu robot balas "LIMIT REACHED".
# ---------------------------------------------------------------------------
def check_reach(x, y, z, e=None):
    """Cek titik (x, y, z) terjangkau atau tidak.

    Return (True, "") kalau valid, atau (False, alasan) kalau di luar jangkauan.
    """
    r_ee = math.hypot(x, y)
    if r_ee == 0:
        return False, "X dan Y tidak boleh dua-duanya 0 (pusat rotasi, bagi nol)"

    # Radius efektif setelah dikurangi panjang end effector.
    r = r_ee - EE_OFFSET
    module = math.sqrt(r * r + z * z)

    if module > R_MAX:
        return False, f"terlalu jauh: radius {module:.1f} mm > R_MAX {R_MAX:.1f} mm"
    if module < R_MIN:
        return False, f"terlalu dekat: radius {module:.1f} mm < R_MIN {R_MIN:.1f} mm"
    if z < Z_MIN:
        return False, f"Z={z:.1f} di bawah Z_MIN {Z_MIN:.1f}"
    if z > Z_MAX:
        return False, f"Z={z:.1f} di atas Z_MAX {Z_MAX:.1f}"
    if e is not None and RAIL_ENABLED and not (0.0 <= e <= RAIL_LENGTH):
        return False, f"E={e:.1f} di luar rel 0..{RAIL_LENGTH:.0f}"

    return True, ""


# ---------------------------------------------------------------------------
# BATAS FISIK  -- yang TIDAK dicek firmware
#
# check_reach() di atas adalah replika persis isAllowedPosition() firmware:
# hanya radius dan Z. Firmware TIDAK pernah memeriksa sudut sendi, padahal
# sudut itulah yang dibatasi mekanik. Dua celah nyata:
#
#  1. hypot(x, y) tidak membedakan DEPAN dan BELAKANG. Titik di belakang badan
#     robot lolos pemeriksaan, padahal lengan akan beradu dengan badannya
#     sendiri. Lebih buruk lagi, rot = asin(x / hypot(x,y)) memetakan titik
#     belakang ke sudut DEPAN yang keliru -- lengan bergerak ke tempat lain
#     daripada yang diminta.
#  2. Rentang putar base dibatasi mekanik, tapi asin() mengizinkan -90..+90.
#
# Nilai di bawah sengaja konservatif. Kalibrasi sendiri dengan tombol ";" di
# jog.py: jog pelan sampai hampir mentok, tekan ";", batasnya terekam.
# ---------------------------------------------------------------------------
import json
import os

BERKAS_BATAS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "batas-fisik.json")

BATAS_FISIK = {
    "y_min": 100.0,        # lengan wajib tetap di DEPAN badan robot
    "rot_maks": 75.0,      # derajat, rentang putar base kiri/kanan
    "x_min": None, "x_maks": None,     # diisi hasil kalibrasi (None = tak dibatasi)
    "y_maks": None,
    "z_min": None, "z_maks": None,
    "e_min": None, "e_maks": None,
}

# True kalau BATAS_FISIK berisi hasil ukur nyata, False kalau masih tebakan
# konservatif di atas. Dibaca entry point supaya operator tahu gerakannya
# divalidasi terhadap apa. Diset oleh muat_batas().
BATAS_TERUKUR = False


def muat_batas(berkas=None):
    """Baca batas hasil kalibrasi kalau ada. Dipanggil otomatis saat impor.

    berkas=None -> pakai BERKAS_BATAS saat DIPANGGIL, bukan saat fungsi ini
    didefinisikan. Kalau dipakai sebagai nilai default argumen, nilainya
    terkunci saat impor dan tidak bisa dialihkan (mis. oleh pengujian).
    """
    global BATAS_TERUKUR
    berkas = berkas or BERKAS_BATAS
    try:
        with open(berkas, encoding="utf-8") as f:
            BATAS_FISIK.update(json.load(f))
        BATAS_TERUKUR = True
        return True
    except (OSError, ValueError):
        BATAS_TERUKUR = False
        return False


def simpan_batas(berkas=None):
    """Tulis batas hasil kalibrasi supaya bertahan antar sesi.

    berkas=None -> pakai BERKAS_BATAS saat DIPANGGIL (lihat muat_batas).
    """
    berkas = berkas or BERKAS_BATAS
    with open(berkas, "w", encoding="utf-8") as f:
        json.dump(BATAS_FISIK, f, indent=2, ensure_ascii=False)
    return berkas


def sudut_sendi(x, y, z):
    """Sudut ketiga sendi (derajat) untuk titik kartesian, seperti firmware.

    Port dari RobotGeometry::calculateGrad() di robotGeometry.cpp.
    Return (rot_base, bahu, siku) atau None kalau tidak terhitung.
    """
    r_ee = math.hypot(x, y)
    if r_ee == 0:
        return None
    rrot = r_ee - EE_OFFSET
    rside = math.hypot(rrot, z)
    if rside == 0:
        return None
    lo2, hi2 = LOW_SHANK ** 2, HIGH_SHANK ** 2
    try:
        rot = math.asin(x / r_ee)
        siku = math.pi - math.acos((lo2 + hi2 - rside ** 2)
                                   / (2 * LOW_SHANK * HIGH_SHANK))
        bagi = (lo2 - hi2 + rside ** 2) / (2 * LOW_SHANK * rside)
        if z > 0:
            bahu = math.acos(z / rside) - math.acos(bagi)
        else:
            bahu = math.pi - math.asin(rrot / rside) - math.acos(bagi)
    except ValueError:
        return None
    return (math.degrees(rot), math.degrees(bahu), math.degrees(siku + bahu))


def check_fisik(x, y, z, e=None):
    """Batas MEKANIS di luar yang dicek firmware. Return (ok, alasan)."""
    if y <= 0:
        return False, ("Y<=0 berarti di BELAKANG badan robot -- lengan akan "
                       "beradu dengan badannya sendiri, dan kinematikanya "
                       "salah petakan")
    ymin = BATAS_FISIK.get("y_min")
    if ymin is not None and y < ymin:
        return False, f"Y={y:.1f} di bawah batas fisik y_min={ymin:.1f}"

    sd = sudut_sendi(x, y, z)
    if sd is None:
        return False, "sudut sendi tidak terhitung di titik ini"
    rot = sd[0]
    rmaks = BATAS_FISIK.get("rot_maks")
    if rmaks is not None and abs(rot) > rmaks:
        return False, (f"putar base {rot:+.1f}deg melewati batas fisik "
                       f"+-{rmaks:.0f}deg")

    for sumbu, nilai in (("x", x), ("y", y), ("z", z), ("e", e)):
        if nilai is None:
            continue
        lo = BATAS_FISIK.get(f"{sumbu}_min")
        hi = BATAS_FISIK.get(f"{sumbu}_maks")
        if lo is not None and nilai < lo:
            return False, f"{sumbu.upper()}={nilai:.1f} di bawah batas rekaman {lo:.1f}"
        if hi is not None and nilai > hi:
            return False, f"{sumbu.upper()}={nilai:.1f} di atas batas rekaman {hi:.1f}"
    return True, ""


def check_aman(x, y, z, e=None):
    """Gabungan: batas firmware DAN batas fisik. Inilah yang dipakai move()."""
    ok, why = check_reach(x, y, z, e)
    if not ok:
        return False, why
    return check_fisik(x, y, z, e)


muat_batas()
if not BATAS_TERUKUR:
    # Dulu kegagalan ini ditelan tanpa suara, jadi setiap run diam-diam
    # memvalidasi gerakan terhadap TEBAKAN dan tidak ada yang tahu kalibrasi
    # belum pernah dijalankan. Peringatan ini tetap bisa dibungkam lewat
    # warnings.filterwarnings() kalau memang disengaja.
    warnings.warn(
        f"batas fisik belum pernah diukur: '{os.path.basename(BERKAS_BATAS)}' "
        f"tidak ada, jadi BATAS_FISIK memakai tebakan konservatif "
        f"(y_min={BATAS_FISIK['y_min']:.0f}, "
        f"rot_maks={BATAS_FISIK['rot_maks']:.0f}). Ukur di robot nyata lalu "
        f"simpan_batas() supaya validasi memakai angka yang sebenarnya.",
        RuntimeWarning, stacklevel=2)


def xy_limits(z):
    """Rentang radius XY yang valid pada ketinggian Z tertentu.

    Berguna buat tahu "sejauh apa robot bisa menjangkau" — jangkauan robot ini
    TIDAK tetap, dia berubah tergantung Z.
    Return (r_min, r_max) dalam mm, atau None kalau Z-nya mustahil.
    """
    if not (Z_MIN <= z <= Z_MAX):
        return None
    outer = R_MAX**2 - z * z
    if outer < 0:
        return None
    r_max = math.sqrt(outer) + EE_OFFSET
    inner = R_MIN**2 - z * z
    r_min = (math.sqrt(inner) + EE_OFFSET) if inner > 0 else EE_OFFSET
    return r_min, r_max


def check_path(start, target, steps=120):
    """Cek SELURUH JALUR LURUS dari start ke target, bukan cuma titik tujuan.

    Ini wajib. Firmware memanggil isAllowedPosition() di SETIAP langkah
    interpolasi (interpolation.cpp baris ~167), bukan sekali di tujuan. Jadi
    gerakan dari titik valid ke titik valid masih bisa gagal kalau garis
    lurusnya melewati "lubang mati" di dekat badan robot (radius < R_MIN).

    Yang terjadi kalau jalur ditolak: firmware MEMBATALKAN gerakan di tengah
    jalan, membekukan posisi di titik terakhir yang valid, lalu TETAP kirim
    'ok'. Jadi tanpa cek ini, Python mengira sudah sampai padahal robot
    berhenti di tempat lain -- posisi desync.

    start / target = (x, y, z). Return (True, "", None) atau
    (False, alasan, t) dengan t = fraksi 0..1 tempat jalur mulai ditolak.
    """
    for i in range(steps + 1):
        t = i / steps
        x = start[0] + t * (target[0] - start[0])
        y = start[1] + t * (target[1] - start[1])
        z = start[2] + t * (target[2] - start[2])
        ok, why = check_aman(x, y, z)
        if not ok:
            return False, f"jalur ditolak di t={t:.3f} (X{x:.1f} Y{y:.1f} Z{z:.1f}): {why}", t
    return True, "", None


def estimate_speed(distance, firmware="v2_01"):
    """Perkiraan kecepatan (mm/s) yang dipakai firmware kalau F tidak diisi.

    Bukan angka yang dikirim ke robot — ini cuma buat memprediksi, misalnya
    untuk menghitung timeout atau memperkirakan lama gerakan.
    """
    factor = SPEED_AUTO_FACTOR.get(firmware, 10.0)
    return max(SPEED_FLOOR, math.sqrt(max(distance, 0.0)) * factor)


# ---------------------------------------------------------------------------
# EVENT SENSOR ASINKRON
# Firmware v2_02 memancarkan "S1 ON" / "S1 OFF" dari loop() setiap ada
# perubahan tepi di IO1/IO2/IO3, TANPA diminta -- lihat
# robotRNV3_v2_02.ino:177-199. Baris ini bisa nyelip kapan saja, termasuk di
# tengah balasan perintah lain, jadi harus dikenali dan disisihkan.
#
# Formatnya polos tanpa prefix: Logger::logINFO() menulis pesan apa adanya
# (prefix "INFO: " di logger.cpp sengaja dikomentari developer-nya).
# Tidak bentrok dengan balasan M105 yang berbentuk
# "SENSOR STATUS: [S1:ON S2:OFF S3:ON]".
# ---------------------------------------------------------------------------
EVENT_SENSOR = re.compile(r"^(S\d)\s+(ON|OFF)$")

# Jeda sebelum membuang sisa balasan setelah timeout. Memberi kesempatan baris
# yang masih di jalan untuk tiba, supaya ikut terbuang dan tidak mengotori
# perintah berikutnya. Lihat RNV3._timeout_error().
SETTLE_AFTER_TIMEOUT = 0.3

# Arti status sensor. Input IO1-3 active-LOW (ada pull-up di board), dan
# firmware melaporkan keadaan pin apa adanya: pin HIGH (idle, tidak ada apa-apa
# di depan sensor) dilaporkan "ON", pin ditarik ke ground oleh sensor NPN yang
# kena benda dilaporkan "OFF". Jadi memang terbalik dari dugaan pertama.
# Contoh resmi python/contoh-sensor/Program input.py juga menunggu "S1 OFF".
SENSOR_ADA_BENDA = "OFF"
SENSOR_KOSONG = "ON"


class RNV3Error(RuntimeError):
    pass


class RNV3:
    """Koneksi serial ke robot RNV3.

    Protokolnya: kirim string perintah diakhiri '\\r' (BUKAN '\\n' --
    Command::handleGcode() mengabaikan '\\n' dan hanya eksekusi saat ketemu '\\r').
    Robot balas satu atau beberapa baris info, lalu 'ok' kalau perintah selesai.
    """

    def __init__(self, port, baud=115200, timeout=2.0, verbose=True, detect=True):
        self.port = port
        self.verbose = verbose
        self.ser = serial.Serial(port, baud, timeout=timeout)

        # Posisi dilacak di sisi Python supaya bisa divalidasi sebelum kirim.
        self.x, self.y, self.z, self.e = HOME_X, HOME_Y, HOME_Z, HOME_E
        self.relative = False
        self.position_known = True
        self.firmware = "v2_01"
        self.boot_lines = []
        self.servo_range_reported = None
        self.rail_reported = None
        self._origin_shifted = False
        # Event sensor asinkron yang tertangkap send(). Lihat EVENT_SENSOR.
        self.events_sensor = collections.deque(maxlen=200)

        # Membuka port serial MERESET Arduino. Bootloader Mega butuh ~1,5-2 s
        # sebelum sketch jalan, baru pesan boot keluar. Kalau kita cuma
        # reset_input_buffer() setelah jeda pendek, pesan boot tiba SETELAH itu
        # dan mengotori balasan perintah pertama.
        time.sleep(2.5)
        self._drain_boot()

        if detect:
            self.detect_firmware()

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.ser.is_open:
            self.ser.close()

    # -- boot ---------------------------------------------------------------
    def _drain_boot(self, quiet=0.6, limit=6.0):
        """Baca pesan boot sampai port sunyi `quiet` detik (batas `limit`).

        Firmware mencetak saat nyala (lihat setup() di robotRNV3_v2_0x.ino):
            SETUP GRIPPER SERVO : MIN 115 MAX 155
            RAIL ON          (atau RAIL OFF)
            READY CALIBRATION

        Dua yang pertama adalah sumber kebenaran soal konfigurasi firmware yang
        BENAR-BENAR ter-upload ke robot, jadi dipakai untuk memeriksa apakah
        konstanta di file ini cocok.
        """
        mulai = time.monotonic()
        terakhir = time.monotonic()
        while time.monotonic() - mulai < limit:
            raw = self.ser.readline()
            if raw:
                baris = raw.decode("utf-8", errors="replace").strip()
                if baris:
                    self.boot_lines.append(baris)
                    if self.verbose:
                        print(f"<< {baris}")
                terakhir = time.monotonic()
            elif time.monotonic() - terakhir >= quiet:
                break
        self._periksa_boot()
        return self.boot_lines

    _SERVO_RE = re.compile(r"SETUP GRIPPER SERVO\s*:\s*MIN\s*(\d+)\s*MAX\s*(\d+)",
                           re.I)

    def _periksa_boot(self):
        """Bandingkan pesan boot robot dengan konstanta di file ini."""
        for baris in self.boot_lines:
            m = self._SERVO_RE.search(baris)
            if m:
                self.servo_range_reported = (int(m.group(1)), int(m.group(2)))
            atas = baris.upper()
            if "RAIL ON" in atas:
                self.rail_reported = True
            elif "RAIL OFF" in atas:
                self.rail_reported = False

        r = self.servo_range_reported
        if r and r != (SERVO_CLOSED, SERVO_OPEN):
            print("\n" + "!" * 68)
            print("PERINGATAN: rentang servo gripper TIDAK COCOK")
            print(f"  robot melaporkan : MIN {r[0]}  MAX {r[1]}")
            print(f"  rnv3.py memakai  : MIN {SERVO_CLOSED}  MAX {SERVO_OPEN}")
            print("  Firmware hanya menerima sudut di dalam rentangnya sendiri;")
            print("  di luar itu perintah gripper DITOLAK dan servo tidak bergerak.")
            print(f"  Perbaiki: ubah SERVO_CLOSED={r[0]} dan SERVO_OPEN={r[1]}")
            print("  di bagian atas rnv3.py.")
            print("!" * 68 + "\n")

        if self.rail_reported is not None and self.rail_reported != RAIL_ENABLED:
            print("\n" + "!" * 68)
            print("PERINGATAN: status rel TIDAK COCOK")
            print(f"  robot melaporkan : RAIL {'ON' if self.rail_reported else 'OFF'}")
            print(f"  rnv3.py memakai  : RAIL_ENABLED = {RAIL_ENABLED}")
            print(f"  Perbaiki: ubah RAIL_ENABLED = {self.rail_reported} "
                  "di bagian atas rnv3.py.")
            print("!" * 68 + "\n")

    # -- primitif -----------------------------------------------------------
    def _timeout_error(self, cmd, timeout):
        """Siapkan RNV3Error timeout SETELAH membersihkan buffer masuk.

        CATATAN.md sempat mengklaim masalah ini sudah ditutup oleh "send()
        baca per baris". Itu tidak cukup: barisnya memang utuh, tapi 'ok' yang
        datang terlambat tetap mengendap di buffer OS dan akan dibaca send()
        BERIKUTNYA sebagai balasan perintah yang baru. Sejak saat itu setiap
        balasan bergeser satu perintah dan tidak pernah sinkron lagi -- dan
        robot tetap menjawab "ok", jadi tidak ada yang tampak salah.

        Jeda pendek dulu supaya sisa balasan yang masih di jalan ikut terbuang,
        bukan hanya yang sudah sampai.
        """
        time.sleep(SETTLE_AFTER_TIMEOUT)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            # Port sudah mati -> tidak ada yang perlu dibersihkan. Error
            # timeout-nya tetap harus naik ke pemanggil.
            pass
        return RNV3Error(f"timeout {timeout}s menunggu 'ok' untuk: {cmd}")

    def send(self, cmd, wait=True, timeout=30.0):
        """Kirim satu perintah, kumpulkan balasan sampai 'ok'.

        Return list baris balasan (tanpa baris 'ok').
        Raise RNV3Error kalau robot lapor ERROR, atau kalau 'ok' tidak datang
        sebelum timeout (ini yang bikin contoh resmi bisa nge-hang selamanya).
        """
        self.ser.write(cmd.encode("ascii") + b"\r")
        if self.verbose:
            print(f">> {cmd}")
        if not wait:
            return []

        lines, deadline, error = [], time.monotonic() + timeout, None
        while True:
            raw = self.ser.readline()
            if not raw:
                if time.monotonic() > deadline:
                    raise self._timeout_error(cmd, timeout)
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if self.verbose:
                print(f"<< {line}")
            if line == "ok":
                break
            # Event sensor asinkron: SISIHKAN, jangan dicampur ke balasan.
            # Kalau ikut masuk `lines`, event ini menyamar jadi balasan
            # perintah yang sedang jalan -- dan move() membuang balasannya,
            # jadi event-nya hilang permanen.
            cocok = EVENT_SENSOR.match(line)
            if cocok:
                self.events_sensor.append(
                    (cocok.group(1), cocok.group(2), time.monotonic()))
                continue
            if line.startswith("ERROR:"):
                error = line          # tetap tunggu 'ok', baru raise
            lines.append(line)
            if time.monotonic() > deadline:
                raise self._timeout_error(cmd, timeout)

        if error:
            raise RNV3Error(f"{error}  (perintah: {cmd})")
        return lines

    def detect_firmware(self):
        """Tebak versi firmware dengan mencoba M105 (cuma ada di v2_02).

        Nggak perlu tanya user: v2_02 balas "SENSOR STATUS: [...]", sedangkan
        v2_01 balas "ERROR: COMMAND NOT RECOGNIZED". Dua-duanya tetap kirim 'ok',
        jadi aman dipanggil di awal.
        """
        try:
            lines = self.send("M105", timeout=10.0)
        except RNV3Error:
            self.firmware = "v2_01"
        else:
            self.firmware = "v2_02" if any("SENSOR STATUS" in l for l in lines) else "v2_01"
        if self.verbose:
            print(f"-- firmware terdeteksi: {self.firmware}")
        return self.firmware

    # -- gerakan ------------------------------------------------------------
    def home(self, timeout=90.0):
        """G28 — kalibrasi/homing. Wajib dijalankan sebelum gerakan apa pun.

        Catatan dari video: geser lengan sedikit ke depan pakai tangan dulu.
        Kalau mentok di endstop belakang, hasil homing kurang presisi.
        """
        self.send("G28", timeout=timeout)
        self.x, self.y, self.z, self.e = HOME_X, HOME_Y, HOME_Z, HOME_E
        self.relative = False
        self.position_known = True
        self._origin_shifted = False   # G28 mereset offset G92 di firmware

    def move(self, x=None, y=None, z=None, e=None, f=None, check=True):
        """G0 — gerak linear ke titik kartesian. Sumbu yang None = dibiarkan.

        f = kecepatan dalam mm/s, maksimal praktis ~500.
        Kalau f < 5 (termasuk 0 atau None), firmware hitung sendiri:
            v2_01: v = sqrt(jarak) * 10
            v2_02: v = sqrt(jarak) * 8
        dan apa pun di bawah 5 mm/s dinaikkan jadi 5. Lihat estimate_speed().

        Sumbu yang digerakkan dalam satu perintah jalan BERSAMAAN (interpolasi
        linear), jadi urutan huruf di string tidak berpengaruh.
        """
        tx = self.x if x is None else (self.x + x if self.relative else x)
        ty = self.y if y is None else (self.y + y if self.relative else y)
        tz = self.z if z is None else (self.z + z if self.relative else z)
        te = self.e if e is None else (self.e + e if self.relative else e)

        if check:
            ok, why = check_aman(tx, ty, tz, te)
            if not ok:
                raise RNV3Error(
                    f"titik ditolak sebelum dikirim -> X{tx:.2f} Y{ty:.2f} "
                    f"Z{tz:.2f} E{te:.2f}: {why}"
                )
            if not self.position_known:
                raise RNV3Error(
                    "posisi robot tidak diketahui (gerakan sebelumnya gagal). "
                    "Jalankan position() untuk sinkron ulang, atau home()."
                )
            ok, why, _ = check_path((self.x, self.y, self.z), (tx, ty, tz))
            if not ok:
                raise RNV3Error(
                    f"{why}\n  -> pecah gerakan ini jadi beberapa langkah, "
                    f"atau naikkan Z dulu supaya jalurnya memutar lubang mati"
                )

        parts = ["G0"]
        if x is not None:
            parts.append(f"X{x:.2f}")
        if y is not None:
            parts.append(f"Y{y:.2f}")
        if z is not None:
            parts.append(f"Z{z:.2f}")
        if e is not None:
            parts.append(f"E{e:.2f}")
        if f is not None:
            parts.append(f"F{f:.2f}")

        try:
            self.send(" ".join(parts))
        except RNV3Error:
            # Firmware bisa membatalkan gerakan di tengah jalan -> posisi fisik
            # tidak lagi sama dengan yang kita lacak.
            self.position_known = False
            raise
        self.x, self.y, self.z, self.e = tx, ty, tz, te

    def goto_xy(self, x, y, f=None):
        """Pindah di bidang XY saja, ketinggian Z dipertahankan."""
        self.move(x=x, y=y, f=f)

    def dwell(self, seconds):
        """G4 — tunggu. Robot balas 'ok' setelah waktunya habis."""
        self.send(f"G4 S{seconds:g}", timeout=seconds + 15.0)

    def absolute(self):
        """G90 — koordinat absolut (default)."""
        self.send("G90")
        self.relative = False

    def incremental(self):
        """G91 — koordinat relatif: nilai yang dikirim DITAMBAHKAN ke posisi kini."""
        self.send("G91")
        self.relative = True

    def set_origin(self, x=None, y=None, z=None, e=None):
        """G92 — tetapkan posisi fisik sekarang sebagai nilai koordinat tertentu.

        HATI-HATI: setelah G92, check_reach() dan check_path() jadi TIDAK AKURAT.
        Firmware memeriksa batas gerak di koordinat ABSOLUT (lihat
        isAllowedPosition() di interpolation.cpp), sedangkan kita memvalidasi di
        koordinat yang sudah digeser offset. Jadi titik yang kita anggap aman
        bisa ditolak robot, dan sebaliknya.

        Jalankan home() untuk menghapus offset dan memulihkan validasi yang benar.
        """
        if not self._origin_shifted:
            self._origin_shifted = True
            print("\n" + "-" * 68)
            print("CATATAN: G92 menggeser titik nol koordinat.")
            print("  check_reach() / check_path() TIDAK akurat lagi selama offset")
            print("  masih aktif. Jalankan home() untuk memulihkannya.")
            print("-" * 68 + "\n")
        parts = ["G92"]
        for label, val in (("X", x), ("Y", y), ("Z", z), ("E", e)):
            if val is not None:
                parts.append(f"{label}{val:.2f}")
        self.send(" ".join(parts))
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        if e is not None:
            self.e = e

    # -- pembacaan ----------------------------------------------------------
    _POS_RE = re.compile(
        r"X:\s*(-?[\d.]+)\s+Y:\s*(-?[\d.]+)\s+Z:\s*(-?[\d.]+)\s+E:\s*(-?[\d.]+)"
    )

    def position(self):
        """M114 — baca koordinat sekarang dari robot. Return dict x/y/z/e."""
        for line in self.send("M114"):
            m = self._POS_RE.search(line)
            if m:
                x, y, z, e = (float(v) for v in m.groups())
                self.x, self.y, self.z, self.e = x, y, z, e
                self.position_known = True
                return {"x": x, "y": y, "z": z, "e": e}
        raise RNV3Error("balasan M114 tidak bisa diparse")

    def endstops(self):
        """M119 — status sensor endstop/home tiap sumbu.

        Kalau hasilnya tidak memuat kunci "E", berarti firmware di robot
        di-compile dengan RAIL=false — set RAIL_ENABLED=False di atas.
        """
        for line in self.send("M119"):
            if "ENDSTOP" in line:
                return dict(re.findall(r"([XYZE]):(\d)", line))
        raise RNV3Error("balasan M119 tidak bisa diparse")

    def sensors(self):
        """M105 — status 3 sensor input. HANYA ada di firmware v2_02 ke atas."""
        if self.firmware != "v2_02":
            raise RNV3Error(
                "M105 butuh firmware v2_02, robot ini terdeteksi "
                f"{self.firmware}. Upload firmware/v2_02 lewat Arduino IDE dulu "
                "(cocokkan RAIL dan INVERSE_*_STEPPER dengan config lama!)."
            )
        for line in self.send("M105"):
            if "SENSOR STATUS" in line:
                return dict(re.findall(r"(S\d):(ON|OFF)", line))
        raise RNV3Error("balasan M105 tidak bisa diparse")

    def ambil_event_sensor(self, bersihkan=True):
        """Ambil event sensor asinkron yang sudah ditangkap send().

        Return list [(nama, status, waktu)], contoh [("S1", "OFF", 12345.6)],
        urut dari yang paling lama. `waktu` dari time.monotonic().

        BATASANNYA: event cuma ikut terbaca kalau ada perintah lain yang
        sedang menunggu balasan. Di luar itu tidak ada yang membaca port, jadi
        event menumpuk di buffer OS dan baru kelihatan pada perintah
        berikutnya. Jadi antrean ini berguna untuk TAHU ada benda lewat saat
        lengan sibuk -- bukan untuk menunggu. Untuk menunggu pakai
        tunggu_sensor() yang aktif polling.
        """
        hasil = list(self.events_sensor)
        if bersihkan:
            self.events_sensor.clear()
        return hasil

    def tunggu_sensor(self, n, status=SENSOR_ADA_BENDA, timeout=60.0, jeda=0.05):
        """Tunggu sampai sensor S<n> berstatus `status`. Return True / False.

        status: SENSOR_ADA_BENDA ("OFF") atau SENSOR_KOSONG ("ON") -- ingat
        input active-LOW, "OFF" justru berarti ada benda di depan sensor.

        Polling M105, bukan menunggu event; alasannya di ambil_event_sensor().
        Polling sekaligus menguras event yang antre, jadi dua-duanya kebagian.

        Return False kalau timeout, BUKAN raise -- belt yang kosong itu
        kondisi normal, biar pemanggil yang memutuskan mau diapakan.
        """
        if n not in (1, 2, 3):
            raise ValueError("n harus 1, 2, atau 3")
        kunci = f"S{n}"
        batas = time.monotonic() + timeout
        while True:
            if self.sensors().get(kunci) == status:
                return True
            if time.monotonic() >= batas:
                return False
            time.sleep(jeda)

    # -- peralatan ----------------------------------------------------------
    def gripper(self, angle):
        """G100 B<sudut> — gripper servo. SERVO_CLOSED..SERVO_OPEN.

        Catatan: di firmware v2_01/v2_02 TIDAK ada M3/M5 (itu firmware lama).
        Gripper sekarang dikendalikan lewat G100.

        PENTING: jangan pernah suruh nutup penuh ke benda. Set sudut sesuai
        ukuran benda; kalau dipaksa mencekik, servo panas dan gearnya rontok.
        """
        angle = max(SERVO_CLOSED, min(SERVO_OPEN, angle))
        self.send(f"G100 B{angle:.0f}")

    def vacuum(self, on):
        """VACUM ON/OFF — makro: pompa + katup + delay sudah diurus firmware."""
        self.send("VACUM ON" if on else "VACUM OFF")

    def output(self, channel, on):
        """LG1/LG2/LG3 ON|OFF — output MOSFET (switching di sisi ground)."""
        if channel not in (1, 2, 3):
            raise ValueError("channel harus 1, 2, atau 3")
        self.send(f"LG{channel} {'ON' if on else 'OFF'}")

    def steppers(self, on):
        """M17 / M18 — enable/disable stepper.

        Setelah M18, posisi hilang: wajib home() lagi.
        """
        self.send("M17" if on else "M18")

    def fan(self, on):
        """M106 / M107 — kipas."""
        self.send("M106" if on else "M107")


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    PORT = "COM4"  # Windows: cek Device Manager > Ports. Linux: /dev/ttyUSB0

    print(f"Jangkauan robot: R_MIN={R_MIN:.2f} mm  R_MAX={R_MAX:.2f} mm")
    print(f"Z: {Z_MIN:.0f} .. {Z_MAX:.0f} mm   Rel: 0 .. {RAIL_LENGTH:.0f} mm")
    print(f"Gripper: tutup={SERVO_CLOSED} buka={SERVO_OPEN}")
    print("\nRadius XY yang valid per ketinggian Z:")
    for z in (138, 100, 50, 0, -50, -95, -131):
        lim = xy_limits(z)
        print(f"  Z={z:>5} -> r_xy {lim[0]:6.1f} .. {lim[1]:6.1f} mm")

    with RNV3(PORT) as bot:
        bot.home()
        print("posisi setelah home:", bot.position())

        # Kotak 100x100 mm di bidang XY, ketinggian Z tetap.
        bot.move(z=0, f=0)
        for x, y in [(-50, 200), (50, 200), (50, 250), (-50, 250), (-50, 200)]:
            bot.goto_xy(x, y, f=80)

        # Contoh titik yang ditolak duluan di Python, tanpa membebani robot.
        try:
            bot.move(x=0, y=400, z=0)
        except RNV3Error as err:
            print(f"ditolak: {err}")

        bot.move(x=HOME_X, y=HOME_Y, z=HOME_Z, f=0)
