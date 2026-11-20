#!/usr/bin/env python3
"""Assembles a TVWallE installer ISO.

Runs inside the tvwalle-isotools container, started by tools/build-installer.sh,
which mounts:

  /repo    this repo, read-only (the autoinstall template, controller.py)
  /iso     the folder holding the stock Ubuntu Server ISO, read-only
  /build   build/ in this repo: the saved settings, the staged files, the result

The staged folder already holds the copy of the repo that goes on the USB
(<stage>/tvwalle). This adds the settings (asked for, or read from the saved
settings file), writes /autoinstall.yaml, retitles the boot menu entry, and
repacks the ISO with xorriso, keeping Ubuntu's boot setup (Secure Boot
included) as it is.

The settings file holds the password HASHES and the Wi-Fi name and password;
it never holds the tvadmin or controller passwords themselves.
"""
import argparse
import datetime
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys

import yaml

TEMPLATE = "/repo/installer/autoinstall.yaml"
CONTROLLER = "/repo/rootfs/usr/local/lib/tvwalle/controller.py"
WIFI_BEGIN, WIFI_END = "tvwalle-wifi-begin", "tvwalle-wifi-end"
WIFI_PLACEHOLDER = "TVWALLE_WIFI_IF"
MENU_FROM = 'menuentry "Try or Install Ubuntu Server"'
MENU_TO = 'menuentry "Install TVWallE (erases this PC\'s disk)"'
MIN_PASSWORD = 8


def fail(message):
    sys.exit(f"\nERROR: {message}")


# --- Settings -------------------------------------------------------------------

def ask_password(what, minimum, maximum=None):
    while True:
        password = getpass.getpass(f"  {what}: ")
        if len(password) < minimum:
            print(f"  Use at least {minimum} characters.")
            continue
        if maximum and len(password) > maximum:
            print(f"  Use at most {maximum} characters.")
            continue
        if getpass.getpass("  Type it again: ") != password:
            print("  The two differ. Try again.")
            continue
        return password


def admin_password_hash(password):
    out = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n",
                         capture_output=True, text=True, check=True).stdout.strip()
    if not out.startswith("$6$"):
        fail("openssl did not return a password hash.")
    return out


