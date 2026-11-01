#!/usr/bin/env python3
"""
place.py -- winding companion for an already-solved sequence.

    python place.py [--sequence examples/cu-buffaloes/sequence.json] [--port 5002]

Shows the next nail to wrap, saves your position to disk on every move, and
tracks a live ETA. Open http://127.0.0.1:5002 next to the board.

Single-user, local tool. Do not expose this past localhost.
"""

import argparse
import json
import os
import shutil
import time

from flask import Flask, jsonify, render_template, request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(APP_DIR, "place_state.json")

# A gap longer than this means you walked away, so it does not count toward
# working time or the per-nail average.
IDLE_CUTOFF_S = 300.0
# Number of recent move gaps the average is drawn from. Short enough to track
# how fast you are winding right now, long enough to smooth out fumbles.
RATE_WINDOW = 40
MAX_MOVE_TIMES = 400

app = Flask(__name__)

SEQ = {}


def load_sequence(seq_path):
    with open(seq_path) as f:
        data = json.load(f)
    path = data["path"]
    SEQ.update(
        file=os.path.abspath(seq_path),
        name=os.path.basename(seq_path),
        path=path,
        n_nails=data["n_nails"],
        n_lines=len(path) - 1,
        meta=data,
    )


def default_state():
    return {"sequence": SEQ["name"], "index": 0, "total_working_seconds": 0.0,
            "move_times": []}


def read_state():
    if not os.path.exists(STATE_PATH):
        return default_state()
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_state()
    # A state file from a different sequence would point at meaningless
    # indices, so start over rather than silently mis-position the board.
    if state.get("sequence") != SEQ["name"]:
        return default_state()
    state["index"] = max(0, min(int(state.get("index", 0)), SEQ["n_lines"]))
    return state


def write_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def stats(state):
    times = state.get("move_times", [])
    gaps = [b - a for a, b in zip(times, times[1:])
            if 0 < b - a <= IDLE_CUTOFF_S]
    recent = gaps[-RATE_WINDOW:]
    avg = sum(recent) / len(recent) if recent else None

    index = state["index"]
    remaining = SEQ["n_lines"] - index
    return {
        "index": index,
        "n_lines": SEQ["n_lines"],
        "remaining": remaining,
        "avg_seconds": avg,
        "samples": len(recent),
        "total_working_seconds": state.get("total_working_seconds", 0.0),
        "eta_seconds": avg * remaining if avg else None,
    }


def payload(state):
    return {"index": state["index"], "stats": stats(state)}


def record_move(state, new_index):
    now = time.time()
    times = state.setdefault("move_times", [])
    if times:
        gap = now - times[-1]
        if 0 < gap <= IDLE_CUTOFF_S:
            state["total_working_seconds"] = (
                state.get("total_working_seconds", 0.0) + gap)
    times.append(now)
    del times[:-MAX_MOVE_TIMES]
    state["index"] = new_index
    state["sequence"] = SEQ["name"]
    write_state(state)


@app.route("/")
def index():
    return render_template("place.html")


@app.route("/api/init")
def api_init():
    state = read_state()
    return jsonify(
        path=SEQ["path"],
        n_nails=SEQ["n_nails"],
        n_lines=SEQ["n_lines"],
        sequence=SEQ["name"],
        idle_cutoff=IDLE_CUTOFF_S,
        **payload(state),
    )


@app.route("/api/move", methods=["POST"])
def api_move():
    delta = int(request.get_json(force=True).get("delta", 1))
    state = read_state()
    new_index = max(0, min(state["index"] + delta, SEQ["n_lines"]))
    if new_index != state["index"]:
        record_move(state, new_index)
    return jsonify(**payload(state))


@app.route("/api/search", methods=["POST"])
def api_search():
    pins = request.get_json(force=True).get("pins") or []
    try:
        pins = [int(p) % SEQ["n_nails"] for p in pins]
    except (TypeError, ValueError):
        return jsonify(error="pins must be integers"), 400
    if not pins:
        return jsonify(error="enter at least one pin"), 400

    path, n = SEQ["path"], len(pins)
    # Index i means "the string is now at path[i]", so a match ending at i
    # says the last n nails you wrapped were exactly `pins`.
    matches = [i for i in range(n - 1, len(path))
               if path[i - n + 1:i + 1] == pins]
    return jsonify(matches=matches, count=len(matches))


@app.route("/api/jump", methods=["POST"])
def api_jump():
    target = int(request.get_json(force=True).get("index", 0))
    if not 0 <= target <= SEQ["n_lines"]:
        return jsonify(error="index out of range"), 400

    backup = None
    if os.path.exists(STATE_PATH):
        backup = f"{STATE_PATH}.bak.{int(time.time())}"
        shutil.copy2(STATE_PATH, backup)

    state = read_state()
    record_move(state, target)
    return jsonify(backup=os.path.basename(backup) if backup else None,
                   **payload(state))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sequence", default="examples/cu-buffaloes/sequence.json")
    ap.add_argument("--port", type=int, default=5002)
    args = ap.parse_args()

    seq_path = args.sequence
    if not os.path.isabs(seq_path):
        seq_path = os.path.join(APP_DIR, seq_path)
    load_sequence(seq_path)
    print(f"{SEQ['name']}: {SEQ['n_lines']} lines over {SEQ['n_nails']} nails")
    print(f"http://127.0.0.1:{args.port}")
    app.run(port=args.port, threaded=True)
