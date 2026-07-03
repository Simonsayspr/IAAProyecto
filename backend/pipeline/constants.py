"""
Constantes y parametros compartidos por el pipeline de asignacion de ayudantes.

Centraliza umbrales, pesos de scoring, nombres de dias, estados de postulacion
y los catalogos de nombres sinteticos usados para completar RUTs sin nombre.
"""

import re

# ── Umbrales y limites del proceso ───────────────────────────────────────────
MINIMUM_GRADE_TO_BE_ELIGIBLE = 5.0        # nota minima para ser candidato a ayudante
GOOD_PERFORMANCE_GRADE_THRESHOLD = 5.5    # umbral de "buen desempeno" (etiqueta del modelo)
MAX_SIMULTANEOUS_ASSISTANTSHIPS = 3       # ayudantias simultaneas maximas por alumno
STUDENTS_PER_TEACHING_ASSISTANT = 25      # ratio alumnos/ayudante para dimensionar cupos
RANDOM_STATE = 42                         # semilla para reproducibilidad

# ── Pesos del score deterministico (sin ML) ──────────────────────────────────
DETERMINISTIC_SCORE_WEIGHTS = {
    "NOTA_RAMO":         0.40,
    "PGA":               0.25,
    "N_VECES_AYUDANTE":  0.10,
    "AVANCE_MALLA":      0.15,
    "CARGA_ACTUAL":      0.05,
    "POSTULANTE_ACTUAL": 0.05,
}

# Presets de pesos configurables desde la UI
WEIGHT_PRESETS = {
    "balanced": {
        "label": "Equilibrado",
        "description": "Balance entre nota, promedio, experiencia y avance curricular",
        "weights": {
            "NOTA_RAMO": 0.40, "PGA": 0.25, "N_VECES_AYUDANTE": 0.10,
            "AVANCE_MALLA": 0.15, "CARGA_ACTUAL": 0.05, "POSTULANTE_ACTUAL": 0.05,
        },
    },
    "academic": {
        "label": "Rendimiento académico",
        "description": "Prioriza nota en el ramo y promedio general",
        "weights": {
            "NOTA_RAMO": 0.55, "PGA": 0.30, "N_VECES_AYUDANTE": 0.05,
            "AVANCE_MALLA": 0.05, "CARGA_ACTUAL": 0.03, "POSTULANTE_ACTUAL": 0.02,
        },
    },
    "experience": {
        "label": "Experiencia previa",
        "description": "Prioriza experiencia como ayudante y postulación activa",
        "weights": {
            "NOTA_RAMO": 0.25, "PGA": 0.15, "N_VECES_AYUDANTE": 0.35,
            "AVANCE_MALLA": 0.10, "CARGA_ACTUAL": 0.05, "POSTULANTE_ACTUAL": 0.10,
        },
    },
    "curriculum": {
        "label": "Avance curricular",
        "description": "Prioriza alumnos avanzados en la malla con baja carga",
        "weights": {
            "NOTA_RAMO": 0.25, "PGA": 0.15, "N_VECES_AYUDANTE": 0.05,
            "AVANCE_MALLA": 0.40, "CARGA_ACTUAL": 0.10, "POSTULANTE_ACTUAL": 0.05,
        },
    },
}

# ── Vocabulario de las planillas ──────────────────────────────────────────────
WEEKDAYS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO"]

# Tipos de actividad NRC que ocupan horario presencial del alumno
CLASSROOM_ACTIVITY_TYPES = {"CLAS", "LAB", "AYUD"}

# Estado de postulacion que cuenta como ayudantia realizada/aceptada
# (valor real en la hoja Postulaciones; los demas son Rechazado/Pendiente/Eliminada)
ACCEPTED_APPLICATION_STATES = {"ACEPTADO"}

# Estados que cuentan como postulacion activa del periodo vigente
# (se excluyen Rechazado y Eliminada)
ACTIVE_APPLICATION_STATES = {"ACEPTADO", "PENDIENTE"}


# ── Generador de nombres sinteticos ───────────────────────────────────────────
# Catalogos para completar de forma deterministica los RUTs que llegan sin nombre.
_SYNTHETIC_LAST_NAMES = [
    "Garcia", "Rodriguez", "Lopez", "Martinez", "Gonzalez", "Perez", "Sanchez",
    "Ramirez", "Torres", "Flores", "Rivera", "Gomez", "Diaz", "Reyes", "Cruz",
    "Morales", "Ortiz", "Herrera", "Medina", "Jimenez", "Munoz", "Rojas", "Vera",
    "Fuentes", "Espinoza", "Bravo", "Navarro", "Molina", "Ramos", "Guerrero",
]
_SYNTHETIC_SECOND_LAST_NAMES = [
    "Vargas", "Castillo", "Silva", "Salinas", "Vega", "Navarrete", "Aguilar",
    "Pino", "Santos", "Mendoza", "Figueroa", "Rios", "Soto", "Araya", "Caceres",
    "Moya", "Sepulveda", "Contreras", "Lara", "Pena", "Vidal", "Palma", "Tapia",
    "Bustos", "Leiva", "Poblete", "Cornejo", "Carrasco", "Valenzuela", "Olea",
]
_SYNTHETIC_MALE_NAMES = [
    "Carlos", "Luis", "Andres", "Diego", "Felipe", "Sebastian", "Pablo", "Tomas",
    "Juan", "Rodrigo", "Nicolas", "Ignacio", "Matias", "Cristobal", "Fernando",
    "Alejandro", "Roberto", "Ricardo", "Alberto", "Mauricio",
]
_SYNTHETIC_FEMALE_NAMES = [
    "Maria", "Valentina", "Camila", "Sofia", "Isabella", "Ana", "Laura",
    "Daniela", "Paula", "Natalia", "Constanza", "Javiera", "Francisca",
    "Carolina", "Catalina", "Gabriela", "Claudia", "Patricia", "Sandra", "Gloria",
]


def generate_synthetic_name(rut: str) -> str:
    """Genera un nombre completo deterministico a partir del RUT."""
    try:
        seed = int(re.sub(r"[^0-9]", "", str(rut)))
    except (ValueError, TypeError):
        seed = hash(str(rut)) % 10000

    last_name = _SYNTHETIC_LAST_NAMES[seed % len(_SYNTHETIC_LAST_NAMES)]
    second_last_name = _SYNTHETIC_SECOND_LAST_NAMES[(seed * 7) % len(_SYNTHETIC_SECOND_LAST_NAMES)]
    if (seed // len(_SYNTHETIC_LAST_NAMES)) % 2 == 0:
        first_name = _SYNTHETIC_MALE_NAMES[(seed * 3) % len(_SYNTHETIC_MALE_NAMES)]
    else:
        first_name = _SYNTHETIC_FEMALE_NAMES[(seed * 3) % len(_SYNTHETIC_FEMALE_NAMES)]
    return f"{last_name} {second_last_name} {first_name}"
