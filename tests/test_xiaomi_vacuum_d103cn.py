from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from custom_components.xiaomi_miot.core.device_customizes import DEVICE_CUSTOMIZES
from custom_components.xiaomi_miot.vacuum import (
    MiotVacuumEntity,
    MiotXiaomiVacuumEntity,
)

MODEL = "xiaomi.vacuum.d103cn"

CLOUD_HOMES = [
    {"id": 1, "roomlist": [{"id": "101", "name": "Kitchen"}, {"id": "102", "name": ""}]},
    {"id": 2, "roomlist": [{"id": "201", "name": "Country house"}]},
]


def make_entity(make_device, load_miot_spec, customizes=None):
    spec = load_miot_spec(f"{MODEL}.json")
    device = make_device(spec, model=MODEL, customizes=customizes)
    return MiotXiaomiVacuumEntity({"device": device, "name": "Vacuum"}, spec.get_service("vacuum"))


def mock_cloud(home_id=1):
    cloud = MagicMock()
    cloud.async_get_homerooms = AsyncMock(return_value=CLOUD_HOMES)
    cloud.async_get_devices_by_key = AsyncMock(return_value={"test-device": {"home_id": home_id}})
    return cloud


def test_customizes_match_spec(load_miot_spec):
    spec = load_miot_spec(f"{MODEL}.json")
    cus = DEVICE_CUSTOMIZES[MODEL]
    props = [p for s in spec.services.values() for p in s.properties.values()]
    actions = {a.name for s in spec.services.values() for a in s.actions.values()}

    for chunk in cus["chunk_coordinators"]:
        for pattern in chunk["props"].split(","):
            assert any(p.in_list([pattern]) for p in props), pattern
    for name in cus["button_actions"].split(","):
        assert name in actions, name
    for name in cus["sensor_properties"].split(","):
        assert any(p.name == name for p in props), name


async def test_room_sweep_action_params(make_device, load_miot_spec):
    entity = make_entity(make_device, load_miot_spec)
    assert entity._act_room_sweep.iid == 3

    with patch.object(entity, "async_call_action", AsyncMock(return_value=True)) as call:
        assert await entity.async_start_room_sweep(room_id="101", attr="room_101")
        assert call.await_args.kwargs["params"] == [{"piid": 4, "value": "101"}]

        await entity.async_send_command("start_room_sweep", [101, "102"])
        assert call.await_args.kwargs["params"] == [{"piid": 4, "value": "101,102"}]

        await entity.async_send_command("start_room_sweep", {"room_ids": ["201"]})
        assert call.await_args.kwargs["params"] == [{"piid": 4, "value": "201"}]

        call.reset_mock()
        assert await entity.async_start_room_sweep(room_id=None) is False
        call.assert_not_awaited()


async def test_other_commands_fall_back_to_miio(make_device, load_miot_spec):
    entity = make_entity(make_device, load_miot_spec)
    with patch.object(MiotVacuumEntity, "async_send_command", AsyncMock(return_value="ok")) as base:
        assert await entity.async_send_command("app_start", None) == "ok"
        base.assert_awaited_once()


async def test_cloud_rooms_filtered_by_home(make_device, load_miot_spec):
    entity = make_entity(make_device, load_miot_spec)
    with patch.object(MiotXiaomiVacuumEntity, "xiaomi_cloud", new_callable=PropertyMock) as cloud:
        cloud.return_value = mock_cloud(home_id=1)
        assert await entity.async_get_rooms() == {"101": "Kitchen", "102": "Room 102"}

        cloud.return_value = None
        assert await entity.async_get_rooms() == {}


async def test_custom_room_mapping_overrides_cloud(make_device, load_miot_spec):
    customizes = {**DEVICE_CUSTOMIZES[MODEL], "room_mapping": '{"10": "Hall", "11": "Bedroom"}'}
    entity = make_entity(make_device, load_miot_spec, customizes=customizes)
    with patch.object(MiotXiaomiVacuumEntity, "xiaomi_cloud", new_callable=PropertyMock) as cloud:
        cloud.return_value = mock_cloud()
        assert await entity.async_get_rooms() == {"10": "Hall", "11": "Bedroom"}


@pytest.mark.parametrize("rooms", [{}, {"10": "Hall"}])
async def test_setup_room_buttons(make_device, load_miot_spec, rooms):
    entity = make_entity(make_device, load_miot_spec)
    add_buttons = MagicMock()
    entity.device.entry.adders["button"] = add_buttons

    with patch.object(entity, "async_get_rooms", AsyncMock(return_value=rooms)):
        await entity.async_setup_room_buttons()
        await entity.async_setup_room_buttons()

    if not rooms:
        add_buttons.assert_not_called()
        assert entity._rooms_retry_at is not None
        return
    add_buttons.assert_called_once()
    (buttons,), _ = add_buttons.call_args
    assert [b._attr for b in buttons] == ["room_10"]
    assert buttons[0]._state_attrs == {"room_id": "10", "room_name": "Hall"}
    assert entity._state_attrs["room_mapping"] == [["10", "Hall"]]
    assert entity._rooms_retry_at is None
