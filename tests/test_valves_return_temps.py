"""Offline tests for the valve output sensors (no Home Assistant).

The data has the shape of the live answers of /outs/, but room names are anonymized.

Run with: python tests/test_valves_return_temps.py
"""
from datetime import timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ha_stubs  # noqa: E402

TZ = timezone(timedelta(hours=1))
ha_stubs.install(TZ)

from custom_components.controme import helpers, sensor  # noqa: E402
from test_read_features import (  # noqa: E402
    SNAPSHOT_230, FakeCoordinator, by_unique_id, collect_entities, entity_ids, run, snapshot_data,
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


def temps_answer():
    return [{"id": floor_id, "etagenname": f"Etage {floor_id}", "raeume": [
        {"id": room_id, "name": f"Raum {room_id}", "temperatur": 21.0, "solltemperatur": 21.5,
         "sensoren": []} for room_id in rooms]} for floor_id, rooms in HOUSE.items()]


def house_data():
    """Coordinator data after a full update: /temps/ with "ausgang" merged."""
    data = temps_answer()
    for floor in data:
        for room in floor["raeume"]:
            room["ausgang"] = dict(OUTPUTS[room["id"]])
    return data


def valve_outputs(entities):
    return [e for e in entities if isinstance(e, sensor.ContromeValveOutputSensor)]


# Acceptance: 20 valve outputs, no duplicate IDs
def test_house_creates_20_valves():
    entities = collect_entities(house_data())
    ids = entity_ids(entities)
    assert len(valve_outputs(entities)) == 20
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


# Snapshot of 2.3.0 stays unchanged with the valve outputs added
def test_snapshot_unchanged_with_new_sensors():
    ids = entity_ids(collect_entities(snapshot_data()))
    missing = {uid: eid for uid, eid in SNAPSHOT_230.items() if ids.get(uid) != eid}
    assert not missing, missing
    new = {uid: eid for uid, eid in ids.items() if uid not in SNAPSHOT_230}
    assert not set(new.values()) & set(SNAPSHOT_230.values())
    assert new["1_1_10_valve_12"] == "sensor.controme_room_10_valve_12"


if __name__ == "__main__":
    sys.exit(1 if run(dict(globals())) else 0)
