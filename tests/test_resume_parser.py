import io
import zipfile

import pytest

from config.skills_config import merge_skills
from rag.resume_parser import ResumeValidationError, extract_profile, extract_skills, parse_resume


def test_docx_resume_is_parsed_into_sections(sample_resume_bytes):
    parsed = parse_resume("resume.docx", sample_resume_bytes)
    names = [s.name for s in parsed.sections]
    assert "Professional Summary" in names
    assert "Professional Experience" in names
    assert "Education" in names
    assert "incident management" in parsed.text


def test_profile_extraction_shows_reviewable_fields(sample_resume_bytes):
    profile = extract_profile(parse_resume("resume.docx", sample_resume_bytes))
    assert profile.name == "Jordan Sample"
    assert profile.current_location.startswith("Bangalore")
    assert profile.total_experience == "12+ years"
    assert profile.experience and profile.experience[0].title == "Service Delivery Manager"
    assert profile.experience[0].dates == "January 2018 - Present"
    assert "ITIL V3 Foundation" in profile.certifications
    assert any("Bachelor of Engineering" in e for e in profile.education)


def test_skills_are_only_those_literally_present(sample_resume_bytes):
    profile = extract_profile(parse_resume("resume.docx", sample_resume_bytes))
    for present in ["ITIL", "ITSM", "ServiceNow", "Incident Management", "RAG", "Python", "LLMs",
                    "Change Governance"]:
        assert present in profile.skills, present
    # Not in the document -> must not be introduced.
    for absent in ["Kubernetes", "Machine Learning", "SRE", "Terraform", "AWS", "Change Management"]:
        assert absent not in profile.skills, absent
    # Every skill carries a snippet that proves it.
    for skill in profile.skills:
        assert profile.skill_evidence[skill]


def test_skill_substrings_are_not_double_counted():
    skills, _ = extract_skills("Built bots in Microsoft Copilot Studio.")
    assert "Microsoft Copilot Studio" in skills
    assert "Copilot Studio" not in skills


def test_merge_with_configured_skills_has_no_duplicates(sample_resume_bytes):
    profile = extract_profile(parse_resume("resume.docx", sample_resume_bytes))
    merged = merge_skills(profile.skills, ["itil", "ITSM", "SRE", "Agentic AI"])
    lowered = [m.casefold() for m in merged]
    assert len(lowered) == len(set(lowered))
    assert "SRE" in merged and "Agentic AI" in merged


def test_plain_text_resume():
    data = "Alex Example\nSummary\nITIL practitioner with ServiceNow experience.\n".encode()
    profile = extract_profile(parse_resume("cv.txt", data))
    assert {"ITIL", "ServiceNow"} <= set(profile.skills)


def test_pdf_resume(tmp_path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Casey Example\nSKILLS\nITIL, SRE and Python\n")
    data = doc.tobytes()
    parsed = parse_resume("cv.pdf", data)
    assert parsed.sections[-1].page == 1
    assert {"ITIL", "SRE", "Python"} <= set(extract_profile(parsed).skills)


@pytest.mark.parametrize("name,data,message", [
    ("resume.exe", b"MZ....", "Unsupported file type"),
    ("resume.pdf", b"not a pdf", "valid PDF"),
    ("resume.docx", b"not a zip", "valid DOCX"),
    ("resume.docx", b"", "empty"),
    ("resume.txt", b"\xff\xfe\x00bad", "UTF-8"),
])
def test_unsafe_or_invalid_uploads_are_rejected(name, data, message):
    with pytest.raises(ResumeValidationError, match=message):
        parse_resume(name, data)


def test_macro_enabled_docx_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
        zf.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(ResumeValidationError, match="Macro"):
        parse_resume("resume.docx", buf.getvalue())


def test_oversized_upload_is_rejected():
    with pytest.raises(ResumeValidationError, match="larger than"):
        parse_resume("resume.txt", b"a" * (5 * 1024 * 1024 + 1))


def test_pdf_with_running_header_label_location_and_multiline_roles():
    fitz = pytest.importorskip("pymupdf")
    doc = fitz.open()
    pages = [
        ["Casey Example | Professional Profile", "Page 1", "Casey Example", "Service Operations | ITIL",
         "Location", "Tumkur, Karnataka, India", "Professional Summary", "ITIL and ServiceNow practitioner."],
        ["Casey Example | Professional Profile", "Page 2", "Professional Experience", "EXAMPLE TELECOM PRIVATE LIMITED",
         "Service Delivery Manager", "January 2019 - Present | Bengaluru", "• Own incident management and",
         "change governance.", "HP", "Team Mentor", "December 2003 - August 2004 | Bangalore", "Education",
         "BE Electronics"],
    ]
    for lines in pages:
        page = doc.new_page()
        page.insert_text((72, 72), "\n".join(lines), fontsize=9)
    profile = extract_profile(parse_resume("cv.pdf", doc.tobytes()))
    assert profile.name == "Casey Example"                       # running header removed
    assert profile.current_location == "Tumkur, Karnataka, India"  # "Location" label + next line
    roles = [(e.employer, e.title, e.dates) for e in profile.experience]
    assert roles == [("Example Telecom Private Limited", "Service Delivery Manager", "January 2019 - Present"),
                     ("HP", "Team Mentor", "December 2003 - August 2004")]  # all-caps employer is not a heading
    assert profile.experience[0].highlights == ["Own incident management and change governance."]
