"""Offline tests for the valve output and return temperature sensors (no Home Assistant).

The data has the shape of the live answers of /outs/ and /rltemps/<room>/, but room
names and 1-Wire IDs are anonymized.

Run with: python tests/test_valves_return_temps.py
"""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

TZ = timezone(timedelta(hours=1))
ha_stubs.install(TZ)

from custom_components.controme import coordinator as coordinator_module, helpers, sensor  # noqa: E402
from test_read_features import (  # noqa: E402
    BASE, LOGS, SNAPSHOT_230, FakeCoordinator, FakeLoop, FakeResponse, SESSION, by_unique_id,
    collect_entities, entity_ids, make_entry, reset_session, run, snapshot_data,
)

# Output counts per room like the tested system: 2 floors, 11 rooms, 20 outputs
HOUSE = {
    1: {1: 2, 2: 1, 3: 2, 4: 3, 6: 1, 7: 1},
    2: {8: 2, 9: 2, 10: 1, 11: 4, 12: 1},
}
# Output numbers of the rooms; 1, 2, 3, 4, ... occur on both floors
OUTPUTS = {
    1: {"3": 0, " 4": 0}, 2: {"7": 0}, 3: {"1": 99, " 2": 99}, 4: {"9": 61, " 8": 99, " 12": 74},
    6: {"6": 52}, 7: {"5": 44},
    8: {"1": 99, " 2": 89}, 9: {"9": 99, " 10": 86}, 10: {"8": 71},
    11: {" 6": 0, "5": 0, " 7": 0, " 4": 0}, 12: {"3": 60},
}


def transmission(minutes_ago=1):
    """A /rltemps/ timestamp ("HH:MM:SS DD.MM.YYYY", local time) minutes_ago in the past."""
    return (datetime.now(TZ) - timedelta(minutes=minutes_ago)).strftime("%H:%M:%S %d.%m.%Y")


def wire_id(room_id, index):
    return f"28_00_00_00_00_00_{room_id:02x}_{index:02x}"


def rltemps(room_id, count, minutes_ago=1):
    """The /rltemps/ answer of a room with count sensors, "ausgang" null like on the live system."""
    return [{"id": room_id * 10 + index, "name": wire_id(room_id, index),
             "beschreibung": f"Raum {room_id} {index}", "wert": 25.0 + index / 10,
             "letzte_uebertragung": transmission(minutes_ago), "ausgang": None}
            for index in range(1, count + 1)]


def temps_answer():
    return [{"id": floor_id, "etagenname": f"Etage {floor_id}", "raeume": [
        {"id": room_id, "name": f"Raum {room_id}", "temperatur": 21.0, "solltemperatur": 21.5,
         "sensoren": []} for room_id in rooms]} for floor_id, rooms in HOUSE.items()]


def outs_answer():
    return [{"id": floor_id, "etagenname": f"Etage {floor_id}", "raeume": [
        {"id": room_id, "name": f"Raum {room_id}", "ausgang": dict(OUTPUTS[room_id])}
        for room_id in rooms]} for floor_id, rooms in HOUSE.items()]


def house_data():
    """Coordinator data after a full update: /temps/ with "ausgang" and "rltemps" merged."""
    data = temps_answer()
    for floor in data:
        for room in floor["raeume"]:
            room["ausgang"] = dict(OUTPUTS[room["id"]])
            room["rltemps"] = rltemps(room["id"], HOUSE[floor["id"]][room["id"]])
    return data


def make_real_coordinator():
    real = coordinator_module.ContromeDataUpdateCoordinator(None, make_entry(), BASE, "1")
    real.hass = type("Hass", (), {"loop": FakeLoop()})()
    # The scene list is not part of these tests, it is read again after 10 minutes
    real._scenes_read_at = real.hass.loop.now
    return real


def update(real, rltemps_responses):
    """Run one coordinator update with the given /rltemps/ answers (in room order)."""
    reset_session()
    SESSION.get_responses.extend([FakeResponse(payload=temps_answer()),
                                  FakeResponse(payload=outs_answer()), *rltemps_responses])
    return asyncio.run(real._async_update_data())


def all_rltemps_responses(minutes_ago=1):
    return [FakeResponse(payload=rltemps(room_id, count, minutes_ago))
            for rooms in HOUSE.values() for room_id, count in rooms.items()]


def valve_outputs(entities):
    return [e for e in entities if isinstance(e, sensor.ContromeValveOutputSensor)]


def return_temps(entities):
    return [e for e in entities if isinstance(e, sensor.ContromeReturnTemperatureSensor)]


