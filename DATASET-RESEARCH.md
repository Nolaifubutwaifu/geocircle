# New datasets for Geocircle: research (20 Nov 2026)

Goal: more puzzles for M5 (150 approved, 60 days ahead) without repeating the mistakes so far. What Max rejected tells us what a good dataset looks like:

- **Global, or at least a whole continent.** The APAD regional industrial data (brick kilns, cement, paper mills) all got cut.
- **Different from "where people live".** The generator's surprise gate wants ≥ 0.25. Population-shaped stats feel samey.
- **A surprising answer people can still reason about.** Lakes in Canada, dinosaurs in Alberta and Mongolia, languages in Papua New Guinea.
- **Licence allows commercial use** (Premium). CC0, CC BY, public domain, CDLA Permissive. No NC, no ND, and no share-alike unless we accept releasing our cell files under the same licence.

The pipeline already takes three kinds of input, so most new datasets need little code:

| Path | Existing example | New dataset needs |
|---|---|---|
| A. Per-country value × population | FAO foods (`FOOD` dict) | one line in `FOOD`, or a per-country CSV |
| B. Points (count or weighted) | earthquakes, McDonald's (`points_to_cells`) | a download + ~10 lines in `step_points` |
| C. Count raster | GLW livestock (`raster_to_cells`) | a GeoTIFF + ~10 lines |

"Checked" below means I read the licence on the source itself on 20 Nov 2026. "Sampled" means I downloaded the data and looked at where it is.

---

## Tier 1: do these first (licence checked, data sampled, existing code path)

| # | Puzzle | Source | Licence | Path | What the data shows (sampled) |
|---|---|---|---|---|---|
| 1 | **12 more FAO foods** | FAOSTAT Food Balances, already in `data-raw/fao/fbs.zip` | ✅ CC BY 4.0 (already used) | A | See the table below. Zero download, one line each. |
| 2 | **Dinosaur fossils found** | Paleobiology Database API | ✅ CC0, stated in the API response itself ("Data License: Creative Commons CC0") | B | 21,941 non-bird dinosaur finds. USA 30%, Canada 9%, China 9%, Spain 7%, Mongolia 4%, Argentina 4%. |
| 3 | **Languages spoken** | Glottolog 5.4 `languages_and_dialects_geo.csv` (1 MB) | ✅ CC BY 4.0 (glottolog.org downloads page) | B | 8,300 languages with a point. Africa 2,339, New Guinea and the Pacific 2,204, all of Eurasia 1,969. India and China have billions of people but far fewer languages than New Guinea: a huge surprise score. |
| 4 | **Lakes** | HydroLAKES points (79 MB, 1.43 M lakes over 10 ha) | ✅ CC BY 4.0 (hydrosheds.org) | B | Canada 61.6%, Russia 14.1%, USA 7.3%, then China, Sweden, Brazil under 2% each. |
| 5 | **Animal sightings**: kangaroos, penguins, flamingos, koalas, and more | GBIF occurrences | ✅ per record: keep CC0 and CC BY, drop CC BY-NC | B | Open-licence records with coordinates: kangaroo family 1.97 M, penguins 0.99 M, flamingos 0.68 M, koalas 0.42 M. Say "reported sightings". |

**FAO foods worth adding** (2022 share of world food supply, top 3 countries). These have a pattern of their own. Wheat, sugar, milk, onions and honey were left out because they mostly follow population.

| Food | Top 3 | Why it's fun |
|---|---|---|
| Yams | Nigeria 61%, Côte d'Ivoire 17%, Ghana 15% | Almost all in one corner of West Africa |
| Cassava | DR Congo 23%, Nigeria 20%, Indonesia 10% | Africa, not Asia |
| Plantains | Uganda 29%, Ghana 11%, DR Congo 11% | Uganda is a shock |
| Dates | Egypt 22%, Saudi Arabia 19%, Algeria 13% | North Africa and the Gulf |
| Rye | Poland 22%, Russia 15%, Germany 14% | A bread-belt puzzle in Europe |
| Pork | China 48%, USA 9%, Russia 4% | Gaps across the Muslim world |
| Millet | India 52%, Niger 12%, Nigeria 9% | India plus the Sahel |
| Sorghum | China 26%, Nigeria 22%, India 14% | |
| Mutton and goat | China 33%, India 14%, Pakistan 5% | Pairs with the sheep and goats rasters |
| Sweet potatoes | China 53%, Nigeria 6%, Indonesia 4% | |
| Olive oil | Italy 16%, Spain 13%, USA 13% | Mediterranean, with a US twist |
| Apples | China 38%, USA 23%, Türkiye 3% | |

Watch out: the PRD already notes food stats feel alike. The 7-day dataset spacing handles that, but don't approve more than one food a week.

