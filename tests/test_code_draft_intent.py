"""Unit tests for the bounded code/script/config draft intent classifier."""

from __future__ import annotations

from voxera.core.code_draft_intent import (
    classify_code_draft_intent,
    code_fence_language,
    extract_code_from_reply,
    is_code_draft_request,
)

# ---------------------------------------------------------------------------
# is_code_draft_request
# ---------------------------------------------------------------------------


class TestIsCodeDraftRequest:
    def test_python_script_request(self):
        assert is_code_draft_request("write me a python script") is True

    def test_python_script_need_phrasing(self):
        assert is_code_draft_request("I need you to code a python script") is True

    def test_bash_script_request(self):
        assert is_code_draft_request("make a bash script for backup") is True

    def test_yaml_config_request(self):
        assert is_code_draft_request("create a yaml config for my service") is True

    def test_json_config_request(self):
        assert is_code_draft_request("draft a JSON file for my settings") is True

    def test_markdown_doc_request(self):
        assert is_code_draft_request("write me a markdown document") is True

    def test_javascript_script(self):
        assert is_code_draft_request("create a javascript script") is True

    def test_typescript_script(self):
        assert is_code_draft_request("write a typescript file") is True

    def test_shell_script(self):
        assert is_code_draft_request("make a shell script") is True

    def test_dockerfile_request(self):
        assert is_code_draft_request("write a dockerfile") is True

    def test_golang_script(self):
        assert is_code_draft_request("create a go script") is True

    def test_rust_script(self):
        assert is_code_draft_request("write a rust script") is True

    def test_sql_file(self):
        assert is_code_draft_request("draft a sql file") is True

    def test_filename_with_extension(self):
        assert is_code_draft_request("write a file called scraper.py") is True

    def test_filename_py_in_message(self):
        assert is_code_draft_request("create backup.sh") is True

    def test_not_code_draft_no_verb(self):
        assert is_code_draft_request("a python script") is False

    def test_not_code_draft_no_language(self):
        assert is_code_draft_request("write me a script") is False

    def test_not_code_draft_save_that(self):
        assert is_code_draft_request("save that to a file") is False

    def test_not_code_draft_write_that(self):
        assert is_code_draft_request("write that to a file") is False

    def test_not_code_draft_put_that(self):
        assert is_code_draft_request("put that in a file") is False

    def test_not_code_draft_empty(self):
        assert is_code_draft_request("") is False

    def test_not_code_draft_informational(self):
        assert is_code_draft_request("what is python?") is False

    def test_not_code_draft_open_file(self):
        assert is_code_draft_request("open the file script.py") is False

    def test_generate_python_script(self):
        assert is_code_draft_request("generate a python script that scrapes web pages") is True

    def test_give_me_bash_script(self):
        assert is_code_draft_request("give me a bash script to monitor disk usage") is True

    def test_i_need_python(self):
        assert is_code_draft_request("I need a python script") is True

    def test_build_yaml_config(self):
        assert is_code_draft_request("build a yaml config for docker-compose") is True


# ---------------------------------------------------------------------------
# classify_code_draft_intent
# ---------------------------------------------------------------------------


