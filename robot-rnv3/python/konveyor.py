"""
Konveyor belt eksternal — dua cara kontrol, satu antarmuka.

PENTING, supaya tidak tertukar: "rel" dan "konveyor" itu DUA BENDA BERBEDA.

    Rel (sumbu E)                   Konveyor (belt)
    track linear tempat robot       ban berjalan yang memindahkan benda
    ROBOTNYA yang geser             BENDANYA yang jalan, robot diam
    bot.move(e=150)                 konveyor.jalan()
    posisi diketahui (endstop)      posisi tidak diketahui
    bagian dari firmware            perangkat terpisah

File ini mengurus yang KANAN. Untuk yang kiri pakai bot.move(e=...).

KENAPA ADA DUA IMPLEMENTASI

Belum pasti siapa yang menyalakan motor belt:

    KonveyorLG   relay disetir output MOSFET board robot (LG1/LG2/LG3)
    KonveyorESP  relay disetir ESP32 kelompok 2

Keduanya memakai antarmuka yang sama, jadi jalur.py TIDAK berubah sedikit pun
saat transportnya ditukar. Keputusan itu bisa ditunda sampai kelompok 2
memastikan punya modul relay atau tidak.

Konveyor fisiknya sendiri unit mandiri: PSU sendiri, dan kecepatan diatur
KNOB MANUAL di boksnya. Jadi yang diotomatisasi cuma nyala-mati; kecepatan
disetel sekali di awal lalu dibiarkan. Setel pelan — belt yang berhenti
mendadak akan menumbangkan botol yang berdiri.

KABEL KALAU PAKAI KonveyorLG (DB25, lihat CATATAN.md "Arsitektur power")
    LG1/LG2/LG3 = pin 7/8/9, MOSFET switching di sisi GROUND, maks 5 A
    sensor 1/2/3 = pin 2/3/4, 5 V dari pin 1 (maks 2 A), GND pin 6

JANGAN sambung motor belt langsung ke LG. Pakai relay: LG cukup menyalakan coil
relay, motornya jalan dari PSU-nya sendiri. Dua alasannya:
  1. beban induktif tanpa dioda flyback bisa membunuh MOSFET di board,
  2. rail 12 V robot dipakai bareng VMOT kelima driver A4988 — motor yang
     menyentak bisa bikin stepper brownout di tengah gerakan.
Ground PSU belt, ground sensor, dan ground board HARUS tersambung semua.
"""

import math
import time

import rnv3


