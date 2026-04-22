"""
Tests for scripts/api.py

Run with:  pytest tests/test_api.py -v

Coverage:
  - extract_viewstate / extract_viewstate_xml
  - parse_options / parse_options_from_xml
  - find_state_input_name / find_display_input_name / find_refresh_ids
  - match_option
  - parse_table  (including the phantom-column Vahan quirk)
  - scrape() via mocked httpx.Client  (list-options, single-year, state+year)
"""

import argparse
import csv
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Make sure the scripts/ directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import api  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

# Minimal real-shaped page HTML (mirrors live Vahan structure)
_PAGE_HTML = """
<html><body>
<form id="masterLayout_formlogin">
<input type="hidden" name="javax.faces.ViewState" value="vs_initial" />

<!-- display-type dropdown (T/L/C/A) — must NOT be mistaken for states -->
<select name="j_idt28_input">
  <option value="T">In Thousand</option>
  <option value="L">In Lakh</option>
  <option value="C">In Crore</option>
  <option value="A">Actual Value</option>
</select>

<!-- state dropdown — identified by -1 + 2-char alpha codes -->
<select name="j_idt36_input">
  <option value="-1">All Vahan4 Running States (36/36)</option>
  <option value="AN">A &amp; N Islands</option>
  <option value="AP">Andhra Pradesh</option>
  <option value="KL">Kerala</option>
  <option value="MH">Maharashtra</option>
  <option value="DL">Delhi</option>
</select>

<!-- known stable dropdowns -->
<select name="yaxisVar_input">
  <option value="VC">Vehicle Category</option>
  <option value="VCL">Vehicle Class</option>
  <option value="FUEL">Fuel</option>
  <option value="MAKER">Maker</option>
</select>
<select name="xaxisVar_input">
  <option value="FUEL">Fuel</option>
  <option value="VC">Vehicle Category</option>
  <option value="FY">Financial Year</option>
  <option value="CY">Calendar Year</option>
</select>
<select name="selectedYear_input">
  <option value="">Select Year</option>
  <option value="A">All</option>
  <option value="2023">2023</option>
  <option value="2024">2024</option>
  <option value="2025">2025</option>
</select>
<select name="selectedRto_input">
  <option value="-1">All Vahan4 Running RTOs</option>
  <option value="KL01">TRIVANDRUM RTO - KL1</option>
  <option value="KL02">KOLLAM RTO - KL2</option>
</select>

<!-- refresh buttons -->
<button id="j_idt66" type="submit">Refresh</button>
<button id="j_idt71" type="submit">Refresh</button>
<button id="j_idt78" type="submit">Refresh</button>
</form>
</body></html>
"""

# AJAX response that contains a ViewState update and a simple table (no phantom column)
_TABLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<partial-response>
<changes>
<update id="groupingTable"><![CDATA[
<table>
  <thead><tr>
    <th>S No</th><th>Vehicle Category</th><th>PETROL</th><th>DIESEL</th><th>ELECTRIC</th><th>TOTAL</th>
  </tr></thead>
  <tbody>
    <tr><td>1</td><td>2W</td><td>100</td><td>50</td><td>10</td><td>160</td></tr>
    <tr><td>2</td><td>3W</td><td>80</td><td>30</td><td>5</td><td>115</td></tr>
    <tr><td>3</td><td>4W</td><td>200</td><td>150</td><td>20</td><td>370</td></tr>
  </tbody>
</table>
]]></update>
<update id="javax.faces.ViewState"><![CDATA[vs_updated]]></update>
</changes>
</partial-response>
"""

# Table XML with the phantom-column Vahan quirk:
#   headers: [S No, Vehicle Category, Fuel, TOTAL, PETROL, DIESEL]  (6 cols)
#   td rows: [S No,  VC,              PETROL, DIESEL]               (4 cols → 5 including TOTAL injected)
# After fix: [S No, Vehicle Category, PETROL, DIESEL, TOTAL]
_PHANTOM_TABLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<partial-response>
<changes>
<update id="groupingTable"><![CDATA[
<table>
  <thead><tr>
    <th>S No</th><th>Vehicle Category</th><th>Fuel</th><th>TOTAL</th><th>PETROL</th><th>DIESEL</th>
  </tr></thead>
  <tbody>
    <tr><td>1</td><td>2W</td><td>100</td><td>50</td><td>150</td></tr>
    <tr><td>2</td><td>3W</td><td>80</td><td>30</td><td>110</td></tr>
  </tbody>
</table>
]]></update>
<update id="javax.faces.ViewState"><![CDATA[vs_phantom]]></update>
</changes>
</partial-response>
"""

