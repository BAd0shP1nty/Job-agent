import pytest

from config.skills_config import SkillsConfigError, load_skills, merge_skills, save_skills
from screening.skill_filter import match_skills, missing_skills, passes_skill_filter, required_skills_in_listing


def test_list_py_loads_default_skills():
    skills = load_skills()
    assert "ITSM" in skills and "Agentic AI" in skills and len(skills) == 11


def test_list_py_round_trip_and_dedup(skills_file):
    saved = save_skills(["ITIL", "itil", " SRE ", "Kubernetes"], skills_file)
    assert saved == ["ITIL", "SRE", "Kubernetes"]
    assert load_skills(skills_file) == saved
    assert skills_file.read_text().count("SKILLS = {") == 1


@pytest.mark.parametrize("content,err", [
    ('SKILLS = {"skills": ["ITIL",]}', "not valid JSON"),
    ("SKILLS = {'skills': ['ITIL']}", "not valid JSON"),
    ('SKILLS = {"items": []}', '"skills" array'),
    ('SKILLS = {"skills": [1, 2]}', "must be a string"),
    ("NOTHING = 1", "SKILLS = {...}"),
])
def test_invalid_list_py_gives_clear_error(skills_file, content, err):
    skills_file.write_text(content)
    with pytest.raises(SkillsConfigError, match=err):
        load_skills(skills_file)


def test_confirmed_skill_matches_have_evidence():
    text = "You will run ITIL processes. Experience with Generative AI tools such as GenAI copilots is a plus."
    matches = match_skills(["ITIL", "Generative AI", "SRE"], text, "https://example.com/j")
    assert {m.skill for m in matches} == {"ITIL", "Generative AI"}
    itil = next(m for m in matches if m.skill == "ITIL")
    assert itil.evidence.text == "You will run ITIL processes."
    assert text[itil.evidence.start:itil.evidence.end] == itil.evidence.text


def test_vague_associations_are_not_matches():
    text = "Work on our cloud platform and keep services safe and reliable. Mumbai office."
    assert match_skills(["AWS", "SAFe", "SRE", "AI", "Machine Learning"], text) == []


def test_acronyms_are_case_sensitive_and_word_bounded():
    assert match_skills(["AI"], "Build AI assistants")[0].skill == "AI"
    assert match_skills(["AI"], "We are based in Mumbai and Dubai") == []
    assert match_skills(["ML"], "html and xml parsing") == []


def test_aliases_are_exact_synonyms():
    assert match_skills(["SRE"], "Site Reliability Engineering team")[0].matched_term == "site reliability engineering"
    assert match_skills(["ITSM"], "IT Service Management lead")[0].skill == "ITSM"


def test_mandatory_skill_condition():
    assert not passes_skill_filter(match_skills(["ITIL"], "Java developer, Spring Boot"))
    assert passes_skill_filter(match_skills(["ITIL"], "ITIL v4 processes"))


def test_missing_requirements():
    required = required_skills_in_listing("Must know Kubernetes, ITIL and ServiceNow.")
    assert set(required) >= {"Kubernetes", "ITIL", "ServiceNow"}
    assert missing_skills(required, ["ITIL", "ServiceNow"]) == ["Kubernetes"]


def test_merge_skills_preserves_first_casing():
    assert merge_skills(["ServiceNow", "ITIL"], ["itil", "SRE"]) == ["ServiceNow", "ITIL", "SRE"]


def test_minimum_skill_matches_setting():
    text = "Agile team building AI tools with ITIL processes."
    matches = match_skills(["AI", "ITIL", "Kubernetes"], text)
    assert passes_skill_filter(matches, 2)
    assert not passes_skill_filter(matches, 3)
    assert passes_skill_filter(matches, 0)  # never below the mandatory minimum of 1
    assert not passes_skill_filter([], 0)
