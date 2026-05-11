from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pydomain.ddd import Entity, Uuid7Generator


class User(Entity[UUID]):
    name: str


class Product(Entity[int]):
    name: str


class Tenant(Entity[str]):
    display_name: str


class TestUuidEntityAutoId:
    def test_auto_generates_id_when_omitted(self) -> None:
        user = User(name="Alice")  # type: ignore[call-arg]
        assert isinstance(user.id, UUID)

    def test_auto_generated_id_is_unique(self) -> None:
        users = {User(name="Alice").id for _ in range(100)}  # type: ignore[call-arg]
        assert len(users) == 100

    def test_accepts_explicit_id(self) -> None:
        uid = uuid4()
        user = User(id=uid, name="Alice")
        assert user.id == uid

    def test_explicit_id_preserves_auto_generated_flag(self) -> None:
        uid = uuid4()
        user = User(id=uid, name="Alice")
        assert user.name == "Alice"


class TestNonUuidEntityRequiresExplicitId:
    def test_int_entity_requires_id(self) -> None:
        with pytest.raises(ValidationError):
            Product(name="Widget")  # type: ignore[call-arg]

    def test_int_entity_accepts_explicit_id(self) -> None:
        p = Product(id=42, name="Widget")
        assert p.id == 42
        assert p.name == "Widget"

    def test_str_entity_requires_id(self) -> None:
        with pytest.raises(ValidationError):
            Tenant(display_name="Acme")  # type: ignore[call-arg]

    def test_str_entity_accepts_explicit_id(self) -> None:
        t = Tenant(id="acme-corp", display_name="Acme")
        assert t.id == "acme-corp"
        assert t.display_name == "Acme"


class TestIdentityBasedEquality:
    def test_equal_when_same_id_and_type(self) -> None:
        uid = uuid4()
        a = User(id=uid, name="Alice")
        b = User(id=uid, name="Bob")
        assert a == b

    def test_not_equal_when_different_id(self) -> None:
        a = User(id=uuid4(), name="Alice")
        b = User(id=uuid4(), name="Alice")
        assert a != b

    def test_not_equal_when_different_type(self) -> None:
        uid = uuid4()
        user = User(id=uid, name="Alice")
        other = User(id=uid, name="Bob")
        assert type(user) is type(other)
        # Same type but different names → equal by identity
        assert user == other

    def test_different_entity_types_not_equal(self) -> None:
        class AdminUser(Entity[UUID]):
            name: str

        uid = uuid4()
        user = User(id=uid, name="Alice")
        admin = AdminUser(id=uid, name="Alice")
        assert user != admin

    def test_not_equal_to_non_entity(self) -> None:
        user = User(id=uuid4(), name="Alice")
        assert user != {"id": user.id, "name": "Alice"}

    def test_not_equal_to_none(self) -> None:
        user = User(id=uuid4(), name="Alice")
        assert user is not None


class TestHashability:
    def test_same_id_has_same_hash(self) -> None:
        uid = uuid4()
        a = User(id=uid, name="Alice")
        b = User(id=uid, name="Bob")
        assert hash(a) == hash(b)

    def test_can_be_used_in_set(self) -> None:
        uid = uuid4()
        s = {
            User(id=uid, name="Alice"),
            User(id=uid, name="Bob"),  # same id → dedup
            User(id=uuid4(), name="Charlie"),
        }
        assert len(s) == 2

    def test_can_be_used_as_dict_key(self) -> None:
        uid = uuid4()
        d: dict[Any, str] = {
            User(id=uid, name="Alice"): "admin",
        }
        assert d[User(id=uid, name="Bob")] == "admin"


class TestMutability:
    def test_can_change_field(self) -> None:
        user = User(id=uuid4(), name="Alice")
        user.name = "Bob"
        assert user.name == "Bob"

    def test_can_change_version(self) -> None:
        user = User(id=uuid4(), name="Alice")
        user.version = 5
        assert user.version == 5

    def test_can_change_id(self) -> None:
        """Id is mutable (frozen=False) but should be treated as
        immutable after creation — changing it while the entity is
        in a set/dict causes hash inconsistency.
        """
        user = User(id=uuid4(), name="Alice")
        new_id = uuid4()
        user.id = new_id
        assert user.id == new_id


class TestVersionField:
    def test_defaults_to_zero(self) -> None:
        user = User(id=uuid4(), name="Alice")
        assert user.version == 0

    def test_can_set_version_on_creation(self) -> None:
        user = User(id=uuid4(), name="Alice", version=3)
        assert user.version == 3


class TestConfigure:
    def test_configure_affects_new_entities(self) -> None:
        class FixedIdGenerator:
            def generate(self) -> UUID:
                return UUID("00000000-0000-0000-0000-000000000001")

        Entity.configure(id_generator=FixedIdGenerator())
        user = User(name="Alice")  # type: ignore[call-arg]
        assert user.id == UUID("00000000-0000-0000-0000-000000000001")

    def test_configure_with_uuid7_restores_default(self) -> None:
        Entity.configure(id_generator=Uuid7Generator())
        user1 = User(name="Alice")  # type: ignore[call-arg]
        user2 = User(name="Bob")  # type: ignore[call-arg]
        assert user1.id != user2.id


class TestSerialization:
    def test_model_dump_round_trip(self) -> None:
        uid = uuid4()
        original = User(id=uid, name="Alice")
        data = original.model_dump()
        restored = User.model_validate(data)
        assert restored == original

    def test_model_dump_json_round_trip(self) -> None:
        uid = uuid4()
        original = User(id=uid, name="Alice")
        json_str = original.model_dump_json()
        restored = User.model_validate_json(json_str)
        assert restored == original

    def test_model_dump_includes_all_fields(self) -> None:
        uid = uuid4()
        user = User(id=uid, name="Alice")
        data = user.model_dump()
        assert data == {"id": uid, "name": "Alice", "version": 0}


class TestInheritance:
    def test_subclass_adds_fields(self) -> None:
        class ExtendedUser(User):
            email: str

        user = ExtendedUser(id=uuid4(), name="Alice", email="alice@example.com")
        assert user.email == "alice@example.com"
        assert user.name == "Alice"

    def test_subclass_preserves_id_behavior(self) -> None:
        class ExtendedUser(User):
            email: str

        uid = uuid4()
        a = ExtendedUser(id=uid, name="Alice", email="a@x.com")
        b = ExtendedUser(id=uid, name="Bob", email="b@x.com")
        assert a == b

    def test_entity_repr(self) -> None:
        uid = uuid4()
        user = User(id=uid, name="Alice")
        r = repr(user)
        assert str(uid) in r
        assert "name='Alice'" in r
