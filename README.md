# AmeliExtract

Converts Ameli third-party payment statements (text PDFs) to JSON, locally, without the insured person's identity.

[![CI](https://ci.wissem.pro/api/badges/9/status.svg)](https://ci.wissem.pro/repos/9)

The output keeps care rows, amounts, rates, notes, professionals, providers, CPAM details and settlements. It drops the insured person's name, NIR and postal address; the beneficiary's date of birth is kept. Nothing is uploaded and there is no OCR: scans and unknown layouts are rejected rather than guessed.

## Install

Python 3.11 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .          # add ".[dev]" for the tests
```

## Use

```powershell
ameli-extract .\statement.pdf -o .\statement.json   # one file
ameli-extract .\statements -o .\exports            # every PDF of a folder, neutral output names
```

Existing JSON files are kept unless `--force` is given. Exit status: 0 on success, 1 when a file needs attention (the folder run goes on), 2 on a command or path error. Messages never contain extracted content.

```python
from ameli_extract import extract_pdf

data = extract_pdf("statement.pdf").to_dict()
```

The result holds `schema_version`, `source` (SHA-256 and page count), `document`, `beneficiaires`, `groupes`, `reglements`, `sections_complementaires` and `validation`. Dates are ISO, money is a decimal string such as `"12.50"`, missing values are `null`. See the fictional [examples/sample.json](examples/sample.json).

Check `validation.statut` before relying on a result: `a_verifier` means a field, a total or an identity detail could not be confirmed. Treat every export as private: health data and the date of birth remain sensitive.

## Development

```powershell
python -m unittest discover -s tests -v
```

Never commit a real PDF, a real export or health data. Tests and examples use invented data only.

## Release

Versions follow Semantic Versioning and changes are listed in [CHANGELOG.md](CHANGELOG.md). Bump `version` in `pyproject.toml`, move the `Unreleased` entries under the new version, merge, then tag `vX.Y.Z`.
