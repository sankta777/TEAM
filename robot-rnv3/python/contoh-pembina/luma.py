import serial
import time

PORT = "/dev/cu.usbserial-1120"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(2)

# =========================
# PARAMETER
# =========================
Z_UP = -60
Z_DOWN = -85

F_FAST = 200
F_DRAW = 80

SCALE = 20  # ukuran huruf


# =========================
# BASIC MOVE
# =========================
def move(x, y, z, feed):
    cmd = f"G0 X{x:.2f} Y{y:.2f} Z{z} F{feed}\r\n"
    ser.write(cmd.encode())
    time.sleep(0.01)


def pen_up():
    move(0, 0, Z_UP, F_FAST)


def pen_down():
    move(0, 0, Z_DOWN, F_FAST)


def go(x, y):
    cmd = f"G0 X{x:.2f} Y{y:.2f} Z{Z_UP} F{F_FAST}\r\n"
    ser.write(cmd.encode())
    time.sleep(0.05)


# =========================
# DRAW LETTERS
# =========================
def draw_L(x, y):
    go(x, y)
    pen_down()

    move(x, y - SCALE, Z_DOWN, F_DRAW)
    move(x + SCALE, y - SCALE, Z_DOWN, F_DRAW)

    pen_up()


def draw_U(x, y):
    go(x, y)
    pen_down()

    move(x, y - SCALE, Z_DOWN, F_DRAW)
    move(x + SCALE, y - SCALE, Z_DOWN, F_DRAW)
    move(x + SCALE, y, Z_DOWN, F_DRAW)

    pen_up()


def draw_M(x, y):
    go(x, y - SCALE)
    pen_down()

    move(x, y, Z_DOWN, F_DRAW)
    move(x + SCALE/2, y - SCALE/2, Z_DOWN, F_DRAW)
    move(x + SCALE, y, Z_DOWN, F_DRAW)
    move(x + SCALE, y - SCALE, Z_DOWN, F_DRAW)

    pen_up()


def draw_A(x, y):
    go(x, y - SCALE)
    pen_down()

    move(x + SCALE/2, y, Z_DOWN, F_DRAW)
    move(x + SCALE, y - SCALE, Z_DOWN, F_DRAW)

    pen_up()

    # garis tengah
    go(x + SCALE*0.25, y - SCALE*0.5)
    pen_down()
    move(x + SCALE*0.75, y - SCALE*0.5, Z_DOWN, F_DRAW)
    pen_up()


# =========================
# MAIN
# =========================
def main():
    # setup
    ser.write(b"G28\r\n")  # absolute
    time.sleep(0.5)

    start_x = -60
    start_y = 250
    spacing = 30

    draw_L(start_x, start_y)
    draw_U(start_x + spacing, start_y)
    draw_M(start_x + spacing*2, start_y)
    draw_A(start_x + spacing*3, start_y)

    print("✅ LUMA selesai")
    ser.close()


if __name__ == "__main__":
    main()