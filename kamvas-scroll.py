#!/usr/bin/env python3
"""
kamvas-scroll.py
================
Translates the Huion Kamvas Pro 16 scroll strip into standard mouse wheel
scroll events on Linux, working alongside OpenTabletDriver.

Background
----------
The Kamvas Pro 16 exposes two HID report types on its primary hidraw interface:
  - 0xe0 reports: express key button presses  (handled natively by OTD)
  - 0xf0 reports: scroll strip position       (NOT handled by OTD as of 2026)

This script reads the raw 0xf0 HID reports directly from /dev/hidraw*, detects
up/down strip movement from the changing position byte, and emits REL_WHEEL
events via a uinput virtual device. The desktop sees this as a normal scroll
wheel. OTD continues to run alongside without conflict — multiple processes can
read the same hidraw device simultaneously.

HID report structure (12 bytes, confirmed by hidraw capture on Fedora 44):
  byte  0: 0x08          (constant)
  byte  1: report type   (0xf0 = scroll strip, 0xe0 = buttons)
  byte  2: 0x01          (constant)
  byte  3: 0x01          (constant)
  byte  4: 0x00          (constant for scroll reports)
  byte  5: position      (0–7, increments/decrements as finger slides)
  bytes 6–9: 0x00        (unused)
  bytes 10–11: 0xe0 0xf8 (constant footer)

Requirements
------------
  pip install python-uinput

Permissions (no sudo needed after setup)
-----------------------------------------
  1. Add yourself to the 'input' group:
         sudo usermod -aG input $USER
  2. Add a udev rule so your user can read the Huion hidraw device:
         echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="256c", ATTRS{idProduct}=="006d", MODE="0660", GROUP="input"' \
           | sudo tee /etc/udev/rules.d/70-huion-kamvas.rules
         sudo udevadm control --reload-rules && sudo udevadm trigger
  3. Also allow uinput access (needed to create the virtual scroll device):
         echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' \
           | sudo tee /etc/udev/rules.d/70-uinput.rules
         sudo udevadm control --reload-rules && sudo udevadm trigger
  4. Log out and back in for group membership to take effect.

Usage
-----
  Test manually:
      python3 kamvas-scroll.py

  Run as a persistent background service (recommended):
      See kamvas-scroll.service

Author: Kim Lang + Claude (Anthropic), 2026
Shared freely — if this helped you, pay it forward on the OTD forums!
"""

import os
import sys
import time
import uinput

# ── Configuration ────────────────────────────────────────────────────────────

# Report ID byte (byte 1) for scroll strip data, confirmed by hidraw capture.
SCROLL_REPORT_ID = 0xF0

# Full HID report length in bytes (confirmed by capture — NOT 8, it's 12).
REPORT_SIZE = 12

# Index of the position byte within the report (0-indexed).
POSITION_BYTE = 5

# How many scroll 'clicks' to emit per strip position step.
# Increase if scrolling feels too slow; decrease if too fast.
SCROLL_SPEED = 1

# Minimum seconds between scroll events (rate limiter).
# 0.01 = 10ms feels smooth without flooding applications.
SCROLL_INTERVAL = 0.01

# ── Device detection ─────────────────────────────────────────────────────────

