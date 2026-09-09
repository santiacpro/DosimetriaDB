import re
import pandas as pd
import pdfplumber
import psycopg2

# --- PATRONES DE EXPRESIONES REGULARES ---
PATRON_CODIGO = re.compile(r"\b(\d{5,6})\.(\d{1,2})\b|\b(\d{7,8})\b")
PATRON_DECIMALES = re.compile(r"\b\d{1,4}[,.]\d{2,3}\b")
PATRON_FECHAS = re.compile(r'\b\d{2}/\d{2}/\d{4}\b')
PATRON_REF = re.compile(r'\b\d{6}-\d{4}\b')
PATRON_NUMEROS = re.compile(r'\b\d+\b')
PATRON_RUIDO = re.compile(r'\b(DLA|DILA|MAS|Zero|per|ser|inferior|a|mSv/mes|CSNGS|CSN-GS|Dosimetria|Anell|Canell|SUPLENTE|VIAJE)\b', re.IGNORECASE)


def normalizar_codigo(codigo_raw):
    if not codigo_raw or str(codigo_raw).lower() == 'nan':
        return ""
    s = str(codigo_raw).strip()
    if s.endswith('.0'):
        s = s[:-2]
    if '.' in s:
        partes = s.split('.')
        return f"{partes[0].zfill(6)}.{partes[1].zfill(2)}"
    s_padded = s.zfill(8)
    return f"{s_padded[:6]}.{s_padded[-2:]}"


def separar_nombre_apellidos(cadena_completa):
    partes = str(cadena_completa).strip().split()
    if not partes:
        return "Desconocido", ""
    if len(partes) == 1:
        return partes[0], ""
    nombre = partes[-1]
    apellidos = " ".join(partes[:-1])
    return apellidos, nombre


