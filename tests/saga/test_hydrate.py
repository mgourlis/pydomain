"""Tests for hydrate_command() — module-path command rehydration."""

from __future__ import annotations

from pydomain.cqrs.saga.hydration import hydrate_command

from .conftest import CancelReservation, ReserveItems


class TestHydrateCommandSuccess:
    """Successful hydration from module path + type name + data."""

    def test_hydrate_reserve_items(self) -> None:
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="ReserveItems",
            data={"order_id": "ORD-1", "item_count": 5},
        )
        assert isinstance(result, ReserveItems)
        assert result.order_id == "ORD-1"
        assert result.item_count == 5

    def test_hydrate_cancel_reservation(self) -> None:
        result = hydrate_command(
            module_name=CancelReservation.__module__,
            command_type="CancelReservation",
            data={"order_id": "ORD-2"},
        )
        assert isinstance(result, CancelReservation)
        assert result.order_id == "ORD-2"

    def test_hydrate_preserves_all_fields(self) -> None:
        data = {"order_id": "ORD-3", "item_count": 10}
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="ReserveItems",
            data=data,
        )
        assert result is not None
        dumped = result.model_dump()
        for key, value in data.items():
            assert dumped[key] == value


class TestHydrateCommandMissingModule:
    """Missing or empty module_name returns None."""

    def test_empty_module_name(self) -> None:
        result = hydrate_command(
            module_name="",
            command_type="ReserveItems",
            data={"order_id": "ORD-1"},
        )
        assert result is None

    def test_nonexistent_module(self) -> None:
        result = hydrate_command(
            module_name="nonexistent.module.path",
            command_type="ReserveItems",
            data={"order_id": "ORD-1"},
        )
        assert result is None


class TestHydrateCommandMissingType:
    """Missing or unknown command_type returns None."""

    def test_empty_command_type(self) -> None:
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="",
            data={"order_id": "ORD-1"},
        )
        assert result is None

    def test_unknown_command_type(self) -> None:
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="NonExistentCommand",
            data={"order_id": "ORD-1"},
        )
        assert result is None


class TestHydrateCommandInvalidData:
    """Invalid data for the command type returns None."""

    def test_missing_required_field(self) -> None:
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="ReserveItems",
            data={},  # Missing order_id
        )
        assert result is None

    def test_wrong_field_type(self) -> None:
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="ReserveItems",
            data={"order_id": "ORD-1", "item_count": "not_a_number"},
        )
        # Pydantic may coerce or reject — hydrate_command catches Exception
        # Either way it should not crash
        assert result is None or isinstance(result, ReserveItems)


class TestHydrateCommandEdgeCases:
    """Edge cases for robustness."""

    def test_both_empty(self) -> None:
        result = hydrate_command(module_name="", command_type="", data={})
        assert result is None

    def test_data_is_empty_dict_for_command_with_defaults(self) -> None:
        """If the command has all defaults, empty data may succeed."""
        # ReserveItems has required fields, so this should fail
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="ReserveItems",
            data={},
        )
        assert result is None

    def test_extra_fields_in_data(self) -> None:
        """Extra fields are stripped — hydration succeeds."""
        result = hydrate_command(
            module_name=ReserveItems.__module__,
            command_type="ReserveItems",
            data={"order_id": "ORD-1", "item_count": 3, "extra": "ignored"},
        )
        assert isinstance(result, ReserveItems)
        assert result.order_id == "ORD-1"
        assert result.item_count == 3
