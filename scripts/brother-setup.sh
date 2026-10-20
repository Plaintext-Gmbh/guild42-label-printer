#!/bin/bash
# brother-setup.sh
# Prepares the Brother QL-820NWBc USB connection on boot.
# Called by systemd as ExecStartPre before the Flask app starts.
#
# The ipp-usb daemon grabs the USB device on connect and prevents
# direct kernel access via /dev/usb/lp0. This script stops ipp-usb,
# reloads the usblp kernel module, and waits for the device node.

set -e

# ipp-usb is a static unit that udev starts via SYSTEMD_WANTS (71-ipp-usb.rules) whenever the
# printer appears - "disable" does not stop that, and a plain "stop" here can lose the race: on
# 20.10.2026 it came back 4 s later, took the interface, and /dev/usb/lp0 was gone (503 in both
# modes). Masking blocks every start path.
echo "[brother-setup] Masking and stopping ipp-usb..."
systemctl mask --now ipp-usb.service 2>/dev/null || true

echo "[brother-setup] Reloading usblp kernel module..."
modprobe -r usblp 2>/dev/null || true
sleep 2
modprobe usblp

echo "[brother-setup] Waiting for /dev/usb/lp0..."
sleep 2

if [ -e /dev/usb/lp0 ]; then
    echo "[brother-setup] Device ready at /dev/usb/lp0."
else
    echo "[brother-setup] WARNING: /dev/usb/lp0 not found. Printer may not be connected."
fi
