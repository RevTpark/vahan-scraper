#!/usr/bin/env python3
"""
Vahan Dashboard Scraper
=======================
Scrapes vehicle registration data from vahan.parivahan.gov.in.

Usage examples:
  # List all available dropdown options
  python3 scraper.py --list-options

  # Scrape Vehicle Category vs Fuel for all states, current year
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel"

  # Scrape for a specific state, specific year
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025

  # Scrape all RTOs in a state across a year range
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --all-rtos --start-year 2020 --end-year 2026

  # Scrape specific RTOs
  python3 scraper.py --yaxis "Maker" --xaxis "Fuel" --state "Kerala" --rto "TRIVANDRUM RTO - KL1" "KOLLAM RTO - KL2"

  # Debug: show browser window
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Delhi" --no-headless
"""

import argparse
import os
import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

VAHAN_URL = "https://vahan.parivahan.gov.in/vahan4dashboard/vahan/view/reportview.xhtml"

YAXIS_OPTIONS = [
    "Vehicle Category",
    "Vehicle Class",
    "Norms",
    "Fuel",
    "Maker",
    "State",
]

XAXIS_OPTIONS = [
    "Vehicle Category",
    "Norms",
    "Fuel",
    "Vehicle Category Group",
    "Financial Year",
    "Calendar Year",
    "Month Wise",
]

# IDs that are NOT the state dropdown (they have stable IDs on the Vahan page)
_KNOWN_DROPDOWN_IDS = {"selectedRto", "yaxisVar", "xaxisVar", "selectedYear"}


# ── Utility ───────────────────────────────────────────────────────────────────

def safe_name(text: str) -> str:
    """Convert arbitrary text to a filesystem-safe string."""
    return "".join(c if c.isalnum() or c in "-." else "_" for c in text).strip("_")


# ── Dropdown helpers ──────────────────────────────────────────────────────────

def find_state_dropdown_id(page) -> str | None:
    """
    Find the state dropdown wrapper div ID.

    Strategy: the state dropdown's label always reads something like
    "All Vahan4 Running States (36/36)" on page load. We check the
    label#{id}_label text for every unknown dropdown and pick the one
    that contains "state". This avoids false-positives from other
    unknown dropdowns (e.g. the Type dropdown: "In Thousand / In Lakh…").

    Falls back to a list of IDs observed across different page versions.
    """
    for el in page.query_selector_all("div.ui-selectonemenu"):
        eid = el.get_attribute("id")
        if not eid or eid in _KNOWN_DROPDOWN_IDS:
            continue
        label = page.query_selector(f"label#{eid}_label")
        if label and "state" in label.inner_text().lower():
            return eid

    # Hard-coded fallbacks observed across page versions
    for fallback in ["j_idt34", "j_idt41", "j_idt45"]:
        if page.locator(f"#{fallback}").count() > 0:
            return fallback

    return None


def select_dropdown(page, label_selector: str, item_text: str, timeout: int = 8000) -> bool:
    """
    Click a PrimeFaces dropdown label and select an item.
    Tries exact match first, then partial match.
    """
    try:
        page.wait_for_selector(label_selector, timeout=timeout)
    except Exception:
        print(f"  [WARN] Dropdown label not found: {label_selector}")
        return False

    page.click(label_selector)
    time.sleep(0.6)

    visible = page.locator("li:visible")
    count = visible.count()

    # Exact match
    for i in range(count):
        try:
            if visible.nth(i).inner_text().strip() == item_text:
                visible.nth(i).click()
                time.sleep(0.5)
                return True
        except Exception:
            continue

    # Partial match fallback
    for i in range(count):
        try:
            if item_text.lower() in visible.nth(i).inner_text().strip().lower():
                visible.nth(i).click()
                time.sleep(0.5)
                return True
        except Exception:
            continue

    print(f"  [WARN] '{item_text}' not found in dropdown {label_selector}")
    page.keyboard.press("Escape")
    time.sleep(0.3)
    return False


def get_dropdown_options(page, label_selector: str) -> list[str]:
    """Open a dropdown and return all visible option texts."""
    try:
        page.wait_for_selector(label_selector, timeout=5000)
        page.click(label_selector)
        time.sleep(0.6)
        texts = page.locator("li:visible").all_inner_texts()
        page.keyboard.press("Escape")
        time.sleep(0.3)
        return [t.strip() for t in texts if t.strip()]
    except Exception as e:
        print(f"  [WARN] Could not read options from {label_selector}: {e}")
        return []


# ── State helpers ─────────────────────────────────────────────────────────────