# AJAX response containing updated yaxis options inside CDATA
_YAXIS_AJAX_XML = """<?xml version='1.0' encoding='UTF-8'?>
<partial-response>
<changes>
<update id="yaxisVar"><![CDATA[
<select name="yaxisVar_input">
  <option value="VC">Vehicle Category</option>
  <option value="FUEL">Fuel</option>
  <option value="RTO">Rto</option>
</select>
]]></update>
<update id="javax.faces.ViewState"><![CDATA[vs_state_change]]></update>
</changes>
</partial-response>
"""

# Empty AJAX XML (no table data)
_EMPTY_XML = """<?xml version='1.0' encoding='UTF-8'?>
<partial-response><changes>
<update id="javax.faces.ViewState"><![CDATA[vs_noop]]></update>
</changes></partial-response>
"""


# ── extract_viewstate ─────────────────────────────────────────────────────────

class TestExtractViewstate(unittest.TestCase):

    def test_extracts_from_page_html(self):
        assert api.extract_viewstate(_PAGE_HTML) == "vs_initial"

    def test_extracts_value_before_name(self):
        html = '<input value="vs_reversed" name="javax.faces.ViewState" />'
        assert api.extract_viewstate(html) == "vs_reversed"

    def test_raises_when_missing(self):
        with self.assertRaises(ValueError):
            api.extract_viewstate("<html>no viewstate here</html>")


class TestExtractViewstateXml(unittest.TestCase):

    def test_extracts_from_table_xml(self):
        assert api.extract_viewstate_xml(_TABLE_XML) == "vs_updated"

    def test_returns_empty_string_when_missing(self):
        assert api.extract_viewstate_xml("<partial-response></partial-response>") == ""


# ── parse_options ─────────────────────────────────────────────────────────────

class TestParseOptions(unittest.TestCase):

    def setUp(self):
        from bs4 import BeautifulSoup
        self.soup = BeautifulSoup(_PAGE_HTML, "html.parser")

    def test_parses_yaxis_options(self):
        opts = api.parse_options(self.soup, "yaxisVar_input")
        assert opts == {
            "VC": "Vehicle Category",
            "VCL": "Vehicle Class",
            "FUEL": "Fuel",
            "MAKER": "Maker",
        }

    def test_excludes_empty_value_options(self):
        opts = api.parse_options(self.soup, "selectedYear_input")
        # "" and "A" have values but "" should be excluded by o.get("value","") check
        assert "" not in opts

    def test_returns_empty_dict_for_unknown_select(self):
        opts = api.parse_options(self.soup, "nonexistent_select")
        assert opts == {}


class TestParseOptionsFromXml(unittest.TestCase):

    def test_extracts_yaxis_from_cdata(self):
        opts = api.parse_options_from_xml(_YAXIS_AJAX_XML, "yaxisVar_input")
        assert "VC" in opts
        assert "RTO" in opts

    def test_returns_empty_when_select_not_in_cdata(self):
        opts = api.parse_options_from_xml(_TABLE_XML, "yaxisVar_input")
        assert opts == {}

    def test_returns_empty_on_empty_xml(self):
        opts = api.parse_options_from_xml(_EMPTY_XML, "yaxisVar_input")
        assert opts == {}


