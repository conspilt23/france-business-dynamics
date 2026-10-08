from __future__ import annotations

import json

import pytest

from france_business_dynamics.utils.errors import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PRECONDITION,
    PipelineError,
    PreconditionError,
    run_cli,
)
from france_business_dynamics.utils.jsonio import read_json, write_json_atomic

pytestmark = pytest.mark.layer("A-unit")


def _exit_code(callable_) -> int:
    with pytest.raises(SystemExit) as info:
        run_cli(callable_)
    return info.value.code


def test_run_cli_success_and_explicit_codes():
    assert _exit_code(lambda: None) == EXIT_OK
    assert _exit_code(lambda: 1) == 1


def test_pipeline_error_renders_message_and_action(capsys):
    def boom():
        raise PipelineError("fichier cassé", hint="relancer avec --overwrite")

    assert _exit_code(boom) == EXIT_FAILED
    err = capsys.readouterr().err
    assert "ERREUR : fichier cassé" in err and "relancer avec --overwrite" in err
    assert "Traceback" not in err


def test_precondition_error_has_distinct_exit_code():
    def boom():
        raise PreconditionError("couche absente")

    assert _exit_code(boom) == EXIT_PRECONDITION


def test_unexpected_error_hides_traceback_but_names_the_type(capsys):
    def boom():
        raise KeyError("x")

    assert _exit_code(boom) == EXIT_FAILED
    err = capsys.readouterr().err
    assert "KeyError" in err and "--debug" in err and "Traceback" not in err


def test_write_json_atomic_roundtrip_and_no_temp_leftover(tmp_path):
    target = tmp_path / "sub" / "x.json"
    write_json_atomic(target, {"é": 1, "liste": [1, 2]})
    assert read_json(target) == {"é": 1, "liste": [1, 2]}
    assert json.loads(target.read_text(encoding="utf-8"))["é"] == 1  # UTF-8 non échappé
    assert not list(target.parent.glob("*.tmp"))
