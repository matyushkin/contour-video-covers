from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from contour_video_covers import HORIZONTAL, VERTICAL, CoverSpec, Hero, make_cover
from contour_video_covers.core import FONT_CANDIDATES

pytestmark = pytest.mark.skipif(
    not any(Path(p).exists() for p in FONT_CANDIDATES), reason="Helvetica Neue is not installed"
)


@pytest.fixture
def frame(tmp_path):
    """A grey field with a red disc: the disc is the hero."""
    im = Image.new("RGB", (1920, 1080), (90, 96, 100))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 700, 1920, 1080), fill=(60, 64, 60))
    d.ellipse((1200, 200, 1600, 600), fill=(200, 40, 40))
    path = tmp_path / "frame.jpg"
    im.save(path)
    return path


def test_box_hero_is_found_and_coloured(frame):
    cover, mask = make_cover(CoverSpec(frame=frame, title=["Test"], hero=Hero(box=(1150, 150, 1650, 650))))
    assert cover.size == (HORIZONTAL.width, HORIZONTAL.height)
    ys, xs = np.nonzero(mask)
    # the disc sits at x 1200..1600 of 1920 -> 800..1067 of 1280
    assert 780 < xs.min() < 830 and 1040 < xs.max() < 1090
    r, g, b = np.asarray(cover)[ys.mean().astype(int), xs.mean().astype(int)]
    assert r > g and r > b


def test_polygon_hero_and_vertical_size(frame):
    square = [(1250, 250), (1550, 250), (1550, 550), (1250, 550)]
    cover, mask = make_cover(
        CoverSpec(frame=frame, title=["A", "B"], hero=Hero(polygon=square), crop=(1100, 0, 1707, 1080), vertical=True)
    )
    assert cover.size == (VERTICAL.width, VERTICAL.height)
    assert mask.any()


def test_exactly_one_hero_source(frame):
    with pytest.raises(ValueError):
        make_cover(CoverSpec(frame=frame, title=["x"], hero=Hero()))
