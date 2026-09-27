"""Sources & Connectors - enable/disable, configure and test each connector."""
from __future__ import annotations

import streamlit as st

from discovery.registry import SourceRegistry
from frontend.components import badge, esc, fmt_time, hero

KIND_LABELS = {"official_api": "Official public API", "employer_api": "Employer ATS API",
               "public_page": "Public career pages", "restricted": "Requires authorized access",
               "test_fixture": "Test fixtures"}


def render(db) -> None:
    hero("Sources & Connectors", "Only official APIs, authorized integrations and permitted public pages are used.")
    registry = SourceRegistry(db)
    st.caption("A connector shows **working** only after a real successful call. Status **blocked** means the "
               "portal or your network refused access; **requires authorized access** means no permitted public "
               "access exists.")
    for entry in registry.entries():
        info = entry["info"]
        name = info.name
        status = entry.get("status") or "untested"
        with st.container(border=True):
            top = st.columns([4, 2, 1.3])
            top[0].markdown(f"### {esc(info.display_name)}")
            top[0].markdown(badge(status.replace("_", " "), status) + badge(KIND_LABELS.get(info.kind, info.kind),
                                                                              "unknown"), unsafe_allow_html=True)
            top[1].markdown(f"**Last success:** {fmt_time(entry.get('last_success_at'))}  \n"
                            f"**Last checked:** {fmt_time(entry.get('last_checked_at'))}")
            restricted = info.kind == "restricted"
            enabled = top[2].toggle("Enabled", value=bool(entry.get("enabled")), key=f"en-{name}",
                                    disabled=restricted, help="Restricted portals cannot be enabled")
            if enabled != bool(entry.get("enabled")):
                registry.repo.set_enabled(name, enabled)
                st.toast(f"{info.display_name} {'enabled' if enabled else 'disabled'}.")
                st.rerun()
            st.markdown(f"{esc(info.description)}  \n**Access:** {esc(info.access_notes)}")
            if info.terms_url:
                st.markdown(f"[Provider terms / docs]({info.terms_url})")
            if entry.get("last_error") and not restricted:
                st.error(entry["last_error"], icon="⚠️")
            if info.secret_fields:
                _key_form(name, info.secret_fields)
            if info.config_fields:
                config = dict(entry.get("config") or {})
                with st.form(f"cfg-{name}"):
                    new = {}
                    for field, help_text in info.config_fields.items():
                        current = config.get(field, "")
                        current = "\n".join(current) if isinstance(current, list) else str(current)
                        new[field] = st.text_area(field, current, help=help_text, height=70)
                    if st.form_submit_button("Save configuration"):
                        registry.repo.set_config(name, new)
                        st.toast("Configuration saved.", icon="💾")
                        st.rerun()
            if not restricted and st.button("Test connection", key=f"test-{name}"):
                with st.spinner(f"Contacting {info.display_name}…"):
                    result = registry.test_connection(name)
                if result.error:
                    st.error(f"{result.status}: {result.error}")
                else:
                    st.success(f"Working – {len(result.listings)} listings returned by a test query.")


def _key_form(name: str, fields: dict[str, str]) -> None:
    """Enter API keys here; they are written to the local .env file only."""
    from config.secrets import SecretError, current, mask, save_secret

    with st.form(f"keys-{name}"):
        st.markdown("**🔑 API keys** – stored only in the `.env` file on this computer")
        values = {}
        for env_name, label in fields.items():
            values[env_name] = st.text_input(f"{label} · {mask(current(env_name))}", type="password",
                                             key=f"secret-{env_name}", placeholder="paste key here")
        if st.form_submit_button("Save keys"):
            saved = []
            for env_name, value in values.items():
                if not value.strip():
                    continue
                try:
                    save_secret(env_name, value)
                    saved.append(env_name)
                except SecretError as exc:
                    st.error(f"{env_name}: {exc}")
            if saved:
                st.success("Saved. Now switch **Enabled** on and press **Test connection** – no restart needed.")
