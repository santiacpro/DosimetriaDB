import unicodedata
import streamlit as st
import pandas as pd
import psycopg2
from sqlalchemy import create_engine, text
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode
from lector import procesar_excel_maestro, procesar_excel_centros, extraer_dosimetria_optimizada, guardar_dosimetria_pdf_en_bd
# --- CONFIGURACIÓN DE PÁGINA Y CSS PROFESIONAL ---
st.set_page_config(page_title="DosimetriaDB", page_icon="☢️", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 0rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            max-width: 100% !important;
        }
        
        [data-testid="stSidebarHeader"] {
            padding: 0 !important;
            min-height: 0 !important;
            height: 0 !important;
        }
        
        section[data-testid="stSidebar"] > div:first-child {
            padding-top: 1rem !important;
        }
        
        [data-testid="stSidebar"] h3 {
            margin-top: 0.5rem !important;
            margin-bottom: 1.0rem !important;
            padding-bottom: 0 !important;
            font-size: 1.1rem;
            font-weight: 600;
        }
        
        [data-testid="stSidebar"] .stElementContainer {
            margin-bottom: -0.5rem !important;
        }
        
        header {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)


# --- FUNCIONES AUXILIARES ---
def quitar_tildes(texto):
    if not isinstance(texto, str): return ""
    texto_limpio = "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').upper()
    return texto_limpio.replace('CH', 'CZZZ')

# --- CONTROL DE ACCESO ---
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            st.markdown("## 🔒 Acceso Restringido")
            clave_ingresada = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Iniciar Sesión", use_container_width=True):
                clave_real = st.secrets.get("app_password") or st.secrets["postgres"].get("app_password")
                if clave_ingresada.strip() == str(clave_real).strip():
                    st.session_state["autenticado"] = True
                    st.rerun()
                else:
                    st.error("❌ Contraseña incorrecta")
    st.stop()

# --- CONEXIÓN Y MIGRACIÓN INICIAL BD ---
pg = st.secrets["postgres"]
db_url = f"postgresql://{pg['user']}:{pg['password']}@{pg['host']}:{pg['port']}/{pg['database']}"
engine = create_engine(db_url)

try:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS avisos (
                id SERIAL PRIMARY KEY,
                codigo_dosimetro VARCHAR NOT NULL,
                periodo DATE NOT NULL,
                estado VARCHAR DEFAULT 'Pendiente',
                tipo_aviso VARCHAR DEFAULT 'Faltan lecturas'
            );
            ALTER TABLE avisos ADD COLUMN IF NOT EXISTS tipo_aviso VARCHAR DEFAULT 'Faltan lecturas';
        """))
except Exception as e:
    print(f"Error en migración inicial: {e}")

# --- INICIALIZACIÓN DE ESTADOS ---
if "filtro_busqueda" not in st.session_state: st.session_state.filtro_busqueda = ""
if "filtro_centro" not in st.session_state: st.session_state.filtro_centro = "Todos los centros"
if "mes_sin_lectura" not in st.session_state: st.session_state.mes_sin_lectura = "-- Ver todos --"
if "pdf_uploader_key" not in st.session_state: st.session_state.pdf_uploader_key = 0

# --- CARGA DE DATOS MAESTROS (Caché) ---
@st.cache_data
def cargar_datos_completos():
    try:
        query_maestro = "SELECT codigo AS \"CODIGO\", CONCAT(apellidos, ', ', nombre) AS \"NOMBRE\", dni AS \"DNI\", centro AS \"CENTRO\", dosimetro AS \"DOSIMETRO\", TO_CHAR(fecha_alta, 'YYYY-MM-DD') AS \"FECHA ALTA\", TO_CHAR(fecha_baja, 'YYYY-MM-DD') AS \"FECHA BAJA\" FROM maestro_dosimetros"
        df_maestro = pd.read_sql_query(query_maestro, engine)
        if df_maestro.empty: return pd.DataFrame(), {}

        query_dosis = "SELECT codigo_dosimetro AS \"CODIGO\", EXTRACT(MONTH FROM periodo)::INTEGER AS \"Mes_Num\", dosis_hsm, dosis_hpm, observaciones FROM registros_dosimetria"
        df_dosis = pd.read_sql_query(query_dosis, engine)
    except Exception as e:
        return pd.DataFrame(), {}

    meses_nombres = {1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'}
    df_flat = df_maestro.copy()

    for m in range(1, 13):
        mes = meses_nombres[m]
        df_flat[f"{mes}_HSM"] = None; df_flat[f"{mes}_HPM"] = None; df_flat[f"{mes}_OBS"] = ""

    if not df_dosis.empty:
        for _, row in df_dosis.iterrows():
            cod = str(row["CODIGO"]).strip()
            if pd.notnull(row["Mes_Num"]) and 1 <= int(row["Mes_Num"]) <= 12:
                mes = meses_nombres[int(row["Mes_Num"])]
                mask = df_flat["CODIGO"] == cod
                df_flat.loc[mask, f"{mes}_HSM"] = row["dosis_hsm"]
                df_flat.loc[mask, f"{mes}_HPM"] = row["dosis_hpm"]
                if pd.notnull(row.get("observaciones")) and str(row["observaciones"]).strip():
                    df_flat.loc[mask, f"{mes}_OBS"] = row["observaciones"]

    cols_hsm = [f"{meses_nombres[m]}_HSM" for m in range(1, 13)]
    cols_hpm = [f"{meses_nombres[m]}_HPM" for m in range(1, 13)]
    df_flat["ACUMULADO_HSM"] = df_flat[cols_hsm].apply(pd.to_numeric, errors='coerce').sum(axis=1)
    df_flat["ACUMULADO_HPM"] = df_flat[cols_hpm].apply(pd.to_numeric, errors='coerce').sum(axis=1)

    df_flat['nombre_sort'] = df_flat['NOMBRE'].apply(quitar_tildes)
    df_flat['es_solapa'] = df_flat['DOSIMETRO'].astype(str).str.strip().str.upper().apply(lambda x: 0 if 'SOLAPA' in x else 1)
    df_flat = df_flat.sort_values(by=['nombre_sort', 'es_solapa', 'CODIGO']).drop(columns=['nombre_sort', 'es_solapa'])

    return df_flat, meses_nombres

df_flat, meses_nombres = cargar_datos_completos()

# --- PANEL LATERAL (SIDEBAR) ---
st.sidebar.markdown("### 📄 Subir PDFs")
archivos_pdf = st.sidebar.file_uploader("Arrastra los informes mensuales", type="pdf", accept_multiple_files=True, key=f"pdf_uploader_{st.session_state.pdf_uploader_key}", label_visibility="collapsed")
if st.sidebar.button("Procesar PDFs", use_container_width=True, type="primary"):
    if archivos_pdf:
        exitos = 0
        for pdf in archivos_pdf:
            with st.spinner(f"Analizando {pdf.name}..."):
                df_ext = extraer_dosimetria_optimizada(pdf)
                if not df_ext.empty and guardar_dosimetria_pdf_en_bd(df_ext, st.secrets["postgres"]):
                    exitos += 1
        if exitos > 0:
            st.sidebar.success(f"✅ {exitos} PDF(s) procesado(s).")
            st.session_state.pdf_uploader_key += 1
            st.cache_data.clear()
            st.rerun()

st.sidebar.markdown("### 🏢 Filtrar Centros")
if not df_flat.empty:
    centros_disponibles = sorted([str(c).strip() for c in df_flat["CENTRO"].dropna().unique() if str(c).strip() != "" and str(c).lower() != "nan"], key=quitar_tildes)
    opciones_centros = ["Todos los centros"] + centros_disponibles
    if st.session_state.filtro_centro not in opciones_centros:
        st.session_state.filtro_centro = "Todos los centros"
else:
    opciones_centros = ["Todos los centros"]

st.session_state.filtro_centro = st.sidebar.selectbox("Selecciona un centro:", options=opciones_centros, index=opciones_centros.index(st.session_state.filtro_centro), label_visibility="collapsed")

st.sidebar.markdown("### 🔎 Buscador General")
st.session_state.filtro_busqueda = st.sidebar.text_input("Buscar por Nombre, DNI...", value=st.session_state.filtro_busqueda, label_visibility="collapsed").strip().lower()

st.sidebar.markdown("### 🔍 Aislar sin Lectura")
opciones_meses_faltantes = ["-- Ver todos --"] + (list(meses_nombres.values()) if 'meses_nombres' in locals() else [])
st.session_state.mes_sin_lectura = st.sidebar.selectbox(
    "Selecciona mes sin dosis:",
    options=opciones_meses_faltantes,
    index=opciones_meses_faltantes.index(st.session_state.mes_sin_lectura) if st.session_state.mes_sin_lectura in opciones_meses_faltantes else 0,
    label_visibility="collapsed"
)

df_display = df_flat.copy() if not df_flat.empty else pd.DataFrame()

if not df_display.empty:
    if st.session_state.filtro_centro != "Todos los centros":
        df_display = df_display[df_display["CENTRO"] == st.session_state.filtro_centro]
    if st.session_state.filtro_busqueda:
        busqueda_normalizada = quitar_tildes(st.session_state.filtro_busqueda)
        mask = (df_display["NOMBRE"].apply(quitar_tildes).str.contains(busqueda_normalizada, na=False) |
                df_display["DNI"].astype(str).str.lower().str.contains(st.session_state.filtro_busqueda, na=False) |
                df_display["CODIGO"].astype(str).str.lower().str.contains(st.session_state.filtro_busqueda, na=False) |
                df_display["CENTRO"].apply(quitar_tildes).str.contains(busqueda_normalizada, na=False))
        df_display = df_display[mask]
        
    if st.session_state.mes_sin_lectura != "-- Ver todos --":
        m_col = st.session_state.mes_sin_lectura
        mask_missing = (
            (df_display[f"{m_col}_HSM"].isna() | (df_display[f"{m_col}_HSM"] == "")) &
            (df_display[f"{m_col}_HPM"].isna() | (df_display[f"{m_col}_HPM"] == ""))
        )
        df_display = df_display[mask_missing]

st.sidebar.markdown("### 📥 Exportar Vista Actual")
csv_data = df_display.to_csv(index=False, encoding='utf-8-sig') if not df_display.empty else ""
st.sidebar.download_button(
    label="Descargar vista actual (CSV)",
    data=csv_data,
    file_name=f"dosimetria_{st.session_state.filtro_centro.replace(' ', '_')}.csv",
    mime="text/csv",
    use_container_width=True
)

st.sidebar.markdown("---")
with st.sidebar.expander("⚙️ Opciones de Administración", expanded=False):
    admin_op = st.selectbox("Acción:", [
        "📥 Cargar Excel Maestro", 
        "🏢 Cargar Excel de Centros", 
        "🗑️ Borrar Datos de un Mes", 
        "💾 Copias de Seguridad"
    ])
    
    if admin_op == "📥 Cargar Excel Maestro":
        archivo_excel = st.file_uploader("Subir Excel Maestro (.xlsx)", type=["xlsx", "xls"], key="up_excel")
        if st.button("Ejecutar carga Maestro", use_container_width=True):
            if archivo_excel:
                ok, msg = procesar_excel_maestro(archivo_excel, st.secrets["postgres"])
                if ok: st.success(msg); st.cache_data.clear(); st.rerun()
                else: st.error(msg)

    elif admin_op == "🏢 Cargar Excel de Centros":
        archivo_centros = st.file_uploader("Subir Excel de Centros (.xlsx)", type=["xlsx", "xls"], key="up_centros")
        if st.button("Ejecutar carga Centros", use_container_width=True):
            if archivo_centros:
                ok, msg = procesar_excel_centros(archivo_centros, st.secrets["postgres"])
                if ok: st.success(msg); st.cache_data.clear(); st.rerun()
                else: st.error(msg)
                
    elif admin_op == "🗑️ Borrar Datos de un Mes":
        try:
            df_p = pd.read_sql_query("SELECT DISTINCT TO_CHAR(periodo, 'YYYY-MM-DD') AS periodo FROM registros_dosimetria ORDER BY periodo DESC", engine)
            opciones_meses = ["Todos los meses"] + (df_p['periodo'].tolist() if not df_p.empty else [])
            mes_a_borrar = st.selectbox("Mes a eliminar:", opciones_meses)
            opciones_borrado = ["Todos los centros"] + centros_disponibles
            centro_a_borrar = st.selectbox("Centro a afectar:", opciones_borrado)
            
            if st.button("⚠️ Eliminar datos", type="primary", use_container_width=True):
                conn = psycopg2.connect(
                    host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"]
                )
                cur = conn.cursor()
                
                if mes_a_borrar == "Todos los meses" and centro_a_borrar == "Todos los centros":
                    cur.execute("DELETE FROM registros_dosimetria")
                    cur.execute("DELETE FROM avisos")
                elif mes_a_borrar == "Todos los meses" and centro_a_borrar != "Todos los centros":
                    cur.execute("DELETE FROM registros_dosimetria WHERE codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s)", (centro_a_borrar,))
                    cur.execute("DELETE FROM avisos WHERE codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s)", (centro_a_borrar,))
                elif mes_a_borrar != "Todos los meses" and centro_a_borrar == "Todos los centros":
                    cur.execute("DELETE FROM registros_dosimetria WHERE periodo = %s", (mes_a_borrar,))
                    cur.execute("DELETE FROM avisos WHERE periodo = %s", (mes_a_borrar,))
                else:
                    cur.execute("DELETE FROM registros_dosimetria WHERE periodo = %s AND codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s)", (mes_a_borrar, centro_a_borrar))
                    cur.execute("DELETE FROM avisos WHERE periodo = %s AND codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s)", (mes_a_borrar, centro_a_borrar))
                
                conn.commit()
                conn.close()
                st.success(f"Datos eliminados con éxito ({mes_a_borrar} - {centro_a_borrar}).")
                st.cache_data.clear()
                st.rerun()
                
        except Exception as e:
            st.error(f"Error en la herramienta de borrado: {e}")

    elif admin_op == "💾 Copias de Seguridad":
        col1, col2 = st.columns(2)
        try:
            df_dump = pd.read_sql_query("SELECT * FROM registros_dosimetria", engine)
            col1.download_button("📥 Bajar CSV", data=df_dump.to_csv(index=False).encode('utf-8'), file_name="backup.csv", mime="text/csv", use_container_width=True)
        except: pass
        csv_upload = col2.file_uploader("Subir CSV", type=["csv"], key="up_csv", label_visibility="collapsed")
        if col2.button("⬆️ Restaurar", use_container_width=True) and csv_upload:
            try:
                pd.read_csv(csv_upload).to_sql('registros_dosimetria', engine, if_exists='append', index=False)
                st.success("Restaurado."); st.cache_data.clear(); st.rerun()
            except Exception as e: st.error(str(e))

# --- INTERFAZ PRINCIPAL ---
if df_flat.empty:
    st.info("👈 Despliega 'Opciones de Administración' en el panel izquierdo para cargar el archivo Excel Maestro.")
    st.stop()

def resolver_aviso(tipo, aviso_id, codigo, dato, datos_alta=None):
    conn = psycopg2.connect(
        host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"]
    )
    cur = conn.cursor()
    
    if tipo == "no_entregado":
        cur.execute("INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones) VALUES (%s, %s, NULL, NULL, 'No entregado') ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET observaciones = 'No entregado';", (codigo, dato))
        cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE id = %s", (aviso_id,))
    elif tipo == "baja":
        cur.execute("UPDATE maestro_dosimetros SET fecha_baja = %s WHERE codigo = %s", (dato, codigo))
        cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND periodo >= %s AND estado = 'Pendiente';", (codigo, dato))
    elif tipo == "confirmar_alta":
        cur.execute("""
            UPDATE maestro_dosimetros 
            SET apellidos = %s, nombre = %s, dni = %s, centro = %s, dosimetro = %s
            WHERE codigo = %s;
        """, (datos_alta['apellidos'], datos_alta['nombre'], datos_alta['dni'], datos_alta['centro'], datos_alta['dosimetro'], codigo))
        cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE id = %s", (aviso_id,))
        
    conn.commit()
    conn.close()
    st.cache_data.clear()
    st.rerun()

# RECUPERAR AVISOS DE LA BD
df_avisos = pd.read_sql_query("""
    SELECT a.id, a.codigo_dosimetro, m.nombre, m.apellidos, m.centro, m.dosimetro, 
           TO_CHAR(a.periodo, 'YYYY-MM-DD') as periodo, 
           COALESCE(a.tipo_aviso, 'Faltan lecturas') as tipo_aviso
    FROM avisos a 
    JOIN maestro_dosimetros m ON a.codigo_dosimetro = m.codigo 
    WHERE a.estado = 'Pendiente'
      AND (
          a.tipo_aviso = 'Alta nueva'
          OR NOT EXISTS (
              SELECT 1 FROM registros_dosimetria r 
              WHERE r.codigo_dosimetro = a.codigo_dosimetro 
                AND r.periodo = a.periodo 
                AND (
                    r.dosis_hsm IS NOT NULL 
                    OR r.dosis_hpm IS NOT NULL 
                    OR (r.observaciones IS NOT NULL AND r.observaciones != '')
                )
          )
      )
