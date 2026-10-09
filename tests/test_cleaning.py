from backlink_lens.cleaning import clean_table, domain_of, normalise_date, normalise_link_type, normalise_url, parse_number
from backlink_lens.columns import detect_mapping
from backlink_lens.readers import Table

HEADERS = ["Referring page URL", "Domain rating", "Referring domains", "Target URL", "Type", "First seen"]


def make(rows: list[list[str]]) -> Table:
    return Table("test.csv", HEADERS, [dict(zip(HEADERS, r, strict=True)) for r in rows])


def test_parse_number_handles_common_formats() -> None:
    assert parse_number("1,234") == 1234
    assert parse_number("1.2k") == 1200
    assert parse_number("45%") == 45
    assert parse_number("n/a") is None
    assert parse_number("—") is None
    assert parse_number("abc") is None


def test_domain_of_strips_scheme_www_and_path() -> None:
    assert domain_of("https://WWW.Example.COM/path?x=1") == "example.com"
    assert domain_of("example.com") == "example.com"
    assert domain_of("sub.example.co.uk:8080") == "sub.example.co.uk"
    assert domain_of("") == ""


def test_normalise_url_drops_tracking_and_trailing_slash() -> None:
    assert normalise_url("https://www.acme.com/page/?utm_source=x&id=2#top") == "https://acme.com/page?id=2"
    assert normalise_url("acme.com/") == "https://acme.com"


def test_normalise_date_and_link_type() -> None:
    assert normalise_date("15/03/2025") == "2025-03-15"
    assert normalise_date("03/15/2025") == "2025-03-15"
    assert normalise_date("2025-03-15T10:00:00") == "2025-03-15"
    assert normalise_date("someday") == "someday"
    assert normalise_link_type("Yes") == "dofollow"
    assert normalise_link_type("rel=nofollow") == "nofollow"
    assert normalise_link_type("UGC") == "ugc"


def test_clean_table_dedupes_keeping_the_strongest_row() -> None:
    table = make(
        [
            ["https://www.quiet.com/a", "45", "1,200", "https://acme.com/", "Dofollow", "2025-01-01"],
            ["http://quiet.com/b", "47", "1,250", "https://acme.com/", "Dofollow", "2025-01-02"],
            ["https://other.net/", "30", "300", "https://acme.com/blog", "Nofollow", "01/02/2025"],
        ]
    )
    result = clean_table(table, detect_mapping(HEADERS))
    assert [r.referring_domain for r in result.rows] == ["quiet.com", "other.net"]
    assert result.rows[0].domain_rating == 47
    assert result.rows[0].referring_domains == 1250
    assert result.duplicates == 1
    assert result.competitor == "acme.com"
    dropped = [c for c in result.changes if c.field == "row"]
    assert dropped and dropped[0].row == 1 and "duplicate" in dropped[0].reason


def test_clean_table_logs_every_normalisation() -> None:
    table = make([["https://www.quiet.com/a", "45.0", "1,200", "https://acme.com/?utm_source=t", "Yes", "15/03/2025"]])
    result = clean_table(table, detect_mapping(HEADERS))
    fields = {c.field for c in result.changes}
    assert {"referring_page", "domain_rating", "referring_domains", "target_url", "link_type", "first_seen"} <= fields


def test_blank_rows_are_dropped_and_counted() -> None:
    table = make([["", "", "", "", "", ""], ["https://a.com/", "10", "5", "https://acme.com/", "", ""]])
    result = clean_table(table, detect_mapping(HEADERS))
    assert result.dropped_empty == 1
    assert len(result.rows) == 1
    assert result.rows[0].index == 2


def test_flags_cover_spam_self_links_and_impossible_metrics() -> None:
    table = make(
        [
            ["https://best-casino-bonus.xyz/", "3", "12", "https://acme.com/", "", ""],
            ["https://blog.acme.com/x", "50", "800", "https://acme.com/", "", ""],
            ["https://maple.org/p", "88", "4", "https://acme.com/", "", ""],
            ["https://fine.org/p", "n/a", "4", "https://acme.com/", "", ""],
            ["https://ok.org/p", "40", "400", "https://acme.com/", "nofollow", ""],
        ]
    )
    result = clean_table(table, detect_mapping(HEADERS))
    codes = {(f.row, f.code) for f in result.flags}
    assert {(1, "suspicious_tld"), (1, "spam_words"), (2, "self_link"), (3, "implausible"), (4, "missing_metrics"), (5, "not_followed")} <= codes
    assert all(f.message for f in result.flags)
