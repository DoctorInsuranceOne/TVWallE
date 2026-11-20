#!/usr/bin/env python3
"""TVWallE web controller.

A small web page, served by the kiosk on port 8080, to refresh the TV's page,
open another page, go back to the default page, or restart the browser.

How it works:
- It drives Firefox through Marionette, Firefox's remote-control protocol. The
  kiosk starts Firefox with --marionette, which listens on 127.0.0.1:2828 only.
- A page opened from here is remembered in /var/lib/tvwalle/url, so a restart
  or reboot comes back to it. "Back to the default page" forgets it.
- "Restart browser" runs `systemctl restart tvwalle-kiosk.service`, and
  "Update now" `systemctl start tvwalle-update.service`; a polkit rule lets
  this service's user do exactly those two things and nothing else.
- "Check for updates" asks GitHub, through the updater (tvwalle-update
  --check), whether a newer signed release is out (docs/UPDATES.md).
- It asks for the password set with `sudo tvwalle-passwd`. Requests from the
  kiosk itself (127.0.0.1, e.g. through an SSH tunnel) skip it: whoever can SSH
  in already has root.
- POSTs must be JSON and come from this page's own origin, so no other web page
  (including one shown on the TV) can press its buttons.

Python standard library only. Installed by TVWallE provision/install.sh.

Maintenance:
  controller.py --set-password       set the password (what tvwalle-passwd runs)
  controller.py --hash-password      read a password on standard input and print
                                     its hash, as the password file holds it
                                     (tools/build-installer.sh)
  controller.py --run-script 'JS'    run JavaScript in the kiosk page, print the
                                     result (diagnostics; e.g. 'return document.title')
  controller.py --watchdog           one watchdog check (tvwalle-watchdog.timer,
                                     every minute): three missed checks in a row
                                     restart the browser
"""

import argparse
import base64
import grp
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

LISTEN = ("0.0.0.0", 8080)
MARIONETTE = ("127.0.0.1", 2828)
KIOSK_CONF = "/etc/tvwalle/kiosk.conf"
SAVED_URL = "/var/lib/tvwalle/url"
PASSWORD_FILE = "/etc/tvwalle/controller-password"
CONTROLLER_GROUP = "tvwalle-ctl"
STATIC_DIR = "/usr/local/share/tvwalle/controller"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
FALLBACK_URL = "https://crm.az.diofl.net/#/tv"
KIOSK_UNIT = "tvwalle-kiosk.service"

# Updates (docs/UPDATES.md)
UPDATE_UNIT = "tvwalle-update.service"
UPDATER = "/usr/local/lib/tvwalle/tvwalle-update"
VERSION_FILE = "/opt/tvwalle/VERSION"
UPDATE_STATUS = "/var/lib/tvwalle/update-status.json"
DEPLOYED_BY_HAND = "/var/lib/tvwalle/deployed-by-hand"
CHECK_TIMEOUT = 90

SESSION_SECONDS = 12 * 3600
MAX_URL_LENGTH = 2048
MAX_BODY = 8 * 1024
# Five wrong passwords from one address within ten minutes locks it out for ten.
LOGIN_FAILS_ALLOWED = 5
LOGIN_WINDOW_SECONDS = 600

# Watchdog: a browser that misses this many checks in a row (one a minute) is
# restarted. A kiosk started less than WATCHDOG_GRACE_SECONDS ago isn't checked.
WATCHDOG_STATE = "/var/lib/tvwalle/watchdog-misses"
WATCHDOG_MISSES = 3
WATCHDOG_GRACE_SECONDS = 120

# scrypt: 16 MiB of memory per check, a fraction of a second on the kiosk.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 8


# --- Marionette ---------------------------------------------------------------

class MarionetteError(Exception):
    pass


