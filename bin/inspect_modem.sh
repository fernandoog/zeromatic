#!/bin/bash
set -eu
systemctl stop buzonera 2>/dev/null || true
sleep 1
echo "=== devices ==="
ls -la /dev/ttyACM* 2>/dev/null || true
ls -la /dev/serial/by-id/ 2>/dev/null || true
echo "=== lsusb -v (short) ==="
lsusb -v -d 0572:1340 2>/dev/null | head -100 || true
echo "=== stty attempt ==="
stty -F /dev/ttyACM0 115200 cs8 -cstopb -parenb crtscts || true
stty -F /dev/ttyACM0 -a || true
# read in background
timeout 3 cat /dev/ttyACM0 > /tmp/modem_out.bin &
CPID=$!
sleep 0.3
printf 'AT\r' > /dev/ttyACM0 || true
sleep 0.5
printf 'ATI3\r' > /dev/ttyACM0 || true
sleep 1.5
wait $CPID 2>/dev/null || true
echo "=== raw out ==="
xxd /tmp/modem_out.bin | head -20 || true
wc -c /tmp/modem_out.bin || true