# Acceptance: 20 valve outputs and 20 return sensors, no duplicate IDs
def test_house_creates_20_valves_and_20_return_sensors():
    entities = collect_entities(house_data())
    ids = entity_ids(entities)
    assert len(valve_outputs(entities)) == 20
    assert len(return_temps(entities)) == 20
    assert len(set(ids.values())) == len(ids), "duplicate entity_id"


# Keys with a leading space are stripped
def test_output_keys_with_spaces():
    entities = by_unique_id(collect_entities(house_data()))
    living = [entities[f"1_1_4_valve_{n}"] for n in ("8", "9", "12")]
    assert [e._attr_native_value for e in living] == [99.0, 61.0, 74.0]
    assert [e.entity_id for e in living] == [
        "sensor.controme_raum_4_valve_8", "sensor.controme_raum_4_valve_9",
        "sensor.controme_raum_4_valve_12"]
    assert living[0]._attr_translation_placeholders == {"output": "8"}
    assert living[0].extra_state_attributes == {"room_id": 4, "floor_id": 1, "house_id": "1",
                                                "output": "8"}
    assert not any(" " in uid for uid in entities)


# The same output number on two floors gives two entities, the floor is in the unique_id
def test_same_output_on_two_floors():
    entities = by_unique_id(collect_entities(house_data()))
    assert entities["1_1_3_valve_1"]._attr_native_value == 99.0
    assert entities["1_2_8_valve_1"]._attr_native_value == 99.0
    assert entities["1_1_4_valve_9"]._attr_native_value == 61.0
    assert entities["1_2_9_valve_9"]._attr_native_value == 99.0
    assert entities["1_1_1_valve_4"]._attr_native_value == 0.0
    assert entities["1_2_11_valve_4"]._attr_native_value == 0.0


def test_valve_output_description_and_device():
    data = house_data()
    entities = collect_entities(data)
    output = by_unique_id(entities)["1_1_4_valve_9"]
    description = output.entity_description
    assert description.native_unit_of_measurement == "%"
    assert description.state_class == "MEASUREMENT"
    assert description.icon == "mdi:valve"
    assert description.entity_registry_enabled_default is not False
    assert description.translation_key == "valve_output"
    # Same room device as temperature and target
    current = by_unique_id(entities)["1_1_4_current"]
    assert output._attr_device_info == current._device_info


def test_valve_output_values_and_range():
    for value, expected in ((0, 0.0), (99, 99.0), (100, 100.0), ("74", 74.0), (101, None), (-1, None),
                            (None, None), (True, None), ("aus", None), ("nan", None)):
        assert helpers.output_value(value) == expected, value
    data = house_data()
    coordinator = FakeCoordinator(data)
    output = by_unique_id(collect_entities(data, coordinator))["1_1_6_valve_6"]
    assert output.available and output._attr_native_value == 52.0
    data[0]["raeume"][4]["ausgang"] = {"6": 101}
    output._handle_coordinator_update()
    assert not output.available
    data[0]["raeume"][4]["ausgang"] = {" 6": 30}
    output._handle_coordinator_update()
    assert output.available and output._attr_native_value == 30.0
    # /outs/ failed: no "ausgang" in the room, the sensor is unavailable
    del data[0]["raeume"][4]["ausgang"]
    output._handle_coordinator_update()
    assert not output.available


def test_rooms_without_outputs_get_no_valve_sensors():
    data = house_data()
    del data[0]["raeume"][0]["ausgang"]
    data[0]["raeume"][1]["ausgang"] = {}
    entities = collect_entities(data)
    assert not any(uid.startswith(("1_1_1_valve", "1_1_2_valve")) for uid in entity_ids(entities))
    assert len(valve_outputs(entities)) == 17


# Several return sensors per room, named after their description
def test_several_return_sensors_per_room():
    entities = collect_entities(house_data())
    bedroom = [e for e in return_temps(entities) if e._room_id == 11]
    assert len(bedroom) == 4
    first = by_unique_id(entities)[f"1_rltemp_{wire_id(11, 1)}"]
    assert first.entity_id == f"sensor.controme_raum_11_return_{wire_id(11, 1)}"
    assert first._attr_translation_placeholders == {"sensor": "Raum 11 1"}
    assert first._attr_native_value == 25.1 and first.available
    description = first.entity_description
    assert description.device_class == "TEMPERATURE"
    assert description.native_unit_of_measurement == "CELSIUS"
    assert description.state_class == "MEASUREMENT"
    assert description.translation_key == "return_temperature"
    assert first._attr_device_info == by_unique_id(entities)["1_2_11_current"]._device_info
    attributes = first.extra_state_attributes
    assert attributes["sensor_id"] == wire_id(11, 1)
    assert datetime.fromisoformat(attributes["last_transmission"]).tzinfo is not None
    # "ausgang" is null: no output attribute, nothing is guessed
    assert "output" not in attributes
    assert first._unrecorded_attributes == frozenset({"last_transmission"})


