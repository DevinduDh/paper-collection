#!/usr/bin/env python3
"""
CIM publication trends across selected computer architecture / EDA conferences.

Step 1: count papers on compute-in-memory / processing-in-memory (CIM, PIM, IMC ...)
        per year, per venue  -> fig1_cim_per_year.png (stacked bars), fig3_cim_lines_by_venue.png (lines)
Step 2: classify each paper (survey / silicon / simulator-tool / compiler-mapping /
        architecture / circuit-device / algorithm / other) and plot how the mix
        changes over time -> fig2_type_trends_*.png

Data source: OpenAlex (free). Only metadata is used. No scraping of IEEE Xplore.

Usage:
    pip install requests pandas matplotlib
    python cim_trends.py --api-key YOUR_KEY --mailto you@rug.nl --start 2012 --end 2026 --out out
    (OpenAlex needs a free API key for paging: https://openalex.org/settings/api.
     Env vars OPENALEX_API_KEY / OPENALEX_MAILTO also work; on Windows cmd use `set`, not `export`.)

Caveats (read before trusting the numbers):
  * OpenAlex abstracts are missing for many IEEE records, so for those papers the match
    is on the title only. Column `in_title` tells you where the match was found.
  * The paper-type labels are keyword heuristics. Spot-check cim_papers.csv.
  * 2026 may be incomplete depending on when proceedings were indexed.
"""
import argparse
import os
import re
import sys
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests

API = "https://api.openalex.org/works"

# venue label -> (include regex, exclude regex or None, group)
VENUES = {
    "ISSCC": (r"international solid[- ]state circuits conference|\bISSCC\b", None, "Circuits"),
    "VLSI Symp.": (r"symposi\w* on vlsi", None, "Circuits"),
    "CICC": (r"custom integrated circuits conference|\bCICC\b", None, "Circuits"),
    "ESSERC/ESSCIRC": (r"european solid[- ]state|\bESSERC\b|\bESSCIRC\b|\bESSDERC\b", None, "Circuits"),
    "ICCAD": (r"conference on computer[- ]aided design|\bICCAD\b", None, "Design/Systems"),
    "ASP-DAC": (r"asia and south pacific design automation|\bASP-?DAC\b", None, "Design/Systems"),
    "ISLPED": (r"low power electronics and design|\bISLPED\b", None, "Design/Systems"),
    "ISCAS": (r"international symposium on circuits and systems|\bISCAS\b",
              r"midwest|mwscas|latin|nordic|integrated circuits and systems", "Design/Systems"),
    # --- architecture and EDA venues (added by collaborator) ---
    "MICRO": (r"symposium on microarchitecture|(?-i:\bMICRO\b)",
              r"^\s*ieee micro\s*$", "Architecture"),          # IEEE Micro is the magazine
    "ASPLOS": (r"architectural support for programming languages and operating systems|\bASPLOS\b",
               None, "Architecture"),
    "ISCA": (r"international symposium on computer architecture|\bISCA\b",
             r"high[- ]performance computing|sbac|speech", "Architecture"),  # SBAC-PAD, Interspeech
    "HPCA": (r"high[- ]performance computer architecture|\bHPCA\b", None, "Architecture"),
    "DATE": (r"design,? automation,? (?:and|&) test in europe|(?-i:\bDATE\b)", None, "EDA"),
    "DAC": (r"design automation conference|(?-i:\bDAC\b)",
            r"asia and south pacific|asp-?dac|asme|design engineering technical", "EDA"),
}
VENUE_RX = {k: (re.compile(inc, re.I), re.compile(exc, re.I) if exc else None, grp)
            for k, (inc, exc, grp) in VENUES.items()}

