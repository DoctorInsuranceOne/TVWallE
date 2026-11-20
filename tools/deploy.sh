#!/usr/bin/env bash
# Copies this repo to a TVWallE machine and runs the setup script there.
#
#   tools/deploy.sh <ssh-host>        e.g.  tools/deploy.sh tvwalle-vm
#
# For development (the dev VM, or trying a change on one PC). PCs normally
# update themselves from signed GitHub releases (docs/UPDATES.md); a PC
# deployed by hand is marked so that its nightly update leaves it alone, until
# "Update now" (or `sudo tvwalle-update --apply`) puts a release back on it.
#
# The copy lives in /opt/tvwalle, owned by root: the same place a PC installed
# from the TVWallE USB has it (docs/INSTALLER.md). Nothing .gitignore lists
# (build output, ISOs, local notes) is copied. The SSH user must be allowed to
# use sudo without a password, which the setup itself arranges for admins
# (group sudo); on a fresh dev VM see docs/VM_SETUP.md, step 6.
set -euo pipefail

host="${1:?usage: tools/deploy.sh <ssh-host>}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(git -C "$repo" describe --always --dirty 2>/dev/null || echo unknown)"
stamp="$(date -u +%Y-%m-%dT%H:%MZ)"

rsync -rlptz --delete --no-owner --no-group \
  --filter=':- .gitignore' --exclude .git --exclude /VERSION \
  --rsync-path="sudo rsync" \
  "$repo/" "$host:/opt/tvwalle/"
ssh "$host" "printf '%s, deployed %s\n' '$version' '$stamp' | sudo tee /opt/tvwalle/VERSION >/dev/null \
  && sudo install -d -m 0755 /var/lib/tvwalle \
  && printf 'deployed by hand from a Mac: %s, %s\n' '$version' '$stamp' | sudo tee /var/lib/tvwalle/deployed-by-hand >/dev/null \
  && sudo bash /opt/tvwalle/provision/install.sh"
