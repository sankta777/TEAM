"""
Kontrak serah-terima kelompok kamera -> orkestrator robot.

File ini SATU-SATUNYA batas software antara dua kelompok. Semua yang lain
(konveyor, lengan, sensor) sudah di dalam board robot dan dipegang orkestrator.

PROTOKOL: TCP, JSON Lines, request/response.
Satu baris JSON per pesan, diakhiri '\n'. Bisa dites manual pakai netcat:

    $ nc 127.0.0.1 5555
    {"cmd":"detect"}
    {"ok": true, "ts": 1792047037.1, "objects": [...]}

Permintaan:
    {"cmd": "detect"}       minta deteksi SEKARANG dari frame terbaru
    {"cmd": "ping"}         cek hidup, balas {"ok": true, "pong": true}

Balasan sukses:
    {"ok": true, "ts": <epoch float>, "objects": [
        {"x_mm": -40.5, "y_mm": 265.0, "angle_deg": 12.0,
         "label": "botol", "conf": 0.92}
    ]}

Balasan gagal:
    {"ok": false, "error": "<alasan singkat>"}

ATURAN YANG HARUS DISEPAKATI TERTULIS:

1. x_mm / y_mm dalam KOORDINAT ROBOT -- origin di pusat rotasi lengan,
   satuan milimeter. Bukan piksel, bukan koordinat belt, bukan koordinat meja.
   Konversi piksel->mm tanggung jawab kelompok kamera.

2. Tidak ada benda = {"ok": true, "objects": []}. BUKAN error, bukan diam,
   bukan koneksi digantung. Belt kosong itu kondisi normal.

3. Kamera wajib membalas dalam TIMEOUT_DEFAULT detik. Orkestrator tidak akan
   menunggu lebih lama dari itu.

4. angle_deg, label, conf boleh dikosongkan. x_mm dan y_mm wajib.

5. Orkestrator TETAP memanggil rnv3.check_reach() sebelum menggerakkan lengan.
   Kamera boleh saja melaporkan titik di luar jangkauan -- itu bukan dosa,
   nanti di-skip. Jangan mencoba menebak jangkauan robot dari sisi kamera.

CATATAN KALIBRASI (ini kerjaan BERSAMA, bukan kelompok kamera sendirian):
supaya kamera bisa melapor dalam mm koordinat robot, dia perlu tahu di mana
origin robot berada di frame-nya. Caranya robot disuruh ke >=4 titik yang
diketahui, marker ditempel di ujung gripper, kamera memotret tiap titik, lalu
dibangun homography piksel->mm. Jadi butuh robot menyala DAN kamera sudah
terpasang kaku. Homography itu hanya berlaku untuk satu bidang datar di satu
ketinggian -- benda dengan tinggi berbeda akan meleset.
"""

import json
import random
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, asdict

import rnv3

PORT_DEFAULT = 5555
TIMEOUT_DEFAULT = 2.0
BATAS_BALASAN = 1 << 20          # 1 MB, pengaman kalau kamera tidak kirim '\n'


class KameraError(RuntimeError):
    """Kamera tidak bisa dihubungi, timeout, atau balas format ngawur."""


