"""Keeps the date each model first appeared in models.dev, shown as "Added" in the app.

Usage: build_added.py <models.json fallback> <prev dir> <out dir>

Dates come from three places, earliest wins:
  - tools/added_seed.json: worked out once from the models.dev git history (the day each
    model's file was first committed, under providers/ or models/)
  - prev/added.json: what this job published last time
  - today (UTC) for any model id seen for the first time
The app decides when a date is worth showing (not before the release date, not the
models.dev launch week), so this file keeps the raw first-seen date.
"""
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "ai-release-data (github.com/iammitul/ai-release-data)"}


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def model_ids(fallback):
    try:
        raw = urllib.request.urlopen(urllib.request.Request("https://models.dev/models.json", headers=UA), timeout=60).read()
        data = json.loads(raw)
    except Exception as ex:  # models.dev down: use the copy in this repo
        print("models.dev unreachable, using", fallback, ex)
        data = load(fallback) or {}
    return [k for k in data if isinstance(k, str) and "/" in k]


def main(fallback, prev, out):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    added = {}
    for source in (load(os.path.join(HERE, "added_seed.json")) or {}, (load(os.path.join(prev, "added.json")) or {}).get("added", {})):
        for k, v in source.items():
            if isinstance(v, str) and len(v) == 10 and (k not in added or v < added[k]):
                added[k] = v
    ids = model_ids(fallback)
    fresh = [k for k in ids if k not in added]
    for k in fresh:
        added[k] = today
    if not ids and not added:
        print("no models and no previous dates; not writing added.json")
        return
    with open(os.path.join(out, "added.json"), "w") as f:
        json.dump({"updated": int(datetime.datetime.now(datetime.timezone.utc).timestamp()), "added": dict(sorted(added.items()))},
                  f, separators=(",", ":"))
    print(f"added.json: {len(added)} models, {len(fresh)} new today")


if __name__ == "__main__":
    main(*sys.argv[1:4])
