#!/usr/bin/env python3
"""
app.py -- local web UI for stringart.py.

    python app.py

Opens a form for every solver parameter, a canvas that replays the winding
sequence with a scrub slider and play button, and export buttons for the
same files the CLI produces (plus the drilling template PDF). Wraps the
functions in stringart.py and template.py directly -- no duplicated logic.

Single-user, local tool. Do not expose this past localhost.
"""

import os
import re
import time
import uuid

import numpy as np
from flask import Flask, jsonify, render_template, request, send_file

import stringart as sa
import template as tmpl

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(APP_DIR, "input")
OUT_DIR = os.path.join(APP_DIR, "out")
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

MAX_CACHED_RUNS = 5
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex_color(param, default):
    color = request.args.get(param, default)
    return color if HEX_COLOR_RE.match(color) else default

app = Flask(__name__)

# In-memory cache of recent runs keyed by run id. Single-user local tool, so
# a dict is fine -- no need for a database or session handling.
RUNS = {}


def _prune_runs():
    while len(RUNS) > MAX_CACHED_RUNS:
        oldest = min(RUNS, key=lambda k: RUNS[k]["ts"])
        del RUNS[oldest]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("image")
    if not f or not f.filename:
        return jsonify(error="no file received"), 400
    ext = os.path.splitext(f.filename)[1].lower() or ".png"
    name = f"{uuid.uuid4().hex}{ext}"
    f.save(os.path.join(INPUT_DIR, name))
    return jsonify(image=name)


@app.route("/input/<name>")
def input_file(name):
    return send_file(os.path.join(INPUT_DIR, name))


@app.route("/api/generate", methods=["POST"])
def generate():
    p = request.get_json(force=True)
    image_name = p.get("image")
    if not image_name:
        return jsonify(error="no image selected"), 400
    image_path = os.path.join(INPUT_DIR, image_name)
    if not os.path.exists(image_path):
        return jsonify(error="image not found -- re-upload it"), 400

    try:
        nails = int(p.get("nails", 200))
        diameter = float(p.get("diameter", 12.0))
        thread_mm = float(p.get("thread_mm", 0.2))
        opacity = float(p.get("opacity", 0.8))
        res = int(p.get("res", 900))
        max_lines = int(p.get("max_lines", 6000))
        weight_override = p.get("weight")
        weight_override = (float(weight_override)
                            if weight_override not in (None, "") else None)
        contrast = float(p.get("contrast", 1.5))
        gamma = float(p.get("gamma", 1.0))
        min_gap = int(p.get("min_gap", 15))
        start_nail = int(p.get("start_nail", 0)) % nails
        allow_repeat_pairs = bool(p.get("allow_repeat_pairs", False))
        invert = bool(p.get("invert", False))
        autocontrast = not bool(p.get("no_autocontrast", False))
    except (TypeError, ValueError) as e:
        return jsonify(error=f"bad parameter: {e}"), 400

    if nails < 3:
        return jsonify(error="need at least 3 nails"), 400

    board_mm = diameter * sa.MM_PER_INCH
    px_wide = thread_mm * res / board_mm
    weight = weight_override if weight_override is not None else min(
        1.0, opacity * px_wide)

    t0 = time.time()
    target, mask = sa.preprocess(image_path, res, contrast, gamma, invert,
                                  autocontrast)
    chords = sa.precompute_chords(sa.nail_positions(nails, res), res)
    path, residual = sa.solve(target, chords, nails, max_lines, weight,
                               min_gap, allow_repeat_pairs, start_nail,
                               verbose=False)
    n_lines = len(path) - 1
    elapsed = time.time() - t0

    simulation = np.clip(target - residual, 0.0, 1.0)
    coverage = float(simulation[mask].mean())
    inches = sa.chord_inches(path, nails, diameter)

    run_id = uuid.uuid4().hex
    run_dir = os.path.join(OUT_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)
    sa.save_gray(target, os.path.join(run_dir, "target.png"))
    sa.save_gray(simulation, os.path.join(run_dir, "simulation.png"))

    meta = {
        "source_image": os.path.abspath(image_path),
        "n_nails": nails,
        "n_lines": n_lines,
        "diameter_in": diameter,
        "thread_mm": thread_mm,
        "nail_order": "clockwise from 12 o'clock, nail 0 at top",
        "weight": weight,
        "res": res,
        "contrast": contrast,
        "gamma": gamma,
        "min_gap": min_gap,
        "start_nail": start_nail,
        "thread_yards": inches / 36.0,
        "thread_m": inches * 0.0254,
        "mean_coverage": coverage,
        "max_lines_hit": n_lines >= max_lines,
    }

    RUNS[run_id] = {"ts": time.time(), "path": path, "meta": meta,
                     "run_dir": run_dir}
    _prune_runs()

    return jsonify(
        run_id=run_id,
        path=path,
        n_nails=nails,
        meta=meta,
        elapsed=round(elapsed, 1),
        target_url=f"/out/{run_id}/target.png",
        simulation_url=f"/out/{run_id}/simulation.png",
    )


