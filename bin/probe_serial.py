#!/usr/bin/env python3
import serial
import time
import subprocess

subprocess.call(["systemctl", "stop", "zeromatic"])
time.sleep(2)
subprocess.call(["pkill", "-9", "-f", "zeromatic_daemon"])
time.sleep(1)

print("fuser:", subprocess.getoutput("fuser -v /dev/ttyACM0 2>&1"))
print("dmesg:", subprocess.getoutput("dmesg | grep -i ttyACM | tail -5"))

for rtscts in (True, False):
    for baud in (115200, 57600, 9600):
        print(f"\n=== baud={baud} rtscts={rtscts} ===")
        try:
            ser = serial.Serial("/dev/ttyACM0", baud, timeout=0.4, rtscts=rtscts)
        except Exception as e:
            print("open fail", e)
            continue
        time.sleep(1.0)
        # toggle DTR/RTS
        try:
            ser.dtr = False
            ser.rts = False
            time.sleep(0.2)
            ser.dtr = True
            ser.rts = True
            time.sleep(0.5)
        except Exception as e:
            print("dtr toggle", e)
        ser.reset_input_buffer()
        for c in [b"AT\r", b"AT\r\n", b"ATI3\r"]:
            ser.write(c)
            ser.flush()
            time.sleep(0.8)
            r = ser.read(512)
            print(f"send {c!r} -> {r!r}")
        ser.close()
        if any(True for _ in [0]):  # keep trying all
            pass

print("\ndone")
