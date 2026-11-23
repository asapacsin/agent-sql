import json

import pytest

from agent_sql.config.prompts import load_prompt
from agent_sql.config.settings import REPO_ROOT, config_hash, flatten_parameters


def test_configs_equal_spec_parameters():
    spec = json.loads((REPO_ROOT / "docs/spec/parameters.json").read_text())["parameters"]
    assert flatten_parameters() == spec


def test_config_hash_is_order_independent():
    assert config_hash({"a": 1, "b": [1, 2]}) == config_hash({"b": [1, 2], "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})


def test_prompts_are_versioned_and_render_strictly():
    p = load_prompt("b0/sql.v1.md")
    assert len(p.sha256) == 64
    with pytest.raises(KeyError):
        p.render(dialect="SQLite")  # schema/context missing
    with pytest.raises(ValueError):
        load_prompt("b0/sql.md")
