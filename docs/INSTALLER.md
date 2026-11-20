# The TVWallE installer USB

One USB stick turns any PC into a TVWallE kiosk: the stock Ubuntu Server 26.04.1 ISO plus TVWallE's install settings (`installer/autoinstall.yaml`) and a copy of this repo. You boot the PC from it and confirm once. It erases the disk, installs Ubuntu, and on the first start it installs the latest TVWallE release from GitHub (or keeps the USB's copy when that is the latest, or GitHub can't be reached) and runs its setup. Then it restarts into the dashboard, and from then on it updates itself ([UPDATES.md](UPDATES.md)). Every PC starts fresh: its own machine ID, SSH host keys and, once paired, its own TV key.

## 1. Build the image (on the Mac)

Needs Docker Desktop running (the ISO tools run in a container; nothing is installed on the Mac), the TVWallE SSH key `~/.ssh/tvwalle_ed25519` ([VM_SETUP.md](VM_SETUP.md), step 5), and the stock ISO in `~/Downloads`:

| For | ISO | From |
|---|---|---|
| A real PC (Intel/AMD) | `ubuntu-26.04.1-live-server-amd64.iso` | https://releases.ubuntu.com/26.04.1/ |
| A test VM on the Mac (UTM) | `ubuntu-26.04.1-live-server-arm64.iso` | https://cdimage.ubuntu.com/releases/26.04.1/release/ |

In a terminal, from the repo folder:

```bash
tools/build-installer.sh amd64
```

The first build asks for three things. They are saved for the next build in `build/installer-settings.json`, readable by you only:

- **Console password for `tvadmin`**: to log in at the PC's own keyboard (for example when its network is down). SSH stays key-only. Saved as a hash only.
- **Controller password**: the web page on port 8080, the same on every PC built from this USB. Saved as a hash only. One PC can be changed later with `sudo tvwalle-passwd`.
- **Wi-Fi name and password**: leave the name empty for wired only. The password is kept as it is (see "What the USB carries").

To type them again: `tools/build-installer.sh amd64 --new-settings`.

The result is `build/tvwalle-installer-amd64.iso` (about 2.9 GB). The build prints its size and SHA-256, and the TVWallE version inside. Built from a release tag with nothing changed (`git checkout v1.2`), the USB carries that release's number; any other build carries the git commit (`-dirty` means uncommitted changes) and counts as older than every release, so the first start replaces it with the latest release.

## 2. Write it to a USB stick (8 GB or more; everything on it is erased)