def test_description_unchanged_and_fallback():
    data = house_data()
    sensors = data[0]["raeume"][3]["rltemps"]
    sensors[0]["beschreibung"] = "Ruam 4 1"
    sensors[1]["beschreibung"] = ""
    del sensors[2]["beschreibung"]
    entities = by_unique_id(collect_entities(data))
    assert entities[f"1_rltemp_{wire_id(4, 1)}"]._attr_translation_placeholders == {"sensor": "Ruam 4 1"}
    for index in (2, 3):
        assert entities[f"1_rltemp_{wire_id(4, index)}"]._attr_translation_placeholders == {
            "sensor": wire_id(4, index)}


def test_output_attribute_only_when_reported():
    data = house_data()
    data[0]["raeume"][3]["rltemps"][0]["ausgang"] = 9
    entities = by_unique_id(collect_entities(data))
    assert entities[f"1_rltemp_{wire_id(4, 1)}"].extra_state_attributes["output"] == 9
    assert "output" not in entities[f"1_rltemp_{wire_id(4, 2)}"].extra_state_attributes


def test_rooms_without_return_sensors():
    data = house_data()
    data[0]["raeume"][0]["rltemps"] = []
    del data[0]["raeume"][1]["rltemps"]
    data[0]["raeume"][2]["rltemps"] = [None, {"wert": 20.0}, {"name": "  ", "wert": 20.0}]
    entities = collect_entities(data)
    assert not any(e._room_id in (1, 2, 3) for e in return_temps(entities))
    assert len(return_temps(entities)) == 15


def test_stale_return_sensor_unavailable():
    data = house_data()
    coordinator = FakeCoordinator(data)
    entity = by_unique_id(collect_entities(data, coordinator))[f"1_rltemp_{wire_id(7, 1)}"]
    assert entity.available
    room = data[0]["raeume"][5]
    for minutes_ago, available in ((14, True), (16, False), (2, True), (-5, True)):
        room["rltemps"][0]["letzte_uebertragung"] = transmission(minutes_ago)
        entity._handle_coordinator_update()
        assert entity.available is available, minutes_ago
    # Without a parseable timestamp the value is never stale
    room["rltemps"][0]["letzte_uebertragung"] = ""
    entity._handle_coordinator_update()
    assert entity.available and entity.extra_state_attributes["last_transmission"] is None


def test_stale_threshold_option_is_used():
    data = house_data()
    data[0]["raeume"][5]["rltemps"][0]["letzte_uebertragung"] = transmission(30)
    entry = make_entry()
    entry.options = {"stale_threshold": 60}
    room = dict(data[0]["raeume"][5], floor_id=1, room_id=7)
    entity = sensor.ContromeReturnTemperatureSensor(
        FakeCoordinator(data), entry, room, room["rltemps"][0], {})
    assert entity.available


def test_return_sensor_value_and_removal():
    data = house_data()
    coordinator = FakeCoordinator(data)
    entity = by_unique_id(collect_entities(data, coordinator))[f"1_rltemp_{wire_id(7, 1)}"]
    sensors = data[0]["raeume"][5]["rltemps"]
    for value, expected in (("26.19", 26.19), (0, 0.0), ("kein Sensor", None), (None, None), ("nan", None)):
        sensors[0]["wert"] = value
        entity._handle_coordinator_update()
        assert entity.available is (expected is not None), value
        if expected is not None:
            assert entity._attr_native_value == expected
    # The sensor moved to another room: still found by its 1-Wire ID
    data[1]["raeume"][0]["rltemps"].append(sensors.pop(0) | {"wert": 22.5})
    entity._handle_coordinator_update()
    assert entity.available and entity._attr_native_value == 22.5
    # /rltemps/ failed for all rooms: unavailable
    for floor in data:
        for room in floor["raeume"]:
            room.pop("rltemps", None)
    entity._handle_coordinator_update()
    assert not entity.available


def test_one_entity_per_1wire_id():
    data = house_data()
    data[1]["raeume"][0]["rltemps"].append(dict(data[0]["raeume"][0]["rltemps"][0]))
    entities = collect_entities(data)
    entity_ids(entities)
    assert len(return_temps(entities)) == 20


# Return sensors with a "Rücklauf" description keep their 2.4.0 entity, no second one
def test_legacy_return_sensors_not_duplicated():
    data = house_data()
    room = data[0]["raeume"][0]
    room["sensoren"] = [{"name": wire_id(1, 1), "beschreibung": "Rücklauf Raum 1", "wert": 25.1,
                         "raumtemperatursensor": False}]
    ids = entity_ids(collect_entities(data))
    assert f"1_1_1_return_{wire_id(1, 1)}" in ids
    assert f"1_rltemp_{wire_id(1, 1)}" not in ids
    assert f"1_rltemp_{wire_id(1, 2)}" in ids


