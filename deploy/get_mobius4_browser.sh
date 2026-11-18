#!/usr/bin/env bash
# deploy/get_mobius4_browser.sh -- download the Mobius4 TUI Resource Browser (optional inspection tool).
# Third-party binary (IoTKETI/mobius4 release); it is NOT bundled in this repository.
#   ./deploy/get_mobius4_browser.sh              # download + verify + extract into tools/mobius4-browser/
#   ./deploy/get_mobius4_browser.sh --print-url  # only print the asset URL for this platform
# Env: MOBIUS4_BROWSER_VERSION (default 0.15.0, the version tested here), MOBIUS4_BROWSER_TAG (default resource_browser),
#      MOBIUS4_BROWSER_BASE_URL / MOBIUS4_BROWSER_DEST (testing only),
#      MOBIUS4_UNAME_S / MOBIUS4_UNAME_M (override platform detection), MOBIUS4_BROWSER_SKIP_PIN=1 (skip the pinned hash).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${MOBIUS4_BROWSER_VERSION:-0.15.0}"
TAG="${MOBIUS4_BROWSER_TAG:-resource_browser}"
OS="${MOBIUS4_UNAME_S:-$(uname -s)}"; ARCH="${MOBIUS4_UNAME_M:-$(uname -m)}"
case "$OS-$ARCH" in
  Linux-x86_64)               PLAT=linux-x86_64 ;;
  Linux-aarch64|Linux-arm64)  PLAT=linux-arm64 ;;
  Darwin-arm64)               PLAT=macos-arm64 ;;
  Darwin-x86_64)              PLAT=macos-x86_64 ;;
  *) echo "unsupported platform: $OS $ARCH (Windows: download the zip manually from the Mobius4 releases page)" >&2; exit 2 ;;
esac
ASSET="mobius4-browser-$VERSION-$PLAT.zip"
BASE="${MOBIUS4_BROWSER_BASE_URL:-https://github.com/IoTKETI/mobius4/releases/download/$TAG}"
URL="$BASE/$ASSET"
[ "${1:-}" = "--print-url" ] && { echo "$URL"; exit 0; }

# known-good SHA-256 of the asset that was tested (0.15.0, linux x86_64)
PIN=""; [ "$VERSION-$PLAT" = "0.15.0-linux-x86_64" ] && PIN="7846d9190f69409872daa91f8655f7916474cacdd5365878d833a355e6a59361"
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }

DEST="${MOBIUS4_BROWSER_DEST:-$ROOT/tools/mobius4-browser}"; TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "downloading $URL"
curl -fsSL -o "$TMP/$ASSET" "$URL" || { echo "download failed (asset missing for this platform/version?)" >&2; exit 1; }
GOT="$(sha256 "$TMP/$ASSET")"
if curl -fsSL -o "$TMP/$ASSET.sha256" "$URL.sha256" 2>/dev/null; then
  WANT="$(cut -d' ' -f1 "$TMP/$ASSET.sha256")"
  [ "$GOT" = "$WANT" ] || { echo "SHA-256 mismatch vs published .sha256 ($GOT != $WANT)" >&2; exit 1; }
  echo "published SHA-256 matches"
else
  echo "warning: no .sha256 published for this asset; verify it yourself: $GOT" >&2
fi
if [ -n "$PIN" ] && [ -z "${MOBIUS4_BROWSER_SKIP_PIN:-}" ]; then
  [ "$GOT" = "$PIN" ] || { echo "SHA-256 differs from the pinned tested value; set MOBIUS4_BROWSER_SKIP_PIN=1 to accept a re-release" >&2; exit 1; }
  echo "pinned SHA-256 matches"
fi
rm -rf "$DEST"; mkdir -p "$DEST"; unzip -q "$TMP/$ASSET" -d "$DEST"
BIN="$(find "$DEST" -maxdepth 2 -type f -name 'mobius4-browser*' ! -name '*.zip' ! -name '*.so*' | head -1)"
[ -n "$BIN" ] || { echo "executable not found in the archive" >&2; exit 1; }
chmod +x "$BIN"
[ "$OS" = "Darwin" ] && xattr -r -d com.apple.quarantine "$DEST" 2>/dev/null || true
echo "installed: $BIN"