With [balenaEtcher](https://etcher.balena.io), or in the Terminal:

```bash
diskutil list external
```

Find the USB stick by its size, for example `/dev/disk4`. **Check the number: the next commands erase that disk.**

```bash
diskutil unmountDisk /dev/disk4
```

```bash
sudo dd if=build/tvwalle-installer-amd64.iso of=/dev/rdisk4 bs=4m status=progress
```

```bash
diskutil eject /dev/disk4
```

When macOS says the disk is not readable, choose **Ignore** or **Eject**: that is normal for an installer stick.

**On Windows, with [Rufus](https://rufus.ie)** (it can run on the TV's PC itself, before Windows is erased):

1. Copy `build/tvwalle-installer-amd64.iso` to the Windows PC and check it arrived whole: `certutil -hashfile tvwalle-installer-amd64.iso SHA256` must print the SHA-256 the build printed.
2. In Rufus: **Device** = the USB stick, **Boot selection** = the ISO, then **START**.
3. When Rufus says it found an **ISOHybrid** image, choose **Write in DD Image mode**, not the recommended ISO mode. DD mode copies the image byte for byte, which is what was tested; ISO mode rebuilds the stick its own way.
4. Afterwards delete the `.iso` from that PC: it carries the Wi-Fi password.

## 3. Install on the PC

1. Plug in the USB stick, and the network cable if the PC is wired.
2. Switch the PC on and open its **boot menu** (usually F12 on Dell and Lenovo, F11 on MSI, F8 on Asus, Esc or F9 on HP). Choose the USB stick (the **UEFI** entry). Secure Boot can stay on.
3. The menu shows **Install TVWallE (erases this PC's disk)** and starts it by itself after 30 seconds.
4. Shortly after, the installer asks **"Continue with autoinstall? (yes|no)"**. Type `yes` and press Enter. **This erases the whole disk**, Windows included. Typing anything else stops without touching it.
5. About 10 to 15 minutes later the PC **switches itself off** (12 minutes on the test VM). With a network the installer also fetches the latest kernel and updates, so it can sit on "installing kernel" for several minutes with nothing new on the screen; that's normal. Take the USB stick out, then switch the PC on. If the USB menu shows again (the stick is still in, or a VM still has the ISO), choose **Boot from next volume**. Left alone, it would only stop at the "Continue with autoinstall?" question.
6. **First start** (53 seconds on the test VM with a fast connection; a few minutes over Wi-Fi; progress shows on the screen):
   - it names the PC `tvwalle-` plus the last 4 characters of its network card's address (for example `tvwalle-a1b2`);
   - it waits for the Internet;
   - it installs the latest TVWallE release from GitHub if it's newer than the USB's copy (if that release's setup fails, it goes back to the USB's copy);
   - it runs the setup, which downloads Firefox ESR and the rest;
   - it sets the controller password from the USB;
   - it restarts.
7. After that restart the TV shows the Doctor Insurance One logo, then the dashboard's pairing code. Pair it from the CRM (Agency Management → TV Displays).

If the first start can't finish (for example, no Internet), it says so on the screen and tries again at the next start. See "If something goes wrong".

## 4. After the install

- **Find its address**: the router's list of devices (look for `tvwalle-…`), or log in at the PC as `tvadmin` and run `hostname -I`.
- **SSH**: add the PC to `~/.ssh/config` on the Mac, as with the dev VM:

  ```
  Host tvwalle-a1b2
    HostName 192.168.1.50
    User tvadmin
    IdentityFile ~/.ssh/tvwalle_ed25519
    IdentitiesOnly yes
  ```

- **Controller**: `http://<its address>:8080`, with the password typed when the USB was built.
- **Updates to TVWallE itself**: the PC installs every new signed release by itself, just before its 4:00 AM restart, or at once with **Update now** in the controller ([UPDATES.md](UPDATES.md)). Ubuntu and Firefox update themselves every night too ([UPKEEP.md](UPKEEP.md)). `tools/deploy.sh tvwalle-a1b2` still copies the Mac's working copy over, for development.
- What it was built from: `cat /opt/tvwalle/VERSION`.

## What the USB carries

| On the USB | Kept as |
|---|---|
| `tvadmin` console password | SHA-512 hash (`$6$…`) |
| Controller password | scrypt hash, as `/etc/tvwalle/controller-password` holds it |
| Wi-Fi name and password | **as typed**: Ubuntu needs them to connect before anyone logs in, so each PC also keeps them in `/etc/netplan/` (readable by root only) |
| Your SSH key | the **public** half only (`~/.ssh/tvwalle_ed25519.pub`) |
| The release key | the **public** half only, in the repo (`release/allowed_signers`): what the PC checks every update against |

Keep the USB stick, the `.iso` and `build/installer-settings.json` like a key to the office Wi-Fi. Nothing in `build/` goes into git.

## Testing it in a VM (the ARM image, on the Mac)

The dev VM stays as it is; the test gets its own VM.

1. `tools/build-installer.sh arm64` (same three questions the first time).
2. In UTM: **Create a New Virtual Machine** → **Virtualize** → **Linux** → Boot ISO image: `build/tvwalle-installer-arm64.iso` → Memory **4096 MB**, CPU **4** → Storage **32 GB** → Name `TVWallE-USB-test`, tick **Open VM Settings** → **Display**: **virtio-gpu-pci** → Save.
3. Start it and follow step 3 above from point 3. When it switches off, clear the ISO in UTM (CD/DVD icon → **Clear**), then start it again. UTM boots the ISO before the disk while one is attached, so without clearing it, choose **Boot from next volume** at every start.
4. The VM has no Wi-Fi card, so it uses the wired network and skips the Wi-Fi part. The Wi-Fi part can only be tried on a PC with a Wi-Fi card.

You don't need to pair the test VM. Pairing makes a real TV device in the CRM; if you do pair it, remove it there afterwards.

## How it works

| File | Runs | Does |
|---|---|---|
| `tools/build-installer.sh` | on the Mac | copies the repo as git sees it (nothing `.gitignore` lists) plus a `VERSION` file, starts the container |
| `tools/isotools/assemble.py` | in the container | asks for or reads the settings, fills in `installer/autoinstall.yaml` and checks it by reading it back, retitles the boot menu entry (and its line in `md5sum.txt`), repacks the ISO with xorriso, keeping Ubuntu's boot setup (`-boot_image any replay`) |
| `installer/autoinstall.yaml` | Ubuntu's installer | whole disk (the largest; the USB is never a candidate), user `tvadmin`, SSH with your key only, time zone, network, then poweroff |
| `installer/early` | installer, before the disk | puts this PC's Wi-Fi card name in place of `TVWALLE_WIFI_IF` (Ubuntu's network setup takes a card only by its exact name), or removes the Wi-Fi part when there is no card |
| `installer/late` | installer, after the install | copies the repo to `/opt/tvwalle`, turns on `tvwalle-firstboot.service` |
| `installer/firstboot` | first start | moves the USB's password hash out of the copy, names the PC, waits for the Internet, installs a newer signed release from GitHub, runs `provision/install.sh` (up to 5 tries; then back to the USB's copy if the release's setup is what failed), installs the controller password, turns itself off, restarts |

The installer asks before it erases anything because the settings are on the USB itself and the boot menu doesn't add `autoinstall` to the kernel line. That question is on purpose: without it, a USB stick left in a PC would erase that PC at its next start.

## If something goes wrong

- **The first start says the setup didn't finish**: log in at the PC as `tvadmin` (the console password) or over SSH, read `journalctl -u tvwalle-firstboot -b`, fix the cause (usually the network), then `sudo systemctl restart tvwalle-firstboot`. It also tries again by itself at every start until it finishes.
- **The installer finds no disk** on a PC that ran Windows: in the firmware settings, set the storage (SATA) mode from **RAID / Intel RST** to **AHCI**. Linux can't see a disk behind Intel RST.
- **Dell OptiPlex**: **F12** at the Dell logo opens the boot menu (pick the USB under UEFI), **F2** opens the firmware settings. Storage mode: **Storage → SATA/NVMe Operation** (older models: System Configuration → SATA Operation), set **AHCI/NVMe** instead of **RAID On**; changing it would stop Windows from starting, which doesn't matter here since the install erases Windows. If the stick is refused with a Secure Boot message, turn on **Secure Boot → Enable Microsoft UEFI CA** (Ubuntu's boot loader is signed by it), or turn Secure Boot off. Many OptiPlex desktops have **no Wi-Fi card**; then the PC needs a network cable.
- **The PC boots Windows or the old system instead of the USB**: use the boot menu key, or turn off "Fast Boot" in the firmware settings.
- **The installer's own log** is on the installed PC in `/var/log/installer/`.
