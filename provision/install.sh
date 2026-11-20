#!/usr/bin/env bash
# TVWallE setup: turns a fresh Ubuntu Server 26.04 LTS into the TV kiosk.
#
# Run it as root on the machine itself:   sudo bash provision/install.sh
# From the Mac, tools/deploy.sh copies the repo over and runs it.
#
# Safe to run again: each step checks before it changes anything, and the
# kiosk URL already set on the machine (/etc/tvwalle/kiosk.conf) is never
# overwritten. Nothing here depends on where the repo copy lives afterwards.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOTFS="$REPO_DIR/rootfs"
KIOSK_USER="kiosk"
CONTROLLER_USER="tvwalle-ctl"
# Local time for the 3:30 AM updates and the 4:00 AM restart (the office's).
TIMEZONE="America/New_York"

# Mozilla's own APT repository: Firefox ESR as a regular package, not the snap.
MOZILLA_KEY_URL="https://packages.mozilla.org/apt/repo-signing-key.gpg"
MOZILLA_KEY_FINGERPRINT="35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3"
MOZILLA_KEYRING="/etc/apt/keyrings/packages.mozilla.org.asc"

export DEBIAN_FRONTEND=noninteractive

log() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Wait up to 10 minutes for another apt or dpkg run to finish instead of failing
# at once: on a PC's first boot Ubuntu's own daily apt job often starts too.
apt_get() { apt-get -o DPkg::Lock::Timeout=600 "$@"; }

require_root() {
  [ "$(id -u)" -eq 0 ] || die "Run it as root: sudo bash $0"
}

check_os() {
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = "ubuntu" ] || die "This is ${PRETTY_NAME:-not Ubuntu}. TVWallE is built for Ubuntu Server 26.04 LTS."
  if [ "${VERSION_ID:-}" != "26.04" ]; then
    printf 'WARNING: TVWallE is built for Ubuntu 26.04, this is %s. Continuing.\n' "${VERSION_ID:-unknown}"
  fi
}

setup_mozilla_repo() {
  log "Mozilla APT repository (Firefox ESR)"
  apt_get update
  apt_get install -y --no-install-recommends ca-certificates curl gpg

  local key gnupg fpr
  key="$(mktemp)"
  curl -fsSL "$MOZILLA_KEY_URL" -o "$key"

  # Check the key is Mozilla's before apt trusts it. A throwaway GnuPG home
  # keeps root's keyring untouched.
  gnupg="$(mktemp -d)"
  fpr="$(GNUPGHOME="$gnupg" gpg --batch --with-colons --import-options show-only --import "$key" 2>/dev/null \
    | awk -F: '$1 == "fpr" { print $10; exit }')"
  rm -rf "$gnupg"
  if [ "$fpr" != "$MOZILLA_KEY_FINGERPRINT" ]; then
    rm -f "$key"
    die "Mozilla's signing key has fingerprint '${fpr:-none}', expected $MOZILLA_KEY_FINGERPRINT. Not trusting it."
  fi

  install -d -m 0755 /etc/apt/keyrings
  install -m 0644 "$key" "$MOZILLA_KEYRING"
  rm -f "$key"
  install -m 0644 "$ROOTFS/etc/apt/sources.list.d/mozilla.sources" /etc/apt/sources.list.d/mozilla.sources
  install -m 0644 "$ROOTFS/etc/apt/preferences.d/mozilla" /etc/apt/preferences.d/mozilla
}

