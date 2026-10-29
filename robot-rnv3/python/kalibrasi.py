"""
Kalibrasi piksel -> milimeter. Jembatan antara kamera dan robot.

KENAPA INI JADI TUGAS KELOMPOK 1, BUKAN KELOMPOK KAMERA

Model deteksi apa pun (YOLO, dsb) keluarannya PIKSEL — posisi di dalam gambar.
Robot cuma mengerti MILIMETER. Mengubah piksel jadi milimeter itu bukan kerjaan
AI, melainkan kalibrasi geometri — dan kalibrasi itu BUTUH ROBOTNYA, karena
titik acuannya didapat dengan menyuruh lengan ke koordinat yang sudah diketahui.
Kelompok kamera tidak memegang robot, jadi mereka memang tidak bisa
mengerjakannya walaupun mau.

CARA KERJA

Permukaan belt itu bidang datar, dan kamera melihatnya dari atas. Hubungan
antara bidang datar dan gambarnya selalu bisa dinyatakan sebagai HOMOGRAFI —
matriks 3x3 yang sekaligus menangani geser, putar, skala, dan kemiringan
perspektif (efek kamera yang tidak benar-benar tegak lurus).

    [x']   [h0 h1 h2]   [u]              x = x'/w
    [y'] = [h3 h4 h5] . [v]              y = y'/w
    [w ]   [h6 h7  1]   [1]

Delapan angka yang belum diketahui, tiap pasang titik memberi dua persamaan,
jadi minimal EMPAT pasang titik. Lebih banyak lebih bagus — sisanya dipakai
meratakan galat.

Tidak butuh numpy maupun OpenCV: penyelesaian kuadrat-terkecilnya ditulis
langsung di sini. Satu dependensi lebih sedikit untuk dipasang di dua Python
berbeda (python3 di WSL, python.exe di Windows).

BATASANNYA — WAJIB DIBACA

Homografi hanya berlaku untuk SATU BIDANG DATAR di SATU KETINGGIAN. Kalibrasi
dilakukan di permukaan belt, jadi:

  - Koin (tinggi ~2 mm) praktis berada di bidang itu  -> akurat
  - Botol berdiri (~150 mm) permukaan atasnya jauh di atas bidang itu, dan
    kamera melihatnya dari sudut -> posisinya TAMPAK bergeser (paralaks)

Makin tegak lurus kamera di atas zona ambil, makin kecil pergeseran itu. Kalau
kamera tidak bisa dibuat tegak, kalibrasi kedua di ketinggian tutup botol bisa
dibuat terpisah lalu dipilih berdasarkan label dari kamera.

CARA PAKAI

    python3 kalibrasi.py --demo        # uji mandiri dengan titik buatan
    python3 kalibrasi.py --sesi        # pemandu kalibrasi, piksel diketik

    # DIANJURKAN -- mode penanda, satu program, piksel diklik di jendela:
    python kalibrasi.py --sesi --kamera 0

    # sesudahnya, robot MENUNJUK benda yang dilihat YOLO (tanpa vakum):
    python server_kamera.py --sumber 0                  # terminal 1
    python kalibrasi.py --uji                           # terminal 2

    import kalibrasi
    k = kalibrasi.Kalibrasi.muat("kalibrasi.json")
    x_mm, y_mm = k.ke_mm(320, 240)
"""

import json
import math
import os
import time

MIN_TITIK = 4
GALAT_WAJAR_MM = 3.0        # di atas ini, kalibrasinya patut dicurigai
MELAYANG_MM = 5.0           # jarak ujung vakum di atas belt/benda: cukup untuk
                            # menyelipkan koin, tanpa menyentuh


class KalibrasiError(RuntimeError):
    """Titik kurang, titik segaris, atau file kalibrasi rusak."""


