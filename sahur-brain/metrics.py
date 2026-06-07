"""
metrics.py — summarize grounding telemetry written by tap_semantic.

Every semantic tap logs whether it resolved via the Moss INDEX (fast, pre-crawled)
or the LOCAL fallback ranker (or matched nothing), plus latency and whether the tap
actually changed the screen. This tells you the index is paying off.

    python metrics.py            # summary
    python metrics.py --tail 20  # last 20 raw events
"""
import collections
import json
import os
import sys

F = os.environ.get("SAHUR_METRICS_FILE", os.path.join(os.path.dirname(__file__), "sahur_metrics.jsonl"))


def main():
    if not os.path.exists(F):
        print(f"no metrics yet at {F} — run some voice commands first.")
        return
    rows = [json.loads(l) for l in open(F) if l.strip()]
    if not rows:
        print("metrics file is empty."); return

    if "--tail" in sys.argv:
        k = int(sys.argv[sys.argv.index("--tail") + 1]) if len(sys.argv) > sys.argv.index("--tail") + 1 else 20
        for r in rows[-k:]:
            print(f"  [{r['source']:5s} {r.get('ms',0):>5}ms] {r['app'].split('.')[-1]:14s} "
                  f"{r['target']!r} -> {r.get('label')!r} ({r.get('score')}) {'✓' if r.get('changed') else '✗'}")
        return

    n = len(rows)
    src = collections.Counter(r["source"] for r in rows)
    changed = sum(1 for r in rows if r.get("changed"))
    moss = src.get("moss", 0)
    print(f"grounding events: {n}")
    print(f"  ⚡ moss index : {moss:4d}  ({moss * 100 // n}%)")
    print(f"  ·  local     : {src.get('local', 0):4d}  ({src.get('local',0) * 100 // n}%)")
    print(f"  ✗  none      : {src.get('none', 0):4d}  ({src.get('none',0) * 100 // n}%)")
    print(f"  taps that changed the screen: {changed}/{n} ({changed * 100 // n}%)")
    ms = [r["ms"] for r in rows if r["source"] == "moss" and r.get("ms")]
    if ms:
        print(f"  moss latency: avg {sum(ms)/len(ms):.0f}ms  max {max(ms):.0f}ms")

    per = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        per[r["app"]][0] += 1
        if r["source"] == "moss":
            per[r["app"]][1] += 1
    print("\n  per app  (moss-hit / total):")
    for app, (t, mh) in sorted(per.items(), key=lambda x: -x[1][0]):
        bar = "█" * (mh * 10 // max(t, 1))
        print(f"    {app.split('.')[-1]:16s} {mh:3d}/{t:<3d} {bar}")

    miss = [r for r in rows if not r.get("changed")][-8:]
    if miss:
        print("\n  recent misses (no match / screen didn't change):")
        for r in miss:
            print(f"    [{r['source']}] {r['app'].split('.')[-1]}: {r['target']!r} -> {r.get('label')}")


if __name__ == "__main__":
    main()