def select_state(page, state_dropdown_id: str, state_query: str) -> str | None:
    """
    Open the state dropdown and select the state whose label contains
    state_query (case-insensitive partial match).
    Returns the full matched label (e.g. "Kerala(87)"), or None on failure.
    """
    page.click(f"#{state_dropdown_id}")

    # Wait for the panel's items to appear
    items_selector = f"ul#{state_dropdown_id}_items li"
    try:
        page.wait_for_selector(items_selector, timeout=6000)
    except Exception:
        # Fallback: any li with a data-label attribute
        try:
            page.wait_for_selector("li[data-label]", timeout=4000)
        except Exception:
            print("  [ERROR] State dropdown items did not appear.")
            return None

    items = page.query_selector_all(items_selector) or page.query_selector_all("li[data-label]")

    matched = None
    for item in items:
        label = item.get_attribute("data-label") or item.inner_text().strip()
        if state_query.lower() in label.lower():
            matched = label
            item.click()
            time.sleep(0.4)
            break

    if not matched:
        print(f"  [ERROR] State '{state_query}' not found in dropdown.")
        page.keyboard.press("Escape")

    return matched


def list_states(page, state_dropdown_id: str) -> list[str]:
    """Return all state labels from the state dropdown."""
    page.click(f"#{state_dropdown_id}")
    items_selector = f"ul#{state_dropdown_id}_items li"
    try:
        page.wait_for_selector(items_selector, timeout=6000)
    except Exception:
        page.keyboard.press("Escape")
        return []

    items = page.query_selector_all(items_selector)
    labels = [
        (item.get_attribute("data-label") or item.inner_text()).strip()
        for item in items
        if (item.get_attribute("data-label") or item.inner_text()).strip()
    ]
    page.keyboard.press("Escape")
    time.sleep(0.4)
    return labels


# ── RTO helpers ───────────────────────────────────────────────────────────────

def get_all_rtos(page) -> list[str]:
    """
    Open the RTO dropdown and return every RTO name except
    the 'All Vahan4' aggregate entries.
    """
    page.click("#selectedRto")
    try:
        page.wait_for_selector("ul[id*='selectedRto_items'] li", timeout=10000)
    except Exception:
        print("  [WARN] RTO dropdown items not found.")
        page.keyboard.press("Escape")
        return []

    rto_elements = page.query_selector_all("ul[id*='selectedRto_items'] li")
    rtos = [
        el.inner_text().strip()
        for el in rto_elements
        if el.inner_text().strip() and "All Vahan4" not in el.inner_text()
    ]

    page.keyboard.press("Escape")
    time.sleep(0.3)
    return rtos


def select_rto(page, rto_label: str) -> bool:
    """Open the RTO dropdown and click the item matching rto_label."""
    page.click("#selectedRto")
    try:
        page.wait_for_selector("ul[id*='selectedRto_items'] li", timeout=10000)
    except Exception:
        print(f"  [WARN] RTO dropdown didn't open for: {rto_label}")
        return False

    try:
        # Use double-quoted attribute value to avoid apostrophe issues
        page.locator(f'li[data-label="{rto_label}"]').first.click()
        time.sleep(0.5)
        return True
    except Exception:
        # Fallback: text-based matching
        locator = page.locator("li:visible").filter(has_text=rto_label)
        if locator.count() > 0:
            locator.first.click()
            time.sleep(0.5)
            return True
        print(f"  [WARN] RTO '{rto_label}' not found in dropdown.")
        page.keyboard.press("Escape")
        return False


# ── Refresh & Download ────────────────────────────────────────────────────────

def click_refresh(page) -> bool:
    """Click the Refresh button, trying multiple selectors."""
    for sel in ["button#j_idt65", "button#j_idt72", "button:has-text('Refresh')"]:
        if page.locator(sel).count() > 0:
            page.click(sel)
            return True
    print("  [WARN] Refresh button not found.")
    return False


def download_xlsx(page, file_path: str, timeout: int = 25000) -> bool:
    """Download the Excel file visible on the page and save to file_path."""
    for sel in ["a[id='groupingTable:xls']", "a[id='vchgroupTable:xls']"]:
        if page.locator(sel).count() > 0:
            try:
                with page.expect_download(timeout=timeout) as dl:
                    page.click(sel)
                dl.value.save_as(file_path)
                return True
            except Exception as e:
                print(f"  [ERROR] Download failed: {e}")
                return False
    print("  [WARN] Excel download button not found on page.")
    return False


# ── Core scrape logic ─────────────────────────────────────────────────────────

