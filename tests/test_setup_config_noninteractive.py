import pytest

import fetchtastic.setup_config as setup_config


@pytest.mark.unit
@pytest.mark.configuration
def test_configure_exclude_patterns_non_interactive_uses_recommended(mocker, capsys):
    config = {}
    mocker.patch("fetchtastic.setup_config.sys.stdin.isatty", return_value=False)

    patterns = setup_config.configure_exclude_patterns(config)

    assert patterns == setup_config.RECOMMENDED_EXCLUDE_PATTERNS
    assert "non-interactive" in capsys.readouterr().out.lower()
