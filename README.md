# Kamvas Pro 16 Scroll Strip — Linux / OpenTabletDriver

Enables the touch scroll strip on the **Huion Kamvas Pro 16** under Linux when using [OpenTabletDriver](https://opentabletdriver.net).

As of mid-2026, OTD does not expose the Kamvas Pro 16's scroll strip as a bindable input. This script fills that gap by reading the raw HID reports directly and emitting standard mouse wheel scroll events via `uinput`. It runs alongside OTD without conflict — both can read the same hidraw device simultaneously.

---

## How it works

The Kamvas Pro 16 sends HID reports on its primary interface (`/dev/hidraw*`). There are two report types:

| Byte 1 | Meaning |
|--------|---------|
| `0xe0` | Express key button press — handled by OTD |
| `0xf0` | Scroll strip position — **ignored by OTD**, handled by this script |

Each `0xf0` report is 12 bytes. Byte 5 contains the strip position (0–7), which increments or decrements as your finger slides. This script watches for position changes and emits `REL_WHEEL` events via a `uinput` virtual device, which the desktop compositor sees as a standard scroll wheel.

---

## Requirements

- Linux with uinput support (standard on most distros)
- Python 3.8+
- [python-uinput](https://github.com/tuomasjjrasanen/python-uinput)
- OpenTabletDriver (for pen and button support — this script only handles the scroll strip)

Install the Python dependency:

```bash
pip install python-uinput
```

---

## One-time permission setup (no sudo needed after this)

### 1. Add yourself to the `input` group

```bash
sudo usermod -aG input $USER
```

### 2. Create a udev rule for the Kamvas hidraw device

```bash
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="256c", ATTRS{idProduct}=="006d", MODE="0660", GROUP="input"' \
  | sudo tee /etc/udev/rules.d/70-huion-kamvas.rules
```

### 3. Create a udev rule for uinput (needed to create the virtual scroll device)

```bash
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' \
  | sudo tee /etc/udev/rules.d/70-uinput.rules
```

### 4. Reload udev rules and log out/in

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then **log out and back in** for the group membership to take effect.

---

## Test it manually first

```bash
python3 kamvas-scroll.py
```

You should see:

```
[kamvas-scroll] Found Kamvas Pro 16 on /dev/hidraw1
[kamvas-scroll] Creating virtual scroll wheel via uinput...
[kamvas-scroll] Listening on /dev/hidraw1 — scroll strip active!
```

Touch the scroll strip — it should scroll in any browser or file manager. Press Ctrl+C to stop.

If the scroll direction feels backwards, open `kamvas-scroll.py` and find this line:

```python
scroll_direction = 1 if delta < 0 else -1
```

Swap the `1` and `-1` to reverse direction.

---

## Install as a persistent background service

Once you've confirmed it works, set it up as a systemd user service so it starts automatically with your session.

```bash
# Copy the script
mkdir -p ~/.local/bin
cp kamvas-scroll.py ~/.local/bin/kamvas-scroll.py
chmod +x ~/.local/bin/kamvas-scroll.py

# Copy the service file
mkdir -p ~/.config/systemd/user
cp kamvas-scroll.service ~/.config/systemd/user/

# Enable and start
systemctl --user daemon-reload
systemctl --user enable --now kamvas-scroll

# Confirm it's running
systemctl --user status kamvas-scroll
```

To watch live logs:

```bash
journalctl --user -u kamvas-scroll -f
```

---

## Tuning

Inside `kamvas-scroll.py`, two constants at the top control feel:

| Constant | Default | Effect |
|----------|---------|--------|
| `SCROLL_SPEED` | `1` | Scroll clicks emitted per strip step. Increase to scroll faster. |
| `SCROLL_INTERVAL` | `0.01` | Minimum seconds between events. Decrease for more responsiveness. |

---

## Finding your hidraw device

The script auto-detects the Kamvas by scanning `/sys/class/hidraw` for Huion's vendor/product IDs (`256c:006d`). If auto-detection fails, you can verify manually:

```bash
for dev in /sys/class/hidraw/hidraw*; do
  echo "$dev:"; cat "$dev/device/uevent" | grep HID; echo "---"
done
```

Look for `HID_ID=0003:0000256C:0000006D` on `input0`. That's your device.

---

## Compatibility

Tested on:
- **Tablet:** Huion Kamvas Pro 16 (USB)
- **OS:** Fedora 44, KDE Plasma / Wayland
- **Driver:** OpenTabletDriver 0.6.7
- **Kernel:** 6.x

Should work on other distros with minimal changes. The hidraw interface and report format appear consistent across firmware versions, but if byte positions differ on your system, run this to check raw output:

```bash
sudo python3 -c "
f = open('/dev/hidraw1','rb')
while True:
    r = f.read(12)
    if r[1] == 0xf0:
        print('strip:', r.hex(), '| pos byte 5:', r[5])
"
```

---

## Why doesn't OTD handle this?

OTD's device configuration for the Kamvas Pro 16 covers the pen and express keys but does not include a parser for the `0xf0` scroll strip report type. This is a known gap — touch bars/scroll strips are not yet supported for Huion tablets in OTD as of 2026. This script is a userspace workaround until native support lands.

---

## Credits

Figured out by **Kim Lang** with assistance from Claude (Anthropic), July 2026.  
Shared freely — if this helped, pay it forward on the [OTD GitHub Discussions](https://github.com/OpenTabletDriver/OpenTabletDriver/discussions) or [r/huion](https://www.reddit.com/r/huion/).
