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
    import pandas as pd
    import altair as alt

    # ---------- Load main + meta ----------
    if not DATA_FILE.exists():
        st.error("Run `python mechabellum_builder.py scrape` first.")
        return
    data = json.loads(DATA_FILE.read_text())
    tiers = load_tiers()

    # extra meta files
    units2 = json.loads((DATA_DIR / "units2.json").read_text())["units2"]
    units_meta = {u["name"]: u for u in units2}
    chaf_units = json.loads((DATA_DIR / "chaf.json").read_text())["chaf"]

    # ---------- UI header ----------
    st.set_page_config("Mechabellum Build Assistant", "🤖", layout="wide")
    st.title("🤖 Mechabellum Build Assistant")

    # Current round selector
    round_num = st.number_input("Current round", 1, 10, 1, step=1)

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
            f"<span style='background:{color_map[t]};padding:2px 6px;"
            f"border-radius:4px;color:#fff;font-size:0.7em'>{t}</span>"
            if t
            else ""
        )

    # ---------- Pickers ----------
    all_units = sorted(data.keys())
    col1, col2, col3 = st.columns(3)
    with col1:
        my_units = st.multiselect("My build", all_units)
    with col2:
        enemy_units = st.multiselect("Enemy units", all_units)
    with col3:
        struggle_units = st.multiselect(
            "Struggle units",
            enemy_units,
            help="Units you struggle against or your opponent comits into them.",
        )

    st.divider()

    # ---------- Counters & Vulnerabilities (side-by-side) ----------
    if enemy_units or my_units:
        lcol, rcol = st.columns(2)

        if enemy_units:
            with lcol:
                st.subheader("Suggested counters (tier-weighted)")
                for u, n in rank_counters(enemy_units, data, tiers):
                    cov = [
                        e
                        for e in enemy_units
                        if u in data.get(e, {}).get("countered_by", [])
                    ]
                    st.markdown(
                        f"- **{u}** {badge(u)} — counters **{n}**: _{', '.join(cov)}_",
                        unsafe_allow_html=True,
                    )

        if my_units:
            with rcol:
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

    colA, colB, colC = st.columns(3)
    # ---------- Focus panels ----------
    if my_units and enemy_units:
        st.divider()

        def enemy_has_counter(u: str) -> bool:
            return any(
                en
                in data.get(u, {}).get(
                    "countered_by", []
                )  # unit u is countered by enemy en
                or u
                in data.get(en, {}).get(
                    "used_against", []
                )  # enemy en is strong against unit u
                for en in enemy_units
            )

        # 🔒 Safe upgrades
        with colA:
            st.header("🔒 Safe upgrades")
            safe = [u for u in my_units if not enemy_has_counter(u)]
            if safe:
                for u in safe:
                    st.markdown(f"- **{u}** {badge(u)}", unsafe_allow_html=True)
            else:
                st.write("Enemy can answer every fielded unit.")

        # 🚀 Free punish picks
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

        with colC:
            st.header("🚫 Avoid for now")
            avoid = []
            for cand in all_units:
                cnt = sum(
                    1
                    for en in enemy_units
                    if cand in data.get(en, {}).get("used_against", [])
                )
                if cnt:
                    avoid.append((cand, cnt))
            avoid.sort(key=lambda x: (-x[1], x[0]))
            if avoid:
                for u, n in avoid:
                    label = "hard" if n >= 2 else "soft"
                    st.markdown(
                        f"- **{u}** {badge(u)} ({label} – {n} enemy counter{'s' if n>1 else ''})",
                        unsafe_allow_html=True,
                    )
            else:
                st.write("Enemy build does not strongly counter anything directly.")

    # ---------- Next focus suggestion ----------
    if my_units and enemy_units:
        st.divider()
        st.header("🔮 Next focus suggestion")

        # ---- scoring ----
        def score_unit(u: str) -> float:
            meta = units_meta.get(u, {})
            is_titan = meta.get("titan", False)
            is_giant = meta.get("giant", False)
            cost = meta.get("cost", 300)
            unlock = meta.get("unlock_cost", 0)

            # subject to change

            chaf_score = 0
            arc_score = 0

            # if name u is chaff unit and round==1 add to score
            if u in chaf_units and round_num == 1:
                if enemy_has_counter(u):
                    chaf_score = 8
                chaf_score = 13
            # if build doesn't have arclight and round==1 add to score
            if "Arclight" not in my_units and round_num == 1 and u == "Arclight":
                arc_score = 9
                if any(chaf in enemy_units for chaf in chaf_units):
                    arc_score = 15

            # if chaf unit is in my build and round!=1 add to score
            if u in chaf_units and round_num != 1:
                chaf_score = (
                    -3
                )  # we already have the tip in place so usually we don't want to upgrade it

            # coverage
            already = {
                e
                for m in my_units
                for e in enemy_units
                if m in data.get(e, {}).get("countered_by", [])
            }
            new_cov = [
                e
                for e in enemy_units
                if u in data.get(e, {}).get("countered_by", []) and e not in already
            ]
            overlap = [
                e
                for e in enemy_units
                if u in data.get(e, {}).get("countered_by", []) and e in already
            ]
            coverage_score = len(new_cov) * 2 - len(overlap) * 0.3

            # tier / in-build
            t_val = TIER_RANK.get(tiers.get(u, ""), 0)
            in_build = 1 if u in my_units else 0

            # titan / giant rules
            titan_pen = (
                -999
                if is_titan and any(units_meta[m].get("titan") for m in my_units)
                else 0
            )
            giants_in = sum(units_meta[m].get("giant", False) for m in my_units)
            giant_pen = 0
            if is_giant:
                if giants_in == 1:
                    giant_pen = -2
                elif giants_in == 2:
                    giant_pen = -5
                elif giants_in >= 3:
                    giant_pen = -10

            if round_num <= 3:
                # In early rounds, penalize titan units slightly
                early_pen = -10 if is_titan else 0
                early_pen += -6 if is_giant else 0
            elif round_num < 6:
                # Moderate penalty for rounds 4-5
                early_pen = -10 if is_titan else 0
            elif round_num == 6:
                # Lessen the penalty slightly at round 6
                early_pen = -7 if is_titan else 0
            else:
                # Linearly decrease early-round penalty from -3 at round 7 to 0 at round 10 and above
                early_pen = -(10 - round_num) if round_num < 10 else 0

            # cost scaling
            cost_pen = 0  # Default initialization
            if round_num <= 3:
                cost_pen = -(cost + unlock) / 400
            elif round_num <= 6:
                cost_pen = (cost + unlock) / 450
            elif round_num > 6:
                cost_pen = (cost + unlock) / (600 - 50 * (round_num - 6))

            # enemy counters
            # Combine enemy counters and enemies used against my units into a unique list
            enemy_interactions = {
                en
                for en in enemy_units
                if en in data.get(u, {}).get("countered_by", [])
                or u in data.get(en, {}).get("used_against", [])
            }
            interaction_count = len(enemy_interactions)

            # combine penalties
            vuln_pen = -8 * interaction_count
            # if u counters struggle_units +5
            if any(
                u in data.get(s, {}).get("used_against", []) for s in struggle_units
            ):
                struggle_priority = 10

            return (
                coverage_score
                + t_val * 0.7
                + in_build
                + titan_pen
                + giant_pen
                + early_pen
                + cost_pen
                + vuln_pen
                + chaf_score
                + arc_score
                + struggle_priority
            )

        # chaf advice round 1
        if round_num == 1:
            best_chaf = max(chaf_units, key=score_unit)
            st.success(
                f"🪳 Early-round tip: play **2× {best_chaf}** chaff "
                f"(or 1× {best_chaf} and a light clear like Arclight)."
            )

        # ranking
        ranked = sorted(all_units, key=score_unit, reverse=True)
        best, second = ranked[0], ranked[1]

        def explain(u: str) -> list[str]:
            meta = units_meta.get(u, {})
            if round_num == 1 and u in chaf_units:
                lines = [("Add more" if u in my_units else "Adding") + f" **{u}**"]
            else:
                lines = [("Upgrading" if u in my_units else "Adding") + f" **{u}**"]
            lines.append(f"• Composite score: `{score_unit(u):.2f}`")
            lines.append(
                f"• Cost: {meta.get('cost',300)} (+{meta.get('unlock_cost',0)} unlock)"
            )
            if meta.get("titan"):
                lines.append("• 🚀 Titan-class (limit 1)")
            if meta.get("giant"):
                lines.append("• 🛡️ Giant")
            # coverage
            already = {
                e
                for m in my_units
                for e in enemy_units
                if m in data.get(e, {}).get("countered_by", [])
            }
            new_cov = [
                e
                for e in enemy_units
                if u in data.get(e, {}).get("countered_by", []) and e not in already
            ]
            ov_cov = [
                e
                for e in enemy_units
                if u in data.get(e, {}).get("countered_by", []) and e in already
            ]
            if new_cov:
                lines.append(
                    f"• New unique coverage: {len(new_cov)} → {', '.join(new_cov)}"
                )
            if ov_cov:
                lines.append(f"• Overlap coverage: {len(ov_cov)} → {', '.join(ov_cov)}")
            t_tag = tiers.get(u)
            if t_tag:
                lines.append(f"• Tier rank: **{t_tag}**")
            enemy_cnt = [
                en
                for en in enemy_units
                if en in data.get(u, {}).get("countered_by", [])
            ]
            if enemy_cnt:
                lines.append(
                    f"• ⚠️ {len(enemy_cnt)} enemy counter(s): {', '.join(enemy_cnt)}"
                )
            return lines

        # Best suggestion
        st.markdown(f"### 🎯 Primary: {best} {badge(best)}", unsafe_allow_html=True)
        for line in explain(best):
            st.markdown(line, unsafe_allow_html=True)

        # Second-best suggestion
        if second:
            st.divider()
            st.markdown(
                f"### 🎯 Secondary: {second} {badge(second)}",
                unsafe_allow_html=True,
            )
            for line in explain(second):
                st.markdown(line, unsafe_allow_html=True)

        st.divider()
        st.info(
            f"📌 You can always add more chaff: **{max(chaf_units, key=score_unit)}**. "
        )
        if round_num < 4:
            st.info(
                f"Don't buy tech before round 4. "
                f"If no drop happened and it is round 3, take an item in choice and wait one turn, then pick a unit in drop to put on it."
            )
        st.divider()
        # ---- Altair chart ----
        chart_df = pd.DataFrame(
            {"unit": ranked[:10], "score": [score_unit(u) for u in ranked[:10]]}
        )
        st.altair_chart(
            alt.Chart(chart_df)
            .mark_bar(size=28)
            .encode(
                x=alt.X("score:Q", sort="-x", title="Composite Score"),
                y=alt.Y("unit:N", sort="-x"),
                color=alt.Color(
                    "score:Q", scale=alt.Scale(scheme="blues"), legend=None
                ),
                tooltip=["unit:N", "score:Q"],
            )
            .properties(height=380),
            use_container_width=True,
        )

    # ---------- Detail expanders ----------
    st.divider()
    for head, units, key in (
        ("Enemy guides", enemy_units, "how_to_counter"),
        ("My unit guides", my_units, "how_to_play"),
    ):
        if units:
            st.header(head)
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
                        textwrap.fill(info.get(key, "No guide available."), 100)
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
