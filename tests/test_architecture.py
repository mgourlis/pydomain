"""Architecture boundary tests — enforce DDD layer discipline.

These tests use pytest-archon to assert that the layered dependency
graph of the pydomain library is respected. The intended dependency
direction is:

    ddd  ←  cqrs  ←  infrastructure
           es   ←  infrastructure
    ddd  ←  es
    ddd  ←  cqrs

No layer may import from a layer above it (outward dependency). Together
these rules leave no import cycle between the packages: the only edge that
could close one, infrastructure → testing, is forbidden too.

The domain layer imports only pydantic, uuid-utils and the standard library
(README). Section 7 checks what importing a layer loads at run time.
"""

import ast
import subprocess
import sys
from functools import cache
from importlib.metadata import distribution, packages_distributions
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from pytest_archon import archrule

import pydomain

PYDOMAIN = Path(pydomain.__file__).parent

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

# Infrastructure is the outermost application layer and may import all
# inner layers, but not the test helpers that build on it.


def test_infrastructure_does_not_import_testing() -> None:
    """Infrastructure must not depend on test helpers (TEST-06)."""
    (
        archrule("infrastructure isolation from testing")
        .match("pydomain.infrastructure*")
        .should_not_import("pydomain.testing*")
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 5. Testing layer — may import ddd, cqrs, es (test utilities)
# ---------------------------------------------------------------------------

# Testing helpers are allowed to import all inner layers to create
# fakes. The one restriction is on test frameworks: only the contract
# suites may import pytest and anyio.


def test_only_contract_suites_import_test_frameworks() -> None:
    """pytest and anyio stay out of everything but the contract suites."""
    (
        archrule("test frameworks only in contract suites")
        .match("pydomain*")
        .exclude("pydomain.testing.contracts*")
        .should_not_import("pytest*", "_pytest*", "anyio*")
        .check("pydomain")
    )


def test_nothing_imports_contract_suites() -> None:
    """No pydomain module outside the suites imports them."""
    (
        archrule("contract suites are a leaf")
        .match("pydomain*")
        .exclude("pydomain.testing.contracts*")
        .should_not_import("pydomain.testing.contracts*")
        .check("pydomain")
    )


def test_contract_suites_do_not_import_shipped_fakes() -> None:
    """The suites test adapters, so they never depend on the fakes they test."""
    (
        archrule("contract suites independent of the fakes")
        .match("pydomain.testing.contracts*")
        .should_not_import("pydomain.testing.fake_*", "pydomain.testing.in_memory_*")
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 5a. Shared private helpers — imported by every layer, import none
# ---------------------------------------------------------------------------


def test_describe_helper_imports_no_pydomain_layer() -> None:
    """``pydomain._describe`` (ADR-071) is a leaf, so every layer may use it."""
    (
        archrule("describe helper is a leaf")
        .match("pydomain._describe")
        .should_not_import(
            "pydomain.ddd*",
            "pydomain.cqrs*",
            "pydomain.es*",
            "pydomain.infrastructure*",
            "pydomain.testing*",
        )
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 6. Domain layer dependencies: pydantic, uuid-utils and the standard library
# ---------------------------------------------------------------------------

DOMAIN_THIRD_PARTY = frozenset({"pydantic", "uuid_utils"})
# `make mutate` runs the tests on copies whose functions import mutmut's
# trampoline; that import is the tool's, not the library's.
MUTATION_TOOL = frozenset({"mutmut"})


def _imported_top_level_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_ddd_imports_only_pydantic_uuid_utils_and_stdlib() -> None:
    """Every import in ``pydomain.ddd`` is stdlib, pydantic, uuid-utils or pydomain."""
    allowed = set(sys.stdlib_module_names) | DOMAIN_THIRD_PARTY | {"pydomain"}
    allowed |= MUTATION_TOOL
    offending = {
        f"{path.relative_to(PYDOMAIN)}: {name}"
        for path in sorted((PYDOMAIN / "ddd").rglob("*.py"))
        for name in _imported_top_level_names(path) - allowed
    }
    assert not offending, sorted(offending)


def test_ddd_imports_no_pydomain_package_outside_the_domain_layer() -> None:
    """``pydomain.ddd`` imports only itself and the ``pydomain._describe`` leaf."""
    (
        archrule("ddd imports only ddd and the describe helper")
        .match("pydomain.ddd*")
        .should_not_import("pydomain.*")
        .may_import("pydomain.ddd*", "pydomain._describe")
        .check("pydomain")
    )


# ---------------------------------------------------------------------------
# 7. Import isolation at run time (audit plan §4.7)
# ---------------------------------------------------------------------------
#
# pytest-archon reads import statements; these tests import a module in a
# fresh interpreter and look at what it loaded. They fail today because
# ``pydomain/__init__.py`` loads every layer eagerly (DDD-17); W2-11 makes
# the re-exports lazy.

DDD_17 = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-126 TEST-06: DDD-17, pydomain/__init__.py loads every layer",
)


def _modules_loaded_by(module: str) -> set[str]:
    """Return the modules ``import <module>`` adds, in a fresh interpreter."""
    script = (
        "import sys\n"
        "before = set(sys.modules)\n"
        f"import {module}\n"
        "print('\\n'.join(sorted(set(sys.modules) - before)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.split())


@cache
def _required_import_names(distribution_name: str) -> frozenset[str]:
    """Top-level import names of a distribution and of everything it requires."""
    names_by_distribution: dict[str, set[str]] = {}
    for name, distributions in packages_distributions().items():
        for dist in distributions:
            names_by_distribution.setdefault(dist.lower(), set()).add(name)
    seen: set[str] = set()
    pending = [distribution_name]
    names: set[str] = set()
    while pending:
        current = pending.pop()
        if current.lower() in seen:
            continue
        seen.add(current.lower())
        dist = distribution(current)
        names |= names_by_distribution.get(dist.metadata["Name"].lower(), set())
        for requirement in map(Requirement, dist.requires or []):
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return frozenset(names)


def _third_party(modules: set[str]) -> set[str]:
    """Top-level names of the *modules* installed outside the standard library."""
    script = (
        "import importlib.util, sys, sysconfig\n"
        "stdlib = sysconfig.get_paths()['stdlib']\n"
        "for name in sys.argv[1:]:\n"
        "    spec = importlib.util.find_spec(name)\n"
        "    origin = (spec.origin if spec else None) or ''\n"
        "    installed = 'site-packages' in origin or 'dist-packages' in origin\n"
        "    if origin in ('built-in', 'frozen'):\n"
        "        continue\n"
        "    if installed or not origin.startswith(stdlib):\n"
        "        print(name)\n"
    )
    tops = sorted({name.split(".")[0] for name in modules} - {"pydomain", "__main__"})
    result = subprocess.run(
        [sys.executable, "-c", script, *tops],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.split())


@DDD_17
def test_importing_ddd_loads_no_other_layer() -> None:
    loaded = _modules_loaded_by("pydomain.ddd")
    others = {
        name
        for name in loaded
        if name.startswith(("pydomain.cqrs", "pydomain.es", "pydomain.infrastructure"))
    }
    assert not others, sorted(others)


def test_importing_ddd_loads_only_pydantic_and_uuid_utils() -> None:
    """Third-party modules loaded by ``import pydomain.ddd`` (README's claim)."""
    allowed = _required_import_names("pydantic") | _required_import_names("uuid-utils")
    allowed |= MUTATION_TOOL
    assert not _third_party(_modules_loaded_by("pydomain.ddd")) - allowed


@DDD_17
def test_importing_cqrs_does_not_load_es() -> None:
    loaded = _modules_loaded_by("pydomain.cqrs")
    assert not {name for name in loaded if name.startswith("pydomain.es")}


@DDD_17
def test_importing_message_bus_does_not_load_es() -> None:
    loaded = _modules_loaded_by("pydomain.infrastructure.message_bus")
    assert not {name for name in loaded if name.startswith("pydomain.es")}
