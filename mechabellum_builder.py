# mechabellum_builder.py – v2.1 (Tier URL fix + graceful fallback)
"""Mechabellum Build Assistant – tier‑aware

**v2.1 changes**
* Tier list URL updated to `.../mechabellum-unit-tier-list/` (site moved).
* `scrape_tier_list()` now tries a list of known slugs and raises a clear
  error if none work.
"""
from __future__ import annotations

import json, re, sys, textwrap, time
from pathlib import Path
from collections import Counter
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup, Tag, NavigableString

BASE = "https://mechamonarch.com"
COUNTERS_URL = f"{BASE}/guide/mechabellum-counters/"
TIER_SLUGS = [
    "mechabellum-unit-tier-list",  # current (Apr‑2025)
    "mechabellum-tier-list",  # legacy
]

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DATA_FILE = DATA_DIR / "units.json"
TIER_FILE = DATA_DIR / "tiers.json"

HEADERS = {"User-Agent": "Mechabellum-Builder/2.1"}
TIER_RANK = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}
###############################################################################
# Utility helpers
###############################################################################


def get_soup(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").title()


def collect_text(node: Tag | NavigableString) -> str:
    if isinstance(node, NavigableString):
        return str(node).strip()
    return node.get_text(" ", strip=True) if isinstance(node, Tag) else ""


###############################################################################
# Tier-list scraping
###############################################################################


def scrape_tier_list() -> Dict[str, str]:
    """Scrape tier list by tracking left‑hand letter badges (S/A/B/…)."""
    last_exc: Optional[Exception] = None
    soup: Optional[BeautifulSoup] = None
    for slug in TIER_SLUGS:
        try:
            soup = get_soup(f"{BASE}/guide/{slug}/")
            break
        except Exception as exc:
            last_exc = exc
    if soup is None:
        raise RuntimeError("Tier list not found on site", last_exc)

    tiers: Dict[str, str] = {}
    current: Optional[str] = None

    for elem in soup.descendants:
        if isinstance(elem, NavigableString):
            txt = str(elem).strip().upper()
            if txt in TIER_RANK and len(txt) == 1:
                current = txt
            continue
        if isinstance(elem, Tag):
            if elem.name == "figcaption":
                name = elem.get_text(strip=True).title()
                if name and current:
                    tiers[name] = current
            elif elem.name == "img" and elem.get("alt") and current:
                alt = elem["alt"].strip().title()
                if alt:
                    tiers.setdefault(alt, current)
    return tiers


###############################################################################
# Unit page scraping
###############################################################################


def extract_unit_names(start: Tag) -> List[str]:
    names, seen = [], set()
    cur = start.next_sibling
    while cur and not (isinstance(cur, Tag) and cur.name.startswith("h")):
        if isinstance(cur, Tag):
            for a in cur.find_all("a", href=True):
                m = re.search(r"/unit/([^/#]+)/?", a["href"])
                if m and m.group(1):
                    name = slug_to_name(m.group(1))
                    if name not in seen:
                        names.append(name)
                        seen.add(name)
        cur = cur.next_sibling
    return names


def collect_paragraphs_after(header: Tag) -> str:
    chunks: List[str] = []
    cur = header.next_sibling
    while cur and not (isinstance(cur, Tag) and cur.name.startswith("h")):
        txt = collect_text(cur)
        if txt:
            chunks.append(txt)
        cur = cur.next_sibling
    return "\n\n".join(chunks)


def scrape_unit_page(url: str) -> Dict:
    soup = get_soup(url)
    unit = slug_to_name(url.rstrip("/").split("/")[-1])
    hero_img: Optional[str] = None
    hero = soup.select_one("article img, main img")
    if hero and hero.get("src"):
        src = hero["src"]
        hero_img = src if src.startswith("http") else BASE + src

    used_against: List[str] = []
    countered_by: List[str] = []
    how_play = how_counter = ""

    for h in soup.find_all(re.compile("^h[1-3]$")):
        title = h.get_text(strip=True).lower()
        if title.startswith("used against"):
            used_against = extract_unit_names(h)
        elif title.startswith("countered by"):
            countered_by = extract_unit_names(h)
        elif title.startswith("how to play"):
            how_play = collect_paragraphs_after(h)
        elif "counter" in title and unit.lower() in title:
            how_counter = collect_paragraphs_after(h)

    return {
        "image": hero_img,
        "used_against": used_against,
        "countered_by": countered_by,
        "how_to_play": how_play,
        "how_to_counter": how_counter,
    }


def scrape_all_units() -> Dict[str, Dict]:
    idx = get_soup(COUNTERS_URL)
    links = {
        slug_to_name(m.group(1)): BASE + m.group(0)
        for a in idx.find_all("a", href=True)
        if (m := re.search(r"/unit/([^/#]+)/?", a["href"])) and m.group(1)
    }
    print(f"Found {len(links)} unit pages. Scraping …")
    data = {}
    for i, (name, url) in enumerate(links.items(), 1):
        print(f"  {i:02}/{len(links)} {name}")
        try:
            data[name] = scrape_unit_page(url)
            time.sleep(0.25)
        except Exception as exc:
            print(f"    ! {url}: {exc}")
    return data


###############################################################################
# Build logic helpers
###############################################################################


def load_tiers() -> Dict[str, str]:
    return json.loads(TIER_FILE.read_text()) if TIER_FILE.exists() else {}


def tier_val(name: str, tiers: Dict[str, str]) -> int:
    return TIER_RANK.get(tiers.get(name, ""), -1)


def rank_counters(enemy: List[str], data: Dict[str, Dict], tiers: Dict[str, str]):
    tally = Counter()
    for e in enemy:
        for c in data.get(e, {}).get("countered_by", []):
            tally[c] += 1
    for e in enemy:
        tally.pop(e, None)
    return sorted(
        tally.items(), key=lambda kv: (-kv[1], -tier_val(kv[0], tiers), kv[0])
    )


def find_vuln(mine: List[str], data: Dict[str, Dict], tiers: Dict[str, str]):
    vul = Counter()
    for m in mine:
        for c in data.get(m, {}).get("countered_by", []):
            vul[c] += 1
    for m in mine:
        vul.pop(m, None)
    return sorted(vul.items(), key=lambda kv: (-kv[1], -tier_val(kv[0], tiers), kv[0]))


###############################################################################
# Streamlit UI
###############################################################################


def run_app():
    import streamlit as st

    if not DATA_FILE.exists():
        st.error("Run `python mechabellum_builder.py scrape` first.")
        return
    data = json.loads(DATA_FILE.read_text())
    tiers = load_tiers()

    st.set_page_config("Mechabellum Build Assistant", "🤖", layout="wide")
    st.title("🤖 Mechabellum Build Assistant")

    color_map = {
        "S": "#e74c3c",
        "A": "#f39c12",
        "B": "#3498db",
        "C": "#7f8c8d",
        "D": "#2c3e50",
    }

    def badge(u: str) -> str:
        t = tiers.get(u)
        return (
            f"<span style='background:{color_map[t]};padding:2px 6px;border-radius:4px;color:#fff;font-size:0.75em'>{t}</span>"
            if t
            else ""
        )

    all_units = sorted(data.keys())
    col1, col2 = st.columns(2)
    with col1:
        my_units = st.multiselect("My build", all_units)
    with col2:
        enemy_units = st.multiselect("Enemy units", all_units)

    if enemy_units or my_units:
        left_col, right_col = st.columns(2)

        if enemy_units:
            with left_col:
                st.subheader("Suggested counters (tier-weighted)")
                ranked = rank_counters(enemy_units, data, tiers)
                for unit, count in ranked:
                    covered = [
                        e
                        for e in enemy_units
                        if unit in data.get(e, {}).get("countered_by", [])
                    ]
                    cover_txt = ", ".join(covered)
                    st.markdown(
                        f"- **{unit}** {badge(unit)} — counters **{count}**: _{cover_txt}_",
                        unsafe_allow_html=True,
                    )

        if my_units:
            with right_col:
                st.subheader("Vulnerabilities in my build")
                vul = find_vuln(my_units, data, tiers)
                if vul:
                    for u, n in vul:
                        st.markdown(
                            f"- **{u}** {badge(u)} (counters {n} of your units)",
                            unsafe_allow_html=True,
                        )
                else:
                    st.success("No listed hard counters – nice!")

    # Focus panels
    if my_units and enemy_units:
        st.divider()
        colA, colB, colC = st.columns(3)

        def enemy_has_counter(u: str) -> bool:
            return any(
                en in data.get(u, {}).get("countered_by", []) for en in enemy_units
            )

        # 🔒 Safe upgrades (in build, not countered)
        with colA:
            st.header("🔒 Safe upgrades")
            safe = [u for u in my_units if not enemy_has_counter(u)]
            if safe:
                for u in safe:
                    st.markdown(f"- **{u}** {badge(u)}", unsafe_allow_html=True)
            else:
                st.write("Enemy can answer every fielded unit.")

        # 🚀 Free punish picks (not in build & not countered)
        with colB:
            st.header("🚀 Free punish picks")
            free = [
                u for u in all_units if u not in my_units and not enemy_has_counter(u)
            ]
            if free:
                for u in free:
                    st.markdown(f"- **{u}** {badge(u)}", unsafe_allow_html=True)
            else:
                st.write("Enemy has coverage for all unused units.")

        # 🚫 Avoid for now (opponent can counter immediately)
        with colC:
            st.header("🚫 Avoid for now")

            avoid = []
            for candidate in all_units:
                # enemy units that can counter this candidate
                counters = [
                    en
                    for en in enemy_units
                    if candidate in data.get(en, {}).get("used_against", [])
                ]
                if counters:
                    avoid.append((candidate, len(counters)))

            avoid.sort(key=lambda x: (-x[1], x[0]))

            if avoid:
                for u, n in avoid:
                    label = "hard" if n >= 2 else "soft"
                    st.markdown(
                        f"- **{u}** {badge(u)} ({label} – {n} counters by current enemy build)",
                        unsafe_allow_html=True,
                    )
            else:
                st.write("Enemy build does not strongly counter anything directly.")

    # --------------------- Next focus suggestion ---------------------
    if my_units and enemy_units:
        st.divider()
        st.header("🔮 Next focus suggestion")

        def score_unit(u: str) -> float:
            already_covered = set()
            for myu in my_units:
                already_covered.update(
                    e
                    for e in enemy_units
                    if myu in data.get(e, {}).get("countered_by", [])
                )

            # What this new unit would newly cover
            newly_covers = [
                e
                for e in enemy_units
                if u in data.get(e, {}).get("countered_by", [])
                and e not in already_covered
            ]
            overlaps = [
                e
                for e in enemy_units
                if u in data.get(e, {}).get("countered_by", []) and e in already_covered
            ]

            unique_coverage = len(set(newly_covers))
            overlap_coverage = len(set(overlaps))

            t_val = TIER_RANK.get(tiers.get(u, ""), 0)
            in_build = 1 if u in my_units else 0

            # You want:
            # - Big reward for unique new coverages
            # - Small reward for overlap coverage (maybe 0.5 per)
            # - Strong scaling for multi-unique coverages
            coverage_score = unique_coverage * 2.5 + overlap_coverage * 0.65

            # Penalty if enemy already counters this unit
            enemy_counters = sum(
                1 for en in enemy_units if en in data.get(u, {}).get("countered_by", [])
            )
            penalty = -2 - (enemy_counters - 1) if enemy_counters > 0 else 0

            return coverage_score * 1.5 + t_val * 0.6 + in_build * 0.7 + penalty * 1.2

        candidates = set(all_units)
        best = max(candidates, key=score_unit)
        best_score = score_unit(best)

        reason_lines = []
        if best in my_units:
            reason_lines.append(
                f"Upgrading **{best}** makes sense because it already sits in your build"
            )
        else:
            reason_lines.append(f"Adding **{best}** to your build is attractive")

        cov = [
            e for e in enemy_units if best in data.get(e, {}).get("countered_by", [])
        ]
        if cov:
            reason_lines.append(
                f"• It directly counters {len(cov)} enemy unit(s): {', '.join(cov)}."
            )
        tier_tag = tiers.get(best)
        if tier_tag:
            reason_lines.append(
                f"• It is rated **{tier_tag} tier**, giving it high intrinsic value."
            )
        if best not in my_units and len(my_units) >= 6:
            reason_lines.append(
                "• Keeps your unit diversity manageable while improving coverage."
            )
        if any(en in data.get(best, {}).get("countered_by", []) for en in enemy_units):
            reason_lines.append(
                "• Note: enemy already holds a soft/hard counter, partly reducing impact."
            )

        st.markdown("".join(reason_lines), unsafe_allow_html=True)

    # Detail expanders
    st.divider()
    for heading, units, text_key in (
        ("Enemy guides", enemy_units, "how_to_counter"),
        ("My unit guides", my_units, "how_to_play"),
    ):
        if units:
            st.header(heading)
            for u in units:
                info = data.get(u, {})
                with st.expander(u):
                    st.markdown(badge(u), unsafe_allow_html=True)
                    if info.get("image"):
                        st.image(info["image"], width=260)
                    st.markdown(
                        "**Used against:** "
                        + ", ".join(info.get("used_against") or ["—"])
                    )
                    st.markdown(
                        "**Countered by:** "
                        + ", ".join(info.get("countered_by") or ["—"])
                    )
                    st.markdown(
                        textwrap.fill(info.get(text_key, "No guide available."), 100)
                    )

    st.markdown(
        f"<sup>Source: <a href='{BASE}' target='_blank'>{BASE}</a></sup>",
        unsafe_allow_html=True,
    )


###############################################################################
# CLI entry
###############################################################################


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "scrape":
            DATA_FILE.write_text(
                json.dumps(scrape_all_units(), indent=2, ensure_ascii=False)
            )
            print(f"Saved → {DATA_FILE}")
        elif cmd == "scrape_tier":
            TIER_FILE.write_text(
                json.dumps(scrape_tier_list(), indent=2, ensure_ascii=False)
            )
            print(f"Saved → {TIER_FILE}")
        else:
            print("Unknown command. Use scrape | scrape_tier | (no arg = run app)")
    else:
        run_app()


if __name__ == "__main__":
    main()


# python mechabellum_builder.py scrape         # refresh unit DB
# python mechabellum_builder.py scrape_tier    # refresh tier list
# streamlit run mechabellum_builder.py         # start the tier-aware UI
