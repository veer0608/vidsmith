#!/usr/bin/env bash
# Fetch the two things a Linux host does not come with: ffmpeg, and the fonts
# the themes name. Both land in gitignored directories the code already looks in.
#
# Run at deploy time. Hosts with no package manager (Render's native runtime,
# most PaaS build steps) can still do this, which is the point.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="$root/bin"
fonts="$root/assets/fonts"
mkdir -p "$bin" "$fonts"

FFMPEG_URL="${FFMPEG_URL:-https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz}"
DEJAVU_URL="${DEJAVU_URL:-https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2}"

if [ ! -x "$bin/ffmpeg" ]; then
  echo "fetching ffmpeg"
  tmp="$(mktemp -d)"
  curl -fsSL "$FFMPEG_URL" -o "$tmp/ffmpeg.tar.xz"
  tar -xJf "$tmp/ffmpeg.tar.xz" -C "$tmp"
  found="$(find "$tmp" -type f -name ffmpeg -perm -u+x | head -1)"
  cp "$found" "$bin/ffmpeg"
  cp "$(dirname "$found")/ffprobe" "$bin/ffprobe"
  chmod +x "$bin/ffmpeg" "$bin/ffprobe"
  rm -rf "$tmp"
fi
"$bin/ffmpeg" -hide_banner -version | head -1

if [ ! -f "$fonts/DejaVuSans-Bold.ttf" ]; then
  echo "fetching fonts"
  tmp="$(mktemp -d)"
  curl -fsSL "$DEJAVU_URL" -o "$tmp/dejavu.tar.bz2"
  tar -xjf "$tmp/dejavu.tar.bz2" -C "$tmp"
  find "$tmp" -name 'DejaVuSans.ttf' -exec cp {} "$fonts/" \;
  find "$tmp" -name 'DejaVuSans-Bold.ttf' -exec cp {} "$fonts/" \;
  rm -rf "$tmp"
fi
ls -1 "$fonts"
