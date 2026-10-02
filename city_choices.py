"""Small, curated city profiles and randomized multiple-choice questions."""

import random

from city_provider import CITIES


# Country/region, continent, and cities. Keep flags separate from photo fetching.
COUNTRY_GROUPS = [
    ("nl", "Netherlands", "Europe", "Amsterdam"),
    ("gr", "Greece", "Europe", "Athens"),
    ("nz", "New Zealand", "Oceania", "Auckland"),
    ("th", "Thailand", "Asia", "Bangkok"),
    ("es", "Spain", "Europe", "Barcelona|Madrid|Seville"),
    ("cn", "China", "Asia", "Beijing|Shanghai|Xi'an|Chengdu"),
    ("de", "Germany", "Europe", "Berlin|Munich"),
    ("us", "United States", "North America", "Boston|Chicago|Los Angeles|New York|San Francisco|Washington, D.C.|Miami|Las Vegas|New Orleans|Seattle"),
    ("be", "Belgium", "Europe", "Brussels"),
    ("hu", "Hungary", "Europe", "Budapest"),
    ("ar", "Argentina", "South America", "Buenos Aires"),
    ("eg", "Egypt", "Africa", "Cairo|Alexandria"),
    ("za", "South Africa", "Africa", "Cape Town|Johannesburg"),
    ("dk", "Denmark", "Europe", "Copenhagen"),
    ("ae", "United Arab Emirates", "Asia", "Dubai|Abu Dhabi"),
    ("ie", "Ireland", "Europe", "Dublin"),
    ("gb", "United Kingdom", "Europe", "Edinburgh|London"),
    ("it", "Italy", "Europe", "Florence|Milan|Rome|Venice"),
    ("hk", "Hong Kong", "Asia", "Hong Kong"),
    ("tr", "Türkiye", "Europe/Asia", "Istanbul"),
    ("jp", "Japan", "Asia", "Kyoto|Osaka|Tokyo"),
    ("pt", "Portugal", "Europe", "Lisbon|Porto"),
    ("au", "Australia", "Oceania", "Melbourne|Sydney|Brisbane"),
    ("mx", "Mexico", "North America", "Mexico City"),
    ("ca", "Canada", "North America", "Montreal|Toronto|Vancouver|Quebec City"),
    ("ru", "Russia", "Europe/Asia", "Moscow"),
    ("in", "India", "Asia", "Mumbai|Delhi|Agra|Jaipur|Varanasi|Kolkata|Bengaluru"),
    ("fr", "France", "Europe", "Paris"),
    ("cz", "Czechia", "Europe", "Prague"),
    ("br", "Brazil", "South America", "Rio de Janeiro|Sao Paulo"),
    ("kr", "South Korea", "Asia", "Seoul|Busan"),
    ("sg", "Singapore", "Asia", "Singapore"),
    ("se", "Sweden", "Europe", "Stockholm"),
    ("tw", "Taiwan", "Asia", "Taipei"),
    ("at", "Austria", "Europe", "Vienna"),
    ("my", "Malaysia", "Asia", "Kuala Lumpur"),
    ("id", "Indonesia", "Asia", "Jakarta"),
    ("vn", "Vietnam", "Asia", "Hanoi|Ho Chi Minh City"),
    ("ph", "Philippines", "Asia", "Manila"),
    ("mo", "Macau", "Asia", "Macau"),
    ("il", "Israel", "Asia", "Jerusalem"),
    ("qa", "Qatar", "Asia", "Doha"),
    ("ma", "Morocco", "Africa", "Marrakech|Casablanca"),
    ("ke", "Kenya", "Africa", "Nairobi"),
    ("ng", "Nigeria", "Africa", "Lagos"),
    ("tz", "Tanzania", "Africa", "Zanzibar City"),
    ("pl", "Poland", "Europe", "Warsaw|Krakow"),
    ("hr", "Croatia", "Europe", "Dubrovnik"),
    ("fi", "Finland", "Europe", "Helsinki"),
    ("no", "Norway", "Europe", "Oslo"),
    ("is", "Iceland", "Europe", "Reykjavik"),
    ("ch", "Switzerland", "Europe", "Zurich"),
    ("cu", "Cuba", "North America", "Havana"),
    ("pa", "Panama", "North America", "Panama City"),
    ("pe", "Peru", "South America", "Lima|Cusco"),
    ("co", "Colombia", "South America", "Bogota|Cartagena"),
    ("cl", "Chile", "South America", "Santiago"),
]

