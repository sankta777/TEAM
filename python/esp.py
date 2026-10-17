"""
Adapter ke ESP32 kelompok 2 — konveyor, sensor kedatangan, gerbang, lampu.

BEDA DENGAN ROBOT: protokol robot sudah ditentukan firmware orang lain dan tidak
bisa diubah (G-code, terminator '\\r', event sensor polos yang gampang tertukar
dengan balasan perintah). Protokol ESP ini KITA yang tentukan, jadi dibikin
sejak awal supaya tidak punya masalah yang sama:

  - balasan perintah SELALU diawali "OK" atau "ERROR"
  - pesan yang datang sendiri SELALU diawali "EVENT"
  - jadi tidak pernah ada kebingungan mana balasan mana event

PROTOKOL (tunjukkan bagian ini ke kelompok 2 — ini kesepakatannya)

  laptop → ESP                      ESP harus balas
  ────────────────────────────────  ──────────────────────────────────
  PING                              OK PONG
  JALAN                             OK            (belt nyala)
  STOP                              OK            (belt mati)
  STATUS                            OK BENDA=YA|TIDAK JARAK=<cm>
  GERBANG KIRI|KANAN|TENGAH         OK
  LAMPU ON|OFF                      OK
  (perintah tak dikenal)            ERROR perintah tidak dikenal

  ESP → laptop, kapan saja tanpa diminta:
  EVENT BENDA                       benda baru terdeteksi sensor
  EVENT KOSONG                      benda sudah lewat
  EVENT WATCHDOG                    belt dimatikan sendiri, laptop hilang

Tiap baris diakhiri '\\n'. Huruf besar semua. Tidak ada JSON — ESP32 tidak perlu
dibebani parser, dan ini gampang dites manual lewat Serial Monitor.

WATCHDOG — WAJIB ADA DI SISI ESP

Robot RNV3 tidak punya E-stop sama sekali, jadi satu-satunya pengaman otomatis
di sistem ini ada di ESP. Aturannya: kalau tidak ada perintah apa pun masuk
selama WATCHDOG_MS (default 2000 ms), ESP **matikan belt sendiri** lalu kirim
"EVENT WATCHDOG". Laptop yang hang, kabel USB kecabut, atau program ditutup
paksa tidak boleh meninggalkan belt berjalan terus.

Sisi laptop wajib mengirim PING berkala supaya watchdog tidak jatuh — itu
tugas detak() di bawah.
"""

import collections
import re
import time

import serial

BAUD_DEFAULT = 115200
TIMEOUT_DEFAULT = 2.0

# Jarak aman detak PING: setengah dari watchdog ESP, supaya satu paket hilang
# tidak langsung menjatuhkan belt.
WATCHDOG_MS = 2000
DETAK_DETIK = WATCHDOG_MS / 1000.0 / 2

EVENT = re.compile(r"^EVENT\s+(\w+)$")
STATUS = re.compile(r"BENDA=(YA|TIDAK)")
JARAK = re.compile(r"JARAK=([\d.]+)")

GERBANG_SAH = ("KIRI", "KANAN", "TENGAH")


class ESPError(RuntimeError):
    """ESP tidak menjawab, menjawab ERROR, atau formatnya tidak dikenal."""


