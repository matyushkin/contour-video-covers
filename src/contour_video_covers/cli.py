"""Command line: one cover from flags, or a whole series from a JSON file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from .core import CoverSpec, Hero, load_polygon, make_cover

THUMB = (168, 94)  # how big a cover really is in a playlist


def _ints(text: str, n: int) -> tuple[int, ...]:
    vals = tuple(int(v) for v in text.split(","))
    if len(vals) != n:
        raise argparse.ArgumentTypeError(f"expected {n} comma-separated numbers, got {text!r}")
    return vals


def _box(text):
    return _ints(text, 4)


def _rgb(text):
    return _ints(text, 3)


def _write(spec: CoverSpec, out: Path, check: bool) -> None:
    cover, mask = make_cover(spec)
    out.parent.mkdir(parents=True, exist_ok=True)
    cover.save(out, quality=92)
    print(out)
    if check:
        Image.fromarray((mask * 255).astype("uint8")).save(out.with_name(out.stem + "-mask.png"))
        size = THUMB if not spec.vertical else (94, 168)
        cover.resize(size, Image.Resampling.LANCZOS).save(out.with_name(out.stem + "-small.png"))


def _spec_from_dict(d: dict, base: Path) -> CoverSpec:
    hero = d["hero"]
    return CoverSpec(
        frame=base / d["frame"],
        title=d["title"] if isinstance(d["title"], list) else [d["title"]],
        hero=Hero(
            box=tuple(hero["box"]) if "box" in hero else None,
            polygon=[tuple(p) for p in hero["polygon"]] if "polygon" in hero else None,
            mask=base / hero["mask"] if "mask" in hero else None,
        ),
        crop=tuple(d["crop"]) if "crop" in d else None,
        mirror=d.get("mirror", False),
        neutral=tuple(d["neutral"]) if "neutral" in d else None,
        accent=tuple(d["accent"]) if "accent" in d else None,
        dark_title=d.get("dark_title", False),
        vertical=d.get("vertical", False),
        font=Path(d["font"]) if "font" in d else None,
    )


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="contour-cover", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="one cover")
    m.add_argument("frame", type=Path, help="a frame from the video")
    m.add_argument("-t", "--title", action="append", required=True, help="one title line; repeat for more lines")
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument("--hero-box", type=_box, help="x0,y0,x1,y1 around the hero, in frame pixels")
    g.add_argument("--hero-polygon", type=Path, help="JSON list of [x, y] points traced around the hero")
    g.add_argument("--hero-mask", type=Path, help="black-and-white mask, same size as the frame")
    m.add_argument("--crop", type=_box, help="x0,y0,x1,y1 part of the frame to use")
    m.add_argument("--mirror", action="store_true")
    m.add_argument("--neutral", type=_rgb, help="R,G,B dark field colour (default: from the frame)")
    m.add_argument("--accent", type=_rgb, help="R,G,B hero colour (default: from the hero)")
    m.add_argument("--dark-title", action="store_true", help="dark title for a light field")
    m.add_argument("--vertical", action="store_true", help="1080x1920 cover for Shorts")
    m.add_argument("--font", type=Path, help="bold font file if Helvetica Neue is missing")
    m.add_argument("-o", "--out", type=Path, default=Path("cover.jpg"))
    m.add_argument("--check", action="store_true", help="also write the mask and a playlist-size preview")

    b = sub.add_parser("batch", help="a series of covers from a JSON file")
    b.add_argument("config", type=Path)
    b.add_argument("-o", "--out-dir", type=Path, default=Path("out"))
    b.add_argument("--only", nargs="*", help="names to render; others stay untouched")
    b.add_argument("--check", action="store_true")

    a = p.parse_args(argv)
    if a.cmd == "make":
        spec = CoverSpec(
            frame=a.frame,
            title=a.title,
            hero=Hero(
                box=a.hero_box,
                polygon=load_polygon(a.hero_polygon) if a.hero_polygon else None,
                mask=a.hero_mask,
            ),
            crop=a.crop,
            mirror=a.mirror,
            neutral=a.neutral,
            accent=a.accent,
            dark_title=a.dark_title,
            vertical=a.vertical,
            font=a.font,
        )
        _write(spec, a.out, a.check)
    else:
        covers = json.loads(a.config.read_text())
        for name, d in covers.items():
            if a.only and name not in a.only:
                continue
            _write(_spec_from_dict(d, a.config.parent), a.out_dir / f"{name}.jpg", a.check)


if __name__ == "__main__":
    main()
