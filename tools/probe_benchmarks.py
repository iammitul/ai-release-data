"""One-off: show what the Epoch AI and LMArena files contain."""
import io, json, zipfile, csv, urllib.request, urllib.parse
UA = {"User-Agent": "AIReleaseTrackerBot/1.0"}
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read()

z = zipfile.ZipFile(io.BytesIO(get("https://epoch.ai/data/benchmark_data.zip")))
print("EPOCH FILES:", [(i.filename, i.file_size) for i in z.infolist()][:80])
for name in z.namelist():
    if name.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(z.read(name).decode("utf-8", "replace"))))
        print(f"\n== {name} rows={len(rows)}")
        for r in rows[:3]:
            print("   ", r[:16])
q = urllib.parse.quote
for cfg in ("text_style_control", "text"):
    u = ("https://datasets-server.huggingface.co/filter?dataset=lmarena-ai/leaderboard-dataset"
         f"&config={cfg}&split=latest&where={q(chr(34)+'category'+chr(34)+chr(61)+chr(39)+'overall'+chr(39))}&offset=0&length=5")
    d = json.loads(get(u))
    print(f"\nLMARENA {cfg}: total={d.get('num_rows_total')}")
    for r in d.get("rows", []):
        print("   ", r["row"])
