"""Builds the News, Products, Repos and Prices feeds the app reads.

Usage: python3 tools/build_feeds.py tools/sources.json <previous output dir> <output dir>

Each run merges what the sources publish now with the previous output, so items
stay available for a few days after they drop out of a source's own feed.
Only titles, a short trimmed summary and links are kept. The full article stays
on the publisher's site.
"""
import sys, os, json, re, time, html, hashlib, calendar, urllib.request, urllib.parse
import concurrent.futures as cf
import feedparser

SOURCES, PREV, OUT = sys.argv[1:4]
NOW = int(time.time())
DAY = 86400
UA = "AIReleaseTrackerBot/1.0 (+https://github.com/iammitul/ai-release-data)"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 AIReleaseTracker"
TOKEN = os.environ.get("GH_TOKEN", "")
KEEP_DAYS = {"news": 7, "products": 21, "repos": 30}
CAP = {"news": 600, "products": 400, "repos": 300}
SUMMARY_LEN = 280
GH_CALLS = 450          # GitHub API budget per run (the Actions token allows 1,000 an hour)
REPO_MIN_STARS = 20
STATE_VERSION = 2       # bump to rebuild repo history from scratch
RULES_VERSION = 3       # bump when News/Products filters change, so kept items are sorted again

# Lab blogs mix product news with research and stories. Products keeps posts that are
# tagged as products or announce something; News gets the rest.
# Clear launches always count as product updates.
STRONG_LAUNCH_RE = re.compile(
    r"\b(introduc\w*|launch\w*|now available|available (?:now|today|in|on|to|for)|rolling out|rolls out|"
    r"general availability|unveil\w*|debut\w*|is here|are here|coming to|arrives?|now in beta|is now|are now|"
    r"can now|now (?:lets|supports?|works|has|with))\b"
    # Feature posts often start with an instruction: "Turn artifacts into apps".
    r"|^(turn|create|make|connect|share|say hello to|bring)\b", re.I)
# Company news (money, deals, people, policy) goes to News even when it "announces" something.
COMPANY_RE = re.compile(
    r"\b(ipo|initial public offering|funding|raises|series [a-f]|acquires|acquisition|appoints|hires|earnings|"
    r"lawsuit|testimony|letter|policy|board|valuation)\b", re.I)
# Stories, guides, interviews and customer case studies go to News.
STORY_RE = re.compile(
    r"(^(how|why|what|when|where|who|inside|behind|lessons|a look)\b)|\b(helps?|uses?|used|using|partners? with|"
    r"customers?|case study|guide|interview|podcast|webinar|on ai\b|explains?|ways to|tips|story|stories|"
    r"perspective|reflections?|q&a)\b", re.I)
WEAK_LAUNCH_RE = re.compile(
    r"\b(releas\w*|announc\w*|new|update[sd]?|ships?|shipping|brings?|adds?|gets|expand\w*|beta|preview|"
    r"pricing|upgrade\w*|features?|try|meet)\b", re.I)
PRODUCT_TAGS = {"product", "products", "product updates", "release notes", "gemini app", "gemini models", "gemini",
                "developer tools", "google labs", "chatgpt", "codex", "api", "claude", "announcements",
                "copilot", "github copilot", "changelog", "search", "android", "chrome", "maps", "google workspace",
                "pixel", "workspace"}
NOT_PRODUCT_TAGS = {"science", "research", "webinars", "startup", "guides", "demos", "engineering",
                    "global affairs", "safety", "ai adoption", "openai on openai", "publication",
                    "intelligence age", "arts & culture", "policy", "learning", "education", "customer stories",
                    "company announcements (deprecated)"}


def is_product(title, tags):
    tags = {t.lower().strip() for t in tags}
    if COMPANY_RE.search(title):
        return False
    if STRONG_LAUNCH_RE.search(title):
        return True
    if STORY_RE.search(title) or tags & NOT_PRODUCT_TAGS:
        return False
    return bool(WEAK_LAUNCH_RE.search(title) or tags & PRODUCT_TAGS)

AI_WORDS = (r"ai|a\.i\.|llms?|gpts?|gpt-\w+|agents?|agentic|machine learning|deep learning|ml|"
            r"neural|claude|gemini|chatgpt|openai|anthropic|mcp|model context protocol|rag|"
            r"diffusion|copilot|inference|transformers?|chatbots?|embeddings?|genai|generative|"
            r"ollama|llama|mistral|qwen|deepseek|whisper|fine-?tun\w*|lora|vlm|multimodal|"
            r"language models?|text-to-\w+|speech-to-text|tts|vllm|langchain|pytorch|hugging ?face|"
            r"reasoning models?|vector database|prompt\w*")
