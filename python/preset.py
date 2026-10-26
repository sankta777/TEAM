"""
Preset: urutan langkah yang direkam operator lalu diputar ulang.

Modul ini MURNI: tidak menyentuh port serial. Ia menyimpan, memvalidasi, dan
menghitung rute; aplikasi.py yang mengeksekusi. Dengan begitu aturan rute
yang divalidasi PERSIS sama dengan yang dijalankan -- satu fungsi
(rencana_gerak) dipakai keduanya.

LANGKAH

    {"jenis": "gerak",   "x":.., "y":.., "z":.., "e":..}   lengan ke titik ini
    {"jenis": "vakum",   "nyala": true|false}
    {"jenis": "gripper", "buka": true|false}
    {"jenis": "tunggu",  "detik": 1.5}

RUTE

  - Gerak di DALAM satu preset = garis lurus, persis seperti direkam.
  - Masuk ke sebuah preset (dari posisi lengan sekarang, atau dari akhir
    preset sebelumnya saat loop) = lewat tinggi aman: naik dulu, pindah XY +
    rel di atas, baru turun. Lengan tidak menyeret benda atau menabrak belt
    saat berpindah antar-preset.

BERKAS preset.json:  {"preset": [...], "sampah": [...]}
Yang dihapus pindah ke "sampah" dulu, jadi salah klik masih bisa dipulihkan.
"""

import copy
import json
import os
import time
import uuid

import jalur
import rnv3

BERKAS_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "preset.json")
TUNGGU_MAKS = 60.0       # detik; lebih dari ini hampir pasti salah ketik
NAMA_MAKS = 40


class PresetError(ValueError):
    """Isi preset/langkah tidak valid."""


def kosong():
    return {"preset": [], "sampah": []}


# ---------------------------------------------------------------------------
# BENTUK DATA
# ---------------------------------------------------------------------------
def _angka(v, nama):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PresetError(f"{nama} harus angka, dapat {v!r}")
    return float(v)


def langkah_gerak(x, y, z, e):
    return {"jenis": "gerak", "x": round(float(x), 2), "y": round(float(y), 2),
            "z": round(float(z), 2), "e": round(float(e), 2)}


def rapikan_langkah(l):
    if not isinstance(l, dict):
        raise PresetError(f"langkah harus objek, dapat {l!r}")
    jenis = l.get("jenis")
    if jenis == "gerak":
        return langkah_gerak(*(_angka(l.get(k), k) for k in "xyze"))
    if jenis == "vakum":
        if not isinstance(l.get("nyala"), bool):
            raise PresetError("langkah vakum butuh nyala true/false")
        return {"jenis": "vakum", "nyala": l["nyala"]}
    if jenis == "gripper":
        if not isinstance(l.get("buka"), bool):
            raise PresetError("langkah gripper butuh buka true/false")
        return {"jenis": "gripper", "buka": l["buka"]}
    if jenis == "tunggu":
        d = _angka(l.get("detik"), "detik")
        if not 0 < d <= TUNGGU_MAKS:
            raise PresetError(f"tunggu harus 0..{TUNGGU_MAKS:g} detik, dapat {d:g}")
        return {"jenis": "tunggu", "detik": round(d, 2)}
    raise PresetError(f"jenis langkah tidak dikenal: {jenis!r}")


def rapikan_nama(nama):
    nama = str(nama or "").strip()
    if not nama:
        raise PresetError("nama preset wajib diisi")
    if len(nama) > NAMA_MAKS:
        raise PresetError(f"nama maksimal {NAMA_MAKS} huruf")
    return nama


def rapikan_preset(p):
    if not isinstance(p, dict):
        raise PresetError("preset harus objek")
    langkah = p.get("langkah")
    if not isinstance(langkah, list) or not langkah:
        raise PresetError("preset harus punya minimal satu langkah")
    hasil = {"id": str(p.get("id") or uuid.uuid4().hex[:8]),
             "nama": rapikan_nama(p.get("nama")),
             "dibuat": str(p.get("dibuat") or ""),
             "langkah": [rapikan_langkah(l) for l in langkah]}
    if "dihapus" in p:
        hasil["dihapus"] = str(p["dihapus"])
    return hasil


def rapikan(data):
    if not isinstance(data, dict):
        raise PresetError("isi berkas preset harus objek")
    return {k: [rapikan_preset(p) for p in data.get(k, [])]
            for k in ("preset", "sampah")}


# ---------------------------------------------------------------------------
# BERKAS
# ---------------------------------------------------------------------------
def muat(berkas):
    """(data, peringatan). Berkas tidak ada = kosong; rusak = kosong + peringatan.

    Berkas rusak TIDAK ditimpa di sini: yang menimpa hanya simpan(), dan itu
    baru terjadi kalau operator mengubah sesuatu.
    """
    if not berkas:
        return kosong(), None
    try:
        with open(berkas, encoding="utf-8") as f:
            return rapikan(json.load(f)), None
    except FileNotFoundError:
        return kosong(), None
    except (OSError, ValueError) as e:
        return kosong(), f"{os.path.basename(berkas)} tidak bisa dibaca ({e})"


