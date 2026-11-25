"""Builds benchmarks.json for the app from Epoch AI and LMArena.

Usage: python3 tools/build_benchmarks.py models.json <previous output dir> <output dir>

Both sources are CC BY 4.0. Only benchmarks Epoch AI runs itself are used, because
external results in Epoch's hub keep their original licenses. Scores are matched to
models.dev ids by normalized model name, and keep the thinking level they were run at.
"""
import csv, io, json, os, re, sys, time, urllib.parse, urllib.request, zipfile

MODELS, PREV, OUT = sys.argv[1:4]
UA = {"User-Agent": "AIReleaseTrackerBot/1.0 (+https://github.com/iammitul/ai-release-data)"}
NOW = int(time.time())

EPOCH_ZIP = "https://epoch.ai/data/benchmark_data.zip"
# Epoch-run benchmarks the app shows, with plain names and what they test.
EPOCH = [
    ("gpqa_diamond", "GPQA Diamond", "PhD-level science questions"),
    ("otis_mock_aime_2024_2025", "AIME", "Competition math"),
    ("frontiermath_tiers_1_3_v2", "FrontierMath", "Research-level math"),
    ("frontiermath_tier_4_v2", "FrontierMath Tier 4", "Hardest research math"),
    ("swe_bench_verified", "SWE-bench Verified", "Fixing real GitHub issues"),
    ("simpleqa_verified", "SimpleQA", "Getting short facts right"),
    ("math_level_5", "MATH Level 5", "Hard school math"),
    ("mirrorcode", "MirrorCode", "Rebuilding software from scratch"),
    ("chess_puzzles", "Chess puzzles", "Reasoning through chess"),
]
ARENAS = [
    ("text_style_control", "text", "Text Arena", "People's votes on chat answers"),
    ("webdev", "webdev", "WebDev Arena", "People's votes on built web apps"),
    ("vision", "vision", "Vision Arena", "People's votes on image questions"),
]

LEVELS = {"none": "none", "minimal": "minimal", "low": "low", "medium": "medium", "high": "high",
          "xhigh": "xhigh", "max": "max", "promax": "max", "thinking": "thinking", "unknown": None}


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90).read()


def key(name):
    """'Claude Opus 5.5 (High)', 'claude-opus-5-5', 'gpt-6.2-astra' and 'gpt-5-2025-08-07' all
    reduce to a comparable form."""
    s = name.lower().strip()
    s = s.split("/")[-1]
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[\s._:]+", "-", s)
    s = re.sub(r"-(20\d\d-\d\d-\d\d|20\d{6}|\d{4})$", "", s)       # dated snapshots
    s = re.sub(r"-(together|fireworks|vertex|bedrock|azure|api)$", "", s)  # hosting suffixes
    return s.strip("-")


def split_epoch(version):
    """'gemini-4-neon_xhigh' -> ('gemini-4-neon', 'xhigh'); 'claude-opus-4-6_32K' -> ('claude-opus-4-6', '32k')"""
    if "_" in version:
        base, lvl = version.rsplit("_", 1)
        low = lvl.lower()
        if low in LEVELS:
            return base, LEVELS[low]
        if re.fullmatch(r"\d+k", low):
            return base, low  # thinking budget in tokens
    return version, None


# Words that change a name without changing the model, dropped for a looser second try.
LOOSE_DROP = re.compile(r"-(instruct|chat|hf|fp8|bf16|preview|exp|latest|fallback|oss|it|\d+e|0)(?=-|$)")


def loose(k):
    prev = None
    while prev != k:
        prev, k = k, LOOSE_DROP.sub("", k)
    return k


def split_arena(name):
    """'Gemini 4 Neon (High)' -> ('Gemini 4 Neon', 'high')"""
    m = re.search(r"\(([^)]*)\)\s*$", name)
    if m:
        lvl = m.group(1).strip().lower().replace(" ", "")
        lvl = {"highthinking": "high", "thinking": "thinking", "reasoning": "thinking"}.get(lvl, lvl)
        return name[: m.start()].strip(), LEVELS.get(lvl, lvl if lvl in LEVELS.values() else None)
    return name, None


def model_index():
    models = json.load(open(MODELS))
    exact, loose_index = {}, {}
    for mid, m in models.items():
        for k in {key(mid), key(m.get("name") or "")}:
            if k:
                exact.setdefault(k, set()).add(mid)
                loose_index.setdefault(loose(k), set()).add(mid)
    return exact, loose_index


# Source names that only matched through the looser second try, for the report.
LOOSE_HITS = {}


def match(index, name):
    exact, loose_index = index
    k = key(name)
    ids = exact.get(k)
    if not ids:
        ids = loose_index.get(loose(k))
        for mid in ids or ():
            LOOSE_HITS.setdefault(mid, set()).add(name)
    return sorted(ids) if ids else []


