#!/usr/bin/env python3
"""
Vahan Dashboard API Scraper
===========================
Faster alternative to scraper.py. Replays the PrimeFaces AJAX/xhtml
protocol directly with plain HTTP requests — no browser, no Playwright.

How it works:
  1. GET the dashboard page to extract the JSF ViewState token.
  2. AJAX POST to select state  → server returns updated RTO + Y-axis options.
  3. AJAX POST to set Y-axis and X-axis.
  4. For each year: AJAX POST to select year, then click Refresh.
  5. Parse the rendered HTML table from CDATA sections in the XML response.
  6. Paginate if the table has multiple pages (25 rows/page).
  7. Save as CSV per (state, rto, yaxis, xaxis, year).

Output format: CSV (rows = table rows, columns = table headers).
Output layout mirrors scraper.py so both tools share the same --out dir.

Usage:
  python3 scripts/api.py --list-options
  python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel"
  python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025
  python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --all-rtos --start-year 2020
  python3 scripts/api.py --yaxis "Maker" --xaxis "Fuel" --state "Kerala" --rto "TRIVANDRUM" "KOLLAM"
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

VAHAN_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml"
FORM_ID = "masterLayout_formlogin"
ROWS_PER_PAGE = 25

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_AJAX_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://vahan.parivahan.gov.in",
    "Referer": VAHAN_URL,
    "Faces-Request": "partial/ajax",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

# Input names that are stable across page versions — used to exclude them
# when dynamically searching for the state / display-type dropdown names.
_KNOWN_INPUT_NAMES = {
    "selectedRto_input", "yaxisVar_input", "xaxisVar_input",
    "selectedYearType_input", "selectedYear_input",
}

# Fallback Refresh IDs used when dynamic discovery fails
_REFRESH_IDS_FALLBACK = ["j_idt66", "j_idt71", "j_idt78", "j_idt65", "j_idt70", "j_idt77"]


# ── Utility ───────────────────────────────────────────────────────────────────

def safe_name(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-." else "_" for c in text).strip("_")


# ── ViewState helpers ─────────────────────────────────────────────────────────

def extract_viewstate(html: str) -> str:
    for pat in [
        r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"',
        r'value="([^"]+)"[^>]*name="javax\.faces\.ViewState"',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise ValueError("javax.faces.ViewState not found in page HTML")


def extract_viewstate_xml(xml: str) -> str:
    m = re.search(
        r'<update\s+id="javax\.faces\.ViewState"><!\[CDATA\[(.*?)\]\]></update>',
        xml,
    )
    return m.group(1) if m else ""


# ── Option discovery ──────────────────────────────────────────────────────────

def parse_options(soup: BeautifulSoup, select_name: str) -> dict[str, str]:
    """Return {value: display_label} for all options in <select name=select_name>."""
    sel = soup.find("select", {"name": select_name})
    if not sel:
        return {}
    return {
        o.get("value", ""): o.get_text(strip=True)
        for o in sel.find_all("option")
        if o.get("value", "")
    }


def parse_options_from_xml(xml: str, select_name: str) -> dict[str, str]:
    """Extract select options from CDATA sections in a PrimeFaces AJAX response."""
    for cd in re.findall(r'<!\[CDATA\[(.*?)\]\]>', xml, re.DOTALL):
        soup = BeautifulSoup(cd, "html.parser")
        opts = parse_options(soup, select_name)
        if opts:
            return opts
    return {}


def find_state_input_name(soup: BeautifulSoup) -> str | None:
    """
    Locate the hidden <select> name for the state dropdown.

    Definitive discriminator (confirmed by inspecting the live page HTML):
      - The state select always has value="-1" for the "All States" aggregate
        option, followed by 2-char alpha-only uppercase state codes (AN, AP…).
      - The display-type select (T/L/C/A) has no "-1" option.
      - No other select on the page matches both conditions.

    Falls back to known IDs seen across page versions.
    """
    for sel in soup.find_all("select"):
        name = sel.get("name", "")
        if not name or name in _KNOWN_INPUT_NAMES:
            continue
        all_vals = [o.get("value", "") for o in sel.find_all("option") if o.get("value", "")]
        if "-1" not in all_vals:
            continue
        state_codes = [v for v in all_vals if v != "-1"]
        if state_codes and all(v.isalpha() and v.isupper() for v in state_codes[:10]):
            return name
    # Fallbacks: IDs observed across different page versions (add new ones here)
    for fallback in ["j_idt36_input", "j_idt34_input", "j_idt41_input", "j_idt45_input"]:
        if soup.find("select", {"name": fallback}):
            return fallback
    return None


def find_display_input_name(soup: BeautifulSoup, state_name: str | None) -> str | None:
    """
    Locate the <select> name for the display-type dropdown
    (options: T=Thousand, L=Lakh, C=Crore, A=Actual).
    """
    for sel in soup.find_all("select"):
        name = sel.get("name", "")
        if not name or name in _KNOWN_INPUT_NAMES or name == state_name:
            continue
        vals = {o.get("value", "") for o in sel.find_all("option") if o.get("value", "")}
        if vals and vals <= {"T", "L", "C", "A"}:
            return name
    for fallback in ["j_idt25_input", "j_idt22_input", "j_idt28_input"]:
        if soup.find("select", {"name": fallback}):
            return fallback
    return None


def find_refresh_ids(soup: BeautifulSoup) -> list[str]:
    """
    Discover Refresh button IDs from the page HTML.
    The IDs are dynamic (j_idt66/71/78 today, but can shift with JSF re-renders).
    Falls back to a known list that covers observed page versions.
    """
    ids = [btn.get("id", "") for btn in soup.find_all("button")
           if btn.get_text(strip=True).lower() == "refresh" and btn.get("id", "")]
    return ids or _REFRESH_IDS_FALLBACK


def match_option(options: dict[str, str], query: str) -> tuple[str, str] | None:
    """
    Case-insensitive partial match against display labels.
    options: {value: label}
    Returns (value, label) or None.
    """
    q = query.lower().strip()
    for val, label in options.items():
        if label.lower() == q:
            return val, label
    for val, label in options.items():
        if q in label.lower():
            return val, label
    return None


# ── AJAX POST ─────────────────────────────────────────────────────────────────

def ajax_post(
    client: httpx.Client,
    form: dict,
    source: str,
    event: str | None = None,
    execute: str | None = None,
    render: str = "@all",
) -> tuple[str, str]:
    """
    Send a PrimeFaces AJAX partial POST.
    Returns (response_text, updated_viewstate).
    """
    data = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": source,
        "javax.faces.partial.execute": execute or source,
        "javax.faces.partial.render": render,
        **form,
    }
    if event:
        data["javax.faces.behavior.event"] = event
        data["javax.faces.partial.event"] = event
    else:
        data[source] = source  # button click: include the source key itself

    resp = client.post(VAHAN_URL, headers=_AJAX_HEADERS, data=data)
    resp.raise_for_status()
    new_vs = extract_viewstate_xml(resp.text)
    return resp.text, new_vs if new_vs else form["javax.faces.ViewState"]


# ── Table parsing ─────────────────────────────────────────────────────────────

def parse_table(resp_text: str) -> tuple[list[str], list[list[str]]]:
    """
    Extract headers and data rows from CDATA sections in a PrimeFaces AJAX response.

    Handles the Vahan-specific header/data column mismatch: the <th> row
    sometimes includes a phantom axis-label column at position 2 that the
    <td> rows omit. When detected, the phantom column is dropped and TOTAL
    is moved to the end to match the data layout.
    """
    raw_headers: list[str] = []
    all_rows: list[list[str]] = []

    for cd in re.findall(r'<!\[CDATA\[(.*?)\]\]>', resp_text, re.DOTALL):
        if "<th" not in cd.lower() and "<td" not in cd.lower():
            continue
        soup = BeautifulSoup(cd, "html.parser")

        ths = soup.find_all("th")
        if ths and len(ths) > 5:
            raw_headers = [th.get_text(strip=True) for th in ths]

        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if tds and len(tds) > 2:
                all_rows.append([td.get_text(strip=True) for td in tds])

    headers = raw_headers
    if (
        all_rows and raw_headers
        and len(raw_headers) == len(all_rows[0]) + 1
        and len(raw_headers) > 4
        and raw_headers[3] == "TOTAL"
    ):
        # Drop the phantom axis-label at index 2, move TOTAL to end
        headers = raw_headers[:2] + raw_headers[4:] + ["TOTAL"]

    return headers, all_rows


def paginate_table(
    client: httpx.Client, form: dict, initial_resp: str
) -> tuple[list[str], list[list[str]]]:
    """Collect all paginated rows from groupingTable. Returns (headers, all_rows)."""
    headers, all_rows = parse_table(initial_resp)

    if "ui-paginator" not in initial_resp:
        return headers, all_rows

    page = 1
    while page < 100:  # safety cap
        first = page * ROWS_PER_PAGE
        page += 1

        page_data = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "groupingTable",
            "javax.faces.partial.execute": "groupingTable",
            "javax.faces.partial.render": "groupingTable",
            "javax.faces.behavior.event": "page",
            "javax.faces.partial.event": "page",
            "groupingTable_pagination": "true",
            "groupingTable_first": str(first),
            "groupingTable_rows": str(ROWS_PER_PAGE),
            "groupingTable_encodeFeature": "true",
            **form,
        }

        try:
            resp = client.post(VAHAN_URL, headers=_AJAX_HEADERS, data=page_data)
            resp.raise_for_status()
            new_vs = extract_viewstate_xml(resp.text)
            if new_vs:
                form["javax.faces.ViewState"] = new_vs

            _, new_rows = parse_table(resp.text)
            if not new_rows:
                break
            all_rows.extend(new_rows)
            print(f"      Page {page}: +{len(new_rows)} rows (total {len(all_rows)})")
            if len(new_rows) < ROWS_PER_PAGE:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"      Pagination error at page {page}: {e}")
            break

    return headers, all_rows


# ── Core scrape logic ─────────────────────────────────────────────────────────

def scrape(args):
    current_year = datetime.now().year

    if args.start_year is not None:
        years = list(range(int(args.start_year), int(args.end_year or current_year) + 1))
    elif args.year is not None:
        years = [int(args.year)]
    else:
        years = [current_year]

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    with httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        verify=False,
        headers={"User-Agent": _UA, "Accept": "text/html"},
    ) as client:

        # ── Step 1: Fetch page, extract ViewState and all option maps ──────
        print("Fetching Vahan dashboard…")
        resp = client.get(VAHAN_URL)
        resp.raise_for_status()
        vs = extract_viewstate(resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")

        state_input_name   = find_state_input_name(soup)
        display_input_name = find_display_input_name(soup, state_input_name)
        refresh_ids        = find_refresh_ids(soup)

        # Exclude the "-1" aggregate entry so only named states are searchable/listable
        states_map = {
            k: v for k, v in parse_options(soup, state_input_name).items() if k != "-1"
        } if state_input_name else {}
        yaxis_map  = parse_options(soup, "yaxisVar_input")
        xaxis_map  = parse_options(soup, "xaxisVar_input")
        years_map  = {
            k: v for k, v in parse_options(soup, "selectedYear_input").items() if k not in ("", "A")
        }

        # ── List-options mode ──────────────────────────────────────────────
        if args.list_options:
            print("\n=== Vahan Dashboard Dropdown Options ===")
            print(f"\nStates ({len(states_map)}):")
            for code, label in states_map.items():
                print(f"  [{code}]  {label}")
            print("\nY-Axis Options:")
            for val, label in yaxis_map.items():
                print(f"  [{val}]  {label}")
            print("\nX-Axis Options:")
            for val, label in xaxis_map.items():
                print(f"  [{val}]  {label}")
            print("\nYear Options:")
            for val, label in years_map.items():
                print(f"  [{val}]  {label}")
            return

        # ── Resolve Y-axis / X-axis display names to form values ──────────
        yaxis_match = match_option(yaxis_map, args.yaxis)
        if not yaxis_match:
            print(f"[ERROR] Y-Axis '{args.yaxis}' not found. Run --list-options.")
            sys.exit(1)
        yaxis_val, yaxis_label = yaxis_match

        xaxis_match = match_option(xaxis_map, args.xaxis)
        if not xaxis_match:
            print(f"[ERROR] X-Axis '{args.xaxis}' not found. Run --list-options.")
            sys.exit(1)
        xaxis_val, xaxis_label = xaxis_match

        print(f"Y-Axis: {yaxis_label}  (form value: {yaxis_val})")
        print(f"X-Axis: {xaxis_label}  (form value: {xaxis_val})")

        # ── Build base form (tracks full server-side form state) ───────────
        form: dict = {
            FORM_ID: FORM_ID,
            "yaxisVar_input": yaxis_val,
            "xaxisVar_input": xaxis_val,
            "selectedRto_input": "-1",       # All RTOs (overridden per-loop below)
            "selectedYearType_input": "C",    # Calendar Year
            "selectedYear_input": str(years[0]),
            "javax.faces.ViewState": vs,
        }
        if display_input_name:
            form[display_input_name] = "A"   # Actual values, not thousands/lakhs
        if state_input_name:
            form[state_input_name] = "-1"    # Default: All States aggregate

        # ── Select state (optional) ────────────────────────────────────────
        matched_state_code  = ""
        matched_state_label = "all_states"
        state_ajax_resp     = ""

        if args.state:
            result = match_option(states_map, args.state)
            if not result:
                print(f"[ERROR] State '{args.state}' not found. Run --list-options.")
                sys.exit(1)
            matched_state_code, matched_state_label = result
            print(f"Selecting state: {matched_state_label}  (code: {matched_state_code})")

            if state_input_name:
                form[state_input_name] = matched_state_code

            state_source_id = (
                state_input_name.replace("_input", "") if state_input_name else "j_idt34"
            )
            state_ajax_resp, vs = ajax_post(
                client, form, source=state_source_id,
                event="change", render="selectedRto yaxisVar",
            )
            form["javax.faces.ViewState"] = vs

            # State change can add extra Y-axis options (e.g. "Rto")
            new_yaxis = parse_options_from_xml(state_ajax_resp, "yaxisVar_input")
            if new_yaxis:
                yaxis_map.update(new_yaxis)
                refreshed = match_option(yaxis_map, args.yaxis)
                if refreshed:
                    yaxis_val, yaxis_label = refreshed
                    form["yaxisVar_input"] = yaxis_val

            time.sleep(0.4)

        # ── Set Y-axis ─────────────────────────────────────────────────────
        print(f"Setting Y-Axis: {yaxis_label}")
        form["yaxisVar_input"] = yaxis_val
        _, vs = ajax_post(client, form, source="yaxisVar", event="change")
        form["javax.faces.ViewState"] = vs
        time.sleep(0.3)

        # ── Set X-axis ─────────────────────────────────────────────────────
        print(f"Setting X-Axis: {xaxis_label}")
        form["xaxisVar_input"] = xaxis_val
        _, vs = ajax_post(client, form, source="xaxisVar", event="change")
        form["javax.faces.ViewState"] = vs
        time.sleep(0.3)

        # ── Determine RTOs to iterate ──────────────────────────────────────
        # Each entry: (selectedRto_input value, directory-name label)
        rto_list: list[tuple[str, str]] = [("-1", "all_rtos")]

        if (args.all_rtos or args.rto) and args.state:
            rto_options = parse_options_from_xml(state_ajax_resp, "selectedRto_input")
            if not rto_options:
                rto_options = parse_options(soup, "selectedRto_input")

            # Remove aggregate/placeholder entries
            rto_options = {
                v: l for v, l in rto_options.items()
                if "All Vahan4" not in l and v not in ("-1", "", "0")
            }

            if args.all_rtos:
                rto_list = list(rto_options.items())
                print(f"Found {len(rto_list)} RTOs")
            elif args.rto:
                rto_list = []
                for query in args.rto:
                    m = match_option(rto_options, query)
                    if m:
                        rto_list.append(m)
                        print(f"  RTO matched: {m[1]}  (code={m[0]})")
                    else:
                        print(f"  [WARN] RTO '{query}' not found — skipping")

        # ── Main loop: RTOs × Years ────────────────────────────────────────
        state_dir = safe_name(matched_state_label)

        for rto_code, rto_label in rto_list:
            form["selectedRto_input"] = rto_code
            if rto_code != "-1":
                print(f"\nRTO: {rto_label}  (code={rto_code})")

            combo_dir = os.path.join(
                out_dir,
                state_dir,
                safe_name(rto_label),
                f"{safe_name(yaxis_label)}__{safe_name(xaxis_label)}",
            )
            os.makedirs(combo_dir, exist_ok=True)

            for year in years:
                file_path = os.path.join(combo_dir, f"{year}.csv")
                if os.path.exists(file_path):
                    print(f"  [{year}] Skip (exists): {file_path}")
                    continue

                form["selectedYear_input"] = str(year)
                _, vs = ajax_post(client, form, source="selectedYear", event="change")
                form["javax.faces.ViewState"] = vs
                time.sleep(0.3)

                # Click Refresh — try each known button ID until we get table data
                resp_text = None
                for refresh_id in refresh_ids:
                    try:
                        rt, vs = ajax_post(client, form, source=refresh_id, execute="@all")
                        form["javax.faces.ViewState"] = vs
                        if "<th" in rt or "<td" in rt:
                            resp_text = rt
                            break
                    except Exception:
                        continue

                if not resp_text:
                    print(f"  [{year}] Refresh failed — no table in response")
                    continue

                headers, rows = parse_table(resp_text)
                if not headers:
                    print(f"  [{year}] Could not parse table headers")
                    continue

                print(f"  [{year}] {len(rows)} rows, {len(headers)} columns")

                if "ui-paginator" in resp_text:
                    headers, rows = paginate_table(client, form, resp_text)

                if not rows:
                    print(f"  [{year}] No data rows found")
                    continue

                with open(file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    writer.writerows(rows)

                print(f"  [{year}] Saved {len(rows)} rows → {file_path}")
                time.sleep(0.5)

    print("\nDone.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="api.py",
        description="Vahan Dashboard API Scraper — faster alternative, no browser required",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Output is CSV (parsed HTML table) instead of XLSX downloads.
Same --out directory layout as scraper.py — both tools can share a folder.

Examples:
  python3 scripts/api.py --list-options
  python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel"
  python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025
  python3 scripts/api.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --all-rtos --start-year 2020
  python3 scripts/api.py --yaxis "Maker" --xaxis "Fuel" --state "Kerala" --rto "TRIVANDRUM" "KOLLAM"
        """,
    )

    parser.add_argument("--yaxis", default=None,
                        help="Y-Axis variable (required unless --list-options)")
    parser.add_argument("--xaxis", default=None,
                        help="X-Axis variable (required unless --list-options)")

    year_grp = parser.add_argument_group("year selection")
    year_grp.add_argument("--year", default=None,
                          help="Single year (default: current year)")
    year_grp.add_argument("--start-year", dest="start_year", default=None,
                          help="Start of year range (inclusive). Overrides --year.")
    year_grp.add_argument("--end-year", dest="end_year", default=None,
                          help="End of year range (inclusive, default: current year)")

    loc_grp = parser.add_argument_group("location filters")
    loc_grp.add_argument("--state", default=None,
                         help="State name, partial match (e.g. 'Kerala')")
    loc_grp.add_argument("--all-rtos", action="store_true",
                         help="Loop through all RTOs for the selected state (requires --state)")
    loc_grp.add_argument("--rto", nargs="+", default=None,
                         help="One or more RTO names (requires --state)")

    parser.add_argument("--out", default="vahan_data",
                        help="Output directory (default: vahan_data/)")
    parser.add_argument("--list-options", action="store_true",
                        help="Print all available dropdown options and exit")

    args = parser.parse_args()

    if not args.list_options:
        if not args.yaxis or not args.xaxis:
            parser.error("--yaxis and --xaxis are required (use --list-options to see valid values)")
        if args.yaxis == args.xaxis:
            parser.error("--yaxis and --xaxis must be different")
        if (args.all_rtos or args.rto) and not args.state:
            parser.error("--all-rtos and --rto require --state")
        if args.year and (args.start_year or args.end_year):
            parser.error("--year cannot be combined with --start-year / --end-year")

    scrape(args)


if __name__ == "__main__":
    main()
