"""Tests for the Hypothesis profiles."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from hypothesis import settings

from tests.support.hypothesis_profiles import load_profile, register_profiles


@pytest.fixture
def restore_profile() -> Iterator[None]:
    previous = settings.get_current_profile_name()
    yield
    settings.load_profile(previous)


def test_ci_profile_is_derandomized_without_deadline() -> None:
    register_profiles()

    ci = settings.get_profile("ci")

    assert ci.derandomize is True
    assert ci.deadline is None
    assert ci.database is None


def test_dev_profile_is_registered() -> None:
    register_profiles()

    assert settings.get_profile("dev") is not None


@pytest.mark.usefixtures("restore_profile")
def test_dev_is_loaded_when_the_variable_is_unset() -> None:
    register_profiles()

    assert load_profile({}) == "dev"
    assert settings.get_current_profile_name() == "dev"


@pytest.mark.usefixtures("restore_profile")
def test_variable_selects_the_profile() -> None:
    register_profiles()

    assert load_profile({"HYPOTHESIS_PROFILE": "ci"}) == "ci"
    assert settings().derandomize is True


def test_conftest_loads_the_profile_named_in_the_environment() -> None:
    expected = os.environ.get("HYPOTHESIS_PROFILE", "dev")

    assert settings.get_current_profile_name() == expected
