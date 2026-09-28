"""Build the list of National Trust places and write it as Supabase seed SQL.

Sources (all open data, fetched fresh unless cached in build/cache/):
  * Wikidata - items owned (P127) or operated (P137) by the National Trust
    (Q333515) that have coordinates. CC0.
  * OpenStreetMap via Overpass - named features with operator=National Trust,
    and the NT's own walking routes, whose website tags point at the NT page
    for each place (/visit/<region>/<place>/<walk>). ODbL.
  * Wikipedia REST summaries - the one-line description. CC BY-SA.

The National Trust website itself is bot-protected, so it is linked to, never
scraped.

Output: build/seed_places.sql (upserts into public.places) and
build/places.json (for inspection). Both are gitignored: the place list is only
served to logged-in users, so it is not published in this public repo.

Usage:  python tools/build_places.py [--refresh]
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "build", "cache")
UA = "NationalTrustMap/1.0 (https://github.com/NewSnoochy/NationalTrust)"
NT = "Q333515"
UK_BBOX = (49.8, -8.3, 56.0, 1.9)          # NT covers England, Wales, N. Ireland
REFRESH = "--refresh" in sys.argv


def http_json(url, data=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - network flakiness, retry
            if i == tries - 1:
                raise
            print("  retry", url[:80], e)
            time.sleep(5 * (i + 1))


def cached(name, fn):
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and not REFRESH:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    data = fn()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


# ---------------------------------------------------------------- Wikidata
def wd_entities(ids):
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        u = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "format": "json",
            "props": "labels|descriptions|claims|sitelinks", "languages": "en", "sitefilter": "enwiki"})
        out.update(http_json(u)["entities"])
        time.sleep(0.3)
    return out


def fetch_wikidata():
    q = f"""SELECT DISTINCT ?item WHERE {{
      {{ ?item wdt:P127 wd:{NT} }} UNION {{ ?item wdt:P137 wd:{NT} }}
      ?item wdt:P625 ?c . }}"""
    r = http_json("https://query.wikidata.org/sparql", urllib.parse.urlencode({"query": q}).encode())
    ids = [b["item"]["value"].rsplit("/", 1)[1] for b in r["results"]["bindings"]]
    return wd_entities(ids)


def claim_vals(e, p):
    out = []
    for c in e.get("claims", {}).get(p, []):
        try:
            out.append((c["mainsnak"]["datavalue"]["value"], c))
        except KeyError:
            pass
    return out


def wd_label_map(ids):
    ents = wd_entities(ids)
    return {k: v.get("labels", {}).get("en", {}).get("value", "") for k, v in ents.items()}


# ---------------------------------------------------- Wikipedia categories
def wp_api(params):
    u = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(dict(params, format="json", formatversion=2))
    return http_json(u)


def fetch_wp_categories():
    """Every article under 'National Trust properties in England/Wales/NI' (and
    their county subcategories), with coordinates and Wikidata id."""
    roots = ["Category:National Trust properties in England", "Category:National Trust properties in Wales",
             "Category:National Trust properties in Northern Ireland"]
    queue, seen, titles = [(c, 0) for c in roots], set(roots), set()
    while queue:
        cat, depth = queue.pop(0)
        cont = {}
        while True:
            r = wp_api(dict({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmlimit": 500,
                             "cmtype": "page|subcat"}, **cont))
            for m in r["query"]["categorymembers"]:
                if m["ns"] == 14 and "National Trust" in m["title"] and m["title"] not in seen and depth < 2:
                    seen.add(m["title"])
                    queue.append((m["title"], depth + 1))
                elif m["ns"] == 0:
                    titles.add(m["title"])
            if "continue" not in r:
                break
            cont = r["continue"]
    titles = sorted(t for t in titles if not t.startswith("List of"))
    out = {}
    for i in range(0, len(titles), 50):
        r = wp_api({"action": "query", "titles": "|".join(titles[i:i + 50]), "prop": "coordinates|pageprops",
                    "ppprop": "wikibase_item", "redirects": 1})
        for p in r["query"]["pages"]:
            co = (p.get("coordinates") or [None])[0]
            out[p["title"]] = {"qid": p.get("pageprops", {}).get("wikibase_item"),
                               "coord": (co["lat"], co["lon"]) if co else None}
        time.sleep(0.2)
    print(f"  {len(seen)} categories, {len(out)} articles")
    return out


# --------------------------------------------------------------- Overpass
def fetch_osm():
    s, w, n, e = UK_BBOX
    ql = f"""[out:json][timeout:240][bbox:{s},{w},{n},{e}];
    ( nwr["operator"="National Trust"]["name"]; nwr["operator"="The National Trust"]["name"]; );
    out center tags;"""
    body = urllib.parse.urlencode({"data": ql}).encode()
    for ep in ("https://overpass.kumi.systems/api/interpreter", "https://overpass-api.de/api/interpreter"):
        try:
            return http_json(ep, body, tries=2)
        except Exception as ex:  # noqa: BLE001
            print("  overpass failed at", ep, ex)
    raise SystemExit("Overpass unavailable - try again later")


# -------------------------------------------------------------- categories
CATS = ["house", "castle", "garden", "nature", "coast", "ancient", "industry", "church", "other"]

# Checked in order; first keyword hit wins. Ancient forts come before castles
# so a hillfort is never drawn as a castle.
TYPE_RULES = [
    ("ancient", ["archaeolog", "hillfort", "hill fort", "promontory fort", "contour fort", "henge", "barrow",
                 "cromlech", "stone circle", "cursus", "roman", "castrum", "hill figure", "megalith", "tumulus",
                 "cairn", "earthwork", "iron age", "bronze age"]),
    ("castle", ["castle", "fortification", "artillery", "battery", "bunker", "watchtower", "gatehouse",
                "tower house", "motte", "fort"]),
    ("church", ["abbey", "priory", "chapel", "church", "cathedral", "monaster", "friary"]),
    ("industry", ["mill", "mine", "mining", "industrial", "railway", "viaduct", "bridge", "workers", "quarry",
                  "forge", "pump", "brewery", "dovecote", "kiln", "demonstration farm", "farm"]),
    ("coast", ["beach", "cape", "headland", "cliff", "island", "lighthouse", "spit", "stack", "coast",
               "promontory", "port", "bay", "harbour", "dune", "sands"]),
    ("garden", ["garden", "arboretum", "orangery", "urban park", "landscape park", "deer park", "sylvan"]),
    ("nature", ["wood", "heath", "nature reserve", "hill", "summit", "mountain", "valley", "lake", "tarn",
                "reservoir", "meadow", "wetland", "grassland", "downland", "down", "forest", "waterfall",
                "escarpment", "landscape", "protected area", "special scientific", "common", "fell", "moor",
                "marsh", "fen", "gorge", "pass", "country park", "rock", "geograph", "national park", "park",
                "estate", "countryside"]),
    ("house", ["house", "manor", "mansion", "cottage", "hall", "residence", "villa", "château", "chateau",
               "hotel", "terrace", "back-to-back", "home", "birthplace", "court", "grange", "lodge"]),
    ("other", ["museum", "building", "folly", "monument", "memorial", "obelisk", "barn", "pub", "inn",
               "post office", "market", "theatre", "library", "tower", "temple", "school", "tearoom"]),
]

# Words in the NAME that settle the category outright.
NAME_RULES = [
    ("church", r"\b(abbey|priory|chapel|church|friary)\b"),
    ("castle", r"\b(castle|fort|battery)\b"),
    ("industry", r"\b(mill|mine|colliery|works|forge|brewhouse|quarry|railway|pumping)\b"),
    ("garden", r"\b(gardens?|arboretum)\b"),
    ("ancient", r"\b(henge|barrow|hillfort|camp|stone circle|roman|cromlech|long man|giant)\b"),
    ("coast", r"\b(beach|bay|head|point|island|cove|sands|dunes|cliffs?|ness|spit|lighthouse|haven)\b"),
    ("house", r"\b(house|hall|manor|court|cottage|mansion|palace|grange|place|villa|lodge)\b"),
    ("nature", r"\b(woods?|heath|hill|down|downs|common|forest|fell|moor|valley|tarn|lake|marsh|fen|nature reserve|estate|park|meadows?|gorge|waterfall|force)\b"),
]
PRIORITY = ["castle", "house", "church", "ancient", "industry", "garden", "coast", "nature", "other"]


def cat_from_text(texts):
    found = set()
    for t in texts:
        t = t.lower()
        for cat, kws in TYPE_RULES:
            if any(k in t for k in kws):
                found.add(cat)
                break
    for c in PRIORITY:
        if c in found:
            return c
    return None


def cat_from_name(name):
    n = name.lower()
    for cat, rx in NAME_RULES:
        if re.search(rx, n):
            return cat
    return None


def cat_from_osm(t):
    h, l, nat, tour = t.get("historic", ""), t.get("leisure", ""), t.get("natural", ""), t.get("tourism", "")
    if h == "castle": return "castle"
    if h in ("manor", "house", "mansion"): return "house"
    if h in ("archaeological_site", "roman_road", "hillfort", "tumulus", "stone"): return "ancient"
    if h in ("church", "abbey", "monastery", "chapel", "wayside_shrine"): return "church"
    if h in ("mill", "mine", "industrial") or t.get("man_made") in ("windmill", "watermill", "mineshaft"): return "industry"
    if l == "garden": return "garden"
    if nat in ("beach", "cliff", "coastline", "cape", "bay") or t.get("place") == "island": return "coast"
    if l in ("nature_reserve", "common") or nat in ("wood", "heath", "grassland", "peak", "water", "scrub", "wetland") \
            or t.get("boundary") == "protected_area" or t.get("landuse") in ("forest", "meadow"):
        return "nature"
    if l == "park": return "garden"
    if tour == "museum": return "other"
    return None


# -------------------------------------------------------------- utilities
def haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))  # km


STOP = {"the", "and", "of", "national", "trust", "nt", "at", "&", "a", "on", "in", "st", "saint"}
GENERIC = {"house", "hall", "garden", "gardens", "estate", "park", "castle", "abbey", "priory", "and",
           "manor", "wood", "woods", "common", "hill", "countryside", "museum", "farm", "place", "court"}


def tokens(name):
    return {w for w in re.findall(r"[a-z0-9']+", name.lower()) if w not in STOP}


def names_match(a, b):
    ta, tb = tokens(a) - GENERIC, tokens(b) - GENERIC
    if not ta or not tb:
        ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return inter / min(len(ta), len(tb)) >= 0.99 or inter / len(ta | tb) >= 0.5


def in_uk(lat, lon):
    s, w, n, e = UK_BBOX
    return s <= lat <= n and w <= lon <= e


def first_sentence(text, limit=240):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    text = re.sub(r"\s*\([^()]*\)", "", text)  # drop pronunciations/dates in brackets
    m = re.match(r"(.+?[a-z0-9\)\]]\.)(\s|$)", text)
    s = m.group(1) if m else text
    if len(s) > limit:
        s = s[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return s


SMALL = {"and", "of", "the", "on", "at", "in", "to", "by", "for"}


def title_from_slug(slug):
    words = slug.replace("-", " ").split()
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w in SMALL) else w[:1].upper() + w[1:])
    return " ".join(out)


# NT page URLs: keep only /visit/<region>/<place> from a walk or place link.
NT_PLACE_RX = re.compile(r"https?://(?:www\.)?nationaltrust\.org\.uk/visit/([a-z0-9-]+)/([a-z0-9-]+)")


def nt_place_url(url):
    m = NT_PLACE_RX.search(url or "")
    return (f"https://www.nationaltrust.org.uk/visit/{m.group(1)}/{m.group(2)}", m.group(1), m.group(2)) if m else None


# ------------------------------------------------------------------- build
NOT_PLACES = {NT, "Q18160511"}   # the Trust itself; Heelis, its head office
BAD_WD_TYPES = ("organization", "organisation", "business", "wikimedia", "charit", "civil parish",
                "human settlement", "village")
BAD_OSM_NAME = re.compile(r"\b(car ?park|parking|visitor cent(re|er)|toilets?|wc|kiosk|shop|caf[eé]|tea ?room|"
                          r"restaurant|play ?(area|ground)|entrance|reception|ticket|information|walk|trail|"
                          r"holiday|cottage \d|bothy|campsite|office|welcome|bench|gate|stile|hide|"
                          r"picnic|admission|field|acres?|ground|avenue|ice ?house|plantation)\b", re.I)
# Bare feature names that only make sense as part of somewhere else.
GENERIC_NAME = re.compile(r"^(the )?(walled|kitchen|rose|water|formal|old)? ?(garden|gardens|walled garden/rose garden|"
                          r"sand dunes|church house|old school house|old rectory|engine house|round house|watch house|"
                          r"chase|coombes|lake|woods?|park|meadows?)$", re.I)


def clean_name(n):
    n = n.split(";")[0].strip()
    n = re.sub(r"\s*[\(\[](national trust|nt)[\)\]]", "", n, flags=re.I)
    n = re.sub(r"^(the )?national trust[ ,:-]+", "", n, flags=re.I)
    n = re.sub(r"[ ,-]+national trust$", "", n, flags=re.I)
    n = re.sub(r"\s+-\s+open access land$", "", n, flags=re.I)
    return n.strip()


def main():
    os.makedirs(CACHE, exist_ok=True)
    print("Wikidata…")
    wd = cached("wikidata.json", fetch_wikidata)
    print(" ", len(wd), "items")
    print("OpenStreetMap…")
    osm = cached("osm.json", fetch_osm)["elements"]
    print(" ", len(osm), "features")
    print("Wikipedia categories…")
    wpc = cached("wp_categories.json", fetch_wp_categories)

    # Wikidata items linked from OSM features or category articles but not
    # owned-by-NT in Wikidata: fetch them too, for types and descriptions.
    extra = {e["tags"]["wikidata"] for e in osm if re.fullmatch(r"Q\d+", e["tags"].get("wikidata", ""))}
    extra |= {v["qid"] for v in wpc.values() if v["qid"]}
    extra -= set(wd)
    wd_extra = cached("wikidata_extra.json", lambda: wd_entities(sorted(extra)))
    missing = sorted(extra - set(wd_extra))
    if missing:
        wd_extra.update(wd_entities(missing))
        with open(os.path.join(CACHE, "wikidata_extra.json"), "w", encoding="utf-8") as f:
            json.dump(wd_extra, f)

    type_ids = {v["id"] for e in list(wd.values()) + list(wd_extra.values()) for v, _ in claim_vals(e, "P31")}
    type_labels = cached("type_labels.json", lambda: wd_label_map(sorted(type_ids)))

    def wd_info(q, e):
        types = [type_labels.get(v["id"], "") for v, _ in claim_vals(e, "P31")]
        coords = claim_vals(e, "P625")
        site = e.get("sitelinks", {}).get("enwiki", {}).get("title")
        webs = [v for v, _ in claim_vals(e, "P856")]
        return {
            "name": e.get("labels", {}).get("en", {}).get("value", ""),
            "wd_desc": e.get("descriptions", {}).get("en", {}).get("value", ""),
            "types": types,
            "coord": (coords[0][0]["latitude"], coords[0][0]["longitude"]) if coords else None,
            "wiki": site,
            "website": webs[0] if webs else None,
            "qid": q,
        }

    places = []
    for q, e in wd.items():
        info = wd_info(q, e)
        if not info["name"] or not info["coord"] or not in_uk(*info["coord"]):
            continue
        tl = " ".join(info["types"]).lower()
        if info["types"] and all(any(b in t.lower() for b in BAD_WD_TYPES) for t in info["types"]):
            continue
        if "organization" in tl and not re.search(r"house|garden|park|castle", info["name"], re.I):
            continue
        cat = cat_from_name(info["name"]) or cat_from_text(info["types"]) or cat_from_text([info["wd_desc"]]) or "other"
        places.append({
            "id": q, "name": info["name"], "lat": info["coord"][0], "lon": info["coord"][1], "cat": cat,
            "wd_desc": info["wd_desc"], "wiki": info["wiki"], "qid": q, "website": info["website"],
            "osm_desc": None, "src": "wikidata"})
    print(" ", len(places), "Wikidata places kept")
    by_qid = {p["qid"]: p for p in places}

    # ---- Wikipedia category articles
    wp_new = 0
    for title, v in wpc.items():
        if v["qid"] in by_qid:
            by_qid[v["qid"]]["wiki"] = by_qid[v["qid"]]["wiki"] or title
            continue
        info = wd_info(v["qid"], wd_extra[v["qid"]]) if v["qid"] in wd_extra else None
        coord = v["coord"] or (info or {}).get("coord")
        if not coord or not in_uk(*coord):
            continue
        types = (info or {}).get("types", [])
        if types and all(any(b in t.lower() for b in BAD_WD_TYPES + ("person", "human", "county", "district",
                                                                         "town", "city", "river", "film", "family"))
                         for t in types):
            continue
        name = re.sub(r"\s*\([^)]*\)$", "", title)       # "Hill Top (house)" -> "Hill Top"
        cat = cat_from_name(name) or cat_from_text(types) or cat_from_text([(info or {}).get("wd_desc", "")]) or "other"
        p = {"id": v["qid"] or "wp-" + re.sub(r"\W+", "-", title.lower()), "name": name, "lat": coord[0], "lon": coord[1],
             "cat": cat, "wd_desc": (info or {}).get("wd_desc", ""), "wiki": title, "qid": v["qid"],
             "website": (info or {}).get("website"), "osm_desc": None, "src": "wikipedia"}
        places.append(p)
        if v["qid"]:
            by_qid[v["qid"]] = p
        wp_new += 1
    print(f"  Wikipedia categories: {wp_new} new places")

    # ---- OSM places (not routes, not facilities)
    routes, cands = [], []
    for el in osm:
        t = el["tags"]
        c = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
        if not c:
            continue
        if t.get("type") == "route" or "route" in t:
            routes.append((el, c))
            continue
        if any(k in t for k in ("amenity", "shop", "highway", "building:part", "craft", "office")) and "tourism" not in t:
            continue
        if t.get("tourism") in ("information", "chalet", "hotel", "hostel", "camp_site", "apartment", "caravan_site",
                                "guest_house", "picnic_site", "artwork"):
            continue
        if t.get("leisure") in ("playground", "fitness_station", "bird_hide", "pitch", "slipway", "picnic_table"):
            continue
        if t.get("boundary") == "marker" or t.get("landuse") in ("farmland", "farmyard", "grass", "orchard",
                                                               "recreation_ground", "allotments"):
            continue
        interesting = ("tourism" in t or "historic" in t or t.get("leisure") in ("park", "garden", "nature_reserve", "common")
                       or t.get("natural") in ("wood", "heath", "beach", "peak", "cliff", "grassland", "water")
                       or t.get("boundary") == "protected_area" or t.get("landuse") in ("forest", "meadow")
                       or "wikidata" in t)
        name = clean_name(t["name"])
        if not interesting or BAD_OSM_NAME.search(name) or GENERIC_NAME.match(name) or len(name) < 3:
            continue
        t["name"] = name
        cands.append((el, c))

    def osm_rank(ec):
        t = ec[0]["tags"]
        return (0 if "wikidata" in t else 1, 0 if t.get("tourism") in ("attraction", "museum") else 1,
                0 if "website" in t else 1, {"relation": 0, "way": 1, "node": 2}[ec[0]["type"]])

    added = merged = 0
    for el, c in sorted(cands, key=osm_rank):
        t = el["tags"]
        pos = (c["lat"], c["lon"])
        qid = t.get("wikidata")
        target = by_qid.get(qid)
        if not target:
            near = [p for p in places if haversine(pos, (p["lat"], p["lon"])) < 5]
            target = next((p for p in near if names_match(p["name"], t["name"])), None)
            if not target and any(haversine(pos, (p["lat"], p["lon"])) < 0.8 for p in near):
                merged += 1      # a part of a place we already have (a lawn, a wood on the estate)
                continue
        if target:
            target["website"] = target["website"] or t.get("website")
            target["osm_desc"] = target["osm_desc"] or t.get("description")
            merged += 1
            continue
        info = wd_info(qid, wd_extra[qid]) if qid in wd_extra else None
        name = t["name"]
        cat = cat_from_name(name) or cat_from_osm(t) or (cat_from_text(info["types"]) if info else None) or "other"
        wiki = t.get("wikipedia", "")
        p = {"id": f"osm-{el['type'][0]}{el['id']}", "name": name, "lat": pos[0], "lon": pos[1], "cat": cat,
             "wd_desc": info["wd_desc"] if info else "", "wiki": (info or {}).get("wiki") or (wiki[3:] if wiki.startswith("en:") else None),
             "qid": qid, "website": t.get("website") or (info or {}).get("website"),
             "osm_desc": t.get("description"), "src": "osm"}
        places.append(p)
        if qid:
            by_qid[qid] = p
        added += 1
    print(f"  OSM: {added} new places, {merged} merged into existing ones")

    # ---- NT walks: group by the NT place page they belong to
    groups = {}
    for el, c in routes:
        t = el["tags"]
        hit = None
        for u in (t.get("website") or "").split(";"):
            hit = nt_place_url(u)
            if hit:
                break
        if not hit:
            continue
        g = groups.setdefault(hit[0], {"url": hit[0], "region": hit[1], "slug": hit[2], "pts": [], "walks": [], "suffixes": []})
        g["pts"].append((c["lat"], c["lon"]))
        walk_url = next((u for u in t["website"].split(";") if "nationaltrust.org.uk" in u), t["website"].split(";")[0])
        wname = t["name"]
        if ", " in wname:
            wname, suffix = wname.rsplit(", ", 1)
            g["suffixes"].append(suffix)
        g["walks"].append({"name": wname, "url": walk_url, "miles": round(float(t["distance"]) / 1.609, 1)
                           if re.fullmatch(r"[\d.]+", t.get("distance", "")) else None})

    walk_new = walk_linked = 0
    for g in groups.values():
        centre = (sum(p[0] for p in g["pts"]) / len(g["pts"]), sum(p[1] for p in g["pts"]) / len(g["pts"]))
        slug_name = title_from_slug(g["slug"])
        suffix = max(set(g["suffixes"]), key=g["suffixes"].count) if g["suffixes"] else slug_name
        near = sorted((p for p in places if haversine(centre, (p["lat"], p["lon"])) < 6),
                      key=lambda p: haversine(centre, (p["lat"], p["lon"])))
        target = next((p for p in near if names_match(p["name"], suffix) or names_match(p["name"], slug_name)), None)
        if target:
            target.setdefault("walks", []).extend(g["walks"])
            target["nt_url"] = target.get("nt_url") or g["url"]
            walk_linked += 1
            continue
        # The walk-name suffix is often the NT's grouping ("North Cornwall"),
        # not the place, so it is only used when it agrees with the page slug.
        name = suffix if names_match(suffix, slug_name) else slug_name
        places.append({"id": "nt-" + g["slug"], "name": name, "lat": centre[0], "lon": centre[1],
                       "cat": cat_from_name(name) or "nature", "wd_desc": "", "wiki": None, "qid": None,
                       "website": None, "osm_desc": None, "src": "walks", "nt_url": g["url"],
                       "region": title_from_slug(g["region"]), "walks": g["walks"]})
        walk_new += 1
    print(f"  walks: {len(groups)} NT place pages, {walk_linked} linked to known places, {walk_new} new places")

    # ---- Fold the parts of one visit into a single marker: Tyntesfield chapel,
    # Tatton Park Gardens and Chirk Castle Museum are all one day out. Anything
    # within 350 m, or within 1.5 km with a matching name, joins a cluster; the
    # best-documented member becomes the marker and the others' Wikipedia pages
    # stay on its card.
    places = [p for p in places if p["id"] not in NOT_PLACES]
    parent = list(range(len(places)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(places):
        for j in range(i + 1, len(places)):
            b = places[j]
            d = haversine((a["lat"], a["lon"]), (b["lat"], b["lon"]))
            if d < 0.35 or (d < 1.5 and names_match(a["name"], b["name"])):
                parent[find(i)] = find(j)
    clusters = {}
    for i, p in enumerate(places):
        clusters.setdefault(find(i), []).append(p)

    def importance(p):
        return (3 * bool(p.get("nt_url")) + 2 * bool(p.get("walks")) + (p["src"] == "wikidata")
                + bool(p.get("wiki")) + (p["cat"] != "other"), -len(p["name"]))
    def rootness(p, members):
        # How many other members extend this one's name ("Tyntesfield" is the
        # root of "Tyntesfield chapel"), so the whole place names the marker.
        tk = tokens(p["name"])
        return sum(1 for m in members if m is not p and tk and tk < tokens(m["name"]))
    merged_places = []
    for members in clusters.values():
        snapshot = list(members)          # list.sort empties the list while it runs
        members.sort(key=lambda p: (rootness(p, snapshot), importance(p)), reverse=True)
        top = members[0]
        top["also"] = []
        for m in members[1:]:
            top["nt_url"] = top.get("nt_url") or m.get("nt_url")
            top["website"] = top.get("website") or m.get("website")
            top.setdefault("walks", []).extend(m.get("walks", []))
            if not top.get("wiki") and m.get("wiki"):
                top["wiki"] = m["wiki"]
            elif m.get("wiki") and m["wiki"] != top.get("wiki"):
                top["also"].append((m["name"], m["wiki"]))
        merged_places.append(top)
    print(f"  merged {len(places)} candidates into {len(merged_places)} places")
    places = merged_places

    # ---- NT page from website where it is an NT link
    for p in places:
        if not p.get("nt_url") and p.get("website"):
            for u in p["website"].split(";"):
                if "nationaltrust.org.uk" in u:
                    p["nt_url"] = nt_place_url(u)[0] if nt_place_url(u) else u.strip()
                    break

    # ---- Wikipedia summaries
    titles = sorted({p["wiki"] for p in places if p.get("wiki")})

    spath = os.path.join(CACHE, "wikipedia.json")
    old = {}
    if os.path.exists(spath) and not REFRESH:
        with open(spath, encoding="utf-8") as f:
            old = json.load(f)
    todo = [tt for tt in titles if tt not in old]

    def fetch_summaries():
        out = dict(old)
        for i, tt in enumerate(todo):
            u = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(tt.replace(" ", "_"), safe="")
            try:
                out[tt] = http_json(u, tries=2).get("extract", "")
            except Exception:  # noqa: BLE001 - a missing page just has no summary
                out[tt] = ""
            if i % 50 == 0:
                print(f"   {i}/{len(todo)}")
            time.sleep(0.05)
        return out
    print("Wikipedia summaries…")
    summaries = fetch_summaries()
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(summaries, f)

    CAT_WORD = {"house": "Historic house", "castle": "Castle", "garden": "Garden", "nature": "Countryside",
                "coast": "Coastline", "ancient": "Ancient site", "industry": "Industrial heritage",
                "church": "Religious site", "other": "Place"}
    rows = []
    for p in places:
        desc = first_sentence(summaries.get(p.get("wiki") or "", ""))
        if not desc and p.get("osm_desc") and not p["osm_desc"].startswith("http"):
            desc = first_sentence(p["osm_desc"])
        if not desc and p.get("wd_desc"):
            desc = p["wd_desc"][:1].upper() + p["wd_desc"][1:] + "."
        if not desc:
            desc = f"{CAT_WORD[p['cat']]} cared for by the National Trust" + (f" in the {p['region']} area." if p.get("region") else ".")
        links = []
        if p.get("nt_url"):
            links.append({"label": "National Trust", "url": p["nt_url"]})
        else:
            links.append({"label": "National Trust (search)",
                          "url": "https://www.nationaltrust.org.uk/search?query=" + urllib.parse.quote(p["name"])})
        if p.get("website") and "nationaltrust.org.uk" not in p["website"]:
            links.append({"label": "Website", "url": p["website"].split(";")[0].strip()})
        if p.get("wiki"):
            links.append({"label": "Wikipedia", "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(p["wiki"].replace(" ", "_"))})
        for aname, awiki in p.get("also", [])[:4]:
            links.append({"label": aname, "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(awiki.replace(" ", "_"))})
        if p.get("qid"):
            links.append({"label": "Wikidata", "url": "https://www.wikidata.org/wiki/" + p["qid"]})
        walks = sorted({w["url"]: w for w in p.get("walks", [])}.values(), key=lambda w: w["name"])
        rows.append({"id": p["id"], "name": p["name"], "lat": round(p["lat"], 5), "lon": round(p["lon"], 5),
                     "cat": p["cat"], "descr": desc, "links": links, "walks": walks})
    rows.sort(key=lambda r: r["name"])

    with open(os.path.join(ROOT, "build", "places.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    def sq(s):
        return "'" + str(s).replace("'", "''") + "'"
    lines = ["-- Generated by tools/build_places.py - safe to re-run: it upserts, never deletes,",
             "-- so your visits/ratings/notes are never touched.",
             "-- Data: Wikidata (CC0), OpenStreetMap contributors (ODbL), Wikipedia (CC BY-SA).",
             "insert into public.places (id, name, lat, lon, cat, descr, links, walks) values"]
    vals = [f"({sq(r['id'])},{sq(r['name'])},{r['lat']},{r['lon']},{sq(r['cat'])},{sq(r['descr'])},"
            f"{sq(json.dumps(r['links'], ensure_ascii=False))}::jsonb,{sq(json.dumps(r['walks'], ensure_ascii=False))}::jsonb)"
            for r in rows]
    lines.append(",\n".join(vals))
    lines.append("on conflict (id) do update set name=excluded.name, lat=excluded.lat, lon=excluded.lon, cat=excluded.cat,"
                 " descr=excluded.descr, links=excluded.links, walks=excluded.walks;")
    with open(os.path.join(ROOT, "build", "seed_places.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    from collections import Counter
    print(f"\n{len(rows)} places written.", dict(Counter(r["cat"] for r in rows)))
    print("with NT page:", sum(any(l["label"] == "National Trust" for l in r["links"]) for r in rows),
          " with walks:", sum(bool(r["walks"]) for r in rows))


if __name__ == "__main__":
    main()
