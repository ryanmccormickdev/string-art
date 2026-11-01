#!/usr/bin/env python3
"""
template.py -- printable, true-scale nail-placement template.

Nail 0 sits at 12 o'clock and numbering runs CLOCKWISE, matching stringart.py.

    python template.py --nails 200 --diameter 12

Writes:
    out/nail_template_tiled.pdf   letter pages, print at 100% / "Actual size"
    out/nail_template_full.pdf    one page at true board size (print shop)

Every page carries a 1-inch calibration square. Measure it with a ruler before
you trust anything. Printer scaling is the number one way this goes wrong.
"""

import argparse
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

PAGE_W, PAGE_H = 8.5, 11.0
MARGIN = 0.25


def nail_xy(n_nails, diameter, inset):
    """Board coordinates in inches, origin at center, y up.

    Clockwise from 12 o'clock so it matches the solver's numbering.
    """
    r = diameter / 2.0 - inset
    t = 2.0 * np.pi * np.arange(n_nails) / n_nails
    return r * np.sin(t), r * np.cos(t), r


def draw(ax, n_nails, diameter, inset, label_every):
    x, y, r = nail_xy(n_nails, diameter, inset)

    ax.add_patch(plt.Circle((0, 0), diameter / 2.0, fill=False, lw=0.6,
                            ls=(0, (6, 4)), ec="0.6"))
    ax.add_patch(plt.Circle((0, 0), r, fill=False, lw=0.5, ec="0.75"))

    # center crosshair for aligning the tiles on the board
    ax.plot([-0.35, 0.35], [0, 0], lw=0.6, c="0.4")
    ax.plot([0, 0], [-0.35, 0.35], lw=0.6, c="0.4")

    for i in range(n_nails):
        ux, uy = x[i] / r, y[i] / r
        major = (i % label_every == 0)
        tick = 0.16 if major else 0.09
        ax.plot([x[i] - ux * tick / 2, x[i] + ux * tick / 2],
                [y[i] - uy * tick / 2, y[i] + uy * tick / 2],
                lw=1.1 if major else 0.5, c="black",
                solid_capstyle="butt")
        # a dot exactly on the nail center -- this is where you drill
        ax.plot([x[i]], [y[i]], marker=".", ms=1.6, c="black")
        if major:
            # horizontal, not radial: rotated numerals on the lower half read
            # upside down and you will misplace a nail at 3 a.m.
            ax.text(x[i] + ux * 0.30, y[i] + uy * 0.30, str(i),
                    ha="center", va="center", fontsize=7)


def calibration(ax, x0, y0):
    ax.add_patch(plt.Rectangle((x0, y0), 1.0, 1.0, fill=False, lw=0.8,
                               ec="black"))
    ax.text(x0 + 0.5, y0 + 0.5, '1"', ha="center", va="center", fontsize=7)


def full_page(path, n_nails, diameter, inset, label_every):
    pad = 0.6
    side = diameter + 2 * pad
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(side, side))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(-side / 2, side / 2)
        ax.set_ylim(-side / 2, side / 2)
        ax.set_aspect("equal")
        ax.axis("off")
        draw(ax, n_nails, diameter, inset, label_every)
        calibration(ax, -side / 2 + 0.15, -side / 2 + 0.15)
        ax.text(0, side / 2 - 0.3,
                f"{n_nails} nails / {diameter}\" -- nail 0 at top, clockwise",
                ha="center", fontsize=8)
        pdf.savefig(fig)
        plt.close(fig)


def tiled(path, n_nails, diameter, inset, label_every, overlap=0.25):
    pad = 0.5
    span = diameter + 2 * pad
    tw, th = PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN
    ncols = int(np.ceil(span / (tw - overlap)))
    nrows = int(np.ceil(span / (th - overlap)))
    x0 = -span / 2
    y1 = span / 2

    with PdfPages(path) as pdf:
        for row in range(nrows):
            for col in range(ncols):
                left = x0 + col * (tw - overlap)
                top = y1 - row * (th - overlap)
                fig = plt.figure(figsize=(PAGE_W, PAGE_H))
                ax = fig.add_axes([MARGIN / PAGE_W, MARGIN / PAGE_H,
                                   tw / PAGE_W, th / PAGE_H])
                ax.set_xlim(left, left + tw)
                ax.set_ylim(top - th, top)
                ax.set_aspect("equal")
                ax.set_xticks([])
                ax.set_yticks([])
                for s in ax.spines.values():
                    s.set_linewidth(0.4)
                    s.set_edgecolor("0.7")
                draw(ax, n_nails, diameter, inset, label_every)
                calibration(ax, left + 0.15, top - th + 0.15)
                ax.text(left + tw - 0.15, top - 0.2,
                        f"row {row + 1} col {col + 1} of {nrows}x{ncols}"
                        f"  |  print at 100%, no fit-to-page",
                        ha="right", va="top", fontsize=7, color="0.35")
                pdf.savefig(fig)
                plt.close(fig)
    return nrows, ncols


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nails", type=int, default=200)
    p.add_argument("--diameter", type=float, default=12.0)
    p.add_argument("--inset", type=float, default=0.35,
                   help="inches from board edge to nail centers")
    p.add_argument("--label-every", type=int, default=10)
    p.add_argument("--out", default="out")
    a = p.parse_args()

    os.makedirs(a.out, exist_ok=True)
    r = a.diameter / 2 - a.inset
    spacing_mm = 2 * np.pi * r * 25.4 / a.nails

    tiled_path = os.path.join(a.out, "nail_template_tiled.pdf")
    full_path = os.path.join(a.out, "nail_template_full.pdf")
    nrows, ncols = tiled(tiled_path, a.nails, a.diameter, a.inset, a.label_every)
    full_page(full_path, a.nails, a.diameter, a.inset, a.label_every)

    print(f"{a.nails} nails on a {2 * r:.2f}\" circle")
    print(f"nail spacing: {spacing_mm:.2f} mm "
          f"({spacing_mm / 25.4:.3f}\") center to center")
    if spacing_mm < 3.0:
        print("  warning: tighter than most nail heads. Fewer nails, "
              "or use headless pins.")
    print(f"{nrows}x{ncols} letter pages -> {tiled_path}")
    print(f"single true-size page      -> {full_path}")


if __name__ == "__main__":
    main()
