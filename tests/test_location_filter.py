import pytest

from screening.location_filter import detect_country, detect_work_arrangement, screen_location


def screen(**kw):
    args = dict(title="Service Delivery Manager", location="", country_hint=None, description="",
                work_arrangement_hint=None, remote_location_field=None, source_url="https://example.com/job")
    args.update(kw)
    return screen_location(**args)


def test_bangalore_role_passes():
    d = screen(location="Bengaluru, Karnataka, India", description="Hybrid role in our Bangalore office.")
    assert d.status == "eligible" and d.country == "India" and d.work_arrangement == "hybrid"


@pytest.mark.parametrize("loc", ["Hyderabad", "Pune, Maharashtra", "Gurugram, Haryana", "Chennai, India",
                                 "Mumbai", "Noida"])
def test_other_india_cities_pass_without_bangalore_only(loc):
    assert screen(location=loc).status == "eligible"


def test_bangalore_only_filter_excludes_other_cities_but_keeps_remote_india():
    assert screen(location="Pune, India", bangalore_only=True).status == "ineligible"
    assert screen(location="Bangalore", bangalore_only=True).status == "eligible"
    assert screen(location="India", work_arrangement_hint="remote", bangalore_only=True).status == "eligible"


def test_remote_only_filter():
    assert screen(location="Bangalore", description="Office based.", remote_only=True).status == "ineligible"
    assert screen(location="Bangalore", work_arrangement_hint="remote", remote_only=True).status == "eligible"


def test_unknown_location_requires_verification():
    d = screen(location="", description="A great opportunity.")
    assert d.status == "requires_verification"


def test_outside_target_markets_rejected():
    d = screen(location="Austin, Texas, United States", description="We sponsor visas.")
    assert d.status == "ineligible" and "target markets" in d.reason


@pytest.mark.parametrize("loc,country,region", [
    ("London, UK", "United Kingdom", "emea"), ("Dublin", "Ireland", "emea"), ("München", "Germany", "emea"),
    ("Dubai, UAE", "United Arab Emirates", "emea"), ("Remote - EMEA", None, "emea"), ("Bengaluru", "India", "india"),
    ("Remote", None, "unknown"), ("Let us know", None, "unknown"),
])
def test_country_detection(loc, country, region):
    assert detect_country(loc) == (country, region)


def test_hybrid_is_not_treated_as_remote():
    assert detect_work_arrangement(None, "Engineer", "London", "Hybrid with remote days available") == "hybrid"
    assert detect_work_arrangement("remote", "Engineer", "", "") == "remote"
    assert detect_work_arrangement(None, "Engineer", "", "Nothing stated") == "unknown"