# Plot styling. Each venue has a fixed solid colour (chosen to be as different from each other as
# possible, similar hues kept far apart in the stack), so a venue looks the same in every run and
# stays the same colour when --venues drops others. Colours are assigned in the order of VENUES;
# a 15th venue and beyond would reuse colours, so extend VENUE_PALETTE if you add venues.
VENUE_PALETTE = ["#1d7bc3", "#a7ec0f", "#eb1a09", "#f748ec", "#c58c00", "#43eee4", "#1a1a1a",
                 "#016833", "#9b354f", "#baabfe", "#605c03", "#2844e3", "#874a18", "#0ff5a7"]
VENUE_COLORS = {v: VENUE_PALETTE[i % len(VENUE_PALETTE)] for i, v in enumerate(VENUES)}
VENUE_MARKERS_LIST = ["o", "s", "^", "v", "D", "P", "X", "*", "<", ">", "h", "p", "d", "H"]
VENUE_MARKERS = {v: VENUE_MARKERS_LIST[i % len(VENUE_MARKERS_LIST)] for i, v in enumerate(VENUES)}
# Colour-blind-safe palette (Okabe-Ito) for the 8 paper types in the type-trend plots.
OKABE_ITO = ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7"]

# ---------------------------------------------------------------- what counts as CIM
CORE = re.compile(
    r"\b(?:compute|computing|computation|processing|process|logic)[\s-]+(?:in|within)[\s-]+(?:the[\s-]+)?(?:\w+[\s-]+)?memory\b"
    r"|\bin[\s-]?memory[\s-]+(?:comput\w*|process\w*|logic|search|accelerat\w*)\b"
    r"|\bin[\s-]?(?:sram|dram|reram|rram|mram|flash|fefet|pcm|nvm)[\s-]+(?:comput\w*|process\w*)\b"
    r"|\b(?:compute|computing)[\s-]in[\s-](?:sram|dram|reram|rram|mram|flash|fefet|pcm|nvm)\b",
    re.I)
ACRONYM_STRONG = re.compile(r"\b(?:CIM|CiM)\b")               # case-sensitive on purpose
ACRONYM_WEAK = re.compile(r"\b(?:PIM|PiM|IMC)\b")             # needs a memory word nearby
MEMWORD = re.compile(r"memory|sram|dram|reram|rram|mram|fefet|crossbar|memristi?\w*|nvm|pcm", re.I)
BROAD = re.compile(r"near[\s-]memory[\s-]+(?:comput\w*|process\w*)|memristi?\w*[\s-]+crossbar|crossbar[\s-]+(?:array|accelerator)", re.I)

# server-side queries (OpenAlex search is stemmed, so "computing" also hits "compute")
QUERIES = [
    '"compute in memory" OR "computing in memory" OR "computation in memory" OR "processing in memory" '
    'OR "in-memory computing" OR "in-memory computation" OR "in-memory processing" OR "logic in memory"',
    'CIM OR CiM',
    '(PIM OR IMC) AND (memory OR SRAM OR DRAM OR ReRAM OR RRAM OR MRAM OR FeFET OR crossbar OR memristive)',
]
BROAD_QUERY = '"near-memory computing" OR "near-memory processing" OR "memristor crossbar" OR "memristive crossbar"'


def is_cim(title, abstract, broad=False):
    text = f"{title} {abstract}"
    if CORE.search(text):
        return True
    if ACRONYM_STRONG.search(text):
        return True
    if ACRONYM_WEAK.search(text) and MEMWORD.search(text):
        return True
    return bool(broad and BROAD.search(text))


