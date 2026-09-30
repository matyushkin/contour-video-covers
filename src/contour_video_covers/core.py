"""Turn one video frame into a contour-field cover with a single coloured hero."""

from __future__ import annotations

import colorsys
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

PAPER = (242, 235, 214)
INK_LINE = (24, 22, 20)
DARK_TITLE = (30, 34, 38)
FONT_CANDIDATES = (
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/HelveticaNeue.ttc",
)


@dataclass(frozen=True)
class Layout:
    """Canvas size and the fixed title grid."""

    width: int
    height: int
    title_size: int
    line_gap: int
    left: int
    baseline: int
    edge: int  # width of the light line around the hero


HORIZONTAL = Layout(1280, 720, title_size=76, line_gap=86, left=60, baseline=640, edge=7)
VERTICAL = Layout(1080, 1920, title_size=104, line_gap=116, left=80, baseline=1590, edge=9)


@dataclass
class Hero:
    """Where the hero is, in source-frame pixels. Give exactly one of the three."""

    box: tuple[int, int, int, int] | None = None  # rough rectangle, cut out by GrabCut
    polygon: list[tuple[float, float]] | None = None  # outline traced by hand
    mask: Path | None = None  # ready black-and-white mask, same size as the frame


@dataclass
class CoverSpec:
    frame: Path
    title: list[str]
    hero: Hero
    crop: tuple[int, int, int, int] | None = None  # part of the frame to use, source pixels
    mirror: bool = False
    neutral: tuple[int, int, int] | None = None  # dark field colour; None = taken from the frame
    accent: tuple[int, int, int] | None = None  # hero colour; None = taken from the hero
    dark_title: bool = False  # for light fields (snow)
    vertical: bool = False
    font: Path | None = None
    levels: int = 5


# ---------------------------------------------------------------- frame


def _source(spec: CoverSpec) -> tuple[Image.Image, tuple[int, int, int, int]]:
    src = Image.open(spec.frame).convert("RGB")
    crop = spec.crop or _default_crop(src.size, spec.vertical)
    return src, crop


def _default_crop(size: tuple[int, int], vertical: bool) -> tuple[int, int, int, int]:
    """Centred crop with the layout's aspect ratio."""
    w, h = size
    lay = VERTICAL if vertical else HORIZONTAL
    ratio = lay.width / lay.height
    if w / h > ratio:
        cw = round(h * ratio)
        x0 = (w - cw) // 2
        return (x0, 0, x0 + cw, h)
    ch = round(w / ratio)
    y0 = (h - ch) // 2
    return (0, y0, w, y0 + ch)


def _fit(img: Image.Image, crop, lay: Layout, mirror: bool, resample=Image.Resampling.LANCZOS) -> Image.Image:
    out = img.crop(crop).resize((lay.width, lay.height), resample)
    return ImageOps.mirror(out) if mirror else out


# ---------------------------------------------------------------- hero mask


def _clean(m: np.ndarray, blur: float = 6) -> np.ndarray:
    """Keep the largest shape, fill its holes, smooth the outline."""
    m = m.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        m = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    m = np.zeros_like(m)
    cv2.drawContours(m, contours, -1, 1, -1)
    return cv2.GaussianBlur(m.astype(np.float32), (0, 0), blur) > 0.5


def hero_mask(spec: CoverSpec, src: Image.Image, crop, lay: Layout, canvas: Image.Image) -> np.ndarray:
    hero = spec.hero
    given = [x is not None for x in (hero.box, hero.polygon, hero.mask)]
    if sum(given) != 1:
        raise ValueError("give exactly one of hero box, polygon or mask")
    if hero.mask is not None:
        m = Image.open(hero.mask).convert("L")
        if m.size != src.size:
            raise ValueError(f"mask size {m.size} differs from frame size {src.size}")
        return np.asarray(_fit(m, crop, lay, spec.mirror)) > 127
    if hero.polygon is not None:
        big = Image.new("L", src.size, 0)
        ImageDraw.Draw(big).polygon([tuple(p) for p in hero.polygon], fill=255)
        return np.asarray(_fit(big, crop, lay, spec.mirror)) > 127
    # box: GrabCut inside the rectangle, on the canvas
    box = Image.new("L", src.size, 0)
    ImageDraw.Draw(box).rectangle(hero.box, fill=255)
    ys, xs = np.nonzero(np.asarray(_fit(box, crop, lay, spec.mirror, Image.Resampling.NEAREST)))
    if not len(xs):
        raise ValueError("hero box lies outside the crop")
    rect = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min()))
    a = cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
    m = np.zeros(a.shape[:2], np.uint8)
    cv2.grabCut(a, m, rect, np.zeros((1, 65)), np.zeros((1, 65)), 6, cv2.GC_INIT_WITH_RECT)
    return _clean((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD))


