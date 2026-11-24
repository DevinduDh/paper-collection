# CIM publication trends

Script for the Survey-CIM project. It counts how many papers on compute-in-memory
(CIM) and processing-in-memory (PIM) appeared per year in a set of computer-architecture and
design-automation conferences, and shows how the type of paper changes over time
(for example surveys, silicon implementations, simulators and tools).

It uses only metadata from OpenAlex (a free, open index). It does not scrape IEEE Xplore.

## Venues covered

Architecture venues: MICRO, ASPLOS, ISCA, HPCA.
EDA venues: DATE, DAC.

To add or change a venue, edit the VENUES table near the top of cim_trends.py.

## Requirements

- Python 3.9 or newer
- A free OpenAlex API key: https://openalex.org/settings/api

Install the packages:

    pip install -r requirements.txt

## Usage

Run from the folder that contains cim_trends.py.

    python cim_trends.py --api-key YOUR_KEY --mailto you@your-institution.edu --start 2012 --end 2026 --out out

Do not commit your API key to the repository. Pass it on the command line, or set it as an
environment variable instead:

    Windows (cmd):        set OPENALEX_API_KEY=YOUR_KEY
    Linux / macOS:        export OPENALEX_API_KEY=YOUR_KEY

The email given with --mailto is only sent to OpenAlex so they can identify you. Nothing is
emailed to you.

A full run takes several minutes. Progress lines are printed while it runs, and it ends with
"Wrote files to out/".

### Options

    --start, --end   first and last publication year (default 2012 to 2026)
    --bin N          group N years together in the paper-type plots (default 1)
    --broad          also count near-memory computing and memristor crossbar papers
    --out DIR        output folder (default out)
    --from-csv FILE  skip downloading and redraw the plots from an existing cim_papers.csv

## Output files

    cim_papers.csv              every matched paper: year, venue, title, DOI, type
    counts_year_by_venue.csv    papers per year per venue (the data behind figure 1)
    fig1_cim_per_year.png       number of CIM/PIM papers per year, stacked by venue
    fig2_type_trends_all.png    paper types per year, all venues
    fig2_type_trends_architecture.png   same, architecture venues only
    fig2_type_trends_eda.png            same, EDA venues only

The CSV files use commas. If Excel puts everything in one column (common with Dutch or
other European regional settings), open them with Data > From Text/CSV and choose the
comma delimiter, with UTF-8 as the file origin.

## How a paper is counted as CIM

A paper counts if its title or abstract contains a phrase such as compute-in-memory,
computing-in-memory, processing-in-memory, in-memory computing or logic-in-memory, or the
acronym CIM or CiM. The acronyms PIM and IMC only count when a memory word (SRAM, ReRAM,
crossbar and so on) also appears, because they have other meanings elsewhere.

## How paper types are assigned

Types are assigned by keyword rules on the title and abstract. The first rule that matches wins,
in this order: survey or review; simulator, tool or benchmark; compiler, mapping or software;
silicon or tapeout; algorithm or hardware-aware co-design; architecture or accelerator;
circuit or device level; other.

## Known limitations

- Many IEEE records have no abstract in OpenAlex, so those papers are matched on the title
  only. The in_title column shows where each match was found. Totals are a lower bound.
- Matches found only in the abstract (in_title is False) can include false positives. Check
  these by hand before using them in the paper.
- The paper-type labels are heuristic. Spot-check a sample of each type before relying on the
  trend plots.
- Coverage in OpenAlex is uneven, so a zero or a sudden drop for one venue in one year is
  probably an indexing gap, not a real drop. 2026 should be treated as a partial year.
- Some older ISCA and ASPLOS papers are indexed under ACM SIGARCH Computer Architecture News
  (or SIGPLAN Notices) instead of the conference name. Those records cannot be attributed to
  a conference and are not counted.
- The venue-mix affects the type trends, which is why the architecture and EDA plots are
  produced separately.

## Data source

OpenAlex: https://openalex.org (API documentation: https://docs.openalex.org)
