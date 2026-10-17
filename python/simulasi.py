"""
Simulator firmware RNV3 — tes program kamu TANPA robot fisik.

Ini tiruan perilaku firmware asli, dibuat dengan membaca source-nya:
  - balasan & teksnya disamakan dengan Logger::logINFO di robotRNV3_v2_0x.ino
  - perintah diparse seperti Command::processMessage() di command.cpp
  - batas gerak pakai isAllowedPosition() di interpolation.cpp, dicek di
    SETIAP langkah jalur (bukan cuma di tujuan) seperti aslinya
  - kalau jalur ditolak: gerakan dibatalkan, posisi dibekukan, tapi 'ok'
    TETAP dikirim -- persis bug/perilaku firmware aslinya

Cara pakai: tukar serial.Serial dengan SerialPalsu, lalu pakai RNV3 biasa.

    import simulasi, rnv3
    bot = simulasi.robot_palsu()      # jalan pintas
    bot.home()
    bot.goto_xy(-50, 200)
    print(bot.position())

Yang TIDAK bisa diuji di sini: kabel/driver CH340, presisi homing, benturan
fisik, kekuatan cengkeram gripper, step hilang karena speed, dan kalibrasi
END_EFFECTOR_OFFSET ke dunia nyata.
"""

import collections
import math
import re

import rnv3


