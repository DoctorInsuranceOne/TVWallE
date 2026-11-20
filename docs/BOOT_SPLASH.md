# Boot splash

From power-on to the dashboard, the TV shows:

1. **The PC's own logo** (the firmware's; the VM shows UTM's). Only replaceable if the motherboard supports a custom logo.
2. **The Doctor Insurance One logo on black** (Plymouth). GRUB is hidden and the kernel is quiet, so no text appears.
3. **About half a second of black** while sway takes over the screen (a mode switch; nothing to fix).
4. **The same logo again**, same size and place: sway's background, then Firefox's start page.
5. **The dashboard.**

Measured on the dev VM: the logo is up 0.5 s into the boot, and the dashboard follows about 8 s after the firmware hands over. A browser restart (controller, crash) goes black → logo → dashboard in about 2 s.

## The pieces

| Stage | What draws the logo | File |
|---|---|---|
| Boot | Plymouth, `script` theme `tvwalle`, 40% of the screen width, centred | `/usr/share/plymouth/themes/tvwalle/` (`tvwalle.plymouth`, `tvwalle.script`, `logo.png`) |
| sway starting | sway's background: a 16:9 black canvas with the logo at 40%, scaled to the screen | `/usr/local/share/tvwalle/splash-3840x2160.png` |
| Firefox starting | the start page, logo at `40vw`, centred | `/usr/local/share/tvwalle/start/index.html` + `logo.png` |

**Change the size in all three together** (`0.40` in the Plymouth script, `LOGO_SHARE` in `tools/make-branding.py`, `40vw` and the message's offset in the start page).

## The start page

`kiosk-session` opens `file:///usr/local/share/tvwalle/start/index.html#<page>`. The page checks the page's server answers (any answer at all), then replaces itself with the page. Without network it stays, shows **"Waiting for the network…"** under the logo (after a minute: **"Still waiting for the network. Check the cable or Wi-Fi."**), and tries every 3 seconds. So the kiosk service no longer waits for the network itself: an unplugged TV shows the logo and a sentence instead of a silent boot screen.

The controller's **Open a page** and **Back to the default page** go straight to the page, without the start page.

## Logo files

`branding/` in this repo:

| File | What |
|---|---|
| `DIO_logo.png` | The source: the wordmark, 1329×512, **opaque on white** (a copy of `HuphSign/public/DIO_logo.png`). No larger, transparent or vector version exists in the repos or on the brand CDN. |
| `logo.png` | Made by `tools/make-branding.py`: the white made transparent. The wordmark has two colours (orange 241,101,66 and blue 17,120,186), so each pixel is unmixed into "brand colour + how much of it": solid letters stay full strength, only the edges are partly transparent. |
| `splash-3840x2160.png` | Made by the same script: `logo.png` at 1536 px on an opaque black 3840×2160 canvas. |

To change the logo: replace `branding/DIO_logo.png` (any two-colour logo on white works; anything else needs a real transparent source), run `python3 tools/make-branding.py`, commit, deploy. A vector logo from the designer would make the splash sharp on a 4K TV; today's source is enlarged about 15% there.

## What the setup script does

`install_boot_splash` in `provision/install.sh`:

- installs the theme, `/etc/plymouth/plymouthd.conf` (`ThemeDir` + `Theme`), the start page and the background;
- rebuilds the boot image (`update-initramfs -u`, which is dracut on Ubuntu 26.04) only when a theme file changed, or when the image is missing the theme or Plymouth's text plugin;
- installs `/etc/default/grub.d/90-tvwalle.cfg` and runs `update-grub` when it changed (or when the kernel line lacks `quiet splash`).

## Things that broke on the way (2026-11-19)

- **`Theme=` alone is ignored by the boot image.** `plymouth-populate-initrd` only honours it when `ThemeDir` is set too; otherwise it copies a theme called "none". Ubuntu 26.04 has no `default.plymouth` alternative and no `plymouth-set-default-theme`.
- **A serial console forces plain text.** The ARM VM has `ttyAMA0` (from its firmware), and Plymouth then shows scrolling text even with `splash`. Fixed with `plymouth.ignore-serial-consoles` on the kernel line.
- **Plymouth 24's script plugin crashes without a text plugin.** No `label-freetype.so`/`label-pango.so` means no "console viewer", and the script plugin later uses it anyway: SEGV, text boot. Fixed by installing `plymouth-label-ft` (its plugin and two Noto fonts then travel in the boot image).
- **A tight screenshot loop during startup left Firefox with no window**: about 30 `grim` captures a second while sway started. Five plain restarts afterwards were all fine. Record a startup at a few frames a second, not continuously.

## Getting to the GRUB menu

It's hidden with no wait. For recovery, press **Esc** repeatedly (UEFI) or hold **Shift** (BIOS) right after power-on. After an unfinished boot (a power cut while starting) Ubuntu normally shows the menu for 30 s; `GRUB_RECORDFAIL_TIMEOUT=0` turns that off, since a TV has nobody to choose.

## Debugging the splash

Add `plymouth.debug` to the kernel line (a drop-in in `/etc/default/grub.d/`, then `update-grub`), reboot, and read `/var/log/plymouth-debug.log` (root). Remove it afterwards. The boot itself can only be watched on the screen (or the UTM window); `lsinitrd /boot/initrd.img | grep plymouth` shows what the boot image carries.