# ── find_state_input_name ─────────────────────────────────────────────────────

class TestFindStateInputName(unittest.TestCase):

    def setUp(self):
        from bs4 import BeautifulSoup
        self.soup = BeautifulSoup(_PAGE_HTML, "html.parser")

    def test_finds_state_select_not_display(self):
        name = api.find_state_input_name(self.soup)
        assert name == "j_idt36_input"

    def test_does_not_return_display_type_select(self):
        name = api.find_state_input_name(self.soup)
        assert name != "j_idt28_input"

    def test_falls_back_when_no_discriminating_select(self):
        from bs4 import BeautifulSoup
        # Soup with only known stable selects, no state/display selects
        html = """<html><body>
        <select name="yaxisVar_input"><option value="VC">Vehicle Category</option></select>
        <select name="j_idt36_input"><option value="-1">All</option><option value="KL">Kerala</option></select>
        </body></html>"""
        soup = BeautifulSoup(html, "html.parser")
        name = api.find_state_input_name(soup)
        assert name == "j_idt36_input"

    def test_returns_none_when_no_state_select_and_no_fallback(self):
        from bs4 import BeautifulSoup
        html = "<html><body><select name='yaxisVar_input'><option value='VC'>VC</option></select></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        result = api.find_state_input_name(soup)
        assert result is None


# ── find_display_input_name ───────────────────────────────────────────────────

class TestFindDisplayInputName(unittest.TestCase):

    def setUp(self):
        from bs4 import BeautifulSoup
        self.soup = BeautifulSoup(_PAGE_HTML, "html.parser")

    def test_finds_display_type_select(self):
        state_name = api.find_state_input_name(self.soup)
        display_name = api.find_display_input_name(self.soup, state_name)
        assert display_name == "j_idt28_input"

    def test_does_not_return_state_select(self):
        state_name = api.find_state_input_name(self.soup)
        display_name = api.find_display_input_name(self.soup, state_name)
        assert display_name != state_name

    def test_returns_none_or_fallback_when_not_present(self):
        from bs4 import BeautifulSoup
        html = "<html><body><select name='yaxisVar_input'><option value='VC'>VC</option></select></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        result = api.find_display_input_name(soup, None)
        # Should either be None or a fallback; crucially not a known stable name
        assert result not in api._KNOWN_INPUT_NAMES


# ── find_refresh_ids ──────────────────────────────────────────────────────────

