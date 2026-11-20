#!/usr/bin/env bash
# Builds the TVWallE installer: the stock Ubuntu Server ISO plus TVWallE's
# autoinstall file and a copy of this repo, as one image to write to a USB.
#
#   tools/build-installer.sh amd64     for a real PC (Intel/AMD)
#   tools/build-installer.sh arm64     for a test VM on the Mac (UTM)
#
# Options:
#   --iso PATH        the stock ISO (default: ~/Downloads/ubuntu-26.04.1-live-server-<arch>.iso)
#   --new-settings    type the passwords and the Wi-Fi again
#
# The first build asks for the tvadmin console password, the controller
# password and the Wi-Fi. Their hashes, and the Wi-Fi name and password, are
# saved in build/installer-settings.json (readable by you only) for the next
# build. The result is build/tvwalle-installer-<arch>.iso.
#
# Needs Docker (the ISO tools run in a container; nothing is installed on the
# Mac) and the TVWallE SSH key, ~/.ssh/tvwalle_ed25519.pub (docs/VM_SETUP.md).
# Guide: docs/INSTALLER.md
set -euo pipefail

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
arch=""
iso=""
new_settings=""
while [ $# -gt 0 ]; do
  case "$1" in
    arm64|amd64) arch="$1" ;;
    --iso) [ $# -ge 2 ] || usage; iso="$2"; shift ;;
    --new-settings) new_settings="--new-settings" ;;
    *) usage ;;
  esac
  shift
done
[ -n "$arch" ] || usage

iso="${iso:-$HOME/Downloads/ubuntu-26.04.1-live-server-$arch.iso}"
[ -f "$iso" ] || die "No ISO at $iso. Download Ubuntu Server 26.04.1 for $arch, or pass --iso."
iso="$(cd "$(dirname "$iso")" && pwd)/$(basename "$iso")"
pubkey_file="$HOME/.ssh/tvwalle_ed25519.pub"
[ -f "$pubkey_file" ] || die "No $pubkey_file. Make the TVWallE SSH key first (docs/VM_SETUP.md, step 5)."
command -v docker >/dev/null || die "Docker is needed (the ISO tools run in a container)."
docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker Desktop and try again."

build="$repo/build"
stage="$build/stage-$arch"
mkdir -p "$build"
# The staged files hold the Wi-Fi password and the password hashes: never left behind.
trap 'rm -rf "$stage"' EXIT
rm -rf "$stage"
mkdir -p "$stage/tvwalle"

# What goes on the USB: the repo as git sees it (tracked and new files; nothing
# .gitignore lists, so no build output or ISOs), plus the version it was built from.
rsync -a --filter=':- .gitignore' --exclude .git "$repo/" "$stage/tvwalle/"
version="$(git -C "$repo" describe --always --dirty 2>/dev/null || echo unknown)"
built="$(date -u +%Y-%m-%dT%H:%MZ)"
# Built from a release tag with nothing changed: the release's number on the
# first line, so the first start doesn't download that same release again.
# Anything else counts as older than every release (docs/UPDATES.md).
release_tag="$(git -C "$repo" describe --exact-match --tags --match 'v[0-9]*' 2>/dev/null || true)"
if [ -n "$release_tag" ] && [ -z "$(git -C "$repo" status --porcelain)" ]; then
  printf '%s\nrelease %s, built into the USB %s\n' "${release_tag#v}" "$release_tag" "$built" > "$stage/tvwalle/VERSION"
else
  printf '%s, built %s\n' "$version" "$built" > "$stage/tvwalle/VERSION"
fi

echo "Preparing the build tools (Docker image tvwalle-isotools)..."
docker build -q -t tvwalle-isotools "$repo/tools/isotools" >/dev/null

tty_flag=""
[ -t 0 ] && tty_flag="-t"
docker run --rm -i $tty_flag \
  -e SSH_PUBKEY="$(cat "$pubkey_file")" \
  -v "$repo:/repo:ro" \
  -v "$(dirname "$iso"):/iso:ro" \
  -v "$build:/build" \
  tvwalle-isotools \
  python3 /repo/tools/isotools/assemble.py \
    --arch "$arch" \
    --iso "/iso/$(basename "$iso")" \
    --stage "/build/stage-$arch" \
    --out "/build/tvwalle-installer-$arch.iso" \
    --settings /build/installer-settings.json \
    $new_settings

chmod 600 "$build/installer-settings.json" 2>/dev/null || true
echo
echo "Next: write build/tvwalle-installer-$arch.iso to a USB stick (docs/INSTALLER.md)."
