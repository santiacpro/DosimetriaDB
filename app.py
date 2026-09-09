import unicodedata
import streamlit as st
import pandas as pd
import psycopg2
from sqlalchemy import create_engine
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode, DataReturnMode

st.set_page_config(page_title="Control Dosimétrico", page_icon="☢️", layout="wide")

# --- FUNCIONES AUXILIARES ---
def quitar_tildes(texto):
    if not isinstance(texto, str):
        return ""
    return "".join(
        c for c in unicodedata.normalize('NFD', texto)
        if unicodedata.category(c) != 'Mn'
    ).upper()

# --- ESTILOS CSS ---
st.markdown("""
    <style>
        .block-container {
            padding-top: 2.5rem !important;
            padding-bottom: 0rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            max-width: 100% !important;
        }
        .ag-header-cell-label, .ag-header-group-cell-label, .ag-header-cell-text, .ag-header-group-text {
            justify-content: center !important;
            text-align: center !important;
            width: 100% !important;
        }
        .ag-cell {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            text-align: center !important;
        }
    </style>
""", unsafe_allow_html=True)

from lector import procesar_excel_maestro, extraer_dosimetria_optimizada, guardar_dosimetria_pdf_en_bd

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

pg = st.secrets["postgres"]
db_url = f"postgresql://{pg['user']}:{pg['password']}@{pg['host']}:{pg['port']}/{pg['database']}"
engine = create_engine(db_url)

