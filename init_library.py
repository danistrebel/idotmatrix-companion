"""Initialize the predefined picture library with Google Cloud, Google G, Antigravity, Red Heart, and Thumbs Up on a black background."""

import math
from pathlib import Path
from PIL import Image, ImageDraw

LIBRARY_DIR = Path(__file__).parent / "library"


def create_google_cloud_logo(output_path: Path, size: int = 512) -> None:
    """Create the Google Cloud logo on a pure black background."""
    if output_path.exists():
        return

    # Procedural Google Cloud icon fallback if bundled file is missing
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.pieslice([64, 96, 448, 416], 180, 360, fill=(234, 67, 53))
    canvas.save(output_path, "PNG")


def create_google_g_logo(output_path: Path, size: int = 512) -> None:
    """Render the four-color Google 'G' logo on a pure black background."""
    # Supersample at 4x for crisp anti-aliased edges
    scale = 2
    s = size * scale
    img = Image.new("RGB", (s, s), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = int(s * 0.08)
    outer_box = [margin, margin, s - margin, s - margin]
    thickness = int(s * 0.19)
    inner_box = [
        margin + thickness,
        margin + thickness,
        s - margin - thickness,
        s - margin - thickness,
    ]

    # Official Google colors
    red = (234, 67, 53)     # #EA4335
    yellow = (251, 188, 5)  # #FBBC05
    green = (52, 168, 83)   # #34A853
    blue = (66, 133, 244)   # #4285F4

    # Angles in PIL: 0 is 3 o'clock (right), increasing clockwise (90 is bottom, 180 is left, 270 is top)
    # Red: top arc (215 deg to 318 deg)
    draw.pieslice(outer_box, 215, 318, fill=red)
    # Yellow: left arc (145 deg to 215 deg)
    draw.pieslice(outer_box, 145, 215, fill=yellow)
    # Green: bottom arc (52 deg to 145 deg)
    draw.pieslice(outer_box, 52, 145, fill=green)
    # Blue: lower-right arc (-12 deg to 52 deg)
    draw.pieslice(outer_box, -12, 52, fill=blue)

    # Cut out the inner circle to form the ring
    draw.ellipse(inner_box, fill=(0, 0, 0))

    # Draw the horizontal blue crossbar of the 'G'
    center_y = s // 2
    bar_top = center_y - int(thickness * 0.45)
    bar_bottom = center_y + int(thickness * 0.48)
    bar_left = s // 2 + int(s * 0.01)
    bar_right = s - margin - int(s * 0.01)
    draw.rectangle([bar_left, bar_top, bar_right, bar_bottom], fill=blue)

    # Re-mask outside of outer circle so the right edge of the crossbar curves with the outer ring
    mask = Image.new("L", (s, s), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse(outer_box, fill=255)
    black_bg = Image.new("RGB", (s, s), (0, 0, 0))
    final_hi = Image.composite(img, black_bg, mask)

    final_img = final_hi.resize((size, size), Image.Resampling.LANCZOS)
    final_img.save(output_path, "PNG")


def create_antigravity_logo(output_path: Path, size: int = 512) -> None:
    """Create the Antigravity logo on a pure black background."""
    if output_path.exists():
        return

    # Procedural fallback: stylized Antigravity 'A' / delta wing in Google/Gemini blue-violet gradient on black
    img = Image.new("RGB", (size, size), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Outer delta / A shape
    draw.polygon([(size // 2, 56), (440, 432), (348, 432), (size // 2, 184), (164, 432), (72, 432)], fill=(102, 157, 246))
    # Floating antigravity diamond / spark in the center
    draw.polygon([(size // 2, 248), (308, 320), (size // 2, 392), (204, 320)], fill=(197, 138, 249))
    img.save(output_path, "PNG")


def create_red_heart_logo(output_path: Path, size: int = 512) -> None:
    """Create a vibrant red heart icon on a pure black background."""
    scale = 2
    s = size * scale
    img = Image.new("RGB", (s, s), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = s / 2.0
    cy = s * 0.50
    d = s * 0.305
    side = d * math.sqrt(2.0)
    r = side / 2.0

    main_red = (234, 35, 53)
    draw.polygon(
        [
            (cx, cy + d),
            (cx + d, cy),
            (cx, cy - d),
            (cx - d, cy),
        ],
        fill=main_red,
    )
    c1x, c1y = cx - d / 2.0, cy - d / 2.0
    c2x, c2y = cx + d / 2.0, cy - d / 2.0
    draw.ellipse([c1x - r, c1y - r, c1x + r, c1y + r], fill=main_red)
    draw.ellipse([c2x - r, c2y - r, c2x + r, c2y + r], fill=main_red)

    # Glossy highlight arc on the top-left lobe
    hl_r = r * 0.68
    hl_thick = r * 0.20
    hl_mask = Image.new("L", (s, s), 0)
    hl_draw = ImageDraw.Draw(hl_mask)
    hl_draw.pieslice([c1x - hl_r, c1y - hl_r, c1x + hl_r, c1y + hl_r], 195, 255, fill=255)
    inner_r = hl_r - hl_thick
    hl_draw.ellipse([c1x - inner_r, c1y - inner_r, c1x + inner_r, c1y + inner_r], fill=0)
    for deg in (195, 255):
        rad = math.radians(deg)
        mid_r = (hl_r + inner_r) / 2.0
        cap_r = hl_thick / 2.0
        ex = c1x + mid_r * math.cos(rad)
        ey = c1y + mid_r * math.sin(rad)
        hl_draw.ellipse([ex - cap_r, ey - cap_r, ex + cap_r, ey + cap_r], fill=255)

    hl_color = Image.new("RGB", (s, s), (255, 115, 130))
    img = Image.composite(hl_color, img, hl_mask)

    final_img = img.resize((size, size), Image.Resampling.LANCZOS)
    final_img.save(output_path, "PNG")


def create_thumbs_up_logo(output_path: Path, size: int = 512) -> None:
    """Create a Thumbs Up icon on a pure black background."""
    if output_path.exists():
        return

    # Geometric vector fallback on pure black background
    scale = 2
    s = size * scale
    img = Image.new("RGB", (s, s), (0, 0, 0))
    d = ImageDraw.Draw(img)

    gold = (251, 188, 5)
    cuff_blue = (66, 133, 244)
    cuff_btn = (232, 240, 254)

    d.rounded_rectangle([110, 430, 275, 860], radius=36, fill=cuff_blue)
    d.ellipse([168, 740, 218, 790], fill=cuff_btn)
    d.rounded_rectangle([295, 430, 740, 845], radius=60, fill=gold)

    finger_specs = [
        (430, 525, 885),
        (535, 630, 910),
        (640, 735, 885),
        (745, 845, 835),
    ]
    for y1, y2, x_right in finger_specs:
        d.rounded_rectangle([520, y1, x_right, y2], radius=int((y2 - y1) / 2), fill=gold)
    for sep_y in (528, 633, 738):
        d.rounded_rectangle([670, sep_y - 7, 920, sep_y + 7], radius=7, fill=(0, 0, 0))

    d.polygon([(295, 460), (445, 165), (595, 165), (595, 460)], fill=gold)
    d.ellipse([445, 105, 605, 255], fill=gold)
    d.rounded_rectangle([460, 155, 605, 460], radius=45, fill=gold)

    final_vec = img.resize((size, size), Image.Resampling.LANCZOS)
    final_vec.save(output_path, "PNG")


def ensure_library() -> list[dict[str, str]]:
    """Ensure the library folder and default logos exist, returning metadata for all available images."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    defaults = [
        ("google-cloud.png", "Google Cloud", create_google_cloud_logo),
        ("google-g.png", "Google G", create_google_g_logo),
        ("antigravity.png", "Antigravity", create_antigravity_logo),
        ("red-heart.png", "Red Heart", create_red_heart_logo),
        ("thumbs-up.png", "Thumbs Up", create_thumbs_up_logo),
    ]

    for filename, _, builder in defaults:
        target = LIBRARY_DIR / filename
        if not target.exists():
            builder(target)

    items = []
    known_titles = {fn: title for fn, title, _ in defaults}
    for path in sorted(LIBRARY_DIR.glob("*.png")) + sorted(LIBRARY_DIR.glob("*.jpg")) + sorted(LIBRARY_DIR.glob("*.gif")):
        title = known_titles.get(path.name, path.stem.replace("-", " ").replace("_", " ").title())
        mtime = int(path.stat().st_mtime)
        items.append({"id": path.name, "title": title, "filename": path.name, "mtime": str(mtime)})
    return items


if __name__ == "__main__":
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    create_google_cloud_logo(LIBRARY_DIR / "google-cloud.png")
    create_google_g_logo(LIBRARY_DIR / "google-g.png")
    create_antigravity_logo(LIBRARY_DIR / "antigravity.png")
    create_red_heart_logo(LIBRARY_DIR / "red-heart.png")
    create_thumbs_up_logo(LIBRARY_DIR / "thumbs-up.png")
    print("Initialized library in", LIBRARY_DIR)