class SerialPalsu:
    """Pengganti serial.Serial yang meniru firmware RNV3."""

    def __init__(self, port="SIM", baud=115200, timeout=2.0,
                 firmware="v2_02", rail=True, sensor=(True, False, True),
                 servo=(rnv3.SERVO_CLOSED, rnv3.SERVO_OPEN),
                 endstop=(0, 0, 0, 0), posisi=None):
        self.port = port
        self.is_open = True
        self.firmware = firmware
        self.rail = rail
        self.sensor = list(sensor)
        # Rentang servo yang "ter-upload" ke robot palsu ini. Dibuat bisa diubah
        # supaya peringatan ketidakcocokan di rnv3._periksa_boot() bisa diuji.
        self.servo_min, self.servo_max = servo
        # Status endstop yang dilaporkan M119. 0 = bebas, 1 = tersentuh
        # (lihat Endstop::state() di endstop.cpp). Bisa diatur supaya cabang
        # "lengan mentok di endstop" di kontak.py bisa diuji.
        self.endstop = list(endstop)
        self._out = []          # baris yang menunggu dibaca program
        # Semua perintah yang pernah masuk. Dipakai cek.py untuk membuktikan
        # kontak.py tidak pernah mengirim perintah gerakan.
        self.log_perintah = []

        # posisi fisik "robot" (INITIAL_* di config.h)
        self.x, self.y, self.z, self.e = posisi if posisi else (
            rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E)
        self.homed = False
        self.relative = False
        self.offset = [0.0, 0.0, 0.0, 0.0]
        self.servo_b = self.servo_max
        self.vacuum_on = False
        self.lg = {1: False, 2: False, 3: False}
        self.steppers_on = False
        # Profil kecepatan aktif. config.h memakai 2 (kosinus) saat boot;
        # M205 S<n> menggantinya saat jalan (interpolation.cpp setSpeedProfile).
        self.speed_profile = 2

        # Perubahan sensor yang dijadwalkan meledak di TENGAH gerakan
        # berikutnya. Lihat picu_saat_gerak().
        self._picu_gerak = []
        # Perubahan sensor yang dijadwalkan terjadi setelah sekian kali M105
        # ditanya. Lihat jadwalkan_sensor().
        self._jadwal_sensor = {}

        # statistik, berguna buat assert di test
        self.jumlah_perintah = 0
        self.jumlah_limit = 0

        # pesan boot, sama urutannya dengan setup() di firmware
        self._say(f"SETUP GRIPPER SERVO : MIN {self.servo_min} "
                  f"MAX {self.servo_max}")
        self._say("RAIL ON" if rail else "RAIL OFF")
        self._say("READY CALIBRATION")

    # -- API serial.Serial yang dipakai RNV3 --------------------------------
    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self.is_open = False

    def readline(self):
        if not self._out:
            return b""              # bikin RNV3.send() kena timeout, seperti asli
        return (self._out.pop(0) + "\n").encode("utf-8")

    def write(self, data):
        for baris in data.decode("ascii", "replace").split("\r"):
            baris = baris.strip()
            if baris:
                self._proses(baris)
        return len(data)

    # -- internal -----------------------------------------------------------
    def _say(self, msg):
        self._out.append(msg)

    # -- simulasi sensor ----------------------------------------------------
    # Dipakai untuk meniru benda lewat di konveyor tanpa hardware apa pun.
    def set_sensor(self, n, aktif):
        """Ubah status sensor S<n> dan pancarkan event tepi seperti firmware.

        `aktif` = keadaan PIN, bukan keadaan benda:
            True  -> pin HIGH -> dilaporkan "ON"  -> TIDAK ada benda
            False -> pin LOW  -> dilaporkan "OFF" -> ADA benda
        (input active-LOW, lihat SENSOR_ADA_BENDA di rnv3.py)

        Firmware cuma bicara saat ada PERUBAHAN tepi
        (robotRNV3_v2_02.ino:177-199), jadi set ke nilai yang sama = diam.
        Blok sensor ini ada di SEMUA varian firmware, bukan cuma v2_02 --
        yang khusus v2_02 itu M105-nya. Return True kalau ada perubahan.
        """
        if n not in (1, 2, 3):
            raise ValueError("n harus 1, 2, atau 3")
        i = n - 1
        if self.sensor[i] == bool(aktif):
            return False
        self.sensor[i] = bool(aktif)
        self._say(f"S{n} {'ON' if aktif else 'OFF'}")
        return True

    def benda_masuk(self, n=1):
        """Benda tiba di depan sensor S<n>. Memancarkan "S<n> OFF"."""
        return self.set_sensor(n, False)

    def benda_keluar(self, n=1):
        """Benda pergi dari sensor S<n>. Memancarkan "S<n> ON"."""
        return self.set_sensor(n, True)

    def jadwalkan_sensor(self, n, aktif, setelah_poll=3):
        """Sensor S<n> berubah jadi `aktif` setelah M105 ditanya N kali lagi.

        Dipakai untuk meniru "benda datang beberapa saat setelah belt jalan"
        tanpa thread: deterministik, jadi tes selalu memberi hasil sama. Yang
        diuji tetap jalur sebenarnya -- RNV3.tunggu_sensor() betulan polling
        M105 berkali-kali sampai statusnya berubah.
        """
        if n not in (1, 2, 3):
            raise ValueError("n harus 1, 2, atau 3")
        self._jadwal_sensor[n] = [bool(aktif), max(1, int(setelah_poll))]

    def jadwalkan_benda_masuk(self, n=1, setelah_poll=3):
        """Benda tiba di sensor S<n> setelah N kali poll."""
        self.jadwalkan_sensor(n, False, setelah_poll)

    def jadwalkan_benda_keluar(self, n=1, setelah_poll=3):
        """Benda pergi dari sensor S<n> setelah N kali poll."""
        self.jadwalkan_sensor(n, True, setelah_poll)

    def _tick_jadwal(self):
        """Dipanggil tiap M105. Jalankan perubahan yang sudah jatuh tempo."""
        for n in list(self._jadwal_sensor):
            self._jadwal_sensor[n][1] -= 1
            if self._jadwal_sensor[n][1] <= 0:
                aktif = self._jadwal_sensor.pop(n)[0]
                self.set_sensor(n, aktif)

    def picu_saat_gerak(self, n, aktif):
        """Jadwalkan perubahan sensor terjadi DI TENGAH gerakan berikutnya.

        Ini mereproduksi skenario paling jahat: event asinkron nyelip di
        antara baris balasan G0. Dulu event seperti ini ketelan oleh
        RNV3.send() lalu dibuang move(); sekarang harus mendarat di
        bot.events_sensor.
        """
        self._picu_gerak.append((n, bool(aktif)))

    def _posisi_valid(self, x, y, z, e):
        """isAllowedPosition() di interpolation.cpp."""
        r_ee = math.hypot(x, y)
        if r_ee == 0:
            return False
        r = r_ee - rnv3.EE_OFFSET
        modul = math.sqrt(r * r + z * z)
        if not (rnv3.R_MIN <= modul <= rnv3.R_MAX):
            return False
        if not (rnv3.Z_MIN <= z <= rnv3.Z_MAX):
            return False
        if self.rail and not (0.0 <= e <= rnv3.RAIL_LENGTH):
            return False
        return True

    def _gerak(self, tx, ty, tz, te):
        """Interpolasi langkah demi langkah, dibatalkan kalau jalur ditolak."""
        ax, ay, az, ae = self.x, self.y, self.z, self.e
        LANGKAH = 200
        for i in range(1, LANGKAH + 1):
            if i == LANGKAH // 2 and self._picu_gerak:
                for n, aktif in self._picu_gerak:
                    self.set_sensor(n, aktif)
                self._picu_gerak.clear()
            t = i / LANGKAH
            px = ax + t * (tx - ax)
            py = ay + t * (ty - ay)
            pz = az + t * (tz - az)
            pe = ae + t * (te - ae)
            if self._posisi_valid(px, py, pz, pe):
                self.x, self.y, self.z, self.e = px, py, pz, pe
            else:
                # Persis firmware: batalkan, bekukan posisi, lapor, lalu 'ok'.
                self.jumlah_limit += 1
                self._say(f"ERROR: LIMIT REACHED: [X:{px:.2f} Y:{py:.2f} "
                          f"Z:{pz:.2f} E:{pe:.2f}]")
                return

    def _proses(self, msg):
        self.jumlah_perintah += 1
        self.log_perintah.append(msg)
        asli = msg
        atas = msg.upper().replace(" ", "")

        # alias teks -> M-code (command.cpp)
        alias = {"VACUMON": "M209", "VACUMOFF": "M230",
                 "LG1ON": "M1", "LG1OFF": "M2",
                 "LG2ON": "M206", "LG2OFF": "M207",
                 "LG3ON": "M6", "LG3OFF": "M7"}
        atas = alias.get(atas, atas)

        if not atas or atas[0] not in "GM":
            self._say("ERROR: COMMAND NOT RECOGNIZED")
            self._say("ok")
            return

        huruf = atas[0]
        angka = re.match(r"[GM](\d+)", atas)
        if not angka:
            self._say("ERROR: COMMAND NOT RECOGNIZED")
            self._say("ok")
            return
        num = int(angka.group(1))
        nilai = {k: float(v) for k, v in re.findall(r"([XYZEFSAB])(-?[\d.]+)", atas)}

        if huruf == "G":
            if num in (0, 1):
                if self.relative:
                    tx = self.x + nilai.get("X", 0.0)
                    ty = self.y + nilai.get("Y", 0.0)
                    tz = self.z + nilai.get("Z", 0.0)
                    te = self.e + nilai.get("E", 0.0)
                else:
                    tx = nilai.get("X", self.x - self.offset[0]) + self.offset[0]
                    ty = nilai.get("Y", self.y - self.offset[1]) + self.offset[1]
                    tz = nilai.get("Z", self.z - self.offset[2]) + self.offset[2]
                    te = nilai.get("E", self.e - self.offset[3]) + self.offset[3]
                self._say(f"LINEAR MOVE: [X:{tx - self.offset[0]:.2f} "
                          f"Y:{ty - self.offset[1]:.2f} "
                          f"Z:{tz - self.offset[2]:.2f} "
                          f"E:{te - self.offset[3]:.2f}]")
                self._gerak(tx, ty, tz, te)
            elif num == 4:
                pass                     # G4: delay; simulator tidak menunggu
            elif num == 28:
                self.x, self.y, self.z, self.e = (
                    rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E)
                self.offset = [0.0, 0.0, 0.0, 0.0]
                self.homed = True
                self._say("HOMING COMPLETE")
            elif num == 90:
                self.relative = False
                self._say("ABSOLUTE MODE ON")
            elif num == 91:
                self.relative = True
                self._say("RELATIVE MODE ON")
            elif num == 92:
                for i, k in enumerate("XYZE"):
                    if k in nilai:
                        kini = (self.x, self.y, self.z, self.e)[i]
                        self.offset[i] = kini - nilai[k]
                self._say(f"POSITION OFFSET: [X{self.offset[0]:.2f} "
                          f"Y{self.offset[1]:.2f} Z{self.offset[2]:.2f} "
                          f"E{self.offset[3]:.2f}]")
            elif num == 100:
                if "B" in nilai:
                    b = nilai["B"]
                    if self.servo_min <= b <= self.servo_max:
                        self.servo_b = b
                        self._say(f"SERVO MOVE: [B:{b:.2f}]")
                    else:
                        self._say("Nilai cmd.valueB di luar rentang yang diizinkan!")
            else:
                self._say("ERROR: COMMAND NOT RECOGNIZED")

        else:  # M
            if num == 1:   self.lg[1] = True
            elif num == 2: self.lg[1] = False
            elif num == 6: self.lg[3] = True
            elif num == 7: self.lg[3] = False
            elif num == 206: self.lg[2] = True
            elif num in (207, 208): self.lg[2] = False
            elif num == 209: self.vacuum_on = True
            elif num == 230:
                self.vacuum_on = False
                self._say("Tunggu..")      # firmware mencetak ini pada M230
            elif num == 17: self.steppers_on = True
            elif num == 18:
                self.steppers_on = False
                self.homed = False
            elif num == 205:
                self.speed_profile = int(nilai.get("S", self.speed_profile))
                self._say(f"SPEED PROFILE: [{self.speed_profile}]")
            elif num in (106, 107): pass
            elif num == 114:
                self._say("RELATIVE MODE" if self.relative else "ABSOLUTE MODE")
                self._say(f"CURRENT POSITION: [X:{self.x - self.offset[0]:.2f} "
                          f"Y:{self.y - self.offset[1]:.2f} "
                          f"Z:{self.z - self.offset[2]:.2f} "
                          f"E:{self.e - self.offset[3]:.2f}]")
            elif num == 119:
                ex, ey, ez, ee = self.endstop
                s = f"ENDSTOP: [X:{ex} Y:{ey} Z:{ez}"
                if self.rail:
                    s += f" E:{ee}"
                self._say(s + "]")
            elif num == 105:
                # Jatuh tempo diproses DULU, jadi event tepinya terpancar
                # sebelum baris SENSOR STATUS -- persis seperti firmware yang
                # memancarkan event dari loop() lalu menjawab perintah.
                self._tick_jadwal()
                if self.firmware == "v2_02":
                    self._say("SENSOR STATUS: [S1:{} S2:{} S3:{}]".format(
                        *("ON" if v else "OFF" for v in self.sensor)))
                else:
                    self._say("ERROR: COMMAND NOT RECOGNIZED")
            else:
                self._say("ERROR: COMMAND NOT RECOGNIZED")

        self._say("ok")