def procesar_excel_centros(archivo_excel, db_config):
    """
    Carga el Excel de Centros (columnas NOMBRE CENTRO y CODIGO CENTRO)
    y nutre la tabla mapa_centros.
    """
    try:
        df = pd.read_excel(archivo_excel, dtype=str)
        df.columns = df.columns.astype(str).str.strip().str.upper()

        col_nombre = next((c for c in df.columns if "NOMBRE" in c), None)
        col_codigo = next((c for c in df.columns if "CODIGO" in c or "COD" in c), None)

        if not col_nombre or not col_codigo:
            return False, "❌ El archivo debe contener las columnas 'NOMBRE CENTRO' y 'CODIGO CENTRO'."

        conexion = psycopg2.connect(
            host=db_config["host"], database=db_config["database"],
            user=db_config["user"], password=db_config["password"],
            port=db_config["port"], client_encoding="utf8"
        )
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mapa_centros (
                codigo_pdf VARCHAR PRIMARY KEY,
                nombre_centro_bd VARCHAR NOT NULL
            );
        """)

        insertados = 0
        for _, fila in df.iterrows():
            cod_pdf = str(fila.get(col_codigo, '')).strip()
            nom_bd = str(fila.get(col_nombre, '')).strip()

            if cod_pdf and nom_bd and cod_pdf.lower() != 'nan':
                cod_limpio = cod_pdf.lstrip('0')
                
                cursor.execute("""
                    INSERT INTO mapa_centros (codigo_pdf, nombre_centro_bd)
                    VALUES (%s, %s)
                    ON CONFLICT (codigo_pdf) DO UPDATE SET nombre_centro_bd = EXCLUDED.nombre_centro_bd;
                """, (cod_pdf, nom_bd))
                
                if cod_limpio != cod_pdf:
                    cursor.execute("""
                        INSERT INTO mapa_centros (codigo_pdf, nombre_centro_bd)
                        VALUES (%s, %s)
                        ON CONFLICT (codigo_pdf) DO UPDATE SET nombre_centro_bd = EXCLUDED.nombre_centro_bd;
                    """, (cod_limpio, nom_bd))

                insertados += 1

        conexion.commit()
        cursor.close()
        conexion.close()
        return True, f"✅ Mapa de Centros actualizado: {insertados} registros procesados."
    except Exception as e:
        return False, f"❌ Error en Excel de Centros: {str(e)}"


def procesar_excel_maestro(archivo_excel, db_config):
    try:
        df = pd.read_excel(archivo_excel, dtype=str)
        df.columns = (
            df.columns.astype(str)
            .str.strip()
            .str.upper()
            .str.replace('Ó', 'O')
            .str.replace('Í', 'I')
            .str.replace('Á', 'A')
            .str.replace('É', 'E')
            .str.replace('Ú', 'U')
        )

        conexion = psycopg2.connect(
            host=db_config["host"], database=db_config["database"],
            user=db_config["user"], password=db_config["password"],
            port=db_config["port"], client_encoding="utf8"
        )
        cursor = conexion.cursor()

        insertados = 0
        for _, fila in df.iterrows():
            codigo_normalizado = normalizar_codigo(fila.get('CODIGO'))
            if not codigo_normalizado:
                continue

            alta = pd.to_datetime(fila.get('ALTA'), errors='coerce')
            baja = pd.to_datetime(fila.get('BAJA'), errors='coerce')

            alta_str = alta.strftime('%Y-%m-%d') if pd.notnull(alta) else None
            baja_str = baja.strftime('%Y-%m-%d') if pd.notnull(baja) else None

            cursor.execute("""
                INSERT INTO maestro_dosimetros (codigo, apellidos, nombre, dni, centro, dosimetro, fecha_alta, fecha_baja)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (codigo) DO UPDATE SET
                    apellidos = EXCLUDED.apellidos,
                    nombre = EXCLUDED.nombre,
                    dni = EXCLUDED.dni,
                    centro = EXCLUDED.centro,
                    dosimetro = EXCLUDED.dosimetro,
                    fecha_alta = EXCLUDED.fecha_alta,
                    fecha_baja = EXCLUDED.fecha_baja;
            """, (
                codigo_normalizado,
                str(fila.get('APELLIDOS', '')).strip() if pd.notnull(fila.get('APELLIDOS')) and str(fila.get('APELLIDOS')).lower() != 'nan' else '',
                str(fila.get('NOMBRE', '')).strip() if pd.notnull(fila.get('NOMBRE')) and str(fila.get('NOMBRE')).lower() != 'nan' else '',
                str(fila.get('DNI', '')).strip() if pd.notnull(fila.get('DNI')) and str(fila.get('DNI')).lower() != 'nan' else '',
                str(fila.get('CENTRO', '')).strip() if pd.notnull(fila.get('CENTRO')) and str(fila.get('CENTRO')).lower() != 'nan' else '',
                str(fila.get('DOSIMETRO', '')).strip() if pd.notnull(fila.get('DOSIMETRO')) and str(fila.get('DOSIMETRO')).lower() != 'nan' else '',
                alta_str,
                baja_str
            ))
            insertados += 1

        conexion.commit()
        cursor.close()
        conexion.close()
        return True, f"✅ Excel Maestro cargado: {insertados} registros procesados."
    except Exception as e:
        return False, f"❌ Error en Excel Maestro: {str(e)}"


def extraer_dosimetria_optimizada(archivo_pdf):
    registros = []
    nombres_por_usuario = {}
    
    with pdfplumber.open(archivo_pdf) as pdf:
        mes_informe = "GENER 2026"
        centro_pdf_detectado = "Centro Desconocido"
        
        for pagina in pdf.pages:
            texto = pagina.extract_text(layout=True)
            if not texto: 
                continue
            
            for linea in texto.split('\n'):
                linea_limpia = linea.strip()
                if not linea_limpia: 
                    continue

                if re.search(r'\bVIAJE\b', linea_limpia, re.IGNORECASE):
                    continue

                if "INFORME MENSUAL" in linea_limpia.upper():
                    mes_informe = linea_limpia.split("PERSONAL ")[-1].strip()

                if "CENTRE:" in linea_limpia.upper() or "CENTRO:" in linea_limpia.upper():
                    partes_c = re.split(r'CENTRE:|CENTRO:', linea_limpia, flags=re.IGNORECASE)
                    if len(partes_c) > 1:
                        centro_pdf_detectado = partes_c[1].strip()

                match_codigo = PATRON_CODIGO.search(linea_limpia)
                if match_codigo:
                    try:
                        codigo_raw = match_codigo.group(0)
                        codigo_completo = normalizar_codigo(codigo_raw)
                        codigo_usuario = codigo_completo.split('.')[0] if '.' in codigo_completo else codigo_completo
                        
                        linea_sin_id = linea_limpia.replace(codigo_raw, "")
                        
                        es_anillo = bool(re.search(r'\b(anell|anillo)\b', linea_sin_id, re.IGNORECASE))
                        es_muneca = bool(re.search(r'\b(canell|muñeca)\b', linea_sin_id, re.IGNORECASE))
                        es_extremidad = es_anillo or es_muneca
                        
                        tipo_dosimetro = "Anillo" if es_anillo else ("Muñeca" if es_muneca else "Solapa")
                        
                        txt_nombres = PATRON_FECHAS.sub('', linea_sin_id)
                        txt_nombres = PATRON_REF.sub('', txt_nombres)
                        txt_nombres = PATRON_DECIMALES.sub('', txt_nombres)
                        txt_nombres = PATRON_NUMEROS.sub('', txt_nombres)
                        txt_nombres = PATRON_RUIDO.sub('', txt_nombres)
                        txt_nombres = re.sub(r'[|\-:]', '', txt_nombres)
                        
                        posible_nombre = " ".join(txt_nombres.split()).strip()
                        if len(posible_nombre) > 4:
                            nombres_por_usuario[codigo_usuario] = posible_nombre
                            
                        nombre_final = nombres_por_usuario.get(codigo_usuario, "Desconocido")

                        if re.search(r'\bVIAJE\b', nombre_final, re.IGNORECASE):
                            continue
                        
                        linea_para_dosis = PATRON_FECHAS.sub('', linea_sin_id)
                        linea_para_dosis = PATRON_REF.sub('', linea_para_dosis)
                        linea_para_dosis = re.sub(r'0[,.]10\s*mSv/mes', '', linea_para_dosis, flags=re.IGNORECASE)
                        linea_para_dosis = re.sub(r'\(CSN.*?\)', '', linea_para_dosis, flags=re.IGNORECASE)
                        
                        numeros_decimales = PATRON_DECIMALES.findall(linea_para_dosis)
                        if not numeros_decimales:
                            continue
                            
                        def to_float(val_str):
                            try: return float(val_str.replace(",", "."))
                            except: return 0.0

                        if len(numeros_decimales) >= 4:
                            hsm_val = to_float(numeros_decimales[-4])
                            hpm_val = to_float(numeros_decimales[-3])
                        elif len(numeros_decimales) in [2, 3]:
                            hsm_val = 0.0
                            hpm_val = to_float(numeros_decimales[-2])
                        else:
                            hsm_val = 0.0
                            hpm_val = to_float(numeros_decimales[0])

                        if hsm_val < 0.10: hsm_val = 0.0
                        if hpm_val < 0.10: hpm_val = 0.0

                        if es_extremidad:
                            hsm_float = hpm_val
                            hpm_float = None
                        else:
                            hsm_float = hsm_val
                            hpm_float = hpm_val
                        
                        registros.append({
                            "Periodo": mes_informe,
                            "Codigo_Dosimetro": codigo_completo,
                            "Nombre_Apellidos": nombre_final,
                            "Tipo_Dosimetro": tipo_dosimetro,
                            "Centro_PDF": centro_pdf_detectado,
                            "Dosis_HSM": hsm_float,
                            "Dosis_HPM": hpm_float
                        })
                    except Exception as e:
                        print(f"Error procesando línea '{linea_limpia}': {e}")
                            
    return pd.DataFrame(registros)


def obtener_centro_mapeado(cursor, centro_pdf):
    if not centro_pdf or str(centro_pdf).strip() in ['', 'None', 'Centro Desconocido']:
        return "Centro Desconocido"
    
    centro_clean = str(centro_pdf).strip()
    num_centro = re.search(r'\b\d{4,6}\b', centro_clean)
    clave_pdf = num_centro.group(0) if num_centro else centro_clean

    try:
        cursor.execute("SELECT nombre_centro_bd FROM mapa_centros WHERE codigo_pdf = %s;", (clave_pdf,))
        res = cursor.fetchone()
        if res and res[0]:
            return res[0]
    except Exception:
        pass

    nombre_sin_codigo = re.sub(r'^\d+\s*', '', centro_clean).strip().upper()
    
    if nombre_sin_codigo:
        cursor.execute("""
            SELECT DISTINCT centro FROM maestro_dosimetros 
            WHERE UPPER(centro) = %s 
               OR UPPER(centro) LIKE %s 
               OR %s LIKE CONCAT('%%', UPPER(centro), '%%');
        """, (nombre_sin_codigo, f"%{nombre_sin_codigo}%", nombre_sin_codigo))
        res_directo = cursor.fetchone()
        if res_directo and res_directo[0]:
            return res_directo[0]

    return centro_clean


def guardar_dosimetria_pdf_en_bd(df_pdf, db_config):
    if df_pdf.empty:
        return False

    try:
        conexion = psycopg2.connect(
            host=db_config["host"], database=db_config["database"],
            user=db_config["user"], password=db_config["password"],
            port=db_config["port"], client_encoding="utf8"
        )
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mapa_centros (
                codigo_pdf VARCHAR PRIMARY KEY,
                nombre_centro_bd VARCHAR NOT NULL
            );
            CREATE TABLE IF NOT EXISTS avisos (
                id SERIAL PRIMARY KEY,
                codigo_dosimetro VARCHAR NOT NULL,
                periodo DATE NOT NULL,
                estado VARCHAR DEFAULT 'Pendiente',
                tipo_aviso VARCHAR DEFAULT 'Faltan lecturas'
            );
            ALTER TABLE avisos ADD COLUMN IF NOT EXISTS tipo_aviso VARCHAR DEFAULT 'Faltan lecturas';
        """)

        meses = {
            'GENER': '01', 'FEBRER': '02', 'MARÇ': '03', 'ABRIL': '04', 
            'MAIG': '05', 'JUNY': '06', 'JULIOL': '07', 'AGOST': '08', 
            'SETEMBRE': '09', 'OCTUBRE': '10', 'NOVEMBRE': '11', 'DESEMBRE': '12',
            'ENERO': '01', 'FEBRERO': '02', 'MARZO': '03', 'MAYO': '05',
            'JUNIO': '06', 'JULIO': '07', 'AGOSTO': '08', 'SEPTIEMBRE': '09',
            'NOVIEMBRE': '11', 'DICIEMBRE': '12'
        }

        todos_codigos_pdf = [normalizar_codigo(c) for c in df_pdf['Codigo_Dosimetro'].unique()]
        cursor.execute("""
            SELECT centro, COUNT(*) 
            FROM maestro_dosimetros 
            WHERE codigo = ANY(%s) 
            GROUP BY centro 
            ORDER BY COUNT(*) DESC LIMIT 1;
        """, (todos_codigos_pdf,))
        res_centro_dominante = cursor.fetchone()
        centro_dominante_pdf = res_centro_dominante[0] if res_centro_dominante else None

        codigos_leidos = []
        fecha_sql_global = None

        for _, fila in df_pdf.iterrows():
            codigo_dosimetro = normalizar_codigo(fila['Codigo_Dosimetro'])

            partes = str(fila['Periodo']).strip().upper().split(" ")
            mes_texto, anio = (partes[0], partes[-1]) if len(partes) >= 2 else ("GENER", "2026")
            mes_num = meses.get(mes_texto, '01')
            fecha_sql = f"{anio}-{mes_num}-01"
            fecha_sql_global = fecha_sql

            cursor.execute("SELECT 1 FROM maestro_dosimetros WHERE codigo = %s;", (codigo_dosimetro,))
            existe = cursor.fetchone()

            if not existe:
                apellidos, nombre = separar_nombre_apellidos(fila['Nombre_Apellidos'])
                
                if centro_dominante_pdf:
                    centro_final = centro_dominante_pdf
                else:
                    centro_final = obtener_centro_mapeado(cursor, fila.get('Centro_PDF', ''))

                cursor.execute("""
                    INSERT INTO maestro_dosimetros (codigo, apellidos, nombre, dni, centro, dosimetro, fecha_alta)
                    VALUES (%s, %s, %s, '', %s, %s, %s);
                """, (codigo_dosimetro, apellidos, nombre, centro_final, fila['Tipo_Dosimetro'], fecha_sql))

                cursor.execute("""
                    INSERT INTO avisos (codigo_dosimetro, periodo, estado, tipo_aviso)
                    SELECT %s, %s, 'Pendiente', 'Alta nueva'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM avisos 
                        WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Alta nueva'
                    );
                """, (codigo_dosimetro, fecha_sql, codigo_dosimetro, fecha_sql))

            dosis_hsm = None if pd.isnull(fila['Dosis_HSM']) else float(fila['Dosis_HSM'])
            dosis_hpm = None if pd.isnull(fila['Dosis_HPM']) else float(fila['Dosis_HPM'])

            cursor.execute("""
                INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones)
                VALUES (%s, %s, %s, %s, '')
                ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET
                    dosis_hsm = EXCLUDED.dosis_hsm,
                    dosis_hpm = EXCLUDED.dosis_hpm,
                    observaciones = '';
            """, (codigo_dosimetro, fecha_sql, dosis_hsm, dosis_hpm))
            
            cursor.execute("""
                UPDATE avisos 
                SET estado = 'Resuelto' 
                WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Faltan lecturas' AND estado = 'Pendiente';
            """, (codigo_dosimetro, fecha_sql))

            codigos_leidos.append(codigo_dosimetro)

        if codigos_leidos and fecha_sql_global:
            cursor.execute("SELECT DISTINCT centro FROM maestro_dosimetros WHERE codigo = ANY(%s)", (codigos_leidos,))
            centros_pdf = [row[0] for row in cursor.fetchall()]

            cursor.execute("""
                SELECT codigo FROM maestro_dosimetros 
                WHERE centro = ANY(%s) 
                AND (fecha_baja IS NULL OR fecha_baja > %s)
            """, (centros_pdf, fecha_sql_global))
            todos_codigos_centro = [row[0] for row in cursor.fetchall()]

            cursor.execute("SELECT DISTINCT codigo_dosimetro FROM registros_dosimetria WHERE periodo = %s", (fecha_sql_global,))
            registrados_en_bd = set([row[0] for row in cursor.fetchall()])

            cubiertos = set(codigos_leidos).union(registrados_en_bd)
            faltantes = set(todos_codigos_centro) - cubiertos

            for cod in faltantes:
                cursor.execute("""
                    INSERT INTO avisos (codigo_dosimetro, periodo, estado, tipo_aviso)
                    SELECT %s, %s, 'Pendiente', 'Faltan lecturas'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM avisos 
                        WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Faltan lecturas'
                    );
                """, (cod, fecha_sql_global, cod, fecha_sql_global))

        conexion.commit()
        cursor.close()
        conexion.close()
        return True
    except Exception as e:
        print(f"Error al guardar dosis del PDF: {e}")
        return False