# ---------------------------------------------------------------- colours


def accent_of(img: Image.Image, mask: np.ndarray) -> np.ndarray:
    """The hero's own hue, weighted by saturation, at a fixed soft strength."""
    px = np.asarray(img).reshape(-1, 3)[mask.reshape(-1)] / 255
    if not len(px):
        raise ValueError("hero mask is empty")
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in px[:: max(1, len(px) // 4000)]])
    w = hsv[:, 1] * hsv[:, 2] + 1e-3
    ang = hsv[:, 0] * 2 * np.pi
    h = (np.arctan2((np.sin(ang) * w).sum(), (np.cos(ang) * w).sum()) / (2 * np.pi)) % 1
    sat = 0.66 if (h < 0.17 or h > 0.9) else 0.42  # warm hues read weak at low saturation
    return np.array(colorsys.hsv_to_rgb(h, sat, 0.9))


def neutral_of(img: Image.Image, mask: np.ndarray) -> np.ndarray:
    """A dark neutral from the field's own palette: its mean hue, barely saturated, very dark."""
    px = np.asarray(img).reshape(-1, 3)[~mask.reshape(-1)] / 255
    r, g, b = px.mean(0)
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    return np.array(colorsys.hsv_to_rgb(h, min(0.45, s * 1.2), 0.17))


# ---------------------------------------------------------------- drawing


def redraw(img: Image.Image, levels: int = 5, smooth: int = 13, ink: int = 4) -> np.ndarray:
    """Luminance of the frame redrawn as flat tones with ink lines on the tone borders (0..1)."""
    a = np.asarray(img)
    for _ in range(3):
        a = cv2.bilateralFilter(a, 9, 50, 9)
    im = Image.fromarray(cv2.medianBlur(a, 7))
    for _ in range(3):
        im = im.filter(ImageFilter.ModeFilter(smooth))
    im = im.filter(ImageFilter.SMOOTH_MORE)
    q = im.quantize(levels, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).convert("RGB")
    q = q.filter(ImageFilter.ModeFilter(7))
    edges = q.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 18 else 0)
    if ink:
        q.paste(INK_LINE, (0, 0), edges.filter(ImageFilter.MaxFilter(ink | 1)))
    return np.asarray(q).astype(float).mean(2, keepdims=True) / 255


def _font(spec: CoverSpec, size: int) -> ImageFont.FreeTypeFont:
    for path in ([spec.font] if spec.font else []) + [Path(p) for p in FONT_CANDIDATES]:
        if path and Path(path).exists():
            return ImageFont.truetype(str(path), size, index=1 if str(path).endswith(".ttc") else 0)
    raise FileNotFoundError("Helvetica Neue Bold not found; pass --font /path/to/a/bold/font")


def compose(canvas: Image.Image, mask: np.ndarray, spec: CoverSpec, lay: Layout) -> Image.Image:
    paper = np.array(PAPER) / 255
    lum = redraw(canvas, spec.levels)
    ink = np.array(spec.neutral) / 255 if spec.neutral else neutral_of(canvas, mask)
    acc = np.array(spec.accent) / 255 if spec.accent else accent_of(canvas, mask)
    field = ink + (paper - ink) * lum  # dark neutral -> paper by luminance
    hero = acc + (paper - acc) * lum  # accent -> paper, so contour lines run through the hero
    out = np.where(mask[..., None], hero, field)
    edge = cv2.Canny((mask * 255).astype(np.uint8), 50, 150)
    out[cv2.dilate(edge, np.ones((lay.edge, lay.edge), np.uint8)) > 0] = paper
    im = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(im)
    font = _font(spec, lay.title_size)
    y = lay.baseline - lay.line_gap * (len(spec.title) - 1)  # the last line always sits on the baseline
    for line in spec.title:
        draw.text((lay.left, y), line, font=font, fill=DARK_TITLE if spec.dark_title else PAPER, anchor="ls")
        y += lay.line_gap
    return im


def make_cover(spec: CoverSpec) -> tuple[Image.Image, np.ndarray]:
    """Return the cover and the hero mask (check the mask on its own before publishing)."""
    lay = VERTICAL if spec.vertical else HORIZONTAL
    src, crop = _source(spec)
    canvas = _fit(src, crop, lay, spec.mirror)
    mask = hero_mask(spec, src, crop, lay, canvas)
    return compose(canvas, mask, spec, lay), mask


def load_polygon(path: Path) -> list[tuple[float, float]]:
    return [tuple(p) for p in json.loads(Path(path).read_text())]