# ---------------------------------------------------------------------------
# ALJABAR SEADANYA — cukup untuk 3x3 dan satu sistem 8x8
# ---------------------------------------------------------------------------
def _solve(A, b):
    """Selesaikan A x = b (A persegi) lewat eliminasi Gauss-Jordan, pivot parsial."""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for kol in range(n):
        piv = max(range(kol, n), key=lambda r: abs(M[r][kol]))
        if abs(M[piv][kol]) < 1e-12:
            raise KalibrasiError(
                "sistem persamaan tidak bisa diselesaikan — kemungkinan besar "
                "titik kalibrasinya SEGARIS atau terlalu berdempetan. "
                "Sebarkan titik ke seluruh zona ambil, jangan menumpuk.")
        M[kol], M[piv] = M[piv], M[kol]
        pv = M[kol][kol]
        for r in range(n):
            if r == kol:
                continue
            f = M[r][kol] / pv
            if f == 0.0:
                continue
            for c in range(kol, n + 1):
                M[r][c] -= f * M[kol][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def _kuadrat_terkecil(A, b):
    """Selesaikan A x ~= b (A lebih tinggi dari lebar) lewat persamaan normal."""
    n = len(A[0])
    AtA = [[sum(A[k][i] * A[k][j] for k in range(len(A))) for j in range(n)]
           for i in range(n)]
    Atb = [sum(A[k][i] * b[k] for k in range(len(A))) for i in range(n)]
    return _solve(AtA, Atb)


def _kali3(P, Q):
    return [[sum(P[i][k] * Q[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _balik3(M):
    """Invers matriks 3x3 lewat adjugate."""
    a, b, c = M[0]
    d, e, f = M[1]
    g, h, i = M[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise KalibrasiError("matriks tidak punya invers (determinan nol)")
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def _normalisasi(titik):
    """Normalisasi Hartley: centroid ke nol, jarak rata-rata jadi sqrt(2).

    Tanpa ini, angka piksel (ratusan sampai ribuan) dan milimeter (puluhan)
    bercampur dalam satu sistem persamaan dan hasilnya jadi peka sekali
    terhadap pembulatan. Dengan ini, hasilnya stabil.
    """
    n = len(titik)
    cx = sum(p[0] for p in titik) / n
    cy = sum(p[1] for p in titik) / n
    jarak = sum(math.hypot(p[0] - cx, p[1] - cy) for p in titik) / n
    s = math.sqrt(2) / jarak if jarak > 1e-9 else 1.0
    T = [[s, 0.0, -s * cx],
         [0.0, s, -s * cy],
         [0.0, 0.0, 1.0]]
    return [((p[0] - cx) * s, (p[1] - cy) * s) for p in titik], T


# ---------------------------------------------------------------------------
# KALIBRASI
# ---------------------------------------------------------------------------
class Kalibrasi:
    """Homografi piksel -> mm, beserta catatan asal-usulnya."""

    def __init__(self, H, pasangan=None, catatan="", dibuat=None):
        self.H = [list(baris) for baris in H]
        self.pasangan = [tuple(map(tuple, p)) for p in (pasangan or [])]
        self.catatan = catatan
        self.dibuat = dibuat or time.strftime("%Y-%m-%d %H:%M:%S")

    # -- membangun -----------------------------------------------------------
    @classmethod
    def dari_titik(cls, pasangan, catatan=""):
        """Bangun dari daftar pasangan ((u_px, v_px), (x_mm, y_mm)).

        Minimal 4 pasang, dan titiknya harus TERSEBAR — empat titik yang
        hampir segaris menghasilkan kalibrasi yang kelihatan jadi tapi
        melesetnya parah di luar garis itu.
        """
        if len(pasangan) < MIN_TITIK:
            raise KalibrasiError(
                f"butuh minimal {MIN_TITIK} pasang titik, baru ada {len(pasangan)}")

        piksel = [p[0] for p in pasangan]
        mm = [p[1] for p in pasangan]
        piksel_n, Tp = _normalisasi(piksel)
        mm_n, Tm = _normalisasi(mm)

        A, b = [], []
        for (u, v), (x, y) in zip(piksel_n, mm_n):
            A.append([u, v, 1.0, 0.0, 0.0, 0.0, -u * x, -v * x])
            b.append(x)
            A.append([0.0, 0.0, 0.0, u, v, 1.0, -u * y, -v * y])
            b.append(y)
        h = _kuadrat_terkecil(A, b)
        Hn = [[h[0], h[1], h[2]],
              [h[3], h[4], h[5]],
              [h[6], h[7], 1.0]]

        # Kembalikan ke satuan asli: H = inv(Tm) . Hn . Tp
        H = _kali3(_balik3(Tm), _kali3(Hn, Tp))
        skala = H[2][2]
        if abs(skala) < 1e-12:
            raise KalibrasiError("hasil kalibrasi tidak masuk akal (h22 ~ 0)")
        H = [[nilai / skala for nilai in baris] for baris in H]

        return cls(H, pasangan=pasangan, catatan=catatan)

    # -- memakai -------------------------------------------------------------
    def ke_mm(self, u, v):
        """Ubah satu titik piksel jadi (x_mm, y_mm) koordinat robot."""
        H = self.H
        w = H[2][0] * u + H[2][1] * v + H[2][2]
        if abs(w) < 1e-12:
            raise KalibrasiError(
                f"titik piksel ({u}, {v}) jatuh di garis horizon homografi — "
                f"di luar bidang yang dikalibrasi")
        x = (H[0][0] * u + H[0][1] * v + H[0][2]) / w
        y = (H[1][0] * u + H[1][1] * v + H[1][2]) / w
        return x, y

    # -- menilai mutu --------------------------------------------------------
    def galat(self, pasangan=None):
        """Galat tiap titik dalam mm. Return list jarak."""
        pasangan = pasangan if pasangan is not None else self.pasangan
        hasil = []
        for (u, v), (x, y) in pasangan:
            px, py = self.ke_mm(u, v)
            hasil.append(math.hypot(px - x, py - y))
        return hasil

    def galat_rata2(self, pasangan=None):
        g = self.galat(pasangan)
        return sum(g) / len(g) if g else 0.0

    def galat_maks(self, pasangan=None):
        g = self.galat(pasangan)
        return max(g) if g else 0.0

    def laporan(self):
        """Ringkasan mutu kalibrasi, untuk dicetak ke operator."""
        if not self.pasangan:
            return "kalibrasi dimuat tanpa titik asal — mutu tidak bisa dinilai"
        rata, maks = self.galat_rata2(), self.galat_maks()
        baris = [
            f"{len(self.pasangan)} titik, dibuat {self.dibuat}",
            f"galat rata-rata {rata:.2f} mm, terburuk {maks:.2f} mm",
        ]
        if maks > GALAT_WAJAR_MM:
            baris.append(
                f"PERINGATAN: galat > {GALAT_WAJAR_MM:.0f} mm. Kemungkinan "
                f"penyebabnya: kamera bergeser saat kalibrasi, titik terlalu "
                f"berdempetan, atau ada titik yang salah catat. Ulangi.")
        return "\n".join(baris)

    # -- simpan / muat -------------------------------------------------------
    def ke_dict(self):
        return {"H": self.H, "pasangan": [list(map(list, p)) for p in self.pasangan],
                "catatan": self.catatan, "dibuat": self.dibuat}

    def simpan(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.ke_dict(), f, indent=2)
        return path

    @classmethod
    def muat(cls, path):
        if not os.path.exists(path):
            raise KalibrasiError(
                f"file kalibrasi '{path}' tidak ada. Jalankan "
                f"'python3 kalibrasi.py --sesi' dulu bersama robot dan kamera "
                f"yang sudah terpasang kaku.")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        try:
            H = d["H"]
            if len(H) != 3 or any(len(b) != 3 for b in H):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise KalibrasiError(f"isi '{path}' bukan kalibrasi yang sah") from None
        return cls(H, pasangan=d.get("pasangan"), catatan=d.get("catatan", ""),
                   dibuat=d.get("dibuat"))

    def __str__(self):
        return f"Kalibrasi({len(self.pasangan)} titik, {self.dibuat})"


# ---------------------------------------------------------------------------
# SESI KALIBRASI — dipandu, butuh robot DAN kamera terpasang
# ---------------------------------------------------------------------------
def sesi_kalibrasi(bot, titik_mm=None, z=None, baca_piksel=None, cetak=print,
                   minggir=None, tanya=input):
    """Pandu operator mengumpulkan pasangan titik, lalu bangun Kalibrasi.

    bot          : objek RNV3 yang sudah di-home
    titik_mm     : daftar (x, y) tempat robot akan berdiri. Sebarkan!
    z            : tinggi saat menandai; pakai tinggi permukaan belt
    baca_piksel  : fungsi () -> (u, v). Default: tanya lewat input().
                   pembaca_klik_kamera() membaca langsung dari klik di frame.
    minggir      : fungsi () yang membawa lengan keluar dari pandangan kamera.
                   Kalau diisi, dipakai MODE PENANDA (lihat di bawah).

    Alur tiap titik: robot pindah ke (x, y) yang kita TAHU persis -> operator
    melihat di mana titik itu muncul di gambar kamera -> koordinat pikselnya
    dicatat. Ulangi minimal 4 kali.

    MODE PENANDA (minggir diisi): kamera melihat dari ATAS, jadi ujung vakum
    tertutup badan gripper sendiri -- yang terlihat cuma bagian atasnya, yang
    tingginya beda dan kena paralaks. Maka: vakum melayang MELAYANG_MM di atas
    belt, operator menaruh penanda tipis (koin) tepat di bawahnya, lengan
    minggir, baru penandanya yang diklik. Penanda di permukaan belt = tepat di
    bidang tempat homografi berlaku.
    """
    if titik_mm is None:
        # Grid 3x3 di zona ambil. Sengaja tidak segaris; titik tengah tiap sisi
        # ikut meratakan galat ke seluruh area, bukan cuma di pojok.
        titik_mm = [(x, y) for y in (240.0, 265.0, 290.0)
                    for x in (-120.0, 0.0, 120.0)]
    if z is None:
        raise KalibrasiError(
            "z wajib diisi — pakai tinggi PERMUKAAN BELT, karena di bidang "
            "itulah homografi berlaku")
    if baca_piksel is None:
        def baca_piksel():
            teks = input("    koordinat piksel ujung gripper (u v): ").strip()
            u, v = teks.replace(",", " ").split()
            return float(u), float(v)

    cetak("=" * 68)
    cetak("SESI KALIBRASI PIKSEL -> MM")
    cetak("=" * 68)
    cetak("Syarat yang tidak bisa ditawar:")
    cetak("  1. Kamera sudah terpasang KAKU dan tidak akan digeser lagi")
    cetak("  2. END_EFFECTOR_OFFSET sudah dikalibrasi (lihat CATATAN.md)")
    cetak("  3. Pencahayaan sudah seperti saat produksi nanti")
    cetak("Kalau salah satu belum, hentikan sekarang — hasilnya akan sia-sia.")
    cetak("")

    pasangan = []
    z_tanda = z + MELAYANG_MM if minggir is not None else z
    for i, (x, y) in enumerate(titik_mm, 1):
        # Cek di KEDUA tinggi yang benar-benar dilewati, dengan aturan yang
        # sama dengan move() -- titik yang lolos di bawah bisa ditolak di
        # tinggi jelajah, dan move() yang menolak di tengah sesi = crash.
        rnv3 = __import__("rnv3")
        ok, alasan = True, ""
        for zz in (z_tanda, max(z + 60, z)):
            ok, alasan = rnv3.check_aman(x, y, zz, 0.0)
            if not ok:
                break
        if not ok:
            cetak(f"  [{i}] ({x:.0f}, {y:.0f}) DILEWATI — {alasan}")
            continue
        cetak(f"  [{i}/{len(titik_mm)}] robot -> X{x:.0f} Y{y:.0f} Z{z_tanda:.0f}")
        bot.move(z=max(z + 60, z), f=30)
        bot.goto_xy(x, y, f=60)
        if minggir is not None:
            # Grid bawaan bukan hasil ukur belt: kalau titiknya jatuh di atas
            # rel/rangka (lebih tinggi dari belt), turun ke z belt = menabrak.
            # Operator ada di depan robot -- suruh dia lihat dulu.
            try:
                jawab = tanya("      CEK: tepat di bawah vakum itu BELT (bukan rel/rangka)? "
                              "Enter = turun, l+Enter = lewati titik ini ")
            except EOFError:
                jawab = "l"
            if jawab.strip().lower() == "l":
                cetak("      dilewati")
                continue
        bot.move(z=z_tanda, f=20)
        try:
            if minggir is not None:
                tanya("      taruh penanda (koin) TEPAT di bawah ujung vakum, "
                      "lalu Enter -> lengan minggir ")
                minggir()
            u, v = baca_piksel()
        except (ValueError, EOFError):
            cetak("      dilewati")
            continue
        # KeyboardInterrupt sengaja TIDAK ditangkap: Ctrl+C = hentikan sesi,
        # bukan lewati satu titik (dulu harus ditekan 9x untuk keluar).
        pasangan.append(((u, v), (x, y)))
        cetak(f"      tercatat: piksel ({u:.0f}, {v:.0f}) = mm ({x:.0f}, {y:.0f})")

    bot.move(z=z + 60, f=20)

    if len(pasangan) < MIN_TITIK:
        raise KalibrasiError(
            f"cuma {len(pasangan)} titik terkumpul, butuh {MIN_TITIK}")

    k = Kalibrasi.dari_titik(pasangan, catatan=f"sesi kalibrasi, z={z}")
    cetak("")
    cetak(k.laporan())
    return k


def pembaca_klik_kamera(kamera, judul="kalibrasi -- klik tengah penanda"):
    """baca_piksel untuk sesi_kalibrasi(): jendela kamera live, operator klik.

    Klik kiri boleh diulang sampai pas; klik kanan / Enter / Spasi = pakai
    titik itu, Esc = lewati titik ini. Konfirmasi lewat MOUSE juga, karena
    tombol keyboard cuma sampai kalau jendela ini yang memegang fokus -- di
    lapangan Enter-nya nyasar ke terminal dan sesi tampak macet.
    Titik yang sudah tercatat tetap digambar (lingkaran hijau) supaya
    sebarannya kelihatan. `kamera` cukup punya read() -> (ok, frame).
    """
    import cv2

    tercatat = []
    petunjuk = ("klik kiri: tandai tengah koin | klik kanan: SIMPAN | "
                "klik kanan tanpa tanda: LEWATI (koin tak terlihat)")

    def baca():
        klik, simpan = [], []

        def saat_klik(ev, x, y, *_):
            if ev == cv2.EVENT_LBUTTONDOWN:
                klik[:] = [(x, y)]
            elif ev == cv2.EVENT_RBUTTONDOWN:
                simpan.append(bool(klik))   # tanpa tanda = lewati titik ini

        cv2.namedWindow(judul)
        cv2.setMouseCallback(judul, saat_klik)
        print("      klik kiri TENGAH penanda di jendela kamera, lalu KLIK KANAN untuk "
              "simpan (koin tak terlihat? klik kanan tanpa tanda = lewati)", flush=True)
        try:
            return _ulang(klik, simpan)
        finally:
            # Jendela ditutup tiap selesai satu titik: di antara titik tidak
            # ada yang memanggil waitKey (robot bergerak, terminal menunggu
            # Enter), jadi jendela yang dibiarkan terbuka dicap Windows
            # "Not Responding" dan operator menutupnya -> sesi mati.
            cv2.destroyWindow(judul)
            cv2.waitKey(1)

    def _ulang(klik, simpan):
        while True:
            ok, frame = kamera.read()
            if ok and frame is not None:
                for p in tercatat:
                    cv2.circle(frame, p, 8, (0, 200, 0), 2)
                for p in klik:
                    cv2.drawMarker(frame, p, (0, 0, 255), cv2.MARKER_CROSS, 30, 2)
                for warna, tebal in (((0, 0, 0), 4), ((255, 255, 255), 1)):
                    cv2.putText(frame, petunjuk, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                                0.6, warna, tebal, cv2.LINE_AA)
                cv2.imshow(judul, frame)
            tombol = cv2.waitKey(30) & 0xFF
            if simpan and not simpan[-1]:
                raise ValueError("dilewati operator")
            if (simpan or tombol in (13, 10, 32)) and klik:
                tercatat.append(klik[0])
                return float(klik[0][0]), float(klik[0][1])
            if tombol == 27:
                raise ValueError("dilewati operator")
            if tombol != 255:
                print(f"      (tombol kode {tombol} diabaikan)", flush=True)

    return baca


def uji_kalibrasi(bot, sumber, z_belt, tinggi, cetak=print, tanya=input):
    """Robot MENUNJUK benda yang dilihat kamera -- tanpa vakum, tanpa bak.

    Uji rantai penuh (YOLO -> piksel -> kalibrasi -> mm -> gerak) sebelum
    jalur.py dijalankan: ujung vakum melayang MELAYANG_MM di atas tiap benda,
    operator melihat apakah tepat di tengahnya.

    sumber : SumberDeteksi yang mengembalikan Deteksi dalam mm
             (SumberTCP dengan kalibrasi=...)
    tinggi : fungsi label -> tinggi benda (mm), mis. jalur.profil(l)["tinggi"]
    Return daftar (label, x_mm, y_mm) yang berhasil ditunjuk.
    """
    import kontrak_kamera
    import rnv3

    ditunjuk = []
    while True:
        try:
            jawab = tanya("\ntaruh benda di belt -- Enter = tunjuk, q = selesai: ")
        except (KeyboardInterrupt, EOFError):
            break
        if jawab.strip().lower() == "q":
            break
        try:
            hasil = sumber.deteksi()
        except kontrak_kamera.KameraError as e:
            cetak(f"  kamera: {e}")
            continue
        if not hasil:
            cetak("  kamera tidak melihat benda (cek ROI dan pencahayaan)")
            continue
        for d in hasil:
            z = z_belt + tinggi(d.label) + MELAYANG_MM
            naik = z + 50.0
            piksel = (f"piksel ({d.u_px:.0f}, {d.v_px:.0f}) -> "
                      if d.u_px is not None else "")
            cetak(f"  {d.label} conf {d.conf:.2f}  {piksel}X{d.x_mm:.1f} Y{d.y_mm:.1f}")
            ok, alasan = rnv3.check_reach(d.x_mm, d.y_mm, z)
            if not ok:
                cetak(f"    dilewati: {alasan}")
                continue
            try:
                bot.move(z=max(bot.z, naik), f=30)
                bot.goto_xy(d.x_mm, d.y_mm, f=60)
                bot.move(z=z, f=20)
                tanya(f"    vakum melayang {MELAYANG_MM:.0f} mm di atasnya -- "
                      "tepat di tengah? Enter = lanjut ")
                bot.move(z=naik, f=20)
            except rnv3.RNV3Error as e:
                cetak(f"    gerakan ditolak: {e}")
                continue
            except (KeyboardInterrupt, EOFError):
                bot.move(z=naik, f=20)
                return ditunjuk
            ditunjuk.append((d.label, d.x_mm, d.y_mm))
    return ditunjuk


# ---------------------------------------------------------------------------
# UJI MANDIRI
# ---------------------------------------------------------------------------
def _demo():
    """Bangun homografi yang SUDAH DIKETAHUI, lalu cek apakah bisa ditemukan lagi."""
    import random

    print("=" * 68)
    print("DEMO KALIBRASI — titik buatan, jawabannya sudah diketahui")
    print("=" * 68)

    # Homografi "asli": skala + geser + sedikit perspektif.
    asli = [[0.32, 0.015, -190.0],
            [0.004, 0.30, 180.0],
            [0.00006, 0.00011, 1.0]]

    def pakai(H, u, v):
        w = H[2][0] * u + H[2][1] * v + H[2][2]
        return ((H[0][0] * u + H[0][1] * v + H[0][2]) / w,
                (H[1][0] * u + H[1][1] * v + H[1][2]) / w)

    piksel = [(120, 90), (1150, 110), (1180, 620), (90, 640), (640, 360), (400, 200)]
    pasangan = [((u, v), pakai(asli, u, v)) for u, v in piksel]

    k = Kalibrasi.dari_titik(pasangan, catatan="demo sintetis")
    print(f"\n{k.laporan()}")

    print("\n  piksel          mm sebenarnya        mm hasil kalibrasi    selisih")
    for (u, v), (x, y) in pasangan:
        px, py = k.ke_mm(u, v)
        print(f"  ({u:4.0f},{v:4.0f})   ({x:7.2f},{y:7.2f})   "
              f"({px:7.2f},{py:7.2f})   {math.hypot(px - x, py - y):.4f} mm")

    assert k.galat_maks() < 1e-6, "homografi tidak berhasil ditemukan kembali"
    print("\n  [OK] homografi asli berhasil ditemukan kembali (galat ~ 0)")

    # Dengan derau pengukuran: operator tidak pernah menandai piksel tepat.
    rng = random.Random(42)
    berderau = [((u + rng.uniform(-2, 2), v + rng.uniform(-2, 2)), mm)
                for (u, v), mm in pasangan]
    kd = Kalibrasi.dari_titik(berderau, catatan="demo + derau 2 px")
    print(f"\n  dengan derau tanda ±2 piksel: galat rata-rata "
          f"{kd.galat_rata2():.2f} mm, terburuk {kd.galat_maks():.2f} mm")
    print("  (segitu wajar — makanya jangan asal menandai titik)")

    # Titik segaris harus DITOLAK, bukan diam-diam menghasilkan sampah.
    print("\n  uji penolakan titik segaris:")
    segaris = [((100, 100), (0.0, 250.0)), ((200, 200), (10.0, 260.0)),
               ((300, 300), (20.0, 270.0)), ((400, 400), (30.0, 280.0))]
    try:
        Kalibrasi.dari_titik(segaris)
    except KalibrasiError as e:
        print(f"  [OK] ditolak: {str(e)[:60]}...")
    else:
        print("  [GAGAL] titik segaris malah diterima")

    # Simpan lalu muat harus menghasilkan hasil yang sama persis.
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "kalibrasi_demo.json")
    k.simpan(path)
    k2 = Kalibrasi.muat(path)
    assert abs(k2.ke_mm(640, 360)[0] - k.ke_mm(640, 360)[0]) < 1e-9
    print(f"\n  [OK] simpan/muat utuh ({path})")
    os.remove(path)

    print("\nSEMUA UJI LULUS")
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    # python.exe yang dipanggil dari terminal WSL menulis ke pipe, bukan konsol:
    # tanpa ini output ter-buffer dan program tampak macet padahal jalan.
    sys.stdout.reconfigure(line_buffering=True)

    p = argparse.ArgumentParser(description="Kalibrasi piksel -> mm.")
    p.add_argument("--demo", action="store_true",
                   help="uji mandiri dengan titik buatan, tanpa alat apa pun")
    p.add_argument("--sesi", action="store_true",
                   help="pemandu kalibrasi (BUTUH robot + kamera terpasang)")
    p.add_argument("--port", default=None,
                   help="port serial robot (default: cari chip CH340 otomatis)")
    p.add_argument("--z", type=float, default=None,
                   help="tinggi permukaan belt (default: z_belt di setelan-robot.json)")
    p.add_argument("--keluar", default="kalibrasi.json")
    p.add_argument("--kamera", default=None, metavar="SUMBER",
                   help="[--sesi] buka kamera ini (mis. 0) -> MODE PENANDA: klik "
                        "penanda di jendela, tidak perlu ketik piksel")
    p.add_argument("--lebar", type=int, default=1280,
                   help="[--kamera] HARUS sama dengan server_kamera.py")
    p.add_argument("--tinggi", type=int, default=720)
    p.add_argument("--x", type=float, nargs="+", default=[-120.0, 0.0, 120.0],
                   help="[--sesi] kolom X grid kalibrasi (mm). Pilih yang TERLIHAT "
                        "kamera, mis. --x -160 -120 -80")
    p.add_argument("--y", type=float, nargs="+", default=[240.0, 265.0, 290.0],
                   help="[--sesi] baris Y grid kalibrasi (mm)")
    p.add_argument("--minggir", default=None, metavar="X,Y",
                   help="[--kamera] titik lengan keluar dari pandangan kamera "
                        "(default: XY parkir di setelan-robot.json)")
    p.add_argument("--uji", action="store_true",
                   help="robot MENUNJUK benda yang dideteksi server_kamera.py "
                        "(harus sudah jalan) memakai --keluar sebagai kalibrasi")
    a = p.parse_args()

    if a.demo or not (a.sesi or a.uji):
        raise SystemExit(_demo())

    import setelan
    _s = setelan.muat()
    if a.z is None:
        a.z = _s["z_belt"]
        print(f"z belt dari setelan-robot.json: {a.z}")

    import rnv3

    # Dicek SEBELUM kamera dibuka: robot tak tercolok harus ketahuan di sini,
    # bukan sebagai traceback serial setelah jendela kamera muncul.
    if not a.port:
        a.port = rnv3.cari_port()
        if not a.port:
            raise SystemExit(1)
        print(f"robot ditemukan di {a.port}")
    else:
        import serial.tools.list_ports as lp
        terlihat = [p.device for p in lp.comports()]
        if a.port.upper() not in (d.upper() for d in terlihat):
            raise SystemExit(
                f"port {a.port} tidak ada. Port yang terlihat: "
                f"{', '.join(terlihat) or 'TIDAK ADA'}\n"
                "  - robot sudah dicolok USB ke laptop INI dan power-nya ON?\n"
                "  - kabel USB-nya kabel DATA, bukan kabel cas?\n"
                "  - atau hapus --port supaya dicari otomatis")

    def buka_robot():
        try:
            return rnv3.RNV3(a.port, verbose=False)
        except Exception as err:
            raise SystemExit(
                f"\nGagal membuka {a.port}: {err}\n"
                "  - tutup GUI robot / Arduino IDE (satu port satu program)\n"
                "  - port terdaftar tapi 'cannot find the file'? chip USB robot\n"
                "    gagal menyala (Device Manager: kode 10) -- sering karena hub\n"
                "    USB. Colok langsung ke laptop, atau cabut-colok dengan robot ON.")

    if a.uji:
        import jalur
        import kontrak_kamera
        kal = Kalibrasi.muat(a.keluar)
        print(kal.laporan())
        sumber = kontrak_kamera.SumberTCP(kalibrasi=kal)
        if not sumber.ping():
            raise SystemExit("server kamera tidak menjawab -- jalankan dulu: "
                             "python server_kamera.py --sumber 0")
        with buka_robot() as bot:
            bot.home()
            uji_kalibrasi(bot, sumber, a.z, lambda l: jalur.profil(l)["tinggi"])
        raise SystemExit(0)

    if a.kamera is not None:
        mx, my = ((float(n) for n in a.minggir.split(",")) if a.minggir
                  else _s["parkir"][:2])
        z_minggir = a.z + 60
        ok, alasan = rnv3.check_reach(mx, my, z_minggir)
        if not ok:
            raise SystemExit(f"titik minggir X{mx} Y{my} Z{z_minggir} tak "
                             f"terjangkau: {alasan} -- pakai --minggir")

    # Robot dibuka DULU: kalau port-nya bermasalah, kamera tidak perlu dibuka.
    with buka_robot() as bot:
        kamera = minggir = baca = None
        try:
            if a.kamera is not None:
                import server_kamera
                kamera, _ = server_kamera.buka_yolo(
                    sumber=a.kamera, lebar=a.lebar, tinggi=a.tinggi, pakai_model=False)
                baca = pembaca_klik_kamera(kamera)

                def minggir():
                    bot.move(z=z_minggir, f=20)
                    bot.goto_xy(mx, my, f=60)
            bot.home()
            titik = [(x, y) for y in a.y for x in a.x]
            k = sesi_kalibrasi(bot, titik_mm=titik, z=a.z, baca_piksel=baca,
                               minggir=minggir)
            k.simpan(a.keluar)
            print(f"\ntersimpan di {a.keluar}")
        finally:
            if kamera is not None:
                import cv2
                kamera.release()
                cv2.destroyAllWindows()
