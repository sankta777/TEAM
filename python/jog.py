"""
Jog manual robot RNV3 — A/D memutar badan, W/S maju-mundur, IJKL untuk
Z + rel. Tombol panah tetap jalan sebagai alias.

Bedanya dengan GUI resmi: setiap gerakan DIVALIDASI SEBELUM dikirim, pakai
check_reach() + check_path() dari rnv3.py yang mereplika isAllowedPosition()
milik firmware. Jadi mustahil menabrak batas jangkauan, dan titik yang ditolak
tidak pernah sampai ke robot. GUI resmi mengirim dulu, baru ditolak firmware —
dan kegagalannya diam-diam.

Berguna juga untuk MENGUKUR angka yang dibutuhkan pick_place.py: jog ke posisi
yang pas, tekan `m` untuk menandai, `o` untuk menyimpan ke berkas dalam bentuk
siap-tempel. Tekan `?` kapan saja untuk daftar tombol lengkap.

Nol dependensi tambahan: msvcrt (Windows) dan termios (Linux) ada di stdlib,
pyserial sudah terpasang.

CARA PAKAI:
    python.exe jog.py --port COM4        # robot asli (Windows)
    python3  jog.py --sim                # simulator, tanpa robot

HOMING TIDAK BISA DILEWATI. Membuka port serial mereset Arduino, jadi posisi
firmware selalu hilang di awal sesi; jogging tanpa homing tidak ada artinya.
"""

import argparse
import math
import os
import shutil
import sys
import time

import kontak
import rnv3
import setelan
from rnv3 import RNV3, RNV3Error, check_path, check_reach

STEPS = [0.5, 1.0, 5.0, 10.0, 25.0]      # mm, dipilih tombol 1..5
SPEEDS = [10, 20, 40, 80, 150, 250]      # mm/s, diubah tombol [ dan ]

# ---------------------------------------------------------------------------
# JOG KONTINU
# Tahan tombol panah -> lengan jalan terus dan makin lama makin cepat.
#
# Firmware RNV3 TIDAK punya perintah stop/abort: sekali G0 dikirim, gerakan itu
# pasti selesai. Jadi "berhenti saat tombol dilepas" hanya bisa didekati dengan
# mengirim potongan kecil.
#
# PENTING -- kenapa kecepatan di sini dihitung, bukan ditentukan asal:
# robot ini memakai SPEED_PROFILE 2 (kosinus) di config.h, artinya SETIAP G0
# melakukan ramp penuh dari diam -> cepat -> diam lagi. Dari
# progress = -cos(t*tmul*PI)/2 + 0.5 di interpolation.cpp, puncak percepatan
# satu gerakan adalah:
#
#       a_puncak = PI^2 * v^2 / (2 * d)
#
# Percepatan naik dengan KUADRAT kecepatan dan berbanding TERBALIK dengan
# panjang potongan. Jadi kecepatan tinggi pada potongan pendek = hentakan
# brutal, dan karena jog mengirim ~30 potongan per detik, hentakan itu
# berulang 30x/detik -> robot bergetar keras sampai bergeser sendiri.
#
# Acuan yang TERBUKTI mulus di robot ini: F20 pada potongan 5 mm = 395 mm/s^2.
# Maka kecepatan diturunkan dari panjang potongan supaya percepatan tetap di
# sekitar angka itu, berapa pun potongannya.
# Pilihan kehalusan: puncak percepatan yang diizinkan (mm/s^2), diubah [ ]
A_PILIHAN = [100.0, 150.0, 200.0, 300.0, 400.0, 600.0, 800.0]
A_DEFAULT = 2                   # indeks -> 200 mm/s^2

# Panjang sapuan maksimum saat tombol ditahan (mm), diubah - =
SAPUAN_PILIHAN = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0]
SAPUAN_DEFAULT = 2              # indeks -> 15 mm

V_MIN = 5.0             # firmware menaikkan apa pun di bawah 5 mm/s jadi 5
V_DATAR_MAKS = 25.0     # batas kecepatan saat profil DATAR (laju pull-in motor)

PROFIL_KOSINUS = 2      # bawaan config.h: ramp halus diam -> cepat -> diam
PROFIL_ARCTAN = 1       # ramp lonceng ringan
PROFIL_DATAR = 0        # kecepatan konstan; mulai dengan LONJAKAN mendadak
URUT_PROFIL = [PROFIL_KOSINUS, PROFIL_ARCTAN, PROFIL_DATAR]
NAMA_PROFIL = {PROFIL_KOSINUS: "kosinus", PROFIL_ARCTAN: "arctan",
               PROFIL_DATAR: "datar"}

# Titik parkir sebelum robot dimatikan, dari setelan-robot.json (satu sumber
# dengan jalur.py dan pick_place.py). Bawaannya SENGAJA konservatif -- ruang
# di bawah lengan belum diukur. Ukur di aplikasi (tab Ukur, "Parkir"). Shift+X
# di sini hanya mengganti titik parkir untuk sesi jog yang sedang berjalan.
PARKIR = tuple(setelan.muat()["parkir"])        # X, Y, Z, E

# True = setelah parkir, matikan motor (M18): stepper berhenti menarik arus
# tahan dan kipas ikut mati (setStepperEnable() di firmware mematikan keduanya
# sekaligus). Lengan jadi loyo.
#
# Sengaja False: PARKIR di atas belum pernah diukur, jadi "turun cuma beberapa
# milimeter" itu tidak terjamin -- di lapangan lengan jatuh dan terbentur
# setiap kali ESC. Dengan False lengan tetap terkunci di titik parkir; motor
# baru lepas saat saklar 12V dimatikan atau saat port dibuka lagi (Arduino
# reset -> setup() memanggil setStepperEnable(false)), dan saat itu lengan
# sudah rendah.
#
# CATATAN: ini TIDAK memutus listrik 220V/12V. Dari software hanya motor dan
# kipas yang bisa dimatikan; saklar fisiknya tetap perlu ditekan sendiri.
MATIKAN_SAAT_KELUAR = False


