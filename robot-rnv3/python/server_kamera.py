"""
Server kamera SUNGGUHAN -- sisi kamera dari kontrak_kamera.py.

kontrak_kamera.py baru punya server TIRUAN (ServerKameraPalsu + SumberPalsu).
File ini mengisi sumbernya dengan YOLO milik kelompok kamera (repo opencv-,
folder sortir-yolo) tanpa memakai lapis robot mereka:

  - DIPAKAI dari repo kamera : src.camera.ThreadedCamera, src.detector.Detector
                               dan models/best.pt -- murni "lihat & tunjuk".
  - TIDAK dipakai            : robot_calib.json / coordinate_transform.py
                               (skala tetap 0.35 mm/px), target_robot.json,
                               mode serial sortir_otomatis.py (kirim
                               "PICK:..." yang tidak dikenal firmware RNV3).

Server mengirim PIKSEL (u_px, v_px). Konversi ke mm dikerjakan SumberTCP di
sisi orkestrator memakai homografi kita sendiri (kalibrasi.py), jadi satu
kalibrasi saja yang berlaku:

    terminal 1:  python server_kamera.py --sumber 0
    terminal 2:  python jalur.py --kalibrasi kalibrasi.json

Untuk membuat kalibrasi.json (butuh robot + kamera terpasang kaku):

    terminal 1:  python server_kamera.py --klik --sumber 0
                 (klik ujung gripper di jendela -> piksel tercetak)
    terminal 2:  python kalibrasi.py --sesi --port COM5
                 (ketik piksel yang tercetak tiap kali diminta)

Resolusi kamera (--lebar/--tinggi) WAJIB sama saat kalibrasi dan saat jalan:
homografi berlaku untuk piksel di resolusi tempat ia diukur.

Butuh Python yang punya ultralytics + opencv (requirements repo kamera).
"""

import argparse
import os
import sys
import threading
import time

import kontrak_kamera

# Label yang boleh diserahkan ke orkestrator. HARUS sama dengan kunci
# jalur.JENIS -- cek.py memeriksanya. Model kamera juga kenal 'charger', tapi
# jalur.py belum punya tinggi & bak untuknya: label tak dikenal jatuh ke
# JENIS_DEFAULT ('koin', tinggi 2 mm), artinya gripper turun menabrak charger
# setinggi ~28 mm. Jadi disaring di sini sampai jalur.JENIS ditambah.
LABEL_DIKENAL = ("tutup_botol", "koin")

REPO_KAMERA_DEFAULT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "opencv-", "sortir-yolo"))


class SumberYOLO(kontrak_kamera.SumberDeteksi):
    """Frame terbaru kamera -> YOLO -> list[Deteksi] dalam piksel.

    `kamera` cukup punya read() -> (ok, frame); `detektor` cukup punya
    detect(frame, apply_smart_filter=...) -> objek ber-atribut class_name,
    confidence, center. Duck-typing supaya cek.py bisa menguji tanpa
    ultralytics maupun kamera.
    """

    def __init__(self, kamera, detektor, label=LABEL_DIKENAL, cetak=print):
        self.kamera = kamera
        self.detektor = detektor
        self.label = set(label) if label else None   # None = loloskan semua
        self.cetak = cetak
        # ThreadingTCPServer melayani tiap koneksi di thread sendiri; model
        # YOLO tidak aman dipanggil dua thread sekaligus.
        self._kunci = threading.Lock()

    def deteksi(self):
        with self._kunci:
            ok, frame = self.kamera.read()
            if not ok or frame is None:
                raise kontrak_kamera.KameraError(
                    "kamera tidak memberi frame (kabel lepas / dipakai program lain?)")
            mentah = self.detektor.detect(frame, apply_smart_filter=True)

        hasil = []
        for d in mentah:
            if self.label is not None and d.class_name not in self.label:
                self.cetak(f"  disaring: '{d.class_name}' conf={d.confidence:.2f} "
                           f"-- belum ada di jalur.JENIS")
                continue
            u, v = d.center
            hasil.append(kontrak_kamera.Deteksi(
                u_px=float(u), v_px=float(v),
                label=d.class_name, conf=float(d.confidence)))
        return hasil


def buka_yolo(repo=REPO_KAMERA_DEFAULT, sumber="0", lebar=1280, tinggi=720,
              bobot=None, conf=0.25, imgsz=1280, roi=None, pakai_model=True):
    """Buka kamera (+ model YOLO kalau pakai_model). Return (kamera, detektor)."""
    if not os.path.isdir(os.path.join(repo, "src")):
        raise SystemExit(f"repo kamera tidak ditemukan di {repo} -- pakai --repo")
    sys.path.insert(0, repo)
    from src.camera import ThreadedCamera, parse_source

    detektor = None
    if pakai_model:
        from src.detector import Detector
        detektor = Detector(
            weights=bobot or os.path.join(repo, "models", "best.pt"),
            conf=conf, imgsz=imgsz, smart_filter=True,
            # Tanpa tracking: tiap permintaan berdiri sendiri, jarak antar
            # permintaan satu siklus robot -- ID pelacak tak bermakna.
            track=False, roi=roi)
    kamera = ThreadedCamera(parse_source(sumber), width=lebar, height=tinggi).open()
    ok, frame = kamera.read()
    if not ok or frame is None:
        kamera.release()
        raise SystemExit(f"kamera '{sumber}' tidak memberi frame")
    print(f"kamera '{sumber}' terbuka, frame {frame.shape[1]}x{frame.shape[0]}")
    if detektor is not None and roi is None:
        # ROI yang digambar dengan drag mouse di detect.py repo kamera -- satu
        # sumber, jadi area aktif sama persis dengan yang dilihat operator.
        from src.config import DEFAULT_ROI_PATH
        from src.utils import load_roi
        detektor.roi = load_roi(frame.shape[1], frame.shape[0], DEFAULT_ROI_PATH)
        if detektor.roi is not None:
            print(f"ROI dari {DEFAULT_ROI_PATH}: {detektor.roi}")
    if detektor is not None:
        print(f"ROI aktif: {detektor.roi or 'tidak ada -- seluruh frame'}")
    return kamera, detektor