def find_hidraw_device():
    """
    Auto-detect the Kamvas Pro 16 hidraw device by scanning /sys/class/hidraw.

    The Kamvas Pro 16 exposes two HID interfaces (input0 and input1).
    input0 is the primary interface carrying both pen data and scroll strip
    reports — this is the one we want.

    Huion vendor ID : 0x256c
    Kamvas Pro 16 product ID: 0x006d
    """
    hidraw_base = "/sys/class/hidraw"
    for name in sorted(os.listdir(hidraw_base)):
        uevent_path = os.path.join(hidraw_base, name, "device", "uevent")
        try:
            with open(uevent_path) as f:
                content = f.read()
            # Match Huion vendor + Kamvas product ID
            if "0000256C" in content and "0000006D" in content:
                # Only grab input0 — that's the interface with scroll data
                hid_phys = [l for l in content.splitlines() if "HID_PHYS" in l]
                if hid_phys and "input0" in hid_phys[0]:
                    device_path = f"/dev/{name}"
                    print(f"[kamvas-scroll] Found Kamvas Pro 16 on {device_path}")
                    return device_path
        except (FileNotFoundError, PermissionError):
            continue

    print("[kamvas-scroll] WARNING: Could not auto-detect Kamvas hidraw device.")
    print("  Check that the tablet is connected and OTD is running.")
    return None


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    device_path = find_hidraw_device()

    if device_path is None:
        sys.exit(1)

    if not os.access(device_path, os.R_OK):
        print(f"[kamvas-scroll] ERROR: No read permission on {device_path}")
        print("  Have you set up the udev rules and added yourself to 'input'?")
        print("  See the README or the docstring at the top of this script.")
        sys.exit(1)

    # Check uinput is accessible (needed to create the virtual scroll device)
    if not os.access("/dev/uinput", os.W_OK):
        print("[kamvas-scroll] ERROR: No write permission on /dev/uinput")
        print("  Have you set up the uinput udev rule?")
        print("  See the README or the docstring at the top of this script.")
        sys.exit(1)

    # Create a virtual input device that emits vertical scroll wheel events.
    # The desktop compositor sees this as a standard mouse scroll wheel.
    print("[kamvas-scroll] Creating virtual scroll wheel via uinput...")
    device = uinput.Device(
        [uinput.REL_WHEEL, uinput.REL_HWHEEL],
        name="Kamvas Pro 16 Scroll Strip",
        vendor=0x256c,
        product=0x006d,
    )

    # Brief pause to let uinput register the new device before we start
    time.sleep(0.5)
    print(f"[kamvas-scroll] Listening on {device_path} — scroll strip active!")

    last_position = None  # Last known strip position (0–7)
    last_event_time = 0   # Monotonic timestamp of last emitted scroll event

    try:
        with open(device_path, "rb") as hid:
            while True:
                # Blocking read — waits until the tablet sends a report
                report = hid.read(REPORT_SIZE)

                if len(report) < REPORT_SIZE:
                    # Partial read (shouldn't normally happen) — skip
                    continue

                # Only process scroll strip reports (byte 1 == 0xf0).
                # Button and pen reports (0xe0, 0x0a etc) are handled by OTD.
                if report[1] != SCROLL_REPORT_ID:
                    continue

                # The position byte counts 0–7 as your finger slides along
                # the strip. It increments going one direction, decrements
                # the other. We compare to the previous position to get direction.
                position = report[POSITION_BYTE]

                if last_position is None:
                    # First report after start — record position, don't scroll yet
                    last_position = position
                    continue

                delta = position - last_position

                if delta == 0:
                    continue

                # Rate limiting: don't flood applications with scroll events
                now = time.monotonic()
                if now - last_event_time < SCROLL_INTERVAL:
                    last_position = position
                    continue

                # Emit scroll event.
                # REL_WHEEL convention: positive = scroll UP, negative = scroll DOWN.
                # Increasing strip position (finger moving one way) = scroll DOWN,
                # so we negate. Swap the sign here if your strip feels backwards.
                scroll_direction = 1 if delta < 0 else -1
                device.emit(uinput.REL_WHEEL, scroll_direction * SCROLL_SPEED)

                last_position = position
                last_event_time = now

    except KeyboardInterrupt:
        print("\n[kamvas-scroll] Stopped.")
    except PermissionError:
        print(f"[kamvas-scroll] ERROR: Permission denied on {device_path}")
        print("  See the README for udev and group setup instructions.")
        sys.exit(1)
    except OSError as e:
        print(f"[kamvas-scroll] ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
