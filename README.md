get_amsl
========

Small CLI to obtain elevation and basic climate normals (temperature and precipitation) for a given latitude/longitude.

Quick usage (PowerShell)

```powershell
# If you use the project's venv (created at .venv)
C:/Users/yanes/Documents/GitHub/get_amsl/.venv/Scripts/python.exe elevation_cli.py <lat> <lon> --pretty --debug

# Or with system python (ensure requests is installed)
pip install -r requirements.txt
python elevation_cli.py 20.981838 -101.961849 --pretty --debug
```

Output

The CLI prints a JSON object containing at least:

- lat, lon: input coordinates
- elevation_m: height above mean sea level (meters)
- provider/dataset: elevation provider metadata

Temperature fields (from Open‑Meteo climate API):

- temp_min_c: minimum average monthly temperature (°C)
- temp_max_c: maximum average monthly temperature (°C)
- temp_mean_c: mean of the 12 monthly mean temperatures (°C)
- temp_provider: data provider (or null if unavailable)

Precipitation fields (from Open‑Meteo climate API):

- annual_precip_mm: mean annual precipitation (mm)
- wettest_month: month number (1-12) with highest mean monthly precipitation
- wettest_month_mm: mean precipitation (mm) in that month
- driest_month: month number (1-12) with lowest mean monthly precipitation
- driest_month_mm: mean precipitation (mm) in that month
- precip_provider: data provider (or null if unavailable)

Debugging

- Use `--debug` to dump raw climate API responses to stderr when the parsing fails.

Tests & CI

- Requirements are in `requirements.txt` (requests, pytest).
- Run tests locally:

```powershell
C:/Users/yanes/Documents/GitHub/get_amsl/.venv/Scripts/python.exe -m pip install -r requirements.txt
C:/Users/yanes/Documents/GitHub/get_amsl/.venv/Scripts/python.exe -m pytest -q
```

- A GitHub Actions workflow is provided at `.github/workflows/python-app.yml` that runs the test suite on push/PR.

Notes

- The climate API requests daily data for the 1991–2020 reference period and computes monthly means locally. If the API response is incomplete or the service is unreachable, temperature/precipitation fields are returned as null but the CLI still prints the elevation.
- If you want more climate outputs (monthly arrays, additional variables), I can add them and expose flags to control which variables are requested.