class Konveyor:
    """Antarmuka konveyor. Jangan dipakai langsung — pilih turunannya.

    Yang diatur di sini cuma logika yang sama untuk semua transport:
    pencatatan status nyala-mati, jeda settle, dan jaminan belt selalu
    dimatikan. Cara bicara ke hardware diserahkan ke turunannya.

    settle : jeda setelah belt berhenti sebelum benda boleh difoto/diambil.
             Benda masih meluncur dan bergoyang sepersekian detik setelah
             motor mati; memotret terlalu cepat = koordinat meleset.
    """

    nama = "konveyor"

    def __init__(self, settle=0.4, verbose=True):
        self.settle = settle
        self.verbose = verbose
        self.jalan_sekarang = False

    # -- yang WAJIB diisi turunan -------------------------------------------
    def _nyalakan(self):
        raise NotImplementedError

    def _matikan(self):
        raise NotImplementedError

    def ada_benda(self):
        """True kalau sensor sedang melihat benda. Sekali tanya, tidak menunggu."""
        raise NotImplementedError

    def _tunggu(self, ada, timeout):
        """Tunggu sensor mencapai keadaan `ada`. Return True/False (bukan raise)."""
        raise NotImplementedError

    def periksa(self):
        """Pre-flight. Raise kalau tidak layak jalan, return status kalau oke."""
        raise NotImplementedError

    def detak(self):
        """Jaga koneksi tetap hidup. Default tidak melakukan apa-apa."""

    # -- logika bersama ------------------------------------------------------
    def jalan(self):
        """Nyalakan motor belt."""
        if not self.jalan_sekarang:
            self._nyalakan()
            self.jalan_sekarang = True
            self._kata("belt JALAN")

    def berhenti(self):
        """Matikan motor belt. Aman dipanggil berkali-kali."""
        if self.jalan_sekarang:
            self._matikan()
            self.jalan_sekarang = False
            self._kata("belt BERHENTI")

    def maju_sampai_benda(self, timeout=60.0):
        """Jalankan belt sampai benda sampai, lalu BERHENTI dan diamkan.

        Ini operasi inti. Belt selalu dimatikan sebelum fungsi ini kembali —
        termasuk kalau timeout, kalau sensornya error, atau kalau pemakai
        menekan Ctrl-C. Firmware RNV3 tidak punya abort dan tidak punya
        E-stop, jadi jaminan belt berhenti ya cuma di sini.

        Return True kalau benda datang, False kalau timeout.
        """
        self.jalan()
        try:
            datang = self._tunggu(True, timeout)
        finally:
            self.berhenti()
        if datang:
            self._kata(f"benda terdeteksi, diamkan {self.settle:.2f} s")
            time.sleep(self.settle)
        else:
            self._kata(f"tidak ada benda sampai {timeout:.0f} s")
        return datang

    def lewatkan_benda(self, timeout=30.0):
        """Jalankan belt sampai zona ambil KOSONG lagi — benda lewat begitu saja.

        Dipakai kalau benda ternyata di luar jangkauan lengan atau kamera tidak
        melihat apa pun: daripada macet selamanya, dorong lewat.
        Return True kalau zona jadi kosong.
        """
        self.jalan()
        try:
            bebas = self._tunggu(False, timeout)
        finally:
            self.berhenti()
        self._kata("zona ambil kosong lagi" if bebas
                   else f"zona ambil MASIH terisi setelah {timeout:.0f} s "
                        f"— kemungkinan benda nyangkut")
        return bebas

    # -- lifecycle -----------------------------------------------------------
    def _kata(self, msg):
        if self.verbose:
            print(f"[{self.nama}] {msg}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        # Apa pun yang terjadi di dalam blok with, belt tidak boleh
        # ditinggal menyala.
        self.berhenti()


class KonveyorLG(Konveyor):
    """Belt disetir output MOSFET board robot, sensor dibaca lewat M105.

    Tidak butuh ESP sama sekali — board RNV3 sudah punya 3 output MOSFET dan
    3 input sensor. Paling sedikit barangnya, tapi berarti kelompok 2 tidak
    kebagian kontrol apa pun atas konveyor.
    """

    nama = "konveyor-LG"

    def __init__(self, bot, channel=1, sensor=1, settle=0.4, verbose=True):
        super().__init__(settle=settle, verbose=verbose)
        if channel not in (1, 2, 3):
            raise ValueError("channel harus 1, 2, atau 3")
        if sensor not in (1, 2, 3):
            raise ValueError("sensor harus 1, 2, atau 3")
        self.bot = bot
        self.channel = channel
        self.sensor = sensor

    def _nyalakan(self):
        self.bot.output(self.channel, True)

    def _matikan(self):
        self.bot.output(self.channel, False)

    def ada_benda(self):
        return self.bot.sensors().get(f"S{self.sensor}") == rnv3.SENSOR_ADA_BENDA

    def _tunggu(self, ada, timeout):
        status = rnv3.SENSOR_ADA_BENDA if ada else rnv3.SENSOR_KOSONG
        return self.bot.tunggu_sensor(self.sensor, status, timeout=timeout)

    def periksa(self):
        if self.bot.firmware != "v2_02":
            raise rnv3.RNV3Error(
                f"KonveyorLG butuh firmware v2_02 untuk membaca sensor lewat "
                f"M105, robot ini {self.bot.firmware}. Event asinkron "
                f"'S1 ON/OFF' memang ada di semua varian, tapi tanpa M105 "
                f"tidak ada cara menanyakan keadaan SEKARANG — dan menunggu "
                f"benda butuh itu.")
        status = self.bot.sensors()
        self._kata(f"sensor saat ini: {status}")
        if status.get(f"S{self.sensor}") == rnv3.SENSOR_ADA_BENDA:
            self._kata(f"CATATAN: S{self.sensor} sudah 'ADA BENDA' sejak awal "
                       f"— entah benda nyangkut di zona ambil, atau sensornya "
                       f"belum terpasang/salah tipe (harus NPN).")
        return status


class KonveyorManual(Konveyor):
    """Tanpa motor dan sensor: OPERATOR yang menjadi belt dan sensornya.

    Dipakai selama motor belt masih dinyalakan rangkaian sendiri dan sensor
    IR belum terpasang. Belt diam; operator menaruh benda lalu menekan Enter
    (= "sensor melihat benda"). Kamera, kalibrasi, ambil, dan taruh di
    jalur.py tetap jalan persis seperti mode otomatis -- cuma transport ini
    yang ditukar.

    q (atau Ctrl-C / EOF) di prompt = selesai: KeyboardInterrupt dilempar
    supaya Jalur.jalankan() keluar lewat jalur normalnya (parkir, vakum OFF).
    """

    nama = "konveyor-manual"

    def __init__(self, settle=0.0, verbose=True, tanya=input):
        super().__init__(settle=settle, verbose=verbose)
        self.tanya = tanya

    # Belt-nya diam terus: tidak ada yang dinyalakan, dan pesan "belt JALAN"
    # dari kelas dasar cuma akan membingungkan operator.
    def jalan(self):
        pass

    def berhenti(self):
        pass

    def _nyalakan(self):
        pass

    def _matikan(self):
        pass

    def ada_benda(self):
        return False            # tidak ada sensor; yang tahu cuma operator

    def _tunggu(self, ada, timeout):
        if ada:
            teks = ("\ntaruh benda di belt (di dalam kotak ROI), tangan "
                    "MENJAUH -- Enter = robot ambil, q = selesai: ")
        else:
            teks = ("  benda tidak diambil -- singkirkan dari belt dengan "
                    "tangan, lalu Enter (q = selesai): ")
        try:
            jawab = self.tanya(teks)
        except EOFError:
            raise KeyboardInterrupt from None
        if jawab.strip().lower() == "q":
            raise KeyboardInterrupt
        return True

    def periksa(self):
        self._kata("mode MANUAL: belt dan sensor tidak dipakai, operator "
                   "menaruh benda lalu menekan Enter")
        return {}


class KonveyorKamera(Konveyor):
    """Tanpa motor dan sensor: KAMERA yang menjadi sensornya.

    Belt diam; operator menaruh benda, kamera terus mengawasi, dan begitu
    ada benda yang DIAM di tempat yang sama `stabil` kali berturut-turut
    (jeda `jeda` detik), dianggap "sensor melihat benda". Syarat diam itu
    pengaman tangan: benda yang masih dipegang/digeser posisinya berubah,
    jadi lengan belum boleh turun. Ini BUKAN deteksi tangan -- YOLO tidak
    kenal tangan.

    kamera   : SumberDeteksi yang mengembalikan Deteksi dalam mm
    saring   : fungsi Deteksi -> bool; hanya benda yang lolos yang dihitung
               (dipakai untuk membuang yang tak terjangkau lengan)
    berhenti : threading.Event; kalau di-set, penantian langsung selesai
    tunggu_selamanya : True = abaikan timeout menunggu benda, tunggu sampai
               ada benda atau `berhenti` di-set. Tanpa ini belt kosong
               menghasilkan siklus "tidak ada benda" tiap timeout.
    """

    nama = "konveyor-kamera"

    def __init__(self, kamera, saring=None, berhenti=None, jeda=0.5, stabil=3,
                 toleransi_mm=3.0, tunggu_selamanya=False, settle=0.0,
                 verbose=True, tidur=time.sleep):
        super().__init__(settle=settle, verbose=verbose)
        self.kamera = kamera
        self.saring = saring or (lambda d: True)
        self.berhenti_event = berhenti
        self.jeda = jeda
        self.stabil = stabil
        self.toleransi_mm = toleransi_mm
        self.tunggu_selamanya = tunggu_selamanya
        self.tidur = tidur
        self._diabaikan_terakhir = None

    # Belt-nya diam terus, sama seperti KonveyorManual.
    def jalan(self):
        pass

    def berhenti(self):
        pass

    def _nyalakan(self):
        pass

    def _matikan(self):
        pass

    def _lihat(self):
        """Satu foto -> daftar benda yang lolos saringan. Error kamera = kosong."""
        import kontrak_kamera
        try:
            semua = self.kamera.deteksi()
        except kontrak_kamera.KameraError as e:
            self._kata(f"kamera: {e}")
            return []
        lolos = [d for d in semua if self.saring(d)]
        diabaikan = tuple(sorted((d.label, round(d.x_mm), round(d.y_mm))
                                 for d in semua if not self.saring(d)))
        # Dicatat sekali per perubahan, bukan tiap 0,5 detik.
        if diabaikan and diabaikan != self._diabaikan_terakhir:
            self._kata(f"{len(diabaikan)} benda di luar jangkauan lengan, "
                       f"diabaikan: " + ", ".join(
                           f"{l}@({x}, {y})" for l, x, y in diabaikan))
        self._diabaikan_terakhir = diabaikan
        return lolos

    def _sama(self, a, b):
        if not a or not b or len(a) != len(b):
            return False
        sisa = list(b)
        for d in a:
            cocok = next((e for e in sisa if e.label == d.label and
                          math.hypot(e.x_mm - d.x_mm, e.y_mm - d.y_mm)
                          <= self.toleransi_mm), None)
            if cocok is None:
                return False
            sisa.remove(cocok)
        return True

    def _distop(self):
        return self.berhenti_event is not None and self.berhenti_event.is_set()

    def ada_benda(self):
        return bool(self._lihat())

    def _tunggu(self, ada, timeout):
        akhir = time.monotonic() + timeout
        sebelumnya, kali = None, 0
        while not self._distop():
            if not (ada and self.tunggu_selamanya) and time.monotonic() >= akhir:
                return False
            sekarang = self._lihat()
            if not ada:
                if not sekarang:
                    return True
            elif sekarang:
                kali = kali + 1 if self._sama(sekarang, sebelumnya) else 1
                if kali >= self.stabil:
                    return True
            else:
                kali = 0
            sebelumnya = sekarang
            self.tidur(self.jeda)
        return False

    def periksa(self):
        if not self.kamera.ping():
            raise rnv3.RNV3Error("kamera tidak menjawab ping -- server kamera "
                                 "belum jalan?")
        self._kata(f"mode KAMERA: robot mengambil sendiri benda yang diam "
                   f"{(self.stabil - 1) * self.jeda:.1f} s di belt")
        return {}


class KonveyorESP(Konveyor):
    """Belt dan sensor dipegang ESP32 kelompok 2, lewat port serial terpisah.

    Robot dan ESP adalah dua port serial berbeda di laptop yang sama. Keduanya
    harus dipegang thread yang sama — port serial bukan barang thread-safe.
    """

    nama = "konveyor-ESP"

    def __init__(self, esp, settle=0.4, verbose=True):
        super().__init__(settle=settle, verbose=verbose)
        self.esp = esp

    def _nyalakan(self):
        self.esp.jalan()

    def _matikan(self):
        self.esp.berhenti()

    def ada_benda(self):
        return self.esp.ada_benda()

    def _tunggu(self, ada, timeout):
        # Polling STATUS sekaligus jadi detak watchdog, jadi belt tidak
        # akan dimatikan ESP sendiri selama penantian ini.
        return self.esp.tunggu_benda(ada=ada, timeout=timeout)

    def detak(self):
        """Tahan watchdog ESP selama lengan sibuk bergerak lama."""
        self.esp.detak()

    def periksa(self):
        if not self.esp.ping():
            raise rnv3.RNV3Error(
                "ESP tidak menjawab PING. Cek kabel USB dan nomor port — "
                "ingat robot dan ESP itu dua port serial berbeda, gampang "
                "tertukar.")
        status = self.esp.status()
        self._kata(f"status ESP: {status}")
        if status["benda"]:
            self._kata("CATATAN: sensor sudah melihat benda sejak awal — "
                       "entah ada yang nyangkut di zona ambil, atau "
                       "sensornya salah pasang.")
        if status["jarak_cm"] is not None:
            self._kata(f"jarak terbaca {status['jarak_cm']:.1f} cm")
        return status