STYLE_GROUPS = {
    "historic": "Amsterdam|Athens|Barcelona|Beijing|Berlin|Boston|Brussels|Budapest|Buenos Aires|Cairo|Copenhagen|Dublin|Edinburgh|Florence|Istanbul|Kyoto|Lisbon|London|Madrid|Mexico City|Milan|Montreal|Moscow|Paris|Prague|Rome|Stockholm|Venice|Vienna|Delhi|Agra|Jaipur|Varanasi|Kolkata|Hanoi|Macau|Xi'an|Jerusalem|Marrakech|Alexandria|Zanzibar City|Munich|Warsaw|Krakow|Dubrovnik|Zurich|Porto|Seville|Washington, D.C.|New Orleans|Havana|Quebec City|Lima|Bogota|Cusco|Cartagena",
    "skyline": "Auckland|Bangkok|Chicago|Dubai|Hong Kong|Los Angeles|Melbourne|Mexico City|Milan|Moscow|Mumbai|New York|Osaka|Seoul|Shanghai|Singapore|Sydney|Taipei|Tokyo|Toronto|Vancouver|Delhi|Bengaluru|Kuala Lumpur|Jakarta|Ho Chi Minh City|Manila|Chengdu|Busan|Abu Dhabi|Doha|Casablanca|Nairobi|Johannesburg|Lagos|Warsaw|Miami|Las Vegas|Seattle|Panama City|Sao Paulo|Santiago|Brisbane",
    "waterfront": "Amsterdam|Auckland|Bangkok|Barcelona|Boston|Buenos Aires|Cape Town|Chicago|Copenhagen|Dubai|Hong Kong|Istanbul|Lisbon|London|Melbourne|Montreal|Mumbai|New York|Rio de Janeiro|San Francisco|Shanghai|Singapore|Stockholm|Sydney|Toronto|Vancouver|Venice|Varanasi|Kolkata|Jakarta|Ho Chi Minh City|Manila|Macau|Busan|Abu Dhabi|Doha|Casablanca|Alexandria|Lagos|Zanzibar City|Dubrovnik|Helsinki|Oslo|Reykjavik|Zurich|Porto|Miami|New Orleans|Seattle|Havana|Panama City|Quebec City|Cartagena|Brisbane",
    "canals": "Amsterdam|Venice|Bangkok|Copenhagen|Stockholm",
}

CITY_PROFILES = {
    name: {"flag": code, "country": country, "continents": set(continent.split("/")), "styles": set()}
    for code, country, continent, names in COUNTRY_GROUPS
    for name in names.split("|")
}
for style, names in STYLE_GROUPS.items():
    for name in names.split("|"):
        CITY_PROFILES[name]["styles"].add(style)


def generate_choices(answer, catalog=None):
    """Prefer related cities, while allowing many different combinations."""
    names = list(dict.fromkeys(catalog if catalog is not None else [city[0] for city in CITIES]))
    target = CITY_PROFILES[answer]
    candidates = [name for name in names if name != answer]
    related = [
        name for name in candidates
        if target["continents"] & CITY_PROFILES[name]["continents"]
        or target["styles"] & CITY_PROFILES[name]["styles"]
    ]
    closest = [
        name for name in related
        if target["continents"] & CITY_PROFILES[name]["continents"]
        and target["styles"] & CITY_PROFILES[name]["styles"]
    ]
    choices = [answer]
    for index in range(min(3, len(candidates))):
        # Guarantee one geographically and stylistically close distractor
        # whenever possible; keep the other two free to vary.
        pool = closest if index == 0 and closest else related or candidates
        weights = []
        for name in pool:
            profile = CITY_PROFILES[name]
            same_continent = bool(target["continents"] & profile["continents"])
            shared_styles = len(target["styles"] & profile["styles"])
            weights.append(1 + 4 * same_continent + 3 * shared_styles)
        selected = random.choices(pool, weights=weights, k=1)[0]
        choices.append(selected)
        candidates.remove(selected)
        if selected in related:
            related.remove(selected)
    random.shuffle(choices)
    return choices