**Notes per Tier 1 source**
- **Dinosaurs:** `paleobiodb.org/data1.2/occs/list.csv?base_name=Dinosauria&show=coords&limit=all`. Drop birds: keep `max_ma > 66` and `accepted_name != "Aves"`. The same call with another `base_name` gives more puzzles from one source: trilobites, mammoths (*Mammuthus*), ammonites. Weight = 1 per find. Credit: "The Paleobiology Database (CC0)".
- **Languages:** keep `level == "language"`, drop dialects. One point per language, so a 100 km circle in New Guinea can hold hundreds. Credit Glottolog (Hammarström, Forkel, Haspelmath, Bank).
- **Lakes:** the points file has `Country` and `Lake_area` columns. Two puzzles: lake count, and lake area (weight = `Lake_area`; then the Great Lakes, Baikal and the Caspian dominate). Credit: Messager et al. 2016.
- **GBIF:** too many records for the free search API (capped at 100,000). Use the monthly Parquet snapshot on AWS with DuckDB, as `overture_brand.py` already does for Overture. Since April 2022 the snapshot includes CC BY-NC records, so filter on the `license` column. Every GBIF download should be cited with its DOI.
  - Kangaroos sit almost entirely in Australia, so use them for "pick the countries" or Australia-scope puzzles. Penguins (the southern hemisphere, plus zoos) and flamingos (Africa, the Americas, southern Europe) work at world scope.

## Tier 2: good, but a bigger download or one open question

| # | Puzzle | Source | Licence | Path | Open question / work |
|---|---|---|---|---|---|
| 6 | **Coffee / cocoa / rice / wheat grown** | SPAM 2020 (Harvard Dataverse doi:10.7910/DVN/SWPENT) | ✅ CC BY 4.0 (GAME-SPEC §4) | C | mapspam.info blocks scripts, so download from Dataverse. Gives the pair "coffee grown" vs "coffee drunk". |
| 7 | **Lightning flashes** | NASA LIS/OTD 0.5° climatology (HRFC v2.3.2015) | ✅ US government work (data.gov lists usa.gov/government-works) | C | Download goes through NASA Earthdata, which may need a free login. Max would have to create it himself. Peak: Lake Maracaibo and the eastern Congo. |
| 8 | **Shipping traffic** | World Bank Global Shipping Traffic Density (510 MB raster) | ✅ CC BY 4.0 (World Bank catalogue) | C | Ocean only. Check the game handles ocean-only weight like offshore earthquakes. Also comes in fishing, passenger and leisure layers. |
| 9 | **People aged 80+ / babies under 5** | UN World Population Prospects 2024 (per-country age shares) × Kontur | ✅ CC BY 3.0 IGO | A | Same code as the FAO foods. Japan and Europe vs Africa. The WorldPop 1 km age rasters (CC BY 4.0) are the precise but heavier option. |
| 10 | **Rain that falls on land** | CHELSA bioclim `bio12` (annual precipitation) | ✅ CC0 (chelsa-climate.org) | C | Weight = mm × cell area. 1 km rasters: aggregate once. Amazon, Congo, Southeast Asia. |
| 11 | **Places named ...** ("-burg", "San ...", "-stan", "-by") and natural features (caves, oases, hot springs) | GeoNames `allCountries.zip` (~400 MB) | ✅ CC BY 4.0, commercial use allowed (geonames.org/export) | B | Toponym puzzles are new territory and map nerds love them. Needs a filter per puzzle. |
| 12 | **Castles, lighthouses, windmills, pyramids, Nobel laureates' birthplaces** | Wikidata | ✅ CC0 | B | The public query service times out after 60 s on big queries: page the results or use the QLever mirror. Coverage leans to Europe. Fun fact: Sudan has more pyramids than Egypt. |
| 13 | **More brands**: 7-Eleven (Japan, Thailand), Starbucks, KFC, IKEA, Tim Hortons | Overture Places | ✅ CDLA Permissive 2.0 (already used) | B | `overture_brand.py` already does this. Overture removed the old `categories` field in the 23 Sep 2026 release. Category puzzles (golf courses, karaoke) must use `taxonomy` or `basic_category` now. Brand matching is unaffected. |
| 14 | **Night lights** | VIIRS annual (Colorado School of Mines EOG) | ⚠️ EOG says "many" products are CC BY 4.0 | C | Read EOG's licence PDF for the annual product before use. Lights partly follow population, so the surprise score may be low. |
| 15 | **Glacier area** (outside the ice sheets) | Randolph Glacier Inventory 7 | ✅ CC BY 4.0 per the RGI user guide | B (centroid × area) | NSIDC download needs an Earthdata login. Alaska, the Canadian Arctic, High Mountain Asia. |
| 16 | **Tornadoes** (USA only) | NOAA SPC 1950 to now | ✅ US government, public domain | B | One country only, like the USA earthquakes puzzle. spc.noaa.gov blocked my fetch, so the file name is unchecked. |
| 17 | **Hurricane track hours** | NOAA IBTrACS v4 | ✅ NOAA, "full and open access" | B | Mostly ocean. Sample every 3 h as points. |
| 18 | **Mangroves / coral reefs** | Global Mangrove Watch v3 / Allen Coral Atlas | ✅ CC BY 4.0 (both) | C | Heavy (10 m and 5 m data): aggregate once. Indonesia, Australia. |
| 19 | **Nuclear / solar / wind capacity** | Global Energy Monitor power tracker | ✅ CC BY 4.0 per GEM's site, "unless otherwise noted" | B | Download may sit behind a form. Lower priority: Max cut the industrial puzzles. |

