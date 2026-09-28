# Sanity check after build_places.py: lists well-known NT places missing from
# build/places.json, then every place that did not come from Wikidata.
import json, math, re
R=json.load(open(__import__('os').path.join(__import__('os').path.dirname(__file__), '..', 'build', 'places.json'),encoding='utf-8'))
must="Stourhead|Cliveden|Chartwell|Giant's Causeway|Mount Stewart|Attingham|Kedleston|Hardwick Hall|Stonehenge|Sissinghurst|Lyme|Castle Drogo|Penrhyn|Bodnant|Fountains Abbey|Wallington|Cragside|Belton|Calke|Mottisfont|Wakehurst|Nymans|Anglesey Abbey|Ickworth|Blickling|Tyntesfield|Dyrham|Killerton|Lanhydrock|St Michael's Mount|Longshaw|Mam Tor|Kinder|Nostell|Beningbrough|Seaton Delaval|Speke|Quarry Bank|Erddig|Powis|Chirk|Dinefwr|Croome|Hidcote|Snowshill|Waddesdon|Ham House|Osterley|Sutton Hoo|Dunstable|Brimham|Gibside|Lindisfarne|Souter|Castle Ward|Florence Court|Carrick-a-Rede|Rowallane|Divis|Stowe|Hughenden|Petworth|Uppark|Scotney|Bateman|Knole|Polesden|Box Hill|Hatfield Forest|Wicken|Blakeney|Felbrigg|Oxburgh|Kingston Lacy|Corfe|Brownsea|Studland|Golden Cap|Glastonbury Tor|Avebury|Lacock|Tintagel|Bodiam|Dunster|Arlington|Coleton|Greenway|Trelissick|Glendurgan|Clumber|Hardcastle|Malham|Tarn Hows|Hill Top|Sizergh|Wordsworth|Plas Newydd|Aberglasney|Llanerchaeron|Tredegar|Sudbury|Shugborough|Moseley|Packwood|Baddesley|Upton House|Coughton|Charlecote|Berrington|Brockhampton|Croft Castle|Dudmaston|Wightwick|Biddulph|Little Moreton|Tatton|Dunham Massey|Gawthorpe|Rufford|Formby|Mr Straw|Red House|Southwell|Mompesson|Standen|Sheffield Park|Claremont|Clandon|Runnymede|Leith Hill|Ightham|Emmetts|Bodiam|Alfriston|Birling Gap|Seven Sisters|White Cliffs"
names=[r['name'] for r in R]
for m in must.split('|'):
    if not any(m.lower() in n.lower() for n in names): print('MISSING', m)
print('--- osm/walk-derived sample')
for r in R:
    if not r['id'].startswith('Q'): print(r['id'][:14].ljust(15), r['cat'].ljust(8), r['name'])