class ESP:
    """Koneksi serial ke ESP32 kelompok 2.

    Dipakai lewat konveyor.KonveyorESP, bukan langsung dari jalur.py --
    orkestrator tidak boleh tahu soal kabel dan protokol teks.
    """

    def __init__(self, port, baud=BAUD_DEFAULT, timeout=TIMEOUT_DEFAULT,
                 verbose=False, ser=None, tunggu_boot=True):
        self.port = port
        self.verbose = verbose
        # ser bisa diisi SerialESPPalsu untuk tes tanpa hardware.
        self.ser = ser if ser is not None else serial.Serial(port, baud, timeout=timeout)
        self.events = collections.deque(maxlen=200)
        self.detak_terakhir = 0.0

        if tunggu_boot and ser is None:
            # Membuka port serial mereset ESP32 (jalur DTR/RTS), sama seperti
            # Arduino. Beri waktu boot sebelum perintah pertama.
            time.sleep(1.5)
            self.ser.reset_input_buffer()

    # -- lifecycle ----------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        """Matikan belt dulu, baru tutup port. Jangan tinggalkan belt jalan."""
        try:
            self.berhenti()
        except (ESPError, OSError):
            pass            # watchdog ESP yang akan menanganinya
        if self.ser.is_open:
            self.ser.close()

    # -- transport ----------------------------------------------------------
    def send(self, cmd, timeout=TIMEOUT_DEFAULT):
        """Kirim satu perintah, kumpulkan balasan sampai OK/ERROR.

        Return sisa teks setelah "OK" (string, bisa kosong).
        Raise ESPError kalau ESP balas ERROR atau diam sampai timeout --
        tidak pernah menggantung selamanya.
        """
        cmd = cmd.strip().upper()
        self.ser.write(cmd.encode("ascii") + b"\n")
        self.detak_terakhir = time.monotonic()
        if self.verbose:
            print(f">> {cmd}")

        batas = time.monotonic() + timeout
        while True:
            raw = self.ser.readline()
            if not raw:
                if time.monotonic() > batas:
                    raise ESPError(f"timeout {timeout}s menunggu balasan: {cmd}")
                continue
            baris = raw.decode("utf-8", "replace").strip()
            if not baris:
                continue
            if self.verbose:
                print(f"<< {baris}")

            # Event bisa nyelip di tengah balasan -- sisihkan, jangan dianggap
            # jawaban perintah. Ini pelajaran dari bug event sensor di rnv3.py.
            cocok = EVENT.match(baris)
            if cocok:
                self.events.append((cocok.group(1), time.monotonic()))
                continue

            if baris.startswith("OK"):
                return baris[2:].strip()
            if baris.startswith("ERROR"):
                raise ESPError(f"ESP tolak '{cmd}': {baris[5:].strip()}")
            # Baris asing: kemungkinan cetakan debug dari firmware ESP.
            if self.verbose:
                print(f"   (baris tidak dikenal, diabaikan: {baris!r})")
            if time.monotonic() > batas:
                raise ESPError(f"timeout {timeout}s menunggu OK untuk: {cmd}")

    def ambil_event(self, bersihkan=True):
        """Event yang sudah tertangkap. list [(nama, waktu)].

        Hanya mengembalikan yang SUDAH terbaca -- tidak menyentuh port. Yang
        masih menumpuk di buffer baru ikut terbaca saat ada perintah berikutnya,
        atau kalau pungut_event() dipanggil. Untuk MENUNGGU benda pakai
        tunggu_benda() yang polling STATUS, jangan mengandalkan antrean ini.
        """
        hasil = list(self.events)
        if bersihkan:
            self.events.clear()
        return hasil

    def pungut_event(self, batas=50):
        """Kuras event yang menumpuk di buffer TANPA mengirim perintah apa pun.

        Perlu karena ESP bisa bicara duluan -- "EVENT WATCHDOG" misalnya muncul
        justru ketika laptop sedang tidak mengirim apa-apa, dan tanpa ini pesan
        itu baru kelihatan entah kapan.

        Tidak memblokir: berhenti begitu buffer habis. HARUS dipanggil dari
        thread yang sama dengan send() -- port serial bukan barang thread-safe.
        """
        for _ in range(batas):
            if not getattr(self.ser, "in_waiting", 0):
                break
            raw = self.ser.readline()
            if not raw:
                break
            baris = raw.decode("utf-8", "replace").strip()
            if not baris:
                continue
            cocok = EVENT.match(baris)
            if cocok:
                self.events.append((cocok.group(1), time.monotonic()))
            elif self.verbose:
                # Balasan tanpa perintah: tidak ada yang menunggunya, buang.
                print(f"   (baris nyasar saat memungut event: {baris!r})")
        return self.ambil_event()

    # -- perintah -----------------------------------------------------------
    def ping(self):
        """True kalau ESP hidup. Tidak pernah raise -- buat pre-flight."""
        try:
            self.send("PING")
            return True
        except (ESPError, OSError):
            return False

    def detak(self, paksa=False):
        """Kirim PING kalau sudah waktunya, supaya watchdog ESP tidak jatuh.

        Panggil ini di sela-sela pekerjaan panjang (misal saat lengan sedang
        bergerak lama). Murah: tidak mengirim apa-apa kalau belum waktunya.
        """
        if paksa or time.monotonic() - self.detak_terakhir >= DETAK_DETIK:
            self.send("PING")

    def jalan(self):
        """Nyalakan motor belt."""
        self.send("JALAN")

    def berhenti(self):
        """Matikan motor belt."""
        self.send("STOP")

    def gerbang(self, sisi):
        """Arahkan gerbang servo: KIRI / KANAN / TENGAH."""
        sisi = sisi.strip().upper()
        if sisi not in GERBANG_SAH:
            raise ValueError(f"sisi harus salah satu dari {GERBANG_SAH}")
        self.send(f"GERBANG {sisi}")

    def lampu(self, nyala):
        self.send("LAMPU ON" if nyala else "LAMPU OFF")

    def status(self):
        """Keadaan sensor SEKARANG. Return {"benda": bool, "jarak_cm": float|None}.

        Ini padanan M105 di robot: menanyakan keadaan, bukan menunggu event.
        Sengaja ada supaya menunggu benda bisa dilakukan dengan polling yang
        andal, bukan event yang bisa hilang.
        """
        sisa = self.send("STATUS")
        cocok = STATUS.search(sisa)
        if not cocok:
            raise ESPError(f"balasan STATUS tidak bisa diparse: {sisa!r}")
        jarak = JARAK.search(sisa)
        return {
            "benda": cocok.group(1) == "YA",
            "jarak_cm": float(jarak.group(1)) if jarak else None,
        }

    def ada_benda(self):
        return self.status()["benda"]

    def tunggu_benda(self, ada=True, timeout=60.0, jeda=0.05):
        """Tunggu sampai sensor melihat benda (ada=True) atau kosong (ada=False).

        Return True kalau tercapai, False kalau timeout -- BUKAN raise, karena
        belt kosong itu kondisi normal. Polling STATUS sekaligus berfungsi
        sebagai detak watchdog, jadi belt tidak akan mati sendiri selama ini.
        """
        batas = time.monotonic() + timeout
        while True:
            if self.status()["benda"] == ada:
                return True
            if time.monotonic() >= batas:
                return False
            time.sleep(jeda)


