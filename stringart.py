#!/usr/bin/env python3
"""
stringart.py -- turn a photo into a nail-and-thread winding sequence.

Greedy chord solver. At each step, pick the nail whose chord from the current
nail covers the most remaining darkness in the residual image, "draw" it,
subtract its ink, repeat. Stops when no chord improves the image.

Quick start:
    python stringart.py input/your-image.png --nails 200 --diameter 12

Outputs land in --out (default ./out):
    target.png      preprocessed circular target -- CHECK THIS FIRST
    simulation.png  physically-modelled prediction of the finished piece
    preview.png     vector-style render (prettier, less honest)
    preview.svg     same, as vector
    progress.png    2x2 grid at 25 / 50 / 75 / 100% of lines
    sequence.txt    human-readable winding list + raw comma-separated path
    sequence.json   machine-readable path + all metadata
"""

import argparse
import json
import os
import time

import numpy as np
from PIL import Image, ImageOps
from skimage.draw import line_aa

MM_PER_INCH = 25.4


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def nail_positions(n_nails, res):
    """Nail (x, y) in pixel space.

    Nail 0 sits at 12 o'clock and numbering runs CLOCKWISE. This convention
    must match template.py and the numbers written on your physical board.
    """
    c = (res - 1) / 2.0
    r = c - 1.0
    theta = 2.0 * np.pi * np.arange(n_nails) / n_nails
    return np.stack([c + r * np.sin(theta), c - r * np.cos(theta)], axis=1)


def precompute_chords(nails, res):
    """Anti-aliased pixel coverage for every nail pair, stored once per pair.

    Flat arrays plus an index table so chord (i, j) slices out in O(1).
    Memory is the binding constraint here: roughly
    n_pairs * 0.64 * res * 8 bytes.
    """
    n = len(nails)
    pair_id = np.full((n, n), -1, dtype=np.int32)
    starts, lengths, idx_chunks, w_chunks = [], [], [], []
    cursor = k = 0
    for i in range(n):
        yi, xi = int(round(nails[i][1])), int(round(nails[i][0]))
        for j in range(i + 1, n):
            yj, xj = int(round(nails[j][1])), int(round(nails[j][0]))
            rr, cc, val = line_aa(yi, xi, yj, xj)
            idx_chunks.append((rr.astype(np.int64) * res + cc).astype(np.int32))
            w_chunks.append(val.astype(np.float32))
            pair_id[i, j] = pair_id[j, i] = k
            starts.append(cursor)
            lengths.append(len(rr))
            cursor += len(rr)
            k += 1
    return {
        "pair_id": pair_id,
        "idx": np.concatenate(idx_chunks),
        "w": np.concatenate(w_chunks),
        "start": np.array(starts, dtype=np.int64),
        "len": np.array(lengths, dtype=np.int64),
    }


# ---------------------------------------------------------------------------
# preprocessing
# ---------------------------------------------------------------------------


def preprocess(path, res, contrast=1.5, gamma=1.0, invert=False,
               autocontrast=True):
    """Load -> square center crop -> grayscale -> circular mask -> darkness map.

    Returns (target, mask). target is float32 in [0, 1] where 1.0 means
    "put as much thread here as possible".

    `invert` flips which tone gets thread: dark thread on a light board wants
    thread where the photo is dark (the default); light thread on a dark
    board wants the opposite, since thread there reads as bright, not dark.
    This is a solve-target change, not a rendering one -- it decides which
    pixels the solver treats as "wants coverage" before a single chord is
    scored.
    """
    img = Image.open(path).convert("L")
    w, h = img.size
    if w != h:
        s = min(w, h)
        img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    img = img.resize((res, res), Image.LANCZOS)
    if autocontrast:
        img = ImageOps.autocontrast(img, cutoff=1)

    a = np.asarray(img, dtype=np.float32) / 255.0
    if invert:
        a = 1.0 - a
    d = 1.0 - a                                     # darkness = where thread goes
    d = np.clip((d - 0.5) * contrast + 0.5, 0.0, 1.0)
    d = np.power(d, gamma)

    c = (res - 1) / 2.0
    yy, xx = np.mgrid[0:res, 0:res]
    mask = ((xx - c) ** 2 + (yy - c) ** 2) <= (c - 1.0) ** 2
    d[~mask] = 0.0                                  # outside the nails is unreachable
    return d.astype(np.float32), mask


# ---------------------------------------------------------------------------
# solver
# ---------------------------------------------------------------------------


