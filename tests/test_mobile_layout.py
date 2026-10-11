"""
Mobile layout invariants.

These are the rules that, when broken, produce bugs nobody sees until someone
opens the site on a phone. CI has no browser, so this reads the stylesheet and
checks the relationships rather than rendering anything — it cannot prove the
layout looks right, only that the couplings which have already failed once stay
intact. The rendering was checked with a headless phone viewport.

Three of these describe bugs that actually shipped:

  * the map legend reserved `right: 50px`, enough for one button, so when the
    visitors and API buttons were added it covered both and swallowed their
    taps — elementFromPoint at either returned a legend chip
  * the AOI toolbar sat across the Leaflet and Esri attribution, which has to
    stay legible
  * land-use chips were 22px tall, under the floor the rest of the UI keeps
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CSS = (ROOT / "css" / "style.css").read_text()

#: The smallest comfortable touch target, and what the README claims.
TAP_FLOOR = 24


def block(selector: str, after: int = 0) -> str:
    """The declaration body of the first rule for `selector` after `after`."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS[after:])
    return m.group(1) if m else ""


def media(query: str) -> str:
    """Everything inside one @media block, by brace matching."""
    i = CSS.index(query)
    depth, j = 0, CSS.index("{", i)
    start = j
    while j < len(CSS):
        if CSS[j] == "{":
            depth += 1
        elif CSS[j] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start:j]
        j += 1
    raise AssertionError(f"unbalanced braces after {query}")


MOBILE = media("@media (max-width: 860px)")
NARROW = media("@media (max-width: 430px)")


# --------------------------------------------------------------------------- #
# The overlay button cluster and the legend
# --------------------------------------------------------------------------- #
def test_the_cluster_metrics_are_shared_not_private():
    """The legend has to reserve room for the buttons, so both need the same
    numbers. Keeping them on .helpbtn is what let the two drift apart."""
    root = block(":root")
    for var in ("--hb-edge", "--hb-step", "--hb-reserve"):
        assert var in root, f"{var} should live on :root"


def test_the_reservation_covers_every_button_not_just_one():
    root = block(":root")
    m = re.search(r"--hb-reserve:\s*calc\(([^;]+)\);", root)
    assert m, "--hb-reserve should be derived, not a literal"
    expr = m.group(1)
    assert "--hb-step) * 2" in expr or "--hb-step)*2" in expr, (
        "the reservation must clear all three buttons, not one: " + expr)
    assert "--hb-edge" in expr


def test_the_legend_reserves_room_rather_than_guessing_a_width():
    """`right: 50px` here is the original bug."""
    legend = re.search(r"\.maplegend\s*\{([^}]*)\}", MOBILE)
    assert legend, "the legend is no longer adjusted for narrow screens"
    body = legend.group(1)
    right = re.search(r"right:\s*([^;]+);", body)
    assert right, "the legend must bound its right edge on narrow screens"
    assert "--hb-reserve" in right.group(1), (
        f"a literal reservation goes stale when a button is added: {right.group(1)}")


def test_the_narrow_block_moves_the_cluster_by_variable():
    """Setting `right` on .helpbtn directly stacks all three in one spot."""
    helpbtn = re.search(r"\.helpbtn\s*\{([^}]*)\}", MOBILE)
    assert helpbtn
    assert not re.search(r"(^|;)\s*right\s*:", helpbtn.group(1))
    assert "--hb-edge" in MOBILE


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #
def _offset(css: str, selector: str) -> int:
    """The `+ Npx` term of a bottom: calc(min(...) + Npx) rule."""
    m = re.search(re.escape(selector) + r"\s*\{[^}]*bottom:\s*calc\([^)]*\)\s*\+\s*(\d+)px",
                  css)
    return int(m.group(1)) if m else None


@pytest.mark.parametrize("scope, css", [("860px", MOBILE), ("430px", NARROW)],
                         ids=["860px", "430px"])
def test_the_aoi_toolbar_clears_the_attribution(scope, css):
    """The Leaflet and Esri credits have to stay readable, and the toolbar is
    180px wide from left:62 so at the same height it reaches across them.

    Both breakpoints are checked because the narrower one overrides the other:
    raising the toolbar at 860px alone changed nothing on a 390px phone.
    """
    toolbar = _offset(css, "#aoi-toolbar")
    attrib = _offset(css, ".leaflet-bottom.leaflet-right")
    assert toolbar is not None, f"no toolbar offset at {scope}"
    assert attrib is not None, f"no attribution offset at {scope}"
    assert toolbar >= attrib + 24, (
        f"at {scope} the toolbar sits {toolbar - attrib}px above the attribution "
        "row, which is not enough to clear it")


# --------------------------------------------------------------------------- #
# Touch targets
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("selector", [".lu-chip", ".fmt-chip"])
def test_chips_meet_the_tap_floor(selector):
    m = re.search(re.escape(selector) + r"\s*\{[^}]*min-height:\s*(\d+)px", CSS)
    assert m, f"{selector} has no min-height"
    assert int(m.group(1)) >= TAP_FLOOR, f"{selector} is {m.group(1)}px"


def test_the_narrow_screen_block_raises_the_text_sized_controls():
    """Tray headers, tabs and toggles are sized by their text and land at
    10-21px, which is unusable with a thumb."""
    for selector in (".mod-h", ".recent-tab", ".fmt-more",
                     ".theme-toggle", ".basemap-toggle button"):
        m = re.search(re.escape(selector) + r"\s*\{[^}]*(min-height|height):\s*(\d+)px",
                      MOBILE)
        assert m, f"{selector} is not raised for touch"
        assert int(m.group(2)) >= TAP_FLOOR, f"{selector} is {m.group(2)}px"


def test_the_edge_handles_are_thick_enough_to_grab():
    """Both are rotated or thin strips, so the small dimension is what you hit."""
    collapse = re.search(r"\.collapse\s*\{[^}]*height:\s*(\d+)px", MOBILE)
    assert collapse and int(collapse.group(1)) >= TAP_FLOOR, "console handle"
    detail = re.search(r"#detail-toggle\s*\{[^}]*height:\s*(\d+)px", MOBILE)
    assert detail and int(detail.group(1)) >= TAP_FLOOR, "detail sheet handle"
