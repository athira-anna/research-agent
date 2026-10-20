import os

from brief_agent.config import load_env


def test_env_file_loaded_without_overriding_shell(tmp_path, monkeypatch):
    (tmp_path / ".env.local").write_text(
        "# comment\nBRIEF_T_A=from_file\nexport BRIEF_T_B='quoted value'\nBRIEF_T_C=from_file\nBRIEF_T_EMPTY=\n")
    monkeypatch.setenv("BRIEF_T_C", "from_shell")
    for k in ("BRIEF_T_A", "BRIEF_T_B", "BRIEF_T_EMPTY"):
        monkeypatch.delenv(k, raising=False)
    load_env(tmp_path)
    assert os.environ["BRIEF_T_A"] == "from_file"
    assert os.environ["BRIEF_T_B"] == "quoted value"
    assert os.environ["BRIEF_T_C"] == "from_shell"
    assert "BRIEF_T_EMPTY" not in os.environ
    for k in ("BRIEF_T_A", "BRIEF_T_B"):
        os.environ.pop(k)
