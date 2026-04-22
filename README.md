# Vahan Scraper

A Playwright-based scraper for the [Vahan Dashboard](https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml): India's national vehicle registration database. Downloads `.xlsx` files for any combination of Y-Axis, X-Axis, state, RTO, and year.

## Requirements

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

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

*`--yaxis` and `--xaxis` are required unless `--list-options` is used.

### Y-Axis options

`Vehicle Category`, `Vehicle Class`, `Norms`, `Fuel`, `Maker`, `State`

### X-Axis options

`Vehicle Category`, `Norms`, `Fuel`, `Vehicle Category Group`, `Financial Year`, `Calendar Year`, `Month Wise`

---

## Examples

**See all available states, axes, and years from the live site:**
```bash
python3 scripts/scraper.py --list-options
```

**All-states aggregate, current year:**
```bash
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel"
```

**Specific state, specific year:**
```bash
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025
```

**Specific state, year range:**
```bash
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --start-year 2020 --end-year 2026
```

**All RTOs in a state, year range:**
```bash
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" \
  --state "Kerala" --all-rtos --start-year 2020 --end-year 2026
```

**Specific RTOs:**
```bash
python3 scripts/scraper.py --yaxis "Maker" --xaxis "Fuel" \
  --state "Kerala" --rto "TRIVANDRUM RTO - KL1" "KOLLAM RTO - KL2"
```

**Debug (show browser window):**
```bash
python3 scripts/scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Delhi" --no-headless
```

---

## Output structure

Files are saved under `--out` (default: `vahan_data/`) with this layout:

```
vahan_data/
└── <state>/               # e.g. Kerala_87_ or all_states
    └── <rto>/             # e.g. TRIVANDRUM_RTO___KL1 or all_rtos
        └── <yaxis>__<xaxis>/
            ├── 2020.xlsx
            ├── 2021.xlsx
            └── ...
```

Already-downloaded files are skipped automatically, so interrupted runs can be safely resumed.