install_packages() {
  log "Packages"
  apt_get update

  local candidate
  candidate="$(apt-cache policy firefox-esr | awk '/Candidate:/ { print $2 }')"
  if [ -z "$candidate" ] || [ "$candidate" = "(none)" ]; then
    die "Mozilla's repository offers no firefox-esr for $(dpkg --print-architecture)."
  fi

  # firefox-esr   the browser
  # sway, swaybg  the display server, run by our own config as a kiosk (one
  #               full-screen window, no bar, no shortcuts). Not cage: cage
  #               0.2.1 aborts when Firefox's kiosk mode asks for full screen
  #               (wlroots assert "surface->initialized", 2026-11-19).
  # dbus-user-session, pipewire, pipewire-pulse, wireplumber
  #               the kiosk user's session bus and sound (Firefox plays sound
  #               through PipeWire; the TV can play a live call)
  # fonts-noto-*  Latin/Spanish text and emoji on the dashboard
  # libavcodec-extra
  #               FFmpeg's decoders. Firefox on Linux plays H.264/AAC (MP4)
  #               only through the system FFmpeg; without it the dashboard's
  #               logo clip (an MP4) shows as an empty grey box.
  # grim          screenshots of the kiosk screen (tvwalle-screenshot)
  # python3, polkitd
  #               the web controller, and the rule that lets it restart the
  #               kiosk (both normally present on Ubuntu Server already)
  # plymouth-label-ft
  #               Plymouth's text plugin. The splash draws no text, but without
  #               any text plugin Plymouth 24's script plugin crashes (SEGV in
  #               ply_console_viewer_hide on a viewer it never created), and the
  #               boot falls back to scrolling text. Measured on the VM.
  apt_get install -y \
    firefox-esr \
    dbus-user-session pipewire pipewire-pulse wireplumber \
    fonts-noto-core fonts-noto-color-emoji \
    libavcodec-extra \
    grim \
    python3 polkitd \
    plymouth-label-ft
  # Without recommends: sway would add a terminal (foot) and a launcher menu
  # (wmenu) a kiosk never uses. The graphics drivers (Mesa) still come in, as
  # hard dependencies of wlroots.
  apt_get install -y --no-install-recommends sway swaybg
}

remove_unused_parts() {
  log "Removing what a kiosk doesn't use"
  # kdump reserves 512 MB of memory for crash dumps nobody reads on a TV. Its
  # GRUB drop-in (crashkernel=) goes with the package, so GRUB is rebuilt.
  # Its helpers go by name (not a broad autoremove, which could take more).
  local pkg dump=""
  for pkg in kdump-tools makedumpfile kexec-tools crash; do
    if dpkg -s "$pkg" >/dev/null 2>&1; then dump="$dump $pkg"; fi
  done
  if [ -n "$dump" ]; then
    # shellcheck disable=SC2086
    apt_get purge -y $dump
    update-grub
  fi

  # snapd: nothing here is a snap. ubuntu-server-minimal DEPENDS on it, so that
  # meta-package goes too; first mark what it brought in as installed on
  # purpose, or a later autoremove could take SSH and the rest with it.
  if dpkg -s snapd >/dev/null 2>&1; then
    if dpkg -s ubuntu-server-minimal >/dev/null 2>&1; then
      local keep
      keep="$(apt-cache depends --installed ubuntu-server-minimal \
        | awk '$1 ~ /^\|?(Pre)?Depends:|^\|?Recommends:/ && $2 !~ /^</ && $2 != "snapd" { print $2 }' | sort -u)"
      # shellcheck disable=SC2086
      [ -z "$keep" ] || apt-mark manual $keep >/dev/null
    fi
    apt_get purge -y snapd
  fi
  install -m 0644 "$ROOTFS/etc/apt/preferences.d/no-snapd" /etc/apt/preferences.d/no-snapd
}

create_kiosk_user() {
  log "Kiosk user ($KIOSK_USER)"
  if ! id "$KIOSK_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /usr/sbin/nologin --comment "TVWallE kiosk" "$KIOSK_USER"
  fi
  # Nobody logs in as the kiosk user; systemd starts its session.
  passwd --lock "$KIOSK_USER" >/dev/null
}

create_controller_user() {
  log "Controller user ($CONTROLLER_USER)"
  if ! id "$CONTROLLER_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin \
      --comment "TVWallE web controller" "$CONTROLLER_USER"
  fi
}