def solve(target, chords, n_nails, max_lines, weight, min_gap,
          allow_repeat_pairs=False, start_nail=0, verbose=True):
    """Returns (path, residual). residual can be negative -- see note below."""
    flat = target.reshape(-1).copy()
    pair_id = chords["pair_id"]
    idx, w, start, length = chords["idx"], chords["w"], chords["start"], chords["len"]

    path = [start_nail]
    used = set()
    current = start_nail
    t0 = time.time()

    for step in range(max_lines):
        best_j, best_score = -1, 0.0
        for j in range(n_nails):
            gap = abs(j - current)
            if min(gap, n_nails - gap) < min_gap:
                continue
            key = (current, j) if current < j else (j, current)
            if not allow_repeat_pairs and key in used:
                continue
            k = pair_id[current, j]
            sl = slice(start[k], start[k] + length[k])
            ww = w[sl]
            score = float(np.dot(flat[idx[sl]], ww) / ww.sum())
            if score > best_score:
                best_score, best_j = score, j

        if best_j < 0:
            if verbose:
                print(f"  converged at line {step}: no chord improves the image")
            break

        k = pair_id[current, best_j]
        sl = slice(start[k], start[k] + length[k])
        np.subtract.at(flat, idx[sl], weight * w[sl])
        # The residual is DELIBERATELY allowed to go negative. Those negative
        # pixels are what repel later chords from already-saturated regions.
        # Clipping at zero here is the single most common way to wreck this
        # algorithm -- it makes overdrawing free and the portrait turns to mud.
        np.clip(flat, -1.0, None, out=flat)

        used.add((current, best_j) if current < best_j else (best_j, current))
        path.append(best_j)
        current = best_j

        if verbose and (step + 1) % 500 == 0:
            print(f"  {step + 1} lines  ({time.time() - t0:.1f}s)")

    return path, flat.reshape(target.shape)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def save_gray(arr, path):
    Image.fromarray((np.clip(1.0 - arr, 0, 1) * 255).astype(np.uint8)).save(path)


