import streamlit as st
import pandas as pd
import psycopg2
from lector import procesar_excel_maestro, extraer_dosimetria_optimizada, guardar_dosimetria_pdf_en_bd
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode

st.set_page_config(page_title="Control Dosimétrico", page_icon="☢️", layout="wide")

# --- CONTROL DE ACCESO ---
def verificar_password():
    if st.session_state.get("autenticado", False):
        return True
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            st.markdown("## 🔒 Acceso Restringido")
            clave_ingresada = st.text_input("Contraseña", type="password")
            btn_login = st.form_submit_button("Iniciar Sesión", use_container_width=True)
            if btn_login:
                clave_real = st.secrets.get("app_password") or st.secrets["postgres"].get("app_password")
                if clave_ingresada.strip() == str(clave_real).strip():
                    st.session_state["autenticado"] = True
                    st.rerun()
                else:
                    st.error("❌ Contraseña incorrecta")
    return False

if not verificar_password():
    st.stop()

# --- BARRA LATERAL ---
st.sidebar.markdown("### 📥 Carga de Datos")
tab_excel, tab_pdf = st.sidebar.tabs(["1. Excel Maestro", "2. PDFs Mensuales"])

with tab_excel:
    archivo_excel = st.file_uploader("Subir Excel Maestro (.xlsx)", type=["xlsx", "xls"])
    if st.button("Cargar Excel Maestro", use_container_width=True):
        if archivo_excel:
            with st.spinner("Cargando lista de trabajadores..."):
                ok, msg = procesar_excel_maestro(archivo_excel, st.secrets["postgres"])
                if ok:
                    st.success(msg)
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(msg)

with tab_pdf:
    archivos_pdf = st.file_uploader("Subir PDFs del Mes", type="pdf", accept_multiple_files=True)
    if st.button("Procesar PDFs", use_container_width=True):
        if archivos_pdf:
            exitos = 0
            for pdf in archivos_pdf:
                with st.spinner(f"Analizando {pdf.name}..."):
                    df_ext = extraer_dosimetria_optimizada(pdf)
                    if not df_ext.empty and guardar_dosimetria_pdf_en_bd(df_ext, st.secrets["postgres"]):
                        exitos += 1
            if exitos > 0:
                st.success(f"✅ {exitos} PDF(s) procesado(s).")
                st.cache_data.clear()
                st.rerun()

