"""
Setelan hasil ukur di lapangan -- SATU berkas untuk semua program.

Dulu titik parkir ditulis di tiga tempat (jog.PARKIR, jalur.PARKIR_XY +
Z_PARKIR, pick_place.Z_PARKIR). Ukur ulang lalu lupa mengganti satu file =
satu program parkir di tempat lama dan menabrak. Sekarang angka-angka itu
tinggal di setelan-robot.json, dan ketiga program membacanya saat diimpor.

Yang disimpan di sini HANYA angka yang diukur di robot nyata:

    z_belt     permukaan atas belt (Z ujung vakum menyentuh belt)
    z_bak      dasar bak keluaran
    bak        {label: [x, y]} tengah tiap bak
    parkir     [x, y, z, e] titik parkir (e = posisi rel); di bawahnya harus
               KOSONG. Berkas lama berisi [x, y, z] dibaca sebagai e=0.
    diukur     {kunci: "YYYY-mm-dd HH:MM"} kapan tiap angka terakhir diukur.
               Kunci yang tidak ada di sini = masih angka tebakan.

Berkas tidak ada / rusak -> pakai BAWAAN dan beri peringatan, bukan crash.

PENGUJIAN: cek.py men-set RNV3_SETELAN=- SEBELUM mengimpor modul lain, jadi
tes selalu memakai BAWAAN dan tidak pernah membaca atau menulis berkas milik
pengguna (pelajaran dari batas-fisik.json).
"""

import copy
import json
import os
import sys
import time
import warnings

BERKAS_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "setelan-robot.json")

# Angka tebakan -- SAMA dengan konstanta lama di jalur.py / jog.py /
# pick_place.py, supaya perilaku tidak berubah sebelum ada yang diukur.
BAWAAN = {
    "z_belt": -80.0,
    "z_bak": -80.0,
    "bak": {
        "tutup_botol": [-170.0, 215.0],
        "koin": [-55.0, 300.0],
    },
    "parkir": [0.0, 217.0, -40.0, 0.0],
    "diukur": {},
}

# Kunci yang boleh diisi dari posisi lengan, dan sumbu yang diambil.
KUNCI_UKUR = {
    "z_belt": ("z",),
    "z_bak": ("z",),
    "bak_koin": ("x", "y"),
    "bak_tutup_botol": ("x", "y"),
    "parkir": ("x", "y", "z", "e"),
}


def berkas_aktif():
    """Path berkas yang dipakai, atau None kalau berkas sengaja diabaikan.

    RNV3_SETELAN=-        -> abaikan berkas, selalu BAWAAN (dipakai cek.py)
    RNV3_SETELAN=<path>   -> pakai berkas itu
    tidak diset           -> setelan-robot.json di sebelah file ini
    """
    nilai = os.environ.get("RNV3_SETELAN")
    if nilai == "-":
        return None
    return nilai or BERKAS_DEFAULT


def _angka(v, nama):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{nama} harus angka, dapat {v!r}")
    return float(v)


def _pasangan(v, n, nama):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ValueError(f"{nama} harus daftar {n} angka, dapat {v!r}")
    return [_angka(a, nama) for a in v]


def rapikan(data):
    """Gabungkan `data` ke atas BAWAAN dan validasi tipenya.

    Raise ValueError kalau ada nilai yang bentuknya salah -- lebih baik gagal
    keras daripada robot parkir di koordinat hasil salah ketik.
    """
    if not isinstance(data, dict):
        raise ValueError("isi setelan harus objek JSON")
    hasil = copy.deepcopy(BAWAAN)
    if "z_belt" in data:
        hasil["z_belt"] = _angka(data["z_belt"], "z_belt")
    if "z_bak" in data:
        hasil["z_bak"] = _angka(data["z_bak"], "z_bak")
    if "bak" in data:
        if not isinstance(data["bak"], dict):
            raise ValueError("bak harus objek {label: [x, y]}")
        for label, xy in data["bak"].items():
            hasil["bak"][str(label)] = _pasangan(xy, 2, f"bak.{label}")
    if "parkir" in data:
        parkir = data["parkir"]
        if isinstance(parkir, (list, tuple)) and len(parkir) == 3:
            parkir = [*parkir, 0.0]     # berkas lama, sebelum rel ikut parkir
        hasil["parkir"] = _pasangan(parkir, 4, "parkir")
    if "diukur" in data:
        if not isinstance(data["diukur"], dict):
            raise ValueError("diukur harus objek {kunci: waktu}")
        hasil["diukur"] = {str(k): str(v) for k, v in data["diukur"].items()}
    return hasil