def robot_palsu(firmware="v2_02", rail=True, sensor=(True, False, True),
                servo=(rnv3.SERVO_CLOSED, rnv3.SERVO_OPEN),
                endstop=(0, 0, 0, 0), posisi=None, verbose=False):
    """Bikin objek RNV3 yang nyambung ke simulator, bukan ke robot asli.

    Menyiapkan field yang sama seperti RNV3.__init__() lalu memanggil
    _drain_boot() -- pesan boot TIDAK dibuang, supaya pemeriksaan kecocokan
    servo/rel ikut teruji seperti pada robot asli.
    """
    palsu = SerialPalsu(firmware=firmware, rail=rail, sensor=sensor, servo=servo,
                        endstop=endstop, posisi=posisi)
    bot = RNV3Simulasi.__new__(RNV3Simulasi)
    bot.port = "SIM"
    bot.verbose = verbose
    bot.ser = palsu
    bot.x, bot.y, bot.z, bot.e = (
        rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E)
    bot.relative = False
    bot.position_known = True
    bot.firmware = "v2_01"
    bot.boot_lines = []
    bot.servo_range_reported = None
    bot.rail_reported = None
    bot._origin_shifted = False
    # robot_palsu() sengaja TIDAK memanggil RNV3.__init__ (itu akan membuka
    # port serial sungguhan), jadi setiap field baru di sana harus dicermin
    # di sini juga.
    bot.events_sensor = collections.deque(maxlen=200)
    bot.sim = palsu
    bot._drain_boot(quiet=0.0, limit=1.0)   # simulator menjawab seketika
    bot.detect_firmware()
    return bot


class RNV3Simulasi(rnv3.RNV3):
    """RNV3 yang tersambung ke SerialPalsu. Akses simulatornya via .sim"""
    pass


if __name__ == "__main__":
    bot = robot_palsu(verbose=True)
    print("\n--- home ---")
    bot.home()
    print("posisi:", bot.position())

    print("\n--- gerak kotak di bidang XY ---")
    bot.move(z=0, f=0)
    for x, y in [(-50, 200), (50, 200), (50, 250), (-50, 250)]:
        bot.goto_xy(x, y, f=80)
    print("posisi:", bot.position())

    print("\n--- rel + gripper + vakum ---")
    bot.move(e=150, f=30)
    bot.gripper(rnv3.SERVO_CLOSED)
    bot.vacuum(True)
    bot.vacuum(False)
    print("sensor:", bot.sensors())

    print(f"\ntotal perintah: {bot.sim.jumlah_perintah}  "
          f"limit kena: {bot.sim.jumlah_limit}")
