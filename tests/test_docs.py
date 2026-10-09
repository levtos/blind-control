from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "blind_control"
DOCS = ROOT / "docs"
FRONTEND = ROOT / "frontend"


class DocumentationTests(unittest.TestCase):
    def test_hacs_package_metadata_matches_integration_manifest(self) -> None:
        hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(hacs, {"name": "SOLARIS — Blind Control"})
        self.assertEqual(hacs["name"], manifest["name"])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")

    def test_normative_documents_exist(self) -> None:
        for filename in (
            "LASTENHEFT.md",
            "ARCHITECTURE.md",
            "INVENTORY.md",
            "CONTRACTS.md",
            "MIGRATION.md",
            "AP2_SHADOW.md",
            "STATUS_MODEL.md",
            "OPEN_METEO_REST.md",
            "AP3_CUTOVER.md",
        ):
            self.assertTrue((DOCS / filename).is_file(), filename)

        lastenheft = (DOCS / "LASTENHEFT.md").read_text(encoding="utf-8")
        for term in (
            "**Dokumentstatus:** v0.3",
            "direct_sun",
            "cloud_shadow",
            "diffuse_bright",
            "solar_not_on_window",
            "storm_approaching",
            "cool_air_available",
            "cold_insulation",
            "Override",
            "### A1 – Vorüberziehende Wolke bei Hitze",
            "14.000 auf 13.000 lx",
            "### A3 – Kanonisches Waking",
            "09:30 Uhr",
            "08:45 Uhr",
            "### A16 – Cover-Entity-Rename",
            "cover.wohnbereich_thermo_verdunklungsrollo",
            "124° OSO",
            "effective_sleep = bio_state in {provisional_sleep, sleep}",
        ):
            self.assertIn(term, lastenheft)

        ap2 = (DOCS / "AP2_SHADOW.md").read_text(encoding="utf-8")
        for term in (
            "blind_control.shadow.v1",
            "blind_control.decision.v2",
            "blind_control.ux.v2",
            "Installed / Shadow / Not Live",
            "shadow_only = true",
            "write_path_reachable = false",
            "waking",
            "cloud_shadow",
            "Owner-/Freshness",
            "blind-control-panel.js",
            "activity_state = none",
            "override_context_changed",
            "Not Live",
            "hass.add_job",
            "native Entity-Selectoren",
            "quality_blockers[]",
            "Status-Sensorentität",
            "inhaltsbasierte Revision",
        ):
            self.assertIn(term, ap2)

        status_model = (DOCS / "STATUS_MODEL.md").read_text(encoding="utf-8")
        for term in (
            "normal",
            "manual",
            "failure",
            "early_morning",
            "forenoon",
            "away_gate",
            "tv > pc > screen > none",
            "missing_optional_capabilities",
            "opening_safety_polarity",
            "async_reload(entry_id)",
            "$state.snapshot",
            "Installed / Shadow / Not Live",
            "blind_control.decision.v4",
            "blind_control.automation_projection.v3",
        ):
            self.assertIn(term, status_model)

    def test_contract_document_contains_versioned_examples_and_decisions(self) -> None:
        source = (DOCS / "CONTRACTS.md").read_text(encoding="utf-8")
        for term in (
            "mapping contract v1.5.0",
            "activity decision v1.0.0",
            "opening.v1",
            "weather_environment.v1",
            "technical_device.v1",
            "unknown",
            "reject",
            "max_age_seconds",
            "require_timestamp",
            "waking_context_superseded",
            "AP2 Decision- und UX-Contract v2",
            "master_mode",
            "active_branches[]",
            "automation_projection.v1",
            "HA-EntitySelector",
            "failure.quality_blockers[]",
            "Status-Sensorentität",
            "blind_control.decision.v4",
            "blind_control.runtime.v3",
            "blind_control.ux.v4",
            "blind_control.automation_projection.v3",
            "manual_hold",
            "effective_sleep = bio_state in {provisional_sleep, sleep}",
        ):
            self.assertIn(term, source)

    def test_inventory_classifies_every_required_surface(self) -> None:
        source = (DOCS / "INVENTORY.md").read_text(encoding="utf-8")
        for term in (
            "ConfigEntry",
            "Storage",
            "WebSocket",
            "entity.py",
            "websocket_api.py",
            "apply_now",
            "set_privacy_bed",
            "clear_manual_override",
            "set_position_profile",
            "get_status",
            "set_apply_enabled",
            "set_manual_position",
            "set_manual_decision",
            "set_invert_position",
            "reset_position_profile",
            "set_heat_lux_min",
            "Apply",
            "Cooldown",
            "Writing guard",
            "Override",
            "Startup",
            "EVENT_HOMEASSISTANT_STARTED",
            "Regression",
            "Consumer",
            "ungeklärt / Blocker",
        ):
            self.assertIn(term, source)

    def test_translation_key_sets_match(self) -> None:
        def key_paths(value, prefix=()):
            if isinstance(value, dict):
                paths = set()
                for key, child in value.items():
                    paths.update(key_paths(child, prefix + (key,)))
                return paths
            return {prefix}

        source = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
        source_keys = key_paths(source)
        for locale in ("de", "en"):
            translated = json.loads(
                (PACKAGE / "translations" / f"{locale}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(source_keys, key_paths(translated), locale)

    def test_native_entity_selector_labels_are_complete_and_human_readable(self) -> None:
        binding_groups = {
            "core_state_bindings": (
                "bio_state",
                "activity_state",
                "day_state",
                "day_context",
                "away",
                "private_time",
                "privacy",
            ),
            "opening_safety_cover_bindings": (
                "opening_state",
                "opening_safe_for_blind",
                "cover_available",
                "cover_ready",
                "cover_position",
                "opening_safety_polarity",
            ),
            "solar_bindings": (
                "outdoor_lux",
                "lux_trend",
                "sun_elevation",
                "sun_azimuth",
                "expected_direct_radiation",
                "expected_diffuse_radiation",
                "cloud_cover",
            ),
            "temperature_weather_bindings": (
                "indoor_temperature",
                "outdoor_temperature",
                "indoor_temperature_trend",
                "outdoor_temperature_trend",
                "weather_alert",
                "precipitation_trend",
                "wind_trend",
                "pressure_trend",
                "air_movement",
            ),
            "legacy_comparison_bindings": (
                "active_mode",
                "effective_target",
                "safety_status",
                "apply_status",
            ),
        }

        for filename in ("strings.json", "translations/de.json", "translations/en.json"):
            document = json.loads((PACKAGE / filename).read_text(encoding="utf-8"))
            for flow, step in (("config", "user"), ("options", "init")):
                form = document[flow]["step"][step]
                self.assertIn("open_meteo_api_url", form["data"])
                self.assertNotEqual(form["data"]["open_meteo_api_url"], "open_meteo_api_url")
                self.assertTrue(form["data_description"]["open_meteo_api_url"].strip())
                for section, fields in binding_groups.items():
                    labels = form["sections"][section]["data"]
                    descriptions = form["sections"][section]["data_description"]
                    for field in fields:
                        with self.subTest(file=filename, flow=flow, field=field):
                            self.assertIn(field, labels)
                            self.assertNotEqual(labels[field], field)
                            self.assertTrue(labels[field].strip())
                            self.assertIn(field, descriptions)
                            self.assertTrue(descriptions[field].strip())

    def test_relative_document_links_resolve(self) -> None:
        link_pattern = re.compile(r"\]\((?!https?://|#)([^)]+)\)")
        for path in (
            DOCS / "LASTENHEFT.md",
            DOCS / "ARCHITECTURE.md",
            DOCS / "INVENTORY.md",
            DOCS / "CONTRACTS.md",
            DOCS / "MIGRATION.md",
            DOCS / "AP2_SHADOW.md",
            DOCS / "STATUS_MODEL.md",
            DOCS / "AP3_CUTOVER.md",
        ):
            source = path.read_text(encoding="utf-8")
            for target in link_pattern.findall(source):
                self.assertTrue((path.parent / target).exists(), f"{path}: {target}")

    def test_ap3_runbook_is_redacted_reversible_and_separates_live_gate(self) -> None:
        source = (DOCS / "AP3_CUTOVER.md").read_text(encoding="utf-8")
        for term in (
            "Consumer-Inventar",
            "custom/homekit.yaml",
            "Core-Devices-Import",
            "source_binding_evidence.py",
            "System Readiness",
            "manual_bio_scripts.yaml",
            "cover.living_thermal_blind",
            "Null-Writer",
            "Rollback",
            "Aktueller Writer-Cutover",
            "Apply weiterhin AUS",
            "HA-Prozessneustart",
            "Testing / Not Live",
        ):
            self.assertIn(term, source)

    def test_frontend_is_contract_driven_and_has_no_secret_surface(self) -> None:
        package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
        self.assertIn("svelte", package["devDependencies"])
        self.assertIn("vite", package["devDependencies"])
        self.assertTrue((FRONTEND / "src" / "lib" / "contracts.ts").is_file())
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (FRONTEND / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".js", ".svelte", ".css"}
        )
        self.assertNotIn("SUPERVISOR_TOKEN", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sampleSnapshot", source)
        self.assertIn("blind_control/get_snapshot", source)
        self.assertIn("blind_control/update_options", source)
        self.assertIn("navigator.clipboard", source)
        self.assertIn("statusTone", source)
        self.assertIn("master_mode", source)
        self.assertIn("active_branches", source)
        self.assertIn("rebaseDraft", source)
        self.assertIn("settingsRevision", source)
        self.assertNotIn("lastSnapshot", source)
        self.assertNotIn("editableSettings.input_bindings", source)
        self.assertNotIn("editableSettings.legacy_bindings", source)
        transport = (FRONTEND / "src" / "lib" / "transport.ts").read_text(encoding="utf-8")
        for critical in ("runtime_mode", "apply_owner", "apply_enabled"):
            self.assertIn(f"delete options.{critical}", transport)

    def test_frontend_is_an_installable_ha_panel_with_official_context(self) -> None:
        main = (FRONTEND / "src" / "main.ts").read_text(encoding="utf-8")
        css = (FRONTEND / "src" / "app.css").read_text(encoding="utf-8")
        transport = (FRONTEND / "src" / "lib" / "transport.ts").read_text(encoding="utf-8")
        panel = (PACKAGE / "panel.py").read_text(encoding="utf-8")
        vite = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")

        self.assertIn("customElements.define('blind-control-panel'", main)
        self.assertIn("set hass(value", main)
        self.assertIn("props: { hass: this.hassContext }", main)
        self.assertIn("app.css?inline", main)
        self.assertIn("data-blind-control-style", main)
        self.assertIn("attachShadow({ mode: 'open' })", main)
        self.assertIn("target: this.panelRoot", main)
        self.assertIn(":host", css)
        self.assertNotIn(":root", css)
        self.assertNotRegex(css, r"(^|[},])\s*body\s*[{,]")
        self.assertNotIn("window.parent", transport)
        self.assertIn("hass.connection", transport)
        self.assertIn("async_register_static_paths", panel)
        self.assertIn("async_register_built_in_panel", panel)
        self.assertIn("js_url", panel)
        self.assertIn("normalizePanelStylesheet", vite)
        self.assertIn("replaceAll('\\r\\n', '\\n')", vite)
        bundle = PACKAGE / "frontend" / "blind-control-panel.js"
        self.assertTrue(bundle.is_file())
        bundle_source = bundle.read_text(encoding="utf-8")
        self.assertIn("blind-control-panel", bundle_source)
        self.assertIn("data-blind-control-style", bundle_source)
        self.assertNotIn("sampleSnapshot", bundle_source)
        self.assertFalse((PACKAGE / "frontend" / "index.html").exists())
        self.assertFalse(any(path.suffix == ".css" for path in (PACKAGE / "frontend").rglob("*")))

    def test_ux_contains_hierarchical_bindings_and_live_household_projection(self) -> None:
        ux = (PACKAGE / "ux_contract.py").read_text(encoding="utf-8")
        app = (FRONTEND / "src" / "App.svelte").read_text(encoding="utf-8")
        config_flow = (PACKAGE / "config_flow.py").read_text(encoding="utf-8")
        coordinator = (PACKAGE / "coordinator.py").read_text(encoding="utf-8")

        for term in ('"cover_position"', '"household"', '"master_mode"', '"active_branches"'):
            self.assertIn(term, ux)
        for term in (
            "Core Contracts",
            "binding_groups",
            "optional_intentionally_empty",
            "<Overview {snapshot}",
            "<Operation {snapshot}",
            "TECHNISCHE EBENE",
            "HAUSHALT & KONTEXT",
            "snapshot.overview.household",
        ):
            self.assertIn(term, app)
        self.assertNotIn("input_bindings", app)
        self.assertNotIn("legacy_bindings", app)
        self.assertIn("class _OptionalEntitySelector(EntitySelector)", config_flow)
        self.assertIn("return super().__call__(data)", config_flow)
        self.assertIn("section(", config_flow)
        self.assertIn('getattr(self.hass, "add_job"', coordinator)
        self.assertIn("_schedule_refresh_in_event_loop", coordinator)

    def test_internal_open_meteo_contract_uses_one_current_request_for_two_values(self) -> None:
        contract = (DOCS / "OPEN_METEO_REST.md").read_text(encoding="utf-8")
        provider = (PACKAGE / "radiation_provider.py").read_text(encoding="utf-8")

        self.assertEqual(provider.count("session.get("), 1)
        self.assertIn("current.direct_normal_irradiance_instant", contract)
        self.assertIn("current.diffuse_radiation_instant", contract)
        self.assertIn("dwd_icon_seamless", contract)
        self.assertIn("900 Sekunden", contract)
        self.assertIn("blind_control_dni_instant", contract)
        self.assertIn("blind_control_diffuse_radiation_instant", contract)
        self.assertIn("ConfigFlow/OptionsFlow", contract)
        self.assertNotIn("```yaml", contract)
        self.assertNotIn("!secret", contract)
        self.assertNotRegex(contract, r"latitude=\d")
        self.assertNotRegex(contract, r"longitude=\d")
        self.assertNotIn("hourly:", contract)


if __name__ == "__main__":
    unittest.main()
