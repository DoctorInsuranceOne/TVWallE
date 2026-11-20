# Upkeep: updates, restarts, access

What keeps a kiosk current and locked down without anyone touching it. All set up by `provision/install.sh`.

## A night on a kiosk (office time, America/New_York)

| Time | What | How |
|---|---|---|
| 3:30–3:40 AM | Install updates: Ubuntu security **and** regular updates, and Firefox ESR | `apt-daily-upgrade.timer` (moved here by `apt-daily-upgrade.timer.d/tvwalle.conf`), rules in `/etc/apt/apt.conf.d/51tvwalle-unattended` |
| 4:00 AM | Install a newer TVWallE release from GitHub, if there is one ([UPDATES.md](UPDATES.md)), then restart the PC: about 10 s of logo, then the dashboard | `tvwalle-reboot.timer` → `/usr/local/lib/tvwalle/nightly-reboot` → `tvwalle-update --apply --trigger nightly` |
| Every minute | Is Firefox answering? | `tvwalle-watchdog.timer` → `controller.py --watchdog` |

The restart waits for any update still running (up to 30 minutes), and skips that night rather than interrupt one. A PC that was off at 4:00 does not restart when it comes back on.

## Updates

- **Ubuntu security updates** were already on; **Ubuntu's regular updates (`-updates`)** are added, since nobody runs `apt upgrade` on a TV (the VM had 47 waiting, all installed by the first run).
- **Firefox ESR** comes from Mozilla's repository, which unattended-upgrades ignores unless told: `Origins-Pattern "site=packages.mozilla.org"`.
- **Config files TVWallE edits stay as they are** when a package ships a new default (`Dpkg::Options --force-confdef --force-confold`). Without it, unattended-upgrades skips any package whose config file was changed: `/etc/plymouth/plymouthd.conf` belongs to the `plymouth` package, so Plymouth would never update.
- An update installed under a running Firefox is picked up by the 4:00 AM restart.
- **TVWallE itself** updates from signed GitHub releases, just before that restart: [UPDATES.md](UPDATES.md).

Check: `sudo unattended-upgrade --dry-run --debug 2>&1 | grep "Allowed origins"`; history in `/var/log/unattended-upgrades/`.

## The watchdog

A crashed Firefox is restarted by systemd at once. A **hung** one (stuck, not crashed) isn't, so every minute the watchdog opens a connection to Firefox's Marionette port and waits for its greeting, which comes from Firefox's main thread. **Three missed checks in a row restart the browser.** It opens no session, so it never collides with a controller button. A kiosk started less than 2 minutes ago isn't checked. It runs as the controller's user and restarts through the controller's polkit rule.

Tested on the VM by freezing Firefox (`kill -STOP`): 1 of 3, 2 of 3, restart; back on the dashboard about 15 s later (the startup script waits 10 s for the frozen Firefox before forcing it closed).

Log: `journalctl -u tvwalle-watchdog`.

## Access

- **SSH accepts keys only** (`/etc/ssh/sshd_config.d/10-tvwalle.conf`; also no root login). It's named `10-` because sshd keeps the **first** value it reads and the installer's `50-cloud-init.conf` turns passwords on. The setup only installs it when an admin (group `sudo`) already has a key, and checks it with `sshd -t` first.
- **Admins use sudo without a password** (`/etc/sudoers.d/90-tvwalle-admins`, `%sudo`), so the key is the credential and `tools/deploy.sh` runs unattended. Checked with `visudo -c` before it takes effect.
- **Firewall (ufw)**: everything coming in is refused except SSH (22) and the controller (8080) from private addresses (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16). Marionette is on 127.0.0.1 only and needs no rule. `sudo ufw status`.

## Removed

- **kdump** (`kdump-tools`, `makedumpfile`, `kexec-tools`, `crash`): it reserved 512 MB of memory (`crashkernel=`) for crash dumps nobody reads on a TV. The VM went from 7.58 GB to 8.10 GB usable.
- **snapd**: nothing on the kiosk is a snap. `ubuntu-server-minimal` **depends** on it and goes with it, so the setup first marks that meta-package's dependencies as installed on purpose (`apt-mark manual`); otherwise a later `autoremove` could remove SSH and the rest. `/etc/apt/preferences.d/no-snapd` keeps snapd from coming back.

## Running something at a different time

The times are in the unit files: `tvwalle-reboot.timer` (`OnCalendar=*-*-* 04:00`) and `apt-daily-upgrade.timer.d/tvwalle.conf` (`03:30`). Keep the updates before the restart. The time zone is `TIMEZONE` in `provision/install.sh`.