install_files() {
  log "Kiosk files"
  install -D -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-kiosk.service" /etc/systemd/system/tvwalle-kiosk.service
  install -D -m 0644 "$ROOTFS/etc/pam.d/tvwalle-kiosk" /etc/pam.d/tvwalle-kiosk
  install -D -m 0755 "$ROOTFS/usr/local/lib/tvwalle/kiosk-session" /usr/local/lib/tvwalle/kiosk-session
  install -D -m 0644 "$ROOTFS/usr/local/share/tvwalle/sway-kiosk.conf" /usr/local/share/tvwalle/sway-kiosk.conf
  install -D -m 0755 "$ROOTFS/usr/local/bin/tvwalle-screenshot" /usr/local/bin/tvwalle-screenshot

  # Web controller
  install -D -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-controller.service" /etc/systemd/system/tvwalle-controller.service
  install -D -m 0644 "$ROOTFS/etc/polkit-1/rules.d/50-tvwalle-controller.rules" /etc/polkit-1/rules.d/50-tvwalle-controller.rules
  install -D -m 0755 "$ROOTFS/usr/local/lib/tvwalle/controller.py" /usr/local/lib/tvwalle/controller.py
  install -D -m 0755 "$ROOTFS/usr/local/sbin/tvwalle-passwd" /usr/local/sbin/tvwalle-passwd
  local f
  for f in index.html app.js style.css; do
    install -D -m 0644 "$ROOTFS/usr/local/share/tvwalle/controller/$f" "/usr/local/share/tvwalle/controller/$f"
  done

  install -d -m 0755 /etc/tvwalle
  if [ -e /etc/tvwalle/kiosk.conf ]; then
    echo "Keeping the existing /etc/tvwalle/kiosk.conf"
  else
    install -m 0644 "$ROOTFS/etc/tvwalle/kiosk.conf" /etc/tvwalle/kiosk.conf
  fi
}

# Copies a file only when it differs; succeeds (0) when it copied.
install_if_changed() {
  local mode="$1" src="$2" dst="$3"
  if [ -e "$dst" ] && cmp -s "$src" "$dst"; then
    return 1
  fi
  install -D -m "$mode" "$src" "$dst"
}

install_boot_splash() {
  log "Boot splash"
  # The logo, the same size and place at every stage: Plymouth (boot), sway's
  # background (while Firefox starts), the start page (waits for the network).
  install -D -m 0644 "$REPO_DIR/branding/splash-3840x2160.png" /usr/local/share/tvwalle/splash-3840x2160.png
  install -D -m 0644 "$ROOTFS/usr/local/share/tvwalle/start/index.html" /usr/local/share/tvwalle/start/index.html
  install -D -m 0644 "$REPO_DIR/branding/logo.png" /usr/local/share/tvwalle/start/logo.png

  # The theme travels inside the boot image (dracut copies the configured
  # Plymouth theme into the initramfs), so it's rebuilt when the theme
  # changes, and also when the image doesn't carry it (a run that stopped
  # half-way, or a kernel installed in between).
  local theme=/usr/share/plymouth/themes/tvwalle boot_changed=0
  install_if_changed 0644 "$REPO_DIR/branding/logo.png" "$theme/logo.png" && boot_changed=1
  install_if_changed 0644 "$ROOTFS$theme/tvwalle.plymouth" "$theme/tvwalle.plymouth" && boot_changed=1
  install_if_changed 0644 "$ROOTFS$theme/tvwalle.script" "$theme/tvwalle.script" && boot_changed=1
  install_if_changed 0644 "$ROOTFS/etc/plymouth/plymouthd.conf" /etc/plymouth/plymouthd.conf && boot_changed=1
  # The image must carry the theme AND the text plugin (see plymouth-label-ft).
  if [ "$boot_changed" -eq 0 ] && command -v lsinitrd >/dev/null; then
    local listing
    listing="$(mktemp)"
    lsinitrd /boot/initrd.img > "$listing" 2>/dev/null || true
    if ! grep -q 'plymouth/themes/tvwalle/tvwalle.script' "$listing" \
        || ! grep -q 'plymouth/label-freetype.so' "$listing"; then
      boot_changed=1
    fi
    rm -f "$listing"
  fi
  if [ "$boot_changed" -eq 1 ]; then
    echo "Rebuilding the boot image (initramfs) with the splash theme..."
    update-initramfs -u
  fi

  # Quiet boot: no menu, no text, "splash" on the kernel command line.
  if install_if_changed 0644 "$ROOTFS/etc/default/grub.d/90-tvwalle.cfg" /etc/default/grub.d/90-tvwalle.cfg \
      || ! grep -q 'quiet splash' /boot/grub/grub.cfg; then
    update-grub
  fi
}

