"""
Aplikasi kontrol robot RNV3 di browser -- pengganti jog.py di terminal.

    python.exe aplikasi.py              # robot asli, port dicari otomatis
    python.exe aplikasi.py --sim        # tanpa robot (simulator)
    python3   aplikasi.py --sim         # dari WSL juga bisa, khusus simulator

Lalu buka http://127.0.0.1:8765 (terbuka sendiri kalau bisa).

ARSITEKTUR

    browser  --HTTP JSON-->  server ini  --antrean-->  SATU thread pekerja  --serial-->  robot

Semua perintah ke robot dijalankan satu per satu oleh SATU thread pekerja,
karena satu port serial hanya boleh dipegang satu pemakai. Thread HTTP tidak
pernah menyentuh port: mereka hanya menaruh perintah di antrean dan membaca
status. Logika gerak (pengaman jangkauan, pembatas percepatan, sapuan saat
tombol ditahan) dipakai ulang dari jog.Jog, bukan ditulis ulang.

KESELAMATAN

  - Server hanya mendengar di 127.0.0.1: perangkat lain di WiFi tidak bisa
    menggerakkan robot. Setiap POST wajib membawa header X-RNV3, jadi halaman
    web lain di browser yang sama juga tidak bisa (header kustom memicu
    preflight CORS yang tidak pernah dijawab server ini).
  - Jog tahan-tombol butuh "detak" dari browser tiap ~100 ms. Tab ditutup,
    browser hang, atau WiFi putus -> detak berhenti -> jog berhenti setelah
    potongan yang sedang jalan (maks. sepanjang "sapuan").
  - Firmware tidak bisa membatalkan G0 yang sudah dikirim. "Berhenti" berarti:
    selesaikan gerakan yang sedang jalan, lalu tidak ada gerakan berikutnya.
  - Keluar dari aplikasi (Ctrl-C) TIDAK mematikan motor -- lengan tetap
    terkunci di tempatnya. Pakai tombol "Matikan robot" untuk parkir + M18.
"""

import argparse
import collections
import contextlib
import copy
import http.server
import io
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import jalur
import jog as mod_jog
import kalibrasi as mod_kalibrasi
import kontrak_kamera
import konveyor as mod_konveyor
import preset as mod_preset
import rnv3
import setelan
from rnv3 import RNV3Error

FOLDER = os.path.dirname(os.path.abspath(__file__))
FOLDER_WEB = os.path.join(FOLDER, "aplikasi_web")
BERKAS_TITIK = os.path.join(FOLDER, "titik-tanda.json")
BERKAS_PRESET = mod_preset.BERKAS_DEFAULT
KECEPATAN_PRESET = (20.0, 40.0, 80.0)   # mm/s pilihan putar ulang
F_COBA = 20.0                           # "Coba sekali" selalu pelan
PORT_WEB = 8765

DETAK_MAKS = 0.35   # s tanpa detak dari browser -> jog tahan berhenti
JEDA_TAP = 0.30     # s pertama setelah ditekan = "tekan sekali" (presisi)
F_PERGI = 40.0      # mm/s saat pergi ke titik tersimpan
F_REL = 40.0        # mm/s rel; sama dengan pick_place.F_RAIL (lebih cepat = step hilang)

# Tab Sortir. Kamera konveyor di laptop ini nomor 1 (nomor 0 = webcam laptop);
# ganti lewat --kamera kalau dipasang di laptop lain.
BERKAS_KALIBRASI = os.path.join(FOLDER, "kalibrasi.json")
KAMERA_SUMBER = "1"
BATAS_NYALA_KAMERA = 120.0   # s; memuat YOLO pertama kali bisa ~20-60 s
LOG_SERVER_KAMERA = os.path.join(tempfile.gettempdir(), "rnv3-server-kamera.log")

# Ukuran yang memakai X/Y harus diambil dengan rel di E=0: X/Y dihitung dari
# badan robot, dan badan ikut bergeser bersama rel.
# Parkir TIDAK termasuk: titik parkir menyimpan posisi rel-nya sendiri.
KUNCI_PERLU_REL_NOL = {"bak_koin", "bak_tutup_botol"}
TOLERANSI_REL = 0.5  # mm

# Perintah yang dijalankan LANGSUNG di thread HTTP: tidak menyentuh port
# serial, jadi boleh diproses walau pekerja sedang sibuk.
LANGSUNG = {"berhenti", "jog_detak", "jog_lepas", "setel_jog", "tandai",
            "hapus_titik",
            # rekam & kelola preset hanya mengubah data, bukan robot
            "rekam_mulai", "rekam_catat", "rekam_tunggu", "rekam_hapus_langkah",
            "rekam_simpan", "rekam_batal", "preset_hapus", "preset_pulihkan",
            "preset_kosongkan_sampah",
            # kendali putar harus bisa masuk SELAGI preset berjalan
            "preset_jeda", "preset_lanjut", "preset_berhenti",
            # ...dan berhenti sortir SELAGI sortir berjalan
            "sortir_berhenti"}

SUMBU_JOG = {"rot", "y", "z", "e"}


class AplikasiError(RuntimeError):
    """Perintah ditolak sebelum menyentuh robot (alasan untuk operator)."""


class Log:
    """Catatan kejadian untuk panel log. Thread-safe, dibaca bertahap."""

    def __init__(self, maks=400):
        self._isi = collections.deque(maxlen=maks)
        self._id = 0
        self._kunci = threading.Lock()

    def tulis(self, teks, jenis="info"):
        teks = str(teks).rstrip()
        if not teks:
            return
        with self._kunci:
            for baris in teks.splitlines():
                if baris.strip():
                    self._id += 1
                    self._isi.append({"id": self._id, "jenis": jenis,
                                      "t": time.strftime("%H:%M:%S"),
                                      "teks": baris.rstrip()})

    def sejak(self, id_terakhir):
        with self._kunci:
            return [b for b in self._isi if b["id"] > id_terakhir]



