"""Approximate coordinates for wine regions, used to place pins on the map.
Coordinates are region centres, good to a few kilometres; anything not listed falls back to the country."""

REGIONS = {
    # United States
    "napa valley": (38.50, -122.36, "USA"), "napa": (38.50, -122.36, "USA"),
    "oakville": (38.44, -122.41, "USA"), "rutherford": (38.46, -122.42, "USA"),
    "stags leap district": (38.41, -122.33, "USA"), "howell mountain": (38.56, -122.44, "USA"),
    "spring mountain district": (38.53, -122.53, "USA"), "mount veeder": (38.39, -122.40, "USA"),
    "yountville": (38.40, -122.36, "USA"), "calistoga": (38.58, -122.58, "USA"),
    "st. helena": (38.51, -122.47, "USA"), "carneros": (38.25, -122.35, "USA"), "los carneros": (38.25, -122.35, "USA"),
    "sonoma": (38.29, -122.46, "USA"), "sonoma county": (38.52, -122.85, "USA"), "sonoma coast": (38.45, -123.05, "USA"),
    "russian river valley": (38.48, -122.87, "USA"), "alexander valley": (38.70, -122.85, "USA"),
    "dry creek valley": (38.66, -122.95, "USA"), "sonoma valley": (38.35, -122.50, "USA"),
    "moon mountain district": (38.35, -122.45, "USA"), "knights valley": (38.62, -122.68, "USA"),
    "anderson valley": (39.08, -123.47, "USA"), "mendocino": (39.15, -123.20, "USA"), "lake county": (38.95, -122.75, "USA"),
    "north coast": (38.70, -122.80, "USA"), "paso robles": (35.63, -120.69, "USA"),
    "san luis obispo county": (35.28, -120.66, "USA"), "edna valley": (35.21, -120.60, "USA"),
    "santa barbara": (34.65, -120.10, "USA"), "santa rita hills": (34.63, -120.35, "USA"), "sta. rita hills": (34.63, -120.35, "USA"),
    "santa ynez valley": (34.61, -120.10, "USA"), "santa maria valley": (34.92, -120.40, "USA"),
    "santa cruz mountains": (37.20, -122.10, "USA"), "monterey": (36.40, -121.50, "USA"),
    "santa lucia highlands": (36.40, -121.38, "USA"), "livermore valley": (37.66, -121.75, "USA"),
    "lodi": (38.13, -121.27, "USA"), "sierra foothills": (38.50, -120.75, "USA"), "central coast": (35.80, -120.90, "USA"),
    "california": (37.50, -121.50, "USA"),
    "willamette valley": (45.10, -123.05, "USA"), "dundee hills": (45.27, -123.03, "USA"),
    "eola-amity hills": (45.03, -123.15, "USA"), "oregon": (44.80, -122.90, "USA"),
    "columbia valley": (46.30, -119.50, "USA"), "walla walla valley": (46.06, -118.35, "USA"),
    "red mountain": (46.29, -119.44, "USA"), "yakima valley": (46.40, -120.20, "USA"), "horse heaven hills": (46.00, -119.60, "USA"),
    "washington": (46.40, -119.80, "USA"), "finger lakes": (42.60, -76.90, "USA"),
    # France
    "bordeaux": (44.84, -0.58, "France"), "medoc": (45.20, -0.85, "France"), "médoc": (45.20, -0.85, "France"),
    "pauillac": (45.20, -0.75, "France"), "saint-julien": (45.17, -0.74, "France"), "margaux": (45.04, -0.67, "France"),
    "saint-estephe": (45.26, -0.78, "France"), "pessac-leognan": (44.76, -0.65, "France"),
    "saint-emilion": (44.89, -0.15, "France"), "pomerol": (44.93, -0.20, "France"), "sauternes": (44.53, -0.34, "France"),
    "burgundy": (47.05, 4.85, "France"), "bourgogne": (47.05, 4.85, "France"), "cote de nuits": (47.20, 4.95, "France"),
    "gevrey-chambertin": (47.23, 4.97, "France"), "vosne-romanee": (47.16, 4.95, "France"),
    "chambolle-musigny": (47.19, 4.95, "France"), "nuits-saint-georges": (47.14, 4.95, "France"),
    "cote de beaune": (47.00, 4.80, "France"), "meursault": (46.98, 4.77, "France"), "pommard": (47.01, 4.80, "France"),
    "puligny-montrachet": (46.95, 4.75, "France"), "chablis": (47.81, 3.80, "France"), "beaujolais": (46.10, 4.65, "France"),
    "champagne": (49.04, 4.00, "France"), "rhone": (44.90, 4.80, "France"), "rhône": (44.90, 4.80, "France"),
    "northern rhone": (45.10, 4.83, "France"), "cote-rotie": (45.48, 4.78, "France"), "hermitage": (45.07, 4.84, "France"),
    "chateauneuf-du-pape": (44.06, 4.83, "France"), "châteauneuf-du-pape": (44.06, 4.83, "France"),
    "southern rhone": (44.10, 4.85, "France"), "gigondas": (44.16, 5.00, "France"),
    "loire": (47.30, 0.70, "France"), "loire valley": (47.30, 0.70, "France"), "sancerre": (47.33, 2.84, "France"),
    "vouvray": (47.41, 0.80, "France"), "provence": (43.50, 6.00, "France"), "languedoc": (43.60, 3.30, "France"),
    "alsace": (48.20, 7.35, "France"), "cahors": (44.45, 1.44, "France"),
    # Italy
    "piedmont": (44.70, 8.00, "Italy"), "piemonte": (44.70, 8.00, "Italy"), "barolo": (44.61, 7.94, "Italy"),
    "barbaresco": (44.72, 8.08, "Italy"), "tuscany": (43.40, 11.25, "Italy"), "toscana": (43.40, 11.25, "Italy"),
    "chianti": (43.55, 11.30, "Italy"), "chianti classico": (43.50, 11.30, "Italy"),
    "montalcino": (43.06, 11.49, "Italy"), "brunello di montalcino": (43.06, 11.49, "Italy"),
    "montepulciano": (43.09, 11.78, "Italy"), "bolgheri": (43.23, 10.61, "Italy"), "maremma": (42.75, 11.10, "Italy"),
    "veneto": (45.50, 11.40, "Italy"), "valpolicella": (45.53, 10.90, "Italy"), "amarone della valpolicella": (45.53, 10.90, "Italy"),
    "prosecco": (45.88, 12.25, "Italy"), "friuli": (46.00, 13.20, "Italy"), "alto adige": (46.50, 11.35, "Italy"),
    "sicily": (37.60, 14.20, "Italy"), "etna": (37.75, 15.00, "Italy"), "campania": (40.90, 14.80, "Italy"),
    "puglia": (40.90, 16.60, "Italy"), "abruzzo": (42.25, 13.90, "Italy"),
    # Spain and Portugal
    "rioja": (42.45, -2.45, "Spain"), "ribera del duero": (41.65, -3.90, "Spain"), "priorat": (41.17, 0.80, "Spain"),
    "toro": (41.52, -5.40, "Spain"), "rias baixas": (42.40, -8.70, "Spain"), "rías baixas": (42.40, -8.70, "Spain"),
    "jerez": (36.68, -6.13, "Spain"), "cava": (41.40, 1.75, "Spain"), "penedes": (41.35, 1.70, "Spain"), "jumilla": (38.47, -1.33, "Spain"),
    "douro": (41.16, -7.79, "Portugal"), "porto": (41.15, -8.61, "Portugal"), "port": (41.16, -7.79, "Portugal"),
    "alentejo": (38.50, -7.90, "Portugal"), "vinho verde": (41.70, -8.30, "Portugal"), "madeira": (32.75, -16.95, "Portugal"),
    # Germany, Austria
    "mosel": (49.90, 6.90, "Germany"), "rheingau": (50.00, 8.00, "Germany"), "pfalz": (49.30, 8.15, "Germany"),
    "wachau": (48.37, 15.42, "Austria"), "burgenland": (47.60, 16.60, "Austria"),
    # Southern hemisphere
    "mendoza": (-32.89, -68.83, "Argentina"), "uco valley": (-33.60, -69.20, "Argentina"),
    "lujan de cuyo": (-33.04, -68.88, "Argentina"), "salta": (-25.50, -66.00, "Argentina"), "cafayate": (-26.07, -65.98, "Argentina"),
    "patagonia": (-38.95, -68.06, "Argentina"),
    "maipo valley": (-33.70, -70.80, "Chile"), "colchagua": (-34.60, -71.30, "Chile"), "casablanca": (-33.30, -71.40, "Chile"),
    "aconcagua": (-32.80, -70.90, "Chile"), "maule": (-35.50, -71.70, "Chile"),
    "barossa": (-34.55, 138.95, "Australia"), "barossa valley": (-34.55, 138.95, "Australia"),
    "mclaren vale": (-35.22, 138.55, "Australia"), "coonawarra": (-37.29, 140.83, "Australia"),
    "clare valley": (-33.83, 138.60, "Australia"), "margaret river": (-33.95, 115.07, "Australia"),
    "yarra valley": (-37.70, 145.45, "Australia"), "hunter valley": (-32.80, 151.25, "Australia"),
    "south australia": (-34.90, 138.60, "Australia"), "south eastern australia": (-34.50, 142.00, "Australia"),
    "marlborough": (-41.52, 173.90, "New Zealand"), "central otago": (-45.03, 169.20, "New Zealand"),
    "hawke's bay": (-39.60, 176.80, "New Zealand"), "martinborough": (-41.22, 175.46, "New Zealand"),
    "stellenbosch": (-33.93, 18.86, "South Africa"), "franschhoek": (-33.91, 19.12, "South Africa"),
    "swartland": (-33.35, 18.75, "South Africa"), "western cape": (-33.70, 19.00, "South Africa"),
    # Other
    "tokaj": (48.12, 21.41, "Hungary"), "santorini": (36.40, 25.43, "Greece"), "okanagan valley": (49.90, -119.50, "Canada"),
    "niagara peninsula": (43.15, -79.40, "Canada"), "valle de guadalupe": (32.08, -116.60, "Mexico"),
}

