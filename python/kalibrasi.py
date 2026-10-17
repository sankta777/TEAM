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
    python3 kalibrasi.py --sesi        # pemandu kalibrasi (butuh robot)

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
def sesi_kalibrasi(bot, titik_mm=None, z=None, baca_piksel=None, cetak=print):
    """Pandu operator mengumpulkan pasangan titik, lalu bangun Kalibrasi.

    bot          : objek RNV3 yang sudah di-home
    titik_mm     : daftar (x, y) tempat robot akan berdiri. Sebarkan!
    z            : tinggi saat menandai; pakai tinggi permukaan belt
    baca_piksel  : fungsi () -> (u, v). Default: tanya lewat input().
                   Kelompok kamera bisa menyediakan versi yang membaca
                   langsung dari frame, supaya tidak perlu diketik manual.

    Alur tiap titik: robot pindah ke (x, y) yang kita TAHU persis -> operator
    melihat di mana ujung gripper muncul di gambar kamera -> koordinat
    pikselnya dicatat. Ulangi minimal 4 kali.
    """
    if titik_mm is None:
        # Sebaran kotak di zona ambil. Sengaja tidak segaris.
        titik_mm = [(-120.0, 240.0), (120.0, 240.0),
                    (120.0, 290.0), (-120.0, 290.0),
                    (0.0, 265.0)]
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
    for i, (x, y) in enumerate(titik_mm, 1):
        ok, alasan = __import__("rnv3").check_reach(x, y, z)
        if not ok:
            cetak(f"  [{i}] ({x:.0f}, {y:.0f}) DILEWATI — {alasan}")
            continue
        cetak(f"  [{i}/{len(titik_mm)}] robot -> X{x:.0f} Y{y:.0f} Z{z:.0f}")
        bot.move(z=max(z + 60, z), f=30)
        bot.goto_xy(x, y, f=60)
        bot.move(z=z, f=20)
        try:
            u, v = baca_piksel()
        except (ValueError, KeyboardInterrupt):
            cetak("      dilewati")
            continue
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

    p = argparse.ArgumentParser(description="Kalibrasi piksel -> mm.")
    p.add_argument("--demo", action="store_true",
                   help="uji mandiri dengan titik buatan, tanpa alat apa pun")
    p.add_argument("--sesi", action="store_true",
                   help="pemandu kalibrasi (BUTUH robot + kamera terpasang)")
    p.add_argument("--port", default="COM4", help="port serial robot")
    p.add_argument("--z", type=float, default=-80.0,
                   help="tinggi permukaan belt (UKUR DULU)")
    p.add_argument("--keluar", default="kalibrasi.json")
    a = p.parse_args()

    if a.demo or not a.sesi:
        raise SystemExit(_demo())

    import rnv3
    with rnv3.RNV3(a.port, verbose=False) as bot:
        bot.home()
        k = sesi_kalibrasi(bot, z=a.z)
        k.simpan(a.keluar)
        print(f"\ntersimpan di {a.keluar}")