class Pengendali:
    """Pemilik tunggal robot. Lihat docstring modul untuk aturan thread.

    buat_robot(port, sim) -> objek RNV3 yang belum di-home. Disuntikkan supaya
    pengujian bisa memakai simulator tanpa membuka port sungguhan.
    """

    def __init__(self, buat_robot=None, berkas_setelan=None,
                 berkas_titik=BERKAS_TITIK, jeda_sim=True,
                 berkas_preset=BERKAS_PRESET, buat_kamera=None,
                 kamera_sumber=KAMERA_SUMBER):
        self.buat_robot = buat_robot or _buat_robot
        # buat_kamera() -> SumberDeteksi dalam mm. Disuntikkan oleh pengujian;
        # bawaannya server_kamera.py + kalibrasi.json (lihat _kamera_siap).
        self.buat_kamera = buat_kamera
        self.kamera_sumber = kamera_sumber
        self.sortir = None           # jalur.Jalur selagi tab Sortir berjalan
        self.sortir_terakhir = None  # snapshot setelah berhenti (hitungan tetap tampil)
        self._proses_kamera = None   # server_kamera.py yang kita nyalakan sendiri
        self.berkas_setelan = berkas_setelan
        self.berkas_titik = berkas_titik
        self.berkas_preset = berkas_preset
        self.jeda_sim = jeda_sim
        self.log = Log()

        self.bot = None
        self.jog = None
        self.sim = False
        self.vakum = False
        self.sibuk = None            # teks aksi yang sedang jalan, atau None
        self.masalah = []            # hasil jalur.validasi_konfigurasi()
        self.setelan = setelan.muat(berkas_setelan)
        setelan.terapkan(self.setelan)
        self.titik = self._muat_titik()
        self.preset, galat = mod_preset.muat(berkas_preset)
        if galat:
            self.log.tulis(f"{galat}; daftar preset dimulai kosong (berkas lama "
                           f"tidak ditimpa sampai ada perubahan)", "peringatan")
        self.rekam = None            # list langkah selagi merekam, atau None
        self.rekam_otomatis = False  # True = tiap gerakan lengan ikut tercatat
        self._ruas_rekam = None      # ruas jog yang masih boleh diperpanjang
        self.putar = None           # kemajuan preset yang sedang diputar
        self._status_preset = (None, None)
        self._jeda = threading.Event()

        self._antrean = queue.Queue()
        self._kunci = threading.Lock()
        self._tahan = None           # token jog tahan yang masih aktif
        self._detak = 0.0
        # Token yang "lepas"-nya tiba SEBELUM "jog"-nya (dua permintaan HTTP
        # bisa saling menyalip). Jog dengan token ini cukup satu langkah.
        self._dilepas = collections.deque(maxlen=32)
        self._peta_cache = (None, None)
        self._henti = threading.Event()
        self._pekerja = threading.Thread(target=self._kerja, daemon=True,
                                         name="pekerja-robot")
        self._pekerja.start()
        self._validasi()

    # -- antarmuka untuk HTTP --------------------------------------------------
    def kirim(self, aksi, data=None):
        """Terima satu perintah. Return pesan singkat; raise AplikasiError."""
        data = data or {}
        if aksi in LANGSUNG:
            return getattr(self, f"_aksi_{aksi}")(**data)
        if not hasattr(self, f"_aksi_{aksi}"):
            raise AplikasiError(f"perintah tidak dikenal: {aksi}")
        if aksi in ("matikan", "rilis_motor") and not data.get("dipegang"):
            # Ditolak di sini, bukan di pekerja, supaya operator langsung
            # melihat alasannya di layar, bukan belakangan di log.
            raise AplikasiError("konfirmasi dulu bahwa lengan sedang dipegang")
        with self._kunci:
            if self.sibuk is not None or not self._antrean.empty():
                raise AplikasiError(f"tunggu dulu, robot sedang {self.sibuk or 'sibuk'}")
            self.sibuk = _LABEL.get(aksi, aksi)
            if aksi == "jog":
                self._tahan = data.get("token")
                self._detak = time.monotonic()
        self._henti.clear()
        self._antrean.put((aksi, data))
        return "diterima"

    def tunggu_selesai(self, batas=10.0):
        """Tunggu antrean kosong dan pekerja diam. Untuk pengujian."""
        akhir = time.monotonic() + batas
        while time.monotonic() < akhir:
            if self.sibuk is None and self._antrean.empty():
                return True
            time.sleep(0.005)
        return False

    def status(self, sejak=0):
        b, j = self.bot, self.jog
        s = {
            "tersambung": b is not None,
            "sim": self.sim,
            "port": getattr(b, "port", None),
            "sibuk": self.sibuk,
            "perlu_homing": bool(b is None or b.butuh_home),
            "posisi_dipercaya": bool(b is not None and b.position_known),
            "motor_terkunci": bool(j.motor_terkunci) if j else False,
            "vakum": self.vakum,
            "posisi": ({"x": b.x, "y": b.y, "z": b.z, "e": b.e}
                       if b is not None else None),
            "jog": None,
            "setelan": self.setelan,
            "setelan_berkas": self.berkas_setelan or setelan.berkas_aktif(),
            "masalah": self.masalah,
            "batas": dict(rnv3.BATAS_FISIK),
            "batas_terukur": rnv3.BATAS_TERUKUR,
            "titik": self.titik,
            "konstanta": {"z_safe": jalur.Z_SAFE, "belt_y": jalur.BELT_Y,
                          "belt_x_lebar": jalur.BELT_X_LEBAR,
                          "home": [rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z],
                          "rel_panjang": rnv3.RAIL_LENGTH},
            "log": self.log.sejak(sejak),
            "rekam": self.rekam,
            "rekam_otomatis": self.rekam_otomatis,
            "preset": self._preset_dengan_status(),
            "sampah": [{"id": p["id"], "nama": p["nama"],
                        "dihapus": p.get("dihapus", ""),
                        "jumlah": len(p["langkah"])} for p in self.preset["sampah"]],
            "putar": self.putar,
            "kecepatan_preset": list(KECEPATAN_PRESET),
            "sortir": self._status_sortir(),
        }
        if j is not None:
            s["jog"] = {
                "i_step": j.i_step, "steps": mod_jog.STEPS,
                "i_accel": j.i_accel, "accel": mod_jog.A_PILIHAN,
                "i_sapuan": j.i_sapuan, "sapuan": mod_jog.SAPUAN_PILIHAN,
                "sisa": list(j.sisa_arah) if j.sisa_arah else None,
                "pesan": j.pesan,
            }
        if b is not None:
            s["peta"] = self._peta(b.z)
        return s

    def tutup(self):
        """Lepas port. Motor TIDAK dimatikan -- lengan tetap terkunci."""
        self._aksi_berhenti()
        if self._proses_kamera is not None and self._proses_kamera.poll() is None:
            self._proses_kamera.terminate()
        if self.bot is not None:
            try:
                if self.vakum:
                    self.bot.vacuum(False)
            except RNV3Error:
                pass
            self.bot.close()
            self.bot = self.jog = None

    # -- pekerja ---------------------------------------------------------------
    def _kerja(self):
        while True:
            aksi, data = self._antrean.get()
            b = self.bot
            sebelum = (b.x, b.y, b.z, b.e) if b is not None else None
            # Rekam otomatis: gerakan selain jog (pulang, rel ke 0, pergi ke
            # titik/langkah) bisa terdiri dari beberapa ruas -- naik, pindah,
            # turun. Tiap ruas dicatat, bukan cuma ujungnya; kalau hanya
            # ujungnya, putar ulang memotong lurus dan menyeret benda.
            dibungkus = (aksi != "jog" and b is not None
                         and self.rekam is not None and self.rekam_otomatis)
            if dibungkus:
                move_asli = b.move

                def _move_tercatat(*a, **kw):
                    hasil_move = move_asli(*a, **kw)
                    self._rekam_gerak(None)
                    return hasil_move
                b.move = _move_tercatat
            try:
                hasil = getattr(self, f"_aksi_{aksi}")(**data)
                if hasil:
                    self.log.tulis(hasil, "ok")
            except AplikasiError as e:
                self.log.tulis(str(e), "galat")
            except RNV3Error as e:
                self.log.tulis(f"robot menolak: {e}", "galat")
            except Exception as e:                       # noqa: BLE001
                # Pekerja tidak boleh mati: kalau mati, semua tombol diam
                # tanpa penjelasan.
                self.log.tulis(f"{type(e).__name__}: {e}", "galat")
            finally:
                if dibungkus:
                    del b.move           # kembali ke method kelas
                # Jaring pengaman untuk gerakan yang tidak lewat move()
                # (mis. homing). Dicatat sebelum sibuk dilepas supaya
                # "Catat posisi" tidak menyalip.
                b = self.bot
                if (aksi != "jog" and b is not None and sebelum is not None
                        and (b.x, b.y, b.z, b.e) != sebelum):
                    self._rekam_gerak(None)
                with self._kunci:
                    self.sibuk = None
                    self._tahan = None

    def _tangkap(self, fungsi, *a, **kw):
        """Jalankan fungsi yang mencetak ke stdout; salin cetakannya ke log."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                return fungsi(*a, **kw)
            finally:
                self.log.tulis(buf.getvalue())

    def _perlu_bot(self, homing=True):
        if self.bot is None:
            raise AplikasiError("robot belum tersambung")
        if homing and (self.bot.butuh_home or self.jog is None):
            raise AplikasiError("homing dulu sebelum menggerakkan lengan")
        if homing and not self.bot.position_known:
            raise AplikasiError("posisi lengan tidak dipercaya -- homing ulang")

    def _jeda_sim(self, jarak, speed):
        if self.sim and self.jeda_sim and speed > 0:
            time.sleep(min(1.0, jarak / speed))

    # -- aksi: sambungan -------------------------------------------------------
    def _aksi_sambung(self, port=None, sim=False):
        if self.bot is not None:
            raise AplikasiError("sudah tersambung")
        try:
            bot = self.buat_robot(port, sim)
        except AplikasiError:
            raise
        except Exception as e:                           # noqa: BLE001
            raise AplikasiError(
                f"gagal membuka port {port or '(otomatis)'}: {e}. Pastikan GUI "
                f"robot / Serial Monitor sudah ditutup dan di WSL pakai "
                f"python.exe") from None
        bot.verbose = False
        if not self._tangkap(mod_jog.praterbang, bot):
            bot.close()
            raise AplikasiError("pra-terbang gagal: config kode tidak cocok "
                                "dengan robot (lihat log)")
        self.bot, self.jog, self.sim, self.vakum = bot, None, bool(sim), False
        return (f"tersambung ke {bot.port}. Motor lepas sampai homing -- "
                f"tekan Homing")

    def _aksi_putus(self):
        if self.bot is None:
            raise AplikasiError("memang belum tersambung")
        self.tutup()
        return "sambungan diputus. Motor dibiarkan seperti adanya"

    def _aksi_homing(self):
        self._perlu_bot(homing=False)
        self.vakum = False
        self.bot.home()
        p = self.bot.position()
        self.jog = mod_jog.Jog(self.bot)
        return (f"homing selesai: X{p['x']:.1f} Y{p['y']:.1f} Z{p['z']:.1f} "
                f"E{p['e']:.1f}. Cek: lengan bawah tegak, lengan atas datar")

    # -- aksi: jog -------------------------------------------------------------
    def _aksi_jog(self, sumbu, arah, token=None):
        """Satu tekanan: langkah presisi; kalau masih ditahan, sapuan."""
        self._perlu_bot()
        if sumbu not in SUMBU_JOG or arah not in (-1, 1):
            raise AplikasiError(f"arah jog tidak valid: {sumbu} {arah}")
        j = self.jog
        mulai = time.monotonic()
        if j.gerak(sumbu, arah, step=j.step, speed=j.speed):
            self._rekam_gerak(sumbu)
            self._jeda_sim(j.step, j.speed)
        else:
            self.log.tulis(j.pesan, "peringatan")
            return None
        while True:
            with self._kunci:
                masih = (self._tahan == token
                         and token not in self._dilepas
                         and time.monotonic() - self._detak < DETAK_MAKS)
            if not masih or self._henti.is_set():
                break
            lama = time.monotonic() - mulai
            if lama < JEDA_TAP:
                time.sleep(0.02)
                continue
            st = next(s for batas, s in j.tangga if lama - JEDA_TAP < batas)
            sp = mod_jog.batas_speed(st, j.profil, j.a_target)
            if not j.gerak(sumbu, arah, step=st, speed=sp):
                self.log.tulis(j.pesan, "peringatan")
                break
            self._rekam_gerak(sumbu)
            self._jeda_sim(st, sp)
        # Jog yang berhasil tidak dicatat: posisinya sudah tampil terus, dan
        # satu sesi ukur bisa berisi ratusan jog.
        return None

    def _aksi_jog_detak(self, token=None):
        with self._kunci:
            if token is not None and token == self._tahan:
                self._detak = time.monotonic()
        return "ok"

    def _aksi_jog_lepas(self, token=None):
        with self._kunci:
            if token is None or token == self._tahan:
                self._tahan = None
            if token is not None:
                self._dilepas.append(token)
        return "ok"

    def _aksi_setel_jog(self, i_step=None, i_accel=None, i_sapuan=None):
        if self.jog is None:
            raise AplikasiError("homing dulu")
        j = self.jog
        for nama, nilai, daftar in (("i_step", i_step, mod_jog.STEPS),
                                    ("i_accel", i_accel, mod_jog.A_PILIHAN),
                                    ("i_sapuan", i_sapuan,
                                     mod_jog.SAPUAN_PILIHAN)):
            if nilai is None:
                continue
            if not isinstance(nilai, int) or not 0 <= nilai < len(daftar):
                raise AplikasiError(f"{nama} di luar pilihan: {nilai}")
            setattr(j, nama, nilai)
        return "ok"

    def _aksi_berhenti(self):
        self._henti.set()
        self._jeda.clear()           # preset yang sedang dijeda harus bangun untuk berhenti
        if self.sortir is not None:
            self.sortir.minta_berhenti()
        with self._kunci:
            self._tahan = None
        dibuang = 0
        while True:
            try:
                self._antrean.get_nowait()
                dibuang += 1
            except queue.Empty:
                break
        if dibuang:
            with self._kunci:
                self.sibuk = None
        if self.sibuk:
            self.log.tulis("berhenti: gerakan yang sedang jalan diselesaikan "
                           "dulu, tidak ada gerakan berikutnya", "peringatan")
        return "berhenti"

    # -- aksi: alat ------------------------------------------------------------
    def _aksi_vakum(self, nyala):
        self._perlu_bot(homing=False)
        self.bot.vacuum(bool(nyala))
        self.vakum = bool(nyala)
        if self.jog:
            self.jog.vakum = self.vakum
        if self.rekam is not None:
            self.rekam.append({"jenis": "vakum", "nyala": self.vakum})
        return f"vakum {'NYALA' if nyala else 'MATI'}"

    def _aksi_gripper(self, buka):
        self._perlu_bot(homing=False)
        self.bot.gripper(rnv3.SERVO_OPEN if buka else rnv3.SERVO_CLOSED)
        if self.rekam is not None:
            self.rekam.append({"jenis": "gripper", "buka": bool(buka)})
        return f"gripper {'buka' if buka else 'genggam'}"

    def _aksi_posisi(self):
        self._perlu_bot(homing=False)
        x0, y0, z0, e0 = self.bot.x, self.bot.y, self.bot.z, self.bot.e
        p = self.bot.position()
        selisih = math.dist((x0, y0, z0, e0), (p["x"], p["y"], p["z"], p["e"]))
        pesan = (f"robot melaporkan X{p['x']:.2f} Y{p['y']:.2f} "
                 f"Z{p['z']:.2f} E{p['e']:.2f}")
        if selisih > 0.5:
            # Python melacak posisi sendiri; beda dengan laporan robot berarti
            # ada gerakan yang dibatalkan firmware di tengah jalan.
            self.log.tulis(f"posisi hitungan Python beda {selisih:.1f} mm dari "
                           f"laporan robot -- posisi disamakan ke laporan robot",
                           "peringatan")
        return pesan

    # -- aksi: pulang, parkir, matikan -----------------------------------------
    def _aksi_pulang(self):
        self._perlu_bot()
        self.jog.pulang()
        return self.jog.pesan

    def _aksi_parkir(self):
        self._perlu_bot()
        if not self.jog.parkir():
            raise AplikasiError(self.jog.pesan)
        return self.jog.pesan

    def _aksi_selesai(self):
        """Pulang ke home, motor TETAP terkunci. Aplikasi tetap jalan."""
        self._perlu_bot()
        if self.vakum:
            self._aksi_vakum(False)
        if not self.jog.pulang():
            raise AplikasiError(self.jog.pesan)
        return "selesai: lengan di home, motor tetap terkunci"

    def _aksi_matikan(self, dipegang=False):
        """Akhir hari: pulang ke home, lalu M18. Wajib konfirmasi dipegang.

        Lengan berhenti tinggi (Z home), jadi saat motor lepas lengan jatuh
        kalau tidak dipegang -- karena itu konfirmasi dipegang wajib.
        """
        if not dipegang:
            raise AplikasiError("konfirmasi dulu bahwa lengan sedang dipegang")
        self._perlu_bot()
        if self.vakum:
            self._aksi_vakum(False)
        if not self.jog.pulang():
            raise AplikasiError(f"pulang gagal, motor TIDAK dimatikan: "
                                f"{self.jog.pesan}")
        self.bot.steppers(False)
        self.jog.motor_terkunci = False
        return ("motor dimatikan di home. Saklar 12V aman dimatikan. "
                "Mau gerak lagi: homing ulang")

    def _aksi_rilis_motor(self, dipegang=False):
        if not dipegang:
            raise AplikasiError("konfirmasi dulu bahwa lengan sedang dipegang")
        self._perlu_bot(homing=False)
        if self.jog is None:
            self.bot.steppers(False)
        else:
            self.jog.rilis_motor()
        return "motor dirilis: lengan bisa digeser tangan. Homing ulang setelahnya"

    # -- aksi: ukur ------------------------------------------------------------
    def _aksi_ukur_simpan(self, kunci, paksa=False):
        self._perlu_bot()
        b = self.bot
        if kunci in KUNCI_PERLU_REL_NOL and abs(b.e) > TOLERANSI_REL and not paksa:
            # X/Y dihitung dari BADAN robot, dan badan ikut bergeser bersama
            # rel. Bak yang diukur di E=190 meleset 19 cm saat robot di E=0.
            raise AplikasiError(
                f"rel di E={b.e:.1f}, bukan 0: posisi {_NAMA_UKUR[kunci]} akan "
                f"meleset saat robot di E=0. Bawa rel ke 0 dulu")
        try:
            baru = setelan.isi_dari_posisi(self.setelan, kunci, b.x, b.y, b.z,
                                           e=b.e)
            berkas = setelan.simpan(baru, self.berkas_setelan)
        except (ValueError, RuntimeError, OSError) as e:
            raise AplikasiError(f"setelan tidak tersimpan: {e}") from None
        self.setelan = baru
        setelan.terapkan(baru)
        if kunci == "parkir":
            self.jog.parkir_titik = tuple(baru["parkir"])
        self._validasi()
        pesan = f"{_NAMA_UKUR[kunci]} disimpan ke {os.path.basename(berkas)}"
        if self.masalah:
            pesan += f" -- tapi ada {len(self.masalah)} masalah konfigurasi"
        return pesan

    def _aksi_ukur_reset(self):
        """Kembalikan SEMUA angka ukur ke bawaan (hapus berkas setelan)."""
        try:
            ada = setelan.hapus(self.berkas_setelan)
        except (RuntimeError, OSError) as e:
            raise AplikasiError(f"setelan tidak bisa direset: {e}") from None
        self.setelan = setelan.muat(self.berkas_setelan)
        setelan.terapkan(self.setelan)
        if self.jog is not None:
            self.jog.parkir_titik = tuple(self.setelan["parkir"])
        self._validasi()
        return ("semua angka ukur kembali ke angka awal" if ada
                else "angka ukur memang sudah angka awal")

    def _aksi_rel_nol(self):
        """Bawa rel ke E=0 di XYZ yang sama -- titik acuan semua ukuran."""
        self._perlu_bot()
        self.bot.move(e=0.0, f=F_REL)
        return "rel di E=0"

    def _aksi_ukur_pergi(self, kunci):
        """Pergi ke titik tersimpan lewat Z aman. Tidak turun ke bak."""
        self._perlu_bot()
        b, s = self.bot, self.setelan
        if kunci == "parkir":
            return self._aksi_parkir()
        if not kunci.startswith("bak_"):
            raise AplikasiError("tinggi Z tidak punya titik XY untuk didatangi")
        tx, ty = s["bak"][kunci[len("bak_"):]]
        if b.z < jalur.Z_SAFE:
            b.move(z=jalur.Z_SAFE, f=F_PERGI)
        b.move(x=tx, y=ty, f=F_PERGI)
        return f"di atas {_NAMA_UKUR[kunci]} pada Z aman {jalur.Z_SAFE:.0f}"

    def _aksi_rekam_batas(self):
        self._perlu_bot()
        if not self._tangkap(self.jog.rekam_batas):
            raise AplikasiError(self.jog.pesan)
        return self.jog.pesan

    def _aksi_tandai(self):
        self._perlu_bot()
        if self.sibuk is not None:
            # Sama dengan rekam_catat: selagi bergerak, posisi yang dilacak
            # masih posisi SEBELUM gerakan.
            raise AplikasiError("tunggu lengan berhenti dulu, baru tandai titik")
        b = self.bot
        self.titik.append({"x": round(b.x, 2), "y": round(b.y, 2),
                           "z": round(b.z, 2), "e": round(b.e, 2),
                           "waktu": time.strftime("%Y-%m-%d %H:%M:%S")})
        self._simpan_titik()
        self.log.tulis(f"titik #{len(self.titik)} ditandai: X{b.x:.1f} "
                       f"Y{b.y:.1f} Z{b.z:.1f} E{b.e:.1f}", "ok")
        return "ok"

    def _aksi_hapus_titik(self, nomor=None, semua=False, terakhir=False):
        if semua:
            n = len(self.titik)
            self.titik = []
        elif terakhir:
            if not self.titik:
                raise AplikasiError("belum ada titik")
            self.titik.pop()
            n = 1
        else:
            if not isinstance(nomor, list) or not nomor:
                raise AplikasiError("pilih nomor titik yang mau dihapus")
            buang = {int(i) for i in nomor}
            if not buang <= set(range(1, len(self.titik) + 1)):
                raise AplikasiError(f"nomor tidak ada: {sorted(buang)}")
            self.titik = [t for i, t in enumerate(self.titik, 1)
                          if i not in buang]
            n = len(buang)
        self._simpan_titik()
        self.log.tulis(f"{n} titik dihapus, sisa {len(self.titik)}", "ok")
        return "ok"

    # -- aksi: rekam preset ----------------------------------------------------
    def _aksi_rekam_mulai(self, otomatis=False):
        self._perlu_bot()
        if self.putar is not None:
            raise AplikasiError("preset sedang diputar")
        self.rekam = []
        self.rekam_otomatis = bool(otomatis)
        self._ruas_rekam = None
        if self.rekam_otomatis:
            # Posisi awal ikut dicatat: ruas pertama diputar dari sini, bukan
            # dari titik jog pertama.
            self._rekam_gerak(None)
            self.log.tulis("mulai merekam: semua gerakan lengan tercatat; vakum "
                           "dan gripper juga", "ok")
        else:
            self.log.tulis("mulai merekam: jog lalu Catat posisi; vakum dan "
                           "gripper tercatat otomatis", "ok")
        return "ok"

    def _rekam_gerak(self, sumbu):
        """Rekam otomatis: catat posisi lengan sekarang sebagai langkah gerak.

        Jog beruntun di sumbu yang sama digabung jadi SATU ruas lurus (hanya
        titik ujungnya yang disimpan) -- satu tahanan tombol bisa berisi
        puluhan potongan kecil, dan kelewatan lalu mundur di sumbu yang sama
        tetap satu garis. Jog putar (rot) jalannya melengkung, jadi ruasnya
        dipotong begitu tali busurnya melendut lebih dari jog.LENDUT_MAKS;
        kalau tidak, putar ulang akan memotong lurus lewat dalam busur.

        sumbu=None -> titik berdiri sendiri, tidak pernah digabung.
        """
        r = self.rekam              # salinan lokal: Simpan/Batal bisa masuk dari thread HTTP
        if r is None or not self.rekam_otomatis:
            return
        b = self.bot
        baru = mod_preset.langkah_gerak(b.x, b.y, b.z, b.e)
        gerak = [l for l in r if l["jenis"] == "gerak"]
        if gerak and gerak[-1] == baru:
            return
        ruas = self._ruas_rekam
        if (sumbu is not None and ruas is not None and ruas["sumbu"] == sumbu
                and r and r[-1] is ruas["langkah"]
                and (sumbu != "rot" or _busur_muat(ruas["awal"], baru))):
            r[-1] = baru
            ruas["langkah"] = baru
            return
        awal = gerak[-1] if gerak else baru
        r.append(baru)
        self._ruas_rekam = ({"sumbu": sumbu, "langkah": baru, "awal": awal}
                            if sumbu is not None else None)

    def _perlu_rekam(self):
        if self.rekam is None:
            raise AplikasiError("tekan Rekam baru dulu")

    def _aksi_rekam_catat(self):
        self._perlu_rekam()
        self._perlu_bot()
        if self.sibuk is not None:
            # Posisi yang dilacak baru diperbarui SETELAH gerakan selesai;
            # mencatat sekarang = menyimpan posisi lama tanpa disadari.
            raise AplikasiError("tunggu lengan berhenti dulu, baru catat posisi")
        b = self.bot
        langkah = mod_preset.langkah_gerak(b.x, b.y, b.z, b.e)
        gerak = [l for l in self.rekam if l["jenis"] == "gerak"]
        if gerak and gerak[-1] == langkah:
            raise AplikasiError("posisi ini sama dengan posisi yang terakhir dicatat")
        self.rekam.append(langkah)
        return "ok"

    def _aksi_rekam_tunggu(self, detik):
        self._perlu_rekam()
        try:
            self.rekam.append(mod_preset.rapikan_langkah(
                {"jenis": "tunggu", "detik": detik}))
        except mod_preset.PresetError as e:
            raise AplikasiError(str(e)) from None
        return "ok"

    def _aksi_rekam_hapus_langkah(self):
        self._perlu_rekam()
        if not self.rekam:
            raise AplikasiError("rekaman masih kosong")
        self.rekam.pop()
        return "ok"

    def _aksi_rekam_simpan(self, nama):
        self._perlu_rekam()
        if not any(l["jenis"] == "gerak" for l in self.rekam):
            raise AplikasiError("catat minimal satu posisi sebelum menyimpan")
        try:
            baru, p = mod_preset.tambah(self.preset, nama, self.rekam)
        except mod_preset.PresetError as e:
            raise AplikasiError(str(e)) from None
        self._simpan_preset(baru)
        self.rekam = None
        self.log.tulis(f"preset '{p['nama']}' disimpan ({len(p['langkah'])} langkah)", "ok")
        return "ok"

    def _aksi_rekam_batal(self):
        self.rekam = None
        return "ok"

    # -- aksi: kelola preset ---------------------------------------------------
    def _aksi_preset_hapus(self, ids=None, semua=False, terakhir=False):
        daftar = self.preset["preset"]
        if semua:
            ids = [p["id"] for p in daftar]
        elif terakhir:
            ids = [daftar[-1]["id"]] if daftar else []
        if not ids:
            raise AplikasiError("tidak ada preset yang dipilih")
        try:
            baru = mod_preset.buang(self.preset, ids)
        except mod_preset.PresetError as e:
            raise AplikasiError(str(e)) from None
        self._simpan_preset(baru)
        self.log.tulis(f"{len(ids)} preset dipindah ke sampah", "ok")
        return "ok"

    def _aksi_preset_pulihkan(self, id):                  # noqa: A002 (nama dari JSON)
        try:
            self._simpan_preset(mod_preset.pulihkan(self.preset, id))
        except mod_preset.PresetError as e:
            raise AplikasiError(str(e)) from None
        return "ok"

    def _aksi_preset_kosongkan_sampah(self):
        self._simpan_preset(mod_preset.kosongkan_sampah(self.preset))
        self.log.tulis("sampah preset dikosongkan", "ok")
        return "ok"

    # -- aksi: putar preset ----------------------------------------------------
    def _aksi_preset_jalan(self, ids, ulang=1, kecepatan=40.0):
        """Putar preset terpilih berurutan, `ulang` kali (0 = terus)."""
        self._perlu_bot()
        if self.rekam is not None:
            raise AplikasiError("selesaikan atau batalkan rekaman dulu")
        if isinstance(ulang, bool) or not isinstance(ulang, int) or not 0 <= ulang <= 9999:
            raise AplikasiError("jumlah ulang tidak valid")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise AplikasiError("daftar preset tidak valid")
        if float(kecepatan) not in KECEPATAN_PRESET:
            raise AplikasiError(f"kecepatan harus salah satu dari {KECEPATAN_PRESET}")
        try:
            daftar = [copy.deepcopy(mod_preset.cari(self.preset, i)) for i in ids]
        except mod_preset.PresetError as e:
            raise AplikasiError(str(e)) from None
        if not daftar:
            raise AplikasiError("centang minimal satu preset")
        b = self.bot
        ok, alasan = mod_preset.validasi_urutan(daftar, (b.x, b.y, b.z, b.e),
                                                berulang=ulang != 1)
        if not ok:
            # Ditolak SEBELUM bergerak: lebih baik tidak jalan sama sekali
            # daripada berhenti di tengah putaran ke-37.
            raise AplikasiError(f"tidak dijalankan, {alasan}")
        return self._putar(daftar, ulang, float(kecepatan))

    def _aksi_preset_coba(self, id):                      # noqa: A002
        return self._aksi_preset_jalan([id], ulang=1, kecepatan=F_COBA)

    def _aksi_preset_ke_langkah(self, id, nomor):         # noqa: A002
        """Pergi ke satu langkah gerak saja, lewat tinggi aman."""
        self._perlu_bot()
        try:
            l = mod_preset.cari(self.preset, id)["langkah"][int(nomor) - 1]
        except (mod_preset.PresetError, IndexError, ValueError, TypeError):
            raise AplikasiError("langkah tidak ada") from None
        if l["jenis"] != "gerak":
            raise AplikasiError("hanya langkah gerak yang punya posisi")
        b = self.bot
        asal = (b.x, b.y, b.z, b.e)
        rute = mod_preset.rencana_gerak(asal, mod_preset._titik(l), True)
        ok, alasan = mod_preset.periksa_rute(asal, rute)
        if not ok:
            raise AplikasiError(f"tidak bisa ke sana: {alasan}")
        for t in rute:
            self._gerak_ke(t, F_PERGI)
        return f"di langkah {nomor}"

    def _aksi_preset_jeda(self):
        if self.putar is None:
            raise AplikasiError("tidak ada preset yang sedang diputar")
        self._jeda.set()
        return "ok"

    def _aksi_preset_lanjut(self):
        self._jeda.clear()
        return "ok"

    def _aksi_preset_berhenti(self):
        if self.putar is None:
            raise AplikasiError("tidak ada preset yang sedang diputar")
        self._henti.set()
        self._jeda.clear()
        return "ok"

    def _putar(self, daftar, ulang, f):
        self._jeda.clear()
        total_putaran = ulang or None
        putaran = 0
        try:
            while total_putaran is None or putaran < total_putaran:
                putaran += 1
                for p in daftar:
                    pertama = True
                    for n, l in enumerate(p["langkah"], 1):
                        self.putar = {"keadaan": "jalan", "putaran": putaran,
                                      "total": total_putaran, "nama": p["nama"],
                                      "langkah_ke": n, "total_langkah": len(p["langkah"])}
                        if not self._tunggu_jeda():
                            return self._putar_dihentikan(putaran)
                        self._jalankan_langkah(l, pertama, f)
                        if l["jenis"] == "gerak":
                            pertama = False
            return f"preset selesai: {putaran} putaran"
        except RNV3Error:
            # Posisi mungkin tidak bisa dipercaya: matikan vakum, JANGAN
            # bergerak otomatis. Galatnya dicatat pekerja.
            self._vakum_mati_diam()
            raise
        finally:
            self.putar = None
            self._jeda.clear()

    def _tunggu_jeda(self):
        """Tahan di sini selama dijeda. False kalau diminta berhenti."""
        while self._jeda.is_set() and not self._henti.is_set():
            if self.putar:
                self.putar["keadaan"] = "jeda"
            time.sleep(0.05)
        return not self._henti.is_set()

    def _jalankan_langkah(self, l, lewat_aman, f):
        jenis = l["jenis"]
        if jenis == "gerak":
            b = self.bot
            for t in mod_preset.rencana_gerak((b.x, b.y, b.z, b.e),
                                              mod_preset._titik(l), lewat_aman):
                self._gerak_ke(t, f)
        elif jenis == "vakum":
            # Langsung ke robot, BUKAN _aksi_vakum: memutar preset tidak
            # boleh ikut menambah langkah ke rekaman.
            self.bot.vacuum(l["nyala"])
            self.vakum = l["nyala"]
            if self.jog:
                self.jog.vakum = self.vakum
        elif jenis == "gripper":
            self.bot.gripper(rnv3.SERVO_OPEN if l["buka"] else rnv3.SERVO_CLOSED)
        elif jenis == "tunggu":
            akhir = time.monotonic() + l["detik"]
            while time.monotonic() < akhir and not self._henti.is_set():
                time.sleep(0.05)

    def _gerak_ke(self, t, f):
        b = self.bot
        jarak = math.dist((b.x, b.y, b.z, b.e), t)
        b.move(x=t[0], y=t[1], z=t[2], e=t[3], f=f)
        self._jeda_sim(jarak, f)

    def _putar_dihentikan(self, putaran):
        """Berhenti: langkah berjalan sudah selesai -> vakum OFF -> home."""
        if self.vakum:
            self.bot.vacuum(False)
            self.vakum = False
            if self.jog:
                self.jog.vakum = False
        if self.bot.position_known and self.jog is not None:
            self.jog.pulang()
        return (f"preset dihentikan di putaran {putaran}: vakum mati, lengan "
                f"pulang ke home, motor tetap terkunci")

    def _vakum_mati_diam(self):
        try:
            if self.vakum:
                self.bot.vacuum(False)
                self.vakum = False
        except RNV3Error:
            pass

    # -- aksi: sortir ----------------------------------------------------------
    def _aksi_sortir_mulai(self):
        """START: kamera mengawasi belt, robot mengambil sendiri benda yang diam.

        Jalan di pekerja sampai Berhenti, jadi selama itu jog/Ukur/preset
        ditolak ("robot sedang menyortir") -- dua pemberi perintah gerak
        tidak boleh berebut lengan.
        """
        self._perlu_bot()
        if self.rekam is not None:
            raise AplikasiError("selesaikan atau batalkan rekaman dulu")
        masalah = jalur.validasi_konfigurasi(cetak=False)
        if masalah:
            raise AplikasiError(f"{len(masalah)} titik di konfigurasi tidak "
                                f"terjangkau -- betulkan di tab Ukur dulu")
        kamera = self._kamera_siap()
        if self._henti.is_set():
            return "sortir dibatalkan sebelum mulai"
        belt = mod_konveyor.KonveyorKamera(
            kamera, tunggu_selamanya=True,
            saring=lambda d: d.terjangkau(jalur.z_ambil(d.label))[0])
        j = jalur.Jalur(self.bot, kamera, belt)
        belt.berhenti_event = j.stop_event
        # Cetakan jalur/konveyor langsung ke panel log SELAGI berjalan --
        # sortir bisa jalan berjam-jam. Lewat _kata, bukan redirect_stdout:
        # redirect itu mengganti sys.stdout seluruh proses, jadi cetakan
        # thread lain ikut tersedot.
        j._kata = belt._kata = lambda msg: self.log.tulis(msg)
        self.sortir = j
        try:
            belt.periksa()
            self._vakum_mati_diam()
            if self.bot.z < jalur.Z_SAFE:
                self.bot.move(z=jalur.Z_SAFE, f=jalur.F_SLOW)
            j.jalankan(0)           # sampai minta_berhenti(); parkir di akhir
        finally:
            self.sortir_terakhir = j.snapshot()
            self.sortir = None
            # Jalur menyalakan/mematikan vakum langsung lewat bot; selesai()
            # selalu mematikannya.
            self.vakum = False
            if self.jog:
                self.jog.vakum = False
        h = j.hitungan
        return ("sortir berhenti: " + ", ".join(f"{k}={v}" for k, v in h.items())
                + ". Lengan parkir, motor tetap terkunci")

    def _aksi_sortir_berhenti(self):
        if self.sortir is None and self.sibuk != _LABEL["sortir_mulai"]:
            raise AplikasiError("sortir tidak sedang jalan")
        self._henti.set()            # kalau masih menyalakan kamera
        if self.sortir is not None:
            self.sortir.minta_berhenti()
        self.log.tulis("berhenti diminta: gerakan yang sedang jalan "
                       "diselesaikan, lalu parkir", "peringatan")
        return "ok"

    def _kamera_siap(self):
        """SumberDeteksi dalam mm; nyalakan server_kamera.py kalau belum jalan."""
        if self.buat_kamera is not None:
            return self.buat_kamera()
        if not os.path.isfile(BERKAS_KALIBRASI):
            raise AplikasiError("kalibrasi.json belum ada -- kalibrasi kamera "
                                "dulu (python kalibrasi.py --sesi --kamera 1)")
        kamera = kontrak_kamera.SumberTCP(
            kalibrasi=mod_kalibrasi.Kalibrasi.muat(BERKAS_KALIBRASI))
        if kamera.ping():
            return kamera
        if self._proses_kamera is None or self._proses_kamera.poll() is not None:
            self.log.tulis(f"menyalakan server kamera (kamera {self.kamera_sumber}) "
                           f"-- memuat YOLO, bisa sampai satu menit")
            with open(LOG_SERVER_KAMERA, "w", encoding="utf-8") as keluaran:
                self._proses_kamera = subprocess.Popen(
                    [sys.executable, "-u", os.path.join(FOLDER, "server_kamera.py"),
                     "--sumber", str(self.kamera_sumber)],
                    cwd=FOLDER, stdout=keluaran, stderr=subprocess.STDOUT)
        akhir = time.monotonic() + BATAS_NYALA_KAMERA
        while time.monotonic() < akhir and not self._henti.is_set():
            if kamera.ping():
                self.log.tulis("server kamera siap", "ok")
                return kamera
            if self._proses_kamera.poll() is not None:
                raise AplikasiError("server kamera mati saat menyala: "
                                    + _ekor_berkas(LOG_SERVER_KAMERA))
            time.sleep(1.0)
        if self._henti.is_set():
            raise AplikasiError("dibatalkan saat menyalakan kamera")
        raise AplikasiError(f"server kamera belum menjawab setelah "
                            f"{BATAS_NYALA_KAMERA:.0f} s: "
                            + _ekor_berkas(LOG_SERVER_KAMERA))

    def _status_sortir(self):
        j = self.sortir
        if j is not None:
            s = j.snapshot()
        elif self.sortir_terakhir is not None:
            s = dict(self.sortir_terakhir)
        else:
            s = {"state": "mati", "hitungan": {k: 0 for k in jalur.JENIS},
                 "benda_terakhir": None, "hasil_terakhir": None}
        s["aktif"] = j is not None
        s["menyalakan"] = j is None and self.sibuk == _LABEL["sortir_mulai"]
        s["berjalan"] = j is not None and not j.stop_event.is_set()
        return s

    # -- bantu -----------------------------------------------------------------
    def _simpan_preset(self, baru):
        try:
            self.preset = mod_preset.simpan(baru, self.berkas_preset)
        except OSError as e:
            raise AplikasiError(f"preset tidak tersimpan: {e}") from None

    def _preset_dengan_status(self):
        """Daftar preset + status validasi per langkah, di-cache per isi."""
        kunci = json.dumps(self.preset["preset"], sort_keys=True)
        if self._status_preset[0] == kunci:
            return self._status_preset[1]
        hasil = []
        for p in self.preset["preset"]:
            status = mod_preset.validasi_internal(p)
            hasil.append({**p, "status": status,
                          "ok": all(s["ok"] for s in status)})
        self._status_preset = (kunci, hasil)
        return hasil

    def _validasi(self):
        self.masalah = jalur.validasi_konfigurasi(cetak=False)

    def _muat_titik(self):
        if not self.berkas_titik:
            return []
        try:
            with open(self.berkas_titik, encoding="utf-8") as f:
                isi = json.load(f)
            return [t for t in isi if isinstance(t, dict)]
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            self.log.tulis(f"titik-tanda tidak bisa dibaca ({e}), mulai kosong",
                           "peringatan")
            return []

    def _simpan_titik(self):
        if not self.berkas_titik:
            return
        sementara = self.berkas_titik + ".tmp"
        with open(sementara, "w", encoding="utf-8") as f:
            json.dump(self.titik, f, indent=2)
        os.replace(sementara, self.berkas_titik)

    def _peta(self, z):
        """Wilayah yang boleh dijangkau di tinggi z, per sudut putar.

        Dihitung pakai check_aman() -- sama dengan yang dipakai move() -- lalu
        di-cache per 2 mm Z karena memindai ribuan titik.
        """
        kunci = round(z / 2.0)
        if self._peta_cache[0] == kunci:
            return self._peta_cache[1]
        sudut, rmin, rmax = [], [], []
        for d in range(-90, 91, 3):
            rad = math.radians(d)
            ok_r = [r for r in range(0, 420, 4)
                    if rnv3.check_aman(r * math.sin(rad), r * math.cos(rad),
                                       z)[0]]
            sudut.append(d)
            rmin.append(min(ok_r) if ok_r else None)
            rmax.append(max(ok_r) if ok_r else None)
        hasil = {"z": z, "sudut": sudut, "rmin": rmin, "rmax": rmax}
        self._peta_cache = (kunci, hasil)
        return hasil


_LABEL = {
    "sambung": "menyambung", "putus": "memutus", "homing": "homing",
    "jog": "jog", "vakum": "vakum", "gripper": "gripper",
    "posisi": "membaca posisi", "pulang": "pulang ke home",
    "parkir": "parkir", "selesai": "pulang ke home", "matikan": "mematikan",
    "rilis_motor": "merilis motor", "ukur_simpan": "menyimpan",
    "ukur_pergi": "pergi ke titik", "rekam_batas": "merekam batas",
    "ukur_reset": "mereset setelan", "rel_nol": "membawa rel ke 0",
    "preset_jalan": "memutar preset", "preset_coba": "mencoba preset",
    "preset_ke_langkah": "pergi ke langkah",
    "sortir_mulai": "menyortir",
}


def _ekor_berkas(path, baris=6):
    """Beberapa baris terakhir berkas log, untuk pesan galat."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return " | ".join(l.strip() for l in f.read().splitlines()[-baris:]
                              if l.strip()) or "(log kosong)"
    except OSError:
        return "(log tidak terbaca)"

