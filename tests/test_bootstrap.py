from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
import types
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "blind_control"


class _FakeSchema:
    def __init__(self, schema):
        self.schema = schema


class _FakeSection:
    def __init__(self, schema, options):
        self.schema = schema
        self.options = options


class _FakeSelector:
    def __init__(self, config):
        self.config = config


class _FakeEntitySelector(_FakeSelector):
    def __init__(self, config=None):
        super().__init__({"entity": config or {}})

    def __call__(self, data):
        if not isinstance(data, str) or "." not in data:
            raise ValueError("invalid entity identifier")
        return data


class _SchemaKey:
    def __init__(self, key, required, options=None):
        self.key = key
        self.required = required
        self.options = options or {}

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        return other == self.key or (
            isinstance(other, _SchemaKey)
            and self.key == other.key
            and self.required == other.required
        )

    def __repr__(self):
        return f"{self.required}({self.key!r})"


class _FakeVoluptuous(types.ModuleType):
    Schema = _FakeSchema

    @staticmethod
    def Required(key, **kwargs):
        return _SchemaKey(key, "required", kwargs)

    @staticmethod
    def Optional(key, **kwargs):
        return _SchemaKey(key, "optional", kwargs)

    @staticmethod
    def Coerce(value):
        return value

    @staticmethod
    def All(*values):
        return values

    @staticmethod
    def Range(**values):
        return values

    @staticmethod
    def In(values):
        return values


class _FakeConfigEntry:
    def __init__(self, entry_id: str, *, data=None, options=None):
        self.entry_id = entry_id
        self.domain = "blind_control"
        self.data = data or {}
        self.options = options or {}
        self.runtime_data = None
        self.unload_callbacks = []
        self.update_listeners = []

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)
        return callback

    def add_update_listener(self, callback):
        self.update_listeners.append(callback)
        return lambda: self.update_listeners.remove(callback)

    @classmethod
    def __class_getitem__(cls, _item):
        return cls


class _FakeSensorEntity:
    async def async_added_to_hass(self):
        return None

    def async_on_remove(self, callback):
        self._remove_callbacks = [*getattr(self, "_remove_callbacks", []), callback]

    def async_write_ha_state(self):
        self._state_write_count = getattr(self, "_state_write_count", 0) + 1

    async def async_will_remove_from_hass(self):
        for callback in getattr(self, "_remove_callbacks", []):
            callback()
        self._remove_callbacks = []


class _FakeDataUpdateCoordinator:
    def __init__(
        self,
        hass,
        _logger,
        *,
        config_entry=None,
        name,
        update_interval=None,
        always_update=True,
        **_kwargs,
    ):
        self.hass = hass
        self.config_entry = config_entry
        self.name = name
        self.update_interval = update_interval
        self.always_update = always_update
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self.listeners = []
        self.shutdown = False
        if config_entry is not None:
            config_entry.async_on_unload(self.async_shutdown)

    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
        except Exception as error:
            self.last_update_success = False
            self.last_exception = error
        else:
            self.last_update_success = True
        for listener in tuple(self.listeners):
            listener()

    def async_add_listener(self, listener, _context=None):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener) if listener in self.listeners else None

    async def async_request_refresh(self):
        await self.async_refresh()

    async def async_shutdown(self):
        self.shutdown = True
        self.listeners.clear()


class _FakeCoordinatorEntity:
    def __init__(self, coordinator, _context=None):
        self.coordinator = coordinator

    @classmethod
    def __class_getitem__(cls, _item):
        return cls

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class _FakeUpdateFailed(Exception):
    pass


class _FakeClientError(Exception):
    pass


class _FakeClientTimeout:
    def __init__(self, *, total):
        self.total = total


class _FakeResponse:
    def __init__(self, payload, *, error=None):
        self.payload = payload
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.error is not None:
            raise self.error

    async def json(self):
        return self.payload


class _FakeClientSession:
    def __init__(self, payload=None, *, request_error=None):
        self.payload = payload or {}
        self.request_error = request_error
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        if self.request_error is not None:
            raise self.request_error
        return _FakeResponse(self.payload)


class _FakeEntityRegistry:
    def __init__(self):
        self.entries = {}

    def register(self, entity):
        unique_id = getattr(entity, "_attr_unique_id", None)
        self.entries[unique_id] = {
            "unique_id": unique_id,
            "translation_key": getattr(entity, "_attr_translation_key", None),
        }

    def remove(self, entity):
        self.entries.pop(getattr(entity, "_attr_unique_id", None), None)


class _FakeHomeAssistant:
    def __init__(self):
        self.data = {}
        self.http = _FakeHttp()
        self.entity_registry = _FakeEntityRegistry()
        self._entities_by_entry = {}
        self._entries = {}
        self._state_values = {}
        self.states = types.SimpleNamespace(get=lambda entity_id: self._state_values.get(entity_id))
        self.config = types.SimpleNamespace(latitude=50.0, longitude=8.0, time_zone="Europe/Berlin")
        self.client_session = _FakeClientSession()
        self.config_entries = types.SimpleNamespace(
            reloads=[],
            updates=[],
            forwards=[],
            unloads=[],
            async_reload=self._async_reload,
            async_update_entry=self._async_update_entry,
            async_forward_entry_setups=self._async_forward_entry_setups,
            async_unload_platforms=self._async_unload_platforms,
        )

    async def _async_reload(self, entry_id):
        """Mirror HA 2026.8 async_reload(entry_id) and rebuild runtime state."""

        self.config_entries.reloads.append(entry_id)
        entry = self._entries[entry_id]
        module = importlib.import_module("custom_components.blind_control")
        await module.async_unload_entry(self, entry)
        callbacks = list(entry.unload_callbacks)
        entry.unload_callbacks.clear()
        for callback in callbacks:
            result = callback()
            if asyncio.iscoroutine(result):
                await result
        await module.async_setup_entry(self, entry)

    def _async_update_entry(self, entry, *, options):
        self.config_entries.updates.append((entry, options))

    async def _async_forward_entry_setups(self, entry, platforms):
        self._entries[entry.entry_id] = entry
        names = tuple(getattr(platform, "value", platform) for platform in platforms)
        self.config_entries.forwards.append((entry.entry_id, names))
        added = []

        def async_add_entities(entities):
            added.extend(entities)

        for name in names:
            module = importlib.import_module(f"custom_components.blind_control.{name}")
            await module.async_setup_entry(self, entry, async_add_entities)
        for entity in added:
            entity.hass = self
            self.entity_registry.register(entity)
            await entity.async_added_to_hass()
        self._entities_by_entry[entry.entry_id] = added

    async def _async_unload_platforms(self, entry, platforms):
        names = tuple(getattr(platform, "value", platform) for platform in platforms)
        self.config_entries.unloads.append((entry.entry_id, names))
        for entity in self._entities_by_entry.pop(entry.entry_id, []):
            await entity.async_will_remove_from_hass()
            self.entity_registry.remove(entity)
        return True