def muat(berkas=None):
    """Setelan lengkap (dict baru). Tidak pernah raise.

    berkas=None -> berkas_aktif(). Berkas tidak ada = BAWAAN tanpa pesan;
    berkas rusak = BAWAAN + peringatan, karena itu kesalahan yang harus
    kelihatan tapi tidak boleh menjatuhkan program.
    """
    berkas = berkas if berkas is not None else berkas_aktif()
    if berkas is None:
        return copy.deepcopy(BAWAAN)
    try:
        with open(berkas, encoding="utf-8") as f:
            return rapikan(json.load(f))
    except FileNotFoundError:
        return copy.deepcopy(BAWAAN)
    except (OSError, ValueError) as e:
        warnings.warn(f"{os.path.basename(berkas)} tidak bisa dipakai ({e}); "
                      f"memakai angka bawaan", RuntimeWarning, stacklevel=2)
        return copy.deepcopy(BAWAAN)


def simpan(data, berkas=None):
    """Tulis setelan (sudah divalidasi) secara atomik. Return path-nya."""
    berkas = berkas if berkas is not None else berkas_aktif()
    if berkas is None:
        raise RuntimeError("RNV3_SETELAN=- : berkas setelan sengaja diabaikan")
    data = rapikan(data)
    sementara = berkas + ".tmp"
    with open(sementara, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    # replace() atomik: listrik padam di tengah menulis tidak meninggalkan
    # berkas setengah jadi yang lalu diabaikan diam-diam saat dimuat.
    os.replace(sementara, berkas)
    return berkas


def hapus(berkas=None):
    """Kembali ke BAWAAN dengan menghapus berkas. Return True kalau ada yang dihapus.

    Menghapus (bukan menulis BAWAAN ke berkas) supaya "belum pernah diukur"
    tetap terbaca jujur: berkas tidak ada = semua angka masih bawaan.
    """
    berkas = berkas if berkas is not None else berkas_aktif()
    if berkas is None:
        raise RuntimeError("RNV3_SETELAN=- : berkas setelan sengaja diabaikan")
    try:
        os.remove(berkas)
        return True
    except FileNotFoundError:
        return False


def isi_dari_posisi(data, kunci, x, y, z, waktu=None, e=0.0):
    """Salinan `data` dengan `kunci` diisi dari posisi lengan (x, y, z, e)."""
    if kunci not in KUNCI_UKUR:
        raise ValueError(f"kunci tidak dikenal: {kunci!r}")
    hasil = rapikan(data)
    if kunci in ("z_belt", "z_bak"):
        hasil[kunci] = round(float(z), 2)
    elif kunci.startswith("bak_"):
        hasil["bak"][kunci[len("bak_"):]] = [round(float(x), 2),
                                             round(float(y), 2)]
    elif kunci == "parkir":
        hasil["parkir"] = [round(float(x), 2), round(float(y), 2),
                           round(float(z), 2), round(float(e), 2)]
    hasil["diukur"][kunci] = waktu or time.strftime("%Y-%m-%d %H:%M")
    return hasil


def terapkan(data):
    """Pasang setelan ke modul yang SUDAH diimpor, tanpa restart.

    Modul membaca setelan sekali saat diimpor. Aplikasi yang menyimpan angka
    baru memanggil ini supaya jalur/jog/pick_place langsung memakainya.
    """
    data = rapikan(data)
    px, py, pz, pe = data["parkir"]
    jalur = sys.modules.get("jalur")
    if jalur is not None:
        jalur.Z_BELT = data["z_belt"]
        jalur.Z_BAK = data["z_bak"]
        jalur.PARKIR_XY = (px, py)
        jalur.Z_PARKIR = pz
        jalur.PARKIR_E = pe
        for label, xy in data["bak"].items():
            if label in jalur.JENIS:
                jalur.JENIS[label]["taruh"] = tuple(xy)
    jog = sys.modules.get("jog")
    if jog is not None:
        jog.PARKIR = (px, py, pz, pe)
    pick_place = sys.modules.get("pick_place")
    if pick_place is not None:
        pick_place.PARKIR_XY = (px, py)
        pick_place.Z_PARKIR = pz
        pick_place.PARKIR_E = pe
