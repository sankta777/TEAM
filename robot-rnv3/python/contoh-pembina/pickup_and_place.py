import sys
import serial
import time
from PyQt5 import QtWidgets

# =========================
# SERIAL
# =========================
PORT = "/dev/cu.usbserial-1120"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.5)
time.sleep(2)

# =========================
# WORKSPACE LIMIT
# =========================
X_MIN, X_MAX = -260, 260
Y_MIN, Y_MAX = 150, 340
Z_MIN, Z_MAX = -100, 100
E_MIN, E_MAX = 0, 300

# =========================
# GRID CONFIG
# =========================
COLS = 6
ROWS = 5

WOOD_W = 50
WOOD_H = 100
GAP = 20

PITCH_X = WOOD_W + GAP   # 120
PITCH_Y = WOOD_H + GAP   # 220

X_START = -250
Y_START = 340

# =========================
# AXIS PARAMETER
# =========================
Z_SAFE = 100
Z_PICK = -75

E_SAFE = 0
E_STEP = 40   # jarak antar kolom di rail

F_FAST = 100
F_MOVE = 80

# =========================
# SEND (STABIL)
# =========================
def send(cmd, retry=3):
    for attempt in range(retry):
        print(f"Kirim: {cmd} (try {attempt+1})")
        ser.write((cmd + "\r\n").encode())
        ser.flush()

        start = time.time()
        buffer = ""

        while time.time() - start < 5:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting).decode(errors="ignore")
                buffer += data

                # print semua response
                for line in buffer.split("\n"):
                    line = line.strip()
                    if line:
                        print("Robot:", line)

                    if "ok" in line.lower():
                        time.sleep(0.03)  # delay kecil biar robot tidak overload
                        return True

        print("⚠️ Retry...")

    print("❌ Gagal kirim command:", cmd)
    return False


# =========================
# VALIDASI
# =========================
def validate(x, y, z=None, e=None):
    if x < X_MIN or x > X_MAX:
        raise ValueError(f"X {x} out of range")

    if y < Y_MIN or y > Y_MAX:
        raise ValueError(f"Y {y} out of range")

    if z is not None:
        if z < Z_MIN or z > Z_MAX:
            raise ValueError(f"Z {z} out of range")

    if e is not None:
        if e < E_MIN or e > E_MAX:
            raise ValueError(f"E {e} out of range")


# =========================
# HITUNG POSISI
# =========================
def get_position(n):
    if n < 1 or n > ROWS * COLS:
        raise ValueError("Nomor kayu harus 1 - 30")

    index = n - 1

    row = index // COLS
    col = index % COLS

    x = X_START + col * PITCH_X
    y = Y_START
    e = E_SAFE + row * E_STEP

    validate(x, y, Z_SAFE, e)

    return x, y, e


# =========================
# PICK FUNCTION
# =========================
def pick(n):
    x, y, e = get_position(n)

    print(f"Pick #{n} → X:{x}, Y:{y}, E:{e}")

    # 1. Naik dulu
    send(f"G0 Z{Z_SAFE} F{F_FAST}")

    # 2. Gerakkan rail
    send(f"G0 E{e} F{F_FAST}")

    # 3. Gerak XY
    send(f"G0 X{x} Y{y} F{F_FAST}")

    # 4. Turun ambil
    send(f"G0 Z{Z_PICK} F{F_MOVE}")

    print("Vacuum ON")
    time.sleep(1)

    # 5. Naik lagi
    send(f"G0 Z{Z_SAFE} F{F_FAST}")

    # 6. Kembali rail (optional)
    send(f"G0 E{E_SAFE} F{F_FAST}")


# =========================
# UI
# =========================
class App(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Robot Pick & Place + Rail")
        self.setGeometry(300, 300, 320, 200)

        layout = QtWidgets.QVBoxLayout()

        self.label = QtWidgets.QLabel("Nomor kayu (1 - 30)")
        layout.addWidget(self.label)

        self.input = QtWidgets.QLineEdit()
        layout.addWidget(self.input)

        self.btn = QtWidgets.QPushButton("PICK")
        self.btn.clicked.connect(self.run_pick)
        layout.addWidget(self.btn)

        self.setLayout(layout)

    def run_pick(self):
        try:
            n = int(self.input.text())

            if n < 1 or n > 30:
                raise ValueError("Nomor harus 1 - 30")

            pick(n)

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "ERROR", str(e))


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    ser.write(b"G28\r\n")  # homing
    time.sleep(5)

    ser.write(b"G90\r\n")  # absolute mode

    app = QtWidgets.QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec_())