""", engine)

num_avisos = len(df_avisos) if st.session_state.filtro_centro == "Todos los centros" else len(df_avisos[df_avisos["centro"] == st.session_state.filtro_centro])

tab_datos, tab_avisos = st.tabs(["📊 Tabla Principal", f"⚠️ Avisos ({num_avisos})"])

# --- PESTAÑA 1: TABLA PRINCIPAL ---
with tab_datos:
    if st.session_state.mes_sin_lectura != "-- Ver todos --":
        st.warning(f"⚠️ Mostrando {len(df_display)} trabajador(es) sin lectura en **{st.session_state.mes_sin_lectura}**.")

    js_confirm = JsCode("""
    function(params) {
        var oldVal = (params.oldValue === null || params.oldValue === undefined) ? '' : String(params.oldValue).trim();
        var newVal = (params.newValue === null || params.newValue === undefined) ? '' : String(params.newValue).trim();
        if (oldVal === newVal) { return false; }
        if (oldVal !== '') {
            if (!window.confirm('⚠️ ¿Seguro que quieres editar esta casilla? Ya contiene datos.')) { return false; }
        }
        params.data[params.colDef.field] = params.newValue;
        return true;
    }
    """)

    js_row_style = JsCode("""
    function(params) {
        if (params.data && params.data['FECHA BAJA']) {
            var val = String(params.data['FECHA BAJA']).trim();
            if (val !== '' && val !== 'None' && val !== 'null' && val !== 'undefined') {
                return {
                    'backgroundColor': '#e2e8f0',
                    'color': '#64748b'
                };
            }
        }
        return null;
    }
    """)

    js_dose_style = JsCode("""
    function(params) {
        if (params.value === null || params.value === undefined || params.value === '') {
            return null;
        }
        var val = parseFloat(String(params.value).replace(',', '.'));
        if (isNaN(val)) return null;

        var dosimetro = params.data && params.data['DOSIMETRO'] ? String(params.data['DOSIMETRO']).toUpperCase() : '';
        var esExtremidad = dosimetro.includes('ANILLO') || dosimetro.includes('MUÑECA') || dosimetro.includes('CANELL') || dosimetro.includes('EXTREMIDAD');

        if (esExtremidad && val > 20.0) {
            return {'backgroundColor': '#fee2e2', 'color': '#991b1b', 'fontWeight': 'bold'};
        } else if (!esExtremidad && val > 1.0) {
            return {'backgroundColor': '#fee2e2', 'color': '#991b1b', 'fontWeight': 'bold'};
        }
        return null;
    }
    """)

    column_defs = [
        {"field": "NOMBRE", "headerName": "NOMBRE", "width": 280, "pinned": "left", "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
        {"field": "DNI", "headerName": "DNI", "width": 120, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
        {"field": "CENTRO", "headerName": "CENTRO", "width": 220, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
        {"field": "DOSIMETRO", "headerName": "DOSIMETRO", "width": 130, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
        {"field": "CODIGO", "headerName": "CODIGO", "width": 130, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
        {"field": "FECHA ALTA", "headerName": "FECHA ALTA", "width": 120, "suppressSizeToFit": True, "editable": True, "valueSetter": js_confirm},
        {"field": "FECHA BAJA", "headerName": "FECHA BAJA", "width": 120, "suppressSizeToFit": True, "editable": True, "valueSetter": js_confirm},
    ]
    
    for m in range(1, 13):
        mes = meses_nombres[m]
        column_defs.append({
            "headerName": mes,
            "children": [
                {"field": f"{mes}_HSM", "headerName": "HSM", "width": 90, "editable": True, "valueSetter": js_confirm, "type": ["numericColumn"], "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)", "cellStyle": js_dose_style, "suppressSizeToFit": True},
                {"field": f"{mes}_HPM", "headerName": "HPM", "width": 90, "editable": True, "valueSetter": js_confirm, "type": ["numericColumn"], "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)", "cellStyle": js_dose_style, "suppressSizeToFit": True},
                {"field": f"{mes}_OBS", "headerName": "Observaciones", "width": 180, "editable": True, "valueSetter": js_confirm, "suppressSizeToFit": True}
            ]
        })

    column_defs.append({
        "headerName": "ACUMULADO ANUAL",
        "children": [
            {"field": "ACUMULADO_HSM", "headerName": "HSM", "width": 110, "type": ["numericColumn"], "cellStyle": {"fontWeight": "bold", "backgroundColor": "#f1f5f9"}, "valueFormatter": "x === null || x === undefined ? '0.00' : Number(x).toFixed(2)", "suppressSizeToFit": True},
            {"field": "ACUMULADO_HPM", "headerName": "HPM", "width": 110, "type": ["numericColumn"], "cellStyle": {"fontWeight": "bold", "backgroundColor": "#f1f5f9"}, "valueFormatter": "x === null || x === undefined ? '0.00' : Number(x).toFixed(2)", "suppressSizeToFit": True}
        ]
    })

    gb = GridOptionsBuilder.from_dataframe(df_display)
    gridOptions = gb.build()
    gridOptions["columnDefs"] = column_defs
    gridOptions["getRowStyle"] = js_row_style

    estilos_tabla = {
        ".ag-header-cell-label": {"justify-content": "center !important", "width": "100% !important"},
        ".ag-header-group-cell-label": {"justify-content": "center !important", "width": "100% !important"},
        ".ag-cell": {"display": "flex !important", "align-items": "center !important", "justify-content": "center !important"}
    }

    grid_response = AgGrid(
        df_display, 
        gridOptions=gridOptions, 
        height=1000, 
        theme='alpine', 
        fit_columns_on_grid_load=False, 
        allow_unsafe_jscode=True, 
        update_mode=GridUpdateMode.VALUE_CHANGED, 
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        custom_css=estilos_tabla
    )

    df_new = pd.DataFrame(grid_response['data'])
    if not df_new.empty and not df_display.empty:
        cambios_detectados = False
        def safe_str(val): return "" if pd.isnull(val) else str(val).strip()
        try:
            conn = psycopg2.connect(
                host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"]
            )
            cur = conn.cursor()
            
            for i in range(len(df_new)):
                row_old = df_display.iloc[i]; row_new = df_new.iloc[i]
                codigo_old = safe_str(row_old["CODIGO"]); codigo_new = safe_str(row_new["CODIGO"])
                if not codigo_old: continue

                update_maestro = False; maestro_query = "UPDATE maestro_dosimetros SET "; maestro_params = []
                if safe_str(row_old["NOMBRE"]) != safe_str(row_new["NOMBRE"]):
                    partes = safe_str(row_new["NOMBRE"]).split(",", 1)
                    maestro_query += "apellidos = %s, nombre = %s, "
                    maestro_params.extend([partes[0].strip(), partes[1].strip() if len(partes) > 1 else ""])
                    update_maestro = True

                for col, db_col in [("CODIGO", "codigo"), ("DNI", "dni"), ("CENTRO", "centro"), ("DOSIMETRO", "dosimetro"), ("FECHA ALTA", "fecha_alta"), ("FECHA BAJA", "fecha_baja")]:
                    if safe_str(row_old[col]) != safe_str(row_new[col]):
                        maestro_query += f"{db_col} = %s, "
                        val = safe_str(row_new[col])
                        maestro_params.append(val if val else None)
                        update_maestro = True

                if update_maestro:
                    maestro_query = maestro_query.rstrip(", ") + " WHERE codigo = %s"
                    maestro_params.append(codigo_old)
                    cur.execute(maestro_query, maestro_params)
                    
                    val_baja = safe_str(row_new["FECHA BAJA"])
                    if val_baja:
                        cur.execute("""
                            UPDATE avisos 
                            SET estado = 'Resuelto' 
                            WHERE codigo_dosimetro = %s AND periodo >= %s AND estado = 'Pendiente';
                        """, (codigo_new, val_baja))
                    
                    cambios_detectados = True

                def fmt_val(v, is_num=True):
                    if pd.isnull(v) or str(v).strip() in ["", "nan"]: return None
                    if is_num:
                        try: return float(str(v).replace(",", "."))
                        except: return None
                    return str(v).strip()

                for m in range(1, 13):
                    mes = meses_nombres[m]
                    if fmt_val(row_old[f"{mes}_HSM"]) != fmt_val(row_new[f"{mes}_HSM"]) or fmt_val(row_old[f"{mes}_HPM"]) != fmt_val(row_new[f"{mes}_HPM"]) or fmt_val(row_old[f"{mes}_OBS"], False) != fmt_val(row_new[f"{mes}_OBS"], False):
                        fecha_sql = f"2026-{m:02d}-01"
                        new_hsm = fmt_val(row_new[f"{mes}_HSM"]); new_hpm = fmt_val(row_new[f"{mes}_HPM"])
                        cur.execute("""INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET dosis_hsm = EXCLUDED.dosis_hsm, dosis_hpm = EXCLUDED.dosis_hpm, observaciones = EXCLUDED.observaciones;""", (codigo_new, fecha_sql, new_hsm, new_hpm, fmt_val(row_new[f"{mes}_OBS"], False)))
                        if new_hsm is not None or new_hpm is not None:
                            cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND periodo = %s", (codigo_new, fecha_sql))
                        cambios_detectados = True

            if cambios_detectados: conn.commit(); st.cache_data.clear(); st.rerun()
        except Exception as e: st.error(f"Error: {e}")
        finally: 
            if 'conn' in locals() and conn: conn.close()

# --- PESTAÑA 2: GESTIÓN DE AVISOS ---
with tab_avisos:
    st.markdown(f"### Gestión de Avisos y Notificaciones ({num_avisos} pendientes)")
    centro_aviso = st.selectbox("Selecciona el centro para ver sus avisos:", options=opciones_centros, index=opciones_centros.index(st.session_state.filtro_centro))
    
    avisos_filtrados = df_avisos if centro_aviso == "Todos los centros" else df_avisos[df_avisos["centro"] == centro_aviso]
    
    if avisos_filtrados.empty:
        st.success(f"🎉 ¡Todo al día! No hay avisos pendientes para {centro_aviso.lower()}.")
    else:
        avisos_lectura = avisos_filtrados[avisos_filtrados['tipo_aviso'] == 'Faltan lecturas']
        avisos_altas = avisos_filtrados[avisos_filtrados['tipo_aviso'] == 'Alta nueva']

        subtab_lecturas, subtab_altas = st.tabs([f"📌 Sin Dosis ({len(avisos_lectura)})", f"🆕 Altas Nuevas ({len(avisos_altas)})"])

        with subtab_lecturas:
            if avisos_lectura.empty:
                st.info("No hay avisos de faltas de lectura.")
            else:
                dic_meses = {1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'}
                for per in sorted(avisos_lectura['periodo'].unique()):
                    dt_p = pd.to_datetime(per)
                    grupo_mes = avisos_lectura[avisos_lectura['periodo'] == per]
                    st.markdown(f"#### 📅 {dic_meses.get(dt_p.month, '')} {dt_p.year} ({len(grupo_mes)})")
                    
                    for _, aviso in grupo_mes.iterrows():
                        with st.expander(f"⚠️ {aviso['apellidos']}, {aviso['nombre']} ({aviso['dosimetro']}) — Centro: {aviso['centro']}", expanded=False):
                            st.write(f"**Código:** {aviso['codigo_dosimetro']}")
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("❌ Marcar No Entregado", key=f"btn_noent_{aviso['id']}"):
                                    resolver_aviso("no_entregado", aviso['id'], aviso['codigo_dosimetro'], aviso['periodo'])
                            with col2:
                                fecha_baja = st.date_input("Fecha de Baja", value=pd.to_datetime(aviso['periodo']).date(), key=f"date_baja_{aviso['id']}")
                                if st.button("🛑 Tramitar Baja", key=f"btn_baja_{aviso['id']}"):
                                    resolver_aviso("baja", aviso['id'], aviso['codigo_dosimetro'], fecha_baja)

        with subtab_altas:
            if avisos_altas.empty:
                st.info("No hay altas nuevas pendientes de revisar.")
            else:
                for _, aviso in avisos_altas.iterrows():
                    with st.expander(f"🆕 Alta Detectada: {aviso['apellidos']}, {aviso['nombre']} ({aviso['codigo_dosimetro']})", expanded=True):
                        st.caption(f"Registrado automáticamente desde PDF en periodo: {aviso['periodo']}")
                        with st.form(key=f"form_alta_{aviso['id']}"):
                            col_a1, col_a2 = st.columns(2)
                            apellidos_edit = col_a1.text_input("Apellidos", value=aviso['apellidos'])
                            nombre_edit = col_a2.text_input("Nombre", value=aviso['nombre'])
                            
                            col_a3, col_a4, col_a5 = st.columns(3)
                            dni_edit = col_a3.text_input("DNI", value="")
                            centro_edit = col_a4.text_input("Centro", value=aviso['centro'])
                            dosimetro_edit = col_a5.selectbox("Dosímetro", options=["Solapa", "Anillo", "Muñeca"], index=["Solapa", "Anillo", "Muñeca"].index(aviso['dosimetro']) if aviso['dosimetro'] in ["Solapa", "Anillo", "Muñeca"] else 0)
                            
                            if st.form_submit_button("✅ Confirmar y Guardar Alta", use_container_width=True):
                                datos_confirmados = {
                                    'apellidos': apellidos_edit,
                                    'nombre': nombre_edit,
                                    'dni': dni_edit,
                                    'centro': centro_edit,
                                    'dosimetro': dosimetro_edit
                                }
                                resolver_aviso("confirmar_alta", aviso['id'], aviso['codigo_dosimetro'], aviso['periodo'], datos_alta=datos_confirmados)