def speed_halus(dist_mm, a_target):
    """Kecepatan tertinggi yang masih menjaga percepatan <= a_target.

    Hanya berlaku untuk PROFIL_KOSINUS / PROFIL_ARCTAN yang punya ramp.
    """
    return max(V_MIN, math.sqrt(a_target * dist_mm / (math.pi ** 2 / 2)))


def accel_puncak(v, dist_mm):
    """Puncak percepatan satu gerakan G0 pada profil ber-ramp (mm/s^2)."""
    return math.pi ** 2 * v * v / (2 * dist_mm)


def batas_speed(dist_mm, profil, a_target):
    """Batas kecepatan aman untuk satu potongan, sesuai profil yang aktif.

    PROFIL_DATAR tidak menurunkan kecepatan dari panjang potongan: selama
    ditahan semua potongan harus berkecepatan SAMA, kalau tidak muncul lonjakan
    di sambungan. Profil ber-ramp sebaliknya -- kecepatan boleh naik mengikuti
    panjang potongan karena rampnya ikut melandai.
    """
    if profil == PROFIL_DATAR:
        return V_DATAR_MAKS
    return speed_halus(dist_mm, a_target)


def tangga_step(sapuan_maks):
    """Tangga potongan saat ditahan, diturunkan dari panjang sapuan maksimum.

    (lama_ditahan_detik, panjang_potongan_mm)
    """
    return [(0.30, round(sapuan_maks * 0.35, 1)),
            (1.00, round(sapuan_maks * 0.70, 1)),
            (float("inf"), sapuan_maks)]

# Jeda yang masih dianggap "tombol ditahan", diukur dari SELESAINYA gerakan
# sebelumnya -- bukan dari penekanan sebelumnya.
#
# Ini penting: saat tombol ditahan, penekanan menumpuk di buffer konsol SELAMA
# robot bergerak, lalu terbaca persis setelah gerakan selesai. Kalau jedanya
# diukur antar penekanan, lamanya gerakan ikut terhitung (potongan 15 mm bisa
# 0.6 detik) sehingga tahan-tombol selalu dikira lepas, dan tangga akselerasi
# tidak pernah naik. Diukur dari selesainya gerakan, jedanya mendekati nol.
JEDA_TAHAN = 0.25

# token tombol -> (sumbu, arah)
ARAH = {
    # tangan kiri: A/D PUTAR badan (radius & Z tetap), W/S maju-mundur Y
    "a": ("rot", -1), "d": ("rot", +1),
    "w": ("y", +1), "s": ("y", -1),
    # tangan kanan: IJKL -> Z dan rel
    "i": ("z", +1), "k": ("z", -1),
    # J = kiri, L = kanan DILIHAT DARI OPERATOR. Di robot nyata E+ bergerak
    # ke kiri operator (laporan operator, 23 Okt 2026), jadi J = E+.
    "j": ("e", +1), "l": ("e", -1),
    # alias lama supaya tidak ada yang hilang
    "KIRI": ("rot", -1), "KANAN": ("rot", +1),
    "ATAS": ("y", +1), "BAWAH": ("y", -1),
    "PGUP": ("z", +1), "PGDN": ("z", -1),
}

BANTUAN = """
  GERAK   tekan sekali = presisi, tahan = sapuan mulus
    W / S   Y maju / mundur          A / D   putar badan kiri / kanan
    I / K   Z naik / turun           J / L   rel kiri / kanan
    (panah dan PgUp/PgDn tetap jalan)

  HALUSKAN   ubah sambil jalan, langsung terasa
    [  ]    kehalusan  - / +         -  =    panjang sapuan - / +
    0       ganti profil kecepatan   1-5     step tekan-sekali

  ALAT
    G / B   gripper genggam / buka   V       vakum on-off

  UKUR
    P       cetak posisi + baris config      M   tandai titik
    N       daftar titik tertandai           O   simpan titik ke berkas

  POSISI & MOTOR
    H       pulang ke home           C       kalibrasi ulang (G28)
    X       parkir sekarang          Shift+X jadikan posisi ini titik parkir
    ;       rekam batas fisik arah terakhir (lengan hampir beradu)
    R       rilis motor (M18)        T       tahan motor (M17)

  LAIN
    ?       bantuan ini              Q / ESC keluar (parkir dulu)
"""


# Ambang peringatan mendekati batas jangkauan (mm).
DEKAT_BATAS = 25.0      # mulai memperingatkan
MENTOK = 0.3            # dianggap sudah benar-benar mentok

NAMA_ARAH = {("rot", 1): "putar kanan", ("rot", -1): "putar kiri",
             ("x", 1): "X+ kanan", ("x", -1): "X- kiri",
             ("y", 1): "Y+ maju", ("y", -1): "Y- mundur",
             ("z", 1): "Z+ naik", ("z", -1): "Z- turun",
             # Kiri/kanan dilihat dari posisi operator: E- (menuju E=0)
             # bergerak ke KANAN -- laporan operator di robot nyata, 23 Okt 2026.
             ("e", 1): "E+ rel kiri", ("e", -1): "E- rel kanan"}


# A/D dulu menggeser X kartesian dengan Y tetap. Akibatnya radius ujung tool
# ikut berubah (X 0 -> 100 di Y 217: radius 217 -> 239 mm, bahu 0 -> 9 deg),
# jadi lengan atas & bawah ikut menekuk/menjulur padahal yang dimaui cuma
# memutar badan. Sumbu "rot" memutar titik mengelilingi pusat rotasi dengan
# radius dan Z tetap -- yang bergerak cuma stepper putar base.
#
# Firmware menginterpolasi GARIS LURUS, jadi satu G0 memotong tali busur, dan
# di tengahnya radius sedikit lebih pendek: lendut = r * (1 - cos(dphi/2)).
# Potongan putar dibatasi supaya lendut itu tidak lebih dari angka ini.
LENDUT_MAKS = 0.3       # mm


