import serial
import time
import sys
from pynput import keyboard

# =========================================================
# GLOBAL FLAG
# =========================================================
stop_requested = False


# =========================================================
# KEYBOARD LISTENER
# q = stop normal
# =========================================================
def on_press(key):
    global stop_requested
    try:
        if key.char.lower() == 'q':
            stop_requested = True
    except AttributeError:
        pass


# =========================================================
# SERIAL CONFIG
# =========================================================
PORT = '/dev/cu.usbserial-1120'   # ganti sesuai port robot
BAUD = 115200
TIMEOUT = 1

try:
    ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
    time.sleep(1)
except Exception as e:
    print(f"Gagal membuka serial: {e}")
    sys.exit()


# =========================================================
# PARAMETER GRID KAYU
# =========================================================
TOTAL_WOOD = 30
COLS = 6   # 6 ke samping (X)
ROWS = 5   # 5 ke belakang (Y)

# ukuran jig dalam mm
JIG_WIDTH = 120    # 12 cm
JIG_LENGTH = 220   # 22 cm
JIG_DEPTH = 4      # 4 mm

# jarak antar jig
GAP = 6            # 6 mm

# pitch antar titik tengah
PITCH_X = JIG_WIDTH + GAP     # 126 mm
PITCH_Y = JIG_LENGTH + GAP    # 226 mm

# titik tengah kayu pertama
X0 = 0
Y0 = 0
Z0 = 0

# tinggi aman
Z_SAFE_PICK = 50
Z_SAFE_PLACE = 80

# feedrate
F_FAST = 300
F_SLOW = 100

# titik tempat taruh kayu setelah diambil
# ganti sesuai kebutuhan kamu
PLACE_X = 800
PLACE_Y = 0
PLACE_Z = 0


# =========================================================
# FUNGSI SERIAL / ROBOT
# =========================================================
def kirim_command(cmd: str):
    cmd_full = cmd + '\r'
    print(f"Kirim: {cmd}")
    ser.write(cmd_full.encode('utf-8'))


def tunggu_selesai():
    while True:
        a = ser.readline()
        if a:
            msg = a.decode("utf-8", errors="ignore").strip()
            print(msg)
            if "ok" in msg.lower():
                break


def gerak(x, y, z, feed=200):
    kirim_command(f"G0 X{x} Y{y} Z{z} F{feed}")
    tunggu_selesai()


def vacuum_on():
    # GANTI command ini sesuai robot/controller kamu
    # contoh:
    # kirim_command("M3")
    # tunggu_selesai()
    print("Vacuum ON")
    time.sleep(0.3)


def vacuum_off():
    # GANTI command ini sesuai robot/controller kamu
    # contoh:
    # kirim_command("M5")
    # tunggu_selesai()
    print("Vacuum OFF")
    time.sleep(0.3)


def home_robot():
    print("\nHome robot...")
    kirim_command("G28")
    tunggu_selesai()


def stop_robot():
    print("\nStop diterima...")
    print("Robot menuju posisi aman...\n")

    try:
        gerak(0, 140, 31, 200)
    except Exception as e:
        print(f"Gagal ke posisi aman: {e}")

    print("Robot Stop\n")
    time.sleep(1)

    print("Stepper akan off dalam 3 detik\n")
    time.sleep(3)

    try:
        kirim_command("M18")
        tunggu_selesai()
    except Exception as e:
        print(f"Gagal M18: {e}")

    try:
        ser.close()
    except:
        pass

    print("Serial ditutup")
    sys.exit()


# =========================================================
# HITUNG KOORDINAT KAYU
# =========================================================
def get_wood_pick_coordinate(wood_number: int):
    """
    Return titik tengah kayu berdasarkan nomor 1-30

    Urutan:
    1   2   3   4   5   6
    7   8   9  10  11  12
    13 14  15  16  17  18
    19 20  21  22  23  24
    25 26  27  28  29  30
    """
    if wood_number < 1 or wood_number > TOTAL_WOOD:
        raise ValueError(f"wood_number harus 1 sampai {TOTAL_WOOD}")

    index = wood_number - 1
    row = index // COLS
    col = index % COLS

    x = X0 + (col * PITCH_X)
    y = Y0 + (row * PITCH_Y)
    z = Z0

    return x, y, z


# =========================================================
# FUNGSI AMBIL KAYU
# =========================================================
def pickup_kayu(wood_number: int):
    if stop_requested:
        stop_robot()

    x, y, z = get_wood_pick_coordinate(wood_number)

    print("")
    print(f"=== AMBIL KAYU {wood_number} ===")
    print(f"Koordinat pick -> X={x}, Y={y}, Z={z}")
    print("")

    # 1. ke atas kayu
    gerak(x, y, Z_SAFE_PICK, F_FAST)

    if stop_requested:
        stop_robot()

    # 2. turun ke titik vacuum
    gerak(x, y, z, F_SLOW)

    if stop_requested:
        stop_robot()

    # 3. vacuum ON
    vacuum_on()

    # 4. naik lagi
    gerak(x, y, Z_SAFE_PICK, F_FAST)


# =========================================================
# FUNGSI TARUH KAYU
# =========================================================
def place_kayu(x=PLACE_X, y=PLACE_Y, z=PLACE_Z):
    if stop_requested:
        stop_robot()

    print("")
    print(f"=== TARUH KAYU -> X={x}, Y={y}, Z={z} ===")
    print("")

    # 1. ke atas titik taruh
    gerak(x, y, Z_SAFE_PLACE, F_FAST)

    if stop_requested:
        stop_robot()

    # 2. turun ke titik taruh
    gerak(x, y, z, F_SLOW)

    if stop_requested:
        stop_robot()

    # 3. vacuum OFF
    vacuum_off()

    # 4. naik lagi
    gerak(x, y, Z_SAFE_PLACE, F_FAST)


# =========================================================
# FUNGSI PROSES SATU KAYU
# =========================================================
def process_one_wood(wood_number: int):
    pickup_kayu(wood_number)
    place_kayu()


# =========================================================
# MAIN
# =========================================================
def main():
    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    home_robot()

    print("")
    mulai = input("Tekan y untuk mulai: ").strip().lower()
    print("")

    if mulai != 'y':
        print("Program dibatalkan")
        ser.close()
        listener.stop()
        sys.exit()

    print("Robot mulai bekerja")
    print("Tekan q untuk stop normal\n")

    try:
        for wood_number in range(1, TOTAL_WOOD + 1):
            if stop_requested:
                stop_robot()

            process_one_wood(wood_number)

        print("\nSemua kayu selesai diproses\n")

        # kembali ke posisi aman
        gerak(0, 140, 31, 200)

        # stepper off
        kirim_command("M18")
        tunggu_selesai()

        ser.close()
        listener.stop()
        print("Program selesai")

    except KeyboardInterrupt:
        print("\nProgram dihentikan manual")
        try:
            ser.close()
        except:
            pass
        listener.stop()
        sys.exit()

    except Exception as e:
        print(f"\nTerjadi error: {e}")
        try:
            ser.close()
        except:
            pass
        listener.stop()
        sys.exit()


if __name__ == "__main__":
    main()
