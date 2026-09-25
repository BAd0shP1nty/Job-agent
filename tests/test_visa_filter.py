import pytest

from screening.location_filter import screen_location
from screening.visa_filter import find_relocation, find_remote_from_india, find_visa_sponsorship

URL = "https://example.com/job/1"


def screen(location, description, hint=None, remote_field=None):
    return screen_location(title="SRE Lead", location=location, country_hint=None, description=description,
                           work_arrangement_hint=hint, remote_location_field=remote_field, source_url=URL)


def test_overseas_without_any_evidence_is_rejected():
    d = screen("London, United Kingdom", "Hybrid, three days a week in the London office.")
    assert d.status == "ineligible"
    assert d.remote_eligibility is d.relocation_evidence is d.visa_sponsorship_evidence is None


def test_verified_visa_sponsorship_passes_with_evidence_fields():
    d = screen("Berlin, Germany", "We offer visa sponsorship for international hires.")
    assert d.status == "eligible"
    assert d.visa_sponsorship_evidence == "We offer visa sponsorship for international hires."
    assert d.evidence_source_url == URL


def test_verified_relocation_passes():
    d = screen("Dublin, Ireland", "Relocation assistance is provided for successful candidates.")
    assert d.status == "eligible" and d.relocation_evidence


def test_fully_remote_without_india_eligibility_is_not_eligible():
    d = screen("Remote", "This is a fully remote role.", hint="remote")
    assert d.status != "eligible"
    d = screen("Remote - EMEA", "Fully remote. Candidates must be based in the EU.", hint="remote")
    assert d.status == "ineligible"


def test_remote_explicitly_open_to_india_passes():
    d = screen("Remote", "Fully remote; candidates based in India are welcome to apply.", hint="remote")
    assert d.status == "eligible" and "India" in d.remote_eligibility


def test_structured_worldwide_field_counts_as_explicit():
    d = screen("Remote", "Join us.", hint="remote", remote_field="Worldwide")
    assert d.status == "eligible"


def test_hybrid_with_remote_days_is_not_remote_eligibility():
    d = screen("Amsterdam, Netherlands", "Hybrid role. You can work from anywhere two days a week.")
    assert d.status != "eligible"


@pytest.mark.parametrize("text", [
    "Visa sponsorship may be available for exceptional candidates.",
    "Sponsorship could be considered on a case-by-case basis.",
])
def test_hedged_sponsorship_is_ambiguous_not_verified(text):
    assert find_visa_sponsorship(text).status == "ambiguous"
    d = screen("Amsterdam, Netherlands", text)
    assert d.status == "requires_verification" and d.visa_sponsorship_evidence is None


@pytest.mark.parametrize("text", [
    "We are unable to offer visa sponsorship.",
    "Visa sponsorship is not available for this role.",
    "Candidates must already have the right to work in the UK.",
    "We do not sponsor visas.",
])
def test_negative_sponsorship(text):
    assert find_visa_sponsorship(text).status == "negative"


def test_contradictory_statements_are_ambiguous():
    f = find_visa_sponsorship("We offer visa sponsorship. We cannot sponsor visas for contractors.")
    assert f.status == "ambiguous"


@pytest.mark.parametrize("text,status", [
    ("We provide a relocation package.", "verified"),
    ("No relocation is offered.", "negative"),
    ("Relocation support may be possible.", "ambiguous"),
    ("Great team culture.", "absent"),
])
def test_relocation_classification(text, status):
    assert find_relocation(text).status == status


def test_remote_negated_for_india():
    assert find_remote_from_india("Remote role, not open to candidates in India.").status == "negative"


def test_remote_worldwide_with_residency_restriction_is_ambiguous():
    f = find_remote_from_india("Work from anywhere in the world! Candidates must be based in the US.")
    assert f.status == "ambiguous"


def test_missing_evidence_never_upgraded():
    d = screen("Paris, France", "Exciting role in Paris. Competitive salary.")
    assert d.status == "ineligible"
    assert d.reason.startswith("Overseas role without explicit")
