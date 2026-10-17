import serial
import time
import math

# =========================
# SERIAL SETUP
# =========================
PORT = "/dev/cu.usbserial-1120"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.5)
time.sleep(2)


# =========================
# SEND FUNCTION (SUPER STABLE)
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
# PARAMETER
# =========================
CENTER_X = 0
CENTER_Y = 220
RADIUS = 35  # diameter 70mm

Z_SAFE = -80
Z_DRAW = -98

F_FAST = 100
F_DRAW = 80

SEGMENTS = 150  # lebih kecil = lebih stabil


# =========================
# GERAK
# =========================
def pen_up():
    send(f"G0 Z{Z_SAFE} F{F_FAST}")


def pen_down():
    send(f"G0 Z{Z_DRAW} F{F_FAST}")


def setup():
    ser.write(b'G28\r')
    print("")
    print("Home sedang dalam proses")
    print("")
    time.sleep(5)
    send("G90")
    pen_up()


# =========================
# GAMBAR LINGKARAN
# =========================
def gambar_lingkaran():
    start_x = CENTER_X + RADIUS
    start_y = CENTER_Y

    pen_up()
    send(f"G0 X{start_x:.2f} Y{start_y:.2f} Z{Z_SAFE} F{F_FAST}")

    pen_down()

    for i in range(SEGMENTS):
        angle = 2 * math.pi * i / SEGMENTS

        x = CENTER_X + RADIUS * math.cos(angle)
        y = CENTER_Y + RADIUS * math.sin(angle)

        send(f"G1 X{x:.2f} Y{y:.2f} Z{Z_DRAW} F{F_DRAW}")
        if not send(f"G0 X{x:.2f} Y{y:.2f} Z{Z_DRAW} F{F_DRAW}"):
            print("Stop karena gagal kirim command")
            return
  
    send(f"G0 X{CENTER_X} Y{CENTER_Y} Z{Z_SAFE} F{F_FAST}")


# =========================
# MAIN
# =========================
def main():
    try:
        setup()
        gambar_lingkaran()
        print("✅ SELESAI")

    except KeyboardInterrupt:
        print("⛔ Stop manual")
        pen_up()

    finally:
        ser.close()
        print("Serial ditutup")


if __name__ == "__main__":
    main()