import io
import pathlib
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from PIL import Image

from page_in_history.maps import Color

DPI = 160
INCHES_PER_UNIT = 0.9
BOX_WIDTH = 2.6
BOX_HEIGHT = 0.8
BOX_GAP = 0.35
LAYER_GAP = 1.5
FONT_SIZE = 13
WRAP = 18
ANCHOR_SIZE = 10.5
ANCHOR_WRAP = 24
MARGIN = 0.1
UNKNOWN = "?"


class SuccessionDiagram:
    """What came before a civilization on top, the civilization (red) in the middle row and what came after it below;
    the civilization reads '?' on the question and its name on the answer. Arrows carry the handover region."""

    def __init__(self, font: pathlib.Path):
        font_manager.fontManager.addfont(str(font))
        self.font = font_manager.FontProperties(fname=font).get_name()

    def render(self, graph: dict, focus: str, reveal: bool, output: pathlib.Path) -> None:
        positions = self._positions(graph, focus)
        xs = [x for x, _ in positions.values()]
        ys = [y for _, y in positions.values()]
        left, right = min(xs) - BOX_WIDTH / 2 - MARGIN, max(xs) + BOX_WIDTH / 2 + MARGIN
        bottom, top = min(ys) - BOX_HEIGHT / 2 - MARGIN, max(ys) + BOX_HEIGHT / 2 + MARGIN
        figure, axes = plt.subplots(figsize=((right - left) * INCHES_PER_UNIT, (top - bottom) * INCHES_PER_UNIT),
                                    dpi=DPI)
        figure.subplots_adjust(0, 0, 1, 1)
        figure.patch.set_alpha(0)
        axes.set_axis_off()
        axes.set_xlim(left, right)
        axes.set_ylim(bottom, top)
        for first, last, anchor in graph["edges"]:
            (x0, y0), (x1, y1) = positions[first], positions[last]
            start, end = (x0, y0 - BOX_HEIGHT / 2), (x1, y1 + BOX_HEIGHT / 2)
            axes.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=14, color=Color.LINE,
                                           linewidth=1.2, shrinkA=2, shrinkB=2))
            middle_x, middle_y = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
            side = -1 if middle_x < -0.01 else 1
            axes.text(middle_x + side * 0.08, middle_y, "\n".join(textwrap.wrap(anchor, ANCHOR_WRAP)),
                      ha="left" if side > 0 else "right", va="center", fontsize=ANCHOR_SIZE, family=self.font,
                      color=Color.LINE, linespacing=1.0)
        for node in graph["nodes"]:
            x, y = positions[node["id"]]
            is_focus = node["id"] == focus
            label = node["name"] if reveal or not is_focus else UNKNOWN
            axes.add_patch(FancyBboxPatch((x - BOX_WIDTH / 2, y - BOX_HEIGHT / 2), BOX_WIDTH, BOX_HEIGHT,
                                          boxstyle="round,pad=0,rounding_size=0.12", linewidth=0,
                                          facecolor=Color.HIGHLIGHT if is_focus else Color.LAND))
            axes.text(x, y, "\n".join(textwrap.wrap(label, WRAP)), ha="center", va="center", fontsize=FONT_SIZE,
                      family=self.font, weight="semibold", linespacing=1.05,
                      color="white" if is_focus else Color.TEXT)
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=DPI, transparent=True)
        plt.close(figure)
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.open(buffer).save(output, optimize=True)

    @staticmethod
    def _positions(graph: dict, focus: str) -> dict[str, tuple[float, float]]:
        """Rows by layer, centred. In the middle row, siblings sharing a predecessor sit to the left of the
        civilization and siblings sharing a successor to the right, so arrows don't cross."""
        sources = {first for first, _, _ in graph["edges"]}
        by_layer: dict[int, list[str]] = {}
        for node in graph["nodes"]:
            by_layer.setdefault(node["layer"], []).append(node["id"])
        middle = by_layer.get(1, [])
        by_layer[1] = ([node for node in middle if node != focus and node not in sources] + [focus]
                       + [node for node in middle if node != focus and node in sources])
        positions = {}
        for row, layer in enumerate(sorted(by_layer)):
            nodes = by_layer[layer]
            span = len(nodes) * BOX_WIDTH + (len(nodes) - 1) * BOX_GAP
            for index, node in enumerate(nodes):
                positions[node] = (-span / 2 + BOX_WIDTH / 2 + index * (BOX_WIDTH + BOX_GAP), -row * LAYER_GAP)
        return positions