# ---------------------------------------------------------------------------
# SATUAN DATA
# ---------------------------------------------------------------------------
@dataclass
class Deteksi:
    """Satu benda yang dilihat kamera, dalam koordinat robot (mm)."""

    x_mm: float = None           # None kalau kamera baru mengirim piksel
    y_mm: float = None
    angle_deg: float = 0.0       # rotasi benda di bidang XY; 0 = tidak dipakai
    label: str = ""              # nama kelas, bebas; menentukan tinggi & bak
    conf: float = 1.0            # 0..1, keyakinan detektor
    u_px: float = None           # posisi di GAMBAR, kalau kamera kirim piksel
    v_px: float = None

    @property
    def punya_mm(self):
        return self.x_mm is not None and self.y_mm is not None

    def ke_mm(self, kal):
        """Salinan Deteksi ini dengan x_mm/y_mm diisi dari piksel.

        `kal` objek apa pun yang punya ke_mm(u, v) -- biasanya
        kalibrasi.Kalibrasi. Sengaja duck-typing supaya kontrak.py tidak
        wajib mengimpor kalibrasi.py.
        """
        if self.punya_mm:
            return self
        if self.u_px is None or self.v_px is None:
            raise KameraError(f"tidak punya mm maupun piksel: {self!r}")
        x, y = kal.ke_mm(self.u_px, self.v_px)
        return Deteksi(x_mm=x, y_mm=y, angle_deg=self.angle_deg,
                       label=self.label, conf=self.conf,
                       u_px=self.u_px, v_px=self.v_px)

    @classmethod
    def dari_dict(cls, d):
        """Parse satu objek dari balasan kamera, dengan validasi galak.

        Sengaja galak: lebih baik gagal keras di sini dengan pesan jelas
        daripada robot bergerak ke koordinat hasil salah ketik.
        """
        if not isinstance(d, dict):
            raise KameraError(
                f"tiap objek harus dict, dapat {type(d).__name__}: {d!r}")
        punya_mm = "x_mm" in d and "y_mm" in d
        punya_px = "u_px" in d and "v_px" in d
        if not punya_mm and not punya_px:
            raise KameraError(
                f"objek harus punya x_mm+y_mm ATAU u_px+v_px, tidak ada "
                f"dua-duanya di {d!r}")
        try:
            x = float(d["x_mm"]) if punya_mm else None
            y = float(d["y_mm"]) if punya_mm else None
            u = float(d["u_px"]) if punya_px else None
            v = float(d["v_px"]) if punya_px else None
            sudut = float(d.get("angle_deg") or 0.0)
            conf = float(d["conf"]) if d.get("conf") is not None else 1.0
        except (TypeError, ValueError) as e:
            raise KameraError(f"angka tidak valid di objek {d!r}: {e}") from None
        return cls(x_mm=x, y_mm=y, angle_deg=sudut,
                   label=str(d.get("label") or ""), conf=conf, u_px=u, v_px=v)

    def ke_dict(self):
        return asdict(self)

    def terjangkau(self, z):
        """(bool, alasan) -- apakah titik ini bisa dicapai lengan di tinggi z.

        Pakai rnv3.check_reach() yang merupakan replika persis
        isAllowedPosition() di firmware, jadi jawabannya sama dengan robot.
        """
        if not self.punya_mm:
            raise KameraError(
                f"{self!r} masih dalam piksel -- belum dikonversi. Beri "
                f"`kalibrasi=` ke sumber deteksinya.")
        return rnv3.check_reach(self.x_mm, self.y_mm, z)

    def __str__(self):
        nama = self.label or "?"
        posisi = (f"({self.x_mm:.1f}, {self.y_mm:.1f})" if self.punya_mm
                  else f"px({self.u_px:.0f}, {self.v_px:.0f})")
        return f"{nama}@{posisi} sudut={self.angle_deg:.0f} conf={self.conf:.2f}"


def yang_terjangkau(deteksi, z):
    """Saring daftar Deteksi, sisakan yang benar-benar bisa diambil di tinggi z.

    Return (bisa, ditolak) dengan ditolak = list (Deteksi, alasan).
    """
    bisa, ditolak = [], []
    for d in deteksi:
        ok, alasan = d.terjangkau(z)
        (bisa if ok else ditolak).append(d if ok else (d, alasan))
    return bisa, ditolak


def paling_dekat(deteksi, x=0.0, y=0.0):
    """Deteksi terdekat ke titik acuan. None kalau daftarnya kosong."""
    if not deteksi:
        return None
    return min(deteksi, key=lambda d: (d.x_mm - x) ** 2 + (d.y_mm - y) ** 2)


# ---------------------------------------------------------------------------
# SISI ORKESTRATOR -- sumber deteksi
# ---------------------------------------------------------------------------
class SumberDeteksi:
    """Antarmuka yang dipakai orkestrator. Implementasi apa pun boleh."""

    def deteksi(self):
        """Return list[Deteksi]. Daftar kosong = tidak ada benda (normal)."""
        raise NotImplementedError

    def ping(self):
        """True kalau sumber hidup. Tidak boleh raise -- ini buat pre-flight.

        Default True: sumber yang jalan di proses yang sama memang selalu
        hidup. Yang lewat jaringan menimpa ini.
        """
        return True

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _baca_baris(sock):
    """Baca satu baris berakhir '\n' dari socket. Raise KameraError kalau gagal."""
    buf = bytearray()
    while True:
        try:
            potong = sock.recv(4096)
        except socket.timeout:
            raise KameraError("kamera tidak membalas sampai timeout") from None
        if not potong:
            raise KameraError("koneksi ditutup kamera sebelum balasan lengkap")
        buf.extend(potong)
        if b"\n" in buf:
            return bytes(buf).split(b"\n", 1)[0]
        if len(buf) > BATAS_BALASAN:
            raise KameraError("balasan kamera kepanjangan tanpa newline")