def geser(x, y, z, e, sumbu, arah, t):
    """Titik (x, y, z, e) setelah bergerak t mm ke arah sumbu/arah.

    Untuk "rot", t adalah panjang busur di ujung tool; positif = putar ke
    kanan (X+), sama seperti arah X lama.
    """
    if sumbu == "rot":
        r = math.hypot(x, y)
        if r == 0:
            return x, y, z, e
        phi = math.atan2(x, y) + arah * t / r
        return r * math.sin(phi), r * math.cos(phi), z, e
    titik = {"x": x, "y": y, "z": z, "e": e}
    titik[sumbu] += arah * t
    return titik["x"], titik["y"], titik["z"], titik["e"]


def busur_maks(x, y, lendut=LENDUT_MAKS):
    """Busur terpanjang (mm) satu G0 putar supaya tali busurnya melendut
    paling banyak `lendut` mm dari lingkaran."""
    r = math.hypot(x, y)
    if r <= lendut:
        return 0.0
    return r * 2 * math.acos(1 - lendut / r)


def jarak_tersisa(x, y, z, e, sumbu, arah, maks=400.0):
    """Berapa mm lagi lengan bisa bergerak ke arah ini sebelum keluar batas.

    Ruang kerja robot berbentuk cangkang bola, jadi sisa jarak BERBEDA untuk
    tiap arah dan berubah terus saat lengan bergerak. Angka inilah yang berguna
    saat jogging -- bukan sekadar "radius sekarang", yang tidak memberi tahu
    arah mana yang sebentar lagi mentok.

    Dipindai maju 1 mm lalu dihaluskan dengan bagi-dua. Murni hitungan, tidak
    menyentuh robot.
    """
    def valid(t):
        return rnv3.check_aman(*geser(x, y, z, e, sumbu, arah, t))[0]

    if not valid(0.0):
        return 0.0
    # Pemindaian 0.5 mm, bukan 1 mm: daerah terlarang bisa SANGAT tipis
    # (misalnya garis Y=0 persis), dan langkah 1 mm pernah melompatinya
    # sehingga melaporkan ruang yang sebenarnya tidak ada.
    t = 0.0
    while t < maks and valid(t + 0.5):
        t += 0.5
    if t >= maks:
        return maks
    lo, hi = t, t + 1.0
    while hi - lo > 0.05:
        tengah = (lo + hi) / 2
        if valid(tengah):
            lo = tengah
        else:
            hi = tengah
    return round(lo, 2)


def lebar_terminal():
    """Lebar terminal dikurangi 1 kolom, supaya kursor tidak memicu lipatan.

    Dipisah jadi fungsi supaya cek.py bisa menirukan terminal sempit.
    """
    return max(20, shutil.get_terminal_size((80, 20)).columns - 1)


# ===========================================================================
# PEMBACA TOMBOL  (dipisah supaya logika jog bisa diuji tanpa keyboard)
# ===========================================================================
class Tombol:
    """Baca satu tombol tanpa perlu Enter. Windows pakai msvcrt, Linux termios."""

    def __init__(self):
        self.windows = False
        try:
            import msvcrt
            self._m = msvcrt
            self.windows = True
        except ImportError:
            import termios, tty
            self._termios, self._tty = termios, tty
            self._fd = sys.stdin.fileno()
            self._simpan = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)

    def tutup(self):
        if not self.windows:
            self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN,
                                    self._simpan)

    def ada(self):
        if self.windows:
            return self._m.kbhit()
        import select
        return bool(select.select([sys.stdin], [], [], 0)[0])

    def ambil(self):
        """Return token: 'KIRI'/'KANAN'/'ATAS'/'BAWAH'/'PGUP'/'PGDN'/'ESC'
        atau karakter apa adanya."""
        if self.windows:
            c = self._m.getch()
            if c in (b"\x00", b"\xe0"):            # prefix tombol khusus
                k = self._m.getch()
                return {b"K": "KIRI", b"M": "KANAN", b"H": "ATAS",
                        b"P": "BAWAH", b"I": "PGUP", b"Q": "PGDN"}.get(k, "?")
            if c == b"\x1b":
                return "ESC"
            return c.decode("latin1")
        c = sys.stdin.read(1)
        if c == "\x1b":
            if not self.ada():
                return "ESC"
            sys.stdin.read(1)                      # '['
            k = sys.stdin.read(1)
            if k in "56":
                sys.stdin.read(1)                  # '~'
                return {"5": "PGUP", "6": "PGDN"}[k]
            return {"D": "KIRI", "C": "KANAN", "A": "ATAS",
                    "B": "BAWAH"}.get(k, "?")
        return c


def kumpulkan(tokens):
    """Gabungkan token berurutan yang sama -> [(token, jumlah), ...].

    Kalau tombol panah ditahan, konsol menumpuk keystroke. Tanpa digabung,
    menahan panah akan mengirim puluhan perintah G0 berurutan dan terasa lag.
    """
    hasil = []
    for t in tokens:
        if hasil and hasil[-1][0] == t:
            hasil[-1][1] += 1
        else:
            hasil.append([t, 1])
    return [(t, n) for t, n in hasil]


