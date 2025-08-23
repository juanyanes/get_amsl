#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
from typing import Optional, Dict
import sys
import requests

class ElevationError(Exception):
    pass

def _from_opentopodata(lat: float, lon: float, dataset: str = "srtm90m", timeout: int = 10) -> Dict:
    """
    Consulta OpenTopoData. Datasets útiles:
      - srtm90m (global ≈56°S–60°N, ~90 m)
      - etopo1 (global, ~1 km)
      - gmted2010, aster30m (cobertura/precisión varían)
    """
    url = f"https://api.opentopodata.org/v1/{dataset}"
    r = requests.get(url, params={"locations": f"{lat},{lon}"}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "OK" or not data.get("results"):
        raise ElevationError(f"OpenTopoData no devolvió resultados ({dataset}).")
    elev = data["results"][0].get("elevation")
    if elev is None:
        raise ElevationError("OpenTopoData sin elevación.")
    return {"elevation_m": float(elev), "provider": "opentopodata", "dataset": dataset}

def _from_openelevation(lat: float, lon: float, timeout: int = 10) -> Dict:
    """
    Consulta Open-Elevation (servicio comunitario).
    """
    url = "https://api.open-elevation.com/api/v1/lookup"
    r = requests.get(url, params={"locations": f"{lat},{lon}"}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    results = data.get("results") or []
    if not results or results[0].get("elevation") is None:
        raise ElevationError("Open-Elevation sin elevación.")
    return {"elevation_m": float(results[0]["elevation"]), "provider": "open-elevation", "dataset": "default"}

def get_elevation(lat: float, lon: float,
                  provider: str = "auto",
                  dataset: str = "srtm90m",
                  timeout: int = 10,
                  fallback: bool = True) -> Dict[str, Optional[float]]:
    """
    Devuelve la altura sobre el nivel medio del mar (metros) para (lat, lon).

    Parámetros:
      - lat, lon: coordenadas en grados (WGS84).
      - provider: "opentopodata", "open-elevation" o "auto".
      - dataset: para OpenTopoData (ej. "srtm90m", "etopo1").
      - timeout: segundos por solicitud HTTP.
      - fallback: si True y falla el proveedor principal, prueba alternativas.

    Retorna:
      dict con keys: elevation_m, provider, dataset.

    Lanza:
      ElevationError o requests HTTPError/Timeout en caso de error.
    """
    last_err = None

    def try_opentopodata(ds: str):
        return _from_opentopodata(lat, lon, dataset=ds, timeout=timeout)

    if provider == "opentopodata":
        return try_opentopodata(dataset)
    if provider == "open-elevation":
        return _from_openelevation(lat, lon, timeout=timeout)

    # provider == "auto": intentar en orden
    for step in [
        ("opentopodata", dataset),
        ("opentopodata", "etopo1"),
        ("open-elevation", None),
    ] if fallback else [("opentopodata", dataset)]:
        try:
            if step[0] == "opentopodata":
                return try_opentopodata(step[1])
            else:
                return _from_openelevation(lat, lon, timeout=timeout)
        except Exception as e:
            last_err = e
            continue

    raise ElevationError(f"No se pudo obtener elevación. Último error: {last_err}")


def get_annual_temperature(lat: float, lon: float, timeout: int = 10, debug: bool = False) -> Dict[str, Optional[float]]:
    """
    Intenta obtener la climatología (temperatura media mensual) para (lat, lon)
    usando la API de Open-Meteo (climate-api). Devuelve min/max/mean anual en °C
    y metadatos del proveedor. Si falla, devuelve valores None y provider 'none'.
    """
    url = "https://climate-api.open-meteo.com/v1/climate"
    # Request daily mean temperature for the 1991-2020 reference period and
    # compute monthly means locally. The API returns daily arrays under 'daily'.
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": "1991-01-01",
        "end_date": "2020-12-31",
        "daily": "temperature_2m_mean",
        # keep response reasonably small-ish; models param could be added later
    }
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()

        # Expecting 'daily' with 'time' and 'temperature_2m_mean' arrays
        daily = data.get("daily") or {}
        times = daily.get("time") or []
        temps = daily.get("temperature_2m_mean") or daily.get("temperature") or []

        if not times or not temps or len(times) != len(temps):
            if debug:
                try:
                    print("DEBUG: climate API response:", file=sys.stderr)
                    print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
                except Exception:
                    print("DEBUG: (no se pudo serializar la respuesta)", file=sys.stderr)
            return {"temp_min_c": None, "temp_max_c": None, "temp_mean_c": None, "temp_provider": None}

        # Aggregate daily temperatures into monthly means across the period
        from collections import defaultdict
        monthly_vals = defaultdict(list)
        for t_str, val in zip(times, temps):
            try:
                # ISO date YYYY-MM-DD
                month = int(t_str.split("-")[1])
                monthly_vals[month].append(float(val))
            except Exception:
                continue

        # Compute mean per month for months 1..12; require at least one value per month
        monthly_means = []
        for m in range(1, 13):
            vals = monthly_vals.get(m, [])
            if not vals:
                # missing month data => fallback to failure
                if debug:
                    print(f"DEBUG: missing data for month {m}", file=sys.stderr)
                return {"temp_min_c": None, "temp_max_c": None, "temp_mean_c": None, "temp_provider": None}
            monthly_means.append(sum(vals) / len(vals))

        tmin = min(monthly_means)
        tmax = max(monthly_means)
        tmean = sum(monthly_means) / len(monthly_means) if monthly_means else None
        return {"temp_min_c": tmin, "temp_max_c": tmax, "temp_mean_c": tmean, "temp_provider": "open-meteo-climate"}
    except Exception:
        # No interrumpimos la CLI por fallo en datos climáticos; devolvemos nulos.
        return {"temp_min_c": None, "temp_max_c": None, "temp_mean_c": None, "temp_provider": None}


def get_annual_precipitation(lat: float, lon: float, timeout: int = 10, debug: bool = False) -> Dict[str, Optional[float]]:
    """
    Obtiene precipitaciones diarias desde la Climate API (1991-2020), calcula la
    precipitación anual media (mm) y determina el mes con más y menos lluvia
    (media mensual acumulada a lo largo del periodo).

    Devuelve claves: annual_precip_mm, wettest_month (1-12), wettest_month_mm,
    driest_month, driest_month_mm, precip_provider.
    """
    url = "https://climate-api.open-meteo.com/v1/climate"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": "1991-01-01",
        "end_date": "2020-12-31",
        "daily": "precipitation_sum",
    }
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()

        daily = data.get("daily") or {}
        times = daily.get("time") or []
        precs = daily.get("precipitation_sum") or daily.get("precipitation") or []

        if not times or not precs or len(times) != len(precs):
            if debug:
                try:
                    print("DEBUG: climate API response (precip):", file=sys.stderr)
                    print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
                except Exception:
                    print("DEBUG: (no se pudo serializar la respuesta)", file=sys.stderr)
            return {"annual_precip_mm": None, "wettest_month": None, "wettest_month_mm": None,
                    "driest_month": None, "driest_month_mm": None, "precip_provider": None}

        from collections import defaultdict
        year_month_totals = defaultdict(float)
        years = set()
        for t_str, val in zip(times, precs):
            try:
                y_str, m_str, _ = t_str.split("-")
                y = int(y_str)
                m = int(m_str)
                year_month_totals[(y, m)] += float(val)
                years.add(y)
            except Exception:
                continue

        if not years:
            return {"annual_precip_mm": None, "wettest_month": None, "wettest_month_mm": None,
                    "driest_month": None, "driest_month_mm": None, "precip_provider": None}

        years = sorted(years)
        # Compute mean monthly totals across years
        monthly_means = {}
        for m in range(1, 13):
            vals = [year_month_totals.get((y, m)) for y in years if (y, m) in year_month_totals]
            if not vals:
                if debug:
                    print(f"DEBUG: missing precipitation data for month {m}", file=sys.stderr)
                return {"annual_precip_mm": None, "wettest_month": None, "wettest_month_mm": None,
                        "driest_month": None, "driest_month_mm": None, "precip_provider": None}
            monthly_means[m] = sum(vals) / len(vals)

        # Mean annual precipitation: average of annual totals across years
        annual_totals_per_year = []
        for y in years:
            s = sum(year_month_totals.get((y, m), 0.0) for m in range(1, 13))
            annual_totals_per_year.append(s)
        mean_annual = sum(annual_totals_per_year) / len(annual_totals_per_year) if annual_totals_per_year else None

        # Wettest/driest months based on monthly_means
        wettest_month = max(monthly_means.items(), key=lambda kv: kv[1])[0]
        wettest_mm = monthly_means[wettest_month]
        driest_month = min(monthly_means.items(), key=lambda kv: kv[1])[0]
        driest_mm = monthly_means[driest_month]

        return {"annual_precip_mm": mean_annual,
                "wettest_month": wettest_month,
                "wettest_month_mm": wettest_mm,
                "driest_month": driest_month,
                "driest_month_mm": driest_mm,
                "precip_provider": "open-meteo-climate"}
    except Exception:
        return {"annual_precip_mm": None, "wettest_month": None, "wettest_month_mm": None,
                "driest_month": None, "driest_month_mm": None, "precip_provider": None}


