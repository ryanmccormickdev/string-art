#!/usr/bin/env python3
"""
caller.py -- reads the winding sequence out loud so you never lose your place.

Standard library only. Run it next to the board.

    python caller.py out/sequence.json
    python caller.py out/sequence.json --speak
    python caller.py out/sequence.json --speak --auto 5

Controls (manual mode):
    Enter / n   next line
    b           back one
    <number>    jump to that line
    q           quit (position is saved either way)

Progress is written to <sequence>.progress next to the file, so you can stop
mid-piece, close the laptop, and pick up exactly where you left off.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time


def load_path(p):
    if p.endswith(".json"):
        with open(p) as f:
            return json.load(f)["path"]
    # fall back to the raw comma-separated line at the bottom of sequence.txt
    with open(p) as f:
        for line in reversed(f.read().splitlines()):
            if "," in line and not line.startswith("#"):
                return [int(v) for v in line.split(",")]
    raise SystemExit(f"no path found in {p}")


def find_speaker():
    for cmd, args in (("say", []), ("espeak-ng", ["-s", "170"]),
                      ("espeak", ["-s", "170"]), ("spd-say", ["-w"])):
        if shutil.which(cmd):
            return [cmd] + args
    return None


def say(speaker, text):
    if speaker:
        subprocess.Popen(speaker + [text],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show(i, total, path, t_start):
    cur, nxt = path[i], path[i + 1]
    after = path[i + 2] if i + 2 < len(path) else None
    done = i / total
    bar_w = 40
    filled = int(bar_w * done)
    elapsed = time.time() - t_start
    rate = i / elapsed if elapsed > 5 and i else 0
    eta = f"{(total - i) / rate / 60:.0f} min left" if rate else "--"

    os.system("clear" if os.name != "nt" else "cls")
    print()
    print(f"   line {i + 1} of {total}")
    print()
    print(f"      {cur:>4}   ->   {nxt:<4}")
    print()
    print(f"   next: {nxt} -> {after}" if after is not None else "   next: tie off")
    print()
    print(f"   [{'#' * filled}{'.' * (bar_w - filled)}] {done:5.1%}   {eta}")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sequence", help="out/sequence.json or out/sequence.txt")
    ap.add_argument("--speak", action="store_true",
                    help="read the target nail aloud")
    ap.add_argument("--auto", type=float, default=None,
                    metavar="SEC", help="hands-free: advance every SEC seconds")
    ap.add_argument("--start", type=int, default=None,
                    help="1-based line to start from, overrides saved progress")
    args = ap.parse_args()

    path = load_path(args.sequence)
    total = len(path) - 1
    prog_file = args.sequence + ".progress"

    i = 0
    if args.start is not None:
        i = max(0, args.start - 1)
    elif os.path.exists(prog_file):
        with open(prog_file) as f:
            i = int(json.load(f).get("index", 0))
        if i:
            print(f"resuming at line {i + 1} of {total}. Enter to go, "
                  f"or Ctrl-C and pass --start N.")
            input()

    speaker = find_speaker() if args.speak else None
    if args.speak and not speaker:
        print("no speech binary found (say / espeak / spd-say); "
              "continuing silently")
        time.sleep(1.5)

    t_start = time.time()

    def save():
        with open(prog_file, "w") as f:
            json.dump({"index": i, "total": total}, f)

    try:
        while i < total:
            show(i, total, path, t_start)
            say(speaker, str(path[i + 1]))
            save()

            if args.auto:
                time.sleep(args.auto)
                i += 1
                continue

            cmd = input("   > ").strip().lower()
            if cmd in ("", "n"):
                i += 1
            elif cmd == "b":
                i = max(0, i - 1)
            elif cmd == "q":
                break
            elif cmd.isdigit():
                i = min(total - 1, max(0, int(cmd) - 1))
    except KeyboardInterrupt:
        pass
    finally:
        save()

    if i >= total:
        os.remove(prog_file) if os.path.exists(prog_file) else None
        print(f"\n   done. tie off at nail {path[-1]}.\n")
    else:
        print(f"\n   stopped at line {i + 1} of {total}. "
              f"Next: {path[i]} -> {path[i + 1]}. Saved.\n")


if __name__ == "__main__":
    main()
