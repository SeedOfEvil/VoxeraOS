from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_module():
    path = Path("scripts/mypy_ratchet.py")
    spec = spec_from_file_location("mypy_ratchet", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_errors_only_collects_mypy_error_lines() -> None:
    module = _load_module()
    output = """
Success: no issues found in 30 source files
src/voxera/example.py:12: error: Incompatible return value type (got \"int\", expected \"str\")  [return-value]
src/voxera/example.py:18: note: Revealed type is \"builtins.int\"
Found 1 error in 1 file (checked 30 source files)
"""

    assert module.parse_errors(output) == {
        'src/voxera/example.py:12: error: Incompatible return value type (got "int", expected "str")  [return-value]'
    }


def test_parse_errors_handles_empty_or_success_output() -> None:
    module = _load_module()
    assert module.parse_errors("") == set()
    assert module.parse_errors("Success: no issues found in 30 source files\n") == set()