def epoch(index, out, unmatched):
    z = zipfile.ZipFile(io.BytesIO(get(EPOCH_ZIP)))
    names = set(z.namelist())
    for bid, title, desc in EPOCH:
        fname = f"{bid}.csv"
        if fname not in names:
            continue
        for row in csv.DictReader(io.StringIO(z.read(fname).decode("utf-8", "replace"))):
            version = (row.get("Model version") or "").strip()
            raw = row.get("mean_score") or row.get("Best score (across scorers)")
            if not version or not raw:
                continue
            try:
                score = float(raw)
            except ValueError:
                continue
            base, level = split_epoch(version)
            ids = match(index, base)
            if not ids:
                unmatched.add(f"epoch:{base}")
                continue
            for mid in ids:
                out.setdefault(mid, []).append({"src": "epoch", "bench": bid, "level": level, "score": round(score, 4)})


def arena(index, out, unmatched):
    q = urllib.parse.quote
    where = q('"category"=\'overall\'')
    for config, bid, title, desc in ARENAS:
        offset = 0
        while True:
            url = ("https://datasets-server.huggingface.co/filter?dataset=lmarena-ai/leaderboard-dataset"
                   f"&config={config}&split=latest&where={where}&offset={offset}&length=100")
            data = json.loads(get(url))
            rows = [r["row"] for r in data.get("rows", [])]
            for r in rows:
                base, level = split_arena(r.get("model_name") or "")
                ids = match(index, base)
                if not ids:
                    unmatched.add(f"arena:{base}")
                    continue
                for mid in ids:
                    out.setdefault(mid, []).append({
                        "src": "lmarena", "bench": bid, "level": level,
                        "score": round(float(r["rating"]), 1), "rank": int(float(r["rank"])),
                        "votes": int(float(r.get("vote_count") or 0)),
                    })
            offset += len(rows)
            if not rows or offset >= data.get("num_rows_total", 0):
                break


def main():
    os.makedirs(OUT, exist_ok=True)
    prev = os.path.join(PREV, "benchmarks.json")
    index = model_index()
    out, unmatched, errors = {}, set(), []
    for name, fn in (("Epoch AI", epoch), ("LMArena", arena)):
        try:
            fn(index, out, unmatched)
        except Exception as ex:
            errors.append(f"{name}: {type(ex).__name__}: {ex}"[:200])
    if errors and os.path.exists(prev):
        # Keep yesterday's scores rather than publishing a partial set.
        old = json.load(open(prev))
        old.setdefault("errors", errors)
        json.dump(old, open(os.path.join(OUT, "benchmarks.json"), "w"), separators=(",", ":"))
        print("kept previous benchmarks:", errors)
        return
    # One score per model, benchmark and thinking level: the best run.
    for mid, rows in out.items():
        best = {}
        for r in rows:
            k = (r["src"], r["bench"], r["level"])
            if k not in best or r["score"] > best[k]["score"]:
                best[k] = r
        out[mid] = sorted(best.values(), key=lambda r: (r["src"], r["bench"], r["level"] or ""))
    doc = {
        "updated": NOW,
        "sources": {
            "epoch": {"name": "Epoch AI", "url": "https://epoch.ai/benchmarks", "license": "CC BY 4.0"},
            "lmarena": {"name": "LMArena", "url": "https://lmarena.ai/leaderboard", "license": "CC BY 4.0"},
        },
        "benchmarks": (
            [{"id": b, "src": "epoch", "name": t, "desc": d, "unit": "percent"} for b, t, d in EPOCH] +
            [{"id": b, "src": "lmarena", "name": t, "desc": d, "unit": "elo"} for _, b, t, d in ARENAS]
        ),
        "models": out,
    }
    json.dump(doc, open(os.path.join(OUT, "benchmarks.json"), "w"), separators=(",", ":"))
    with open(os.path.join(OUT, "benchmarks_report.md"), "w") as f:
        f.write(f"# Benchmarks {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(NOW))}\n\n")
        f.write(f"{len(out)} models with scores. Errors: {errors or 'none'}\n\n")
        f.write(f"Unmatched names ({len(unmatched)}):\n\n" + "\n".join(sorted(unmatched)) + "\n")
        f.write(f"\nLoose matches ({len(LOOSE_HITS)} models), check these by eye:\n\n")
        f.write("\n".join(f"{mid} <= {', '.join(sorted(n))}" for mid, n in sorted(LOOSE_HITS.items())) + "\n")
    print(f"benchmarks: {len(out)} models, {sum(len(v) for v in out.values())} scores, {len(unmatched)} unmatched")


main()
