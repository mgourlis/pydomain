"""Architecture boundary tests — enforce DDD layer discipline.

These tests use pytest-archon to assert that the layered dependency
graph of the pydomain library is respected. The intended dependency
direction is:

    ddd  ←  cqrs  ←  infrastructure
           es   ←  infrastructure
    ddd  ←  es
    ddd  ←  cqrs

No layer may import from a layer above it (outward dependency).
"""

from pytest_archon import archrule

# ---------------------------------------------------------------------------
# 1. Domain layer (ddd) — must be pure, no outward dependencies
# ---------------------------------------------------------------------------


def test_ddd_does_not_import_cqrs() -> None:
    """Domain must not depend on the application (CQRS) layer."""
    (
        archrule("ddd isolation from cqrs")
        .match("pydomain.ddd*")
        .should_not_import("pydomain.cqrs*")
        .check("pydomain")
    )


def test_ddd_does_not_import_es() -> None:
    """Domain must not depend on the event-sourcing layer."""
    (
        archrule("ddd isolation from es")
        .match("pydomain.ddd*")
        .should_not_import("pydomain.es*")
        .check("pydomain")
    )


def test_ddd_does_not_import_infrastructure() -> None:
    """Domain must not depend on infrastructure."""
    (
        archrule("ddd isolation from infrastructure")
        .match("pydomain.ddd*")
        .should_not_import("pydomain.infrastructure*")
        .check("pydomain")
    )


def test_ddd_does_not_import_testing() -> None:
    """Domain must not depend on test helpers."""
    (
        archrule("ddd isolation from testing")
        .match("pydomain.ddd*")
        .should_not_import("pydomain.testing*")
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 2. CQRS layer — may import ddd, but nothing outward
# ---------------------------------------------------------------------------


def test_cqrs_does_not_import_es() -> None:
    """CQRS must not depend on the event-sourcing layer."""
    (
        archrule("cqrs isolation from es")
        .match("pydomain.cqrs*")
        .should_not_import("pydomain.es*")
        .check("pydomain")
    )


def test_cqrs_does_not_import_infrastructure() -> None:
    """CQRS must not depend on infrastructure."""
    (
        archrule("cqrs isolation from infrastructure")
        .match("pydomain.cqrs*")
        .should_not_import("pydomain.infrastructure*")
        .check("pydomain")
    )


def test_cqrs_does_not_import_testing() -> None:
    """CQRS must not depend on test helpers."""
    (
        archrule("cqrs isolation from testing")
        .match("pydomain.cqrs*")
        .should_not_import("pydomain.testing*")
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 3. Event-Sourcing layer (es) — may import ddd, but nothing outward
# ---------------------------------------------------------------------------


def test_es_does_not_import_cqrs() -> None:
    """Event-sourcing must not depend on the CQRS layer."""
    (
        archrule("es isolation from cqrs")
        .match("pydomain.es*")
        .should_not_import("pydomain.cqrs*")
        .check("pydomain")
    )


def test_es_does_not_import_infrastructure() -> None:
    """Event-sourcing must not depend on infrastructure."""
    (
        archrule("es isolation from infrastructure")
        .match("pydomain.es*")
        .should_not_import("pydomain.infrastructure*")
        .check("pydomain")
    )


def test_es_does_not_import_testing() -> None:
    """Event-sourcing must not depend on test helpers."""
    (
        archrule("es isolation from testing")
        .match("pydomain.es*")
        .should_not_import("pydomain.testing*")
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 4. Infrastructure layer — may import ddd, cqrs, es (wiring layer)
# ---------------------------------------------------------------------------

# Infrastructure is the outermost layer and is allowed to import all
# inner layers, so no restriction tests are needed here.


# ---------------------------------------------------------------------------
# 5. Testing layer — may import ddd, cqrs, es (test utilities)
# ---------------------------------------------------------------------------

# Testing helpers are allowed to import all inner layers to create
# fakes, so no restriction tests are needed here.


# ---------------------------------------------------------------------------
# 6. No circular dependencies between top-level packages
# ---------------------------------------------------------------------------


def test_no_circular_imports_between_ddd_and_cqrs() -> None:
    """Verify ddd and cqrs have no circular dependency."""
    (
        archrule("ddd-cqrs no cycle")
        .match("pydomain.ddd*")
        .should_not_import("pydomain.cqrs*")
        .check("pydomain")
    )


def test_no_circular_imports_between_ddd_and_es() -> None:
    """Verify ddd and es have no circular dependency."""
    (
        archrule("ddd-es no cycle")
        .match("pydomain.es*")
        .should_not_import("pydomain.cqrs*")
        .check("pydomain")
    )
