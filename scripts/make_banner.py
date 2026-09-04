"""Draw the YouTube channel banner in the project's own midnight theme.

2560x1440 is the recommended upload size. Everything that must survive on a
phone lives inside the centred 1546x423 safe area, so the whole lockup is
measured and fitted to that box rather than positioned by eye. The sides are
left as flat background, which is all a TV crop will show.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 2560, 1440
SAFE_W, SAFE_H = 1546, 423
PAD = 90                      # breathing room inside the safe area
BG, GOLD, TEXT, MUTED = "#0B1020", "#FFC24B", "#F5F7FA", "#8C93A8"
FONTS = Path("C:/Windows/Fonts")
# The output is tracked rather than generated on demand, because FONTS below is
# a Windows path: this script does not run on the CI machines or on a mac, so
# the only copy of the banner is the one committed beside it.
OUT = (Path(__file__).resolve().parent.parent
       / "assets" / "brand" / "banner-vidsmith.png")

WORDMARK = "vidsmith"
LINE1 = "Markdown script in, narrated and captioned video out."
LINE2 = "Runs on your own machine.  No subscription."


def width(d, text, font):
    box = d.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def fit(d, budget):
    """Largest type scale whose longest line still clears the safe area."""
    for scale in range(100, 39, -2):
        head = ImageFont.truetype(str(FONTS / "seguibl.ttf"), int(scale * 1.30))
        kick = ImageFont.truetype(str(FONTS / "bahnschrift.ttf"), int(scale * 0.46))
        small = ImageFont.truetype(str(FONTS / "bahnschrift.ttf"), int(scale * 0.40))
        text_w = max(width(d, WORDMARK, head), width(d, LINE1, kick), width(d, LINE2, small))
        mark_w = scale * 3.1
        if mark_w + scale * 0.9 + text_w <= budget:
            return scale, head, kick, small, text_w, mark_w
    raise SystemExit("no scale fits")


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    scale, head, kick, small, text_w, mark_w = fit(d, SAFE_W - PAD * 2)
    gap = scale * 0.9
    total = mark_w + gap + text_w
    x0 = W / 2 - total / 2
    cy = H / 2

    # the mark: three script lines feeding a play triangle
    u = scale / 100
    lw, lh, step = 108 * u, 19 * u, 45 * u
    for i, w in enumerate((lw, lw * 0.7, lw * 0.86)):
        y = cy - (step + lh / 2) + i * step
        d.rounded_rectangle([x0, y, x0 + w, y + lh], radius=lh / 2, fill=TEXT)
    tri = x0 + 172 * u
    d.polygon([(tri, cy - 88 * u), (tri, cy + 88 * u), (tri + 126 * u, cy)], fill=GOLD)

    tx = x0 + mark_w + gap
    d.text((tx, cy - 118 * u), WORDMARK, font=head, fill=TEXT)
    d.text((tx + 4, cy + 46 * u), LINE1, font=kick, fill=MUTED)
    d.text((tx + 4, cy + 112 * u), LINE2, font=small, fill=GOLD)

    img.save(OUT, optimize=True)
    over = total > SAFE_W - PAD * 2
    print(f"{OUT.name}  {img.size[0]}x{img.size[1]}  {OUT.stat().st_size // 1024} KB")
    print(f"scale={scale}  lockup={total:.0f}px  safe area={SAFE_W}px  overflows={over}")


if __name__ == "__main__":
    main()