class _FakeStaticPathConfig:
    def __init__(self, url_path, path, cache_headers):
        self.url_path = url_path
        self.path = path
        self.cache_headers = cache_headers


class _FakeHttp:
    def __init__(self):
        self.static_paths = []

    async def async_register_static_paths(self, paths):
        if any(
            existing.url_path == path.url_path for existing in self.static_paths for path in paths
        ):
            raise RuntimeError("static path already registered")
        self.static_paths.extend(paths)


class _FakeEventModule(types.ModuleType):
    def __init__(self):
        super().__init__("homeassistant.helpers.event")
        self.state_callbacks = []
        self.time_callbacks = []

    def async_track_state_change_event(self, _hass, _entity_ids, callback):
        self.state_callbacks.append(callback)
        return lambda: self.state_callbacks.remove(callback)

    def async_track_time_interval(self, _hass, callback, _interval):
        self.time_callbacks.append(callback)
        return lambda: self.time_callbacks.remove(callback)


class _FakeFrontend(types.ModuleType):
    def async_register_built_in_panel(self, hass, **kwargs):
        hass.data.setdefault("frontend_panels", {})[kwargs["frontend_url_path"]] = kwargs

    def async_remove_panel(self, hass, url_path):
        hass.data.setdefault("frontend_panels", {}).pop(url_path, None)


class _FakeWebsocket(types.ModuleType):
    def __init__(self):
        super().__init__("homeassistant.components.websocket_api")
        self.commands = []

    def websocket_command(self, schema):
        def decorate(handler):
            handler.websocket_schema = schema
            return handler

        return decorate

    @staticmethod
    def async_response(handler):
        return handler

    @staticmethod
    def require_admin(handler):
        async def guarded(hass, connection, msg):
            if not getattr(getattr(connection, "user", None), "is_admin", False):
                connection.send_error(msg["id"], "unauthorized", "Admin required")
                return
            await handler(hass, connection, msg)

        guarded.requires_admin = True
        return guarded

    def async_register_command(self, _hass, handler):
        self.commands.append(handler)


class _FakeConnection:
    def __init__(self, *, is_admin):
        self.user = types.SimpleNamespace(is_admin=is_admin)
        self.results = []
        self.errors = []

    def send_result(self, message_id, result):
        self.results.append((message_id, result))

    def send_error(self, message_id, code, message):
        self.errors.append((message_id, code, message))


class _DuplicateEntry(Exception):
    pass


class _FakeConfigFlow:
    configured_unique_ids: set[str] = set()

    def __init_subclass__(cls, **kwargs):
        kwargs.pop("domain", None)
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, unique_id: str):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if self._unique_id in self.configured_unique_ids:
            raise _DuplicateEntry(self._unique_id)
        self.configured_unique_ids.add(self._unique_id)

    def async_show_form(self, *, step_id, data_schema, **kwargs):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, **kwargs}

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}


class _FakeOptionsFlow(_FakeConfigFlow):
    pass


def _schema_value(schema: _FakeSchema, key: str):
    for schema_key, value in schema.schema.items():
        if getattr(schema_key, "key", schema_key) == key:
            return value
    raise KeyError(key)


def _schema_key(schema: _FakeSchema, key: str):
    for schema_key in schema.schema:
        if getattr(schema_key, "key", schema_key) == key:
            return schema_key
    raise KeyError(key)


@contextmanager
def _home_assistant_imports():
    voluptuous = _FakeVoluptuous("voluptuous")
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = _FakeClientError
    aiohttp.ClientTimeout = _FakeClientTimeout
    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = _FakeConfigEntry
    config_entries.ConfigFlow = _FakeConfigFlow
    config_entries.OptionsFlow = _FakeOptionsFlow
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = _FakeHomeAssistant
    core.callback = lambda function: function
    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.section = lambda schema, options: _FakeSection(schema, options)
    helpers = types.ModuleType("homeassistant.helpers")
    event = _FakeEventModule()
    selector_module = types.ModuleType("homeassistant.helpers.selector")
    selector_module.selector = lambda config: _FakeSelector(config)
    selector_module.EntitySelector = _FakeEntitySelector
    helpers.selector = selector_module
    helpers.event = event
    components = types.ModuleType("homeassistant.components")
    websocket_api = _FakeWebsocket()
    frontend = _FakeFrontend("homeassistant.components.frontend")
    http = types.ModuleType("homeassistant.components.http")
    http.StaticPathConfig = _FakeStaticPathConfig
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.SensorEntity = _FakeSensorEntity
    sensor.SensorDeviceClass = types.SimpleNamespace(IRRADIANCE="irradiance")
    sensor.SensorStateClass = types.SimpleNamespace(MEASUREMENT="measurement")
    const = types.ModuleType("homeassistant.const")
    const.Platform = types.SimpleNamespace(SENSOR="sensor")
    const.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")
    const.UnitOfIrradiance = types.SimpleNamespace(WATTS_PER_SQUARE_METER="W/m²")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddConfigEntryEntitiesCallback = object
    device_registry = types.ModuleType("homeassistant.helpers.device_registry")
    device_registry.DeviceInfo = dict
    update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    update_coordinator.DataUpdateCoordinator = _FakeDataUpdateCoordinator
    update_coordinator.CoordinatorEntity = _FakeCoordinatorEntity
    update_coordinator.UpdateFailed = _FakeUpdateFailed
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: hass.client_session
    components.websocket_api = websocket_api
    components.frontend = frontend
    components.http = http
    components.sensor = sensor
    homeassistant.config_entries = config_entries
    homeassistant.core = core
    homeassistant.data_entry_flow = data_entry_flow
    homeassistant.helpers = helpers
    homeassistant.components = components
    _FakeConfigFlow.configured_unique_ids.clear()

    for name in tuple(sys.modules):
        if name == "custom_components.blind_control" or name.startswith(
            "custom_components.blind_control."
        ):
            sys.modules.pop(name, None)

    with patch.dict(
        sys.modules,
        {
            "voluptuous": voluptuous,
            "aiohttp": aiohttp,
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.const": const,
            "homeassistant.data_entry_flow": data_entry_flow,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.selector": selector_module,
            "homeassistant.helpers.event": event,
            "homeassistant.helpers.entity_platform": entity_platform,
            "homeassistant.helpers.device_registry": device_registry,
            "homeassistant.helpers.update_coordinator": update_coordinator,
            "homeassistant.helpers.aiohttp_client": aiohttp_client,
            "homeassistant.components": components,
            "homeassistant.components.websocket_api": websocket_api,
            "homeassistant.components.frontend": frontend,
            "homeassistant.components.http": http,
            "homeassistant.components.sensor": sensor,
        },
    ):
        try:
            yield
        finally:
            for name in tuple(sys.modules):
                if name == "custom_components.blind_control" or name.startswith(
                    "custom_components.blind_control."
                ):
                    sys.modules.pop(name, None)


