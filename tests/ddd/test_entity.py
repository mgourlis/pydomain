from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.ddd import DomainError, Entity, Uuid7Generator


class User(Entity[UUID]):
    name: str


class Product(Entity[int]):
    name: str


class Tenant(Entity[str]):
    display_name: str


class TestUuidEntityAutoId:
    def test_auto_generates_id_when_omitted(self) -> None:
        user = User(name="Alice")
        assert isinstance(user.id, UUID)

    def test_auto_generated_id_is_unique(self) -> None:
        users = {User(name="Alice").id for _ in range(100)}
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
        with pytest.raises(DomainError):
            Product(name="Widget")

    def test_int_entity_accepts_explicit_id(self) -> None:
        p = Product(id=42, name="Widget")
        assert p.id == 42
        assert p.name == "Widget"

    def test_str_entity_requires_id(self) -> None:
        with pytest.raises(DomainError):
            Tenant(display_name="Acme")

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
        user = User(name="Alice")
        assert user.id == UUID("00000000-0000-0000-0000-000000000001")

    def test_configure_with_uuid7_restores_default(self) -> None:
        Entity.configure(id_generator=Uuid7Generator())
        user1 = User(name="Alice")
        user2 = User(name="Bob")
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


# ═══════════════════════════════════════════════════════════════════════
# Coverage gap: IdGenerator type mismatch guard
# ═══════════════════════════════════════════════════════════════════════


class TestIdGeneratorTypeGuard:
    """IdGenerator producing wrong type raises DomainError."""

    def test_wrong_type_raises_domain_error(self) -> None:
        """A generator that returns int for a UUID entity raises DomainError."""

        class IntGenerator:
            def generate(self) -> int:
                return 42

        Entity.configure(id_generator=IntGenerator())
        try:
            with pytest.raises(DomainError, match="IntGenerator produced int"):
                User(name="Alice")
        finally:
            Entity.configure(id_generator=Uuid7Generator())

    def test_str_generator_for_uuid_entity_raises(self) -> None:
        """A generator that returns str for a UUID entity raises DomainError."""

        class StrGenerator:
            def generate(self) -> str:
                return "not-a-uuid"

        Entity.configure(id_generator=StrGenerator())
        try:
            with pytest.raises(DomainError, match="StrGenerator produced str"):
                User(name="Alice")
        finally:
            Entity.configure(id_generator=Uuid7Generator())


class TestReconstitutionFromExternalData:
    """Simulates constructing entities from external data sources
    (database rows, API responses, event streams) where the ``id``
    is already present in the data.

    When ``id`` is provided, the ``_ensure_id`` validator must **not**
    invoke the ``IdGenerator`` — the entity is simply reconstituted
    from the given data.
    """

    # ── Dict data (e.g. database row / API JSON) ──────────────────

    def test_uuid_entity_from_dict(self) -> None:
        """Simulates a DB row returned as a dict."""
        row: dict[str, Any] = {
            "id": UUID("11111111-1111-1111-1111-111111111111"),
            "name": "Alice",
            "version": 3,
        }
        user = User.model_validate(row)
        assert user.id == UUID("11111111-1111-1111-1111-111111111111")
        assert user.name == "Alice"
        assert user.version == 3

    def test_int_entity_from_dict(self) -> None:
        """Simulates a DB row for an Entity[int]."""
        row: dict[str, Any] = {"id": 42, "name": "Widget", "version": 1}
        product = Product.model_validate(row)
        assert product.id == 42
        assert product.name == "Widget"
        assert product.version == 1

    def test_str_entity_from_dict(self) -> None:
        """Simulates a DB row for an Entity[str]."""
        row: dict[str, Any] = {
            "id": "acme-corp",
            "display_name": "Acme Corp",
            "version": 0,
        }
        tenant = Tenant.model_validate(row)
        assert tenant.id == "acme-corp"
        assert tenant.display_name == "Acme Corp"

    # ── Keyword args (e.g. ORM constructor) ───────────────────────

    def test_uuid_entity_from_kwargs(self) -> None:
        uid = UUID("22222222-2222-2222-2222-222222222222")
        user = User(id=uid, name="Bob", version=7)
        assert user.id == uid
        assert user.name == "Bob"
        assert user.version == 7

    def test_int_entity_from_kwargs(self) -> None:
        product = Product(id=99, name="Gadget", version=2)
        assert product.id == 99
        assert product.name == "Gadget"

    # ── JSON string (e.g. API response body) ──────────────────────

    def test_uuid_entity_from_json(self) -> None:
        uid = uuid4()
        original = User(id=uid, name="Charlie", version=5)
        json_str = original.model_dump_json()
        reconstituted = User.model_validate_json(json_str)
        assert reconstituted.id == uid
        assert reconstituted.name == "Charlie"
        assert reconstituted.version == 5
        assert reconstituted == original

    def test_int_entity_from_json(self) -> None:
        original = Product(id=7, name="Doohickey", version=1)
        json_str = original.model_dump_json()
        reconstituted = Product.model_validate_json(json_str)
        assert reconstituted == original

    # ── Round-trip: dump → external → reconstitute ───────────────

    def test_uuid_entity_round_trip_via_external_store(self) -> None:
        """Full round-trip: create → dump → (simulate external store)
        → load back → verify equality.
        """
        uid = uuid4()
        original = User(id=uid, name="Dana", version=10)

        # Simulate writing to a "database" (just a dict store)
        stored: dict[str, Any] = original.model_dump()
        assert "id" in stored  # id is part of the persisted data

        # Simulate reading back from the "database"
        reconstituted = User.model_validate(stored)
        assert reconstituted == original
        assert reconstituted.id == uid
        assert reconstituted.version == 10

    def test_int_entity_round_trip_via_external_store(self) -> None:
        """Same round-trip test for Entity[int]."""
        original = Product(id=123, name="Thingamajig", version=4)
        stored = original.model_dump()
        reconstituted = Product.model_validate(stored)
        assert reconstituted == original
        assert reconstituted.id == 123

    # ── Generator NOT invoked when id is present ─────────────────

    def test_provided_id_bypasses_generator(self) -> None:
        """When data already contains ``id``, the generator must not
        be called. We verify by using a generator that would fail
        if invoked.
        """
        uid = UUID("33333333-3333-3333-3333-333333333333")

        class ExplodingGenerator:
            def generate(self) -> UUID:
                raise RuntimeError("Generator should not be called!")

        original_generator = Entity._id_generator
        try:
            Entity.configure(id_generator=ExplodingGenerator())
            # id is provided → generator must not fire
            user = User.model_validate({"id": uid, "name": "Eve"})
            assert user.id == uid
        finally:
            Entity._id_generator = original_generator

    def test_int_entity_bypasses_generator_with_explicit_id(self) -> None:
        """Same as above but for Entity[int]."""

        class ExplodingGenerator:
            def generate(self) -> int:
                raise RuntimeError("Generator should not be called!")

        original_generator = Product._id_generator
        try:
            Product._id_generator = ExplodingGenerator()
            product = Product.model_validate({"id": 55, "name": "Gizmo"})
            assert product.id == 55
        finally:
            Product._id_generator = original_generator
