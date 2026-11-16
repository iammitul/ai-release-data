"""Builds models.json from a checkout of the open source models.dev catalog.

Usage: python3 tools/build_data.py <path to models.dev checkout> <output folder>
The output uses the same shape as https://models.dev/models.json, trimmed to the
fields the app reads.
"""
import sys, os, json, tomllib
root, out = sys.argv[1], sys.argv[2]
def mod(m):
    if m == "application/pdf": return "pdf"
    if m in ("decision", "embedding", "reranking"): return "text"
    return m.split("/")[0]
def uniq(xs):
    r = []
    for x in xs:
        if x not in r: r.append(x)
    return r
models = {}
for d, _, fs in os.walk(os.path.join(root, "models")):
    for f in fs:
        if not f.endswith(".toml"): continue
        p = os.path.join(d, f)
        t = tomllib.load(open(p, "rb"))
        if t.get("v2_only") is True or t.get("type") == "decision": continue
        if not t.get("release_date"): continue
        mid = os.path.relpath(p, os.path.join(root, "models"))[:-5].replace(os.sep, "/")
        m = {"id": mid, "name": t["name"], "description": t.get("description", ""), "release_date": str(t["release_date"]),
             "open_weights": bool(t.get("open_weights", False)),
             "modalities": {"input": uniq(map(mod, t.get("modalities", {}).get("input", []))),
                            "output": uniq(map(mod, t.get("modalities", {}).get("output", [])))}}
        if t.get("type") and t["type"] != "audio": m["type"] = t["type"]
        if t.get("limit"): m["limit"] = {k: v for k, v in t["limit"].items() if k in ("context", "output")}
        if t.get("license"): m["license"] = t["license"]
        if t.get("links"): m["links"] = [{k: l[k] for k in ("label", "url", "type") if k in l} for l in t["links"]]
        if t.get("benchmarks"): m["benchmarks"] = [{k: b[k] for k in ("name", "score", "metric") if k in b} for b in t["benchmarks"]]
        if t.get("capabilities", {}).get("reasoning", {}).get("supported"): m["reasoning"] = True
        models[mid] = m
if len(models) < 50:
    sys.exit(f"Only {len(models)} models found, refusing to write")
os.makedirs(out, exist_ok=True)
json.dump(dict(sorted(models.items())), open(os.path.join(out, "models.json"), "w"), ensure_ascii=False, separators=(",", ":"))
print("models written:", len(models), "newest:", max(m["release_date"] for m in models.values()))
