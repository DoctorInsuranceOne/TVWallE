# Updates: kiosks update themselves from GitHub

TVWallE's own code (the setup script, the controller, the kiosk session, the boot splash, the Firefox policies) is published as **signed releases** on GitHub: https://github.com/DoctorInsuranceOne/TVWallE/releases. Every kiosk installs a newer release by itself:

| When | How |
|---|---|
| **Every night**, just before the 4:00 AM restart | `nightly-reboot` runs the updater, then restarts the PC, so the new version starts clean. If GitHub can't be reached, it just restarts. |
| **When someone presses Update now** in the [web controller](CONTROLLER.md) | The controller starts `tvwalle-update.service`; the screen restarts once it's installed (a few minutes). **Check for updates** only asks. |
| **At the first start** of a PC installed from the USB | Before the setup runs, a newer release replaces the copy on the USB. Without the Internet, or with nothing newer, the USB's copy is used. |

Ubuntu and Firefox update separately, through Ubuntu's own updates at 3:30 AM ([UPKEEP.md](UPKEEP.md)).

Nothing private is in the repo or a release, so an update never brings or replaces any. The passwords, the Wi-Fi, the SSH key, the TV's pairing with the CRM and the page opened from the controller stay on each PC. They come from the USB ([INSTALLER.md](INSTALLER.md)) or are set on the PC.

## Publishing a release (on the Mac)

Commit everything on `main`, then:

```bash
tools/release.sh 1.2
```

It checks the version is newer than the last release, packs that commit, signs it with the release key (asking for its passphrase), tags `v1.2`, pushes, and creates the GitHub Release. Kiosks install it that night, or right away with **Update now**.

Version numbers are numbers and dots (`1.2`, `1.10`, `2.0`); a kiosk installs only a version newer than the one it has, so a release can't be taken back by publishing an older number. To undo a bad release, publish a newer one with the fix.

## How a kiosk checks a release

A release has three files: `tvwalle.tar.gz` (the repo, with a `VERSION` file), `release.json` (the version, the commit and the tarball's SHA-256) and `release.json.sig`.

1. The kiosk downloads `release.json` and its signature from the latest release (a plain HTTPS download: no GitHub account, no API limits).
2. It checks the signature with **the release key already on the kiosk** (`/opt/tvwalle/release/allowed_signers`), never with a key that came in the download. Someone who took over the GitHub repo still couldn't run code on the TVs without the release key.
3. Only a **newer** version goes on. The tarball must match the SHA-256 in the signed `release.json`, and its `VERSION` the version.
4. It unpacks next to `/opt/tvwalle`, keeps the current copy as `/opt/tvwalle.previous`, puts the new one in place and runs its setup (`provision/install.sh`).
5. **If the setup fails, the previous copy goes back** and its setup runs again; the failed copy stays in `/opt/tvwalle.failed` for a look. The nightly update doesn't retry a version that failed on that PC; **Update now** still does.

The outcome of each run is in `/var/lib/tvwalle/update-status.json`, which the controller shows. The log: `journalctl -u tvwalle-update` (Update now), `journalctl -u tvwalle-reboot` (the nightly run), `journalctl -u tvwalle-firstboot` (the first start).

By hand on a kiosk:

```bash
tvwalle-update --check
```

```bash
sudo tvwalle-update --apply
```

## The release key

An SSH key, made once on the Mac that publishes releases. Its public half is in the repo (`release/allowed_signers`), so it is on every kiosk and every USB; its private half never leaves the Mac.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/tvwalle_release_ed25519 -C tvwalle-release
```

Give it a passphrase, and keep a copy of `~/.ssh/tvwalle_release_ed25519` somewhere safe (a password manager): **without it no kiosk accepts a new release**, and each PC would have to be given a new key by hand. If it's ever exposed, make a new one, publish a release whose `release/allowed_signers` has the new key (signed with the old one, so kiosks accept it), and remove the old line in the release after.

The line in `release/allowed_signers`:

```
tvwalle-release namespaces="tvwalle-release" ssh-ed25519 AAAA…
```

## A PC deployed by hand

`tools/deploy.sh` (development, the dev VM) copies the Mac's working copy over and marks the PC (`/var/lib/tvwalle/deployed-by-hand`). Its nightly update then leaves it alone, so the next night doesn't replace the code being tried. **Update now** (or `sudo tvwalle-update --apply`) puts the latest release back and removes the mark.

## A PC set up before the updater existed

A PC installed before TVWallE could update itself has no updater and no release key. Once, at the PC: plug in a keyboard, press **Ctrl+Alt+F2** for a login prompt over the TV picture, and log in as `tvadmin` (the console password typed when its USB was built). **Ctrl+Alt+F1** goes back to the TV picture. Then:

```bash
curl -fsSLO https://github.com/DoctorInsuranceOne/TVWallE/releases/latest/download/tvwalle.tar.gz
```

```bash
tar -xzf tvwalle.tar.gz
```

```bash
sudo python3 tvwalle/rootfs/usr/local/lib/tvwalle/tvwalle-update --apply --signers tvwalle/release/allowed_signers
```

That one time it trusts the key in the downloaded release (over GitHub's HTTPS). From then on the PC checks every release against the key it has. It keeps its pairing, passwords and Wi-Fi. Reinstalling from a new USB does the same but pairs as a new TV.

## Releases from somewhere else

`/etc/tvwalle/update.conf` (optional) points a kiosk at another release server:

```
RELEASES_URL=https://github.com/<owner>/<repo>/releases
```

For a fork with its own key, or a test server. The server needs GitHub's layout: `latest/download/<file>` and `download/<tag>/<file>`.

## Testing a release without publishing it

```bash
tools/release.sh 1.2 --out /tmp/tvwalle-releases --key /tmp/test_release_key
```

packs the working copy (uncommitted changes included) into that folder in GitHub's layout, signed with a test key whose public half goes into that package. Serve the folder (`python3 -m http.server 8765 --bind 192.168.64.1` in it, for a UTM VM), put `RELEASES_URL=http://192.168.64.1:8765` in the VM's `/etc/tvwalle/update.conf`, and install the first test release on it as in "A PC set up before the updater existed", with the test server's address. Make a test key with `ssh-keygen -t ed25519 -N '' -f /tmp/test_release_key`.

## Files

| Path | What |
|---|---|
| `tools/release.sh` | publishes a release (on the Mac) |
| `release/allowed_signers` | the release key's public half |
| `/usr/local/lib/tvwalle/tvwalle-update` (`/usr/local/sbin/tvwalle-update`) | the updater |
| `/etc/systemd/system/tvwalle-update.service` | runs it for **Update now** |
| `/usr/local/lib/tvwalle/nightly-reboot` | runs it before the nightly restart |
| `/opt/tvwalle/installer/firstboot` | runs it at the first start |
| `/var/lib/tvwalle/update-status.json` | the last run's outcome |
| `/opt/tvwalle.previous`, `/opt/tvwalle.failed` | the copy before the last update; a copy whose setup failed |
| `/etc/tvwalle/update.conf` | optional: another release server |