def get_soil_type(lat: float, lon: float, timeout: int = 10, debug: bool = False) -> Dict[str, Optional[object]]:
    """
    Query SoilGrids REST API for WRB soil classification at the point.
    Returns a dict with keys: soil_provider, soil_most_probable, soil_most_probable_pct, soil_classes.
    Non-fatal: on failure returns None fields.
    """
    url = "https://rest.isric.org/soilgrids/v2.0/classification/query"
    params = {"lat": lat, "lon": lon, "number_classes": 5}
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()

        # Attempt to extract most probable class and list of classes
        most = None
        classes = []
        # Preferred keys according to API variations
        if isinstance(data, dict):
            # 'most_probable' may exist
            if data.get("most_probable") and isinstance(data.get("most_probable"), dict):
                mp = data.get("most_probable")
                name = mp.get("class_name") or mp.get("name") or mp.get("wrb")
                pct = mp.get("probability") or mp.get("prob") or mp.get("percentage")
                most = (name, float(pct)) if name is not None and pct is not None else (name, None)
            # 'classes' list may exist
            if data.get("classes") and isinstance(data.get("classes"), list):
                for c in data.get("classes"):
                    if not isinstance(c, dict):
                        continue
                    cname = c.get("class_name") or c.get("name") or c.get("wrb") or c.get("label")
                    cpct = c.get("probability") or c.get("prob") or c.get("percentage")
                    try:
                        classes.append({"class": cname, "pct": float(cpct) if cpct is not None else None})
                    except Exception:
                        classes.append({"class": cname, "pct": None})

        # Fallback: sometimes API returns arrays or different structure
        if not most and classes:
            # pick first class as most probable
            most = (classes[0].get("class"), classes[0].get("pct"))

        # Another common response shape uses WRB keys
        # e.g. 'wrb_class_name' and 'wrb_class_probability': [[name, pct], ...]
        if not most and isinstance(data, dict) and data.get("wrb_class_name"):
            try:
                name = data.get("wrb_class_name")
                probs = data.get("wrb_class_probability") or []
                classes = []
                pct = None
                for item in probs:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        cname = item[0]
                        try:
                            cpct = float(item[1])
                        except Exception:
                            cpct = None
                        classes.append({"class": cname, "pct": cpct})
                        if cname == name:
                            pct = cpct
                if pct is None and classes:
                    pct = classes[0].get("pct")
                most = (name, pct)
            except Exception:
                pass

        if not most:
            if debug:
                try:
                    print("DEBUG: soil API response:", file=sys.stderr)
                    print(json.dumps(data, ensure_ascii=False, indent=2), file=sys.stderr)
                except Exception:
                    print("DEBUG: (no se pudo serializar la respuesta)", file=sys.stderr)
            return {"soil_provider": None, "soil_most_probable": None, "soil_most_probable_pct": None, "soil_classes": None}

        name, pct = most
        return {"soil_provider": "isric-soilgrids", "soil_most_probable": name, "soil_most_probable_pct": pct, "soil_classes": classes or None}
    except Exception:
        return {"soil_provider": None, "soil_most_probable": None, "soil_most_probable_pct": None, "soil_classes": None}