install_firefox_policies() {
  log "Firefox policies"
  # Firefox reads policies.json from the distribution folder beside
  # application.ini, wherever the package put it. awk (not grep | head) reads
  # the whole list, so pipefail never sees a broken pipe.
  local ini appdir
  ini="$(dpkg -L firefox-esr | awk '/\/application\.ini$/ && !found { print; found = 1 }')"
  [ -n "$ini" ] || die "Could not find Firefox ESR's install folder (no application.ini)."
  appdir="$(dirname "$ini")"
  install -D -m 0644 "$ROOTFS/firefox/policies.json" "$appdir/distribution/policies.json"
  echo "Policies installed in $appdir/distribution/"
}

install_sound() {
  log "Sound (out of the screen only)"
  # WirePlumber in the kiosk user's session reads this: the screen's output
  # (HDMI/DisplayPort) is the default, starts at full volume, and the PC's own
  # outputs are off (docs/SOUND.md).
  install -D -m 0755 "$ROOTFS/usr/local/sbin/tvwalle-sound" /usr/local/sbin/tvwalle-sound
  local conf=/etc/wireplumber/wireplumber.conf.d/50-tvwalle.conf home
  if install_if_changed 0644 "$ROOTFS$conf" "$conf"; then
    # Volumes WirePlumber remembered under the old settings would win over the
    # new default; nobody sets one on a kiosk, so they go.
    home="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
    [ -n "$home" ] && rm -f "$home/.local/state/wireplumber/default-routes"
    # A running session picks the rules up now, not at the next restart.
    if systemctl --user -M "$KIOSK_USER@" is-active --quiet wireplumber.service 2>/dev/null; then
      systemctl --user -M "$KIOSK_USER@" restart wireplumber.service || true
    fi
  fi
}

enable_kiosk() {
  log "Kiosk service"
  systemctl daemon-reload
  systemctl set-default graphical.target
  systemctl enable tvwalle-kiosk.service
  # Restart so a re-run picks up new files right away (the page reloads).
  systemctl restart tvwalle-kiosk.service
}

enable_controller() {
  log "Web controller"
  systemctl enable tvwalle-controller.service
  systemctl restart tvwalle-controller.service
}

install_upkeep() {
  log "Updates, nightly restart, watchdog"
  # Office time, so 3:30 and 4:00 AM mean the office's night.
  if [ "$(timedatectl show -p Timezone --value)" != "$TIMEZONE" ]; then
    timedatectl set-timezone "$TIMEZONE"
  fi

  # Automatic updates: Ubuntu's security updates (already on) plus Firefox ESR,
  # installed at 3:30 AM.
  install -m 0644 "$ROOTFS/etc/apt/apt.conf.d/51tvwalle-unattended" /etc/apt/apt.conf.d/51tvwalle-unattended
  install -D -m 0644 "$ROOTFS/etc/systemd/system/apt-daily-upgrade.timer.d/tvwalle.conf" \
    /etc/systemd/system/apt-daily-upgrade.timer.d/tvwalle.conf

  # TVWallE's own updates: signed releases from GitHub, installed by the
  # nightly restart, by the controller's "Update now", and at the first start
  # (docs/UPDATES.md).
  install -D -m 0755 "$ROOTFS/usr/local/lib/tvwalle/tvwalle-update" /usr/local/lib/tvwalle/tvwalle-update
  ln -sf /usr/local/lib/tvwalle/tvwalle-update /usr/local/sbin/tvwalle-update
  install -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-update.service" /etc/systemd/system/tvwalle-update.service

  # Restart every night at 4:00 AM, never in the middle of an update; TVWallE
  # updates itself first.
  install -D -m 0755 "$ROOTFS/usr/local/lib/tvwalle/nightly-reboot" /usr/local/lib/tvwalle/nightly-reboot
  install -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-reboot.service" /etc/systemd/system/tvwalle-reboot.service
  install -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-reboot.timer" /etc/systemd/system/tvwalle-reboot.timer

  # Restart the browser when Firefox stops answering (checked every minute).
  install -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-watchdog.service" /etc/systemd/system/tvwalle-watchdog.service
  install -m 0644 "$ROOTFS/etc/systemd/system/tvwalle-watchdog.timer" /etc/systemd/system/tvwalle-watchdog.timer

  systemctl daemon-reload
  systemctl restart apt-daily-upgrade.timer
  systemctl enable --now tvwalle-reboot.timer tvwalle-watchdog.timer
}