AI_RE = re.compile(r"\b(" + AI_WORDS + r")\b", re.I)
AI_TOPICS = {"ai", "llm", "llms", "machine-learning", "deep-learning", "artificial-intelligence",
             "generative-ai", "genai", "agents", "ai-agents", "agent", "mcp", "rag", "chatgpt",
             "openai", "claude", "gemini", "llama", "diffusion", "stable-diffusion", "nlp",
             "transformers", "pytorch", "computer-vision", "inference", "vllm", "langchain",
             "ollama", "embeddings", "vector-database", "multimodal", "speech-recognition", "tts"}

TAG = re.compile(r"<[^>]+>")
JUNK = re.compile(r"\s*(Discussion\s*\|\s*Link|The post .{0,200} appeared first on .{0,80}\.?|Read more|Continue reading.*)\s*$", re.I)
IMG = re.compile(r"<img[^>]+src=[\"'](https?://[^\"']+)[\"']", re.I)
GH = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})")
GH_RESERVED = {"features", "topics", "sponsors", "marketplace", "apps", "settings", "login", "about",
               "pricing", "enterprise", "collections", "trending", "orgs", "users", "blog", "site",
               "security", "customer-stories", "readme", "events", "explore", "notifications",
               "new", "organizations", "contact", "resources", "solutions", "team", "github-copilot",
               "copilot", "codespaces", "advisories", "search", "pulls", "issues", "join", "signup"}

report = []


def log(*a):
    print(*a, flush=True)