_NAMA_UKUR = {
    "z_belt": "Z belt", "z_bak": "Z bak", "bak_koin": "bak koin",
    "bak_tutup_botol": "bak tutup botol", "parkir": "titik parkir",
}


def _busur_muat(awal, akhir):
    """True kalau putar dari awal ke akhir masih boleh diputar ulang sebagai
    satu garis lurus: lendut tali busurnya <= jog.LENDUT_MAKS."""
    r = math.hypot(awal["x"], awal["y"])
    sudut = abs(math.atan2(akhir["x"], akhir["y"])
                - math.atan2(awal["x"], awal["y"]))
    sudut = min(sudut, 2 * math.pi - sudut)
    return r * sudut <= mod_jog.busur_maks(awal["x"], awal["y"]) + 1e-6


def _buat_robot(port, sim):
    if sim:
        import simulasi
        return simulasi.robot_palsu(verbose=False)
    if not port:
        port = rnv3.cari_port(diam=True)
        if not port:
            raise AplikasiError(
                "robot tidak ditemukan otomatis. Cek kabel USB data, tombol "
                "power, lalu isi port manual (mis. COM4)")
    return rnv3.RNV3(port, verbose=False)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_JENIS_BERKAS = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8",
                 ".svg": "image/svg+xml"}


def buat_handler(pengendali):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "RNV3"

        def log_message(self, *a):          # bungkam log per-permintaan
            pass

        def _json(self, kode, isi):
            data = json.dumps(isi).encode("utf-8")
            self.send_response(kode)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            jalur_url, _, query = self.path.partition("?")
            if jalur_url == "/api/status":
                sejak = 0
                for bagian in query.split("&"):
                    k, _, v = bagian.partition("=")
                    if k == "sejak" and v.isdigit():
                        sejak = int(v)
                return self._json(200, pengendali.status(sejak))
            nama = "index.html" if jalur_url == "/" else jalur_url.lstrip("/")
            # Hanya berkas langsung di aplikasi_web/, tanpa subfolder dan
            # tanpa "..": server ini tidak boleh menyajikan berkas lain.
            if "/" in nama or "\\" in nama or nama.startswith("."):
                return self._json(404, {"ok": False, "pesan": "tidak ada"})
            path = os.path.join(FOLDER_WEB, nama)
            jenis = _JENIS_BERKAS.get(os.path.splitext(nama)[1])
            if not jenis or not os.path.isfile(path):
                return self._json(404, {"ok": False, "pesan": "tidak ada"})
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", jenis)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/api/perintah":
                return self._json(404, {"ok": False, "pesan": "tidak ada"})
            if self.headers.get("X-RNV3") != "1":
                return self._json(403, {"ok": False, "pesan": "header X-RNV3 wajib"})
            try:
                panjang = int(self.headers.get("Content-Length", "0"))
                isi = json.loads(self.rfile.read(min(panjang, 65536)) or b"{}")
                aksi = isi.pop("aksi")
                if not isinstance(aksi, str):
                    raise ValueError("aksi harus teks")
            except (ValueError, KeyError, AttributeError) as e:
                return self._json(400, {"ok": False, "pesan": f"permintaan rusak: {e}"})
            try:
                pesan = pengendali.kirim(aksi, isi)
            except AplikasiError as e:
                return self._json(409, {"ok": False, "pesan": str(e)})
            except TypeError as e:
                return self._json(400, {"ok": False, "pesan": f"parameter salah: {e}"})
            return self._json(200, {"ok": True, "pesan": pesan})

    return Handler