# Snapshot of 2.3.0 stays unchanged with outputs and return sensors added
def test_snapshot_unchanged_with_new_sensors():
    data = snapshot_data()
    data[0]["raeume"][0]["rltemps"] = [{"name": "28_00_00_00_00_00_00_02", "beschreibung": "Rücklauf",
                                        "wert": 30.0, "letzte_uebertragung": transmission(), "ausgang": None},
                                       {"name": "28_00_00_00_00_00_00_99", "beschreibung": "Neu",
                                        "wert": 31.0, "letzte_uebertragung": transmission(), "ausgang": None}]
    ids = entity_ids(collect_entities(data))
    missing = {uid: eid for uid, eid in SNAPSHOT_230.items() if ids.get(uid) != eid}
    assert not missing, missing
    new = {uid: eid for uid, eid in ids.items() if uid not in SNAPSHOT_230}
    assert not set(new.values()) & set(SNAPSHOT_230.values())
    assert new["1_1_10_valve_12"] == "sensor.controme_room_10_valve_12"
    assert new["1_rltemp_28_00_00_00_00_00_00_99"] == "sensor.controme_room_10_return_28_00_00_00_00_00_00_99"
    # The legacy return sensor of the same 1-Wire ID gets no second entity
    assert "1_rltemp_28_00_00_00_00_00_00_02" not in new


# Coordinator: /rltemps/ room by room, read only, errors never fail the update
def rltemps_urls():
    return [url for url in SESSION.gets if "/rltemps/" in url]


def test_coordinator_reads_rltemps_per_room():
    real = make_real_coordinator()
    data = update(real, all_rltemps_responses())
    room_ids = [room_id for rooms in HOUSE.values() for room_id in rooms]
    assert rltemps_urls() == [f"http://controme.local/get/json/v1/1/rltemps/{room_id}/" for room_id in room_ids]
    assert all(timeout.total == 5 for url, timeout in zip(SESSION.gets, SESSION.get_timeouts)
               if "/rltemps/" in url)
    assert SESSION.posts == []
    entities = collect_entities(data)
    assert len(return_temps(entities)) == 20 and len(valve_outputs(entities)) == 20


def test_coordinator_rltemps_errors():
    import aiohttp
    responses = all_rltemps_responses()
    # 500 and broken JSON only skip that room
    responses[0] = FakeResponse(500)
    responses[1] = FakeResponse(payload=ValueError("no JSON"))
    responses[2] = FakeResponse(payload={"x": 1})
    real = make_real_coordinator()
    data = update(real, responses)
    assert len(rltemps_urls()) == 11
    assert [room["id"] for floor in data for room in floor["raeume"] if "rltemps" not in room] == [1, 2, 3]
    # Missing endpoint, missing rights, timeout or connection error: the remaining rooms are skipped
    for error in (FakeResponse(404), FakeResponse(403), TimeoutError(), aiohttp.ClientError("x")):
        data = update(make_real_coordinator(), [error])
        assert len(rltemps_urls()) == 1, error
        assert not any("rltemps" in room for floor in data for room in floor["raeume"])
        # The rest of the update is unchanged
        assert all("ausgang" in room for floor in data for room in floor["raeume"])
    assert SESSION.posts == []


def test_coordinator_rltemps_problem_logged_once():
    real = make_real_coordinator()
    LOGS.messages.clear()
    update(real, [FakeResponse(404)])
    update(real, [FakeResponse(404)])
    warnings = [m for m in LOGS.messages if m.startswith("Could not read the return sensors (/rltemps/), their")]
    assert len(warnings) == 1, LOGS.messages
    update(real, all_rltemps_responses())
    assert any(m == "The return sensors (/rltemps/) can be read again" for m in LOGS.messages)
    # Nothing secret in the logs
    assert not any("password" in m or "p@" in m for m in LOGS.messages)


def test_coordinator_skips_rooms_without_numeric_id():
    real = make_real_coordinator()
    reset_session()
    answer = [{"id": 1, "raeume": [{"id": "x/../"}, {"id": True}, {"name": "ohne ID"}, {"id": 5}]}]
    SESSION.get_responses.extend([FakeResponse(payload=answer), FakeResponse(payload=[]),
                                  FakeResponse(payload=[])])
    asyncio.run(real._async_update_data())
    assert rltemps_urls() == ["http://controme.local/get/json/v1/1/rltemps/5/"]


if __name__ == "__main__":
    sys.exit(1 if run(dict(globals())) else 0)
