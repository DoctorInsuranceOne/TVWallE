# TVWallE

A small Ubuntu-based kiosk that shows a web page full screen on an office TV, in Firefox ESR, and keeps it there unattended: a boot splash with the company logo, a web page on the office network to refresh it or change the page, nightly updates and restarts, a watchdog, and updates to itself from signed GitHub releases.

Built for Doctor Insurance One's office TVs, so the default page is its CRM's TV dashboard (`KIOSK_URL` in `/etc/tvwalle/kiosk.conf` changes it, and so does the controller). Built on Ubuntu Server 26.04 LTS; everything a kiosk needs is set up by the scripts here, and nothing on a kiosk is configured by hand.

**This repo holds no secrets.** The passwords and the Wi-Fi are typed when an installer USB is built and stay on that USB and the PCs installed from it; the SSH key and the release key that go with them are only their public halves.

| Folder | What's in it |
|---|---|
| `provision/` | `install.sh`, which turns a fresh Ubuntu Server into the kiosk (safe to re-run) |
| `installer/` | The installer USB's settings (`autoinstall.yaml`) and its three steps: `early`, `late`, `firstboot` |
| `rootfs/` | Files copied onto the machine as they are: systemd units, PAM session, polkit rule, sway kiosk config, Firefox policies, the sound rules, settings, the web controller, the updater, the Plymouth theme, the GRUB drop-in, the start page, `tvwalle-screenshot`, `tvwalle-passwd`, `tvwalle-sound` |
| `release/` | The public half of the release key every kiosk checks updates against |
| `branding/` | The logo: the source, the transparent version and the splash canvas |
| `tools/` | Helpers run from a Mac: `build-installer.sh` (the USB image; its container tools in `isotools/`), `release.sh` (publishes a signed release), `deploy.sh` (development), `make-branding.py` |
| `docs/` | Guides: [the installer USB](docs/INSTALLER.md), [updates](docs/UPDATES.md), [the web controller](docs/CONTROLLER.md), [the boot splash](docs/BOOT_SPLASH.md), [sound](docs/SOUND.md), [nightly upkeep and access](docs/UPKEEP.md), [the dev VM](docs/VM_SETUP.md) |

**A new PC** is installed from the USB: [docs/INSTALLER.md](docs/INSTALLER.md). Its first start installs the latest release from GitHub.

**Updates** reach every kiosk by themselves: publish one with `tools/release.sh <version>`, and each kiosk installs it just before its 4:00 AM restart, or at once with **Update now** in its controller. Only releases signed with the release key are installed: [docs/UPDATES.md](docs/UPDATES.md).

**Development:** build the VM with [docs/VM_SETUP.md](docs/VM_SETUP.md), run `tools/deploy.sh tvwalle-vm`, then set the controller password with `ssh -t tvwalle-vm sudo tvwalle-passwd` and open `http://<vm address>:8080`. On every machine the repo copy lives in `/opt/tvwalle`.