def render_vector(path, n_nails, out_png, out_svg=None, size_px=2000,
                  alpha=0.25, lw=0.35, bg="white", fg="black"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    nails = nail_positions(n_nails, 1000)
    segs = [[tuple(nails[a]), tuple(nails[b])] for a, b in zip(path, path[1:])]
    dpi = 200
    fig, ax = plt.subplots(figsize=(size_px / dpi, size_px / dpi), dpi=dpi)
    ax.add_collection(LineCollection(segs, linewidths=lw, colors=fg, alpha=alpha))
    ax.scatter(nails[:, 0], nails[:, 1], s=0.4, c=fg, alpha=0.4)
    ax.set_xlim(-12, 1012)
    ax.set_ylim(1012, -12)
    ax.set_aspect("equal")
    ax.set_facecolor(bg)
    ax.axis("off")
    fig.patch.set_facecolor(bg)
    fig.savefig(out_png, dpi=dpi, bbox_inches="tight", pad_inches=0.1,
                facecolor=bg)
    if out_svg:
        fig.savefig(out_svg, bbox_inches="tight", pad_inches=0.1, facecolor=bg)
    plt.close(fig)


def render_progress(path, n_nails, out_png, alpha=0.25, lw=0.45, bg="white",
                    fg="black"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    nails = nail_positions(n_nails, 1000)
    fig, axes = plt.subplots(2, 2, figsize=(10, 10), dpi=150)
    total = len(path) - 1
    for ax, frac in zip(axes.ravel(), [0.25, 0.5, 0.75, 1.0]):
        k = max(2, int(total * frac))
        sub = path[:k + 1]
        segs = [[tuple(nails[a]), tuple(nails[b])] for a, b in zip(sub, sub[1:])]
        ax.add_collection(LineCollection(segs, linewidths=lw, colors=fg,
                                         alpha=alpha))
        ax.set_xlim(-12, 1012)
        ax.set_ylim(1012, -12)
        ax.set_aspect("equal")
        ax.set_facecolor(bg)
        ax.axis("off")
        ax.set_title(f"{k} lines ({int(frac * 100)}%)", fontsize=10)
    fig.patch.set_facecolor(bg)
    fig.tight_layout()
    fig.savefig(out_png, facecolor=bg)
    plt.close(fig)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def chord_inches(path, n_nails, diameter_in):
    nails = nail_positions(n_nails, 1000)
    px_per_in = 998.0 / diameter_in
    return sum(float(np.hypot(*(nails[a] - nails[b])))
               for a, b in zip(path, path[1:])) / px_per_in


def write_sequence(path, out_dir, meta):
    with open(os.path.join(out_dir, "sequence.txt"), "w") as f:
        f.write(f"# {meta['n_lines']} lines over {meta['n_nails']} nails\n")
        f.write(f"# nail 0 = 12 o'clock, numbering clockwise\n")
        f.write(f"# thread: {meta['thread_yards']:.0f} yd "
                f"({meta['thread_m']:.0f} m) -- buy 1.5x that\n")
        f.write(f"# start at nail {path[0]}, tie off at nail {path[-1]}\n\n")
        for n, (a, b) in enumerate(zip(path, path[1:]), start=1):
            f.write(f"{n:5d}. {a:3d} -> {b:3d}\n")
        f.write("\n# raw path\n" + ",".join(map(str, path)) + "\n")
    with open(os.path.join(out_dir, "sequence.json"), "w") as f:
        json.dump({**meta, "path": path}, f, indent=2)


# ---------------------------------------------------------------------------


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("--nails", type=int, default=200)
    p.add_argument("--diameter", type=float, default=12.0,
                   help="physical board diameter, inches")
    p.add_argument("--thread-mm", type=float, default=0.2,
                   help="thread diameter in mm (all-purpose sewing ~0.2, "
                        "fine machine embroidery ~0.1)")
    p.add_argument("--opacity", type=float, default=0.8,
                   help="how opaque one strand is over the board, 0-1")
    p.add_argument("--out", default="out")
    p.add_argument("--res", type=int, default=1200,
                   help="solve resolution; keep high enough that one thread "
                        "is about one pixel")
    p.add_argument("--max-lines", type=int, default=6000,
                   help="safety cap; the solver normally converges first")
    p.add_argument("--weight", type=float, default=None,
                   help="override the physically-derived ink per line")
    p.add_argument("--contrast", type=float, default=1.5)
    p.add_argument("--gamma", type=float, default=1.0,
                   help=">1 crushes midtones, <1 lifts them")
    p.add_argument("--min-gap", type=int, default=15,
                   help="forbid chords between nails closer than this")
    p.add_argument("--start-nail", type=int, default=0)
    p.add_argument("--allow-repeat-pairs", action="store_true")
    p.add_argument("--no-autocontrast", action="store_true")
    p.add_argument("--invert", action="store_true",
                   help="light thread on a dark board")
    args = p.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # One strand covers (thread_mm / board_mm) of the width, so at solve
    # resolution `res` it is that fraction of `res` pixels wide. Ink per line
    # is that width times the strand's opacity. This is what keeps the line
    # count and thread estimate honest instead of a magic number.
    board_mm = args.diameter * MM_PER_INCH
    px_wide = args.thread_mm * args.res / board_mm
    weight = args.weight if args.weight is not None else min(
        1.0, args.opacity * px_wide)

    print(f"board {args.diameter}\" | thread {args.thread_mm}mm "
          f"= {px_wide:.2f} px at res {args.res} | ink/line {weight:.3f}")
    if px_wide < 0.6:
        print("  warning: solve resolution is coarse for this thread; "
              "consider --res higher")

    print("preprocessing...")
    target, mask = preprocess(args.image, args.res, args.contrast, args.gamma,
                              args.invert, not args.no_autocontrast)
    save_gray(target, os.path.join(args.out, "target.png"))

    n_pairs = args.nails * (args.nails - 1) // 2
    est_mb = n_pairs * 0.64 * args.res * 8 / 1e6
    print(f"precomputing {n_pairs} chords (~{est_mb:.0f} MB)...")
    t0 = time.time()
    chords = precompute_chords(nail_positions(args.nails, args.res), args.res)
    print(f"  {time.time() - t0:.1f}s")

    print("solving...")
    t0 = time.time()
    path, residual = solve(target, chords, args.nails, args.max_lines, weight,
                           args.min_gap, args.allow_repeat_pairs,
                           args.start_nail)
    n_lines = len(path) - 1
    print(f"  {n_lines} lines in {time.time() - t0:.1f}s")
    if n_lines >= args.max_lines:
        print("  note: hit --max-lines before converging; raise it for more detail")

    simulation = np.clip(target - residual, 0.0, 1.0)
    save_gray(simulation, os.path.join(args.out, "simulation.png"))

    inches = chord_inches(path, args.nails, args.diameter)
    coverage = float(simulation[mask].mean())
    meta = {
        "source_image": os.path.abspath(args.image),
        "n_nails": args.nails,
        "n_lines": n_lines,
        "diameter_in": args.diameter,
        "thread_mm": args.thread_mm,
        "nail_order": "clockwise from 12 o'clock, nail 0 at top",
        "weight": weight,
        "res": args.res,
        "contrast": args.contrast,
        "gamma": args.gamma,
        "min_gap": args.min_gap,
        "thread_yards": inches / 36.0,
        "thread_m": inches * 0.0254,
        "mean_coverage": coverage,
    }

    print("rendering...")
    render_vector(path, args.nails, os.path.join(args.out, "preview.png"),
                  os.path.join(args.out, "preview.svg"),
                  alpha=min(0.6, max(0.12, weight * 0.7)))
    render_progress(path, args.nails, os.path.join(args.out, "progress.png"),
                    alpha=min(0.6, max(0.12, weight * 0.7)))
    write_sequence(path, args.out, meta)

    print(f"\n{n_lines} lines | {meta['thread_yards']:.0f} yd thread "
          f"({meta['thread_m']:.0f} m) | mean coverage {coverage:.0%} | "
          f"~{n_lines * 6 / 3600:.1f} h at 6 s/line")
    print(f"outputs in {os.path.abspath(args.out)}/")


if __name__ == "__main__":
    main()
