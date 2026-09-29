"""Build the list of National Trust and National Trust for Scotland places.

Sources (all open data, fetched fresh unless cached in build/cache/):
  * Wikidata - items owned (P127) or operated (P137) by the Trust, with
    coordinates. CC0.
  * Wikipedia - the "<Trust> properties in ..." category trees, and the
    one-line descriptions (REST summaries). CC BY-SA.
  * OpenStreetMap via Overpass - named features with the Trust as operator,
    and the NT's own walking routes, whose website tags point at the NT page
    for each place (/visit/<region>/<place>/<walk>). ODbL.

Neither Trust's website is scraped; both are only linked to.

Place ids must never change once published: visits (ticks, ratings, notes)
are keyed by them. If build/existing_ids.json exists (a list of ids already in
the database - tools/sync_supabase.py writes it), a merged place keeps any id
it already had rather than taking a new one.

Output: build/places.json and build/seed_places.sql (upserts). Both are
gitignored: the list is only served to signed-in users.

Usage:  python tools/build_places.py [--refresh]
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
CACHE = os.path.join(BUILD, "cache")
UA = "NationalTrustMap/1.0 (https://github.com/NewSnoochy/NationalTrust)"
REFRESH = "--refresh" in sys.argv

ORGS = [
    {
        "key": "nt", "name": "National Trust", "short": "National Trust", "qid": "Q333515",
        "wp_roots": ["Category:National Trust properties in England", "Category:National Trust properties in Wales",
                     "Category:National Trust properties in Northern Ireland"],
        "wp_sub": "National Trust properties",
        "operators": ["National Trust", "The National Trust"],
        "bbox": (49.8, -8.3, 56.0, 1.9),                 # England, Wales, Northern Ireland
        "domain": "nationaltrust.org.uk",
        "page_rx": r"https?://(?:www\.)?nationaltrust\.org\.uk/visit/([a-z0-9-]+)/([a-z0-9-]+)",
        "page_fmt": "https://www.nationaltrust.org.uk/visit/{0}/{1}",
        "search": "https://www.nationaltrust.org.uk/search?query=",
        "not_places": {"Q333515", "Q18160511"},          # the Trust itself; Heelis, its head office
        # Trust places no source above lists: Wikipedia title -> (lat, lon), or
        # None to take the article's own coordinates.
        "extra_titles": {"Mam Tor": None},
        # Pieces no source links to their whole: id -> id of the pin they join.
        "merge_into": {},
    },
    {
        "key": "nts", "name": "National Trust for Scotland", "short": "NT for Scotland", "qid": "Q599997",
        "wp_roots": ["Category:National Trust for Scotland properties"],
        "wp_sub": "National Trust for Scotland",
        "operators": ["National Trust for Scotland", "The National Trust for Scotland", "NTS"],
        "bbox": (54.6, -8.7, 61.0, -0.6),                # Scotland
        "domain": "nts.org.uk",
        "page_rx": r"https?://(?:www\.)?nts\.org\.uk/visit/places/([a-z0-9-]+)",
        "page_fmt": "https://www.nts.org.uk/visit/places/{0}",
        "search": "https://www.nts.org.uk/search?query=",
        "not_places": {"Q599997"},
        "extra_titles": {"Grey Mare's Tail, Moffat Hills": None, "Pass of Killiecrankie": None,
                         "Priorwood Garden": (55.5990, -2.7196), "Balmacara": (57.2830, -5.6390)},
        "merge_into": {                                    # St Kilda's outlying stacks, its NNR outline; Glencoe NNR
            "Q3778175": "Q166479", "Q1849173": "Q166479", "Q1639552": "Q166479",
            "osm-r8307660": "Q166479", "osm-r9390954": "Q92671",
        },
    },
]


def http_json(url, data=None, tries=4, headers=None):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
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


def fetch_wikidata(qid):
    q = f"""SELECT DISTINCT ?item WHERE {{
      {{ ?item wdt:P127 wd:{qid} }} UNION {{ ?item wdt:P137 wd:{qid} }}
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


def fetch_wp_categories(roots, sub):
    """Every article under the root categories (and their subcategories whose
    names contain `sub`), with coordinates and Wikidata id."""
    queue, seen, titles = [(c, 0) for c in roots], set(roots), set()
    while queue:
        cat, depth = queue.pop(0)
        cont = {}
        while True:
            r = wp_api(dict({"action": "query", "list": "categorymembers", "cmtitle": cat, "cmlimit": 500,
                             "cmtype": "page|subcat"}, **cont))
            for m in r["query"]["categorymembers"]:
                if m["ns"] == 14 and sub in m["title"] and m["title"] not in seen and depth < 2:
                    seen.add(m["title"])
                    queue.append((m["title"], depth + 1))
                elif m["ns"] == 0:
                    titles.add(m["title"])
            if "continue" not in r:
                break
            cont = r["continue"]
    out = wp_pages(sorted(t for t in titles if not t.startswith("List of")))
    print(f"  {len(seen)} categories, {len(out)} articles")
    return out


def wp_pages(titles):
    """Coordinates and Wikidata id for each Wikipedia article title."""
    out = {}
    for i in range(0, len(titles), 50):
        r = wp_api({"action": "query", "titles": "|".join(titles[i:i + 50]), "prop": "coordinates|pageprops",
                    "ppprop": "wikibase_item", "redirects": 1})
        for p in r["query"]["pages"]:
            co = (p.get("coordinates") or [None])[0]
            out[p["title"]] = {"qid": p.get("pageprops", {}).get("wikibase_item"),
                               "coord": (co["lat"], co["lon"]) if co else None}
        time.sleep(0.2)
    return out


# --------------------------------------------------------------- Overpass
def fetch_osm(org):
    s, w, n, e = org["bbox"]
    ops = "".join(f'nwr["operator"="{o}"]["name"];' for o in org["operators"])
    ql = f"[out:json][timeout:240][bbox:{s},{w},{n},{e}];({ops});out center tags;"
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
                 "cairn", "earthwork", "iron age", "bronze age", "broch", "crannog", "battlefield"]),
    ("castle", ["castle", "fortification", "artillery", "battery", "bunker", "watchtower", "gatehouse",
                "tower house", "motte", "fort"]),
    ("church", ["abbey", "priory", "chapel", "church", "cathedral", "monaster", "friary", "kirk"]),
    ("industry", ["mill", "mine", "mining", "industrial", "railway", "viaduct", "bridge", "workers", "quarry",
                  "forge", "pump", "brewery", "dovecote", "kiln", "demonstration farm", "farm", "doocot"]),
    ("coast", ["beach", "cape", "headland", "cliff", "island", "lighthouse", "spit", "stack", "coast",
               "promontory", "port", "bay", "harbour", "dune", "sands", "archipelago"]),
    ("garden", ["garden", "arboretum", "orangery", "urban park", "landscape park", "deer park", "sylvan"]),
    ("nature", ["wood", "heath", "nature reserve", "hill", "summit", "mountain", "valley", "lake", "tarn",
                "reservoir", "meadow", "wetland", "grassland", "downland", "down", "forest", "waterfall",
                "escarpment", "landscape", "protected area", "special scientific", "common", "fell", "moor",
                "marsh", "fen", "gorge", "pass", "country park", "rock", "geograph", "national park", "park",
                "estate", "countryside", "glen", "loch", "munro", "corbett"]),
    ("house", ["house", "manor", "mansion", "cottage", "hall", "residence", "villa", "château", "chateau",
               "hotel", "terrace", "back-to-back", "home", "birthplace", "court", "grange", "lodge", "tenement"]),
    ("other", ["museum", "building", "folly", "monument", "memorial", "obelisk", "barn", "pub", "inn",
               "post office", "market", "theatre", "library", "tower", "temple", "school", "tearoom"]),
]

