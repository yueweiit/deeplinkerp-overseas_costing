"""Load the real translation helper in Node tests that extract class fragments."""

import os
import shlex
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def node_translation_runtime(monkeypatch):
    helper = Path(__file__).with_name("i18n_runtime.cjs")
    options = os.environ.get("NODE_OPTIONS", "")
    monkeypatch.setenv("NODE_OPTIONS", options + " --require=" + shlex.quote(str(helper)))
