import streamlit as st
import pandas as pd
import psycopg2
from lector import extraer_dosimetria_optimizada, guardar_en_bd
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode, GridUpdateMode

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(
    page_title="Control Dosimétrico", 
    page_icon="☢️", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# --- ESTILOS CSS AVANZADOS (UI/UX MODERN ERP) ---
st.markdown("""
    <style>
        /* Reducción estricta de márgenes de Streamlit */
        .block-container {
            padding-top: 0.8rem !important;
            padding-bottom: 0rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            max-width: 100% !important;
        }
        
        /* Ocultar elementos nativos innecesarios */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header[data-testid="stHeader"] {background: transparent;}
        
        /* Estilizado de tarjetas KPI superiores */
        .kpi-card {
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 12px 18px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .kpi-title {
            font-size: 0.75rem;
            text-transform: uppercase;
            font-weight: 600;
            color: #64748b;
            letter-spacing: 0.05em;
        }
        .kpi-value {
            font-size: 1.35rem;
            font-weight: 700;
            color: #0f172a;
        }

        /* Personalización de AG Grid mediante Variables CSS */
        .ag-theme-alpine {
            --ag-header-background-color: #f8fafc;
            --ag-header-foreground-color: #1e293b;
            --ag-border-color: #cbd5e1;
            --ag-secondary-border-color: #e2e8f0;
            --ag-row-hover-color: #f1f5f9;
            --ag-selected-row-background-color: #e0f2fe;
            --ag-font-size: 12px;
            --ag-font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #cbd5e1 !important;
        }
        
        /* Estilizado de las cabeceras agrupadas */
        .ag-header-group-cell-label {
            justify-content: center;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }
    </style>
""", unsafe_allow_html=True)

# --- BARRA LATERAL: INGESTA Y CONTROLES ---
st.sidebar.markdown("### 📄 Gestión de Archivos")
archivos_pdf = st.sidebar.file_uploader(
    "Subir informes mensuales (PDF)", 
    type="pdf", 
    accept_multiple_files=True,
    help="Selecciona uno o varios PDFs de dosimetría para procesar e inyectar en la base de datos."
)

# --- GUARDADO CON SECRETS ---
if st.sidebar.button("⚙️ Procesar y Cargar", use_container_width=True):
    if archivos_pdf:
        exitos = 0
        for archivo in archivos_pdf:
            with st.spinner(f"Analizando {archivo.name}..."):
                df_extraido = extraer_dosimetria_optimizada(archivo)
                if not df_extraido.empty:
                    # Le pasamos los secrets de la base de datos
                    guardar_en_bd(df_extraido, st.secrets["postgres"])
                    exitos += 1
                else:
                    st.sidebar.error(f"Error procesando {archivo.name}")
        if exitos > 0:
            st.sidebar.success(f"✅ {exitos} archivo(s) guardado(s).")
            st.cache_data.clear()
    else:
        st.sidebar.warning("Selecciona al menos un archivo PDF.")

# --- LECTURA CON SECRETS ---
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
        query = """
            SELECT t.nombre_apellidos AS "Nombre", c.nombre AS "CENTRO", r.tipo_dosimetro AS "Dosímetro",
                   r.codigo_trabajador, r.codigo_dosimetro, r.periodo, r.dosis_hsm, r.dosis_hpm
            FROM registros_dosimetria r
            JOIN trabajadores t ON r.codigo_trabajador = t.codigo 
            JOIN centros c ON r.codigo_centro = c.codigo
        """
        df = pd.read_sql_query(query, conexion)
        conexion.close()
    except Exception as e:
        st.error(f"Error al conectar con la base de datos: {e}")
        return pd.DataFrame()


    if df.empty: 
        return df

    # Normalización de metadatos
    df["DNI"] = ""
    df["Código"] = df["codigo_trabajador"] + "." + df["codigo_dosimetro"]
    df["Fecha Alta"] = ""
    df["Fecha Baja"] = ""
    df["periodo"] = pd.to_datetime(df["periodo"])
    df["Mes_Num"] = df["periodo"].dt.month

    # Pivot de datos
    df_pivot = df.pivot_table(
        index=["Nombre", "DNI", "CENTRO", "Dosímetro", "Código", "Fecha Alta", "Fecha Baja"],
        columns="Mes_Num", values=["dosis_hsm", "dosis_hpm"], aggfunc="sum"
    ).reset_index()

    # Construcción de estructura plana
    df_flat = pd.DataFrame()
    df_flat["NOMBRE"] = df_pivot[("Nombre", "")]
    df_flat["DNI"] = df_pivot[("DNI", "")]
    df_flat["CENTRO"] = df_pivot[("CENTRO", "")]
    df_flat["DOSIMETRO"] = df_pivot[("Dosímetro", "")]
    df_flat["CODIGO"] = df_pivot[("Código", "")]
    df_flat["FECHA ALTA"] = df_pivot[("Fecha Alta", "")]
    df_flat["FECHA BAJA"] = df_pivot[("Fecha Baja", "")]

    meses_nombres = {
        1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO',
        7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'
    }

    for m in range(1, 13):
        mes = meses_nombres[m]
        df_flat[f"{mes}_HSM"] = df_pivot[('dosis_hsm', m)] if ('dosis_hsm', m) in df_pivot else None
        df_flat[f"{mes}_HPM"] = df_pivot[('dosis_hpm', m)] if ('dosis_hpm', m) in df_pivot else None
        df_flat[f"{mes}_OBS"] = ""

    # Acumulados Anuales
    cols_hsm = [('dosis_hsm', m) for m in range(1, 13) if ('dosis_hsm', m) in df_pivot]
    cols_hpm = [('dosis_hpm', m) for m in range(1, 13) if ('dosis_hpm', m) in df_pivot]
    
    df_flat["ACUMULADO_HSM"] = df_pivot[cols_hsm].sum(axis=1) if cols_hsm else 0.0
    df_flat["ACUMULADO_HPM"] = df_pivot[cols_hpm].sum(axis=1) if cols_hpm else 0.0

    return df_flat, meses_nombres

# --- MOSTRAR PANEL Y TABLA ---
resultados = cargar_y_transformar_datos()

if isinstance(resultados, tuple):
    df_flat, meses_nombres = resultados
    
    # Filtro lateral
    st.sidebar.markdown("### 🔍 Filtros de Vista")
    centros = ["Todos"] + sorted(df_flat["CENTRO"].unique().tolist())
    centro_sel = st.sidebar.selectbox("Centro de Trabajo:", centros)
    
    if centro_sel != "Todos":
        df_display = df_flat[df_flat["CENTRO"] == centro_sel].copy()
    else:
        df_display = df_flat.copy()

    # --- PANEL DE METRICAS SUPERIORES (KPIS) ---
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="kpi-card"><div class="kpi-title">Trabajadores</div><div class="kpi-value">{len(df_display)}</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="kpi-card"><div class="kpi-title">Centro Seleccionado</div><div class="kpi-value" style="font-size: 1.1rem; line-height: 1.6;">{centro_sel}</div></div>', unsafe_allow_html=True)
    with col3:
        dosis_max = df_display["ACUMULADO_HSM"].max() if not df_display.empty else 0.0
        st.markdown(f'<div class="kpi-card"><div class="kpi-title">Dosis Máx. Acumulada</div><div class="kpi-value">{dosis_max:.2f} mSv</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="kpi-card"><div class="kpi-title">Estado Base de Datos</div><div class="kpi-value" style="color: #16a34a; font-size: 1.1rem; line-height: 1.6;">● Conectado</div></div>', unsafe_allow_html=True)

    st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)

    # --- CONFIGURACIÓN DE COLUMNAS DE AG GRID ---
    column_defs = [
        {
            "field": "NOMBRE", 
            "headerName": "NOMBRE", 
            "width": 320, 
            "minWidth": 260, 
            "pinned": "left", 
            "suppressSizeToFit": True,
            "cellStyle": {"fontWeight": "600", "color": "#0f172a"}
        },
        {"field": "DNI", "headerName": "DNI", "width": 130, "minWidth": 110, "suppressSizeToFit": True},
        {"field": "CENTRO", "headerName": "CENTRO", "width": 260, "minWidth": 200, "suppressSizeToFit": True},
        {"field": "DOSIMETRO", "headerName": "DOSIMETRO", "width": 140, "minWidth": 120, "suppressSizeToFit": True},
        {"field": "CODIGO", "headerName": "CODIGO", "width": 140, "minWidth": 110, "suppressSizeToFit": True},
        {"field": "FECHA ALTA", "headerName": "FECHA ALTA", "width": 130, "minWidth": 110, "suppressSizeToFit": True},
        {"field": "FECHA BAJA", "headerName": "FECHA BAJA", "width": 130, "minWidth": 110, "suppressSizeToFit": True},
    ]

    # Configuración de los 12 meses
    for m in range(1, 13):
        mes = meses_nombres[m]
        column_defs.append({
            "headerName": mes,
            "children": [
                {
                    "field": f"{mes}_HSM", 
                    "headerName": "HSM", 
                    "width": 95, 
                    "minWidth": 85, 
                    "type": ["numericColumn"], 
                    "cellStyle": {"textAlign": "right", "color": "#334155"},
                    "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)",
                    "suppressSizeToFit": True
                },
                {
                    "field": f"{mes}_HPM", 
                    "headerName": "HPM", 
                    "width": 95, 
                    "minWidth": 85, 
                    "type": ["numericColumn"], 
                    "cellStyle": {"textAlign": "right", "color": "#334155"},
                    "valueFormatter": "x === null || x === undefined ? '' : Number(x).toFixed(2)",
                    "suppressSizeToFit": True
                },
                {
                    "field": f"{mes}_OBS", 
                    "headerName": "Observaciones", 
                    "width": 220, 
                    "minWidth": 180, 
                    "editable": True,
                    "cellStyle": {"backgroundColor": "#fefce8"}, # Destacado tenue para avisar que es editable
                    "suppressSizeToFit": True
                }
            ]
        })

    # Configuración del bloque acumulado (Destacado en azul grisáceo)
    column_defs.append({
        "headerName": "ACUMULADO ANUAL",
        "children": [
            {
                "field": "ACUMULADO_HSM", 
                "headerName": "HSM", 
                "width": 130, 
                "minWidth": 110, 
                "type": ["numericColumn"], 
                "cellStyle": {"fontWeight": "700", "textAlign": "right", "backgroundColor": "#f1f5f9", "color": "#1e3a8a"},
                "valueFormatter": "x === null || x === undefined ? '0.00' : Number(x).toFixed(2)",
                "suppressSizeToFit": True
            },
            {
                "field": "ACUMULADO_HPM", 
                "headerName": "HPM", 
                "width": 130, 
                "minWidth": 110, 
                "type": ["numericColumn"], 
                "cellStyle": {"fontWeight": "700", "textAlign": "right", "backgroundColor": "#f1f5f9", "color": "#1e3a8a"},
                "valueFormatter": "x === null || x === undefined ? '0.00' : Number(x).toFixed(2)",
                "suppressSizeToFit": True
            }
        ]
    })

    gb = GridOptionsBuilder.from_dataframe(df_display)
    gb.configure_default_column(resizable=True, suppressSizeToFit=True)
    
    # Opciones globales del Grid
    gridOptions = gb.build()
    gridOptions["columnDefs"] = column_defs
    gridOptions["headerHeight"] = 34
    gridOptions["groupHeaderHeight"] = 34
    gridOptions["rowHeight"] = 32

    # Renderizado final con AG Grid
    AgGrid(
        df_display, 
        gridOptions=gridOptions, 
        update_mode=GridUpdateMode.MODEL_CHANGED,
        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
        height=720, 
        theme='alpine',
        fit_columns_on_grid_load=False,
        allow_unsafe_jscode=True
    )
else:
    st.info("Sube un informe en PDF desde el panel lateral para iniciar la visualización.")