# --- MENÚ SUPERIOR DE OPCIONES DE ADMINISTRACIÓN ---
with st.expander("⚙️ Opciones de Administración", expanded=False):
    tab_excel, tab_borrar, tab_backup = st.tabs(["📥 Excel Maestro", "🗑️ Borrar Mes", "💾 Copias de Seguridad"])
    
    with tab_excel:
        st.markdown("**Actualizar o Cargar lista de Trabajadores**")
        archivo_excel = st.file_uploader("Subir Excel Maestro (.xlsx)", type=["xlsx", "xls"], key="up_excel")
        if st.button("Cargar Excel", use_container_width=True):
            if archivo_excel:
                with st.spinner("Procesando..."):
                    ok, msg = procesar_excel_maestro(archivo_excel, st.secrets["postgres"])
                    if ok:
                        st.success(msg)
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(msg)
                        
    with tab_borrar:
        st.markdown("**Borrar lecturas extraídas de un mes específico**")
        try:
            df_periodos = pd.read_sql_query("SELECT DISTINCT periodo FROM registros_dosimetria ORDER BY periodo DESC", engine)
            if not df_periodos.empty:
                # CORRECCIÓN: Convertir explícitamente a datetime antes de extraer .dt
                df_periodos['periodo'] = pd.to_datetime(df_periodos['periodo'])
                opciones_mes = df_periodos['periodo'].dt.strftime('%Y-%m-%d').tolist()
                mes_a_borrar = st.selectbox("Selecciona el mes a eliminar:", opciones_mes)
                if st.button("⚠️ Borrar datos de este mes", type="primary"):
                    conn = psycopg2.connect(host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"])
                    cur = conn.cursor()
                    cur.execute("DELETE FROM registros_dosimetria WHERE periodo = %s", (mes_a_borrar,))
                    conn.commit()
                    conn.close()
                    st.success(f"Datos de {mes_a_borrar} eliminados.")
                    st.cache_data.clear()
                    st.rerun()
            else:
                st.info("No hay lecturas registradas.")
        except Exception as e:
            st.error(f"Error cargando periodos: {e}")

    with tab_backup:
        st.markdown("**Exportar e Importar CSV de Lecturas (Registros de Dosis)**")
        col_down, col_up = st.columns(2)
        with col_down:
            try:
                df_dump = pd.read_sql_query("SELECT * FROM registros_dosimetria", engine)
                csv_backup = df_dump.to_csv(index=False).encode('utf-8')
                st.download_button(label="📥 Descargar Copia (CSV)", data=csv_backup, file_name="backup_lecturas.csv", mime="text/csv", use_container_width=True)
            except:
                st.warning("Error al preparar descarga.")
        with col_up:
            csv_upload = st.file_uploader("Subir CSV para Restaurar", type=["csv"], key="up_csv")
            if st.button("⬆️ Restaurar Copia", use_container_width=True):
                if csv_upload:
                    try:
                        df_restore = pd.read_csv(csv_upload)
                        df_restore.to_sql('registros_dosimetria', engine, if_exists='append', index=False)
                        st.success("Copia restaurada con éxito.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al restaurar: {e}")

# --- BARRA LATERAL: INGESTA DE PDF ---
st.sidebar.markdown("### 📄 Subir Lecturas")
archivos_pdf = st.sidebar.file_uploader("Subir PDFs del Mes", type="pdf", accept_multiple_files=True)
if st.sidebar.button("Procesar PDFs", use_container_width=True):
    if archivos_pdf:
        exitos = 0
        for pdf in archivos_pdf:
            with st.spinner(f"Analizando {pdf.name}..."):
                df_ext = extraer_dosimetria_optimizada(pdf)
                if not df_ext.empty and guardar_dosimetria_pdf_en_bd(df_ext, st.secrets["postgres"]):
                    exitos += 1
        if exitos > 0:
            st.sidebar.success(f"✅ {exitos} PDF(s) procesado(s).")
            st.cache_data.clear()
            st.rerun()

# --- CONSULTA Y TRANSFORMACIÓN DE DATOS ---
@st.cache_data
def cargar_y_transformar_datos():
    try:
        query_maestro = "SELECT codigo AS \"CODIGO\", CONCAT(apellidos, ', ', nombre) AS \"NOMBRE\", dni AS \"DNI\", centro AS \"CENTRO\", dosimetro AS \"DOSIMETRO\", TO_CHAR(fecha_alta, 'YYYY-MM-DD') AS \"FECHA ALTA\", TO_CHAR(fecha_baja, 'YYYY-MM-DD') AS \"FECHA BAJA\" FROM maestro_dosimetros"
        df_maestro = pd.read_sql_query(query_maestro, engine)
        if df_maestro.empty: return pd.DataFrame()

        query_dosis = "SELECT codigo_dosimetro AS \"CODIGO\", EXTRACT(MONTH FROM periodo)::INTEGER AS \"Mes_Num\", dosis_hsm, dosis_hpm, observaciones FROM registros_dosimetria"
        df_dosis = pd.read_sql_query(query_dosis, engine)
    except Exception as e:
        st.error(f"Error: {e}")
        return pd.DataFrame()

    meses_nombres = {1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'}
    df_flat = df_maestro.copy()

    for m in range(1, 13):
        mes = meses_nombres[m]
        df_flat[f"{mes}_HSM"] = None
        df_flat[f"{mes}_HPM"] = None
        df_flat[f"{mes}_OBS"] = ""

    if not df_dosis.empty:
        for _, row in df_dosis.iterrows():
            cod = str(row["CODIGO"]).strip()
            m_num = row["Mes_Num"]
            if pd.notnull(m_num) and 1 <= int(m_num) <= 12:
                mes = meses_nombres[int(m_num)]
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

# --- FUNCIONES DE AVISOS ---
def resolver_no_entregado(aviso_id, codigo, periodo):
    conn = psycopg2.connect(host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"])
    cur = conn.cursor()
    cur.execute("INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones) VALUES (%s, %s, NULL, NULL, 'No entregado') ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET observaciones = 'No entregado';", (codigo, periodo))
    cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE id = %s", (aviso_id,))
    conn.commit(); conn.close()
    st.cache_data.clear(); st.rerun()

def resolver_baja(aviso_id, codigo, fecha_baja):
    conn = psycopg2.connect(host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"])
    cur = conn.cursor()
    cur.execute("UPDATE maestro_dosimetros SET fecha_baja = %s WHERE codigo = %s", (fecha_baja, codigo))
    cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE id = %s", (aviso_id,))
    conn.commit(); conn.close()
    st.cache_data.clear(); st.rerun()

# --- INTERFAZ PRINCIPAL ---
resultados = cargar_y_transformar_datos()

if isinstance(resultados, tuple):
    df_flat, meses_nombres = resultados

    tab_datos, tab_avisos = st.tabs(["📊 Tabla Principal", "⚠️ Avisos Pendientes"])

    with tab_datos:
        # Filtros
        st.sidebar.markdown("---")
        st.sidebar.markdown("### 🔍 Buscador y Filtros")
        
        busqueda_texto = st.sidebar.text_input("🔎 Buscador Rápido:", placeholder="Nombre, DNI...", key="filtro_busqueda").strip().lower()
        centros_disponibles = sorted([str(c).strip() for c in df_flat["CENTRO"].dropna().unique() if str(c).strip() != "" and str(c).lower() != "nan"], key=quitar_tildes)
        st.session_state["filtro_centros"] = [c for c in st.session_state.get("filtro_centros", []) if c in centros_disponibles]
        centros_seleccionados = st.sidebar.multiselect("🏢 Filtrar por Centro:", options=centros_disponibles, placeholder="Todos los centros...", key="filtro_centros")

        df_display = df_flat.copy()
        if centros_seleccionados: df_display = df_display[df_display["CENTRO"].isin(centros_seleccionados)]
        if busqueda_texto:
            busqueda_normalizada = quitar_tildes(busqueda_texto)
            mask = (df_display["NOMBRE"].apply(quitar_tildes).str.contains(busqueda_normalizada, na=False) |
                    df_display["DNI"].astype(str).str.lower().str.contains(busqueda_texto, na=False) |
                    df_display["CODIGO"].astype(str).str.lower().str.contains(busqueda_texto, na=False) |
                    df_display["CENTRO"].apply(quitar_tildes).str.contains(busqueda_normalizada, na=False))
            df_display = df_display[mask]

        # --- JAVASCRIPT: ALERTA AL EDITAR ---
        js_confirm = JsCode("""
        function(params) {
            var oldVal = params.oldValue;
            if (oldVal !== null && oldVal !== undefined && oldVal !== '') {
                var confirmEdit = window.confirm('⚠️ ¿Seguro que quieres editar esta casilla? Ya contiene datos extraídos.');
                if (!confirmEdit) { return false; }
            }
            params.data[params.colDef.field] = params.newValue;
            return true;
        }
        """)

        # --- CORRECCIÓN: HACER EDITABLES LOS DATOS PERSONALES (MENOS EL CÓDIGO) ---
        column_defs = [
            {"field": "NOMBRE", "headerName": "NOMBRE", "width": 280, "pinned": "left", "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
            {"field": "DNI", "headerName": "DNI", "width": 120, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
            {"field": "CENTRO", "headerName": "CENTRO", "width": 220, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
            {"field": "DOSIMETRO", "headerName": "DOSIMETRO", "width": 130, "suppressSizeToFit": True, "filter": True, "editable": True, "valueSetter": js_confirm},
            # CÓDIGO BLOQUEADO
            {"field": "CODIGO", "headerName": "CODIGO", "width": 130, "suppressSizeToFit": True, "filter": True, "editable": False},
            {"field": "FECHA ALTA", "headerName": "FECHA ALTA", "width": 120, "suppressSizeToFit": True, "editable": True, "valueSetter": js_confirm},
            {"field": "FECHA BAJA", "headerName": "FECHA BAJA", "width": 120, "suppressSizeToFit": True, "editable": True, "valueSetter": js_confirm},
        ]
        
        for m in range(1, 13):
            mes = meses_nombres[m]
            column_defs.append({
                "headerName": mes,
                "children": [
                    {"field": f"{mes}_HSM", "headerName": "HSM", "width": 90, "editable": True, "valueSetter": js_confirm, "type": ["numericColumn"], "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)", "suppressSizeToFit": True},
                    {"field": f"{mes}_HPM", "headerName": "HPM", "width": 90, "editable": True, "valueSetter": js_confirm, "type": ["numericColumn"], "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)", "suppressSizeToFit": True},
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

        # --- DIBUJAR TABLA CON MODO DE ACTUALIZACIÓN DE VALORES ---
        grid_response = AgGrid(
            df_display, 
            gridOptions=gridOptions, 
            height=1000, 
            theme='alpine', 
            fit_columns_on_grid_load=False, 
            allow_unsafe_jscode=True,
            update_mode=GridUpdateMode.VALUE_CHANGED,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED
        )

        # --- LOGICA DE AUTOGUARDADO EN BD ---
        df_new = pd.DataFrame(grid_response['data'])
        
        if not df_new.empty and not df_display.empty:
            df_old_idx = df_display.set_index("CODIGO")
            df_new_idx = df_new.set_index("CODIGO")
            cambios_detectados = False

            try:
                conn = psycopg2.connect(host=pg["host"], database=pg["database"], user=pg["user"], password=pg["password"], port=pg["port"])
                cur = conn.cursor()

                for codigo in df_new_idx.index:
                    if codigo not in df_old_idx.index: continue
                    
                    row_old = df_old_idx.loc[codigo]
                    row_new = df_new_idx.loc[codigo]

                    # 1. Detectar Cambios en Maestro Dosímetros
                    update_maestro = False
                    maestro_query = "UPDATE maestro_dosimetros SET "
                    maestro_params = []

                    # Nombre (Separación Apellidos / Nombre)
                    if str(row_old["NOMBRE"]) != str(row_new["NOMBRE"]):
                        partes = str(row_new["NOMBRE"]).split(",", 1)
                        apellidos = partes[0].strip()
                        nombre = partes[1].strip() if len(partes) > 1 else ""
                        maestro_query += "apellidos = %s, nombre = %s, "
                        maestro_params.extend([apellidos, nombre])
                        update_maestro = True

                    # Resto Maestro (Centro, DNI, Dosimetro)
                    for col, db_col in [("DNI", "dni"), ("CENTRO", "centro"), ("DOSIMETRO", "dosimetro")]:
                        if str(row_old[col]) != str(row_new[col]):
                            maestro_query += f"{db_col} = %s, "
                            maestro_params.append(str(row_new[col]))
                            update_maestro = True

                    # Fechas
                    for col, db_col in [("FECHA ALTA", "fecha_alta"), ("FECHA BAJA", "fecha_baja")]:
                        old_v = str(row_old[col]) if pd.notnull(row_old[col]) else ""
                        new_v = str(row_new[col]) if pd.notnull(row_new[col]) else ""
                        if old_v != new_v:
                            maestro_query += f"{db_col} = %s, "
                            maestro_params.append(new_v if new_v else None)
                            update_maestro = True

                    if update_maestro:
                        maestro_query = maestro_query.rstrip(", ") + " WHERE codigo = %s"
                        maestro_params.append(codigo)
                        cur.execute(maestro_query, maestro_params)
                        cambios_detectados = True

                    # 2. Detectar Cambios en Dosis Mensuales
                    def fmt_val(v, is_num=True):
                        if pd.isnull(v) or str(v).strip() == "": return None
                        if is_num:
                            try: return float(v)
                            except: return None
                        return str(v).strip()

                    for m in range(1, 13):
                        mes = meses_nombres[m]
                        
                        old_hsm = fmt_val(row_old[f"{mes}_HSM"])
                        new_hsm = fmt_val(row_new[f"{mes}_HSM"])
                        
                        old_hpm = fmt_val(row_old[f"{mes}_HPM"])
                        new_hpm = fmt_val(row_new[f"{mes}_HPM"])
                        
                        old_obs = fmt_val(row_old[f"{mes}_OBS"], is_num=False)
                        new_obs = fmt_val(row_new[f"{mes}_OBS"], is_num=False)
                        
                        if old_hsm != new_hsm or old_hpm != new_hpm or old_obs != new_obs:
                            fecha_sql = f"2026-{m:02d}-01"
                            cur.execute("""
                                INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET
                                    dosis_hsm = EXCLUDED.dosis_hsm,
                                    dosis_hpm = EXCLUDED.dosis_hpm,
                                    observaciones = EXCLUDED.observaciones;
                            """, (codigo, fecha_sql, new_hsm, new_hpm, new_obs))
                            cambios_detectados = True

                if cambios_detectados:
                    conn.commit()
                    st.cache_data.clear()
                    st.rerun()

            except Exception as e:
                st.error(f"Error al autoguardar: {e}")
            finally:
                if conn: conn.close()

    with tab_avisos:
        df_avisos = pd.read_sql_query("""
            SELECT a.id, a.codigo_dosimetro, m.nombre, m.apellidos, m.centro, m.dosimetro, TO_CHAR(a.periodo, 'YYYY-MM-DD') as periodo
            FROM avisos a JOIN maestro_dosimetros m ON a.codigo_dosimetro = m.codigo WHERE a.estado = 'Pendiente'
        """, engine)

        if df_avisos.empty:
            st.success("🎉 ¡Todo al día! No hay avisos pendientes.")
        else:
            st.warning(f"Tienes {len(df_avisos)} dosímetros pendientes de justificar.")
            for _, aviso in df_avisos.iterrows():
                with st.expander(f"⚠️ {aviso['apellidos']}, {aviso['nombre']} ({aviso['dosimetro']}) - Falta lectura de {aviso['periodo']}", expanded=False):
                    st.write(f"**Centro:** {aviso['centro']} | **Código:** {aviso['codigo_dosimetro']}")
                    col1, col2, col3 = st.columns([1, 1, 2])
                    with col1:
                        if st.button("❌ Marcar como No Entregado", key=f"btn_noent_{aviso['id']}"):
                            resolver_no_entregado(aviso['id'], aviso['codigo_dosimetro'], aviso['periodo'])
                    with col2:
                        fecha_baja = st.date_input("Fecha de Baja", key=f"date_baja_{aviso['id']}")
                        if st.button("🛑 Tramitar Baja", key=f"btn_baja_{aviso['id']}"):
                            resolver_baja(aviso['id'], aviso['codigo_dosimetro'], fecha_baja)

else:
    st.info("Despliega '⚙️ Opciones de Administración' para cargar el archivo Excel Maestro.")