def controller_password_hash(password):
    out = subprocess.run(["python3", CONTROLLER, "--hash-password"], input=password + "\n",
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.startswith("scrypt$"):
        fail(f"controller.py could not hash the password: {out.stderr.strip()}")
    return out.stdout.strip()


def ask_settings():
    print("\nTVWallE installer settings. The passwords are saved only as hashes.\n")
    print("Console password for tvadmin (to log in at the PC's own keyboard;")
    print("SSH stays key-only):")
    admin = admin_password_hash(ask_password("New password", MIN_PASSWORD))
    print("\nController password (the web page on port 8080; the same on every PC")
    print("installed from this USB):")
    controller = controller_password_hash(ask_password("New password", MIN_PASSWORD))
    print("\nWi-Fi network name (leave it empty for wired network only):")
    ssid = input("  Network name: ").strip()
    wifi = None
    if ssid:
        if len(ssid.encode()) > 32:
            fail("A Wi-Fi network name is at most 32 bytes.")
        print("Wi-Fi password (WPA, 8 to 63 characters). It is stored on the USB and on")
        print("each PC as it is: the PC needs it to connect before anyone logs in.")
        wifi = {"ssid": ssid, "password": ask_password("Wi-Fi password", 8, 63)}
    return {
        "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "admin_password_hash": admin,
        "controller_password_hash": controller,
        "wifi": wifi,
    }


def save_settings(path, settings):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.chmod(path, 0o600)


def network_label(settings):
    wifi = settings.get("wifi")
    return f'Wi-Fi "{wifi["ssid"]}" + wired' if wifi else "wired only"


def describe(settings):
    return f"saved {settings.get('created', '?')}, {network_label(settings)}"


def get_settings(path, new):
    interactive = sys.stdin.isatty()
    if os.path.exists(path) and not new:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
        if not interactive:
            print(f"Using the saved settings ({describe(settings)}).")
            return settings
        answer = input(f"Use the saved settings ({describe(settings)})? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return settings
    if not interactive:
        fail("No saved settings. Run tools/build-installer.sh once in a terminal to type the passwords.")
    settings = ask_settings()
    save_settings(path, settings)
    print(f"\nSaved in build/{os.path.basename(path)} for the next build.")
    return settings


# --- The autoinstall file ---------------------------------------------------------

def render_autoinstall(settings, pubkey):
    with open(TEMPLATE, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)
    wifi = settings.get("wifi")
    if not wifi:
        start = next(i for i, line in enumerate(lines) if WIFI_BEGIN in line)
        end = next(i for i, line in enumerate(lines) if WIFI_END in line)
        del lines[start:end + 1]
    text = "".join(lines)

    values = {
        "ADMIN_PASSWORD_HASH": settings["admin_password_hash"],
        "SSH_AUTHORIZED_KEY": pubkey,
        "PACKAGES": ["wpasupplicant"] if wifi else [],
    }
    if wifi:
        values["WIFI_SSID"] = wifi["ssid"]
        values["WIFI_PASSWORD"] = wifi["password"]
    for key, value in values.items():
        # A JSON string or list is also valid YAML, with every character escaped.
        text = text.replace(f"@{key}@", json.dumps(value))
    left = re.findall(r"@[A-Z_]+@", text)
    if left:
        fail(f"Unfilled values in the autoinstall template: {', '.join(sorted(set(left)))}")

    # Read it back the way the installer will, and check what matters.
    config = yaml.safe_load(text)["autoinstall"]
    if config["identity"]["password"] != settings["admin_password_hash"]:
        fail("The tvadmin password hash did not survive the YAML round trip.")
    if config["ssh"]["authorized-keys"] != [pubkey] or config["ssh"]["allow-pw"] is not False:
        fail("The SSH settings did not come out as intended.")
    wifis = config["network"].get("wifis")
    if wifi:
        points = wifis[WIFI_PLACEHOLDER]["access-points"]
        if list(points) != [wifi["ssid"]] or points[wifi["ssid"]]["password"] != wifi["password"]:
            fail("The Wi-Fi settings did not survive the YAML round trip.")
    elif wifis:
        fail("Wi-Fi settings were left in a wired-only build.")
    return text


# --- The ISO -------------------------------------------------------------------------

def xorriso(*args):
    result = subprocess.run(["xorriso", *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr[-3000:], file=sys.stderr)
        fail("xorriso failed (its messages are above).")
    return result


def extract(iso, path, dest):
    xorriso("-osirrox", "on", "-indev", iso, "-extract", path, dest)
    os.chmod(dest, 0o644)


def volume_id(iso):
    out = xorriso("-indev", iso, "-pvd_info").stdout
    match = re.search(r"^Volume Id\s*:\s*(.+)$", out, re.M)
    return match.group(1).strip() if match else ""


def retitle_boot_menu(stage, iso):
    """The first boot menu entry says what it does; md5sum.txt follows the change."""
    grub = os.path.join(stage, "grub.cfg")
    sums = os.path.join(stage, "md5sum.txt")
    extract(iso, "/boot/grub/grub.cfg", grub)
    extract(iso, "/md5sum.txt", sums)

    with open(grub, encoding="utf-8") as f:
        text = f.read()
    if text.count(MENU_FROM) != 1:
        fail(f"The ISO's boot menu has no single '{MENU_FROM}' entry; this Ubuntu ISO is laid out differently.")
    text = text.replace(MENU_FROM, MENU_TO)
    with open(grub, "w", encoding="utf-8") as f:
        f.write(text)

    digest = hashlib.md5(text.encode()).hexdigest()
    with open(sums, encoding="utf-8") as f:
        lines = f.read().splitlines()
    lines = [f"{digest}  ./boot/grub/grub.cfg" if line.endswith("  ./boot/grub/grub.cfg") else line
             for line in lines]
    with open(sums, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", required=True, choices=["arm64", "amd64"])
    parser.add_argument("--iso", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--new-settings", action="store_true")
    args = parser.parse_args()

    pubkey = os.environ.get("SSH_PUBKEY", "").strip()
    if not pubkey.startswith("ssh-"):
        fail("No SSH public key was passed in (SSH_PUBKEY).")

    volid = volume_id(args.iso)
    if args.arch not in volid or "Ubuntu-Server" not in volid:
        fail(f"{os.path.basename(args.iso)} is '{volid}', not an Ubuntu Server ISO for {args.arch}.")

    settings = get_settings(args.settings, args.new_settings)

    with open(os.path.join(args.stage, "autoinstall.yaml"), "w", encoding="utf-8") as f:
        f.write(render_autoinstall(settings, pubkey))
    seed = os.path.join(args.stage, "tvwalle", "seed")
    os.makedirs(seed, exist_ok=True)
    with open(os.path.join(seed, "controller-password"), "w", encoding="utf-8") as f:
        f.write(settings["controller_password_hash"] + "\n")
    retitle_boot_menu(args.stage, args.iso)

    print(f"\nBuilding {os.path.basename(args.out)} from {volid} ...")
    partial = args.out + ".partial"
    if os.path.exists(partial):
        os.unlink(partial)
    xorriso("-indev", args.iso, "-outdev", partial,
            "-map", os.path.join(args.stage, "autoinstall.yaml"), "/autoinstall.yaml",
            "-map", os.path.join(args.stage, "tvwalle"), "/tvwalle",
            "-map", os.path.join(args.stage, "grub.cfg"), "/boot/grub/grub.cfg",
            "-map", os.path.join(args.stage, "md5sum.txt"), "/md5sum.txt",
            "-boot_image", "any", "replay")
    os.replace(partial, args.out)

    sha = hashlib.sha256()
    with open(args.out, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            sha.update(block)
    with open(os.path.join(args.stage, "tvwalle", "VERSION"), encoding="utf-8") as f:
        lines = f.read().strip().splitlines() or ["unknown"]
    # A release build has two lines: "1.0" and how it was built.
    version = lines[0] + (f" ({lines[1]})" if len(lines) > 1 else "")
    print(f"Done: build/{os.path.basename(args.out)}")
    print(f"  size     {os.path.getsize(args.out):,} bytes")
    print(f"  sha256   {sha.hexdigest()}")
    print(f"  TVWallE  {version}")
    print(f"  network  {network_label(settings)}")


if __name__ == "__main__":
    main()