def buat_server(pengendali, port=PORT_WEB):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                          buat_handler(pengendali))
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="Aplikasi kontrol RNV3 di browser.")
    ap.add_argument("--sim", action="store_true",
                    help="langsung tersambung ke simulator, tanpa robot")
    ap.add_argument("--port-web", type=int, default=PORT_WEB,
                    help=f"port HTTP lokal (bawaan {PORT_WEB})")
    ap.add_argument("--tanpa-browser", action="store_true",
                    help="jangan buka browser otomatis")
    ap.add_argument("--kamera", default=KAMERA_SUMBER,
                    help=f"nomor kamera konveyor untuk tab Sortir (bawaan "
                         f"{KAMERA_SUMBER}; 0 biasanya webcam laptop)")
    a = ap.parse_args(argv)

    p = Pengendali(kamera_sumber=a.kamera)
    if a.sim:
        p.kirim("sambung", {"sim": True})
    try:
        srv = buat_server(p, a.port_web)
    except OSError as e:
        print(f"port {a.port_web} sudah dipakai ({e}). Aplikasi lain masih "
              f"jalan? Atau pakai --port-web 8766")
        return 1
    url = f"http://127.0.0.1:{a.port_web}"
    print(f"Aplikasi RNV3 jalan di {url}  (Ctrl-C untuk keluar)")
    print("Satu port serial satu pemakai: tutup jog.py / GUI robot selama ini jalan.")
    if not a.tanpa_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nmenutup ...")
    finally:
        srv.server_close()
        p.tutup()
        print("port dilepas. Motor TIDAK dimatikan: lengan tetap terkunci.")
        print("Pegang lengan saat mematikan saklar 12V.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
