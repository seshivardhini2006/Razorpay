import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import tempfile
from pathlib import Path

import env_loader

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)


def test_loads_key_value_lines(tmp_path, monkeypatch):
    # env_loader writes straight into os.environ; monkeypatch cannot roll back
    # out-of-band writes, so clean up explicitly after asserting.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RAZORPAY_TEST_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_TEST_KEY_SECRET", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    f = tmp_path / ".env"
    f.write_text("# comment\nGEMINI_API_KEY=abc123\nRAZORPAY_TEST_KEY_ID = rzp_test_x\n"
                 "export LLM_MODEL='gemini-2-flash'\n\nINVALID_LINE\n", encoding="utf-8")
    env_loader.load_env_file(f)
    assert os.environ["GEMINI_API_KEY"] == "abc123"
    assert os.environ["RAZORPAY_TEST_KEY_ID"] == "rzp_test_x"
    assert os.environ["LLM_MODEL"] == "gemini-2-flash"
    for key in ("GEMINI_API_KEY", "RAZORPAY_TEST_KEY_ID", "RAZORPAY_TEST_KEY_SECRET",
                "LLM_MODEL"):
        os.environ.pop(key, None)


def test_real_env_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell")
    f = tmp_path / ".env"
    f.write_text("GEMINI_API_KEY=from-file\n", encoding="utf-8")
    env_loader.load_env_file(f)
    assert os.environ["GEMINI_API_KEY"] == "from-shell"


def test_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    env_loader.load_env_file(tmp_path / "nope.env")
    assert os.environ.get("GEMINI_API_KEY") is None


def test_no_env_leak_on_plain_import():
    # A populated .env must NOT affect plain imports (cold-start guarantee):
    # db / engine / razorpay_payments never call the loader themselves.
    snippet = (
        "import os, sys\n"
        f"sys.path.insert(0, r'{BACKEND}')\n"
        "import db, engine, razorpay_payments\n"
        "assert not os.environ.get('RAZORPAY_TEST_KEY_ID'), 'leaked from .env on import'\n"
        "assert not os.environ.get('RAZORPAY_TEST_KEY_SECRET'), 'leaked from .env on import'\n"
    )
    tmp = Path(tempfile.mkdtemp()) / "check_no_leak.py"
    tmp.write_text(snippet, encoding="utf-8")
    res = subprocess.run([sys.executable, str(tmp)], capture_output=True, text=True, cwd=ROOT)
    assert res.returncode == 0, res.stderr