def _pemanasan(detektor, kamera):
    """Inferensi pertama YOLO jauh lebih lambat -- habiskan di sini, bukan
    di permintaan pertama orkestrator yang cuma mau menunggu TIMEOUT_DEFAULT."""
    ok, frame = kamera.read()
    for i in range(2):
        t0 = time.time()
        detektor.detect(frame, apply_smart_filter=True)
        lama = time.time() - t0
    print(f"inferensi YOLO ~{lama:.2f} s per frame")
    if lama > 0.7 * kontrak_kamera.TIMEOUT_DEFAULT:
        print(f"PERINGATAN: mendekati batas tunggu orkestrator "
              f"({kontrak_kamera.TIMEOUT_DEFAULT} s). Turunkan --imgsz.")


def mode_klik(kamera):
    """Jendela live; klik kiri mencetak 'u v' untuk diketik ke kalibrasi.py --sesi."""
    import cv2

    titik = []

    def saat_klik(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN:
            titik.append((x, y))
            print(f"{x} {y}", flush=True)

    nama = "klik ujung gripper -- q untuk keluar"
    cv2.namedWindow(nama)
    cv2.setMouseCallback(nama, saat_klik)
    while True:
        ok, frame = kamera.read()
        if ok and frame is not None:
            for x, y in titik[-1:]:
                cv2.drawMarker(frame, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 30, 2)
            cv2.imshow(nama, frame)
        if cv2.waitKey(30) & 0xFF in (ord("q"), 27):
            break
    cv2.destroyAllWindows()


def _roi(teks):
    try:
        x1, y1, x2, y2 = (int(n) for n in teks.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("format ROI: x1,y1,x2,y2") from None
    return (x1, y1, x2, y2)


if __name__ == "__main__":
    # python.exe yang dipanggil dari terminal WSL menulis ke pipe, bukan konsol:
    # tanpa ini output ter-buffer dan program tampak macet padahal jalan.
    sys.stdout.reconfigure(line_buffering=True)
    p = argparse.ArgumentParser(description="Server kamera YOLO sesuai kontrak_kamera.py.")
    p.add_argument("--sumber", default="0", help="index webcam atau URL stream (default 0)")
    p.add_argument("--repo", default=REPO_KAMERA_DEFAULT,
                   help="folder sortir-yolo milik kelompok kamera")
    p.add_argument("--bobot", default=None, help="file .pt (default <repo>/models/best.pt)")
    p.add_argument("--lebar", type=int, default=1280)
    p.add_argument("--tinggi", type=int, default=720)
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--imgsz", type=int, default=1280)
    p.add_argument("--roi", type=_roi, default=None,
                   help="area aktif x1,y1,x2,y2 (piksel). Default: roi.json di repo "
                        "kamera, digambar dengan drag mouse di detect.py")
    p.add_argument("--host", default="127.0.0.1",
                   help="0.0.0.0 kalau orkestrator jalan di laptop lain")
    p.add_argument("--port", type=int, default=kontrak_kamera.PORT_DEFAULT)
    p.add_argument("--semua-label", action="store_true",
                   help="jangan saring label (BAHAYA: label asing diambil sebagai koin)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--klik", action="store_true",
                      help="mode bantu kalibrasi: klik di jendela -> piksel tercetak")
    mode.add_argument("--sekali", action="store_true",
                      help="deteksi satu frame, cetak, keluar (tanpa server)")
    a = p.parse_args()

    kamera, detektor = buka_yolo(a.repo, a.sumber, a.lebar, a.tinggi, a.bobot,
                                 a.conf, a.imgsz, a.roi, pakai_model=not a.klik)
    try:
        if a.klik:
            mode_klik(kamera)
            sys.exit(0)
        _pemanasan(detektor, kamera)
        sumber = SumberYOLO(kamera, detektor,
                            label=None if a.semua_label else LABEL_DIKENAL)
        if a.sekali:
            hasil = sumber.deteksi()
            print(f"{len(hasil)} benda:")
            for d in hasil:
                print(f"  {d}")
            sys.exit(0)
        srv = kontrak_kamera.ServerKameraPalsu(sumber, host=a.host, port=a.port)
        print(f"server kamera jalan di {a.host}:{a.port}  (label: "
              f"{'SEMUA' if a.semua_label else ', '.join(LABEL_DIKENAL)})")
        print("Ctrl-C untuk berhenti")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nberhenti")
        finally:
            srv.server_close()
    finally:
        kamera.release()