# ===========================================================================
# LOGIKA JOG
# ===========================================================================
class Jog:
    def __init__(self, bot):
        self.bot = bot
        self.i_step = 0          # -> 0.5 mm (presisi; traversal lewat tahan tombol)
        self.i_speed = 1         # -> 20 mm/s
        self.titik = []
        self.pesan = ""
        self.selesai = False
        self.vakum = False
        self.kontinu = True          # jog kontinu + sapuan saat ditahan
        self.kasar = False           # True = lewati pembatas percepatan
        self.profil = PROFIL_KOSINUS # bawaan config.h; diubah lewat tombol 0
        self.sp_diminta = self.sp_dipakai = 0.0
        self.i_accel = A_DEFAULT     # kehalusan, tombol [ ]
        self.i_sapuan = SAPUAN_DEFAULT  # panjang sapuan, tombol - =
        self.parkir_titik = tuple(PARKIR)
        self.sisa_arah = None      # (nama_arah, mm) arah terakhir digerakkan
        self.motor_terkunci = True   # setelah homing stepper tetap berenergi
        self._tahan_token = None
        self._tahan_mulai = 0.0
        self._selesai_terakhir = 0.0   # kapan gerakan terakhir selesai

    @property
    def step(self):
        return STEPS[self.i_step]

    @property
    def speed(self):
        return SPEEDS[self.i_speed]

    @property
    def a_target(self):
        return A_PILIHAN[self.i_accel]

    @property
    def sapuan(self):
        return SAPUAN_PILIHAN[self.i_sapuan]

    @property
    def tangga(self):
        return tangga_step(self.sapuan)

    # -- tampilan ----------------------------------------------------------
    def cetak_status(self, di_tempat=False):
        """Cetak baris status. di_tempat=True menimpa baris yang sama, jadi
        selama jogging layar cuma berisi SATU kalimat yang angkanya berganti.

        Baris dipotong ke lebar terminal: baris yang terlipat membuat '\\r'
        hanya kembali ke awal potongan terakhir, dan setiap gerakan jadi
        tampak sebagai baris baru. Sengaja tanpa kode ANSI supaya aman di
        python.exe dari terminal WSL maupun di PowerShell.
        """
        baris = self.status()
        if not di_tempat:
            print(baris + (f"   <- {self.pesan}" if self.pesan else ""))
            return
        lebar = lebar_terminal()
        # Pesan cuma ditempel kalau muat utuh, atau dipotong rapi dengan '..'
        # kalau ruangnya masih layak -- bukan terpenggal jadi "<- Y".
        ruang = lebar - len(baris) - len(" <- ")
        if self.pesan and ruang >= len(self.pesan):
            baris += f" <- {self.pesan}"
        elif self.pesan and ruang >= 12:
            baris += f" <- {self.pesan[:ruang - 2]}.."
        print("\r" + baris[:lebar].ljust(lebar), end="", flush=True)

    def status(self):
        """Satu kalimat: koordinat di depan, lalu penanda yang sedang berlaku.

        Setelan (step, sapuan, kehalusan, profil) tidak ikut di sini karena
        jarang berubah -- lihat setelan(), dicetak tombol '?'.
        """
        b = self.bot
        s = (f"Posisi lengan  X {b.x:7.2f}  Y {b.y:7.2f}  "
             f"Z {b.z:7.2f}  E {b.e:6.2f}")
        if self.sisa_arah:
            nama, d = self.sisa_arah
            if d < MENTOK:
                s += f" | !!! MENTOK {nama}"
            elif d < DEKAT_BATAS:
                s += f" | >>> {nama} sisa {d:.1f}"
            else:
                s += f" | {nama} sisa {d:.0f}"
        if not self.motor_terkunci:
            s += " | MOTOR RILIS"
        if self.vakum:
            s += " | VAKUM"
        return s

    def setelan(self):
        """Setelan jog yang sedang aktif, untuk dicetak sesekali."""
        return (f"setelan: step {self.step:g} mm | sapuan {self.sapuan:g} mm | "
                f"kehalusan a={self.a_target:g} mm/s2 | "
                f"profil {NAMA_PROFIL[self.profil]}")

    # -- profil kecepatan --------------------------------------------------
    def pakai_profil(self, profil):
        """Ganti profil kecepatan robot lewat M205 (tanpa upload firmware).

        PROFIL_KOSINUS (bawaan) membuat setiap G0 ramp penuh diam -> cepat ->
        diam. Saat jog mengirim potongan beruntun, itu jadi osilasi kecepatan
        belasan kali per detik -> robot bergetar. PROFIL_DATAR menjaga kecepatan
        konstan, jadi potongan beruntun menyambung mulus.

        Return True kalau berhasil. Kalau firmware menolak, jog tetap jalan
        dengan profil lama -- bukan kegagalan fatal.
        """
        try:
            self.bot.send(f"M205 S{profil}")
        except RNV3Error as err:
            self.pesan = f"profil kecepatan tidak bisa diganti: {err}"
            return False
        self.profil = profil
        return True

    # -- akselerasi --------------------------------------------------------
    def step_efektif(self, token, ulang=1):
        """Tentukan step & speed untuk satu penekanan arah.

        Return (step_mm, speed, sedang_ditahan). Kalau penekanan datang
        beruntun (auto-repeat keyboard), dianggap ditahan -> naik tangga
        akselerasi. Penekanan terpisah kembali ke step manual pilihan user.
        """
        if not self.kontinu:
            return self.step, self.speed, False

        now = time.monotonic()
        # Dua sinyal "masih ditahan", mana saja cukup:
        #  1. ulang > 1 -> beberapa penekanan menumpuk di buffer = pasti ditahan
        #  2. penekanan datang segera SETELAH gerakan sebelumnya selesai
        beruntun = token == self._tahan_token and (
            ulang > 1 or (now - self._selesai_terakhir) < JEDA_TAHAN)
        if not beruntun:
            self._tahan_token = token
            self._tahan_mulai = now

        if not beruntun:
            return self.step, self.speed, False

        lama = now - self._tahan_mulai
        for batas, st in self.tangga:
            if lama < batas:
                return st, batas_speed(st, self.profil, self.a_target), True
        st = self.tangga[-1][1]
        return st, batas_speed(st, self.profil, self.a_target), True

    def lepas_tahan(self):
        """Reset akselerasi -- dipanggil kalau gerakan ditolak atau ganti aksi."""
        self._tahan_token = None

    # -- aksi --------------------------------------------------------------
    def gerak(self, sumbu, arah, ulang=1, step=None, speed=None):
        """Jog satu sumbu. Return True kalau benar-benar bergerak."""
        st = self.step if step is None else step
        sp = self.speed if speed is None else speed

        # Berapa jauh lagi bisa bergerak KE ARAH INI sebelum keluar batas.
        # Potongan dijepit ke angka ini supaya lengan melandai berhenti tepat
        # di tepi jangkauan, bukan ditolak mendadak saat masih ada ruang.
        nama = NAMA_ARAH.get((sumbu, arah), f"{sumbu}{arah:+d}")
        sisa = jarak_tersisa(self.bot.x, self.bot.y, self.bot.z, self.bot.e,
                             sumbu, arah)
        self.sisa_arah = (nama, sisa)
        if sisa < MENTOK:
            self.pesan = f"!!! MENTOK -- tidak bisa lebih jauh ke {nama}"
            self.lepas_tahan()
            return False

        if sumbu == "rot":
            # Satu G0 putar = tali busur. Pendekkan supaya lengan tidak ikut
            # menekuk di tengah potongan (lihat LENDUT_MAKS).
            bm = busur_maks(self.bot.x, self.bot.y)
            if st * ulang > bm:
                st, ulang = bm, 1

        minta = st * ulang
        dijepit = minta > sisa - 0.05
        if dijepit:
            st = max(0.05, sisa - 0.05)
            ulang = 1
        delta = arah * st * ulang

        # PEMBATAS PERCEPATAN.
        # Pada SPEED_PROFILE 2, a_puncak = PI^2*v^2/(2*d) -- naik dengan kuadrat
        # kecepatan dan berbanding terbalik dengan panjang gerakan. Tanpa
        # pembatas ini, memilih F250 (tombol ]) lalu menekan panah dengan step
        # 1 mm menghasilkan >300.000 mm/s^2 dan robot akan terguncang hebat.
        batas = batas_speed(abs(delta), self.profil, self.a_target)
        self.sp_diminta, self.sp_dipakai = sp, sp
        if not self.kasar and sp > batas:
            sp = batas
            self.sp_dipakai = sp
        tx, ty, tz, te = geser(self.bot.x, self.bot.y, self.bot.z,
                               self.bot.e, sumbu, arah, abs(delta))
        target = {"x": tx, "y": ty, "z": tz, "e": te}
        sudut_awal = math.degrees(math.atan2(self.bot.x, self.bot.y))

        ok, why = check_reach(target["x"], target["y"], target["z"], target["e"])
        if not ok:
            self.pesan = f"mentok: {why}"
            self.lepas_tahan()
            return False
        ok, why, _ = check_path((self.bot.x, self.bot.y, self.bot.z),
                                (target["x"], target["y"], target["z"]))
        if not ok:
            self.pesan = f"jalur ditolak: {why.split(':')[-1].strip()}"
            self.lepas_tahan()
            return False

        try:
            if sumbu == "rot":
                self.bot.move(x=target["x"], y=target["y"], f=sp)
            else:
                self.bot.move(**{sumbu: target[sumbu]}, f=sp)
        except RNV3Error as err:
            self.pesan = f"ditolak: {err}"
            self.lepas_tahan()
            return False
        self._selesai_terakhir = time.monotonic()
        sisa_baru = jarak_tersisa(self.bot.x, self.bot.y, self.bot.z,
                                  self.bot.e, sumbu, arah)
        self.sisa_arah = (nama, sisa_baru)
        if sumbu == "rot":
            putar = math.degrees(math.atan2(self.bot.x, self.bot.y)) - sudut_awal
            self.pesan = f"putar {putar:+.2f} deg"
        else:
            self.pesan = f"{sumbu.upper()} {delta:+.3g}"
        if dijepit:
            self.pesan += f"  (dipendekkan, mepet batas {nama})"
        if sisa_baru < MENTOK:
            self.pesan += f"  !!! MENTOK ke {nama}"
        elif sisa_baru < DEKAT_BATAS:
            self.pesan += f"  >>> sisa {sisa_baru:.1f} mm ke batas {nama}"
        return True

    def pulang(self):
        """Kembali ke pose home dengan jalur yang divalidasi."""
        H = (rnv3.HOME_X, rnv3.HOME_Y, rnv3.HOME_Z, rnv3.HOME_E)
        # 1. coba langsung -- check_path akan menolak kalau jalurnya tak aman
        try:
            self.bot.move(x=H[0], y=H[1], z=H[2], e=H[3], f=self.speed)
            self.pesan = "pulang (langsung)"
            return True
        except RNV3Error:
            pass
        # 2. XY dulu di Z sekarang, baru Z, baru rel
        try:
            self.bot.move(x=H[0], y=H[1], f=self.speed)
            self.bot.move(z=H[2], f=self.speed)
            self.bot.move(e=H[3], f=self.speed)
            self.pesan = "pulang (bertahap)"
            return True
        except RNV3Error as err:
            self.pesan = f"gagal pulang: {err} -- jog manual saja"
            return False

    def parkir(self):
        """Turunkan lengan ke titik parkir supaya aman saat power dimatikan.

        Stepper memang menahan posisi selama robot menyala, tapi begitu power
        dicabut lengan jadi loyo dan jatuh. Parkir rendah membuat jatuhnya cuma
        beberapa milimeter.

        Urutan sengaja X/Y dulu BARU Z: menurunkan Z di tempat berisiko
        menyeruduk benda yang kebetulan ada di bawah lengan.
        """
        px, py, pz, pe = self.parkir_titik
        try:
            self.bot.move(x=px, y=py, f=self.speed)
            self.bot.move(e=pe, f=self.speed)
            self.bot.move(z=pz, f=self.speed)
        except RNV3Error as err:
            self.pesan = f"gagal parkir: {err}"
            return False
        self.pesan = f"parkir di X{px:g} Y{py:g} Z{pz:g}"
        return True

    def set_parkir(self):
        """Jadikan posisi lengan sekarang sebagai titik parkir."""
        b = self.bot
        self.parkir_titik = (b.x, b.y, b.z, b.e)
        print(f"\n  titik parkir -> X{b.x:.1f} Y{b.y:.1f} Z{b.z:.1f} E{b.e:.1f}")
        print("  berlaku untuk sesi ini saja. Supaya permanen (dan dipakai")
        print("  jalur.py + pick_place.py juga), simpan lewat aplikasi:")
        print("  tab Ukur -> Parkir -> 'Simpan posisi ini'.\n")

    def rekam_batas(self):
        """Rekam posisi sekarang sebagai batas fisik untuk arah terakhir dijog.

        Dipakai saat lengan hampir beradu dengan badannya sendiri atau mentok
        mekanis -- hal yang TIDAK diketahui firmware, karena firmware hanya
        memeriksa radius dan Z.
        """
        if not self.sisa_arah:
            self.pesan = "gerakkan dulu ke arah yang mau dibatasi, baru tekan ;"
            return False
        nama = self.sisa_arah[0]
        sumbu, arah = next(k for k, v in NAMA_ARAH.items() if v == nama)
        if sumbu == "rot":
            # BATAS_FISIK hanya punya satu batas putar, simetris kiri-kanan.
            nilai = abs(math.degrees(math.atan2(self.bot.x, self.bot.y)))
            kunci = "rot_maks"
        else:
            nilai = {"x": self.bot.x, "y": self.bot.y,
                     "z": self.bot.z, "e": self.bot.e}[sumbu]
            kunci = f"{sumbu}_{'maks' if arah > 0 else 'min'}"
        rnv3.BATAS_FISIK[kunci] = round(nilai, 1)
        berkas = rnv3.simpan_batas()
        print(f"\n  batas direkam: {kunci} = {nilai:.1f}")
        print(f"  disimpan ke {os.path.basename(berkas)} -- berlaku juga untuk")
        print("  pick_place.py, dan bertahan antar sesi.")
        print(f"  arah {nama} sekarang berhenti di situ.\n")
        self.pesan = f"batas {kunci} = {nilai:.1f} direkam"
        return True

    def rilis_motor(self):
        """M18 -- lepas stepper supaya lengan bisa digeser dengan tangan."""
        self.bot.steppers(False)
        self.motor_terkunci = False
        # Lengan digeser tangan -> posisi yang dilacak tidak lagi benar.
        # Pengaman di rnv3.move() akan menolak gerak sampai home()/position().
        self.bot.position_known = False
        self.pesan = "motor DIRILIS -- geser pakai tangan, lalu C untuk kalibrasi"

    def tahan_motor(self):
        """M17 -- kunci stepper lagi."""
        self.bot.steppers(True)
        self.motor_terkunci = True
        self.pesan = "motor dikunci (posisi belum dipercaya, tekan C)"

    def tandai(self):
        b = self.bot
        self.titik.append({"x": b.x, "y": b.y, "z": b.z, "e": b.e})
        self.pesan = f"titik #{len(self.titik)} ditandai"

    def simpan(self, berkas="titik-terukur.txt"):
        if not self.titik:
            self.pesan = "belum ada titik ditandai (tekan m dulu)"
            return False
        with open(berkas, "a", encoding="utf-8") as f:
            f.write(f"\n# ditandai {time.strftime('%Y-%m-%d %H:%M')}\n")
            for i, t in enumerate(self.titik, 1):
                f.write(f"#  titik {i}: X={t['x']:.2f} Y={t['y']:.2f} "
                        f"Z={t['z']:.2f} E={t['e']:.2f}\n")
            t = self.titik[-1]
            f.write("# baris siap-tempel ke pick_place.py:\n")
            f.write(f"Y_ROW  = {t['y']:.1f}\n")
            f.write(f"Z_PICK = {t['z']:.1f}\n")
        self.pesan = f"{len(self.titik)} titik disimpan ke {berkas}"
        return True

    def baris_config(self):
        b = self.bot
        return [f"Y_ROW  = {b.y:.1f}     # jarak grid dari badan robot",
                f"Z_PICK = {b.z:.1f}     # ketinggian saat menyentuh benda",
                f"X_CENTER = {b.x:.1f}   # titik tengah grid di sumbu X"]

    # -- dispatcher --------------------------------------------------------
    def tangani(self, token, ulang=1):
        """Jalankan satu token tombol. Return True kalau status perlu dicetak."""
        self.pesan = ""
        if token not in ARAH:
            self.lepas_tahan()

        if token in ARAH:
            sumbu, arah = ARAH[token]
            st, sp, ditahan = self.step_efektif(token, ulang)
            # Saat ditahan, penekanan yang menumpuk SENGAJA tidak dikalikan:
            # satu potongan per putaran, sisanya dibuang. Kalau dikalikan,
            # lengan akan terus jalan lama setelah tombol dilepas.
            self.gerak(sumbu, arah, 1 if ditahan else ulang, step=st, speed=sp)
            return True

        if token in "12345":
            self.i_step = int(token) - 1
            self.pesan = f"step -> {self.step} mm"
            return True

        if token in "[]":
            arah = -1 if token == "[" else +1
            self.i_accel = max(0, min(len(A_PILIHAN) - 1, self.i_accel + arah))
            self.pesan = (f"kehalusan -> a={self.a_target:g} mm/s2 "
                          f"({'lebih halus' if arah < 0 else 'lebih bertenaga'})")
            return True

        if token in "-=":
            arah = -1 if token == "-" else +1
            self.i_sapuan = max(0, min(len(SAPUAN_PILIHAN) - 1,
                                       self.i_sapuan + arah))
            t = [st for _, st in self.tangga]
            self.pesan = (f"sapuan -> maks {self.sapuan:g} mm "
                          f"(tangga {t[0]:g}/{t[1]:g}/{t[2]:g})")
            return True

        if token == "0":
            i = (URUT_PROFIL.index(self.profil) + 1) % len(URUT_PROFIL)
            baru = URUT_PROFIL[i]
            if self.pakai_profil(baru):
                self.pesan = f"profil -> {NAMA_PROFIL[baru]}"
            return True

        if token == "x":
            self.parkir()
            return True
        if token == "X":
            self.set_parkir()
            return True

        if token == ";":
            self.rekam_batas()
            return True

        if token == "r":
            self.rilis_motor()
            return True
        if token == "t":
            self.tahan_motor()
            return True

        if token == "g":
            self.bot.gripper(rnv3.SERVO_CLOSED)
            self.pesan = f"gripper genggam ({rnv3.SERVO_CLOSED})"
            return True
        if token == "b":
            self.bot.gripper(rnv3.SERVO_OPEN)
            self.pesan = f"gripper buka ({rnv3.SERVO_OPEN})"
            return True

        if token == "v":
            self.vakum = not self.vakum
            self.bot.vacuum(self.vakum)
            self.pesan = f"vakum {'ON' if self.vakum else 'OFF'}"
            return True

        if token == "p":
            p = self.bot.position()
            print(f"\n  robot melaporkan: X={p['x']:.2f} Y={p['y']:.2f} "
                  f"Z={p['z']:.2f} E={p['e']:.2f}")
            print("  baris siap-tempel ke pick_place.py:")
            for ln in self.baris_config():
                print(f"    {ln}")
            print()
            return True

        if token == "m":
            self.tandai()
            return True
        if token == "n":
            if not self.titik:
                self.pesan = "belum ada titik ditandai"
            else:
                print()
                for i, t in enumerate(self.titik, 1):
                    print(f"  titik {i}: X={t['x']:7.2f} Y={t['y']:7.2f} "
                          f"Z={t['z']:7.2f} E={t['e']:6.2f}")
                print()
            return True
        if token == "o":
            self.simpan()
            return True

        if token == "h":
            self.pulang()
            return True

        if token == "c":
            print("\n  kalibrasi ulang (G28) ...")
            self.bot.home()
            # G28 menyalakan semua stepper (Endstop::home), jadi status
            # "MOTOR RILIS" dari R sebelumnya tidak berlaku lagi.
            self.motor_terkunci = True
            self.pesan = "kalibrasi selesai"
            return True

        if token == "?":
            print(BANTUAN)
            print(f"  {self.setelan()}\n")
            return True

        if token in ("q", "ESC", "\x03"):
            self.selesai = True
            return False

        return False

    def bersih_bersih(self):
        """Selalu dijalankan saat keluar: vakum mati, lengan ke pose aman."""
        print("\nmembersihkan ...")
        try:
            if self.vakum:
                self.bot.vacuum(False)
            if self.profil != PROFIL_KOSINUS:
                self.pakai_profil(PROFIL_KOSINUS)   # kembalikan ke bawaan

            if not self.bot.position_known:
                # Motor pernah dirilis (R) -> lengan mungkin digeser tangan,
                # jadi posisi di firmware maupun di sini sama-sama tidak bisa
                # dipercaya. Satu-satunya pemulihan yang benar adalah G28, dan
                # itu gerakan besar yang tidak pantas dijalankan diam-diam saat
                # keluar. Jadi: jangan parkir, beri tahu apa adanya.
                print("  TIDAK parkir: motor pernah dirilis, posisi lengan")
                print("  tidak diketahui. Pilihan:")
                print("    - turunkan lengan dengan TANGAN sebelum power dimatikan")
                print("    - atau jalankan jog.py lagi, tekan C untuk kalibrasi,")
                print("      lalu X untuk parkir")
                # Motor TIDAK dimatikan di sini: lengan bisa saja sedang tinggi,
                # dan mematikannya akan menjatuhkannya dari ketinggian itu.
                print("  motor dibiarkan terkunci -- lengan menahan posisi")
                print("  (sengaja TIDAK dimatikan: posisi tidak diketahui,")
                print("   bisa saja lengan sedang tinggi)")
            else:
                self.parkir()
                print(f"  {self.pesan}")
                if MATIKAN_SAAT_KELUAR:
                    self.bot.steppers(False)      # M18: motor + kipas mati
                    self.motor_terkunci = False
                    print("  motor & kipas DIMATIKAN (M18) -- robot diam total")
                    print("  lengan sekarang loyo, tapi sudah parkir rendah")
                    print("  jadi turunnya cuma beberapa milimeter.")
                    print("  Saklar fisik aman ditekan sekarang.")
                else:
                    print("  motor dibiarkan terkunci -- lengan menahan posisi")
        except RNV3Error as err:
            print(f"  tidak bisa parkir otomatis: {err}")