# ---------------------------------------------------------------- paper-type heuristics
TYPE_RULES = [  # first match wins
    ("Survey / review", re.compile(
        r"\b(survey|review|overview|tutorial|perspective|roadmap|taxonomy|systematic|primer|a decade of|"
        r"challenges and opportunities|state[- ]of[- ]the[- ]art (?:and|in|of))\b", re.I)),
    ("Simulator / tool / benchmark", re.compile(
        r"\b(simulator|simulation (?:framework|platform|tool)|modeling (?:framework|tool)|evaluation framework|"
        r"benchmark(?:ing| suite)?|design[- ]space exploration|toolchain|cycle[- ]accurate|co-?simulation|"
        r"analytical model|performance model|open[- ]source|NeuroSim|MNSIM)\b", re.I)),
    ("Compiler / mapping / software", re.compile(
        r"\b(compiler|compilation|mapping|scheduling|dataflow|programming model|runtime|instruction set|ISA|"
        r"code generation|partitioning|software stack|operator fusion|tiling)\b", re.I)),
    ("Silicon / tapeout", re.compile(
        r"(\bfabricated\b|test chip|tape-?out|prototype|\bsilicon\b|\bmeasured\b|measurement results|"
        r"\b\d{1,3}\s?-?nm\b|\b\d+\s?(?:Kb|Mb|Gb)\b|TOPS/W|\bmacro\b)", re.I)),
    ("Algorithm / HW-aware co-design", re.compile(
        r"\b(quantization|noise[- ]aware|hardware[- ]aware|non-?idealit\w*|variation[- ]aware|variability|"
        r"fine-?tuning|retraining|pruning|sparsity)\b", re.I)),
    ("Architecture / accelerator", re.compile(
        r"\b(accelerator|architecture|microarchitecture|processor|engine|system|chiplet|3d)\b", re.I)),
    ("Circuit / device level", re.compile(
        r"\b(cell|bitcell|device|sense amplifier|ADC|DAC|array|peripheral|circuit|selector|memristor)\b", re.I)),
]


TYPE_COLORS = dict(zip([lab for lab, _ in TYPE_RULES] + ["Other"], OKABE_ITO + ["#8c8c8c"]))


def classify(title, abstract):
    text = f"{title} {abstract}"
    for label, rx in TYPE_RULES:
        if rx.search(text):
            return label
    return "Other"


