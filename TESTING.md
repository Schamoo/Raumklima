# Tests ausführen

Die Testumgebung ist in `requirements_test.txt` festgehalten. Mit Python 3.14 lässt sich die Umgebung so einrichten:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements_test.txt
python -m pytest -q -p no:cacheprovider
python -m compileall -q custom_components/raumklima tests
```

Auf Linux oder macOS den Aktivierungsschritt durch `source .venv/bin/activate` ersetzen.