def simpan(data, berkas):
    """Tulis atomik (sama dengan setelan.simpan)."""
    data = rapikan(data)
    if not berkas:
        return data
    sementara = berkas + ".tmp"
    with open(sementara, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(sementara, berkas)
    return data


# ---------------------------------------------------------------------------
# UBAH DAFTAR (semua mengembalikan data BARU)
# ---------------------------------------------------------------------------
def tambah(data, nama, langkah, waktu=None):
    baru = copy.deepcopy(data)
    p = rapikan_preset({"nama": nama, "langkah": langkah,
                        "dibuat": waktu or time.strftime("%Y-%m-%d %H:%M")})
    baru["preset"].append(p)
    return baru, p


def buang(data, ids, waktu=None):
    """Pindahkan preset dengan id tertentu ke sampah."""
    ids = set(ids)
    tak_ada = ids - {p["id"] for p in data["preset"]}
    if tak_ada:
        raise PresetError(f"preset tidak ada: {sorted(tak_ada)}")
    baru = copy.deepcopy(data)
    waktu = waktu or time.strftime("%Y-%m-%d %H:%M")
    tetap = []
    for p in baru["preset"]:
        if p["id"] in ids:
            p["dihapus"] = waktu
            baru["sampah"].append(p)
        else:
            tetap.append(p)
    baru["preset"] = tetap
    return baru


def pulihkan(data, id_):
    baru = copy.deepcopy(data)
    for i, p in enumerate(baru["sampah"]):
        if p["id"] == id_:
            p.pop("dihapus", None)
            baru["preset"].append(baru["sampah"].pop(i))
            return baru
    raise PresetError(f"tidak ada di sampah: {id_}")


def kosongkan_sampah(data):
    baru = copy.deepcopy(data)
    baru["sampah"] = []
    return baru


def cari(data, id_):
    for p in data["preset"]:
        if p["id"] == id_:
            return p
    raise PresetError(f"preset tidak ada: {id_}")


# ---------------------------------------------------------------------------
# RUTE & VALIDASI
# ---------------------------------------------------------------------------
def tinggi_aman(asal, tujuan):
    return max(jalur.Z_SAFE, asal[2], tujuan[2])


def rencana_gerak(asal, tujuan, lewat_aman):
    """Titik-titik (x, y, z, e) dari asal ke tujuan, TANPA asal.

    lewat_aman=False -> satu garis lurus. True -> naik, pindah, turun.
    """
    asal, tujuan = tuple(asal), tuple(tujuan)
    if not lewat_aman:
        rute = [tujuan]
    else:
        za = tinggi_aman(asal, tujuan)
        rute = [(asal[0], asal[1], za, asal[3]),
                (tujuan[0], tujuan[1], za, tujuan[3]),
                tujuan]
    hasil, sebelum = [], asal
    for t in rute:
        if any(abs(a - b) > 1e-6 for a, b in zip(t, sebelum)):
            hasil.append(t)
            sebelum = t
    return hasil


def periksa_rute(asal, rute):
    """(ok, alasan) untuk satu rute: tiap titik check_aman, tiap ruas check_path."""
    sebelum = asal
    for t in rute:
        ok, alasan = rnv3.check_aman(*t)
        if not ok:
            return False, alasan
        ok, alasan, _ = rnv3.check_path(sebelum[:3], t[:3])
        if not ok:
            return False, alasan
        sebelum = t
    return True, ""


def _titik(l):
    return (l["x"], l["y"], l["z"], l["e"])


def validasi_internal(p):
    """Status per langkah TANPA tahu dari mana lengan datang.

    Langkah gerak pertama cukup dicek titiknya; jalur masuknya bergantung
    pada posisi saat diputar dan dicek di validasi_urutan().
    """
    status, sebelum = [], None
    for l in p["langkah"]:
        if l["jenis"] != "gerak":
            status.append({"ok": True, "alasan": ""})
            continue
        t = _titik(l)
        if sebelum is None:
            ok, alasan = rnv3.check_aman(*t)
        else:
            ok, alasan = periksa_rute(sebelum, rencana_gerak(sebelum, t, False))
        status.append({"ok": ok, "alasan": alasan})
        sebelum = t
    return status


def validasi_urutan(daftar, posisi_awal, berulang):
    """Cek SELURUH putaran sebelum apa pun bergerak.

    daftar      : preset dalam urutan putar
    posisi_awal : (x, y, z, e) lengan sekarang
    berulang    : True kalau lebih dari satu putaran -> perpindahan dari akhir
                  preset terakhir kembali ke preset pertama ikut dicek.
    Return (ok, pesan_galat_pertama).
    """
    pos = tuple(posisi_awal)
    putaran = 2 if berulang else 1
    for _ in range(putaran):
        for p in daftar:
            pertama = True
            for n, l in enumerate(p["langkah"], 1):
                if l["jenis"] != "gerak":
                    continue
                t = _titik(l)
                ok, alasan = periksa_rute(pos, rencana_gerak(pos, t, pertama))
                if not ok:
                    asal = "masuk ke" if pertama else "di"
                    return False, f"preset '{p['nama']}' {asal} langkah {n}: {alasan}"
                pos, pertama = t, False
    return True, ""
