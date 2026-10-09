from backlink_lens.columns import CONFIDENT, Mapping, apply_overrides, detect_mapping, match_header, normalise_header


def test_normalise_header_strips_punctuation_and_case() -> None:
    assert normalise_header("Domain Rating (DR)") == "domain rating dr"
    assert normalise_header("  # Referring_Domains ") == "referring domains"


def test_known_headers_match_exactly() -> None:
    assert match_header("DR") == ("domain_rating", 1.0)
    assert match_header("Linking Root Domains") == ("referring_domains", 1.0)
    assert match_header("Target URL") == ("target_url", 1.0)


def test_header_with_extra_words_is_still_confident() -> None:
    name, score = match_header("Domain Rating (DR)")
    assert name == "domain_rating"
    assert score >= CONFIDENT


def test_detect_mapping_assigns_each_field_once() -> None:
    mapping = detect_mapping(["Source URL", "DA", "Linking Root Domains", "Target", "Link Text", "Spam Score"])
    assert mapping.fields["domain_rating"] == "DA"
    assert mapping.fields["referring_page"] == "Source URL"
    assert mapping.fields["target_url"] == "Target"
    assert "Spam Score" in mapping.unmapped
    assert mapping.missing_required == []


def test_missing_required_is_reported() -> None:
    mapping = detect_mapping(["Linking Site", "Trust Score", "Category"])
    assert "referring_domains" in mapping.missing_required
    assert "target_url" in mapping.missing_required


def test_user_override_moves_a_header_between_fields() -> None:
    headers = ["Page To", "Rating", "Sites Linking In"]
    mapping = detect_mapping(headers)
    mapping = apply_overrides(mapping, {"target_url": "Page To", "referring_domains": "Sites Linking In"}, headers)
    assert mapping.fields["target_url"] == "Page To"
    assert mapping.fields["referring_domains"] == "Sites Linking In"
    assert mapping.source["target_url"] == "user"
    assert "Page To" not in [h for f, h in mapping.fields.items() if f != "target_url"]


def test_override_with_empty_header_clears_the_field() -> None:
    headers = ["DR", "RD", "Target URL"]
    mapping = apply_overrides(detect_mapping(headers), {"domain_rating": ""}, headers)
    assert "domain_rating" not in mapping.fields
    assert "DR" in mapping.unmapped


def test_mapping_round_trips_through_dict() -> None:
    mapping = detect_mapping(["DR", "RD", "Target URL", "Notes"])
    again = Mapping.from_dict(mapping.to_dict())
    assert again.fields == mapping.fields
    assert again.unmapped == ["Notes"]
