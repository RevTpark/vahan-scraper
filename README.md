# Vahan Scraper

A scraper for the [Vahan Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml): India's national vehicle registration database.

Two scraping methods are available:

| | `scraper.py` | `api.py` |
|---|---|---|
| Method | Playwright browser automation | Direct AJAX/HTTP requests |
| Output | `.xlsx` (downloaded from site) | `.csv` (parsed from HTML table) |
| Speed | Slower (browser startup + download per file) | ~10× faster (plain HTTP, no browser) |
| Use when | You need the original XLSX format | You need speed or can't install Playwright |

Both tools use identical CLI flags and write to the same `--out` directory layout, so you can switch between them without changing anything else.

---

## Requirements

```bash
pip install -r requirements.txt
playwright install chromium   # only needed for scraper.py
```

---

## scraper.py - Browser-based (XLSX output)

```
python3 scripts/scraper.py --yaxis <value> --xaxis <value> [options]
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `--yaxis` | Yes* | — | Y-Axis variable |
| `--xaxis` | Yes* | — | X-Axis variable |
| `--year` | No | current year | Single year to scrape |
| `--start-year` | No | — | Start of year range (inclusive) |
| `--end-year` | No | current year | End of year range (inclusive) |
| `--state` | No | all-states aggregate | State name, partial match (e.g. `Kerala`) |
| `--all-rtos` | No | — | Loop through every RTO in the selected state (requires `--state`) |
| `--rto` | No | — | One or more specific RTO names (requires `--state`) |
| `--out` | No | `vahan_data/` | Output directory |
| `--list-options` | No | — | Print all available dropdown options and exit |
| `--no-headless` | No | — | Show the browser window (useful for debugging) |

*Required unless `--list-options` is used.

### Examples

```bash
# See all available states, axes, and years from the live site
python3 scripts/scraper.py --list-options

# All-states aggregate, current year
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel"

# Specific state and year
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025

# Year range for a state
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" \
  --state "Kerala" --start-year 2020 --end-year 2026

# All RTOs in a state, year range
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" \
  --state "Kerala" --all-rtos --start-year 2020 --end-year 2026

# Specific RTOs
python3 scripts/scraper.py --yaxis "Maker" --xaxis "Fuel" \
  --state "Kerala" --rto "TRIVANDRUM RTO - KL1" "KOLLAM RTO - KL2"

# Debug (show browser window)
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" \
  --state "Delhi" --no-headless
```

---

## api.py - HTTP-based (CSV output, no browser)

Replays the PrimeFaces AJAX protocol the dashboard uses internally.
Significantly faster than the browser-based approach.

```
python3 scripts/api.py --yaxis <value> --xaxis <value> [options]
```

Same flags as `scraper.py` except there is no `--no-headless` (no browser involved).
Output files are `.csv` instead of `.xlsx`.

### Examples

```bash
# See all available options (reads from live site, no browser)
python3 scripts/api.py --list-options

# All-states aggregate, current year
python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel"

# Specific state and year
python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025

# Year range for a state
python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" \
  --state "Kerala" --start-year 2020 --end-year 2026

# All RTOs in a state, year range
python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" \
  --state "Kerala" --all-rtos --start-year 2020 --end-year 2026

# Specific RTOs (partial name match)
python3 scripts/api.py --yaxis "Maker" --xaxis "Fuel" \
  --state "Kerala" --rto "TRIVANDRUM" "KOLLAM"
```

---

## Axis options

### Y-Axis
`Vehicle Category`, `Vehicle Class`, `Norms`, `Fuel`, `Maker`, `State`

### X-Axis
`Vehicle Category`, `Norms`, `Fuel`, `Vehicle Category Group`, `Financial Year`, `Calendar Year`, `Month Wise`

---

## Output structure

Both tools write to the same layout under `--out` (default: `vahan_data/`):

```
vahan_data/
└── <state>/                      # e.g. Kerala_87_ or all_states
    └── <rto>/                    # e.g. TRIVANDRUM_RTO___KL1 or all_rtos
        └── <yaxis>__<xaxis>/
            ├── 2020.xlsx          # scraper.py
            ├── 2021.csv           # api.py
            └── ...
```

Already-downloaded files are skipped automatically, so interrupted runs resume safely.