class TestClassifyCodeDraftIntent:
    def test_python_script_basic(self):
        result = classify_code_draft_intent("write me a python script")
        assert result is not None
        assert result["goal"].startswith("draft a python script")
        wf = result["write_file"]
        assert wf["path"].endswith(".py")
        assert wf["path"].startswith("~/VoxeraOS/notes/")
        assert wf["content"] == ""
        assert wf["mode"] == "overwrite"

    def test_python_script_default_filename(self):
        result = classify_code_draft_intent("write me a python script")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/script.py"

    def test_bash_script_default_filename(self):
        result = classify_code_draft_intent("make a bash script for backup")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/script.sh"

    def test_yaml_config_default_filename(self):
        result = classify_code_draft_intent("create a yaml config for my service")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/config.yaml"

    def test_json_config_default_filename(self):
        result = classify_code_draft_intent("draft a JSON config file")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/config.json"

    def test_markdown_doc_default_filename(self):
        result = classify_code_draft_intent("write me a markdown document")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/document.md"

    def test_explicit_filename_preserved(self):
        result = classify_code_draft_intent("write me a python script called scraper.py")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/scraper.py"

    def test_named_filename_gets_extension(self):
        result = classify_code_draft_intent("write me a bash script called backup")
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/backup.sh"

    def test_content_is_empty_placeholder(self):
        result = classify_code_draft_intent("create a python script")
        assert result is not None
        assert result["write_file"]["content"] == ""

    def test_shell_normalizes_to_bash(self):
        result = classify_code_draft_intent("make a shell script")
        assert result is not None
        assert result["write_file"]["path"].endswith(".sh")

    def test_not_code_draft_returns_none(self):
        assert classify_code_draft_intent("save that to a file") is None

    def test_no_language_returns_none(self):
        assert classify_code_draft_intent("write me a script") is None

    def test_javascript_script(self):
        result = classify_code_draft_intent("write a javascript script")
        assert result is not None
        assert result["write_file"]["path"].endswith(".js")

    def test_yaml_goal_label(self):
        result = classify_code_draft_intent("create a yaml config")
        assert result is not None
        assert "config file" in result["goal"]

    def test_markdown_goal_label(self):
        result = classify_code_draft_intent("write a markdown document")
        assert result is not None
        assert "document" in result["goal"]

    def test_write_file_contract_has_required_keys(self):
        result = classify_code_draft_intent("write me a python script")
        assert result is not None
        wf = result["write_file"]
        assert "path" in wf
        assert "content" in wf
        assert "mode" in wf

    def test_i_need_python_script(self):
        result = classify_code_draft_intent("I need you to code a python script")
        assert result is not None
        assert result["write_file"]["path"].endswith(".py")

    def test_generate_bash_script(self):
        result = classify_code_draft_intent("generate a bash script to monitor disk usage")
        assert result is not None
        assert result["write_file"]["path"].endswith(".sh")


# ---------------------------------------------------------------------------
# extract_code_from_reply
# ---------------------------------------------------------------------------


class TestExtractCodeFromReply:
    def test_extracts_fenced_python(self):
        reply = "Here's a Python script:\n\n```python\nprint('hello')\n```\n\nThis does X."
        result = extract_code_from_reply(reply)
        assert result == "print('hello')"

    def test_extracts_fenced_bash(self):
        reply = "Here's the bash script:\n\n```bash\n#!/bin/bash\necho hello\n```"
        result = extract_code_from_reply(reply)
        assert result == "#!/bin/bash\necho hello"

    def test_extracts_fenced_yaml(self):
        reply = "Config:\n\n```yaml\nversion: '3'\nservices:\n  web:\n    image: nginx\n```"
        result = extract_code_from_reply(reply)
        assert result == "version: '3'\nservices:\n  web:\n    image: nginx"

    def test_extracts_fence_without_language(self):
        reply = "Code:\n\n```\nsome code here\n```"
        result = extract_code_from_reply(reply)
        assert result == "some code here"

    def test_returns_none_when_no_fence(self):
        reply = "Here is some text without a code block."
        result = extract_code_from_reply(reply)
        assert result is None

    def test_returns_none_for_empty_string(self):
        assert extract_code_from_reply("") is None

    def test_returns_none_for_empty_fence(self):
        reply = "```python\n```"
        assert extract_code_from_reply(reply) is None

    def test_returns_first_fence_when_multiple(self):
        reply = "First:\n```python\nfirst_code()\n```\nSecond:\n```python\nsecond_code()\n```"
        result = extract_code_from_reply(reply)
        assert result == "first_code()"

    def test_multiline_code(self):
        code = "import os\nimport sys\n\ndef main():\n    print('hello')\n\nmain()"
        reply = f"Here:\n\n```python\n{code}\n```"
        result = extract_code_from_reply(reply)
        assert result == code

    def test_returns_none_for_none_input(self):
        assert extract_code_from_reply(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# code_fence_language
# ---------------------------------------------------------------------------


class TestCodeFenceLanguage:
    def test_python(self):
        assert code_fence_language("write me a python script") == "python"

    def test_bash(self):
        assert code_fence_language("make a bash script") == "bash"

    def test_shell_normalizes_to_bash(self):
        assert code_fence_language("make a shell script") == "bash"

    def test_yaml(self):
        assert code_fence_language("create a yaml config") == "yaml"

    def test_json(self):
        assert code_fence_language("draft a json file") == "json"

    def test_javascript(self):
        assert code_fence_language("write a javascript script") == "javascript"

    def test_no_language_returns_none(self):
        assert code_fence_language("write me a script") is None

    def test_empty_returns_none(self):
        assert code_fence_language("") is None