class Marionette:
    """One short connection per action: connect, open a session, run the
    commands, close. Marionette allows one session at a time, so the lock keeps
    two web requests from colliding."""

    _lock = threading.Lock()

    def __init__(self, timeout=10):
        self.timeout = timeout
        self.sock = None
        self.msg_id = 0

    def __enter__(self):
        if not Marionette._lock.acquire(timeout=20):
            raise MarionetteError("The kiosk is busy with another request. Try again.")
        try:
            try:
                self.sock = socket.create_connection(MARIONETTE, timeout=self.timeout)
                self._read()  # handshake: {"applicationType": "gecko", "marionetteProtocol": 3}
            except (OSError, ValueError):
                raise MarionetteError("The browser isn't answering.")
            # "none": navigate and refresh return at once instead of waiting
            # for the page to finish loading.
            self.call("WebDriver:NewSession", {"pageLoadStrategy": "none"})
        except Exception:
            self._close()
            Marionette._lock.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.sock:
                try:
                    self.call("WebDriver:DeleteSession", {})
                except Exception:
                    pass
        finally:
            self._close()
            Marionette._lock.release()

    def call(self, name, params=None):
        self.msg_id += 1
        payload = json.dumps([0, self.msg_id, name, params or {}]).encode()
        try:
            self.sock.sendall(str(len(payload)).encode() + b":" + payload)
            while True:
                msg = self._read()
                if isinstance(msg, list) and len(msg) == 4 and msg[0] == 1 and msg[1] == self.msg_id:
                    break
        except (OSError, ValueError) as err:
            raise MarionetteError(f"The browser stopped answering ({err}).")
        error, result = msg[2], msg[3]
        if error:
            raise MarionetteError(f"{name} failed: {error.get('error')}: {error.get('message')}")
        return result

    def _read(self):
        # A message is "<length>:<json>".
        length = b""
        while True:
            ch = self.sock.recv(1)
            if not ch:
                raise MarionetteError("The browser closed the connection.")
            if ch == b":":
                break
            length += ch
            if len(length) > 10:
                raise MarionetteError("The browser sent something unexpected.")
        size = int(length)
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise MarionetteError("The browser closed the connection.")
            data += chunk
        return json.loads(data)

    def _close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


# --- URLs ---------------------------------------------------------------------

def default_url():
    """KIOSK_URL from /etc/tvwalle/kiosk.conf (the page the setup put there)."""
    url = ""
    try:
        with open(KIOSK_CONF, encoding="utf-8") as f:
            for line in f:
                if line.startswith("KIOSK_URL="):
                    url = line[len("KIOSK_URL="):].strip()
    except OSError:
        pass
    return url or FALLBACK_URL


def saved_url():
    try:
        with open(SAVED_URL, encoding="utf-8") as f:
            return f.readline().strip() or None
    except OSError:
        return None


def save_url(url):
    directory = os.path.dirname(SAVED_URL)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".url-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(url + "\n")
        os.chmod(tmp, 0o644)  # kiosk-session (another user) reads it
        os.replace(tmp, SAVED_URL)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def forget_saved_url():
    try:
        os.unlink(SAVED_URL)
    except FileNotFoundError:
        pass


def url_problem(url):
    """A sentence saying what's wrong with the URL, or None."""
    if not url:
        return "Type the address of a page."
    if len(url) > MAX_URL_LENGTH:
        return f"That address is too long (over {MAX_URL_LENGTH} characters)."
    if any(c.isspace() for c in url):
        return "Addresses can't contain spaces."
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return "Use a full web address starting with https:// (or http://)."
    return None


# --- Password -----------------------------------------------------------------

def password_is_set():
    try:
        return os.path.getsize(PASSWORD_FILE) > 0
    except OSError:
        return False


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=SCRYPT_MAXMEM, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def check_password(password):
    try:
        with open(PASSWORD_FILE, encoding="utf-8") as f:
            stored = f.readline().strip()
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), maxmem=SCRYPT_MAXMEM, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (OSError, ValueError):
        return False