class TestFindRefreshIds(unittest.TestCase):

    def setUp(self):
        from bs4 import BeautifulSoup
        self.soup = BeautifulSoup(_PAGE_HTML, "html.parser")

    def test_finds_three_refresh_buttons(self):
        ids = api.find_refresh_ids(self.soup)
        assert set(ids) == {"j_idt66", "j_idt71", "j_idt78"}

    def test_falls_back_when_no_refresh_buttons(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        ids = api.find_refresh_ids(soup)
        assert ids == api._REFRESH_IDS_FALLBACK

    def test_case_insensitive_button_text(self):
        from bs4 import BeautifulSoup
        html = '<html><body><button id="btn1">REFRESH</button><button id="btn2">refresh</button></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        ids = api.find_refresh_ids(soup)
        assert "btn1" in ids
        assert "btn2" in ids


# ── match_option ──────────────────────────────────────────────────────────────

class TestMatchOption(unittest.TestCase):

    def setUp(self):
        self.opts = {
            "VC": "Vehicle Category",
            "FUEL": "Fuel",
            "MAKER": "Maker",
            "KL": "Kerala",
            "MH": "Maharashtra",
        }

    def test_exact_match_case_insensitive(self):
        result = api.match_option(self.opts, "fuel")
        assert result == ("FUEL", "Fuel")

    def test_exact_match_same_case(self):
        result = api.match_option(self.opts, "Fuel")
        assert result == ("FUEL", "Fuel")

    def test_partial_match(self):
        result = api.match_option(self.opts, "vehicle")
        assert result == ("VC", "Vehicle Category")

    def test_partial_state_match(self):
        result = api.match_option(self.opts, "Keral")
        assert result == ("KL", "Kerala")

    def test_no_match_returns_none(self):
        result = api.match_option(self.opts, "Nonexistent State XYZ")
        assert result is None

    def test_exact_match_preferred_over_partial(self):
        opts = {"A": "Fuel", "B": "Fuel Type"}
        result = api.match_option(opts, "Fuel")
        assert result == ("A", "Fuel")


# ── parse_table ───────────────────────────────────────────────────────────────

class TestParseTable(unittest.TestCase):

    def test_normal_table_parsed_correctly(self):
        headers, rows = api.parse_table(_TABLE_XML)
        assert headers == ["S No", "Vehicle Category", "PETROL", "DIESEL", "ELECTRIC", "TOTAL"]
        assert len(rows) == 3
        assert rows[0] == ["1", "2W", "100", "50", "10", "160"]
        assert rows[2] == ["3", "4W", "200", "150", "20", "370"]

    def test_phantom_column_corrected(self):
        # Raw headers: [S No, Vehicle Category, Fuel, TOTAL, PETROL, DIESEL] (6)
        # Raw td rows: [S No, VC, col1, col2, col3] (5 each)
        # After fix:   [S No, Vehicle Category, PETROL, DIESEL, TOTAL]
        headers, rows = api.parse_table(_PHANTOM_TABLE_XML)
        assert len(headers) == len(rows[0]), "headers and rows must have same column count after fix"
        assert headers[-1] == "TOTAL", "TOTAL should be moved to last position"
        assert "Fuel" not in headers, "phantom axis-label column should be dropped"
        assert headers == ["S No", "Vehicle Category", "PETROL", "DIESEL", "TOTAL"]
        assert len(rows) == 2

    def test_empty_response_returns_empty(self):
        headers, rows = api.parse_table(_EMPTY_XML)
        assert headers == []
        assert rows == []

    def test_viewstate_only_response_returns_empty(self):
        headers, rows = api.parse_table(_EMPTY_XML)
        assert headers == []
        assert rows == []

    def test_row_count_correct(self):
        headers, rows = api.parse_table(_TABLE_XML)
        assert len(rows) == 3


# ── scrape() integration tests with mocked httpx ─────────────────────────────

def _make_mock_response(text: str, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.status_code = status
    r.raise_for_status = MagicMock()
    return r


def _make_mock_client(get_text: str, post_responses: list[str]) -> MagicMock:
    """
    Returns a mock that behaves as an httpx.Client context manager.
    get() returns get_text; subsequent post() calls return post_responses in order.
    """
    mock_client = MagicMock()

    get_resp = _make_mock_response(get_text)
    mock_client.get.return_value = get_resp

    post_resps = [_make_mock_response(t) for t in post_responses]
    mock_client.post.side_effect = post_resps

    # Context-manager protocol
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    return mock_client


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "yaxis": None,
        "xaxis": None,
        "year": None,
        "start_year": None,
        "end_year": None,
        "state": None,
        "all_rtos": False,
        "rto": None,
        "out": None,  # overridden per test
        "list_options": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestScrapeListOptions(unittest.TestCase):

    def test_list_options_prints_and_returns(self, capsys=None):
        import tempfile
        mock_client = _make_mock_client(_PAGE_HTML, [])
        args = _args(list_options=True, out=tempfile.mkdtemp())

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with patch("httpx.Client", return_value=mock_client):
            with redirect_stdout(buf):
                api.scrape(args)

        output = buf.getvalue()
        assert "Kerala" in output
        assert "Vehicle Category" in output
        assert "Fuel" in output
        # No POST calls — list-options should only do a GET
        mock_client.post.assert_not_called()


class TestScrapeNoState(unittest.TestCase):
    """Single year, no state, no RTO — the minimal happy path."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_single_year_produces_csv(self):
        # POST sequence: yaxis change, xaxis change, year change, refresh (4 POSTs)
        post_responses = [
            _EMPTY_XML,   # yaxis change
            _EMPTY_XML,   # xaxis change
            _EMPTY_XML,   # year change
            _TABLE_XML,   # refresh → table
        ]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(yaxis="Vehicle Category", xaxis="Fuel", year="2024", out=self.tmp)

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        # Expect 4 POST calls
        assert mock_client.post.call_count == 4

        # CSV should be written under all_states/all_rtos/Vehicle_Category__Fuel/2024.csv
        csv_files = list(Path(self.tmp).rglob("*.csv"))
        assert len(csv_files) == 1
        csv_path = csv_files[0]
        assert csv_path.name == "2024.csv"

    def test_csv_content_matches_table(self):
        post_responses = [_EMPTY_XML, _EMPTY_XML, _EMPTY_XML, _TABLE_XML]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(yaxis="Vehicle Category", xaxis="Fuel", year="2024", out=self.tmp)

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        csv_files = list(Path(self.tmp).rglob("*.csv"))
        assert csv_files, "expected a CSV file to be written"
        with open(csv_files[0], newline="") as f:
            reader = csv.reader(f)
            header_row = next(reader)
            data_rows = list(reader)

        assert header_row == ["S No", "Vehicle Category", "PETROL", "DIESEL", "ELECTRIC", "TOTAL"]
        assert len(data_rows) == 3
        assert data_rows[0] == ["1", "2W", "100", "50", "10", "160"]

    def test_skips_existing_csv(self):
        """If the CSV already exists, no POSTs should be made for that year."""
        post_responses = [_EMPTY_XML, _EMPTY_XML]  # yaxis + xaxis only
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(yaxis="Vehicle Category", xaxis="Fuel", year="2024", out=self.tmp)

        # Pre-create the output file
        out_path = (
            Path(self.tmp) / "all_states" / "all_rtos"
            / "Vehicle_Category__Fuel" / "2024.csv"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("existing content")

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        # File should still contain original content (not overwritten)
        assert out_path.read_text() == "existing content"


class TestScrapeWithState(unittest.TestCase):
    """Scrape with a state selection — adds a state-change POST before y/x/year/refresh."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_state_selection_makes_extra_post(self):
        # POST sequence: state change (5th at end), yaxis, xaxis, year change, refresh
        post_responses = [
            _YAXIS_AJAX_XML,  # state change → new yaxis options
            _EMPTY_XML,        # yaxis change
            _EMPTY_XML,        # xaxis change
            _EMPTY_XML,        # year change
            _TABLE_XML,        # refresh → table
        ]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(
            yaxis="Vehicle Category",
            xaxis="Fuel",
            year="2024",
            state="Kerala",
            out=self.tmp,
        )

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        # 5 total POSTs: state + yaxis + xaxis + year + refresh
        assert mock_client.post.call_count == 5

    def test_state_dir_in_output_path(self):
        post_responses = [_YAXIS_AJAX_XML, _EMPTY_XML, _EMPTY_XML, _EMPTY_XML, _TABLE_XML]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(
            yaxis="Vehicle Category",
            xaxis="Fuel",
            year="2024",
            state="Kerala",
            out=self.tmp,
        )

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        csv_files = list(Path(self.tmp).rglob("*.csv"))
        assert len(csv_files) == 1
        # Path should contain "Kerala" somewhere
        assert "Kerala" in str(csv_files[0])

    def test_invalid_state_exits(self):
        mock_client = _make_mock_client(_PAGE_HTML, [])
        args = _args(
            yaxis="Vehicle Category",
            xaxis="Fuel",
            year="2024",
            state="Nonexistent Province XYZ",
            out=self.tmp,
        )

        with patch("httpx.Client", return_value=mock_client):
            with self.assertRaises(SystemExit):
                api.scrape(args)


class TestScrapeYearRange(unittest.TestCase):
    """--start-year / --end-year should iterate all years in range."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_year_range_produces_multiple_csvs(self):
        # yaxis + xaxis posts, then per year: year_change + refresh
        # 2023 and 2024 → 2 + (2 * 2) = 6 POSTs
        post_responses = [
            _EMPTY_XML,  # yaxis
            _EMPTY_XML,  # xaxis
            _EMPTY_XML,  # year 2023 change
            _TABLE_XML,  # year 2023 refresh
            _EMPTY_XML,  # year 2024 change
            _TABLE_XML,  # year 2024 refresh
        ]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(
            yaxis="Vehicle Category",
            xaxis="Fuel",
            start_year="2023",
            end_year="2024",
            out=self.tmp,
        )

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        csv_files = sorted(Path(self.tmp).rglob("*.csv"), key=lambda p: p.name)
        names = [f.name for f in csv_files]
        assert "2023.csv" in names
        assert "2024.csv" in names

    def test_refresh_failure_skips_year(self):
        """If refresh returns no table, year is skipped but scrape continues."""
        # scrape() tries each refresh ID in order until it gets table data.
        # _REFRESH_IDS_FALLBACK has 6 entries; the live page has 3 discovered.
        # With _PAGE_HTML, find_refresh_ids returns 3 IDs → 3 empty responses per failed year.
        post_responses = [
            _EMPTY_XML,   # yaxis
            _EMPTY_XML,   # xaxis
            _EMPTY_XML,   # year 2023 change
            _EMPTY_XML,   # year 2023 refresh id1 — no table
            _EMPTY_XML,   # year 2023 refresh id2 — no table
            _EMPTY_XML,   # year 2023 refresh id3 — no table, year skipped
            _EMPTY_XML,   # year 2024 change
            _TABLE_XML,   # year 2024 refresh id1 — has table, breaks
        ]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(
            yaxis="Vehicle Category",
            xaxis="Fuel",
            start_year="2023",
            end_year="2024",
            out=self.tmp,
        )

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        csv_files = list(Path(self.tmp).rglob("*.csv"))
        names = [f.name for f in csv_files]
        assert "2024.csv" in names
        assert "2023.csv" not in names


class TestScrapePhantomColumn(unittest.TestCase):
    """Phantom column in Refresh response must result in a valid CSV."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def test_phantom_column_csv_headers_consistent(self):
        post_responses = [_EMPTY_XML, _EMPTY_XML, _EMPTY_XML, _PHANTOM_TABLE_XML]
        mock_client = _make_mock_client(_PAGE_HTML, post_responses)
        args = _args(yaxis="Vehicle Category", xaxis="Fuel", year="2024", out=self.tmp)

        with patch("httpx.Client", return_value=mock_client):
            api.scrape(args)

        csv_files = list(Path(self.tmp).rglob("*.csv"))
        assert csv_files
        with open(csv_files[0], newline="") as f:
            reader = csv.reader(f)
            header_row = next(reader)
            data_rows = list(reader)

        assert len(header_row) == len(data_rows[0]), (
            f"header cols ({len(header_row)}) != data cols ({len(data_rows[0])})"
        )
        assert header_row[-1] == "TOTAL"


# ── safe_name ─────────────────────────────────────────────────────────────────

class TestSafeName(unittest.TestCase):

    def test_replaces_spaces(self):
        assert " " not in api.safe_name("Vehicle Category")

    def test_replaces_special_chars(self):
        result = api.safe_name("Kerala (87)")
        assert "(" not in result
        assert ")" not in result

    def test_keeps_alphanumeric_and_dash_dot(self):
        assert api.safe_name("KL-1.csv") == "KL-1.csv"

    def test_strips_leading_trailing_underscores(self):
        result = api.safe_name("  test  ")
        assert not result.startswith("_")
        assert not result.endswith("_")


if __name__ == "__main__":
    unittest.main()
