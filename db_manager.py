import pandas as pd
import psycopg2
from sqlalchemy import create_engine, text
import unicodedata

# --- FUNCIONES AUXILIARES ---
def quitar_tildes(texto):
    if not isinstance(texto, str): return ""
    texto_limpio = "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').upper()
    return texto_limpio.replace('CH', 'CZZZ')

# --- CONEXIÓN Y CONFIGURACIÓN ---
def iniciar_conexion(pg_secrets):
    db_url = f"postgresql://{pg_secrets['user']}:{pg_secrets['password']}@{pg_secrets['host']}:{pg_secrets['port']}/{pg_secrets['database']}"
    return create_engine(db_url, client_encoding='utf8')

def inicializar_base_datos(engine):
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

                CREATE INDEX IF NOT EXISTS idx_maestro_centro ON maestro_dosimetros(centro);
                CREATE INDEX IF NOT EXISTS idx_registros_cod_per ON registros_dosimetria(codigo_dosimetro, periodo);
                CREATE INDEX IF NOT EXISTS idx_avisos_cod_per ON avisos(codigo_dosimetro, periodo);
                CREATE INDEX IF NOT EXISTS idx_avisos_estado ON avisos(estado);
            """))
    except Exception as e:
        print(f"Error en migración inicial: {e}")

# --- LECTURA DE DATOS ---
def cargar_datos_completos(engine):
    try:
        query_maestro = "SELECT codigo AS \"CODIGO\", CONCAT(apellidos, ', ', nombre) AS \"NOMBRE\", dni AS \"DNI\", centro AS \"CENTRO\", dosimetro AS \"DOSIMETRO\", TO_CHAR(fecha_alta, 'YYYY-MM-DD') AS \"FECHA ALTA\", TO_CHAR(fecha_baja, 'YYYY-MM-DD') AS \"FECHA BAJA\" FROM maestro_dosimetros"
        df_maestro = pd.read_sql_query(query_maestro, engine)
        if df_maestro.empty: return pd.DataFrame(), {}

        query_dosis = "SELECT codigo_dosimetro AS \"CODIGO\", EXTRACT(MONTH FROM periodo)::INTEGER AS \"Mes_Num\", dosis_hsm, dosis_hpm, observaciones FROM registros_dosimetria"
        df_dosis = pd.read_sql_query(query_dosis, engine)
    except Exception:
        return pd.DataFrame(), {}

    meses_nombres = {1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'}
    
    df_flat = df_maestro.copy()
    for m in range(1, 13):
        mes = meses_nombres[m]
        df_flat[f"{mes}_HSM"] = None; df_flat[f"{mes}_HPM"] = None; df_flat[f"{mes}_OBS"] = ""

    if not df_dosis.empty:
        df_dosis['Mes_Nombre'] = df_dosis['Mes_Num'].map(meses_nombres)
        df_pivot = df_dosis.pivot_table(index='CODIGO', columns='Mes_Nombre', values=['dosis_hsm', 'dosis_hpm', 'observaciones'], aggfunc='first')
        
        nuevas_cols = []
        for col in df_pivot.columns:
            metrica = "HSM" if col[0] == "dosis_hsm" else "HPM" if col[0] == "dosis_hpm" else "OBS"
            nuevas_cols.append(f"{col[1]}_{metrica}")
        df_pivot.columns = nuevas_cols
        
        df_flat = df_flat.set_index('CODIGO')
        df_flat.update(df_pivot)
        df_flat = df_flat.reset_index()

    cols_hsm = [f"{meses_nombres[m]}_HSM" for m in range(1, 13)]
    cols_hpm = [f"{meses_nombres[m]}_HPM" for m in range(1, 13)]
    df_flat["ACUMULADO_HSM"] = df_flat[cols_hsm].apply(pd.to_numeric, errors='coerce').sum(axis=1)
    df_flat["ACUMULADO_HPM"] = df_flat[cols_hpm].apply(pd.to_numeric, errors='coerce').sum(axis=1)

    df_flat['es_especial'] = df_flat['NOMBRE'].astype(str).str.upper().apply(lambda x: 1 if ('AREA' in x or 'SUPLENTE' in x) else 0)
    df_flat['nombre_sort'] = df_flat['NOMBRE'].astype(str).str.strip().apply(quitar_tildes)
    df_flat['es_solapa'] = df_flat['DOSIMETRO'].astype(str).str.strip().str.upper().apply(lambda x: 0 if 'SOLAPA' in x else 1)

    df_flat = df_flat.sort_values(by=['es_especial', 'nombre_sort', 'es_solapa', 'CODIGO']).drop(columns=['es_especial', 'nombre_sort', 'es_solapa'])
    return df_flat, meses_nombres

def obtener_avisos_pendientes(engine):
    return pd.read_sql_query("""
        SELECT a.codigo_dosimetro, TO_CHAR(a.periodo, 'YYYY-MM-DD') as periodo, COALESCE(a.tipo_aviso, 'Faltan lecturas') as tipo_aviso
        FROM avisos a 
        WHERE a.estado = 'Pendiente'
          AND (
              a.tipo_aviso IN ('Alta nueva', 'Falta DNI') 
              OR (a.tipo_aviso = 'Faltan lecturas' AND NOT EXISTS (
                  SELECT 1 FROM registros_dosimetria r 
                  WHERE r.codigo_dosimetro = a.codigo_dosimetro AND r.periodo = a.periodo 
                    AND (r.dosis_hsm IS NOT NULL OR r.dosis_hpm IS NOT NULL OR (r.observaciones IS NOT NULL AND r.observaciones != ''))
              ))
          )
    """, engine)

def guardar_cambios_tabla(pg_secrets, df_old, df_new, meses_nombres):
    def safe_str(val): return "" if pd.isnull(val) else str(val).strip()
    
    def normalizar_valor(val):
        if pd.isnull(val) or val is None: return ""
        s = str(val).strip()
        if s.lower() in ["nan", "none", "null"]: return ""
        try:
            return f"{float(s.replace(',', '.')):.2f}"
        except ValueError:
            return s.upper()

    try:
        cols_comp = [c for c in df_old.columns if c in df_new.columns and c not in ["MESES_FALTANTES"]]
        
        old_comp = df_old[cols_comp].set_index("CODIGO")
        new_comp = df_new[cols_comp].set_index("CODIGO")
        
        common_indices = new_comp.index.intersection(old_comp.index)
        old_comp = old_comp.loc[common_indices]
        new_comp = new_comp.loc[common_indices]
        
        # Normalización estricta para evitar fallos de tipos al comparar
        old_norm = old_comp.apply(lambda col: col.map(normalizar_valor))
        new_norm = new_comp.apply(lambda col: col.map(normalizar_valor))
        
        changed_mask = (old_norm != new_norm).any(axis=1)
        changed_codigos = changed_mask[changed_mask].index.tolist()
        
        if not changed_codigos:
            return True, None, False

        cambios_realizados = False
        conn = psycopg2.connect(host=pg_secrets["host"], database=pg_secrets["database"], user=pg_secrets["user"], password=pg_secrets["password"], port=pg_secrets["port"])
        cur = conn.cursor()

        for codigo in changed_codigos:
            row_old = old_comp.loc[codigo]
            row_new = new_comp.loc[codigo]

            update_maestro = False
            maestro_query = "UPDATE maestro_dosimetros SET "
            maestro_params = []
            
            if safe_str(row_old["NOMBRE"]) != safe_str(row_new["NOMBRE"]):
                partes = safe_str(row_new["NOMBRE"]).split(",", 1)
                maestro_query += "apellidos = %s, nombre = %s, "
                maestro_params.extend([partes[0].strip(), partes[1].strip() if len(partes) > 1 else ""])
                update_maestro = True

            for df_col, db_col in [("DNI", "dni"), ("CENTRO", "centro"), ("DOSIMETRO", "dosimetro"), ("FECHA ALTA", "fecha_alta"), ("FECHA BAJA", "fecha_baja")]:
                if safe_str(row_old[df_col]) != safe_str(row_new[df_col]):
                    val = safe_str(row_new[df_col])
                    if db_col in ["fecha_alta", "fecha_baja"] and val:
                        try: val = pd.to_datetime(val, dayfirst=True).strftime('%Y-%m-%d')
                        except Exception: raise ValueError(f"Fecha incorrecta en {df_col}. Usa YYYY-MM-DD o DD/MM/YYYY.")
                    maestro_query += f"{db_col} = %s, "
                    maestro_params.append(val if val else None)
                    update_maestro = True

            if update_maestro:
                maestro_query = maestro_query.rstrip(", ") + " WHERE codigo = %s"
                maestro_params.append(codigo)
                cur.execute(maestro_query, maestro_params)
                
                dni_new = safe_str(row_new["DNI"])
                if dni_new:
                    cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND tipo_aviso IN ('Alta nueva', 'Falta DNI');", (codigo,))
                else:
                    cur.execute("UPDATE avisos SET tipo_aviso = 'Falta DNI' WHERE codigo_dosimetro = %s AND tipo_aviso = 'Alta nueva' AND estado = 'Pendiente';", (codigo,))

                val_baja = safe_str(row_new["FECHA BAJA"])
                if val_baja:
                    cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND periodo >= %s AND estado = 'Pendiente';", (codigo, val_baja))
                cambios_realizados = True

            if safe_str(row_new.get("ESTADO_AVISO")) == "IGNORADO":
                cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND tipo_aviso IN ('Alta nueva', 'Falta DNI');", (codigo,))
                cambios_realizados = True

            def fmt_val(v, is_num=True):
                if pd.isnull(v) or str(v).strip() in ["", "nan"]: return None
                if is_num:
                    try: return float(str(v).replace(",", "."))
                    except: return None
                return str(v).strip()

            for m in range(1, 13):
                mes = meses_nombres[m]
                new_obs = fmt_val(row_new[f"{mes}_OBS"], False)
                new_hsm = fmt_val(row_new[f"{mes}_HSM"])
                new_hpm = fmt_val(row_new[f"{mes}_HPM"])
                
                if new_obs and ("no entregado" in new_obs.lower() or "baja" in new_obs.lower()):
                    new_hsm, new_hpm = None, None
                    
                old_obs = fmt_val(row_old[f"{mes}_OBS"], False)
                old_hsm = fmt_val(row_old[f"{mes}_HSM"])
                old_hpm = fmt_val(row_old[f"{mes}_HPM"])

                if old_hsm != new_hsm or old_hpm != new_hpm or old_obs != new_obs:
                    fecha_sql = f"2026-{m:02d}-01"
                    cur.execute("""
                        INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones) 
                        VALUES (%s, %s, %s, %s, %s) 
                        ON CONFLICT (codigo_dosimetro, periodo) 
                        DO UPDATE SET dosis_hsm = EXCLUDED.dosis_hsm, dosis_hpm = EXCLUDED.dosis_hpm, observaciones = EXCLUDED.observaciones;
                    """, (codigo, fecha_sql, new_hsm, new_hpm, new_obs))
                    
                    if new_hsm is not None or new_hpm is not None or new_obs:
                        cur.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Faltan lecturas';", (codigo, fecha_sql))
                    cambios_realizados = True

        conn.commit()
        return True, None, cambios_realizados
    except Exception as e:
        if 'conn' in locals() and conn: conn.rollback()
        return False, str(e), False
    finally:
        if 'cur' in locals() and cur: cur.close()
        if 'conn' in locals() and conn: conn.close()

# --- ADMINISTRACIÓN ---
def obtener_meses_disponibles(engine):
    df_p = pd.read_sql_query("SELECT DISTINCT TO_CHAR(periodo, 'YYYY-MM-DD') AS periodo FROM registros_dosimetria ORDER BY periodo DESC", engine)
    return ["Todos los meses"] + (df_p['periodo'].tolist() if not df_p.empty else [])

def ejecutar_borrado_datos(pg_secrets, mes_a_borrar, centro_a_borrar):
    conn = psycopg2.connect(host=pg_secrets["host"], database=pg_secrets["database"], user=pg_secrets["user"], password=pg_secrets["password"], port=pg_secrets["port"])
    cur = conn.cursor()
    try:
        if mes_a_borrar == "Todos los meses":
            if centro_a_borrar == "Todos los centros":
                cur.execute("DELETE FROM registros_dosimetria WHERE codigo_dosimetro IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva');")
                cur.execute("DELETE FROM maestro_dosimetros WHERE codigo IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva');")
                cur.execute("DELETE FROM registros_dosimetria;")
                cur.execute("DELETE FROM avisos;")
                cur.execute("UPDATE maestro_dosimetros SET fecha_baja = NULL;")
            else:
                cur.execute("DELETE FROM registros_dosimetria WHERE codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s AND codigo IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva'));", (centro_a_borrar,))
                cur.execute("DELETE FROM maestro_dosimetros WHERE centro = %s AND codigo IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva');", (centro_a_borrar,))
                cur.execute("DELETE FROM registros_dosimetria WHERE codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s);", (centro_a_borrar,))
                cur.execute("DELETE FROM avisos WHERE codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s);", (centro_a_borrar,))
                cur.execute("UPDATE maestro_dosimetros SET fecha_baja = NULL WHERE centro = %s;", (centro_a_borrar,))
        else:
            if centro_a_borrar == "Todos los centros":
                cur.execute("DELETE FROM registros_dosimetria WHERE codigo_dosimetro IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva' AND periodo = %s);", (mes_a_borrar,))
                cur.execute("DELETE FROM maestro_dosimetros WHERE codigo IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva' AND periodo = %s);", (mes_a_borrar,))
                cur.execute("DELETE FROM registros_dosimetria WHERE periodo = %s;", (mes_a_borrar,))
                cur.execute("DELETE FROM avisos WHERE periodo = %s;", (mes_a_borrar,))
                cur.execute("UPDATE maestro_dosimetros SET fecha_baja = NULL WHERE DATE_TRUNC('month', CAST(fecha_baja AS DATE)) = DATE_TRUNC('month', CAST(%s AS DATE));", (mes_a_borrar,))
            else:
                cur.execute("DELETE FROM registros_dosimetria WHERE codigo_dosimetro IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva' AND periodo = %s) AND codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s);", (mes_a_borrar, centro_a_borrar))
                cur.execute("DELETE FROM maestro_dosimetros WHERE centro = %s AND codigo IN (SELECT DISTINCT codigo_dosimetro FROM avisos WHERE tipo_aviso = 'Alta nueva' AND periodo = %s);", (centro_a_borrar, mes_a_borrar))
                cur.execute("DELETE FROM registros_dosimetria WHERE periodo = %s AND codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s);", (mes_a_borrar, centro_a_borrar))
                cur.execute("DELETE FROM avisos WHERE periodo = %s AND codigo_dosimetro IN (SELECT codigo FROM maestro_dosimetros WHERE centro = %s);", (mes_a_borrar, centro_a_borrar))
                cur.execute("UPDATE maestro_dosimetros SET fecha_baja = NULL WHERE centro = %s AND DATE_TRUNC('month', CAST(fecha_baja AS DATE)) = DATE_TRUNC('month', CAST(%s AS DATE));", (centro_a_borrar, mes_a_borrar))
        conn.commit()
        return True, None
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        cur.close()
        conn.close()

def exportar_backup(engine):
    return pd.read_sql_query("SELECT * FROM registros_dosimetria", engine)