def set_password():
    """What `sudo tvwalle-passwd` runs. Reads the password from the keyboard,
    or one line from standard input when that is not a terminal."""
    if os.geteuid() != 0:
        sys.exit("Run it as root: sudo tvwalle-passwd")
    if sys.stdin.isatty():
        import getpass
        password = getpass.getpass("New controller password: ")
        if getpass.getpass("Type it again: ") != password:
            sys.exit("The two passwords differ. Nothing changed.")
    else:
        password = sys.stdin.readline().rstrip("\n")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Use at least {MIN_PASSWORD_LENGTH} characters. Nothing changed.")

    gid = grp.getgrnam(CONTROLLER_GROUP).gr_gid
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(PASSWORD_FILE), prefix=".controller-password-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(hash_password(password) + "\n")
        os.chown(tmp, 0, gid)
        os.chmod(tmp, 0o640)
        os.replace(tmp, PASSWORD_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    print("Password set. Anyone signed in to the controller stays signed in until they sign out.")


def print_password_hash():
    """For tools/build-installer.sh: one line on standard input in, its hash out,
    so the installer USB carries the hash and never the password."""
    password = sys.stdin.readline().rstrip("\n")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    print(hash_password(password))


# --- Sessions and sign-in limits ----------------------------------------------

_state_lock = threading.Lock()
_sessions = {}  # token -> expiry (monotonic seconds)
_login_fails = {}  # client address -> [failure times]


def new_session():
    token = secrets.token_urlsafe(32)
    with _state_lock:
        _sessions[token] = time.monotonic() + SESSION_SECONDS
    return token


def session_valid(token):
    if not token:
        return False
    with _state_lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if expiry < time.monotonic():
            del _sessions[token]
            return False
        return True


def end_session(token):
    with _state_lock:
        _sessions.pop(token, None)


def login_locked(addr):
    now = time.monotonic()
    with _state_lock:
        fails = [t for t in _login_fails.get(addr, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_fails[addr] = fails
        return len(fails) >= LOGIN_FAILS_ALLOWED


def note_login_failure(addr):
    with _state_lock:
        _login_fails.setdefault(addr, []).append(time.monotonic())


# --- Browser actions ----------------------------------------------------------

def browser_state():
    try:
        with Marionette() as m:
            url = m.call("WebDriver:GetCurrentURL").get("value")
            title = m.call("WebDriver:GetTitle").get("value")
        return {"answering": True, "url": url, "title": title}
    except MarionetteError as err:
        return {"answering": False, "error": str(err)}


def restart_browser():
    result = subprocess.run(["systemctl", "restart", KIOSK_UNIT], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"The kiosk could not restart the browser. {detail}".strip())


# --- Updates ------------------------------------------------------------------

# The last "Check for updates" answer, shown until the next one (or a restart).
_last_check = {}


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def software_state():
    try:
        with open(UPDATE_STATUS, encoding="utf-8") as f:
            last_update = json.load(f)
    except (OSError, ValueError):
        last_update = None
    running = subprocess.run(["systemctl", "is-active", UPDATE_UNIT],
                             capture_output=True, text=True, timeout=10).stdout.strip() in ("activating", "active")
    return {
        "version": read_text(VERSION_FILE),
        "deployed_by_hand": read_text(DEPLOYED_BY_HAND) or None,
        "last_update": last_update,
        "updating": running,
        "last_check": _last_check or None,
    }


def check_for_update():
    try:
        out = subprocess.run(["/usr/bin/python3", UPDATER, "--check", "--json"],
                             capture_output=True, text=True, timeout=CHECK_TIMEOUT)
        result = json.loads(out.stdout)
    except subprocess.TimeoutExpired:
        result = {"ok": False, "error": "GitHub didn't answer in time. Try again later."}
    except (OSError, ValueError):
        result = {"ok": False, "error": "The updater isn't working on this kiosk."}
    result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _last_check.clear()
    _last_check.update(result)
    return result


def start_update():
    result = subprocess.run(["systemctl", "start", "--no-block", UPDATE_UNIT],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"The kiosk could not start the update. {detail}".strip())


# --- Watchdog -----------------------------------------------------------------

def marionette_greets(timeout=10):
    """Does Firefox's Marionette server greet a new connection? The greeting
    comes from Firefox's main thread, so a hung browser doesn't send it. No
    session is opened, so this never collides with a button being pressed."""
    try:
        with socket.create_connection(MARIONETTE, timeout=timeout) as sock:
            sock.settimeout(timeout)
            data = b""
            while b"applicationType" not in data:
                chunk = sock.recv(256)
                if not chunk or len(data) > 4096:
                    return False
                data += chunk
            return True
    except OSError:
        return False


def kiosk_running_seconds():
    """How long tvwalle-kiosk.service has been active, or None when it isn't."""
    out = subprocess.run(
        ["systemctl", "show", KIOSK_UNIT, "-p", "ActiveState", "-p", "ActiveEnterTimestampMonotonic"],
        capture_output=True, text=True, timeout=10).stdout
    fields = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if fields.get("ActiveState") != "active":
        return None
    # systemd's monotonic clock is the same CLOCK_MONOTONIC as time.monotonic().
    return time.monotonic() - int(fields.get("ActiveEnterTimestampMonotonic") or 0) / 1_000_000


def read_misses():
    try:
        with open(WATCHDOG_STATE, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def write_misses(count):
    with open(WATCHDOG_STATE, "w", encoding="utf-8") as f:
        f.write(f"{count}\n")


def watchdog():
    running = kiosk_running_seconds()
    if running is None or running < WATCHDOG_GRACE_SECONDS:
        # Not running (systemd restarts a crash itself) or just started.
        write_misses(0)
        return
    if marionette_greets():
        write_misses(0)
        return
    misses = read_misses() + 1
    if misses < WATCHDOG_MISSES:
        write_misses(misses)
        print(f"Firefox didn't answer ({misses} of {WATCHDOG_MISSES}).", flush=True)
        return
    write_misses(0)
    print(f"Firefox didn't answer {WATCHDOG_MISSES} checks in a row; restarting the browser.", flush=True)
    restart_browser()


# --- HTTP ---------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "TVWallE"
    sys_version = ""

    def log_message(self, fmt, *args):
        # The request line only; bodies (passwords) are never logged.
        print(f"{self.client_address[0]} {fmt % args}", flush=True)

    # Who is asking

    def is_local(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "tvwalle_session":
                return value
        return None

    def authenticated(self):
        return self.is_local() or session_valid(self.cookie_token())

    # Responses

    def send_json(self, status, data, extra_headers=()):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, message):
        self.send_json(status, {"ok": False, "error": message})

    def done(self, message, extra_headers=()):
        self.send_json(200, {"ok": True, "message": message}, extra_headers)

    def send_static(self, name, content_type):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as f:
                body = f.read()
        except OSError:
            self.fail(404, "Not found.")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    # GET

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            self.send_static(*STATIC_FILES[path])
        elif path == "/api/status":
            self.status()
        else:
            self.fail(404, "Not found.")

    def status(self):
        data = {
            "authenticated": self.authenticated(),
            "local": self.is_local(),
            "password_set": password_is_set(),
            "hostname": socket.gethostname(),
        }
        if data["authenticated"]:
            data["browser"] = browser_state()
            data["saved_url"] = saved_url()
            data["default_url"] = default_url()
            data["software"] = software_state()
        self.send_json(200, data)

    # POST

    def do_POST(self):
        path = urlsplit(self.path).path
        actions = {
            "/api/login": self.login,
            "/api/logout": self.logout,
            "/api/refresh": self.refresh,
            "/api/open": self.open_url,
            "/api/reset": self.reset,
            "/api/restart": self.restart,
            "/api/update/check": self.update_check,
            "/api/update/start": self.update_start,
        }
        action = actions.get(path)
        if action is None:
            self.fail(404, "Not found.")
            return

        # Only this page may press the buttons: a JSON body (other sites can't
        # send one without the kiosk's permission) from this page's own origin.
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self.fail(415, "Send JSON.")
            return
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self.fail(403, "Requests from other sites are refused.")
            return

        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY:
            self.fail(413, "The request is too large.")
            return
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self.fail(400, "The request isn't valid JSON.")
            return

        if path != "/api/login" and not self.authenticated():
            self.fail(401, "Sign in first.")
            return

        try:
            action(body)
        except MarionetteError as err:
            self.fail(502, f"{err} Try Restart browser.")
        except Exception as err:  # keep serving; say what happened
            print(f"error in {path}: {err!r}", flush=True)
            self.fail(500, str(err) or "Something went wrong on the kiosk.")

    def login(self, body):
        addr = self.client_address[0]
        if self.is_local():
            self.done("Signed in.")
            return
        if not password_is_set():
            self.fail(403, "No password is set yet. On the kiosk, run: sudo tvwalle-passwd")
            return
        if login_locked(addr):
            self.fail(429, "Too many wrong passwords. Wait ten minutes and try again.")
            return
        password = body.get("password")
        if not isinstance(password, str) or not check_password(password):
            note_login_failure(addr)
            time.sleep(1)
            self.fail(401, "Wrong password.")
            return
        cookie = (f"tvwalle_session={new_session()}; Path=/; Max-Age={SESSION_SECONDS}; "
                  f"HttpOnly; SameSite=Strict")
        self.done("Signed in.", [("Set-Cookie", cookie)])

    def logout(self, body):
        end_session(self.cookie_token())
        self.done("Signed out.", [("Set-Cookie", "tvwalle_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")])

    def refresh(self, body):
        with Marionette() as m:
            m.call("WebDriver:Refresh")
        self.done("Page refreshed.")

    def open_url(self, body):
        url = body.get("url")
        url = url.strip() if isinstance(url, str) else ""
        problem = url_problem(url)
        if problem:
            self.fail(400, problem)
            return
        # Saved first: if the browser doesn't answer, a restart opens it anyway.
        save_url(url)
        with Marionette() as m:
            m.call("WebDriver:Navigate", {"url": url})
        self.done("Page opened. The kiosk will come back to it after a restart.")

    def reset(self, body):
        forget_saved_url()
        url = default_url()
        with Marionette() as m:
            m.call("WebDriver:Navigate", {"url": url})
        self.done("Back on the default page.")

    def restart(self, body):
        restart_browser()
        self.done("Browser restarting. The screen comes back in a few seconds.")

    def update_check(self, body):
        result = check_for_update()
        if not result.get("ok"):
            self.fail(502, result.get("error") or "The check didn't work.")
        elif result.get("newer"):
            self.done(f"TVWallE {result['latest']} is out. Press Update now to install it.")
        else:
            self.done(f"Up to date: {result['latest']} is the latest release.")

    def update_start(self, body):
        if software_state()["updating"]:
            self.fail(409, "An update is already running.")
            return
        start_update()
        self.done("Updating. The kiosk downloads the release, checks its signature and installs it; "
                  "the screen and this page come back by themselves in a few minutes.")


# --- Main ---------------------------------------------------------------------

def run_script(script):
    with Marionette() as m:
        result = m.call("WebDriver:ExecuteScript", {"script": script, "args": []})
    print(json.dumps(result.get("value"), indent=2))


def main():
    parser = argparse.ArgumentParser(description="TVWallE web controller")
    parser.add_argument("--set-password", action="store_true", help="set the controller password (root)")
    parser.add_argument("--hash-password", action="store_true",
                        help="print the hash of a password read on standard input")
    parser.add_argument("--run-script", metavar="JS", help="run JavaScript in the kiosk page and print the result")
    parser.add_argument("--watchdog", action="store_true", help="one watchdog check (tvwalle-watchdog.timer)")
    args = parser.parse_args()

    if args.set_password:
        set_password()
        return
    if args.hash_password:
        print_password_hash()
        return
    if args.watchdog:
        watchdog()
        return
    if args.run_script:
        run_script(args.run_script)
        return

    server = ThreadingHTTPServer(LISTEN, Handler)
    server.daemon_threads = True
    print(f"TVWallE controller listening on http://{LISTEN[0]}:{LISTEN[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
