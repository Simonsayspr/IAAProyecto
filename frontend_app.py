import streamlit as st
import pandas as pd
import datetime

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="MVP - Selección de Ayudantes", page_icon="🎓", layout="centered")

st.title("🎓 Buscador de Candidatos Ideales para Ayudantías")
st.write("Selecciona los parámetros deseados para que nuestro modelo (XGBoost) ordene a los mejores postulantes.")

# --- BARRA LATERAL (PARÁMETROS DEL USUARIO) ---
st.sidebar.header("⚙️ Parámetros de Búsqueda")

# 1. Materia específica
materia = st.sidebar.selectbox(
    "Materia Específica",
    ["Cálculo I", "Álgebra Lineal", "Física Clásica", "Programación", "Economía"]
)

# 2. Promedio mínimo en la materia
promedio_minimo = st.sidebar.slider(
    "Promedio Mínimo Exigido en la Materia",
    min_value=4.0, max_value=7.0, value=5.0, step=0.1
)

# 3. Promedio general mínimo
promedio_general = st.sidebar.slider(
    "Promedio General Mínimo",
    min_value=4.0, max_value=7.0, value=5.0, step=0.1
)

# 4. Experiencia previa (Binaria)
experiencia = st.sidebar.radio(
    "Requiere Experiencia Previa",
    ["Sí", "No"]
)

# 5. Horarios disponibles
st.sidebar.markdown("---")
st.sidebar.subheader("📅 Disponibilidad Horaria")
dias = st.sidebar.multiselect(
    "Días de la semana",
    ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]
)
hora_inicio = st.sidebar.time_input("Horario de Inicio", datetime.time(8, 30))
hora_fin = st.sidebar.time_input("Horario de Finalización", datetime.time(17, 30))

# --- ACCIÓN PRINCIPAL ---
if st.sidebar.button("🔍 Buscar Candidatos Ideales"):
    
    st.subheader(f"Resultados para: {materia}")
    st.write(f"**Filtros aplicados:** Promedio Materia >= {promedio_minimo} | Promedio General >= {promedio_general} | Experiencia: {experiencia}")
    st.write(f"**Disponibilidad:** Días: {', '.join(dias) if dias else 'Cualquiera'} | Rango: {hora_inicio.strftime('%H:%M')} a {hora_fin.strftime('%H:%M')}")
    
    # --- DATOS SIMULADOS (MOCK DATA) ---
    # Aquí es donde en el futuro conectarás tu backend o cargarás tu cruce de datos y el modelo XGBoost.
    # Por ahora, mostramos una tabla inventada para el MVP.
    mock_data = pd.DataFrame({
        "Nombre": ["Ana Pérez", "Juan Soto", "Camila Gómez", "Diego Silva", "Sofía Ruiz"],
        "Promedio Materia": [6.8, 6.5, 6.2, 5.9, 5.5],
        "Promedio General": [6.5, 6.0, 5.8, 5.5, 5.0],
        "Semestres Ayudante": [3, 1, 0, 2, 0],
        "Score Afinidad (Modelo)": [98, 92, 85, 78, 70]
    })
    
    # --- APLICAR FILTROS DUROS ---
    df_filtrado = mock_data[
        (mock_data["Promedio Materia"] >= promedio_minimo) &
        (mock_data["Promedio General"] >= promedio_general)
    ].copy()
    
    if experiencia == "Sí":
        df_filtrado = df_filtrado[df_filtrado["Semestres Ayudante"] > 0]
        
    if df_filtrado.empty:
        st.warning("⚠️ Ningún candidato cumple con todos los parámetros establecidos.")
    else:
        df_filtrado.insert(0, "Ranking", range(1, len(df_filtrado) + 1))
        df_filtrado["Score Afinidad (Modelo)"] = df_filtrado["Score Afinidad (Modelo)"].astype(str) + "%"
        # Mostrar la tabla en la interfaz
        st.dataframe(df_filtrado, hide_index=True, use_container_width=True)
        st.success("✅ ¡Búsqueda completada! Arriba se muestran los candidatos recomendados ordenados por su puntaje de afinidad.")
else:
    st.info("👈 Por favor, ajusta los parámetros en la barra lateral y presiona 'Buscar Candidatos Ideales'.")