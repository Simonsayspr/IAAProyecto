"""
Demo standalone — no requiere conexión a Google Sheets.

Genera datos sintéticos que imitan las 4 planillas reales y ejecuta
el pipeline completo (cruce → RF → ILP → KPIs).

Uso:
    python run_demo.py

Para usar con datos reales (archivos Excel/CSV):
    python run_demo.py --real
    y responder las rutas de cada archivo cuando se solicite.
"""

import argparse
import sys

# Forzar UTF-8 en la consola de Windows para mostrar tildes y ñ correctamente
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from backend.pipeline import run_pipeline


# ── Nombres sintéticos ────────────────────────────────────────────────────────

_PATERNO = ["García","Rodríguez","López","Martínez","González","Pérez","Sánchez",
            "Ramírez","Torres","Flores","Rivera","Gómez","Díaz","Reyes","Cruz",
            "Morales","Ortiz","Herrera","Medina","Jiménez","Muñoz","Rojas","Vera",
            "Fuentes","Espinoza","Bravo","Navarro","Molina","Ramos","Guerrero"]
_MATERNO = ["Vargas","Castillo","Silva","Salinas","Vega","Navarrete","Aguilar",
            "Pino","Santos","Mendoza","Figueroa","Rios","Soto","Araya","Cáceres",
            "Moya","Sepúlveda","Contreras","Lara","Peña","Vidal","Palma","Tapia",
            "Bustos","Leiva","Poblete","Cornejo","Carrasco","Valenzuela","Olea"]
_NOM_M = ["Carlos","Luis","Andrés","Diego","Felipe","Sebastián","Pablo","Tomás",
          "Juan","Rodrigo","Nicolás","Ignacio","Matías","Cristóbal","Fernando",
          "Alejandro","Roberto","Ricardo","Alberto","Mauricio"]
_NOM_F = ["María","Valentina","Camila","Sofía","Isabella","Ana","Laura",
          "Daniela","Paula","Natalia","Constanza","Javiera","Francisca",
          "Carolina","Catalina","Gabriela","Claudia","Patricia","Sandra","Gloria"]


