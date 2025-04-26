import re
import requests
from bs4 import BeautifulSoup
import json

BASE_URL = "https://mechabellum.wiki"
MAIN_PAGE = f"{BASE_URL}/index.php/Mechabellum_Wiki"


# ---------- 1. (small) Tweak: skip the “Unit Overview” link ----------
def get_unit_links() -> list[str]:
    res = requests.get(MAIN_PAGE, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    units_heading = soup.find(lambda t: t.name in ("h2", "h3") and "Units" in t.text)
    if units_heading is None:
        raise RuntimeError("Could not find the Units heading")

    links = set()
    for sib in units_heading.find_next_siblings():
        if sib.name and sib.name.startswith("h"):  # reached next section
            break
        for a in sib.find_all("a", href=True):
            title = a.get_text(strip=True)
            href = a["href"]
            if (
                title  # text exists
                and title.lower() != "unit overview"
                and href.startswith("/index.php/")
                and ":" not in href  # ignore special pages
            ):
                links.add(BASE_URL + href)

    return sorted(links)


# ---------- 2. New: robust parser that works with *or* without an infobox ----------
def parse_unit_page(url: str) -> dict:
    """Return dict with name, giant (bool), titan (bool), cost (int|None), unlock_cost (int|None)."""
    res = requests.get(url, timeout=20)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    info = {
        "name": soup.find("h1", id="firstHeading").get_text(strip=True),
        "giant": False,
        "titan": False,
        "cost": None,
        "unlock_cost": None,
    }

    # ---------- 2a. First try: parse the usual <table class="infobox"> ----------
    box = soup.find("table", class_="infobox")
    if box:
        for row in box.select("tr"):
            th, td = row.find("th"), row.find("td")
            if not th or not td:
                continue
            k = th.get_text(strip=True).lower()
            v = td.get_text(strip=True)
            if k == "giant":
                info["giant"] = v.lower() in {"yes", "✔", "true"}
            elif k == "cost":
                info["cost"] = _as_int(v)
            elif k in {"unlock cost", "unlock_cost"}:
                info["unlock_cost"] = _as_int(v)
            if "titan" in k or "titan" in v.lower():
                info["titan"] = True

    # ---------- 2b. Fallback: scan page text for the spec block ----------
    if info["cost"] is None:  # (means infobox route probably failed)
        # The spec block appears as single-line items near the top: “Giant No”, “Cost 100”, …
        text_block = soup.get_text(" ", strip=True)  # flatten to 1 big string
        # Giant (Yes / No)
        m = re.search(r"\bGiant\s+(Yes|No|✔|✘)", text_block, re.I)
        if m:
            info["giant"] = m.group(1).lower() in {"yes", "✔"}
        # Cost
        m = re.search(r"\bCost\s+(\d+)", text_block, re.I)
        if m:
            info["cost"] = int(m.group(1))
        # Unlock cost
        m = re.search(r"\bUnlock\s+cost\s+(\d+)", text_block, re.I)
        if m:
            info["unlock_cost"] = int(m.group(1))
        # Titan keyword anywhere on the page
        if re.search(r"\bTitan\b", text_block, re.I):
            info["titan"] = True

    return info


# ---------- 3. tiny helper ----------
def _as_int(s: str) -> int | None:
    s = re.sub(r"[^\d]", "", s)
    return int(s) if s else None
    return info


def main():
    unit_urls = get_unit_links()
    all_units = []
    for u in unit_urls:
        try:
            data = parse_unit_page(u)
            all_units.append(data)
        except Exception as e:
            print(f"⚠️ failed to parse {u}: {e}")

    # output JSON
    print(json.dumps({"units": all_units}, indent=2))
    # save json into data folder
    with open("data/units2.json", "w") as f:
        json.dump({"units2": all_units}, f, indent=2)


if __name__ == "__main__":
    main()