# --- CONSULTA Y TRANSFORMACIÓN DE DATOS ---
@st.cache_data
def cargar_y_transformar_datos():
    try:
        conexion = psycopg2.connect(
            host=st.secrets["postgres"]["host"],
            database=st.secrets["postgres"]["database"],
            user=st.secrets["postgres"]["user"],
            password=st.secrets["postgres"]["password"],
            port=st.secrets["postgres"]["port"],
            client_encoding="utf8"
        )
        # LEFT JOIN: Trae TODOS los del Excel, tengan o no lecturas PDF aún
        query = """
            SELECT 
                m.codigo AS "CODIGO",
                CONCAT(m.apellidos, ', ', m.nombre) AS "NOMBRE",
                m.dni AS "DNI",
                m.centro AS "CENTRO",
                m.dosimetro AS "DOSIMETRO",
                TO_CHAR(m.fecha_alta, 'YYYY-MM-DD') AS "FECHA ALTA",
                TO_CHAR(m.fecha_baja, 'YYYY-MM-DD') AS "FECHA BAJA",
                r.periodo,
                r.dosis_hsm,
                r.dosis_hpm
            FROM maestro_dosimetros m
            LEFT JOIN registros_dosimetria r ON m.codigo = r.codigo_dosimetro
        """
        df = pd.read_sql_query(query, conexion)
        conexion.close()
    except Exception as e:
        st.error(f"Error de conexión: {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    df["periodo"] = pd.to_datetime(df["periodo"])
    df["Mes_Num"] = df["periodo"].dt.month

    # Pivot de datos conservando metadatos completos
    df_pivot = df.pivot_table(
        index=["NOMBRE", "DNI", "CENTRO", "DOSIMETRO", "CODIGO", "FECHA ALTA", "FECHA BAJA"],
        columns="Mes_Num", values=["dosis_hsm", "dosis_hpm"], aggfunc="sum"
    ).reset_index()

    df_flat = pd.DataFrame()
    df_flat["NOMBRE"] = df_pivot[("NOMBRE", "")]
    df_flat["DNI"] = df_pivot[("DNI", "")]
    df_flat["CENTRO"] = df_pivot[("CENTRO", "")]
    df_flat["DOSIMETRO"] = df_pivot[("DOSIMETRO", "")]
    df_flat["CODIGO"] = df_pivot[("CODIGO", "")]
    df_flat["FECHA ALTA"] = df_pivot[("FECHA ALTA", "")]
    df_flat["FECHA BAJA"] = df_pivot[("FECHA BAJA", "")]

    meses_nombres = {1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO',
                     7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'}

    for m in range(1, 13):
        mes = meses_nombres[m]
        df_flat[f"{mes}_HSM"] = df_pivot[('dosis_hsm', m)] if ('dosis_hsm', m) in df_pivot else None
        df_flat[f"{mes}_HPM"] = df_pivot[('dosis_hpm', m)] if ('dosis_hpm', m) in df_pivot else None
        df_flat[f"{mes}_OBS"] = ""

    cols_hsm = [('dosis_hsm', m) for m in range(1, 13) if ('dosis_hsm', m) in df_pivot]
    cols_hpm = [('dosis_hpm', m) for m in range(1, 13) if ('dosis_hpm', m) in df_pivot]
    df_flat["ACUMULADO_HSM"] = df_pivot[cols_hsm].sum(axis=1) if cols_hsm else 0.0
    df_flat["ACUMULADO_HPM"] = df_pivot[cols_hpm].sum(axis=1) if cols_hpm else 0.0

    return df_flat, meses_nombres

# --- DIBUJAR TABLA DE AG GRID ---
resultados = cargar_y_transformar_datos()

if isinstance(resultados, tuple):
    df_flat, meses_nombres = resultados
    
    column_defs = [
        {"field": "NOMBRE", "headerName": "NOMBRE", "width": 300, "minWidth": 240, "pinned": "left", "suppressSizeToFit": True},
        {"field": "DNI", "headerName": "DNI", "width": 130, "minWidth": 110, "suppressSizeToFit": True},
        {"field": "CENTRO", "headerName": "CENTRO", "width": 240, "minWidth": 180, "suppressSizeToFit": True},
        {"field": "DOSIMETRO", "headerName": "DOSIMETRO", "width": 140, "minWidth": 120, "suppressSizeToFit": True},
        {"field": "CODIGO", "headerName": "CODIGO", "width": 140, "minWidth": 110, "suppressSizeToFit": True},
        {"field": "FECHA ALTA", "headerName": "FECHA ALTA", "width": 130, "minWidth": 110, "suppressSizeToFit": True},
        {"field": "FECHA BAJA", "headerName": "FECHA BAJA", "width": 130, "minWidth": 110, "suppressSizeToFit": True},
    ]

    for m in range(1, 13):
        mes = meses_nombres[m]
        column_defs.append({
            "headerName": mes,
            "children": [
                {"field": f"{mes}_HSM", "headerName": "HSM", "width": 95, "type": ["numericColumn"], "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)", "suppressSizeToFit": True},
                {"field": f"{mes}_HPM", "headerName": "HPM", "width": 95, "type": ["numericColumn"], "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)", "suppressSizeToFit": True},
                {"field": f"{mes}_OBS", "headerName": "Observaciones", "width": 200, "editable": True, "suppressSizeToFit": True}
            ]
        })

    column_defs.append({
        "headerName": "ACUMULADO ANUAL",
        "children": [
            {"field": "ACUMULADO_HSM", "headerName": "HSM", "width": 120, "type": ["numericColumn"], "cellStyle": {"fontWeight": "bold", "backgroundColor": "#f1f5f9"}, "valueFormatter": "x === null || x === undefined ? '0.00' : Number(x).toFixed(2)", "suppressSizeToFit": True},
            {"field": "ACUMULADO_HPM", "headerName": "HPM", "width": 120, "type": ["numericColumn"], "cellStyle": {"fontWeight": "bold", "backgroundColor": "#f1f5f9"}, "valueFormatter": "x === null || x === undefined ? '0.00' : Number(x).toFixed(2)", "suppressSizeToFit": True}
        ]
    })

    gb = GridOptionsBuilder.from_dataframe(df_flat)
    gridOptions = gb.build()
    gridOptions["columnDefs"] = column_defs

    AgGrid(df_flat, gridOptions=gridOptions, height=720, theme='alpine', fit_columns_on_grid_load=False, allow_unsafe_jscode=True)
else:
    st.info("Carga el archivo Excel Maestro desde la barra lateral para empezar.")