# Words in the NAME that settle the category outright.
NAME_RULES = [
    ("church", r"\b(abbey|priory|chapel|church|friary|kirk)\b"),
    ("castle", r"\b(castle|fort|battery|palace)\b"),
    ("industry", r"\b(mill|mine|colliery|works|forge|brewhouse|quarry|railway|pumping|weaver's)\b"),
    ("garden", r"\b(gardens?|arboretum)\b"),
    ("ancient", r"\b(henge|barrow|hillfort|camp|stone circle|roman|cromlech|long man|giant|broch|battlefield)\b"),
    ("coast", r"\b(beach|bay|head|point|island|isle|cove|sands|dunes|cliffs?|ness|spit|lighthouse|haven)\b"),
    ("house", r"\b(house|hall|manor|court|cottage|mansion|grange|place|villa|lodge|tenement)\b"),
    ("nature", r"\b(woods?|heath|hill|down|downs|common|forest|fell|moor|valley|tarn|lake|marsh|fen|nature reserve|estate|park|meadows?|gorge|waterfall|force|glen|loch|ben|mountain)\b"),
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
    if h in ("archaeological_site", "roman_road", "hillfort", "tumulus", "stone", "battlefield"): return "ancient"
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


STOP = {"the", "and", "of", "national", "trust", "nt", "nts", "for", "scotland", "at", "&", "a", "on", "in", "st", "saint"}
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


def in_bbox(bbox, lat, lon):
    s, w, n, e = bbox
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


def org_page(org, url):
    """The Trust's own page for a place, from any link into it (a walk page,
    say), as (page url, *slug parts); None if the link is not one."""
    m = re.search(org["page_rx"], url or "")
    return (org["page_fmt"].format(*m.groups()), *m.groups()) if m else None


# ------------------------------------------------------------------- build
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
    n = re.sub(r"\s*[\(\[](national trust( for scotland)?|nts?)[\)\]]", "", n, flags=re.I)
    n = re.sub(r"^(the )?national trust( for scotland)?[ ,:-]+", "", n, flags=re.I)
    n = re.sub(r"[ ,-]+national trust( for scotland)?$", "", n, flags=re.I)
    n = re.sub(r"\s+-\s+open access land$", "", n, flags=re.I)
    return n.strip()


def load_existing_ids():
    path = os.path.join(BUILD, "existing_ids.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def build_org(org, existing):
    k = org["key"]
    print(f"\n=== {org['name']}")
    print("Wikidata…")
    wd = cached(f"{k}_wikidata.json", lambda: fetch_wikidata(org["qid"]))
    print(" ", len(wd), "items")
    print("OpenStreetMap…")
    osm = cached(f"{k}_osm.json", lambda: fetch_osm(org))["elements"]
    print(" ", len(osm), "features")
    print("Wikipedia categories…")
    wpc = cached(f"{k}_wp_categories.json", lambda: fetch_wp_categories(org["wp_roots"], org["wp_sub"]))
    extras = [t for t in org["extra_titles"] if t not in wpc]
    if extras:
        wpc.update(wp_pages(extras))
    for t, coord in org["extra_titles"].items():
        if t in wpc:
            wpc[t]["extra"] = True
            if coord:
                wpc[t]["coord"] = coord

    # Wikidata items linked from OSM features or category articles but not
    # owned-by-the-Trust in Wikidata: fetch them too, for types and descriptions.
    extra = {e["tags"]["wikidata"] for e in osm if re.fullmatch(r"Q\d+", e["tags"].get("wikidata", ""))}
    extra |= {v["qid"] for v in wpc.values() if v["qid"]}
    extra -= set(wd)
    wd_extra = cached(f"{k}_wikidata_extra.json", lambda: wd_entities(sorted(extra)))
    missing = sorted(extra - set(wd_extra))
    if missing:
        wd_extra.update(wd_entities(missing))
        with open(os.path.join(CACHE, f"{k}_wikidata_extra.json"), "w", encoding="utf-8") as f:
            json.dump(wd_extra, f)

    type_ids = {v["id"] for e in list(wd.values()) + list(wd_extra.values()) for v, _ in claim_vals(e, "P31")}
    labels_path = os.path.join(CACHE, "type_labels.json")
    type_labels = {}
    if os.path.exists(labels_path) and not REFRESH:
        with open(labels_path, encoding="utf-8") as f:
            type_labels = json.load(f)
    if type_ids - set(type_labels):
        type_labels.update(wd_label_map(sorted(type_ids - set(type_labels))))
        with open(labels_path, "w", encoding="utf-8") as f:
            json.dump(type_labels, f)

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
            "part_of": [v["id"] for v, _ in claim_vals(e, "P361")],
        }

    places = []
    for q, e in wd.items():
        info = wd_info(q, e)
        if not info["name"] or not info["coord"] or not in_bbox(org["bbox"], *info["coord"]):
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
            "osm_desc": None, "src": "wikidata", "part_of": info["part_of"]})
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
        if not coord or not in_bbox(org["bbox"], *coord):
            continue
        types = (info or {}).get("types", [])
        if types and not v.get("extra") and all(any(b in t.lower() for b in BAD_WD_TYPES + ("person", "human", "county", "district",
                                                                         "town", "city", "river", "film", "family"))
                         for t in types):
            continue
        name = re.sub(r"\s*\([^)]*\)$", "", title)       # "Hill Top (house)" -> "Hill Top"
        cat = cat_from_name(name) or cat_from_text(types) or cat_from_text([(info or {}).get("wd_desc", "")]) or "other"
        p = {"id": v["qid"] or "wp-" + re.sub(r"\W+", "-", title.lower()), "name": name, "lat": coord[0], "lon": coord[1],
             "cat": cat, "wd_desc": (info or {}).get("wd_desc", ""), "wiki": title, "qid": v["qid"],
             "website": (info or {}).get("website"), "osm_desc": None, "src": "wikipedia",
             "part_of": (info or {}).get("part_of", [])}
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
        if any(key in t for key in ("amenity", "shop", "highway", "building:part", "craft", "office")) and "tourism" not in t:
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

    # ---- Walks: group by the Trust's page for the place they belong to.
    # Only the National Trust publishes these to OSM with page links, so the
    # NT page pattern (/visit/<region>/<place>) is assumed here.
    groups = {}
    for el, c in routes:
        t = el["tags"]
        hit = None
        for u in (t.get("website") or "").split(";"):
            hit = org_page(org, u)
            if hit and len(hit) == 3:
                break
            hit = None
        if not hit:
            continue
        g = groups.setdefault(hit[0], {"url": hit[0], "region": hit[1], "slug": hit[2], "pts": [], "walks": [], "suffixes": []})
        g["pts"].append((c["lat"], c["lon"]))
        walk_url = next((u for u in t["website"].split(";") if org["domain"] in u), t["website"].split(";")[0])
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
            target["page"] = target.get("page") or g["url"]
            if target["id"] not in existing and "nt-" + g["slug"] in existing:
                target["id"] = "nt-" + g["slug"]          # this place was published under its walks' id
            walk_linked += 1
            continue
        # The walk-name suffix is often the NT's grouping ("North Cornwall"),
        # not the place, so it is only used when it agrees with the page slug.
        name = suffix if names_match(suffix, slug_name) else slug_name
        places.append({"id": "nt-" + g["slug"], "name": name, "lat": centre[0], "lon": centre[1],
                       "cat": cat_from_name(name) or "nature", "wd_desc": "", "wiki": None, "qid": None,
                       "website": None, "osm_desc": None, "src": "walks", "page": g["url"],
                       "region": title_from_slug(g["region"]), "walks": g["walks"]})
        walk_new += 1
    if groups:
        print(f"  walks: {len(groups)} place pages, {walk_linked} linked to known places, {walk_new} new places")

    # ---- Fold the parts of one visit into a single marker: Tyntesfield chapel,
    # Tatton Park Gardens and Chirk Castle Museum are all one day out. Anything
    # within 350 m, or within 1.5 km with a matching name, joins a cluster; the
    # member whose name the others extend names the marker, and the others'
    # Wikipedia pages stay on its card.
    places = [p for p in places if p["id"] not in org["not_places"]]
    parent = list(range(len(places)))
    # Ids already published in each cluster. Two published pins are never
    # merged: one would vanish from the build but linger in the database,
    # taking anyone's notes on it with it.
    published = [{p["id"]} & existing for p in places]

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj and not (published[ri] and published[rj]):
            parent[ri] = rj
            published[rj] |= published[ri]
    by_q = {p["qid"]: i for i, p in enumerate(places) if p.get("qid")}
    by_id = {p["id"]: i for i, p in enumerate(places)}
    for src, dst in org["merge_into"].items():
        if src in by_id and dst in by_id:
            union(by_id[src], by_id[dst])
    for i, a in enumerate(places):
        # Wikidata "part of": St Kilda's islands and stacks belong on St Kilda's pin.
        for q in a.get("part_of", []):
            j = by_q.get(q)
            if j is not None and haversine((a["lat"], a["lon"]), (places[j]["lat"], places[j]["lon"])) < 80:
                union(i, j)
        for j in range(i + 1, len(places)):
            b = places[j]
            d = haversine((a["lat"], a["lon"]), (b["lat"], b["lon"]))
            if d < 0.35 or (d < 1.5 and names_match(a["name"], b["name"])):
                union(i, j)
    clusters = {}
    for i, p in enumerate(places):
        clusters.setdefault(find(i), []).append(p)

    def importance(p):
        return (3 * bool(p.get("page")) + 2 * bool(p.get("walks")) + (p["src"] == "wikidata")
                + bool(p.get("wiki")) + (p["cat"] != "other"), -len(p["name"]))

    def rootness(p, members):
        # How many other members this one is the whole of: they extend its name
        # ("Tyntesfield" is the root of "Tyntesfield chapel") or are recorded as
        # part of it (Hirta is part of St Kilda). The whole names the marker.
        tk = tokens(p["name"])
        return sum(1 for m in members if m is not p and ((tk and tk < tokens(m["name"]))
                                                           or (p.get("qid") and p["qid"] in m.get("part_of", []))))
    merged_places = []
    for members in clusters.values():
        snapshot = list(members)          # list.sort empties the list while it runs
        members.sort(key=lambda p: (rootness(p, snapshot), importance(p)), reverse=True)
        top = members[0]
        # Keep an id the database already knows, so nobody's notes are orphaned
        # when a new source item reshuffles which member leads the cluster.
        known = [m["id"] for m in members if m["id"] in existing]
        if known and top["id"] not in existing:
            top["id"] = known[0]
        top["also"] = []
        for m in members[1:]:
            top["page"] = top.get("page") or m.get("page")
            top["website"] = top.get("website") or m.get("website")
            top.setdefault("walks", []).extend(m.get("walks", []))
            if not top.get("wiki") and m.get("wiki"):
                top["wiki"] = m["wiki"]
            elif m.get("wiki") and m["wiki"] != top.get("wiki"):
                top["also"].append((m["name"], m["wiki"]))
        merged_places.append(top)
    print(f"  merged {len(places)} candidates into {len(merged_places)} places")
    places = merged_places

    # ---- The Trust's page from a website link where it points at the Trust
    for p in places:
        if not p.get("page") and p.get("website"):
            for u in p["website"].split(";"):
                if org["domain"] in u:
                    hit = org_page(org, u)
                    p["page"] = hit[0] if hit else u.strip()
                    break
    for p in places:
        p["org"] = org
    return places


