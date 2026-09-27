"""GUI tests using Streamlit's AppTest harness (headless)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from database.connection import Database, set_database
from database.lifecycle import JobLifecycleService
from database.migrations import migrate
from database.repository import JobRepository, SourceRegistryRepository
from discovery.registry import SourceRegistry
from frontend.selected_jobs import apply_edits
from tests.helpers import insert_job

PAGES = ["Dashboard", "Find Jobs", "Pending Approval", "Selected Jobs", "Applied Jobs", "Resume & Skills",
         "Search Settings", "Sources & Connectors", "Agent Logs"]


@pytest.fixture
def ui_db(tmp_path):
    database = Database(tmp_path / "ui.db")
    migrate(database)
    SourceRegistry(database)
    repo = SourceRegistryRepository(database)
    for row in repo.list():  # never touch the network from UI tests
        repo.set_enabled(row["source_name"], row["source_name"] == "test_fixtures")
    set_database(database)
    st.cache_resource.clear()
    yield database
    set_database(None)
    st.cache_resource.clear()
    database.close()


def app() -> AppTest:
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def markdown_text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown)


def goto(at: AppTest, page: str) -> AppTest:
    at.button(key=f"nav-{page}").click().run()
    assert not at.exception, at.exception
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_navigation_button_works(ui_db, page):
    at = goto(app(), page)
    assert at.session_state["page"] == page
    heading = "Recruitment command center" if page == "Dashboard" else page
    assert f"<h1>{heading.replace('&', '&amp;')}</h1>" in markdown_text(at)


def test_empty_states_explain_next_steps(ui_db):
    at = goto(app(), "Pending Approval")
    assert "Your approval queue is empty" in markdown_text(at)
    at = goto(at, "Selected Jobs")
    assert "No selected jobs yet" in markdown_text(at)
    at = goto(at, "Applied Jobs")
    assert "No active applied jobs" in markdown_text(at)
    at = goto(at, "Agent Logs")
    assert "No runs yet" in markdown_text(at)


def test_dashboard_counts_come_from_database(ui_db):
    insert_job(ui_db, title="Role A", url="https://careers.example.com/a")
    insert_job(ui_db, title="Role B", url="https://careers.example.com/b")
    at = app()
    text = markdown_text(at)
    assert "Awaiting approval" in text and ">2<" in text


def test_run_job_search_from_gui_with_fixtures(ui_db):
    at = goto(app(), "Find Jobs")
    at.button[-1]  # form submit exists
    submit = next(b for b in at.button if "Run Job Search" in b.label)
    submit.click().run()
    assert not at.exception, at.exception
    assert JobRepository(ui_db).counts()["pending"] >= 6
    assert any(m.label == "Added to approval queue" for m in at.metric)


def test_mark_relevant_and_ignore_buttons_give_feedback(ui_db):
    a = insert_job(ui_db, title="Role A", url="https://careers.example.com/a")
    b = insert_job(ui_db, title="Role B", url="https://careers.example.com/b")
    at = goto(app(), "Pending Approval")
    assert "TEST FIXTURE" in markdown_text(at)
    at.button(key=f"rel-{a}").click().run()
    assert not at.exception
    assert [j["job_id"] for j in JobRepository(ui_db).list_selected()] == [a]
    at.button(key=f"ign-{b}").click().run()
    assert JobRepository(ui_db).get(b) is None
    assert "Your approval queue is empty" in markdown_text(at)


def test_selected_table_has_links_and_applied_dropdown(ui_db):
    a = insert_job(ui_db, title="Role A", url="https://careers.example.com/a")
    JobLifecycleService(ui_db).mark_relevant(a)
    at = goto(app(), "Selected Jobs")
    assert "https://careers.example.com/a" in markdown_text(at)  # direct links list
    messages = apply_edits(ui_db, [a], {0: {"Status": "Applied"}})
    assert messages[0][0] == "✅"
    assert JobRepository(ui_db).list_selected() == []
    at = goto(app(), "Applied Jobs")
    assert at.metric[0].value == "1"


def test_applied_dropdown_on_stale_row_reports_error(ui_db):
    a = insert_job(ui_db, title="Role A", url="https://careers.example.com/a")
    messages = apply_edits(ui_db, [a], {0: {"Status": "Applied"}})  # not selected yet
    assert messages[0][0] == "⚠️"


def test_untrusted_text_is_escaped(ui_db):
    insert_job(ui_db, title="<script>alert(1)</script>Evil", url="https://careers.example.com/x")
    at = goto(app(), "Pending Approval")
    text = markdown_text(at)
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_add_a_job_you_found_from_gui(ui_db):
    at = goto(app(), "Find Jobs")
    field = {w.label: w for w in list(at.text_input) + list(at.text_area)}
    field["Job link *"].set_value("https://www.naukri.com/job-listings-service-delivery-manager-123")
    field["Job title *"].set_value("Service Delivery Manager")
    field["Company *"].set_value("Example Services")
    field["Location (as stated in the listing)"].set_value("Pune, India")
    field["Full job description *"].set_value(
        "Own ITIL service delivery, incident management and vendor governance for enterprise clients. "
        "Office based in Pune with occasional travel to client sites across India.")
    next(b for b in at.button if "Check this job" in b.label).click().run()
    assert not at.exception, at.exception
    assert any("added to Pending Approval" in s.value for s in at.success)
    job = JobRepository(ui_db).list_by_status("pending")[0]
    assert job["source_name"] == "manual:naukri.com"


def test_enter_adzuna_key_in_gui(ui_db, tmp_path, monkeypatch):
    import os

    import config.secrets as secrets

    monkeypatch.setattr(secrets, "ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    at = goto(app(), "Sources & Connectors")
    at.text_input(key="secret-ADZUNA_APP_ID").set_value("myappid1")
    at.text_input(key="secret-ADZUNA_APP_KEY").set_value("myappkey0123456789")
    next(b for b in at.button if b.label == "Save keys" and "adzuna" in str(b.form_id)).click().run()
    assert not at.exception, at.exception
    assert "ADZUNA_APP_ID=myappid1" in (tmp_path / ".env").read_text()
    assert os.environ["ADZUNA_APP_KEY"] == "myappkey0123456789"
    assert "myappkey0123456789" not in markdown_text(at)
    os.environ.pop("ADZUNA_APP_ID", None)
    os.environ.pop("ADZUNA_APP_KEY", None)
