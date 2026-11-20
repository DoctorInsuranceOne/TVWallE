# Web controller

Every kiosk serves a small page on port **8080** that controls what the TV shows:

| Button | What it does |
|---|---|
| **Refresh page** | Reloads the page on the TV. |
| **Open a page** | Opens the address you type. The kiosk keeps it: after a restart or a reboot it comes back to that page. |
| **Back to the default page** | Forgets the opened page and goes back to the kiosk's default (`KIOSK_URL` in `/etc/tvwalle/kiosk.conf`, normally the TV dashboard). |
| **Restart browser** | Closes and reopens Firefox. The screen goes dark for a few seconds. Use it when the page is stuck. |
| **Check for updates** | Asks GitHub whether a newer TVWallE release is out, and says which. |
| **Update now** | Installs the latest signed release now instead of at the nightly restart. The screen restarts once it's installed (a few minutes); this page comes back by itself. See [UPDATES.md](UPDATES.md). |

The top card shows what is on the screen right now (title and address) and whether that page was opened from here. The **Software** card shows the installed TVWallE version, the last update (when, how it started, what happened) and the last check.

## Set the password (once per kiosk)

On the kiosk, or from the Mac over SSH:

```bash
ssh -t tvwalle-vm sudo tvwalle-passwd
```

It asks for the password twice (8 characters at least). Run it again any time to change it. The page refuses every sign-in until a password is set.

## Open it

In a browser on the office network: `http://<kiosk address>:8080` (the setup script prints the address at the end; for the dev VM it's http://192.168.64.3:8080). Sign in with the password. You stay signed in for 12 hours, or until you sign out or the controller restarts.

**Without the password, from a computer with SSH access to the kiosk:**

```bash
ssh -N -L 8080:127.0.0.1:8080 tvwalle-vm
```

Then open http://localhost:8080. Requests that reach the controller from the kiosk itself skip the password, since anyone who can SSH in already has full control of the machine.

## How it works

- **Firefox runs with Marionette on** (`--marionette`), Firefox's own remote-control protocol, listening on 127.0.0.1:2828 only. `kiosk-session` also writes `user_pref("remote.prefs.recommended", false)` so Marionette doesn't switch on the preferences meant for automated tests.
- **The controller** is one Python file using only the standard library (`/usr/local/lib/tvwalle/controller.py`), run by `tvwalle-controller.service` as the user `tvwalle-ctl`. Each button opens a short Marionette session, does its one thing, and closes it.
- **The opened page** is saved in `/var/lib/tvwalle/url`. `kiosk-session` opens that page first, then `KIOSK_URL`, then the TV dashboard.
- **Restart browser** runs `systemctl restart tvwalle-kiosk.service`, and **Update now** `systemctl start tvwalle-update.service`. The polkit rule `/etc/polkit-1/rules.d/50-tvwalle-controller.rules` lets `tvwalle-ctl` do exactly those two things and nothing else, so the service runs with `NoNewPrivileges` and a read-only system. The update itself runs as root in its own service, so it keeps going while the setup restarts the controller.
- **Check for updates** runs the updater's check (`tvwalle-update --check --json`) as `tvwalle-ctl`: it downloads the latest `release.json` and checks its signature, and changes nothing.

## Security

- **The password is stored only as a hash** (scrypt) in `/etc/tvwalle/controller-password`, readable by root and the controller only.
- **Five wrong passwords from one address in ten minutes** lock that address out for ten minutes.
- **Only this page can press the buttons.** Every action must be a JSON request from the page's own origin. Another website, including the page shown on the TV, can't trigger one.
- **Marionette is local only** (127.0.0.1). Anyone with a shell on the kiosk can drive the browser; that is the same set of people who are admins anyway.
- ⚠️ **The page is plain HTTP.** On the office network the password crosses the wire unencrypted. That's fine for a trusted LAN; HTTPS can come later if the kiosk ever sits on a network you don't trust. Limiting port 8080 to the office network is part of phase 4 (firewall).

## Files

| Path | What |
|---|---|
| `/usr/local/lib/tvwalle/controller.py` | the controller (also `--set-password`, `--run-script`) |
| `/usr/local/share/tvwalle/controller/` | the page: `index.html`, `app.js`, `style.css` |
| `/usr/local/sbin/tvwalle-passwd` | sets the password |
| `/etc/systemd/system/tvwalle-controller.service` | the service |
| `/etc/polkit-1/rules.d/50-tvwalle-controller.rules` | permission to restart the kiosk and start an update |
| `/var/lib/tvwalle/update-status.json` | the last update's outcome (written by the updater) |
| `/etc/tvwalle/controller-password` | password hash |
| `/var/lib/tvwalle/url` | the page opened from the controller, if any |

## Troubleshooting

- Controller log: `journalctl -u tvwalle-controller -f` (request lines only; passwords are never logged).
- "The browser isn't answering": press **Restart browser**. If that doesn't help, `journalctl -u tvwalle-kiosk -b`.
- Look inside the page on the TV (any user on the kiosk can do this):

  ```bash
  python3 /usr/local/lib/tvwalle/controller.py --run-script 'return document.title'
  ```

  The script runs in the page and must `return` its answer. For something that takes time, return a Promise; `await` at the top level is not allowed.