@app.route("/out/<run_id>/<fname>")
def out_file(run_id, fname):
    run = RUNS.get(run_id)
    if not run:
        return "run expired -- generate again", 404
    fpath = os.path.join(run["run_dir"], fname)
    if not os.path.exists(fpath):
        return "not found", 404
    return send_file(fpath)


@app.route("/api/export/<run_id>/<fmt>")
def export(run_id, fmt):
    run = RUNS.get(run_id)
    if not run:
        return jsonify(error="run expired -- generate again"), 404
    path, meta, run_dir = run["path"], run["meta"], run["run_dir"]
    n_nails = meta["n_nails"]
    alpha = min(0.6, max(0.12, meta["weight"] * 0.7))
    color = _hex_color("color", "#000000")
    bg = _hex_color("bg", "#ffffff")

    # Color is a rendering choice, not a solve param, so these three always
    # re-render on export rather than trusting a cached file from a run that
    # may have used different colors.
    if fmt == "preview_png":
        out_png = os.path.join(run_dir, "preview.png")
        sa.render_vector(path, n_nails, out_png, alpha=alpha, bg=bg, fg=color)
        return send_file(out_png, as_attachment=True,
                          download_name="preview.png")

    if fmt == "preview_svg":
        out_png = os.path.join(run_dir, "preview.png")
        out_svg = os.path.join(run_dir, "preview.svg")
        sa.render_vector(path, n_nails, out_png, out_svg, alpha=alpha, bg=bg,
                         fg=color)
        return send_file(out_svg, as_attachment=True,
                          download_name="preview.svg")

    if fmt == "progress_png":
        out_png = os.path.join(run_dir, "progress.png")
        sa.render_progress(path, n_nails, out_png, alpha=alpha, bg=bg, fg=color)
        return send_file(out_png, as_attachment=True,
                          download_name="progress.png")

    if fmt == "simulation_png":
        return send_file(os.path.join(run_dir, "simulation.png"),
                          as_attachment=True, download_name="simulation.png")

    if fmt in ("sequence_json", "sequence_txt"):
        sa.write_sequence(path, run_dir, meta)
        fname = "sequence.json" if fmt == "sequence_json" else "sequence.txt"
        return send_file(os.path.join(run_dir, fname), as_attachment=True,
                          download_name=fname)

    if fmt == "template_pdf":
        out_pdf = os.path.join(run_dir, "nail_template_full.pdf")
        if not os.path.exists(out_pdf):
            tmpl.full_page(out_pdf, n_nails, meta["diameter_in"], 0.35, 10)
        return send_file(out_pdf, as_attachment=True,
                          download_name="nail_template_full.pdf")

    return jsonify(error=f"unknown export format: {fmt}"), 400


if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)