## Avoid (licence problems found)

| Dataset | Problem |
|---|---|
| EDGAR CO2 emissions grids | The fossil CO2 part comes from the IEA under CC BY-NC-ND 4.0 |
| WorldClim | Commercial use needs permission. Use CHELSA (CC0) instead. |
| Crowther global tree density | CC BY-ND (no derivatives) on the Yale mirror |
| Meteorite Landings (NASA copy) | Still no licence: NASA lists "no license information", and the Meteoritical Bulletin site blocked my check. Stays ⚠️. 69% of the 32k located finds are in Antarctica, which already scores badly in the game. |
| USGS Global Islands | Sources disagree: public domain vs CC BY-SA 4.0. sciencebase.gov blocked my check. |
| OpenStreetMap extracts (OpenSkiMap etc.) | ODbL share-alike: our cell files would have to be ODbL too |
| eBird, Global Fishing Watch, Ookla | Non-commercial (GFW and Ookla were already listed in GAME-SPEC §4c) |

## Suggested order

1. **FAO foods** (1 hour): add the 12 items to `FOOD` and `FOOD_STAT`, run `stats candidates`. Write a fun fact for each.
2. **Dinosaurs, languages, lakes** (half a day together): three point datasets, all sampled already. Each one is a download, a `points_to_cells` call and a `DATASETS` line. Lakes gives two datasets (count and area).
3. **GBIF animals** (half a day): a DuckDB extract script like `overture_brand.py`, with a licence filter.
4. **SPAM crops, UN age data, CHELSA rain** (a day): they need a manual download or a new per-country CSV.
5. Everything else when the generator needs variety.

Steps 1 to 3 add 17+ new datasets (12 foods, dinosaurs, languages, lake count, lake area, 1+ animals). At the generator's current pass rate that is several hundred new candidates, more than the 150 that M5 needs.

## Sources
- [FAOSTAT Food Balances](https://data.fao.org/catalog/dataset/2f264bb6-1238-459a-bf8b-0e2d0a16804a)
- [Paleobiology Database API](https://paleobiodb.org/data1.2/) (licence in the `datainfo` header) and [CC blog on the PBDB licence](https://creativecommons.org/?p=41216)
- [Glottolog downloads](https://glottolog.org/meta/downloads)
- [HydroLAKES](https://www.hydrosheds.org/products/hydrolakes)
- [GBIF on AWS](https://registry.opendata.aws/gbif/) and [GBIF cloud services docs](https://techdocs.gbif.org/en/cloud-services/)
- [SPAM 2020 on FAO catalogue](https://data.fao.org/catalog/iso/3757ea29-7c21-419e-a9d0-46e0b268049e)
- [LIS/OTD climatology on data.gov](https://catalog.data.gov/dataset/lis-otd-0-5-degree-high-resolution-full-climatology-hrfc-v2-3-2015)
- [World Bank Global Shipping Traffic Density](https://datacatalog.worldbank.org/search/dataset/0037580)
- [UN WPP 2024 data (CC BY 3.0 IGO)](https://github.com/PPgp/wpp2024) and [WorldPop age structures](https://hub.worldpop.org/geodata/summary?id=24798)
- [CHELSA climatologies](https://www.chelsa-climate.org/datasets/chelsa_climatologies)
- [GeoNames export](https://geonames.org/export/)
- [Wikidata Query Service manual](https://www.mediawiki.org/wiki/Wikidata_Query_Service/Manual)
- [Overture Places guide](https://docs.overturemaps.org/guides/places) and [taxonomy](https://docs.overturemaps.org/guides/places/taxonomy/)
- [EOG VIIRS night lights](https://eogdata.mines.edu/products/vnl/)
- [RGI 7 user guide](https://www.glims.org/rgi_user_guide/welcome.html)
- [NOAA SPC GIS](https://www.spc.noaa.gov/gis/svrgis/)
- [IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive)
- [Global Mangrove Watch v3](https://zenodo.org/record/6894273) and [Allen Coral Atlas on Earth Engine](https://developers.google.com/earth-engine/datasets/catalog/ACA_reef_habitat_v2_0)
- [Global Energy Monitor GIPT](https://globalenergymonitor.org/projects/global-integrated-power-tracker/download-data/)
- [EDGAR GHG 2024 (IEA CO2 licence)](https://edgar.jrc.ec.europa.eu/dataset_ghg2024)
- [WorldClim 2.1](https://worldclim.org/data/worldclim21.html)
- [Crowther tree density](https://figshare.com/articles/Global_map_of_tree_density/3179986)
- [Meteorite Landings](https://data.nasa.gov/dataset/meteorite-landings)
- [USGS Global Islands](https://doi.org/10.5066/P91ZCSGM)
