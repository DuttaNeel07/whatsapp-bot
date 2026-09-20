from __future__ import annotations

from features.sih_scrape import parse_count, parse_problem_statements, parse_submission


SAMPLE_HTML = """
<table id="dataTablePS">
  <tbody>
    <tr>
      <td>1</td>
      <td>Ministry of Agriculture</td>
      <td>
        <a data-target="#view1">Smart irrigation for small farms</a>
        <div id="view1">
          <table>
            <tr><td>Problem Statement ID</td><td>SIH9999</td></tr>
            <tr><td>Description</td><td>nested modal must not become a row</td></tr>
          </table>
        </div>
      </td>
      <td>Software</td>
      <td>SIH1601</td>
      <td>312/500</td>
      <td>Agriculture</td>
      <td>15-10-2026</td>
    </tr>
    <tr>
      <td>2</td>
      <td>MoHFW</td>
      <td><a data-target="#view2">Rural telemedicine kiosk</a></td>
      <td>Hardware</td>
      <td>SIH1602</td>
      <td>18/500</td>
      <td>Healthcare</td>
      <td>20-10-2026</td>
    </tr>
  </tbody>
</table>
"""


def test_parse_count_strips_noise():
    assert parse_count("312") == 312
    assert parse_count(" 1,204 ") == 1204
    assert parse_count("") == 0
    assert parse_count(None) == 0


def test_parse_submission_fraction():
    assert parse_submission("312/500") == (312, 500)
    assert parse_submission(" 225 / 500 ") == (225, 500)
    assert parse_submission("18") == (18, None)


def test_parse_problem_statements_reads_counts():
    rows = parse_problem_statements(SAMPLE_HTML)
    assert [row["ps_number"] for row in rows] == ["SIH1601", "SIH1602"]
    assert rows[0]["submitted_ideas_count"] == 312
    assert rows[0]["submitted_ideas_limit"] == 500
    assert rows[0]["title"] == "Smart irrigation for small farms"
    assert rows[0]["deadline"] == "15-10-2026"
    assert rows[1]["submitted_ideas_count"] == 18
    assert rows[1]["organization"] == "MoHFW"


def test_nested_modal_does_not_create_extra_rows():
    rows = parse_problem_statements(SAMPLE_HTML)
    assert len(rows) == 2
    assert all(row["ps_number"] != "SIH9999" for row in rows)


def test_threshold_split():
    rows = parse_problem_statements(SAMPLE_HTML)
    hot = [row for row in rows if row["submitted_ideas_count"] >= 300]
    assert [row["ps_number"] for row in hot] == ["SIH1601"]