def get(url, accept="*/*", headers=None):
    h = {"User-Agent": UA, "Accept": accept}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return r.read()


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(name, data):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def text(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = TAG.sub(" ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def trim(s, n=SUMMARY_LEN):
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(",;:-(") + "…"


def canon(u):
    p = urllib.parse.urlsplit(u.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith(("utm_", "ref", "fbclid", "gclid", "mc_", "source"))]
    return urllib.parse.urlunsplit(((p.scheme or "https").lower(), p.netloc.lower(), p.path,
                                    urllib.parse.urlencode(q), ""))


def item_id(url):
    return hashlib.sha1(canon(url).encode()).hexdigest()[:16]


def when(e):
    for k in ("published_parsed", "updated_parsed", "created_parsed"):
        t = e.get(k)
        if t:
            return min(calendar.timegm(t), NOW)
    return None


def good_image(u):
    if not u or not u.startswith("http"):
        return False
    low = u.lower()
    return not any(x in low for x in ("pixel", "1x1", "feedburner", "gravatar", "emoji", ".svg"))


def image(e):
    for k in ("media_content", "media_thumbnail"):
        for m in e.get(k) or []:
            u = m.get("url")
            if good_image(u) and m.get("medium") in (None, "image"):
                return u
    for l in e.get("links") or []:
        if l.get("rel") == "enclosure" and (l.get("type") or "").startswith("image") and good_image(l.get("href")):
            return l.get("href")
    for blob in [e.get("summary") or ""] + [c.get("value") or "" for c in e.get("content") or []]:
        m = IMG.search(blob)
        if m and good_image(m.group(1)):
            return m.group(1)
    return None


# ---------------------------------------------------------------- fetching

def fetch_source(src):
    """Returns (entries, used_url, error). Entries are feedparser entries or dicts for APIs."""
    urls = src["url"] if isinstance(src["url"], list) else [src["url"]]
    err = None
    attempts = [(u, ua) for u in urls for ua in (None, BROWSER_UA)]
    for u, ua in attempts:
        try:
            raw = get(u, accept="application/json" if src.get("kind") == "hf_papers"
                      else "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                      headers={"User-Agent": ua} if ua else None)
            if src.get("kind") == "hf_papers":
                return json.loads(raw), u, None
            parsed = feedparser.parse(raw)
            if not parsed.entries:
                err = "no entries" + (f" ({parsed.bozo_exception})" if parsed.bozo else "")
                continue
            return parsed.entries, u, None
        except Exception as ex:
            err = f"{type(ex).__name__}: {ex}"[:160]
    return [], None, err


# ---------------------------------------------------------------- news and products

def items_from(src, entries):
    out = []
    for e in entries[:40]:
        link, title = e.get("link"), text(e.get("title"))
        if not link or not title:
            continue
        summary = "" if src.get("nosummary") else trim(JUNK.sub("", text(e.get("summary") or "")))
        if summary and summary.lower().rstrip("…").strip() in title.lower():
            summary = ""
        if src.get("filter") == "ai" and not AI_RE.search(f"{title} {summary}"):
            continue
        if src.get("filter") in ("product", "notproduct"):
            tags = [t.get("term") or "" for t in e.get("tags") or []]
            if is_product(title, tags) != (src["filter"] == "product"):
                continue
        out.append({"id": item_id(link), "src": src["id"], "title": title, "summary": summary,
                    "url": link, "image": image(e), "ts": when(e)})
    return out


def build_tab(tab, sources, fetched):
    prev_file = load(os.path.join(PREV, f"{tab}.json"), {})
    # When the sorting rules change, rebuild from the sources instead of keeping old items,
    # but remember when each item was first seen so nothing looks new again.
    seen_before = {}
    for other in ("news", "products"):
        for i in load(os.path.join(PREV, f"{other}.json"), {}).get("items", []):
            seen_before.setdefault(i["id"], i.get("seen"))
    prev = {i["id"]: i for i in prev_file.get("items", [])} if prev_file.get("rules") == RULES_VERSION else {}
    merged, cutoff = {}, NOW - KEEP_DAYS[tab] * DAY
    for src in sources:
        entries = fetched[src["id"]][0]
        kept = 0
        for it in items_from(src, entries):
            if it["id"] in merged:
                continue
            old = prev.get(it["id"])
            it["seen"] = old["seen"] if old else (seen_before.get(it["id"]) or NOW)
            if it["ts"] is None:
                it["ts"] = it["seen"]
            if it["ts"] >= cutoff:
                merged[it["id"]] = it
                kept += 1
        fetched[src["id"]][3]["kept"] = kept
    by_id = {s["id"]: s for s in sources}
    for pid, it in prev.items():
        src = by_id.get(it["src"])
        if pid in merged or it["ts"] < cutoff or src is None:
            continue
        # Older items get the same cleanup and filters as fresh ones, so rule changes apply to them too.
        if it.get("summary"):
            it["summary"] = JUNK.sub("", it["summary"]).strip() or None
        if src.get("nosummary"):
            it.pop("summary", None)
        if src.get("filter") == "ai" and not AI_RE.search(f"{it['title']} {it.get('summary') or ''}"):
            continue
        merged[pid] = it
    items = sorted(merged.values(), key=lambda i: i["ts"], reverse=True)[:CAP[tab]]
    for i in items:
        if not i.get("image"):
            i.pop("image", None)
        if not i.get("summary"):
            i.pop("summary", None)
    save(f"{tab}.json", {"updated": NOW, "rules": RULES_VERSION, "items": items})
    return len(items)


# ---------------------------------------------------------------- repos

def repos_in(*blobs):
    found = []
    for b in blobs:
        for owner, repo in GH.findall(b or ""):
            repo = re.sub(r"\.git$", "", repo).rstrip(".")
            if owner.lower() in GH_RESERVED or not repo or repo.startswith("."):
                continue
            full = f"{owner}/{repo}"
            if full.lower() not in (f.lower() for f in found):
                found.append(full)
    return found


def repo_candidates(sources, fetched):
    cands, cutoff = {}, NOW - 7 * DAY
    for src in sources:
        entries = fetched[src["id"]][0]
        names = []
        if src.get("kind") == "hf_papers":
            for x in entries if isinstance(entries, list) else []:
                p = x.get("paper") or {}
                if p.get("githubRepo"):
                    names += repos_in(p["githubRepo"])
        else:
            for e in entries[:60]:
                t = when(e)
                if t and t < cutoff:
                    continue
                blobs = [e.get("link")] if src.get("linkonly") else \
                    [e.get("link"), e.get("summary")] + [c.get("value") for c in e.get("content") or []]
                names += repos_in(*blobs)
        for full in names:
            c = cands.setdefault(full.lower(), {"full": full, "via": []})
            if src["id"] not in c["via"]:
                c["via"].append(src["id"])
        fetched[src["id"]][3]["repos"] = len(set(n.lower() for n in names))
    return cands


def gh_repo(full):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    try:
        d = json.loads(get(f"https://api.github.com/repos/{full}", headers=h))
    except urllib.error.HTTPError as ex:
        if ex.code in (404, 451):
            return {"missing": True}
        raise
    return {
        "full": d["full_name"], "desc": d.get("description") or "", "stars": d.get("stargazers_count", 0),
        "lang": d.get("language"), "topics": d.get("topics") or [], "url": d["html_url"],
        "home": d.get("homepage") or None, "fork": d.get("fork", False), "archived": d.get("archived", False),
        "created": calendar.timegm(time.strptime(d["created_at"], "%Y-%m-%dT%H:%M:%SZ")),
        "license": (d.get("license") or {}).get("spdx_id"),
        "avatar": (d.get("owner") or {}).get("avatar_url"),
    }


def is_ai(meta):
    if set(t.lower() for t in meta.get("topics", [])) & AI_TOPICS:
        return True
    return bool(AI_RE.search(f"{meta.get('full', '')} {meta.get('desc', '')}"))


def build_repos(sources, fetched, state):
    known = state.setdefault("repos", {})
    cands = repo_candidates(sources, fetched)
    today = time.strftime("%Y-%m-%d", time.gmtime(NOW))
    calls = 0
    # Look up new candidates first, then refresh stale ones while budget lasts.
    order = sorted(cands, key=lambda k: k in known)
    order += [k for k in known if k not in cands]
    for key in order:
        st = known.get(key)
        stale = not st or NOW - st.get("fetched", 0) > (12 * 3600 if st.get("ai") else 7 * DAY)
        if not stale or calls >= GH_CALLS:
            continue
        if not st and key not in cands:
            continue
        calls += 1
        try:
            meta = gh_repo((st or {}).get("meta", {}).get("full") or cands[key]["full"])
        except Exception as ex:
            log("github api", key, ex)
            if "403" in str(ex) or "429" in str(ex):
                break
            continue
        st = known.setdefault(key, {"first": NOW, "via": []})
        st["fetched"] = NOW
        if meta.get("missing"):
            st["ai"] = False
            continue
        st["meta"] = meta
        st["ai"] = is_ai(meta) and not meta["fork"] and not meta["archived"]
        hist = st.setdefault("stars", {})
        hist[today] = meta["stars"]
        for d in sorted(hist)[:-10]:
            del hist[d]
    for key, c in cands.items():
        st = known.get(key)
        if st:
            for v in c["via"]:
                if v not in st.setdefault("via", []):
                    st["via"].append(v)
    cutoff = NOW - KEEP_DAYS["repos"] * DAY
    for key in [k for k, st in known.items() if st.get("first", NOW) < cutoff and k not in cands]:
        del known[key]

    items = []
    for key, st in known.items():
        m = st.get("meta")
        if not st.get("ai") or not m or st.get("first", 0) < cutoff:
            continue
        trending = any(v.startswith("gh-trending") for v in st["via"])
        if m["stars"] < REPO_MIN_STARS and not trending:
            continue
        it = {"id": "gh:" + key, "src": st["via"][0], "via": st["via"], "title": m["full"],
              "summary": trim(m["desc"]), "url": m["url"], "ts": st["first"], "seen": st["first"],
              "stars": m["stars"], "lang": m["lang"], "topics": m["topics"][:4],
              "license": m["license"], "created": m["created"], "home": m["home"]}
        if m.get("avatar"):
            it["image"] = m["avatar"] + ("&" if "?" in m["avatar"] else "?") + "s=128"
        hist = st.get("stars", {})
        if today in hist:
            age = lambda d: NOW - calendar.timegm(time.strptime(d, "%Y-%m-%d"))
            older = [d for d in sorted(hist) if d != today and age(d) <= 7.5 * DAY]
            if older:
                it["gain"] = hist[today] - hist[older[0]]
                it["gainDays"] = max(1, round(age(older[0]) / DAY))
        items.append({k: v for k, v in it.items() if v not in (None, "", [])})
    items.sort(key=lambda i: i["ts"], reverse=True)
    items = items[:CAP["repos"]]
    save("repos.json", {"updated": NOW, "items": items})
    log(f"repos: {len(cands)} candidates, {calls} GitHub lookups, {len(items)} shown")
    return len(items), len(cands), calls


# ---------------------------------------------------------------- prices

PRICE_FIELDS = ("input", "output", "cache", "batch_input", "batch_output")


def build_prices():
    prev = load(os.path.join(PREV, "prices.json"), {})
    try:
        data = json.loads(get("https://llmprices.org/api/v1/prices", accept="application/json"))
        models = data["models"]
    except Exception as ex:
        log("prices failed", ex)
        if prev:
            save("prices.json", prev)
        return f"failed: {ex}"[:120]
    before = {m["api_id"]: m for m in prev.get("models", [])}
    changes = prev.get("changes", [])
    if before:
        for m in models:
            old = before.get(m["api_id"])
            if not old:
                changes.append({"model": m["api_id"], "name": m["name"], "vendor": m["vendor"],
                                "kind": "new", "ts": NOW})
                continue
            for f in PRICE_FIELDS:
                if old.get(f) is not None and m.get(f) is not None and old[f] != m[f]:
                    changes.append({"model": m["api_id"], "name": m["name"], "vendor": m["vendor"],
                                    "kind": "change", "field": f, "old": old[f], "new": m[f], "ts": NOW})
            if m.get("price_change_date") and m.get("price_change_date") != old.get("price_change_date"):
                changes.append({"model": m["api_id"], "name": m["name"], "vendor": m["vendor"],
                                "kind": "announced", "date": m["price_change_date"],
                                "input": m.get("input_after"), "output": m.get("output_after"), "ts": NOW})
    changes = [c for c in changes if c["ts"] >= NOW - 180 * DAY][-400:]
    keep = ("name", "api_id", "vendor", "input", "output", "cache", "batch_input", "batch_output",
            "context_window", "price_change_date", "input_after", "output_after", "deprecated")
    slim = []
    for m in models:
        x = {k: m[k] for k in keep if m.get(k) is not None}
        primary = next((p for p in m.get("prices") or [] if p.get("primary")), None)
        if primary and primary.get("source"):
            x["pricingUrl"] = primary["source"].get("url")
            x["verified"] = primary["source"].get("verified_at")
        slim.append(x)
    save("prices.json", {"updated": NOW, "sourceUpdated": data.get("updated"),
                         "source": "llmprices.org", "sourceUrl": "https://llmprices.org",
                         "license": data.get("license"), "models": slim, "changes": changes})
    return f"ok, {len(models)} models, {len(changes)} changes on record"


# ---------------------------------------------------------------- main

def main():
    os.makedirs(OUT, exist_ok=True)
    sources = load(SOURCES, [])
    state = load(os.path.join(PREV, "state.json"), {})
    if state.get("v") != STATE_VERSION:
        state = {"v": STATE_VERSION}
    # Several sources share a feed URL (one for news, one for repos), so fetch each URL once.
    by_url, fetched = {}, {}
    with cf.ThreadPoolExecutor(12) as pool:
        futs = {}
        for s in sources:
            key = json.dumps(s["url"])
            if key not in futs:
                futs[key] = pool.submit(fetch_source, s)
        for key, fut in futs.items():
            by_url[key] = fut.result()
    for s in sources:
        entries, used, err = by_url[json.dumps(s["url"])]
        fetched[s["id"]] = (entries, used, err, {})

    counts = {}
    for tab in ("news", "products"):
        counts[tab] = build_tab(tab, [s for s in sources if s["tab"] == tab], fetched)
    counts["repos"], cands, calls = build_repos([s for s in sources if s["tab"] == "repos"], fetched, state)
    prices = build_prices()

    out_sources = []
    for s in sources:
        entries, used, err, stats = fetched[s["id"]]
        prev_ok = (state.get("health", {}).get(s["id"]) or {}).get("lastOk")
        ok = err is None
        state.setdefault("health", {})[s["id"]] = {"lastOk": NOW if ok else prev_ok, "error": err}
        out_sources.append({k: v for k, v in {
            "id": s["id"], "name": s["name"], "tab": s["tab"], "group": s["group"], "home": s["home"],
            "desc": s.get("desc"), "on": s.get("on", True), "ok": ok,
        }.items() if v is not None})
        report.append(f"| {s['tab']} | {s['id']} | {'ok' if ok else 'FAIL'} | {len(entries)} | "
                      f"{stats.get('kept', stats.get('repos', ''))} | {(used or '')[:70]} | {err or ''} |")
    save("sources.json", {"updated": NOW, "sources": out_sources})
    save("state.json", state)
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write(f"# Feed report {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(NOW))}\n\n")
        f.write(f"News {counts['news']}, Products {counts['products']}, Repos {counts['repos']} "
                f"({cands} candidates, {calls} GitHub lookups). Prices: {prices}\n\n")
        f.write("| tab | source | status | entries | kept/repos | url | error |\n|---|---|---|---|---|---|---|\n")
        f.write("\n".join(report) + "\n")
    log(open(os.path.join(OUT, "report.md")).read())


main()