def scrape(args):
    current_year = datetime.now().year

    # Resolve years to scrape
    if args.start_year is not None:
        years = list(range(int(args.start_year), int(args.end_year or current_year) + 1))
    elif args.year is not None:
        years = [int(args.year)]
    else:
        years = [current_year]

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.no_headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        print("Loading Vahan dashboard…")
        page.goto(VAHAN_URL)
        page.wait_for_timeout(5000)

        # ── List-options mode ──────────────────────────────────────────────
        if args.list_options:
            state_id = find_state_dropdown_id(page)
            print("\n=== Vahan Dashboard Dropdown Options ===")

            if state_id:
                states = list_states(page, state_id)
                print(f"\nStates ({len(states)}):")
                for s in states:
                    print(f"  {s}")

            for label, name in [
                ("label#yaxisVar_label", "Y-Axis"),
                ("label#xaxisVar_label", "X-Axis"),
                ("label#selectedYear_label", "Year"),
            ]:
                opts = get_dropdown_options(page, label)
                print(f"\n{name} Options:")
                for o in opts:
                    print(f"  {o}")

            browser.close()
            return

        # ── Locate state dropdown ──────────────────────────────────────────
        state_dropdown_id = find_state_dropdown_id(page)
        if not state_dropdown_id:
            print("[ERROR] Could not locate state dropdown on page.")
            browser.close()
            sys.exit(1)

        # ── Select state (optional) ────────────────────────────────────────
        matched_state = None
        if args.state:
            print(f"Selecting state: {args.state}")
            matched_state = select_state(page, state_dropdown_id, args.state)
            if not matched_state:
                browser.close()
                sys.exit(1)
            print(f"  → Matched: {matched_state}")
            print("  Waiting for RTO list to load…")
            time.sleep(5)

        # ── Determine RTOs to iterate ──────────────────────────────────────
        # None means "no RTO selected" (uses the state/all-states aggregate)
        if args.all_rtos and args.state:
            print("Fetching all RTOs for selected state…")
            rto_list = get_all_rtos(page)
            print(f"  Found {len(rto_list)} RTOs")
        elif args.rto:
            rto_list = args.rto
        else:
            rto_list = [None]

        # ── Set Y-Axis and X-Axis ──────────────────────────────────────────
        print(f"Setting Y-Axis: {args.yaxis}")
        select_dropdown(page, "label#yaxisVar_label", args.yaxis)

        print(f"Setting X-Axis: {args.xaxis}")
        select_dropdown(page, "label#xaxisVar_label", args.xaxis)

        # ── Main loop: RTOs × Years ────────────────────────────────────────
        state_dir = safe_name(matched_state) if matched_state else "all_states"

        for rto in rto_list:
            if rto is not None:
                print(f"\nRTO: {rto}")
                if not select_rto(page, rto):
                    print(f"  Skipping RTO: {rto}")
                    continue
                time.sleep(2)

                # Re-apply axes after RTO selection (they can reset on state/RTO change)
                select_dropdown(page, "label#yaxisVar_label", args.yaxis)
                select_dropdown(page, "label#xaxisVar_label", args.xaxis)

            rto_dir = safe_name(rto) if rto else "all_rtos"
            combo_dir = os.path.join(
                out_dir,
                state_dir,
                rto_dir,
                f"{safe_name(args.yaxis)}__{safe_name(args.xaxis)}",
            )
            os.makedirs(combo_dir, exist_ok=True)

            for year in years:
                file_path = os.path.join(combo_dir, f"{year}.xlsx")

                if os.path.exists(file_path):
                    print(f"  [{year}] Skip (already exists): {file_path}")
                    continue

                select_dropdown(page, "label#selectedYear_label", str(year))

                click_refresh(page)
                time.sleep(3)

                ok = download_xlsx(page, file_path)
                if ok:
                    print(f"  [{year}] Saved → {file_path}")
                else:
                    print(f"  [{year}] Download failed — check the browser state")

                time.sleep(2)

        browser.close()
        print("\nDone.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="scraper.py",
        description="Generalized Vahan Dashboard Scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Y-Axis options : {', '.join(YAXIS_OPTIONS)}
X-Axis options : {', '.join(XAXIS_OPTIONS)}

Examples:
  python3 scraper.py --list-options
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel"
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --year 2025
  python3 scraper.py --yaxis "Vehicle Category" --xaxis "Fuel" --state "Kerala" --all-rtos --start-year 2020 --end-year 2026
  python3 scraper.py --yaxis "Maker" --xaxis "Fuel" --state "Kerala" --rto "TRIVANDRUM RTO - KL1" "KOLLAM RTO - KL2"
        """,
    )

    parser.add_argument(
        "--yaxis", default=None,
        help="Y-Axis variable (required unless --list-options)",
    )
    parser.add_argument(
        "--xaxis", default=None,
        help="X-Axis variable (required unless --list-options)",
    )

    year_grp = parser.add_argument_group("year selection")
    year_grp.add_argument(
        "--year", default=None,
        help="Single year to scrape (default: current year)",
    )
    year_grp.add_argument(
        "--start-year", dest="start_year", default=None,
        help="Start of year range (inclusive). Overrides --year.",
    )
    year_grp.add_argument(
        "--end-year", dest="end_year", default=None,
        help="End of year range (inclusive, default: current year).",
    )

    loc_grp = parser.add_argument_group("location filters")
    loc_grp.add_argument(
        "--state", default=None,
        help="State name, partial match (e.g. 'Kerala'). Omit for all-states aggregate.",
    )
    loc_grp.add_argument(
        "--all-rtos", action="store_true",
        help="Loop through every RTO in the selected state.",
    )
    loc_grp.add_argument(
        "--rto", nargs="+", default=None,
        help="One or more specific RTO names to scrape (requires --state).",
    )

    parser.add_argument(
        "--out", default="vahan_data",
        help="Output directory (default: vahan_data/)",
    )
    parser.add_argument(
        "--list-options", action="store_true",
        help="Print all available dropdown options and exit.",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Show browser window (useful for debugging).",
    )

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