# ---------------------------------------------------------------------------
# ESP TIRUAN — biar seluruh alur bisa dites tanpa hardware
# Ditaruh di file ini (bukan simulasi.py) supaya protokol dan tiruannya
# bersebelahan: kalau protokol berubah, yang harus ikut berubah kelihatan.
# ---------------------------------------------------------------------------
class SerialESPPalsu:
    """Pengganti serial.Serial yang meniru firmware ESP32 kelompok 2."""

    def __init__(self, jarak_kosong=25.0, jarak_benda=6.0, watchdog_ms=WATCHDOG_MS):
        self.is_open = True
        self.belt = False
        self.benda = False
        self.gerbang_posisi = "TENGAH"
        self.lampu_nyala = False
        self.jarak_kosong = jarak_kosong
        self.jarak_benda = jarak_benda
        self.watchdog_ms = watchdog_ms

        self._out = []
        self.log_perintah = []
        # Perubahan sensor terjadwal: {sisa_poll: nilai}. Deterministik,
        # tanpa thread -- pola yang sama dengan simulasi.SerialPalsu.
        self._jadwal = None
        self.perintah_terakhir = time.monotonic()
        self.watchdog_jatuh = 0

    # -- API serial.Serial yang dipakai ESP ---------------------------------
    @property
    def in_waiting(self):
        """Jumlah byte yang menunggu dibaca -- dipakai pungut_event()."""
        return sum(len(b) + 1 for b in self._out)

    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self.is_open = False

    def readline(self):
        if not self._out:
            return b""
        return (self._out.pop(0) + "\n").encode("utf-8")

    def write(self, data):
        for baris in data.decode("ascii", "replace").split("\n"):
            baris = baris.strip()
            if baris:
                self._proses(baris)
        return len(data)

    # -- kontrol tes --------------------------------------------------------
    def jadwalkan_benda(self, ada, setelah_poll=3):
        """Sensor berubah setelah STATUS ditanya N kali lagi."""
        self._jadwal = [bool(ada), max(1, int(setelah_poll))]

    def set_benda(self, ada):
        """Ubah sensor sekarang juga + pancarkan event seperti firmware asli."""
        if self.benda == bool(ada):
            return False
        self.benda = bool(ada)
        self._out.append(f"EVENT {'BENDA' if ada else 'KOSONG'}")
        return True

    def majukan_waktu(self, detik):
        """Pura-pura waktu berjalan, untuk menguji watchdog tanpa menunggu."""
        self.perintah_terakhir -= detik
        self._cek_watchdog()

    # -- internal -----------------------------------------------------------
    def _cek_watchdog(self):
        diam_ms = (time.monotonic() - self.perintah_terakhir) * 1000
        if diam_ms > self.watchdog_ms and self.belt:
            self.belt = False
            self.watchdog_jatuh += 1
            self._out.append("EVENT WATCHDOG")

    def _tick_jadwal(self):
        if self._jadwal is None:
            return
        self._jadwal[1] -= 1
        if self._jadwal[1] <= 0:
            ada = self._jadwal[0]
            self._jadwal = None
            self.set_benda(ada)

    def _proses(self, baris):
        self._cek_watchdog()
        self.log_perintah.append(baris)
        self.perintah_terakhir = time.monotonic()
        atas = baris.upper()

        if atas == "PING":
            self._out.append("OK PONG")
        elif atas == "JALAN":
            self.belt = True
            self._out.append("OK")
        elif atas == "STOP":
            self.belt = False
            self._out.append("OK")
        elif atas == "STATUS":
            self._tick_jadwal()
            jarak = self.jarak_benda if self.benda else self.jarak_kosong
            self._out.append(
                f"OK BENDA={'YA' if self.benda else 'TIDAK'} JARAK={jarak:.1f}")
        elif atas.startswith("GERBANG"):
            sisi = atas[7:].strip()
            if sisi in GERBANG_SAH:
                self.gerbang_posisi = sisi
                self._out.append("OK")
            else:
                self._out.append("ERROR arah gerbang tidak dikenal")
        elif atas in ("LAMPU ON", "LAMPU OFF"):
            self.lampu_nyala = atas.endswith("ON")
            self._out.append("OK")
        else:
            self._out.append("ERROR perintah tidak dikenal")


