# Sanity check after build_places.py: lists well-known places that are missing
# from build/places.json, or that are only marked as owned by a Trust (not a
# place to visit). Nothing printed under either heading means all is well.
import json, os
from collections import Counter

R = json.load(open(os.path.join(os.path.dirname(__file__), "..", "build", "places.json"), encoding="utf-8"))
MUST = {
    "nt": "Stourhead|Cliveden|Chartwell|Giant's Causeway|Mount Stewart|Attingham|Kedleston|Hardwick|Stonehenge|"
          "Sissinghurst|Lyme|Castle Drogo|Penrhyn|Bodnant|Fountains Abbey|Wallington|Cragside|Belton|Calke|Mottisfont|"
          "Wakehurst|Nymans|Anglesey Abbey|Ickworth|Blickling|Tyntesfield|Dyrham|Killerton|Lanhydrock|St Michael's Mount|"
          "Longshaw|Mam Tor|Kinder|Nostell|Beningbrough|Seaton Delaval|Speke|Quarry Bank|Erddig|Powis|Chirk|Dinefwr|"
          "Croome|Hidcote|Snowshill|Waddesdon|Ham House|Osterley|Sutton Hoo|Dunstable|Brimham|Gibside|Lindisfarne|Souter|"
          "Castle Ward|Florence Court|Carrick-a-Rede|Rowallane|Divis|Stowe|Hughenden|Petworth|Uppark|Scotney|Bateman|"
          "Knole|Polesden|Box Hill|Hatfield Forest|Wicken|Blakeney|Felbrigg|Oxburgh|Kingston Lacy|Corfe|Brownsea|Studland|"
          "Golden Cap|Glastonbury Tor|Avebury|Lacock|Tintagel|Bodiam|Dunster|Arlington|Coleton|Greenway|Trelissick|"
          "Glendurgan|Clumber|Hardcastle|Malham|Tarn Hows|Hill Top|Sizergh|Wordsworth|Plas Newydd|Llanerchaeron|Tredegar|"
          "Sudbury|Shugborough|Moseley|Packwood|Baddesley|Upton House|Coughton|Charlecote|Berrington|Brockhampton|"
          "Croft Castle|Dudmaston|Wightwick|Biddulph|Little Moreton|Tatton|Dunham Massey|Gawthorpe|Rufford|Formby|"
          "Mr Straw|Red House|Southwell|Mompesson|Standen|Sheffield Park|Claremont|Clandon|Runnymede|Leith Hill|Ightham|"
          "Emmetts|Alfriston|Birling Gap|White Cliffs",
    "nts": "Culzean|Brodick|Glen Coe|Bannockburn|Culloden|Inverewe|Crathes|Falkland|Hill House|Burns|Threave|"
           "Georgian House|Gladstone|St Kilda|Iona|Ben Lomond|Grey Mare|Brodie|Drum|Craigievar|Fyvie|Haddo|"
           "Castle Fraser|Leith Hall|Kellie|House of Dun|Hill of Tarvit|Mar Lodge|Torridon|Kintail|Canna|Fair Isle|"
           "Glenfinnan|Killiecrankie|Binns|Newhailes|Greenbank|Holmwood|Tenement|Weaver|Branklyn|Arduaine|Crarae|"
           "Geilston|Broughton|Priorwood|Harmony|Malleny|Inveresk|Hermitage|Corrieshalloch|Culross|Alloa|Pitmedden|"
           "Balmacara|Unst|Staffa|Goat Fell",
}

for org, must in MUST.items():
    mine = [r for r in R if r["org"] == org]
    for m in must.split("|"):
        hits = [r for r in mine if m.lower() in r["name"].lower()]
        if not hits:
            print(f"MISSING ({org})", m)
        elif not any(r["listed"] for r in hits):
            print(f"ONLY OWNED ({org})", m, "-", ", ".join(r["name"] for r in hits))

print("---", dict(Counter((r["org"], "visit" if r["listed"] else "owned") for r in R)),
      "| with Wikipedia:", sum(any(l["label"] == "Wikipedia" for l in r["links"]) for r in R), "of", len(R))