def summaries_for(titles):
    spath = os.path.join(CACHE, "wikipedia.json")
    old = {}
    if os.path.exists(spath) and not REFRESH:
        with open(spath, encoding="utf-8") as f:
            old = json.load(f)
    out = dict(old)
    todo = [tt for tt in titles if tt not in old]
    print(f"\nWikipedia summaries… ({len(todo)} to fetch)")
    for i, tt in enumerate(todo):
        u = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(tt.replace(" ", "_"), safe="")
        try:
            out[tt] = http_json(u, tries=4).get("extract", "")     # 4 tries: Wikipedia rate-limits (429) bursts
        except Exception:  # noqa: BLE001 - a missing page just has no summary
            out[tt] = ""
        if i and i % 100 == 0:
            print(f"   {i}/{len(todo)}")
        time.sleep(0.2)
    with open(spath, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


CAT_WORD = {"house": "Historic house", "castle": "Castle", "garden": "Garden", "nature": "Countryside",
            "coast": "Coastline", "ancient": "Ancient site", "industry": "Industrial heritage",
            "church": "Religious site", "other": "Place"}


def to_row(p, summaries):
    org = p["org"]
    desc = first_sentence(summaries.get(p.get("wiki") or "", ""))
    if not desc and p.get("osm_desc") and not p["osm_desc"].startswith("http"):
        desc = first_sentence(p["osm_desc"])
    if not desc and p.get("wd_desc"):
        desc = p["wd_desc"][:1].upper() + p["wd_desc"][1:] + "."
    if not desc:
        desc = f"{CAT_WORD[p['cat']]} cared for by the {org['name']}" + (f" in the {p['region']} area." if p.get("region") else ".")
    links = []
    if p.get("page"):
        links.append({"label": org["short"], "url": p["page"]})
    else:
        links.append({"label": org["short"] + " (search)", "url": org["search"] + urllib.parse.quote(p["name"])})
    if p.get("website") and org["domain"] not in p["website"]:
        links.append({"label": "Website", "url": p["website"].split(";")[0].strip()})
    if p.get("wiki"):
        links.append({"label": "Wikipedia", "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(p["wiki"].replace(" ", "_"))})
    for aname, awiki in p.get("also", [])[:4]:
        links.append({"label": aname, "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(awiki.replace(" ", "_"))})
    if p.get("qid"):
        links.append({"label": "Wikidata", "url": "https://www.wikidata.org/wiki/" + p["qid"]})
    walks = sorted({w["url"]: w for w in p.get("walks", [])}.values(), key=lambda w: w["name"])
    return {"id": p["id"], "org": org["key"], "name": p["name"], "lat": round(p["lat"], 5), "lon": round(p["lon"], 5),
            "cat": p["cat"], "descr": desc, "links": links, "walks": walks}


def main():
    os.makedirs(CACHE, exist_ok=True)
    existing = load_existing_ids()
    if existing:
        print(f"{len(existing)} ids already in the database will be kept")
    places, seen = [], set()
    for org in ORGS:
        for p in build_org(org, existing):
            if p["id"] not in seen:          # one place, one pin, even if both Trusts claim it
                seen.add(p["id"])
                places.append(p)
    summaries = summaries_for(sorted({p["wiki"] for p in places if p.get("wiki")}))
    rows = sorted((to_row(p, summaries) for p in places), key=lambda r: r["name"])

    with open(os.path.join(BUILD, "places.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    def sq(s):
        return "'" + str(s).replace("'", "''") + "'"
    lines = ["-- Generated by tools/build_places.py - safe to re-run: it upserts, never deletes,",
             "-- so nobody's visits, ratings or notes are touched.",
             "-- Data: Wikidata (CC0), OpenStreetMap contributors (ODbL), Wikipedia (CC BY-SA).",
             "insert into public.places (id, org, name, lat, lon, cat, descr, links, walks) values"]
    vals = [f"({sq(r['id'])},{sq(r['org'])},{sq(r['name'])},{r['lat']},{r['lon']},{sq(r['cat'])},{sq(r['descr'])},"
            f"{sq(json.dumps(r['links'], ensure_ascii=False))}::jsonb,{sq(json.dumps(r['walks'], ensure_ascii=False))}::jsonb)"
            for r in rows]
    lines.append(",\n".join(vals))
    lines.append("on conflict (id) do update set org=excluded.org, name=excluded.name, lat=excluded.lat, lon=excluded.lon,"
                 " cat=excluded.cat, descr=excluded.descr, links=excluded.links, walks=excluded.walks;")
    with open(os.path.join(BUILD, "seed_places.sql"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n{len(rows)} places written.", dict(Counter(r["org"] for r in rows)), dict(Counter(r["cat"] for r in rows)))
    print("with the Trust's own page:", sum(not l["label"].endswith("(search)") for r in rows for l in r["links"][:1]),
          " with walks:", sum(bool(r["walks"]) for r in rows))


if __name__ == "__main__":
    main()