class SumberTCP(SumberDeteksi):
    """Client ke proses kamera sungguhan.

    Sengaja connect-per-permintaan, bukan koneksi menetap: lajunya rendah
    (satu kali per siklus ambil) sementara connect ke localhost cuma ~1 ms,
    dan ini membuat kamera yang restart di tengah jalan tidak merusak apa pun
    -- tidak perlu logika reconnect sama sekali.
    """

    def __init__(self, host="127.0.0.1", port=PORT_DEFAULT,
                 timeout=TIMEOUT_DEFAULT, kalibrasi=None):
        self.host, self.port, self.timeout = host, port, timeout
        # Kalau kamera cuma mengirim piksel, konversi dilakukan DI SINI --
        # di lapisan adapter. Dengan begitu jalur.py selalu menerima mm dan
        # tidak perlu tahu kamera aslinya bicara dalam satuan apa.
        self.kalibrasi = kalibrasi

    def _tanya(self, pesan):
        try:
            with socket.create_connection((self.host, self.port),
                                          timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(json.dumps(pesan).encode("utf-8") + b"\n")
                baris = _baca_baris(sock)
        except KameraError:
            raise
        except OSError as e:
            raise KameraError(
                f"tidak bisa menghubungi kamera di {self.host}:{self.port} -- {e}"
            ) from None

        try:
            jawab = json.loads(baris.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            raise KameraError(f"balasan kamera bukan JSON valid: {e}") from None
        if not isinstance(jawab, dict):
            raise KameraError(f"balasan kamera harus objek JSON, dapat {jawab!r}")
        if not jawab.get("ok"):
            raise KameraError(f"kamera lapor gagal: {jawab.get('error', '(tanpa alasan)')}")
        return jawab

    def deteksi(self):
        jawab = self._tanya({"cmd": "detect"})
        objek = jawab.get("objects", [])
        if not isinstance(objek, list):
            raise KameraError(f"'objects' harus list, dapat {type(objek).__name__}")
        hasil = []
        for o in objek:
            d = Deteksi.dari_dict(o)
            if not d.punya_mm:
                if self.kalibrasi is None:
                    raise KameraError(
                        "kamera mengirim piksel tapi belum ada kalibrasi. "
                        "Jalankan 'python3 kalibrasi.py --sesi' lalu berikan "
                        "hasilnya ke SumberTCP(kalibrasi=...).")
                d = d.ke_mm(self.kalibrasi)
            hasil.append(d)
        return hasil

    def ping(self):
        """True kalau kamera hidup. Tidak pernah raise -- buat pre-flight."""
        try:
            self._tanya({"cmd": "ping"})
            return True
        except KameraError:
            return False


class SumberPalsu(SumberDeteksi):
    """Kamera tiruan untuk tes tanpa hardware.

    Mengeluarkan koordinat acak yang DIJAMIN terjangkau, plus bisa disuruh
    sesekali mengembalikan daftar kosong atau titik di luar jangkauan supaya
    cabang penanganan error di orkestrator ikut teruji.
    """

    def __init__(self, z_pick, x=(-150.0, 150.0), y=(240.0, 280.0),
                 peluang_kosong=0.0, peluang_jauh=0.0, seed=None, label=None):
        self.z_pick = z_pick
        self.x, self.y = x, y
        # Label diacak dari daftar ini, supaya cabang "botol" dan "koin" di
        # orkestrator dua-duanya kena saat simulasi.
        self.label = list(label) if label else ["benda-palsu"]
        self.peluang_kosong = peluang_kosong
        self.peluang_jauh = peluang_jauh
        self.rng = random.Random(seed)
        self.jumlah_diminta = 0

    def deteksi(self):
        self.jumlah_diminta += 1
        if self.rng.random() < self.peluang_kosong:
            return []
        if self.rng.random() < self.peluang_jauh:
            # Jauh di luar R_MAX -- orkestrator harus menolak ini, bukan crash.
            return [Deteksi(x_mm=0.0, y_mm=rnv3.R_MAX + 200.0,
                            label=self.rng.choice(self.label), conf=0.5)]
        # Tolak-dan-ulang sampai dapat titik yang beneran valid: rentang x/y
        # berbentuk kotak sedangkan jangkauan robot berbentuk cincin, jadi
        # pojok-pojok kotak itu tidak selalu bisa dicapai.
        for _ in range(100):
            d = Deteksi(
                x_mm=round(self.rng.uniform(*self.x), 1),
                y_mm=round(self.rng.uniform(*self.y), 1),
                angle_deg=round(self.rng.uniform(-90, 90), 1),
                label=self.rng.choice(self.label),
                conf=round(self.rng.uniform(0.7, 0.99), 2))
            if d.terjangkau(self.z_pick)[0]:
                return [d]
        raise KameraError(
            f"SumberPalsu gagal menemukan titik terjangkau di x={self.x} "
            f"y={self.y} z={self.z_pick} -- rentangnya di luar jangkauan robot")


# ---------------------------------------------------------------------------
# SISI KAMERA -- server tiruan
# Kelompok kamera boleh pakai ini sebagai contoh, atau menulis server sendiri
# dalam bahasa apa pun asal formatnya sama.
# ---------------------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for baris in self.rfile:
            baris = baris.strip()
            if not baris:
                continue
            try:
                minta = json.loads(baris.decode("utf-8", "replace"))
                cmd = minta.get("cmd") if isinstance(minta, dict) else None
            except json.JSONDecodeError as e:
                self._balas({"ok": False, "error": f"JSON tidak valid: {e}"})
                continue

            if cmd == "ping":
                self._balas({"ok": True, "pong": True})
            elif cmd == "detect":
                try:
                    objek = [d.ke_dict() for d in self.server.sumber.deteksi()]
                except Exception as e:                      # noqa: BLE001
                    self._balas({"ok": False, "error": f"{type(e).__name__}: {e}"})
                else:
                    self._balas({"ok": True, "ts": time.time(), "objects": objek})
            else:
                self._balas({"ok": False, "error": f"cmd tidak dikenal: {cmd!r}"})

    def _balas(self, obj):
        self.wfile.write(json.dumps(obj).encode("utf-8") + b"\n")

    def log_message(self, *a):      # bungkam log bawaan
        pass


class ServerKameraPalsu(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, sumber, host="127.0.0.1", port=PORT_DEFAULT):
        super().__init__((host, port), _Handler)
        self.sumber = sumber

    def jalan_di_background(self):
        """Nyalakan di thread terpisah. Return thread-nya."""
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return t

    def __enter__(self):
        self.jalan_di_background()
        return self

    def __exit__(self, *exc):
        self.shutdown()
        self.server_close()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Server kamera TIRUAN -- buat tes orkestrator tanpa kamera.")
    p.add_argument("--port", type=int, default=PORT_DEFAULT)
    p.add_argument("--z-pick", type=float, default=-75.0,
                   help="tinggi ambil, dipakai menyaring titik agar terjangkau")
    p.add_argument("--kosong", type=float, default=0.0,
                   help="peluang 0..1 membalas daftar kosong")
    p.add_argument("--jauh", type=float, default=0.0,
                   help="peluang 0..1 membalas titik di luar jangkauan")
    a = p.parse_args()

    sumber = SumberPalsu(z_pick=a.z_pick, peluang_kosong=a.kosong,
                         peluang_jauh=a.jauh)
    srv = ServerKameraPalsu(sumber, port=a.port)
    print(f"server kamera palsu jalan di 127.0.0.1:{a.port}")
    print('coba: echo \'{"cmd":"detect"}\' | nc 127.0.0.1 %d' % a.port)
    print("Ctrl-C untuk berhenti")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nberhenti")
    finally:
        srv.server_close()