COUNTRIES = {
    "usa": (39.5, -98.5), "united states": (39.5, -98.5), "france": (46.6, 2.4), "italy": (42.8, 12.6),
    "spain": (40.2, -3.7), "portugal": (39.6, -8.0), "germany": (51.0, 10.3), "austria": (47.6, 14.1),
    "argentina": (-34.6, -64.2), "chile": (-33.5, -71.0), "australia": (-25.7, 134.5), "new zealand": (-41.8, 172.8),
    "south africa": (-30.6, 23.0), "greece": (39.1, 22.0), "hungary": (47.2, 19.4), "canada": (52.0, -97.0),
    "mexico": (23.6, -102.6), "uruguay": (-32.8, -56.0), "israel": (31.4, 35.0), "lebanon": (33.9, 35.9),
    "georgia": (42.3, 43.4), "england": (52.4, -1.5), "united kingdom": (52.4, -1.5), "switzerland": (46.8, 8.2),
    "slovenia": (46.1, 14.8), "croatia": (45.1, 15.2),
}


def locate(region: str, country: str):
    """Returns (lat, lon, precision) for a wine, where precision is 'region', 'country' or None."""
    r = (region or "").strip().lower()
    if r:
        if r in REGIONS:
            lat, lon, _ = REGIONS[r]
            return lat, lon, "region"
        for part in [p.strip() for p in r.replace(" - ", ",").split(",")]:
            if part in REGIONS:
                lat, lon, _ = REGIONS[part]
                return lat, lon, "region"
        for name, (lat, lon, _) in REGIONS.items():
            if len(name) > 4 and (name in r or r in name):
                return lat, lon, "region"
    c = (country or "").strip().lower()
    if c in COUNTRIES:
        lat, lon = COUNTRIES[c]
        return lat, lon, "country"
    if r:
        for name, (lat, lon, ctry) in REGIONS.items():
            if name == r:
                return lat, lon, "region"
    return None, None, None