def _demo_name(rut_str: str) -> tuple:
    """Retorna (paterno, materno, nombre) determinístico para el RUT."""
    n = int(rut_str)
    pat = _PATERNO[n % len(_PATERNO)]
    mat = _MATERNO[(n * 7) % len(_MATERNO)]
    if (n // len(_PATERNO)) % 2 == 0:
        nom = _NOM_M[(n * 3) % len(_NOM_M)]
    else:
        nom = _NOM_F[(n * 3) % len(_NOM_F)]
    return pat, mat, nom


# ── Generador de datos sintéticos ─────────────────────────────────────────────

def generate_demo_data(n_alumnos: int = 120, seed: int = 42) -> tuple:
    """
    Genera 4 DataFrames sintéticos con la misma estructura que las planillas reales.
    """
    np.random.seed(seed)
    ruts = [str(100000 + i) for i in range(n_alumnos)]
    # Nombres determinísticos por RUT
    _names = {r: _demo_name(r) for r in ruts}

    # Cursos base del plan de estudios
    cursos_base = [
        ("ING", "1101", "CALCULO I"),
        ("ING", "1102", "FUNDAMENTOS DE QUIMICA"),
        ("ING", "2102", "CALCULO II"),
        ("ING", "2204", "ESTATICA"),
        ("ING", "3301", "TERMODINAMICA"),
        ("ICC", "1310", "INTRO A LA PROGRAMACION"),
        ("ICC", "3210", "BASE DE DATOS"),
        ("MAT", "1200", "ALGEBRA LINEAL"),
        ("FIS", "1101", "FISICA I"),
        ("FIS", "2101", "FISICA II"),
    ]

    periodos_hist = ["202110", "202210", "202310", "202410", "202510"]

    # ── Malla (historial aprobados) ──────────────────────────────────────────
    malla_rows = []
    for rut in ruts:
        n_aprobados = np.random.randint(4, len(cursos_base) + 1)
        idx_cursos = np.random.choice(len(cursos_base), n_aprobados, replace=False)
        for idx in idx_cursos:
            mat, cur, tit = cursos_base[idx]
            nota = round(np.random.normal(5.4, 0.8), 1)
            nota = max(1.0, min(7.0, nota))
            pat, mat2, nom = _names[rut]
            malla_rows.append({
                "RUT": rut,
                "PATERNO": pat, "MATERNO": mat2, "NOMBRE": nom,
                "PROGRAMA": "ING", "CARRERA": "INGE",
                "PERIODO": "202110", "INGRESO": "202210",
                "TIPO ADMISION": "Estandar", "ESTADO": "INPROGRESS",
                "NUM CAPP": np.random.randint(50, 130),
                "AREA": f"{mat}1.1", "REGLA": f"{mat}{cur}",
                "DESCRIPCION REGLA": tit,
                "PERIODO CURSADO": np.random.choice(periodos_hist),
                "NRC": np.random.randint(1000, 9999),
                "MATERIA": mat, "CURSO": cur, "TITULO": tit,
                "ATRIBUTO": "", "CREDITOS": 3,
                "NOTA": nota,
                "ORIGEN": "H",
                "RAZON": "OE",
            })
    malla_df = pd.DataFrame(malla_rows)

    # ── Promedios ────────────────────────────────────────────────────────────
    prom_rows = []
    for rut in ruts:
        pga = round(np.random.normal(5.3, 0.6), 2)
        pga = max(3.0, min(7.0, pga))
        pua = round(pga + np.random.uniform(-0.3, 0.5), 2)
        pua = max(3.0, min(7.0, pua))
        pat, mat, nom = _names[rut]
        prom_rows.append({
            "RUT": rut,
            "PATERNO": pat, "MATERNO": mat, "NOMBRE": nom,
            "PROGRAMA": "ING", "CARRERA": np.random.choice(["INGE", "INGC", "INGA"]),
            "TIPO ADMISION": "Estandar", "ESTADO": "INPROGRESS",
            "CATALOGO": "202210", "PERIODO": "202610", "INGRESO": "202210",
            "PROMEDIO RAMOS APROBADOS": str(round(pga * 1.03, 2)).replace(".", ","),
            "PROMEDIO GENERAL ACUMULADO": str(pga).replace(".", ","),
            "PROMEDIO ULTIMO AÑO CURSADO": str(pua).replace(".", ","),
            "SOLICITUD": 100 + int(rut) % 50,
            "FECHA SOLICITUD": "13/04/2026",
        })
    promedios_df = pd.DataFrame(prom_rows)

    # ── NRC (secciones del semestre actual 202610) ───────────────────────────
    nrc_rows = []
    nrc_id = 5000
    horarios_posibles = [
        {"LUNES": "10:30 -12:20", "MIERCOLES": "10:30 -12:20"},
        {"MARTES": "08:30 -10:20", "JUEVES": "08:30 -10:20"},
        {"LUNES": "14:30 -16:20", "VIERNES": "14:30 -16:20"},
        {"MIERCOLES": "16:30 -18:20", "JUEVES": "16:30 -18:20"},
        {"MARTES": "12:30 -14:20", "VIERNES": "12:30 -14:20"},
    ]
    for mat, cur, tit in cursos_base:
        for secc in range(1, 4):
            horario = np.random.choice(horarios_posibles)
            inscritos = np.random.randint(20, 55)
            row = {
                "N°": nrc_id - 4999,
                "PERIODO": "202610",
                "ESCUELA": "ING",
                "NRC": nrc_id,
                "CONECTOR LIGA": "",
                "LISTA CRUZADA": "",
                "MATERIA": mat,
                "CURSO": cur,
                "SECC.": secc,
                "CALIFICABLE": "Y",
                "TITULO": tit,
                "STATUS": "A",
                "P/P": 1,
                "CREDITO": 3,
                "ESCALA CALIFICACION": "S",
                "CAMPUS": "SC",
                "LUNES": "", "MARTES": "", "MIERCOLES": "",
                "JUEVES": "", "VIERNES": "", "SABADO": "",
                "INICIO": "11/03/2026",
                "FIN": "24/06/2026",
                "SALA": f"C-{np.random.randint(100, 300)}",
                "TIPO": "CLAS",
                "RUT PROFESOR": np.random.randint(10000000, 19999999),
                "PROFESOR": "APELLIDO/NOMBRE",
                "CUPOS": 60,
                "INSCRITOS": inscritos,
                "% INSCRITOS / CUPOS": f"{round(inscritos/60*100)}%",
                "CAPACIDAD SALA": 70,
                "% OCUPACION SALA": f"{round(inscritos/70*100)}%",
            }
            row.update({k: "" for k in ["LUNES","MARTES","MIERCOLES","JUEVES","VIERNES","SABADO"]})
            row.update(horario)
            nrc_rows.append(row)
            nrc_id += 1

    nrc_df = pd.DataFrame(nrc_rows)

    # ── Ramos inscritos (semestre actual 202610) ──────────────────────────────
    ins_rows = []
    nrcs_disponibles = nrc_df["NRC"].tolist()
    for rut in ruts:
        n_inscritos = np.random.randint(3, 6)
        nrcs_sel = np.random.choice(nrcs_disponibles, n_inscritos, replace=False)
        for nrc in nrcs_sel:
            r = nrc_df[nrc_df["NRC"] == nrc].iloc[0]
            pat, mat, nom = _names[rut]
            ins_rows.append({
                "RUT": rut,
                "PATERNO": pat, "MATERNO": mat, "NOMBRE": nom,
                "PROGRAMA": "ING",
                "PERIODO ING. PROG.": "202210",
                "PER. CATALOGO": "202210",
                "ESTADO": "INPROGRESS",
                "NRC": nrc,
                "MATERIA": r["MATERIA"],
                "CURSO": r["CURSO"],
                "ASIGNATURA": r["TITULO"],
                "CREDITOS": 3,
                "PERIODO": "202610",
                "INSCRIPCION": "CARREÑO/NOMBRE",
                "PROFESOR": "APELLIDO/NOMBRE",
                "RUT PROFESOR": np.random.randint(10000000, 19999999),
                "FECHA INSCRIPCION": "12/12/2025 09:23",
                "TIPO INSCRIPCION": "Inscrito",
            })
    inscritos_df = pd.DataFrame(ins_rows)

    # ── Postulaciones (historial de ayudantías + evaluaciones) ───────────────
    # ~25% de los alumnos tiene experiencia previa como ayudante
    post_rows = []
    periodos_hist = ["202410", "202510"]
    estados = ["Aceptado", "Aceptado", "Aceptado", "Rechazado", "Pendiente"]

    ruts_con_exp = np.random.choice(ruts, size=int(n_alumnos * 0.30), replace=False)
    for rut in ruts_con_exp:
        # Cada uno postulo en 1 o 2 periodos previos
        n_periodos = np.random.randint(1, 3)
        for periodo in np.random.choice(periodos_hist, n_periodos, replace=False):
            mat, cur, tit = cursos_base[np.random.randint(0, len(cursos_base))]
            estado = np.random.choice(estados, p=[0.6, 0.6, 0.6, 0.1, 0.1] / np.sum([0.6, 0.6, 0.6, 0.1, 0.1]))
            evaluacion = ""
            if estado == "Aceptado":
                evaluacion = str(round(float(np.clip(np.random.normal(5.5, 0.8), 1, 7)), 1))
            post_rows.append({
                "RUT":              rut,
                "Nombre":           "Alumno Demo",
                "Correo":           f"{rut}@miuandes.cl",
                "Periodo":          periodo,
                "NRC":              np.random.randint(1000, 9999),
                "Materia":          mat,
                "Curso":            cur,
                "Sección":          np.random.randint(1, 4),
                "Asignatura":       tit,
                "Profesor":         "APELLIDO/NOMBRE",
                "Motivación":       str(np.random.randint(1, 5)),
                "Tipo de ayudante": np.random.choice(["Docente", "Corrección", "Laboratorio"]),
                "Estado":           estado,
                "Aceptado por":     "Coordinador" if estado == "Aceptado" else "",
                "Firma":            "Si" if estado == "Aceptado" else "",
                "Asistencia taller": np.random.choice(["Si", "No"]),
                "Evaluación":       evaluacion,
                "Fecha Modificación": f"15/03/{periodo[:4]}",
            })

    # También agregar postulantes del periodo actual (202610) sin evaluación aún
    ruts_postulantes = np.random.choice(ruts, size=int(n_alumnos * 0.40), replace=False)
    for rut in ruts_postulantes:
        mat, cur, tit = cursos_base[np.random.randint(0, len(cursos_base))]
        post_rows.append({
            "RUT":              rut,
            "Nombre":           "Alumno Demo",
            "Correo":           f"{rut}@miuandes.cl",
            "Periodo":          "202610",
            "NRC":              np.random.randint(5000, 5030),
            "Materia":          mat,
            "Curso":            cur,
            "Sección":          1,
            "Asignatura":       tit,
            "Profesor":         "APELLIDO/NOMBRE",
            "Motivación":       str(np.random.randint(1, 5)),
            "Tipo de ayudante": np.random.choice(["Docente", "Corrección"]),
            "Estado":           "Pendiente",
            "Aceptado por":     "",
            "Firma":            "",
            "Asistencia taller": "",
            "Evaluación":       "",
            "Fecha Modificación": "01/04/2026",
        })

    postulaciones_df = pd.DataFrame(post_rows)

    return malla_df, promedios_df, inscritos_df, nrc_df, postulaciones_df


# ── Carga de datos reales desde archivo ───────────────────────────────────────

def load_real_data() -> tuple:
    """Carga los 4 archivos reales ingresados por el usuario."""
    print("\nIngresa las rutas de los archivos (Excel .xlsx o CSV .csv):")
    rutas = {}
    nombres = {
        "malla": "Cumplimiento de Malla Pregrado",
        "promedios": "Reporte Alumnos con Promedio y Catálogo",
        "inscritos": "Ramos Inscritos por Periodo",
        "nrc": "Listado de NRC por Periodo",
    }
    for key, desc in nombres.items():
        ruta = input(f"  [{desc}]: ").strip().strip('"')
        rutas[key] = ruta

    dfs = {}
    for key, ruta in rutas.items():
        if ruta.endswith(".csv"):
            # Intentar separador tab primero (formato típico de Banner/SAF)
            try:
                dfs[key] = pd.read_csv(ruta, sep="\t", encoding="latin-1")
            except Exception:
                dfs[key] = pd.read_csv(ruta, encoding="latin-1")
        else:
            dfs[key] = pd.read_excel(ruta)
        print(f"  ✓ {key}: {len(dfs[key])} filas")

    return dfs["malla"], dfs["promedios"], dfs["inscritos"], dfs["nrc"]


# ── Exportar resultados ───────────────────────────────────────────────────────

def export_results(results: dict, prefix: str = "output") -> None:
    """Exporta ranking y tabla de asignación a Excel."""
    output_file = f"{prefix}_resultados.xlsx"
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        if "ranking" in results:
            results["ranking"].to_excel(writer, sheet_name="Ranking por Sección", index=False)
        if "result" in results:
            asig = results["result"][results["result"]["ASIGNADO"] == 1]
            cols = [c for c in ["NRC", "MATERIA", "CURSO", "RUT", "NOTA_RAMO", "PGA", "SCORE"] if c in asig.columns]
            asig[cols].to_excel(writer, sheet_name="Asignación Final", index=False)
        if results.get("feature_importance") is not None:
            fi = results["feature_importance"].reset_index()
            fi.columns = ["Variable", "Importancia"]
            fi.to_excel(writer, sheet_name="Importancia Variables", index=False)

    print(f"  Resultados exportados → {output_file}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MVP Asignación de Ayudantes")
    parser.add_argument("--real", action="store_true", help="Usar datos reales en lugar de demo")
    parser.add_argument("--export", action="store_true", help="Exportar resultados a Excel")
    args = parser.parse_args()

    print("=" * 62)
    print("   MVP — SISTEMA DE ASIGNACIÓN DE AYUDANTES")
    print("   Facultad de Ingeniería, Universidad de los Andes")
    print("=" * 62)

    postulaciones = None
    if args.real:
        malla, promedios, inscritos, nrc = load_real_data()
    else:
        print("\n[MODO DEMO] Generando datos sintéticos (120 alumnos, 30 secciones)...")
        malla, promedios, inscritos, nrc, postulaciones = generate_demo_data()
        print(f"  malla:          {len(malla)} filas")
        print(f"  promedios:      {len(promedios)} filas")
        print(f"  inscritos:      {len(inscritos)} filas")
        print(f"  nrc:            {len(nrc)} filas")
        print(f"  postulaciones:  {len(postulaciones)} filas")

    results = run_pipeline(
        malla, promedios, inscritos, nrc,
        postulaciones_df=postulaciones,
        periodo_actual="202610",
    )

    if results and args.export:
        print("\n── Exportando resultados ─────────────────────────────────────")
        export_results(results)

    if results:
        print("Top 10 candidatos mejor rankeados:")
        ranking = results["ranking"]
        print(ranking.head(10).to_string(index=False))

    return results


if __name__ == "__main__":
    main()
