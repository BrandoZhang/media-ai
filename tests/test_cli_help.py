"""The human-facing CLI must explain every input it accepts."""

from __future__ import annotations

import argparse

import pytest

from media_ai.cli import bindings, capabilities, config, doctor, image, init, job, music, sound, speech, uninstall, usage, video

_PARSERS = (
    bindings._build_parser,
    capabilities._build_parser,
    config._build_parser,
    doctor._build_parser,
    image._build_parser,
    init._build_parser,
    job._build_parser,
    music._build_parser,
    sound._build_parser,
    speech._build_parser,
    uninstall._build_parser,
    usage._build_parser,
    video._build_parser,
)


def _undocumented_actions(parser: argparse.ArgumentParser) -> list[str]:
    missing: list[str] = []
    for action in parser._actions:  # argparse exposes no public action iterator.
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                if not choice.help or choice.help is argparse.SUPPRESS:
                    missing.append(f"{parser.prog} {choice.dest}")
            for child in action.choices.values():
                missing.extend(_undocumented_actions(child))
            continue
        if not action.help or action.help is argparse.SUPPRESS:
            label = "/".join(action.option_strings) if action.option_strings else action.dest
            missing.append(f"{parser.prog} {label}")
    return missing


@pytest.mark.parametrize("build_parser", _PARSERS)
def test_every_cli_argument_has_help(build_parser):
    assert _undocumented_actions(build_parser()) == []


def test_top_level_help_describes_every_group(capsys):
    from media_ai.__main__ import _GROUP_HELP, _GROUPS, _usage

    assert set(_GROUP_HELP) == set(_GROUPS)
    _usage(None)
    out = capsys.readouterr().out
    for name, description in _GROUP_HELP.items():
        assert name in out
        assert description in out
