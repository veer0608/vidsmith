#!/usr/bin/env bash
# Fetch the two things a Linux host does not come with: ffmpeg, and the fonts
# the themes name. Both land in gitignored directories the code already looks in.
#
# Run at deploy time. Hosts with no package manager (Render's native runtime,
# most PaaS build steps) can still do this, which is the point.
#
# ffmpeg is fatal if it fails - there is no video without it. Fonts are not:
# a missing face degrades to whatever the host has, and failing a deploy over
# typography would be worse than shipping it.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="$root/bin"
fonts="$root/assets/fonts"
mkdir -p "$bin" "$fonts"

FFMPEG_URL="${FFMPEG_URL:-https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz}"

# ---- ffmpeg (required) ------------------------------------------------------ #
if [ ! -x "$bin/ffmpeg" ]; then
  echo "fetching ffmpeg"
  tmp="$(mktemp -d)"
  curl -fsSL --retry 3 --retry-delay 2 "$FFMPEG_URL" -o "$tmp/ffmpeg.tar.xz"
  tar -xJf "$tmp/ffmpeg.tar.xz" -C "$tmp"
  found="$(find "$tmp" -type f -name ffmpeg -perm -u+x | head -1)"
  cp "$found" "$bin/ffmpeg"
  cp "$(dirname "$found")/ffprobe" "$bin/ffprobe"
  chmod +x "$bin/ffmpeg" "$bin/ffprobe"
  rm -rf "$tmp"
fi
"$bin/ffmpeg" -hide_banner -version | head -1

# ---- fonts (best effort) ---------------------------------------------------- #
have_fonts() { [ -f "$fonts/DejaVuSans-Bold.ttf" ] && [ -f "$fonts/DejaVuSans.ttf" ]; }

# already installed on the image? cheapest source there is
if ! have_fonts; then
  for dir in /usr/share/fonts/truetype/dejavu /usr/share/fonts/dejavu \
             /usr/share/fonts/TTF; do
    if [ -f "$dir/DejaVuSans-Bold.ttf" ]; then
      echo "using system fonts from $dir"
      cp "$dir/DejaVuSans.ttf" "$dir/DejaVuSans-Bold.ttf" "$fonts/" || true
      break
    fi
  done
fi

if ! have_fonts; then
  for url in \
    "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip" \
    "https://downloads.sourceforge.net/project/dejavu/dejavu/2.37/dejavu-fonts-ttf-2.37.zip"
  do
    echo "trying $url"
    tmp="$(mktemp -d)"
    if curl -fsSL --retry 2 --retry-delay 2 --max-time 120 "$url" -o "$tmp/f.zip"; then
      if command -v unzip >/dev/null; then
        unzip -qo "$tmp/f.zip" -d "$tmp" || true
      else
        python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
          "$tmp/f.zip" "$tmp" || true
      fi
      find "$tmp" -name 'DejaVuSans.ttf' -exec cp {} "$fonts/" \; 2>/dev/null || true
      find "$tmp" -name 'DejaVuSans-Bold.ttf' -exec cp {} "$fonts/" \; 2>/dev/null || true
    fi
    rm -rf "$tmp"
    have_fonts && break
  done
fi

if have_fonts; then
  ls -1 "$fonts"
else
  echo "WARNING: no bundled fonts. Cards and captions will fall back to whatever"
  echo "         the host has, which may not match the theme. /healthz reports this."
fi
