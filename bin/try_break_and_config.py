#!/usr/bin/env python3
import os
import time
import serial
import subprocess

subprocess.call(["systemctl", "stop", "zeromatic"])
time.sleep(1)

dev = "/sys/bus/usb/devices/1-1"
print("current config:", open(f"{dev}/bConfigurationValue").read().strip())
print("num configs:", open(f"{dev}/bNumConfigurations").read().strip())

# Try config 1 explicitly, then 2
for cfg in ("1", "2", "1"):
    try:
        with open(f"{dev}/bConfigurationValue", "w") as f:
            f.write(cfg)
        print(f"set config {cfg}")
        time.sleep(2)
        print("tty:", subprocess.getoutput("ls -l /dev/ttyACM*"))
    except Exception as e:
        print(f"config {cfg} fail:", e)

time.sleep(2)
if not os.path.exists("/dev/ttyACM0"):
    print("NO ttyACM0")
    raise SystemExit(1)

ser = serial.Serial("/dev/ttyACM0", 115200, timeout=0.5, rtscts=True)
time.sleep(1)
print("send_break")
try:
    ser.send_break(0.3)
except Exception as e:
    print("break err", e)
time.sleep(0.5)
ser.reset_input_buffer()
for i in range(5):
    ser.write(b"AT\r")
    ser.flush()
    time.sleep(0.6)
    r = ser.read(256)
    print(f"try{i} -> {r!r}")
    if r:
        break
ser.write(b"ATI3\r")
ser.flush()
time.sleep(1)
print("ATI3 ->", ser.read(256))
ser.close()