def _try_import(name: str):
    try:
        return __import__(name)
    except Exception:
        return None



# ---------- CLI ----------
def _valid_lat(val: str) -> float:
    try:
        f = float(val)
    except ValueError:
        raise argparse.ArgumentTypeError("Latitud inválida.")
    if not -90.0 <= f <= 90.0:
        raise argparse.ArgumentTypeError("Latitud debe estar entre -90 y 90.")
    return f

def _valid_lon(val: str) -> float:
    try:
        f = float(val)
    except ValueError:
        raise argparse.ArgumentTypeError("Longitud inválida.")
    if not -180.0 <= f <= 180.0:
        raise argparse.ArgumentTypeError("Longitud debe estar entre -180 y 180.")
    return f

def main():
    parser = argparse.ArgumentParser(
        description="Obtiene la elevación (m) para una latitud/longitud."
    )
    parser.add_argument("lat", type=_valid_lat, help="Latitud en grados decimales (WGS84).")
    parser.add_argument("lon", type=_valid_lon, help="Longitud en grados decimales (WGS84).")
    parser.add_argument("--provider", choices=["auto", "opentopodata", "open-elevation"],
                        default="auto", help="Proveedor de elevación (por defecto: auto).")
    parser.add_argument("--dataset", default="srtm90m",
                        help="Dataset para OpenTopoData (ej.: srtm90m, etopo1).")
    parser.add_argument("--timeout", type=int, default=10, help="Timeout por solicitud (s).")
    parser.add_argument("--no-fallback", action="store_true",
                        help="Desactiva intentos de respaldo si falla el proveedor principal.")
    parser.add_argument("--pretty", action="store_true", help="Imprime JSON con indentación.")
    parser.add_argument("--debug", action="store_true", help="Imprime información de depuración (respuestas crudas de APIs).")
    parser.add_argument("--table", action="store_true", help="Imprime los resultados en una tabla legible en la CLI.")

    args = parser.parse_args()

    try:
        info = get_elevation(
            args.lat, args.lon,
            provider=args.provider,
            dataset=args.dataset,
            timeout=args.timeout,
            fallback=not args.no_fallback
        )
        # Obtener climatología anual (min/max/mean) si es posible. No falla la CLI si no se obtienen datos.
        temp_info = get_annual_temperature(args.lat, args.lon, timeout=args.timeout, debug=args.debug)
        precip_info = get_annual_precipitation(args.lat, args.lon, timeout=args.timeout, debug=args.debug)
        soil_info = get_soil_type(args.lat, args.lon, timeout=args.timeout, debug=args.debug)
        out = {
            "lat": args.lat,
            "lon": args.lon,
            **info,
            **temp_info,
            **precip_info,
            **soil_info,
        }

        if args.table:
            # Simple two-column table
            def fmt(v, precision=3):
                if v is None:
                    return "-"
                if isinstance(v, float):
                    return f"{v:.{precision}f}"
                return str(v)

            month_names = [None, 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

            rows = []
            rows.append(("Latitude", fmt(out.get('lat'), 6)))
            rows.append(("Longitude", fmt(out.get('lon'), 6)))
            rows.append(("Elevation (m)", fmt(out.get('elevation_m')) + (f" ({out.get('provider')})" if out.get('provider') else "")))
            rows.append(("Temp min (°C)", fmt(out.get('temp_min_c'))))
            rows.append(("Temp max (°C)", fmt(out.get('temp_max_c'))))
            rows.append(("Temp mean (°C)", fmt(out.get('temp_mean_c'))))
            rows.append(("Annual precip (mm)", fmt(out.get('annual_precip_mm'))))
            wm = out.get('wettest_month')
            if wm:
                rows.append(("Wettest month", f"{month_names[wm]} ({wm}) — {fmt(out.get('wettest_month_mm'))} mm"))
            else:
                rows.append(("Wettest month", "-"))
            dm = out.get('driest_month')
            if dm:
                rows.append(("Driest month", f"{month_names[dm]} ({dm}) — {fmt(out.get('driest_month_mm'))} mm"))
            else:
                rows.append(("Driest month", "-"))

            rows.append(("Soil (most probable)", f"{out.get('soil_most_probable') or '-'} ({fmt(out.get('soil_most_probable_pct'))}%)"))

            # Compute column widths
            col1 = max(len(r[0]) for r in rows) if rows else 10
            col2 = max(len(r[1]) for r in rows) if rows else 10
            sep = " | "
            print(f"{ 'Metric'.ljust(col1) }{sep}{ 'Value'.ljust(col2) }")
            print("-" * (col1) + "-+-" + "-" * (col2))
            for a, b in rows:
                print(f"{a.ljust(col1)}{sep}{b.ljust(col2)}")

            # Print soil classes if available
            sc = out.get('soil_classes')
            if sc:
                print()
                print("Soil classes (class : pct)")
                for item in sc:
                    cname = item.get('class') if isinstance(item, dict) else str(item)
                    cpct = item.get('pct') if isinstance(item, dict) else None
                    print(f" - {cname} : {fmt(cpct)}")
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