# ---------------------------------------------------------------- OpenAlex plumbing
def rebuild_abstract(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def venue_of(rec):
    names = []
    locs = [rec.get("primary_location")] + (rec.get("locations") or [])
    for loc in locs:
        if not loc:
            continue
        src = loc.get("source") or {}
        names += [src.get("display_name"), loc.get("raw_source_name")]
    names = [n for n in names if n]
    for label, (inc, exc, _grp) in VENUE_RX.items():
        for n in names:
            if inc.search(n) and not (exc and exc.search(n)):
                return label
    return None


def get(url, params, retries=6):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            print(f"  network error ({e}); retrying", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        raise SystemExit(f"OpenAlex returned HTTP {r.status_code}: {r.text[:300]}\n"
                         "If it says an API key is required, pass --api-key YOUR_KEY.")
    raise SystemExit("Giving up after repeated failures.")


def fetch_candidates(query, y0, y1):
    params = {
        "filter": f"title_and_abstract.search:{query},publication_year:{y0}-{y1}",
        "per-page": 200,
        "cursor": "*",
        "select": "id,doi,title,publication_year,abstract_inverted_index,primary_location,locations",
    }
    if os.getenv("OPENALEX_MAILTO"):
        params["mailto"] = os.environ["OPENALEX_MAILTO"]
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    n = 0
    while params["cursor"]:
        data = get(API, params)
        for rec in data.get("results", []):
            n += 1
            yield rec
        params["cursor"] = (data.get("meta") or {}).get("next_cursor")
        if n % 2000 < 200:
            print(f"  ...{n} candidate records", file=sys.stderr)
        time.sleep(0.15)


def collect(y0, y1, broad):
    queries = QUERIES + ([BROAD_QUERY] if broad else [])
    seen, rows = set(), []
    for q in queries:
        print(f"Query: {q[:70]}...", file=sys.stderr)
        for rec in fetch_candidates(q, y0, y1):
            venue = venue_of(rec)
            if not venue:
                continue
            title = rec.get("title") or ""
            abstract = rebuild_abstract(rec.get("abstract_inverted_index"))
            if not is_cim(title, abstract, broad):
                continue
            doi = (rec.get("doi") or "").lower()
            # One paper can be indexed as several OpenAlex works with different DOIs
            # (e.g. ACM DL 10.5555/... and EDAA 10.7873/... for DATE), so dedupe on
            # venue + normalized title as well as on the DOI.
            keys = {(venue, re.sub(r"\W+", "", title.lower()))}
            if doi:
                keys.add(doi)
            if keys & seen:
                continue
            seen |= keys
            rows.append({
                "year": rec.get("publication_year"), "venue": venue,
                "group": VENUE_RX[venue][2], "title": title, "doi": doi,
                "in_title": is_cim(title, "", broad),
                "has_abstract": bool(abstract),
                "type": classify(title, abstract),
                "openalex_id": rec.get("id"),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- plots
def plot_per_year(df, y0, y1, path):
    years = list(range(y0, y1 + 1))
    pivot = (df.pivot_table(index="year", columns="venue", values="title", aggfunc="count")
               .reindex(years).fillna(0).astype(int))
    pivot = pivot[[v for v in VENUES if v in pivot.columns]]
    fig, ax = plt.subplots(figsize=(12, 5.5))
    pivot.plot(kind="bar", stacked=True, ax=ax, width=0.8, legend=False,
               color=[VENUE_COLORS.get(v, "#999999") for v in pivot.columns],
               edgecolor="white", linewidth=0.6)
    for i, tot in enumerate(pivot.sum(axis=1)):
        ax.text(i, tot + max(1, pivot.sum(axis=1).max() * 0.01), str(tot), ha="center", fontsize=9)
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Number of CIM / PIM papers")
    ax.set_title("Compute-in-memory papers per year (selected conferences)")
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], title="Venue", fontsize=8, frameon=False,
              bbox_to_anchor=(1.01, 1), loc="upper left")      # same top-to-bottom order as the stacks
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return pivot


def plot_lines(pivot, path):
    """One line per venue: x = year, y = number of CIM / PIM papers. Each venue keeps its colour
    and has its own marker symbol."""
    order = pivot.sum().sort_values(ascending=False).index          # legend: busiest venue first
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for v in order:
        ax.plot(pivot.index, pivot[v], color=VENUE_COLORS.get(v, "#999999"),
                marker=VENUE_MARKERS.get(v, "o"), markersize=6.5, markeredgecolor="#333333",
                markeredgewidth=0.5, linewidth=1.8, label=v)
    ax.set_xticks(list(pivot.index))
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", alpha=0.3)
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Number of CIM / PIM papers")
    ax.set_title("Compute-in-memory papers per year, by venue")
    ax.legend(title="Venue", fontsize=8, frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left")
    fig.text(0.01, 0.005, "The latest year may be incomplete (proceedings not yet indexed).",
             fontsize=8, color="#555555", ha="left", va="bottom")
    fig.tight_layout(rect=[0, 0.03, 1, 1])
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def plot_types(df, y0, y1, binsize, title, path):
    if df.empty:
        return
    d = df.copy()
    d["period"] = d["year"] - ((d["year"] - y0) % binsize)
    labels = {p: (str(p) if binsize == 1 else f"{p}-{min(p + binsize - 1, y1)}")
              for p in range(y0, y1 + 1, binsize)}
    order = [lab for lab, _ in TYPE_RULES] + ["Other"]
    pivot = (d.pivot_table(index="period", columns="type", values="title", aggfunc="count")
               .reindex(list(labels)).fillna(0))
    pivot = pivot[[c for c in order if c in pivot.columns]]
    pivot.index = [labels[p] for p in pivot.index]
    share = pivot.div(pivot.sum(axis=1).replace(0, float("nan")), axis=0) * 100
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.5))
    colors = [TYPE_COLORS.get(c, "#999999") for c in pivot.columns]
    pivot.plot(kind="bar", stacked=True, ax=a1, width=0.8, color=colors, edgecolor="white", linewidth=0.6)
    a1.set_ylabel("Number of papers")
    a1.set_title(f"{title}: counts by type")
    share.plot(kind="bar", stacked=True, ax=a2, width=0.8, color=colors, edgecolor="white", linewidth=0.6,
               legend=False)
    a2.set_ylabel("Share of papers (%)")
    a2.set_title(f"{title}: share by type")
    for a in (a1, a2):
        a.set_xlabel("Year" if binsize == 1 else f"{binsize}-year period")
    handles, labs = a1.get_legend_handles_labels()
    a1.get_legend().remove()
    fig.legend(handles, labs, loc="lower center", ncol=4, fontsize=9, frameon=False)
    fig.tight_layout(rect=[0, 0.09, 1, 1])
    fig.savefig(path, dpi=200)
    plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2012)
    ap.add_argument("--end", type=int, default=2026)
    ap.add_argument("--bin", type=int, default=1, help="years per bin in the type-trend plots")
    ap.add_argument("--broad", action="store_true", help="also count near-memory computing and memristor crossbar papers")
    ap.add_argument("--out", default="out")
    ap.add_argument("--from-csv", help="skip fetching, re-plot from an existing cim_papers.csv")
    ap.add_argument("--venues", nargs="+", metavar="VENUE",
                    help="only use these venue labels, e.g. --venues MICRO ISCA DAC (default: all)")
    ap.add_argument("--api-key", help="free OpenAlex API key (https://openalex.org/settings/api)")
    ap.add_argument("--mailto", help="your email, for OpenAlex's polite pool")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if args.venues:
        unknown = [v for v in args.venues if v not in VENUES]
        if unknown:
            raise SystemExit(f"Unknown venue(s): {unknown}. Choose from: {list(VENUES)}")
        for k in list(VENUES):
            if k not in args.venues:
                del VENUES[k]
                del VENUE_RX[k]
    if args.api_key:
        os.environ["OPENALEX_API_KEY"] = args.api_key
    if args.mailto:
        os.environ["OPENALEX_MAILTO"] = args.mailto
    if not args.from_csv and not os.getenv("OPENALEX_API_KEY"):
        raise SystemExit("OpenAlex needs a free API key for paging through results.\n"
                         "Get one at https://openalex.org/settings/api, then run:\n"
                         "  python cim_trends.py --api-key YOUR_KEY --mailto you@rug.nl")

    if args.from_csv:
        df = pd.read_csv(args.from_csv)
        df["title"] = df["title"].fillna("")
    else:
        df = collect(args.start, args.end, args.broad)
        df = df.sort_values(["year", "venue", "title"]).reset_index(drop=True)
        df.to_csv(os.path.join(args.out, "cim_papers.csv"), index=False)

    print(f"\n{len(df)} CIM papers in the selected venues, "
          f"{int(df['in_title'].sum())} matched in the title.")
    counts = plot_per_year(df, args.start, args.end, os.path.join(args.out, "fig1_cim_per_year.png"))
    counts.to_csv(os.path.join(args.out, "counts_year_by_venue.csv"))
    plot_lines(counts, os.path.join(args.out, "fig3_cim_lines_by_venue.png"))
    print(counts.assign(TOTAL=counts.sum(axis=1)).to_string())

    plot_types(df, args.start, args.end, args.bin, "All venues",
               os.path.join(args.out, "fig2_type_trends_all.png"))
    for grp in sorted(df["group"].dropna().unique()):
        plot_types(df[df["group"] == grp], args.start, args.end, args.bin, grp,
                   os.path.join(args.out, f"fig2_type_trends_{grp.split('/')[0].lower()}.png"))
    print(f"\nWrote files to {args.out}/")


if __name__ == "__main__":
    main()
