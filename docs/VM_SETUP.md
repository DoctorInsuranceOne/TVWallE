# Dev VM setup (UTM on the Mac)

The dev VM is where the setup scripts are run and tested. It is never configured by hand beyond this page: everything the kiosk needs comes from `provision/install.sh`, so the same steps can later run on the real PC.

The Mac is Apple silicon (ARM), so the VM is the **ARM64** build of Ubuntu and runs at full speed. The real PC is Intel/AMD (x86-64) and gets the amd64 build. The scripts are the same for both.

## 1. Download the ISO

Ubuntu Server 26.04.1 LTS for ARM: https://ubuntu.com/download/server/arm

The file is named like `ubuntu-26.04.1-live-server-arm64.iso`.

## 2. Create the VM in UTM

1. **Create a New Virtual Machine** → **Virtualize** (not Emulate) → **Linux**.
2. **Boot ISO Image**: pick the ISO from step 1.
3. **Hardware**: Memory **8192 MB**, CPU **4** cores.
4. **Storage**: **32 GB**.
5. Shared directory: skip.
6. **Name**: `TVWallE`. Tick **Open VM Settings**, then **Save**.
7. In the settings that open:
   - **Display** → Emulated Display Card: **virtio-gpu-pci**. The kiosk needs a real graphics device; the default framebuffer-only card can't run it. Avoid the `-gl` variant for now, it's the one that most often misbehaves.
   - **Network**: leave **Shared Network**. The Mac can reach the VM at its 192.168.64.x address.
   - **Sound**: leave the default. The VM has no screen with sound, so with TVWallE's sound rules ([SOUND.md](SOUND.md)) its sound goes nowhere ("Dummy Output").
   - Save.

UEFI is UTM's default for this kind of VM, which matches the real PC.

## 3. Install Ubuntu Server

Start the VM and pick **Try or Install Ubuntu Server**. In the installer:

| Screen | Choose |
|---|---|
| Language, keyboard | English, your keyboard |
| Type of install | **Ubuntu Server** (not the minimized one) |
| Network | leave DHCP as offered |
| Proxy, mirror | leave as offered |
| Storage | **Use an entire disk**, leave the rest as offered |
| Profile | Your name: anything. Server name: **`tvwalle-vm`**. Username: **`tvadmin`**. A password you'll remember. |
| Ubuntu Pro | Skip for now |
| SSH | **Install OpenSSH server**: on. **Allow password authentication over SSH**: on (needed once, to copy the key in step 5) |
| Featured snaps | none |

When it says **Reboot Now**: first remove the ISO in UTM (the CD/DVD icon in the toolbar → **Clear**), then reboot. Otherwise it boots the installer again.

## 4. Find the VM's address

Log in at the VM window as `tvadmin` and run:

```bash
hostname -I
```

The first address (192.168.64.x) is the one to use below.

## 5. On the Mac: an SSH key for TVWallE machines

A key used only for TVWallE machines. Run in the Mac's Terminal (press Enter at the passphrase prompts for no passphrase, or set one and add it to the keychain afterwards):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/tvwalle_ed25519 -C tvwalle
```

Add a short name for the VM to `~/.ssh/config` (replace the address with the one from step 4):

```
Host tvwalle-vm
  HostName 192.168.64.X
  User tvadmin
  IdentityFile ~/.ssh/tvwalle_ed25519
  IdentitiesOnly yes
```

Copy the key to the VM (asks for the `tvadmin` password once):

```bash
ssh-copy-id -i ~/.ssh/tvwalle_ed25519.pub tvwalle-vm
```

## 6. In the VM: sudo without a password (dev VM only)

So the deploy script can run the setup without stopping to ask. In the VM:

```bash
echo 'tvadmin ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/90-tvadmin
```

```bash
sudo chmod 440 /etc/sudoers.d/90-tvadmin
```

The first run of the setup then makes it permanent for every admin (`/etc/sudoers.d/90-tvwalle-admins`), turns SSH passwords off (keys only), and turns the firewall on. See [UPKEEP.md](UPKEEP.md).

## 7. Check

From the Mac, this should print `tvwalle-vm` without asking for anything:

```bash
ssh tvwalle-vm 'sudo hostname'
```

Then the kiosk is installed with:

```bash
tools/deploy.sh tvwalle-vm
```

It copies the repo to `/opt/tvwalle` in the VM (owned by root, the same place a PC installed from the USB has it) and runs the setup there. It also marks the VM as deployed by hand, so its nightly update doesn't replace the code being tried with the latest release ([UPDATES.md](UPDATES.md)).

## Getting around once the kiosk runs

- The UTM window shows only the browser. Use SSH to get in.
- Kiosk log: `journalctl -u tvwalle-kiosk -f`
- Stop the kiosk and get the text login back: `sudo systemctl stop tvwalle-kiosk` (this also ends Firefox)
- Screenshot of the kiosk screen, saved in the VM: `sudo tvwalle-screenshot /tmp/kiosk.png`. To look at it on the Mac: `scp tvwalle-vm:/tmp/kiosk.png .`
