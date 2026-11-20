#!/usr/bin/env bash
# Publishes a signed TVWallE release on GitHub. Every kiosk installs it at its
# next nightly restart, or at once with "Update now" in its web controller
# (docs/UPDATES.md).
#
#   tools/release.sh 1.2
#
# What it does, on the Mac:
#   1. Checks: on main, everything committed, v1.2 not used yet and newer than
#      the last release, and the release key matches release/allowed_signers
#      (the key every kiosk checks against).
#   2. Packs the commit into tvwalle.tar.gz (the repo under tvwalle/, with a
#      VERSION file saying 1.2), and writes release.json (version, commit,
#      the tarball's SHA-256).
#   3. Signs release.json with the release key (~/.ssh/tvwalle_release_ed25519):
#      release.json.sig. It asks for the key's passphrase if it has one.
#   4. Tags v1.2, pushes main and the tag, and creates the GitHub Release with
#      the three files (gh).
#
# For testing (nothing is tagged, pushed or published):
#   tools/release.sh 1.2 --out DIR --key FILE
# packs the working tree as git sees it (uncommitted changes included) into DIR,
# laid out like GitHub (DIR/latest/download/, DIR/download/v1.2/), signed with
# FILE, whose public half replaces release/allowed_signers inside the package.
# Serve DIR over HTTP and point a test kiosk at it (RELEASES_URL in
# /etc/tvwalle/update.conf).
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
key="$HOME/.ssh/tvwalle_release_ed25519"
out=""
version=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() { die "usage: tools/release.sh <version, e.g. 1.2> [--out DIR --key FILE]"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --key) key="${2:?}"; shift 2 ;;
    --out) out="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    -*) usage ;;
    *) [ -z "$version" ] || usage; version="$1"; shift ;;
  esac
done
[ -n "$version" ] || usage
[[ "$version" =~ ^[0-9]+(\.[0-9]+){0,3}$ ]] || die "A version is numbers and dots, like 1.2 (not '$version')."
tag="v$version"

[ -f "$key" ] && [ -f "$key.pub" ] || die "No release key at $key (and $key.pub). See docs/UPDATES.md, 'The release key'."
command -v ssh-keygen >/dev/null || die "ssh-keygen is missing."
pub="$(awk '{ print $1 " " $2 }' "$key.pub")"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stage="$work/stage"
mkdir -p "$stage/tvwalle"
signers="$work/allowed_signers"

newer_than() {  # newer_than A B: is version A newer than B?
  python3 -c 'import sys
a, b = ([int(x) for x in v.split(".")] for v in sys.argv[1:3])
w = max(len(a), len(b)); a += [0] * (w - len(a)); b += [0] * (w - len(b))
sys.exit(0 if a > b else 1)' "$1" "$2"
}

if [ -z "$out" ]; then
  cd "$repo"
  [ "$(git branch --show-current)" = "main" ] || die "Release from the main branch (this is '$(git branch --show-current)')."
  [ -z "$(git status --porcelain)" ] || die "Commit (or stash) everything first: a release is exactly one commit."
  ! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || die "$tag already exists. Pick the next number."
  git fetch -q --tags origin || die "Can't reach GitHub (git fetch)."
  last="$(git tag -l 'v[0-9]*' | sed 's/^v//' | grep -E '^[0-9]+(\.[0-9]+)*$' | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -n 1 || true)"
  if [ -n "$last" ] && ! newer_than "$version" "$last"; then
    die "$version isn't newer than the last release ($last). Kiosks install only newer versions."
  fi
  grep -qF "$pub" release/allowed_signers \
    || die "$key.pub isn't the key in release/allowed_signers, so no kiosk would accept this release."
  cp release/allowed_signers "$signers"
  git archive --format=tar --prefix=tvwalle/ HEAD | tar -x -C "$stage"
  commit="$(git rev-parse HEAD)"
  echo "Packing TVWallE $version from commit ${commit:0:12}."
else
  rsync -a --filter=':- .gitignore' --exclude .git "$repo/" "$stage/tvwalle/"
  mkdir -p "$stage/tvwalle/release"
  printf 'tvwalle-release namespaces="tvwalle-release" %s\n' "$pub" > "$stage/tvwalle/release/allowed_signers"
  cp "$stage/tvwalle/release/allowed_signers" "$signers"
  commit="$(git -C "$repo" describe --always --dirty 2>/dev/null || echo unknown)"
  echo "TEST build of TVWallE $version from the working tree ($commit), into $out."
fi

released="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
short="$commit"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] && short="${commit:0:12}"
printf '%s\nrelease %s, commit %s, released %s\n' "$version" "$tag" "$short" "$released" > "$stage/tvwalle/VERSION"

# The tarball: owned by root, one fixed timestamp, no macOS extras.
python3 - "$stage" "$work/tvwalle.tar.gz" "$released" <<'PY'
import calendar, gzip, os, sys, tarfile, time
stage, dest, released = sys.argv[1:4]
mtime = calendar.timegm(time.strptime(released, "%Y-%m-%dT%H:%M:%SZ"))
def clean(info):
    if os.path.basename(info.name).startswith("._") or info.name.endswith(".DS_Store"):
        return None
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = mtime
    if info.isdir() or info.mode & 0o111:
        info.mode = 0o755
    else:
        info.mode = 0o644
    return info
with gzip.GzipFile(dest, "wb", mtime=mtime) as gz:
    with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path, dirs, files in os.walk(stage):
            dirs.sort()
            for name in sorted(dirs) + sorted(files):
                full = os.path.join(path, name)
                tar.add(full, arcname=os.path.relpath(full, stage), recursive=False, filter=clean)
PY

sha="$(shasum -a 256 "$work/tvwalle.tar.gz" | awk '{ print $1 }')"
size="$(wc -c < "$work/tvwalle.tar.gz" | tr -d ' ')"
python3 - "$work/release.json" "$version" "$tag" "$commit" "$sha" "$size" "$released" <<'PY'
import json, sys
dest, version, tag, commit, sha, size, released = sys.argv[1:8]
with open(dest, "w") as f:
    json.dump({"format": 1, "version": version, "tag": tag, "commit": commit,
               "tarball": "tvwalle.tar.gz", "sha256": sha, "size": int(size),
               "released": released}, f, indent=2)
    f.write("\n")
PY

echo "Signing release.json with $key..."
ssh-keygen -q -Y sign -f "$key" -n tvwalle-release "$work/release.json"
ssh-keygen -Y verify -f "$signers" -I tvwalle-release -n tvwalle-release \
  -s "$work/release.json.sig" < "$work/release.json" >/dev/null \
  || die "The signature doesn't verify against release/allowed_signers."

files=("$work/tvwalle.tar.gz" "$work/release.json" "$work/release.json.sig")

if [ -n "$out" ]; then
  mkdir -p "$out/latest/download" "$out/download/$tag"
  cp "${files[@]}" "$out/download/$tag/"
  cp "${files[@]}" "$out/latest/download/"
  echo "Done: $out/download/$tag/ (and latest/), $size bytes, sha256 $sha"
  exit 0
fi

command -v gh >/dev/null || die "The GitHub CLI (gh) is missing."
git tag -a "$tag" -m "TVWallE $version"
git push origin main "$tag"
gh release create "$tag" "${files[@]}" --verify-tag --latest \
  --title "TVWallE $version" --generate-notes
echo "Published TVWallE $version: kiosks install it at their next nightly restart, or now with Update now."