def esp_palsu(verbose=False, **kw):
    """Objek ESP yang tersambung ke SerialESPPalsu. Akses tiruannya via .sim"""
    palsu = SerialESPPalsu(**kw)
    e = ESP(port="SIM-ESP", ser=palsu, verbose=verbose, tunggu_boot=False)
    e.sim = palsu
    return e


if __name__ == "__main__":
    e = esp_palsu(verbose=True)
    print("\n-- ping --")
    print("hidup:", e.ping())

    print("\n-- belt jalan, tunggu benda --")
    e.jalan()
    e.sim.jadwalkan_benda(True, setelah_poll=3)
    print("benda datang:", e.tunggu_benda(True, timeout=5, jeda=0.01))
    e.berhenti()
    print("status:", e.status())
    print("event :", e.ambil_event())

    print("\n-- gerbang + lampu --")
    e.gerbang("KIRI")
    e.lampu(True)
    print("posisi gerbang:", e.sim.gerbang_posisi, "| lampu:", e.sim.lampu_nyala)

    print("\n-- watchdog: laptop hilang saat belt jalan --")
    e.jalan()
    print("belt sebelum:", e.sim.belt)
    e.sim.majukan_waktu(5.0)                 # pura-pura laptop diam 5 detik
    print("belt sesudah:", e.sim.belt, "| watchdog jatuh:", e.sim.watchdog_jatuh)
    # ESP bicara duluan tanpa diminta -- harus dipungut, bukan ditunggu.
    print("event        :", e.pungut_event())