# Is there an admin (group sudo) with an SSH key? Turning passwords off
# without one would lock everyone out.
admin_has_ssh_key() {
  local user home
  for user in $(getent group sudo | cut -d: -f4 | tr ',' ' '); do
    home="$(getent passwd "$user" | cut -d: -f6)"
    if [ -n "$home" ] && [ -s "$home/.ssh/authorized_keys" ]; then
      return 0
    fi
  done
  return 1
}

secure_access() {
  log "SSH, sudo, firewall"
  # Admins (group sudo) use sudo without a password; their SSH key is the
  # credential. Checked with visudo before it takes effect.
  local tmp
  tmp="$(mktemp)"
  install -m 0440 "$ROOTFS/etc/sudoers.d/90-tvwalle-admins" "$tmp"
  visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "The sudoers rule failed visudo's check."; }
  install -m 0440 "$tmp" /etc/sudoers.d/90-tvwalle-admins
  rm -f "$tmp"

  # SSH with a key only, and only once an admin has a key.
  if admin_has_ssh_key; then
    install -m 0644 "$ROOTFS/etc/ssh/sshd_config.d/10-tvwalle.conf" /etc/ssh/sshd_config.d/10-tvwalle.conf
    sshd -t || { rm -f /etc/ssh/sshd_config.d/10-tvwalle.conf; die "sshd rejected the TVWallE settings; left SSH as it was."; }
    systemctl try-reload-or-restart ssh.service
  else
    echo "WARNING: no admin (group sudo) has an SSH key in ~/.ssh/authorized_keys yet."
    echo "         SSH keeps accepting passwords until one does; then run the setup again."
  fi

  # Firewall: SSH (22) and the controller (8080) from private (office)
  # addresses only; everything else coming in is refused. Re-running adds
  # nothing twice. The DHCP answers and replies to the kiosk's own
  # connections pass, as ufw allows them by default.
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  local net port
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16; do
    for port in 22 8080; do
      ufw allow from "$net" to any port "$port" proto tcp comment "TVWallE" >/dev/null
    done
  done
  ufw --force enable >/dev/null
}

main() {
  require_root
  check_os
  setup_mozilla_repo
  install_packages
  remove_unused_parts
  create_kiosk_user
  create_controller_user
  install_files
  install_boot_splash
  install_firefox_policies
  install_sound
  enable_kiosk
  enable_controller
  install_upkeep
  secure_access

  local url
  url="$(awk -F= '/^KIOSK_URL=/ { sub(/^KIOSK_URL=/, ""); u = $0 } END { print u }' /etc/tvwalle/kiosk.conf)"
  if [ -s /var/lib/tvwalle/url ]; then
    url="$(head -n 1 /var/lib/tvwalle/url) (opened from the controller)"
  fi
  log "Done. The kiosk is showing: ${url:-the default page}"
  echo "Kiosk log:       journalctl -u tvwalle-kiosk -f"
  echo "Web controller:  http://$(hostname -I | awk '{ print $1 }'):8080"
  if [ ! -s /etc/tvwalle/controller-password ]; then
    echo "The controller has no password yet. Set one:  sudo tvwalle-passwd"
  fi
  if ! grep -q splash /proc/cmdline; then
    echo "The boot splash shows from the next reboot."
  fi
  echo "Next restart:    $(systemctl show tvwalle-reboot.timer -p NextElapseUSecRealtime --value) (every night)"
}

main "$@"
