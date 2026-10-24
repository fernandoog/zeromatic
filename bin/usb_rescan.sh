#!/bin/bash
set -eu
echo "devices:"
ls -la /sys/bus/usb/devices/ || true
echo "---"
# Re-enable root hub
if [[ -f /sys/bus/usb/devices/usb1/authorized ]]; then
  echo 0 > /sys/bus/usb/devices/usb1/authorized || true
  sleep 2
  echo 1 > /sys/bus/usb/devices/usb1/authorized || true
  echo "toggled usb1 root hub"
fi
sleep 3
# Try unbind/bind port 1-1 if present
if [[ -e /sys/bus/usb/devices/1-1 ]]; then
  echo "1-1 exists"
  echo 0 > /sys/bus/usb/devices/1-1/authorized 2>/dev/null || true
  sleep 1
  echo 1 > /sys/bus/usb/devices/1-1/authorized 2>/dev/null || true
fi
sleep 2
echo "=== lsusb ==="
lsusb
echo "=== tty ==="
ls -l /dev/ttyACM* 2>/dev/null || echo none
echo "=== dmesg ==="
dmesg | tail -30
