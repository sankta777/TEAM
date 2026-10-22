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
import http.server
import io
import json
import math
import os
import queue
import sys
import threading
import time
import uuid
import webbrowser

import jalur
import jog as mod_jog
import rnv3
import setelan
from rnv3 import RNV3Error

FOLDER = os.path.dirname(os.path.abspath(__file__))
FOLDER_WEB = os.path.join(FOLDER, "aplikasi_web")
BERKAS_TITIK = os.path.join(FOLDER, "titik-tanda.json")
PORT_WEB = 8765

DETAK_MAKS = 0.35   # s tanpa detak dari browser -> jog tahan berhenti
JEDA_TAP = 0.30     # s pertama setelah ditekan = "tekan sekali" (presisi)
F_PERGI = 40.0      # mm/s saat pergi ke titik tersimpan

# Perintah yang dijalankan LANGSUNG di thread HTTP: tidak menyentuh port
# serial, jadi boleh diproses walau pekerja sedang sibuk.
LANGSUNG = {"berhenti", "jog_detak", "jog_lepas", "setel_jog", "tandai",
            "hapus_titik"}

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
                 berkas_titik=BERKAS_TITIK, jeda_sim=True):
        self.buat_robot = buat_robot or _buat_robot
        self.berkas_setelan = berkas_setelan
        self.berkas_titik = berkas_titik
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
        return f"vakum {'NYALA' if nyala else 'MATI'}"

    def _aksi_gripper(self, buka):
        self._perlu_bot(homing=False)
        self.bot.gripper(rnv3.SERVO_OPEN if buka else rnv3.SERVO_CLOSED)
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
        """Akhir hari: parkir rendah, lalu M18. Wajib konfirmasi dipegang."""
        if not dipegang:
            raise AplikasiError("konfirmasi dulu bahwa lengan sedang dipegang")
        self._perlu_bot()
        if self.vakum:
            self._aksi_vakum(False)
        if not self.jog.parkir():
            raise AplikasiError(f"parkir gagal, motor TIDAK dimatikan: "
                                f"{self.jog.pesan}")
        self.bot.steppers(False)
        self.jog.motor_terkunci = False
        return ("motor dimatikan setelah parkir. Saklar 12V aman dimatikan. "
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
    def _aksi_ukur_simpan(self, kunci):
        self._perlu_bot()
        b = self.bot
        try:
            baru = setelan.isi_dari_posisi(self.setelan, kunci, b.x, b.y, b.z)
            berkas = setelan.simpan(baru, self.berkas_setelan)
        except (ValueError, RuntimeError, OSError) as e:
            raise AplikasiError(f"setelan tidak tersimpan: {e}") from None
        self.setelan = baru
        setelan.terapkan(baru)
        if kunci == "parkir":
            self.jog.parkir_titik = (*baru["parkir"], 0.0)
        self._validasi()
        pesan = f"{_NAMA_UKUR[kunci]} disimpan ke {os.path.basename(berkas)}"
        if self.masalah:
            pesan += f" -- tapi ada {len(self.masalah)} masalah konfigurasi"
        return pesan

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

    # -- bantu -----------------------------------------------------------------
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
}

_NAMA_UKUR = {
    "z_belt": "Z belt", "z_bak": "Z bak", "bak_koin": "bak koin",
    "bak_tutup_botol": "bak tutup botol", "parkir": "titik parkir",
}


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
    a = ap.parse_args(argv)

    p = Pengendali()
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