class BootstrapTests(unittest.TestCase):
    def test_v061_persisted_temperature_floor_survives_options_and_entry_load(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            flow_module = importlib.import_module("custom_components.blind_control.config_flow")
            hass = _FakeHomeAssistant()
            hass.config = types.SimpleNamespace(time_zone="UTC")
            entry = _FakeConfigEntry(
                "fixture",
                data={"config_version": 6},
                options={
                    "observation_freshness_seconds": 120,
                    "input_bindings": {"outdoor_temperature": "weather.fixture"},
                    "binding_freshness": {
                        "outdoor_temperature": {
                            "max_age_seconds": 120,
                            "require_timestamp": True,
                        }
                    },
                },
            )
            hass._state_values["weather.fixture"] = types.SimpleNamespace(
                entity_id="weather.fixture",
                state="cloudy",
                attributes={"temperature": 12},
                last_updated=datetime.now(UTC) - timedelta(seconds=600),
            )

            async def scenario():
                self.assertTrue(await module.async_setup_entry(hass, entry))
                runtime = entry.runtime_data
                self.assertEqual(
                    runtime.config.binding_policy("outdoor_temperature").max_age_seconds, 1800
                )
                self.assertEqual(runtime.snapshot.inputs["outdoor_temperature"]["quality"], "fresh")
                options = flow_module.BlindControlOptionsFlow(entry)
                options.hass = hass
                saved = await options.async_step_init({"observation_freshness_seconds": 120})
                self.assertEqual(saved["type"], "create_entry")
                self.assertEqual(
                    saved["data"]["binding_freshness"]["outdoor_temperature"]["max_age_seconds"],
                    1800,
                )
                self.assertEqual(saved["data"]["runtime_mode"], "shadow")
                self.assertEqual(saved["data"]["apply_owner"], "legacy")
                self.assertTrue(await module.async_unload_entry(hass, entry))

            asyncio.run(scenario())

    def test_native_options_persist_all_three_cutover_states_into_runtime(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            flow_module = importlib.import_module("custom_components.blind_control.config_flow")
            hass = _FakeHomeAssistant()
            hass.config = types.SimpleNamespace(time_zone="UTC")
            entry = _FakeConfigEntry("fixture", data={"config_version": 6})

            async def scenario():
                for mode, owner, enabled in (
                    ("shadow", "legacy", False),
                    ("live", "blind_control", False),
                    ("live", "blind_control", True),
                    ("shadow", "legacy", False),
                ):
                    flow = flow_module.BlindControlOptionsFlow(entry)
                    flow.hass = hass
                    form = await flow.async_step_init()
                    for key in ("runtime_mode", "apply_owner", "apply_enabled"):
                        self.assertIn(key, form["data_schema"].schema)
                    saved = await flow.async_step_init(
                        {"runtime_mode": mode, "apply_owner": owner, "apply_enabled": enabled}
                    )
                    self.assertEqual(saved["type"], "create_entry")
                    entry.options = saved["data"]
                    self.assertTrue(await module.async_setup_entry(hass, entry))
                    runtime = entry.runtime_data
                    self.assertEqual(runtime.config.runtime_mode, mode)
                    self.assertEqual(runtime.config.apply_owner, owner)
                    self.assertEqual(runtime.config.apply_enabled, enabled)
                    # Arming cannot bypass absent technical evidence.
                    self.assertFalse(runtime.snapshot.write_path_reachable)
                    self.assertFalse(runtime.snapshot.actuation_executed)
                    self.assertTrue(await module.async_unload_entry(hass, entry))

            asyncio.run(scenario())

    def test_manifest_is_native_config_entry_bootstrap(self) -> None:
        manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["domain"], "blind_control")
        self.assertEqual(manifest["name"], "SOLARIS — Blind Control")
        self.assertTrue(manifest["config_flow"])
        self.assertEqual(manifest["iot_class"], "cloud_polling")
        self.assertNotIn("platforms", manifest)

    def test_setup_and_unload_use_entry_runtime_data(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            hass = _FakeHomeAssistant()
            entry = _FakeConfigEntry("entry-1")

            self.assertTrue(asyncio.run(module.async_setup(hass, {})))
            self.assertIn("blind-control", hass.data["frontend_panels"])
            self.assertEqual(hass.http.static_paths[0].url_path, "/blind-control/frontend")
            self.assertTrue(asyncio.run(module.async_setup_entry(hass, entry)))
            self.assertIsInstance(entry.runtime_data, module.BlindControlRuntimeData)
            self.assertEqual(entry.runtime_data.phase, "shadow")
            self.assertIsNotNone(entry.runtime_data.snapshot)
            self.assertEqual(hass.config_entries.forwards, [("entry-1", ("sensor",))])
            self.assertTrue(asyncio.run(module.async_unload_entry(hass, entry)))
            self.assertEqual(hass.config_entries.unloads, [("entry-1", ("sensor",))])

    def test_internal_provider_one_request_two_registry_sensors_and_clean_unload(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            open_meteo = importlib.import_module("custom_components.blind_control.open_meteo")
            hass = _FakeHomeAssistant()
            hass.client_session = _FakeClientSession(
                {
                    "current": {
                        "time": "2026-08-21T12:00",
                        "direct_normal_irradiance_instant": 320,
                        "diffuse_radiation_instant": 75,
                    }
                }
            )
            url = open_meteo.suggested_open_meteo_url(50, 8, "Europe/Berlin")
            entry = _FakeConfigEntry("entry-1", data={"open_meteo_api_url": url})

            asyncio.run(module.async_setup_entry(hass, entry))

            provider = entry.runtime_data.radiation_provider
            self.assertEqual(len(hass.client_session.requests), 1)
            self.assertEqual(provider.data.direct_normal_irradiance, 320)
            self.assertEqual(provider.data.diffuse_radiation, 75)
            self.assertEqual(
                entry.runtime_data.snapshot.inputs["expected_direct_radiation"]["value"],
                320,
            )
            self.assertEqual(
                entry.runtime_data.snapshot.inputs["expected_diffuse_radiation"]["value"],
                75,
            )
            entities = hass._entities_by_entry[entry.entry_id]
            self.assertEqual(
                {entity._attr_unique_id for entity in entities},
                {
                    "entry-1_shadow_status",
                    "blind_control_dni_instant",
                    "blind_control_diffuse_radiation_instant",
                },
            )
            values = {entity._attr_unique_id: entity.native_value for entity in entities}
            self.assertEqual(values["blind_control_dni_instant"], 320)
            self.assertEqual(values["blind_control_diffuse_radiation_instant"], 75)
            radiation = next(
                entity
                for entity in entities
                if entity._attr_unique_id == "blind_control_dni_instant"
            )
            attributes = json.dumps(radiation.extra_state_attributes)
            self.assertEqual(radiation._attr_device_class, "irradiance")
            self.assertEqual(radiation._attr_state_class, "measurement")
            self.assertEqual(radiation._attr_native_unit_of_measurement, "W/m²")
            self.assertNotIn(url, attributes)
            self.assertNotIn("latitude", attributes)
            self.assertNotIn("longitude", attributes)
            projection = json.dumps(entry.runtime_data.ux_snapshot)
            self.assertNotIn(url, projection)
            self.assertNotIn("latitude", projection)
            self.assertNotIn("longitude", projection)
            self.assertEqual(provider.update_interval.total_seconds(), 900)
            self.assertEqual(len(provider.listeners), 3)

            self.assertTrue(asyncio.run(module.async_unload_entry(hass, entry)))
            self.assertTrue(provider.shutdown)
            self.assertEqual(provider.listeners, [])

    def test_legacy_entry_location_prefill_makes_provider_sensors_available(self) -> None:
        """Entries created before the URL field must not strand the sensors unavailable."""

        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            open_meteo = importlib.import_module("custom_components.blind_control.open_meteo")
            hass = _FakeHomeAssistant()
            hass.client_session = _FakeClientSession(
                {
                    "current": {
                        "direct_normal_irradiance_instant": 280,
                        "diffuse_radiation_instant": 65,
                    }
                }
            )
            entry = _FakeConfigEntry("entry-legacy")

            asyncio.run(module.async_setup_entry(hass, entry))

            expected_url = open_meteo.suggested_open_meteo_url(50, 8, "Europe/Berlin")
            self.assertEqual(entry.runtime_data.config.open_meteo_api_url, expected_url)
            self.assertNotIn("open_meteo_api_url", entry.data)
            self.assertEqual(len(hass.client_session.requests), 1)
            radiation_entities = [
                entity
                for entity in hass._entities_by_entry[entry.entry_id]
                if entity._attr_unique_id.startswith("blind_control_")
            ]
            self.assertEqual(len(radiation_entities), 2)
            self.assertTrue(all(entity.available for entity in radiation_entities))
            self.assertEqual(
                {entity.native_value for entity in radiation_entities},
                {280.0, 65.0},
            )

            self.assertTrue(asyncio.run(module.async_unload_entry(hass, entry)))

    def test_provider_failure_retains_only_fresh_last_success_and_zero_is_valid(self) -> None:
        with _home_assistant_imports():
            provider_module = importlib.import_module(
                "custom_components.blind_control.radiation_provider"
            )
            open_meteo = importlib.import_module("custom_components.blind_control.open_meteo")
            hass = _FakeHomeAssistant()
            entry = _FakeConfigEntry("entry-1")
            url = open_meteo.suggested_open_meteo_url(50, 8)
            provider = provider_module.OpenMeteoRadiationCoordinator(hass, entry, url)
            hass.client_session = _FakeClientSession(request_error=TimeoutError())

            asyncio.run(provider.async_refresh())
            self.assertIsNone(provider.data)
            self.assertEqual(provider.provider_status(), "unavailable")

            hass.client_session = _FakeClientSession(
                {"current": {"direct_normal_irradiance_instant": "bad"}}
            )
            asyncio.run(provider.async_refresh())
            self.assertIsNone(provider.data)
            self.assertEqual(provider.provider_status(), "unavailable")

            hass.client_session = _FakeClientSession(
                {
                    "current": {
                        "direct_normal_irradiance_instant": 0,
                        "diffuse_radiation_instant": 0,
                    }
                }
            )
            asyncio.run(provider.async_refresh())
            self.assertEqual(provider.data.direct_normal_irradiance, 0)
            self.assertEqual(provider.provider_status(), "ready")

            hass.client_session = _FakeClientSession(request_error=TimeoutError())
            asyncio.run(provider.async_refresh())
            self.assertEqual(provider.data.direct_normal_irradiance, 0)
            self.assertEqual(provider.provider_status(), "degraded")
            provider.data = replace(
                provider.data,
                fetched_at=datetime.now(UTC) - timedelta(seconds=1300),
            )
            self.assertEqual(provider.provider_status(), "stale")

    def test_config_and_options_flow_validate_store_and_preserve_provider_url(self) -> None:
        with _home_assistant_imports():
            loaded = importlib.import_module("custom_components.blind_control.config_flow")
            open_meteo = importlib.import_module("custom_components.blind_control.open_meteo")
            hass = _FakeHomeAssistant()
            flow = loaded.BlindControlConfigFlow()
            flow.hass = hass
            form = asyncio.run(flow.async_step_user())
            suggested = _schema_key(form["data_schema"], "open_meteo_api_url").options["default"]
            self.assertEqual(
                suggested,
                open_meteo.suggested_open_meteo_url(50, 8, "Europe/Berlin"),
            )

            invalid = asyncio.run(
                flow.async_step_user({"open_meteo_api_url": "http://example.invalid"})
            )
            self.assertEqual(
                invalid["errors"],
                {"open_meteo_api_url": "invalid_open_meteo_url"},
            )

            custom = open_meteo.suggested_open_meteo_url(49, 9, "UTC")
            created = asyncio.run(flow.async_step_user({"open_meteo_api_url": custom}))
            self.assertEqual(created["data"]["open_meteo_api_url"], custom)

        with _home_assistant_imports():
            loaded = importlib.import_module("custom_components.blind_control.config_flow")
            open_meteo = importlib.import_module("custom_components.blind_control.open_meteo")
            old_url = open_meteo.suggested_open_meteo_url(50, 8)
            entry = _FakeConfigEntry("entry-1", data={"open_meteo_api_url": old_url})
            options_flow = loaded.BlindControlOptionsFlow(entry)
            options_flow.hass = _FakeHomeAssistant()
            invalid = asyncio.run(
                options_flow.async_step_init({"open_meteo_api_url": old_url + "&api_key=forbidden"})
            )
            self.assertEqual(
                invalid["errors"],
                {"open_meteo_api_url": "invalid_open_meteo_url"},
            )
            self.assertEqual(entry.data["open_meteo_api_url"], old_url)
            self.assertEqual(entry.options, {})
            new_url = open_meteo.suggested_open_meteo_url(49, 9, "UTC")
            saved = asyncio.run(options_flow.async_step_init({"open_meteo_api_url": new_url}))
            self.assertEqual(saved["type"], "create_entry")
            self.assertEqual(saved["data"]["open_meteo_api_url"], new_url)

    def test_native_status_projection_is_registry_backed_redacted_and_unloads(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            contracts = importlib.import_module("custom_components.blind_control.contracts")
            hass = _FakeHomeAssistant()
            entry = _FakeConfigEntry("entry-1")

            asyncio.run(module.async_setup_entry(hass, entry))
            sensor = hass._entities_by_entry[entry.entry_id][0]
            registry_entry = hass.entity_registry.entries[sensor._attr_unique_id]
            self.assertEqual(registry_entry["unique_id"], "entry-1_shadow_status")
            self.assertEqual(registry_entry["translation_key"], "status")
            self.assertEqual(sensor.native_value, "normal")
            initial_attributes = sensor.extra_state_attributes
            self.assertEqual(initial_attributes["failure_status"], "none")
            self.assertEqual(initial_attributes["safety_status"], "blocked")
            self.assertIn(
                "bio_state",
                {issue["evidence"] for issue in initial_attributes["decision"]["issues"]},
            )

            def fresh(value, source):
                return contracts.InputObservation(
                    value=value,
                    source=source,
                    quality=contracts.InputQuality.FRESH,
                )

            inputs = contracts.BlindControlInputs(
                bio_state=fresh("awake", "sensor.fixture_bio"),
                activity_state=fresh("none", "sensor.fixture_activity"),
                day_state=fresh("forenoon", "sensor.fixture_day"),
                day_context=fresh("weekday", "sensor.fixture_context"),
                away=fresh(False, "sensor.fixture_away"),
                private_time=fresh(False, "sensor.fixture_private_time"),
                privacy=fresh(False, "sensor.fixture_privacy"),
                opening_state=fresh("closed", "sensor.fixture_opening"),
                opening_safe_for_blind=fresh(True, "sensor.fixture_opening"),
                cover_available=fresh(True, "sensor.fixture_cover"),
                cover_ready=fresh(True, "sensor.fixture_cover"),
                cover_position=fresh(42.0, "sensor.fixture_cover"),
                outdoor_lux=fresh(14_000.0, "sensor.fixture_lux"),
                lux_trend=fresh(0.0, "sensor.fixture_lux"),
                sun_elevation=fresh(30.0, "sensor.fixture_sun"),
                sun_azimuth=fresh(304.0, "sensor.fixture_sun"),
                expected_direct_radiation=fresh(400.0, "sensor.fixture_weather"),
                expected_diffuse_radiation=fresh(50.0, "sensor.fixture_weather"),
                cloud_cover=fresh(0.1, "sensor.fixture_weather"),
                indoor_temperature=fresh(22.0, "sensor.fixture_indoor"),
                outdoor_temperature=fresh(20.0, "sensor.fixture_outdoor"),
            )
            entry.runtime_data.shadow.on_restart(
                42
            )  # Quiet baseline for this projection-only fixture.
            entry.runtime_data.snapshot = entry.runtime_data.shadow.evaluate(inputs)
            entry.runtime_data.coordinator._notify_snapshot_listeners()

            self.assertEqual(sensor.native_value, "normal")
            self.assertGreater(sensor._state_write_count, 0)
            attributes = sensor.extra_state_attributes
            self.assertEqual(attributes["active_category"], "neutral")
            self.assertEqual(attributes["failure_status"], "none")
            self.assertEqual(attributes["failure_quality_blockers"], [])
            self.assertFalse(attributes["safety_blocked"])
            self.assertFalse(attributes["apply_blocked"])
            serialized = json.dumps(attributes)
            self.assertNotIn("sensor.fixture", serialized)

            unresolved_inputs = replace(
                inputs,
                indoor_temperature=contracts.InputObservation(
                    value=22.0,
                    source="sensor.fixture_indoor",
                    quality=contracts.InputQuality.STALE,
                    reason="matrix_stale",
                ),
            )
            entry.runtime_data.snapshot = entry.runtime_data.shadow.evaluate(unresolved_inputs)
            entry.runtime_data.coordinator._notify_snapshot_listeners()
            self.assertEqual(sensor.native_value, "normal")
            failure_attributes = sensor.extra_state_attributes
            self.assertEqual(
                failure_attributes["failure_reason"],
                None,
            )
            self.assertIn(
                "indoor_temperature",
                {issue["evidence"] for issue in failure_attributes["decision"]["issues"]},
            )
            self.assertNotIn("sensor.fixture", json.dumps(failure_attributes))

            writes_before_unload = sensor._state_write_count
            self.assertTrue(asyncio.run(module.async_unload_entry(hass, entry)))
            entry.runtime_data.coordinator._notify_snapshot_listeners()
            self.assertEqual(sensor._state_write_count, writes_before_unload)
            self.assertEqual(hass.entity_registry.entries, {})

    def test_reload_re_registers_panel_and_options_listener_reloads_entry(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            open_meteo = importlib.import_module("custom_components.blind_control.open_meteo")
            hass = _FakeHomeAssistant()
            hass.client_session = _FakeClientSession(
                {
                    "current": {
                        "direct_normal_irradiance_instant": 100,
                        "diffuse_radiation_instant": 25,
                    }
                }
            )
            now = datetime.now(UTC)
            old_binding = "sensor.owner_old"
            new_binding = "sensor.owner_new"
            old_url = open_meteo.suggested_open_meteo_url(50, 8)
            new_url = open_meteo.suggested_open_meteo_url(49, 9)
            entry = _FakeConfigEntry(
                "entry-1",
                data={
                    "input_bindings": {"bio_state": old_binding},
                    "open_meteo_api_url": old_url,
                },
            )
            hass._state_values = {
                old_binding: types.SimpleNamespace(
                    state="sleep",
                    attributes={},
                    last_updated=now,
                    last_changed=now,
                ),
                new_binding: types.SimpleNamespace(
                    state="awake",
                    attributes={},
                    last_updated=now,
                    last_changed=now,
                ),
            }

            asyncio.run(module.async_setup(hass, {}))
            asyncio.run(module.async_setup(hass, {}))
            self.assertEqual(len(hass.http.static_paths), 1)
            self.assertEqual(len(hass.data["frontend_panels"]), 1)

            asyncio.run(module.async_setup_entry(hass, entry))
            self.assertEqual(len(entry.update_listeners), 1)
            old_runtime = entry.runtime_data
            old_coordinator = old_runtime.coordinator
            old_provider = old_runtime.radiation_provider
            update_listener = entry.update_listeners[0]
            config_flow = importlib.import_module("custom_components.blind_control.config_flow")
            entry.options = config_flow._mapping_from_form(
                {
                    "open_meteo_api_url": new_url,
                    "core_state_bindings": {"bio_state": new_binding},
                },
                old_runtime.config,
            )

            asyncio.run(update_listener(hass, entry))

            event = sys.modules["homeassistant.helpers.event"]
            self.assertEqual(hass.config_entries.reloads, ["entry-1"])
            self.assertIsNot(entry.runtime_data, old_runtime)
            self.assertIsNot(entry.runtime_data.coordinator, old_coordinator)
            self.assertIsNot(entry.runtime_data.radiation_provider, old_provider)
            self.assertTrue(old_provider.shutdown)
            self.assertEqual(old_coordinator._unsubscribers, [])
            self.assertEqual(old_coordinator._snapshot_listeners, [])
            self.assertEqual(len(entry.update_listeners), 1)
            self.assertEqual(len(event.state_callbacks), 1)
            self.assertEqual(len(event.time_callbacks), 1)
            self.assertEqual(len(hass.client_session.requests), 2)
            self.assertEqual(hass.client_session.requests[-1][0], new_url)
            self.assertEqual(len(entry.runtime_data.radiation_provider.listeners), 3)
            self.assertEqual(
                set(hass.entity_registry.entries),
                {
                    "entry-1_shadow_status",
                    "blind_control_dni_instant",
                    "blind_control_diffuse_radiation_instant",
                },
            )
            self.assertEqual(
                entry.runtime_data.config.input_bindings, (("bio_state", new_binding),)
            )
            self.assertEqual(entry.runtime_data.snapshot.inputs["bio_state"]["value"], "awake")
            sensor = hass._entities_by_entry[entry.entry_id][0]
            self.assertEqual(sensor._snapshot.inputs["bio_state"]["value"], "awake")
            self.assertNotEqual(entry.runtime_data.snapshot.trace.master_mode.value, "manual")

    def test_websocket_contracts_read_and_admin_protect_options_write(self) -> None:
        with _home_assistant_imports():
            module = importlib.import_module("custom_components.blind_control")
            websocket = sys.modules["homeassistant.components.websocket_api"]
            websocket_api_module = importlib.import_module(
                "custom_components.blind_control.websocket_api"
            )
            hass = _FakeHomeAssistant()
            entry = _FakeConfigEntry(
                "entry-1",
                data={
                    "input_bindings": {"bio_state": "sensor.fixture_bio"},
                    "legacy_bindings": {"active_mode": "sensor.fixture_legacy"},
                },
            )
            asyncio.run(module.async_setup(hass, {}))
            asyncio.run(module.async_setup_entry(hass, entry))
            hass.config_entries.async_entries = lambda _domain: [entry]

            get_handler = next(
                handler
                for handler in websocket.commands
                if handler.websocket_schema["type"] == websocket_api_module.GET_SNAPSHOT
            )
            update_handler = next(
                handler
                for handler in websocket.commands
                if handler.websocket_schema["type"] == websocket_api_module.UPDATE_OPTIONS
            )
            update_schema = update_handler.websocket_schema
            required = {
                key.key
                for key in update_schema
                if isinstance(key, _SchemaKey) and key.required == "required"
            }
            optional = {
                key.key
                for key in update_schema
                if isinstance(key, _SchemaKey) and key.required == "optional"
            }
            self.assertIn("options", required)
            self.assertIn("entry_id", optional)
            self.assertTrue(get_handler.requires_admin)
            self.assertTrue(update_handler.requires_admin)

            denied = _FakeConnection(is_admin=False)
            asyncio.run(update_handler(hass, denied, {"id": 1, "options": {}}))
            self.assertEqual(denied.errors[0][1], "unauthorized")
            self.assertEqual(hass.config_entries.updates, [])

            denied_read = _FakeConnection(is_admin=False)
            asyncio.run(get_handler(hass, denied_read, {"id": 2, "entry_id": "entry-1"}))
            self.assertEqual(denied_read.errors[0][1], "unauthorized")
            self.assertEqual(denied_read.results, [])

            allowed = _FakeConnection(is_admin=True)
            asyncio.run(
                update_handler(
                    hass,
                    allowed,
                    {
                        "id": 3,
                        "entry_id": "entry-1",
                        "options": {
                            "apply_enabled": False,
                        },
                    },
                )
            )
            self.assertEqual(len(hass.config_entries.updates), 0)
            self.assertEqual(allowed.errors[0][1], "invalid_options")

            allowed_safe = _FakeConnection(is_admin=True)
            asyncio.run(
                update_handler(
                    hass,
                    allowed_safe,
                    {
                        "id": 4,
                        "entry_id": "entry-1",
                        "options": {"axis_inverted": True},
                    },
                )
            )
            self.assertEqual(len(hass.config_entries.updates), 1)
            self.assertTrue(hass.config_entries.updates[0][1]["axis_inverted"])
            binding_write = _FakeConnection(is_admin=True)
            asyncio.run(
                update_handler(
                    hass,
                    binding_write,
                    {
                        "id": 4,
                        "entry_id": "entry-1",
                        "options": {"input_bindings": {"activity_state": "sensor.new_activity"}},
                    },
                )
            )
            self.assertEqual(binding_write.errors[0][1], "invalid_options")
            self.assertEqual(len(hass.config_entries.updates), 1)
            intent_write = _FakeConnection(is_admin=True)
            asyncio.run(
                update_handler(
                    hass,
                    intent_write,
                    {
                        "id": 5,
                        "entry_id": "entry-1",
                        "options": {"binding_intents": {"activity_state": "intentionally_empty"}},
                    },
                )
            )
            self.assertEqual(intent_write.errors[0][1], "invalid_options")
            self.assertEqual(len(hass.config_entries.updates), 1)

            read_only = _FakeConnection(is_admin=True)
            asyncio.run(get_handler(hass, read_only, {"id": 6, "entry_id": "entry-1"}))
            self.assertEqual(read_only.results[0][0], 6)
            projection = read_only.results[0][1]
            self.assertEqual(projection["version"], "blind_control.ux.v6")
            serialized = json.dumps(projection)
            self.assertNotIn("sensor.fixture_bio", serialized)
            self.assertNotIn("sensor.fixture_legacy", serialized)
            self.assertNotIn("sensor.new_activity", serialized)
            self.assertTrue(
                next(
                    field
                    for group in projection["settings"]["binding_groups"]
                    for field in group["fields"]
                    if field["key"] == "bio_state"
                )["configured"]
            )

    def test_config_flow_is_singleton_and_persists_shadow_configuration(self) -> None:
        if importlib.util.find_spec("homeassistant") is None:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module("custom_components.blind_control.config_flow")

        with _home_assistant_imports():
            loaded = importlib.import_module("custom_components.blind_control.config_flow")
            flow = loaded.BlindControlConfigFlow()
            flow.hass = _FakeHomeAssistant()

            form = asyncio.run(flow.async_step_user())
            self.assertEqual(form["type"], "form")
            self.assertEqual(form["step_id"], "user")
            self.assertIn("window_azimuth", form["data_schema"].schema)
            provider_url = _schema_key(form["data_schema"], "open_meteo_api_url")
            self.assertIn("api.open-meteo.com", provider_url.options["default"])
            self.assertIn("position_waking_logical", form["data_schema"].schema)
            core_state_section = _schema_value(form["data_schema"], "core_state_bindings")
            self.assertIsInstance(core_state_section, _FakeSection)
            self.assertTrue(core_state_section.options["collapsed"])
            bio_selector = _schema_value(core_state_section.schema, "bio_state")
            self.assertIsInstance(bio_selector, _FakeSelector)
            self.assertEqual(bio_selector.config, {"entity": {}})
            opening_section = _schema_value(form["data_schema"], "opening_safety_cover_bindings")
            polarity_selector = _schema_value(opening_section.schema, "opening_safety_polarity")
            self.assertEqual(
                polarity_selector.config["select"]["translation_key"],
                "opening_safety_polarity",
            )
            visible_fields = sum(
                len(value.schema.schema) if isinstance(value, _FakeSection) else 1
                for value in form["data_schema"].schema.values()
            )
            self.assertEqual(visible_fields, 84)
            self.assertNotIn("runtime_mode", form["data_schema"].schema)
            self.assertNotIn("apply_owner", form["data_schema"].schema)
            unsafe_result = asyncio.run(
                flow.async_step_user({"runtime_mode": "live", "apply_owner": "blind_control"})
            )
            self.assertEqual(unsafe_result["type"], "form")
            self.assertEqual(unsafe_result["errors"]["base"], "invalid_configuration")

            suggested_schema = loaded._config_schema(
                suggestions=loaded.BindingSuggestions(
                    {"bio_state": "sensor.contract_bio"},
                    {"active_mode": "sensor.contract_legacy"},
                    "negative_unsafe",
                )
            )
            suggested_core = _schema_value(suggested_schema, "core_state_bindings")
            self.assertEqual(
                _schema_key(suggested_core.schema, "bio_state").options["description"][
                    "suggested_value"
                ],
                "sensor.contract_bio",
            )
            self.assertEqual(
                _schema_key(suggested_core.schema, "bio_state").options["default"],
                "sensor.contract_bio",
            )
            self.assertEqual(
                _schema_key(suggested_schema, "core_state_bindings").options["default"][
                    "bio_state"
                ],
                "sensor.contract_bio",
            )
            suggested_opening = _schema_value(suggested_schema, "opening_safety_cover_bindings")
            self.assertEqual(
                _schema_key(suggested_opening.schema, "opening_safety_polarity").options["default"],
                "negative_unsafe",
            )
            suggested_solar = _schema_value(suggested_schema, "solar_bindings")
            self.assertNotIn(
                "default",
                _schema_key(suggested_solar.schema, "expected_direct_radiation").options,
            )

            from custom_components.blind_control.config import BlindControlConfig

            required_defaults = {
                "bio_state": "sensor.contract_bio",
                "activity_state": "sensor.contract_activity",
                "day_state": "sensor.contract_day",
                "day_context": "sensor.contract_day_context",
                "away": "sensor.contract_presence",
                "private_time": "sensor.contract_private_time",
                "privacy": "sensor.contract_privacy",
                "opening_state": "sensor.contract_opening",
                "cover_available": "cover.contract_blind",
                "cover_ready": "binary_sensor.contract_blind_ready",
                "cover_position": "cover.contract_blind",
                "outdoor_lux": "sensor.contract_lux",
                "sun_elevation": "sun.contract_sun",
                "sun_azimuth": "sun.contract_sun",
                "indoor_temperature": "sensor.contract_indoor_temperature",
                "outdoor_temperature": "sensor.contract_weather",
                "opening_safe_for_blind": "binary_sensor.contract_opening_unsafe",
            }
            full_prefill_schema = loaded._config_schema(
                suggestions=loaded.BindingSuggestions(required_defaults, {}, "negative_unsafe")
            )
            prefill_sections = {
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
                    "cover_available",
                    "cover_ready",
                    "cover_position",
                    "opening_safe_for_blind",
                ),
                "solar_bindings": ("outdoor_lux", "sun_elevation", "sun_azimuth"),
                "temperature_weather_bindings": (
                    "indoor_temperature",
                    "outdoor_temperature",
                ),
            }
            for section, keys in prefill_sections.items():
                section_schema = _schema_value(full_prefill_schema, section)
                for key in keys:
                    if key == "privacy":
                        self.assertNotIn("default", _schema_key(section_schema.schema, key).options)
                        continue
                    self.assertEqual(
                        _schema_key(section_schema.schema, key).options["default"],
                        required_defaults[key],
                    )

            preserved = loaded._config_schema(
                config=BlindControlConfig.from_mapping(
                    {
                        "input_bindings": {"bio_state": "sensor.user_selected_bio"},
                        "binding_intents": {"bio_state": "bound"},
                    }
                ),
                suggestions=loaded.BindingSuggestions(
                    {"bio_state": "sensor.discovered_bio"}, {}, None
                ),
            )
            self.assertEqual(
                _schema_key(
                    _schema_value(preserved, "core_state_bindings").schema, "bio_state"
                ).options["default"],
                "sensor.user_selected_bio",
            )
            intentionally_empty = loaded._config_schema(
                config=BlindControlConfig.from_mapping(
                    {
                        "input_bindings": {"bio_state": "sensor.user_selected_bio"},
                        "binding_intents": {"bio_state": "intentionally_empty"},
                    }
                ),
                suggestions=loaded.BindingSuggestions(
                    {"bio_state": "sensor.discovered_bio"}, {}, None
                ),
            )
            empty_core = _schema_value(intentionally_empty, "core_state_bindings")
            empty_bio = _schema_key(empty_core.schema, "bio_state")
            self.assertNotIn("default", empty_bio.options)
            self.assertNotIn(
                "bio_state",
                _schema_key(intentionally_empty, "core_state_bindings").options["default"],
            )
            empty_mapping = loaded._mapping_from_form(
                {"apply_enabled": False},
                BlindControlConfig.from_mapping(
                    {
                        "input_bindings": {"bio_state": "sensor.user_selected_bio"},
                        "binding_intents": {"bio_state": "intentionally_empty"},
                    }
                ),
            )
            self.assertNotIn("bio_state", empty_mapping["input_bindings"])

            config = BlindControlConfig.defaults()
            user_input = {
                "window_azimuth": config.window_azimuth,
                "window_tilt": config.window_tilt,
                "axis_inverted": config.axis_inverted,
                "automation_enabled": config.automation_enabled,
                "apply_enabled": config.apply_enabled,
                "open_meteo_api_url": provider_url.options["default"],
                "heat_outdoor_threshold": config.heat_outdoor_threshold,
                "heat_indoor_threshold": config.heat_indoor_threshold,
                "heat_radiation_threshold": config.heat_radiation_threshold,
                "heat_confidence_threshold": config.heat_confidence_threshold,
                "cloud_shadow_lux_drop": config.cloud_shadow_lux_drop,
                "cloud_cover_threshold": config.cloud_cover_threshold,
                "model_lux_ratio": config.model_lux_ratio,
                "minimum_incidence_factor": config.minimum_incidence_factor,
                "model_lux_per_watt": config.model_lux_per_watt,
                "cold_lux_enter_threshold": config.cold_lux_enter_threshold,
                "cold_lux_exit_threshold": config.cold_lux_exit_threshold,
                "environment_hysteresis_ratio": config.environment_hysteresis_ratio,
                "environment_enter_seconds": config.environment_enter_seconds,
                "environment_exit_seconds": config.environment_exit_seconds,
                "movement_recovery_seconds": config.movement_recovery_seconds,
                "position_settle_seconds": config.position_settle_seconds,
                "movement_timeout_seconds": config.movement_timeout_seconds,
                "diffuse_lux_threshold": config.diffuse_lux_threshold,
                "night_lux_threshold": config.night_lux_threshold,
                "cold_outdoor_threshold": config.cold_outdoor_threshold,
                "cool_air_delta": config.cool_air_delta,
                "storm_precipitation_trend_threshold": config.storm_precipitation_trend_threshold,
                "storm_wind_trend_threshold": config.storm_wind_trend_threshold,
                "storm_pressure_drop_threshold": config.storm_pressure_drop_threshold,
                "storm_required_signals": config.storm_required_signals,
                "apply_cooldown_seconds": config.apply_cooldown_seconds,
                "position_tolerance": config.position_tolerance,
            }
            for name, profile in config.profiles:
                user_input[f"position_{name}_logical"] = profile.logical
            user_input["core_state_bindings"] = {"bio_state": "sensor.bound_bio"}
            user_input["opening_safety_cover_bindings"] = {
                "opening_safety_polarity": "positive_safe"
            }
            user_input["solar_bindings"] = {}
            user_input["temperature_weather_bindings"] = {}
            user_input["legacy_comparison_bindings"] = {}

            result = asyncio.run(flow.async_step_user(user_input))
            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(result["title"], "Blind Control")
            self.assertEqual(result["data"]["config_version"], 6)
            self.assertEqual(
                result["data"]["open_meteo_api_url"],
                provider_url.options["default"],
            )
            self.assertEqual(result["data"]["input_bindings"], {"bio_state": "sensor.bound_bio"})
            self.assertEqual(result["data"]["opening_safety_polarity"], "positive_safe")
            existing = BlindControlConfig.from_mapping(result["data"])
            cleared = loaded._mapping_from_form(
                {"core_state_bindings": {"bio_state": ""}}, existing
            )
            self.assertNotIn("bio_state", cleared["input_bindings"])
            self.assertEqual(cleared["binding_intents"]["bio_state"], "intentionally_empty")
            picker = _schema_value(core_state_section.schema, "bio_state")
            for empty in (None, ""):
                self.assertIsNone(picker(empty))
                validated_clear = loaded._mapping_from_form(
                    {"core_state_bindings": {"bio_state": picker(empty)}}, existing
                )
                self.assertNotIn("bio_state", validated_clear["input_bindings"])
                self.assertEqual(
                    validated_clear["binding_intents"]["bio_state"], "intentionally_empty"
                )
            self.assertEqual(picker("sensor.valid"), "sensor.valid")
            with self.assertRaises(ValueError):
                picker("invalid")
            options_flow = loaded.BlindControlOptionsFlow(
                _FakeConfigEntry("entry-1", data=result["data"])
            )
            options_form = asyncio.run(options_flow.async_step_init())
            calibration = {
                "cold_lux_enter_threshold": 350,
                "cold_lux_exit_threshold": 600,
                "environment_hysteresis_ratio": 0.75,
                "environment_enter_seconds": 15,
                "environment_exit_seconds": 150,
                "movement_recovery_seconds": 45,
            }
            options_flow.hass = _FakeHomeAssistant()
            saved = asyncio.run(options_flow.async_step_init(calibration))
            self.assertEqual(saved["type"], "create_entry")
            for key, value in calibration.items():
                self.assertEqual(saved["data"][key], value)
            self.assertIn("runtime_mode", options_form["data_schema"].schema)
            self.assertIn("apply_owner", options_form["data_schema"].schema)
            self.assertIsInstance(
                _schema_value(options_form["data_schema"], "solar_bindings"), _FakeSection
            )
            with self.assertRaises(_DuplicateEntry):
                asyncio.run(flow.async_step_user(user_input))


if __name__ == "__main__":
    unittest.main()
