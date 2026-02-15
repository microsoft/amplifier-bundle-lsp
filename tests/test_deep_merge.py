"""Test that Python bundle config deep merges correctly with core LSP bundle."""

from pathlib import Path

import yaml


def deep_merge(base: dict, overlay: dict) -> dict:
    """Deep merge overlay into base, returning new dict."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def test_python_config_merges_with_core():
    """Verify Python language config merges into core's empty languages dict."""
    # Load core bundle behavior
    core_path = Path(__file__).parent.parent / "behaviors" / "lsp-core.yaml"
    with open(core_path) as f:
        core_config = yaml.safe_load(f)

    # Load Python bundle behavior
    python_path = (
        Path(__file__).parent.parent.parent
        / "amplifier-bundle-python-dev"
        / "behaviors"
        / "python-lsp.yaml"
    )
    with open(python_path) as f:
        python_config = yaml.safe_load(f)

    # Extract tool-lsp configs
    core_tool_config = core_config["tools"][0]["config"]
    python_tool_config = python_config["tools"][0]["config"]

    # Verify core has empty languages
    assert core_tool_config["languages"] == {}, "Core should have empty languages dict"

    # Verify Python has python language config
    assert "python" in python_tool_config["languages"], (
        "Python bundle should define python language"
    )

    # Deep merge should result in Python language being present
    merged = deep_merge(core_tool_config, python_tool_config)

    assert "python" in merged["languages"], "Merged config should have python language"
    assert ".py" in merged["languages"]["python"]["extensions"], (
        "Python should handle .py files"
    )
    assert "pyright-langserver" in merged["languages"]["python"]["server"]["command"], (
        "Should use pyright"
    )

    print("✓ Deep merge test passed!")
    print(f"  Core languages: {core_tool_config['languages']}")
    print(f"  Python languages: {list(python_tool_config['languages'].keys())}")
    print(f"  Merged languages: {list(merged['languages'].keys())}")


if __name__ == "__main__":
    test_python_config_merges_with_core()