# ===========================================================================
# PRA-TERBANG
# ===========================================================================
def praterbang(bot):
    """Pastikan config kode cocok dengan robot SEBELUM menggerakkan apa pun."""
    hasil = kontak.baca_robot(bot)
    baris = kontak.verdict(hasil)
    beda = [(n, s) for n, _, _, c, s in baris if c == "beda"]
    perhatian = [(n, s) for n, _, _, c, s in baris if c == "perhatian"]

    print("=" * 70)
    print("PRA-TERBANG")
    print("=" * 70)
    for nama, dari_robot, _, status, _ in baris:
        if status != "info":
            print(f"  {nama:<16} {str(dari_robot):<26} {status}")
    for nama, s in perhatian:
        print(f"\n  perhatian [{nama}]: {s}")

    if beda:
        print()
        for nama, s in beda:
            print(f"  TIDAK COCOK [{nama}]: {s}")
        print("\nDibatalkan: jangan menggerakkan robot dengan config yang tidak")
        print("cocok. Betulkan rnv3.py dulu, lalu jalankan `python3 cek.py`.")
        return False
    return True


def konfirmasi_homing():
    print()
    print("=" * 70)
    print("SEBELUM HOMING -- BACA DULU")
    print("=" * 70)
    print("  Keempat sumbu akan bergerak sendiri mencari endstop.")
    print()
    print("  1. Geser lengan maju sedikit PAKAI TANGAN. Kalau mentok di")
    print("     endstop belakang, titik nol hasil homing kurang berulang.")
    print("  2. Pastikan area radius ~34 cm dari badan robot bersih,")
    print("     termasuk jalur rel.")
    print("  3. Tangan dekat tombol power, siap mematikan kalau ada yang")
    print("     menuju benturan.")
    print()
    try:
        jwb = input("  Sudah siap semua? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return jwb == "y"


# ===========================================================================
# MAIN
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Jog manual robot RNV3 pakai tombol panah.")
    ap.add_argument("--port", default=None,
                    help="port serial; kosongkan untuk deteksi otomatis")
    ap.add_argument("--sim", action="store_true", help="simulator, tanpa robot")
    ap.add_argument("--diam", action="store_true",
                    help="sembunyikan lalu-lintas serial sejak awal (juga saat "
                         "menyambung dan homing)")
    ap.add_argument("--ramai", action="store_true",
                    help="tetap tampilkan lalu-lintas serial (>> G0 / << ok) "
                         "selama jogging. Bawaannya disembunyikan supaya layar "
                         "cuma berisi satu baris status.")
    ap.add_argument("--urutan", help="jalankan token tombol ini lalu keluar "
                                     "(untuk uji otomatis, mis. 'KANAN PGDN p q')")
    ap.add_argument("--kasar", action="store_true",
                    help="LEWATI pembatas percepatan. Robot akan terguncang "
                         "keras pada potongan pendek -- hanya untuk uji.")
    ap.add_argument("--diskret", action="store_true",
                    help="matikan jog kontinu: tiap penekanan = tepat satu step, "
                         "tanpa akselerasi")
    ap.add_argument("--tanpa-tanya", action="store_true",
                    help="lewati prompt konfirmasi homing. Hanya pakai ini kalau "
                         "kesiapan sudah dipastikan di luar (operator ada di "
                         "sebelah robot, area bersih, tangan di tombol power). "
                         "Pengingat keselamatan tetap dicetak.")
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
        print("  Membuka port mereset Arduino, jadi GRIPPER akan membuka sendiri.")
        try:
            bot = RNV3(a.port, verbose=not a.diam)
        except Exception as err:
            print(f"\nGagal membuka {a.port}: {err}")
            print("  - pastikan GUI robot / Arduino IDE sudah DIPUTUS")
            print("    (satu port serial hanya boleh dipegang satu program)")
            print("  - di WSL pakai `python.exe`, bukan `python3`")
            return 1

    try:
        if not praterbang(bot):
            return 2

        if a.tanpa_tanya:
            if not a.sim:
                print("\n  --tanpa-tanya: prompt dilewati. Pastikan operator ADA")
                print("  di sebelah robot, area bersih, tangan di tombol power.")
        elif not konfirmasi_homing():
            print("\nDibatalkan, robot tidak digerakkan.")
            return 0

        print("\n>> G28 homing ...")
        bot.home()
        p = bot.position()
        print(f"\nselesai homing. posisi: X={p['x']:.2f} Y={p['y']:.2f} "
              f"Z={p['z']:.2f} E={p['e']:.2f}")
        print("\nPeriksa dengan mata: lengan bawah TEGAK, lengan atas HORIZONTAL.")
        print("Kalau miring, X_HOME_STEPS / Y_HOME_STEPS di config.h perlu disetel.")

        # Mulai dari sini lalu-lintas serial disembunyikan: tiap gerakan
        # mencetak '>> G0', '<< LINEAR MOVE', '<< ok' sebagai baris baru dan
        # memecah baris status yang mestinya diam di tempat.
        if not a.ramai:
            bot.verbose = False

        jog = Jog(bot)
        jog.kontinu = not a.diskret
        if jog.kontinu:
            t = [st for _, st in jog.tangga]
            print("\nJOG: tekan sekali = presisi, tahan = sapuan mulus")
            print(f"  tekan sekali   {jog.step} mm")
            print(f"  tahan          {t[0]:g} -> {t[1]:g} -> {t[2]:g} mm")
            print(f"  profil {NAMA_PROFIL[jog.profil]}, "
                  f"kehalusan a={jog.a_target:g} mm/s2")
            print(f"  lepas tombol -> lengan maju maksimal {jog.sapuan:g} mm")
            print("\n  MASIH TERASA MENGHENTAK? ubah sambil jalan:")
            print("    =   perpanjang sapuan (hentakan makin jarang)")
            print("    [   lebih halus (ramp makin landai)")
            print("    0   bandingkan profil kosinus / arctan / datar")
        print(BANTUAN)
        print(jog.status())

        if a.urutan:
            for tok in a.urutan.split():
                if jog.tangani(tok):
                    jog.cetak_status()
                if jog.selesai:
                    break
        else:
            kb = Tombol()
            try:
                while not jog.selesai:
                    tokens = [kb.ambil()]
                    while kb.ada():                      # kuras yang menumpuk
                        tokens.append(kb.ambil())
                    for tok, ulang in kumpulkan(tokens):
                        # Aksi yang mencetak sendiri (p, l, ?, c) butuh baris
                        # baru; sisanya menimpa baris status yang sama.
                        di_tempat = tok not in "pnc?X;"
                        if not di_tempat:
                            print()
                        if jog.tangani(tok, ulang):
                            jog.cetak_status(di_tempat=di_tempat)
                        if jog.selesai:
                            break
                print()
            finally:
                kb.tutup()

        jog.bersih_bersih()
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
