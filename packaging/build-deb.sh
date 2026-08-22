#!/usr/bin/env bash
# Wrap the built binary in a .deb.
#
# Why a package at all, when there is already a binary on the release page: a
# downloaded binary arrives without its executable bit, because HTTP cannot
# carry one and browsers deliberately will not add it. So a bare binary always
# costs the user a chmod, or a trip to a file manager's properties dialog,
# before it will start. A package sidesteps that entirely — double-click,
# install, and it is in the applications menu like anything else.
#
# Usage: packaging/build-deb.sh <version> <path-to-built-binary> <output-dir>
set -euo pipefail

VERSION="${1:?usage: build-deb.sh VERSION BINARY OUTDIR}"
BINARY="${2:?usage: build-deb.sh VERSION BINARY OUTDIR}"
OUTDIR="${3:?usage: build-deb.sh VERSION BINARY OUTDIR}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# The one-file bundle lives in /usr/lib, not /usr/bin: /usr/bin is for things
# on the PATH, and this is a 70 MB self-extracting blob with a launcher name.
install -Dm755 "$BINARY"                      "$STAGE/usr/lib/anydmx/AnyDMX"
install -Dm644 "$ROOT/packaging/anydmx.desktop" \
                                              "$STAGE/usr/share/applications/anydmx.desktop"
install -Dm644 "$ROOT/assets/AnyDMX.png" \
       "$STAGE/usr/share/icons/hicolor/256x256/apps/anydmx.png"
install -Dm644 "$ROOT/LICENSE"                "$STAGE/usr/share/doc/anydmx/copyright"

mkdir -p "$STAGE/usr/bin"
ln -s ../lib/anydmx/AnyDMX "$STAGE/usr/bin/anydmx"

mkdir -p "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/" "$ROOT/packaging/debian/control.in" > "$STAGE/DEBIAN/control"

mkdir -p "$OUTDIR"
DEB="$OUTDIR/anydmx_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB"
dpkg-deb --info "$DEB"
dpkg-deb --contents "$DEB"
echo "built $DEB"
