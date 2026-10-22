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
import os
import shutil
import subprocess
import sys
import tomllib
from functools import cache
from importlib.metadata import distribution, packages_distributions
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version
from pytest_archon import archrule

import pydomain

PYDOMAIN = Path(pydomain.__file__).parent
ROOT = Path(__file__).resolve().parents[1]

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
# suites may import pytest. anyio is a runtime dependency since ADR-079,
# imported by the unit of work's shield helper and the contract suites only.


def test_only_contract_suites_import_test_frameworks() -> None:
    """pytest stays out of everything but the contract suites (B-128)."""
    (
        archrule("test frameworks only in contract suites")
        .match("pydomain*")
        .exclude("pydomain.testing.contracts*")
        .should_not_import("pytest*", "_pytest*")
        .check("pydomain")
    )


def test_only_the_unit_of_work_imports_anyio_at_runtime() -> None:
    """B-128: anyio is a runtime dependency for the shield helper alone.

    The rule checks direct imports: every module that uses the helper reaches
    anyio through ``pydomain.cqrs.unit_of_work``.
    """
    (
        archrule("anyio only in the unit of work")
        .match("pydomain*")
        .exclude("pydomain.testing.contracts*", "pydomain.cqrs.unit_of_work")
        .should_not_import("anyio*")
        .check("pydomain", only_direct_imports=True)
    )


def test_anyio_is_a_runtime_dependency() -> None:
    """B-122, B-133: ``anyio>=4`` in ``dependencies``; the CI floor job runs the
    suite at the declared floor."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirements = [Requirement(line) for line in project["dependencies"]]
    anyio = [r for r in requirements if r.name == "anyio"]

    assert len(anyio) == 1
    floors = [Version(s.version) for s in anyio[0].specifier if s.operator == ">="]
    assert len(floors) == 1
    assert floors[0] >= Version("4")


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="DCE-145 B-141: the testing extra still lists anyio",
)
def test_testing_extra_installs_pytest_only() -> None:
    """B-141: anyio is a runtime dependency, so the ``testing`` extra adds
    pytest alone."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    extra = [Requirement(line) for line in project["optional-dependencies"]["testing"]]

    assert [r.name for r in extra] == ["pytest"]


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
# `make mutate` runs the tests on copies of src/ under mutants/, whose
# functions import mutmut's trampoline; that import is the tool's, not the
# library's, so it is allowed there and nowhere else.
IN_MUTATION_WORKSPACE = "mutants" in PYDOMAIN.parts
MUTATION_TRAMPOLINE = "mutmut.mutation.trampoline"
MUTATION_TOOL = frozenset({"mutmut"}) if IN_MUTATION_WORKSPACE else frozenset()


def _imported_top_level_names(path: Path) -> set[str]:
    """Top-level names *path* imports, leaving out the mutation trampoline."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if IN_MUTATION_WORKSPACE and node.module == MUTATION_TRAMPOLINE:
                continue
            names.add(node.module.split(".")[0])
    return names


def test_ddd_imports_only_pydantic_uuid_utils_and_stdlib() -> None:
    """Every import in ``pydomain.ddd`` is stdlib, pydantic, uuid-utils or pydomain."""
    allowed = set(sys.stdlib_module_names) | DOMAIN_THIRD_PARTY | {"pydomain"}
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
    unexpected = _third_party(_modules_loaded_by("pydomain.ddd")) - allowed
    assert not unexpected, sorted(unexpected)


@DDD_17
def test_importing_cqrs_does_not_load_es() -> None:
    loaded = _modules_loaded_by("pydomain.cqrs")
    assert not {name for name in loaded if name.startswith("pydomain.es")}


@DDD_17
def test_importing_message_bus_does_not_load_es() -> None:
    loaded = _modules_loaded_by("pydomain.infrastructure.message_bus")
    assert not {name for name in loaded if name.startswith("pydomain.es")}


# ---------------------------------------------------------------------------
# 8. The rules catch violations (meta-tests)
# ---------------------------------------------------------------------------
#
# Each case copies the package, adds one import to one module and runs the
# named rule on the copy in a fresh interpreter.

REPO = Path(__file__).resolve().parents[1]


def _rule_passes_on_a_copy(
    tmp_path: Path, module: str, line: str, rule: str, *, under: str = "copy"
) -> bool:
    """Whether *rule* passes once *line* is appended to ``src/pydomain/<module>``."""
    root = tmp_path / under
    shutil.copytree(
        REPO / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__")
    )
    tests = root / "tests"
    shutil.copytree(
        REPO / "tests" / "support",
        tests / "support",
        ignore=shutil.ignore_patterns("probes", "__pycache__"),
    )
    for name in ("__init__.py", "conftest.py", "test_architecture.py"):
        shutil.copy(REPO / "tests" / name, tests / name)
    for name in ("pyproject.toml", "README.md"):
        shutil.copy(REPO / name, root / name)
    with (root / "src" / "pydomain" / module).open("a", encoding="utf-8") as file:
        file.write(f"\n{line}\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            f"tests/test_architecture.py::{rule}",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return result.returncode == 0


def test_mutmut_import_in_ddd_fails_outside_the_mutation_workspace(
    tmp_path: Path,
) -> None:
    assert not _rule_passes_on_a_copy(
        tmp_path,
        "ddd/entity.py",
        "import mutmut",
        "test_ddd_imports_only_pydantic_uuid_utils_and_stdlib",
    )


def test_trampoline_import_in_ddd_passes_inside_the_mutation_workspace(
    tmp_path: Path,
) -> None:
    assert _rule_passes_on_a_copy(
        tmp_path,
        "ddd/entity.py",
        "from mutmut.mutation.trampoline import wrap_in_trampoline",
        "test_ddd_imports_only_pydantic_uuid_utils_and_stdlib",
        under="mutants",
    )


def test_third_party_import_in_ddd_fails(tmp_path: Path) -> None:
    assert not _rule_passes_on_a_copy(
        tmp_path,
        "ddd/entity.py",
        "import hypothesis",
        "test_ddd_imports_only_pydantic_uuid_utils_and_stdlib",
    )


def test_infrastructure_importing_testing_fails(tmp_path: Path) -> None:
    assert not _rule_passes_on_a_copy(
        tmp_path,
        "infrastructure/bootstrap.py",
        "import pydomain.testing",
        "test_infrastructure_does_not_import_testing",
    )


@pytest.mark.parametrize(
    ("module", "rule"),
    [
        (
            "ddd/entity.py",
            "test_ddd_imports_no_pydomain_package_outside_the_domain_layer",
        ),
        ("cqrs/commands.py", "test_cqrs_does_not_import_es"),
    ],
)
def test_importing_the_top_level_package_from_a_layer_fails(
    tmp_path: Path, module: str, rule: str
) -> None:
    assert not _rule_passes_on_a_copy(tmp_path, module, "import pydomain", rule)
