# BDGemini

## Setup

```powershell
pip install -r requirements.txt
python -m playwright install
```

The login worker needs Playwright browser binaries on the host. Without them,
jobs can fail before the Google login page opens.

## Checks

```powershell
python -m unittest -q
python -m compileall -q main.py bot test_dotenv.py test_login_worker_browser.py test_login_worker_google_login.py test_login_worker_page.py test_audit_fixes.py
```

`pytest` and `pyright` are optional developer tools in this repo. Install them
before using `python -m pytest -q` or `python -m pyright`.
