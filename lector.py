import re
import pandas as pd
import pdfplumber
import psycopg2
import collections

# =============================================================================
# --- CONFIGURACIÓN DE IDIOMA, TEXTOS Y PALABRAS CLAVE ---
# =============================================================================

# 1. DICCIONARIO DE MESES (Traducción del PDF a números)
MESES_DICT = {
    'GENER': '01', 'FEBRER': '02', 'MARÇ': '03', 'ABRIL': '04', 
    'MAIG': '05', 'JUNY': '06', 'JULIOL': '07', 'AGOST': '08', 
    'SETEMBRE': '09', 'OCTUBRE': '10', 'NOVEMBRE': '11', 'DESEMBRE': '12',
    'ENERO': '01', 'FEBRERO': '02', 'MARZO': '03', 'MAYO': '05',
    'JUNIO': '06', 'JULIO': '07', 'AGOSTO': '08', 'SEPTIEMBRE': '09',
    'NOVIEMBRE': '11', 'DICIEMBRE': '12'
}

# 2. PALABRAS CLAVE DE BÚSQUEDA Y FILTRADO
TXT_INFORME = "INFORME MENSUAL"
TXT_PERSONAL = "PERSONAL "
TXT_CENTRO = ["CENTRE:", "CENTRO:"]
TXT_VIAJE = r'\bVIAJE\b'
TXT_ANILLO = r'\b(anell|anillo)\b'
TXT_MUNECA = r'\b(canell|muñeca)\b'
TXT_LEYENDAS_PIE = ["motiu", "correccio"]
TXT_IGNORAR_DOSIS_CERO = "zero per ser inferior"
TXT_ALTA = "Alta"

# 3. FRASES DINÁMICAS PARA OBSERVACIONES (Motor de texto)
FRASES_OBS = {
    "lectura_unica_atrasada": "Lectura correspondiente a {mes}",
    
    "una_actual": "una correspondiente al mes actual",
    "varias_actual": "{cant} correspondientes al mes actual",
    
    "una_atrasada": "una correspondiente a {mes}",
    "varias_atrasada": "{cant} correspondientes a {mes}",
    
    "resumen_todas_iguales": "Se reciben {total} lecturas, todas {texto}",
    "resumen_dos_grupos": "Se reciben {total} lecturas, {grupo1} y {grupo2}",
    "resumen_varios_grupos": "Se reciben {total} lecturas, {grupos} y {ultimo}",
    
    "alta_con_obs": "Alta. {obs}",
    
    "reemplazar_una_por_varias": ("una correspondiente", "correspondientes"),
    "reemplazar_otra": ("una ", "otra ")
}

# 4. PATRONES DE EXPRESIONES REGULARES ESTRUCTURALES
PATRON_CODIGO = re.compile(r"\b(\d{5,6})\.(\d{1,2})\b|\b(\d{7,8})\b")
# Ajustado para capturar también dos puntos (:) como separador decimal de lectura
PATRON_DECIMALES = re.compile(r"\b\d{1,4}[,.:]\d{2,3}\b")
PATRON_FECHAS = re.compile(r'\b\d{2}/\d{2}/\d{4}\b')
PATRON_REF = re.compile(r'\b\d{6}-(\d{2})(\d{2})\b')
PATRON_NUMEROS = re.compile(r'\b\d+\b')
PATRON_MES_ATRASADO = re.compile(r'(?:Enviat pel mes|Enviado para el mes):\s*(\d{2})(\d{2})', re.IGNORECASE)
PATRON_MAS = re.compile(r'\bMAS:\s*(.*)', re.IGNORECASE)

# Patrones para limpiar texto no deseado de las filas
PATRON_RUIDO = re.compile(r'\b(SPA|DLA|DILA|MAS|Zero|per|ser|inferior|a|mSv/mes|CSNGS|CSN-GS|Dosimetria|Anell|Canell|SUPLENTE|VIAJE)\b', re.IGNORECASE)
PATRON_CABECERAS = re.compile(r'(EQUIVALENTS DE DOSI|ASSIGNATS|USUARI|HSA|HPA|HSM|HPM|CONTROL DOSIMÈTRIC|motiu d\'assignació|extensió periode|notes de l\'usuari si procedeixen)', re.IGNORECASE)
PATRON_PIE_PAGINA = re.compile(r'(fi informe|m[eéè]tode d\'assaig|incertesa|fons natural|els resultats|acreditaci[oó]|motiu d\'assignaci[oó]|validaci[oó] digital)', re.IGNORECASE)
PATRON_DOSIS_CERO_TXT = re.compile(r'0[,.]10\s*mSv/mes', re.IGNORECASE)
PATRON_CSN = re.compile(r'\(CSN.*?\)', re.IGNORECASE)


# =============================================================================
# --- LÓGICA DEL PROGRAMA ---
# =============================================================================

def normalizar_codigo(codigo_raw):
    if not codigo_raw or str(codigo_raw).lower() == 'nan': return ""
    s = str(codigo_raw).strip()
    if s.endswith('.0'): s = s[:-2]
    if '.' in s: return f"{s.split('.')[0].zfill(6)}.{s.split('.')[1].zfill(2)}"
    return f"{s.zfill(8)[:6]}.{s.zfill(8)[-2:]}"


def separar_nombre_apellidos(cadena_completa):
    partes = str(cadena_completa).strip().split()
    if not partes: return "Desconocido", ""
    if len(partes) == 1: return partes[0], ""
    return " ".join(partes[:-1]), partes[-1]


def es_registro_valido(nombre, codigo):
    if not nombre or not codigo:
        return False
        
    nombre_str = str(nombre).strip()
    if nombre_str == "Desconocido":
        return True

    # 1. Descartar si el nombre contiene solo símbolos, puntos, barras o números
    nombre_limpio = re.sub(r'[\/\.\-\_\,\:\;\s\d]', '', nombre_str)
    if len(nombre_limpio) < 3:
        return False
    
    # 2. Descartar si el nombre es una etiqueta del sistema, sello o pie de página
    ignorar = [
        "DOSIMETRIA ANELL", "DOSIMETRIA ANELL-", "DOSIMETRIA CANELL",
        "AREA CONTROL", "SUPLENTE", "VALIDACIO DIGITAL", "CENTRO DE DOSIMETRIA",
        "CENTRE DE DOSIMETRIA", "INFORME MENSUAL", "RESPONSABLE VALIDACIO",
        "FISIC RESPONSABLE", "JULIA MUÑOZ"
    ]
    if any(tag in nombre_str.upper() for tag in ignorar):
        return False
        
    # 3. Validar estructura de código
    if not re.search(r'\d{5,}', str(codigo)):
        return False
        
    return True


def procesar_excel_centros(archivo_excel, db_config):
    try:
        df = pd.read_excel(archivo_excel, dtype=str)
        df.columns = df.columns.astype(str).str.strip().str.upper()
        col_nombre = next((c for c in df.columns if "NOMBRE" in c), None)
        col_codigo = next((c for c in df.columns if "CODIGO" in c or "COD" in c), None)
        if not col_nombre or not col_codigo: return False, "❌ Faltan columnas 'NOMBRE CENTRO' y 'CODIGO CENTRO'."

        conexion = psycopg2.connect(
            host=db_config["host"], database=db_config["database"],
            user=db_config["user"], password=db_config["password"],
            port=db_config["port"], client_encoding="utf8"
        )
        cursor = conexion.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS mapa_centros (codigo_pdf VARCHAR PRIMARY KEY, nombre_centro_bd VARCHAR NOT NULL);")
        
        insertados = 0
        for _, fila in df.iterrows():
            cod_pdf = str(fila.get(col_codigo, '')).strip()
            nom_bd = str(fila.get(col_nombre, '')).strip()
            if cod_pdf and nom_bd and cod_pdf.lower() != 'nan':
                cursor.execute("INSERT INTO mapa_centros (codigo_pdf, nombre_centro_bd) VALUES (%s, %s) ON CONFLICT (codigo_pdf) DO UPDATE SET nombre_centro_bd = EXCLUDED.nombre_centro_bd;", (cod_pdf, nom_bd))
                if cod_pdf.lstrip('0') != cod_pdf:
                    cursor.execute("INSERT INTO mapa_centros (codigo_pdf, nombre_centro_bd) VALUES (%s, %s) ON CONFLICT (codigo_pdf) DO UPDATE SET nombre_centro_bd = EXCLUDED.nombre_centro_bd;", (cod_pdf.lstrip('0'), nom_bd))
                insertados += 1
        conexion.commit(); cursor.close(); conexion.close()
        return True, f"✅ Mapa de Centros actualizado: {insertados} registros."
    except Exception as e: return False, f"❌ Error: {str(e)}"


def procesar_excel_maestro(archivo_excel, db_config):
    try:
        df = pd.read_excel(archivo_excel, dtype=str)
        df.columns = df.columns.astype(str).str.strip().str.upper().str.translate(str.maketrans('ÓÍÁÉÚ', 'OIAEU'))
        
        conexion = psycopg2.connect(
            host=db_config["host"], database=db_config["database"],
            user=db_config["user"], password=db_config["password"],
            port=db_config["port"], client_encoding="utf8"
        )
        cursor = conexion.cursor()
        
        insertados = 0
        for _, fila in df.iterrows():
            codigo = normalizar_codigo(fila.get('CODIGO'))
            if not codigo: continue
            alta = pd.to_datetime(fila.get('ALTA'), errors='coerce')
            baja = pd.to_datetime(fila.get('BAJA'), errors='coerce')
            
            cursor.execute("""
                INSERT INTO maestro_dosimetros (codigo, apellidos, nombre, dni, centro, dosimetro, fecha_alta, fecha_baja)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (codigo) DO UPDATE SET
                apellidos=EXCLUDED.apellidos, nombre=EXCLUDED.nombre, dni=EXCLUDED.dni, centro=EXCLUDED.centro, dosimetro=EXCLUDED.dosimetro, fecha_alta=EXCLUDED.fecha_alta, fecha_baja=EXCLUDED.fecha_baja;
            """, (
                codigo, str(fila.get('APELLIDOS', '')).strip() if pd.notnull(fila.get('APELLIDOS')) else '',
                str(fila.get('NOMBRE', '')).strip() if pd.notnull(fila.get('NOMBRE')) else '',
                str(fila.get('DNI', '')).strip() if pd.notnull(fila.get('DNI')) else '',
                str(fila.get('CENTRO', '')).strip() if pd.notnull(fila.get('CENTRO')) else '',
                str(fila.get('DOSIMETRO', '')).strip() if pd.notnull(fila.get('DOSIMETRO')) else '',
                alta.strftime('%Y-%m-%d') if pd.notnull(alta) else None, baja.strftime('%Y-%m-%d') if pd.notnull(baja) else None
            ))
            insertados += 1
        conexion.commit(); cursor.close(); conexion.close()
        return True, f"✅ Maestro cargado: {insertados} registros."
    except Exception as e: return False, f"❌ Error: {str(e)}"


def extraer_dosimetria_optimizada(archivo_pdf):
    registros_raw, nombres_por_usuario = [], {}
    
    with pdfplumber.open(archivo_pdf) as pdf:
        texto_total = "".join([p.extract_text(layout=True) + "\n" for p in pdf.pages if p.extract_text(layout=True)])
            
        mes_informe, centro_pdf_detectado = "GENER 2026", "Centro Desconocido"
        for linea in texto_total.split('\n'):
            if TXT_INFORME in linea.upper(): mes_informe = linea.split(TXT_PERSONAL)[-1].strip()
            if any(c in linea.upper() for c in TXT_CENTRO):
                partes_c = re.split('|'.join(TXT_CENTRO), linea, flags=re.IGNORECASE)
                if len(partes_c) > 1: centro_pdf_detectado = partes_c[1].strip()
                
        partes = mes_informe.split()
        mes_actual_referencia = "01/26"
        if len(partes) >= 2:
            mes_actual_referencia = f"{MESES_DICT.get(partes[0].upper(), '01')}/{partes[-1][-2:] if len(partes[-1]) >= 2 else '26'}"

        last_record = None
        for linea in texto_total.split('\n'):
            linea_limpia = linea.strip()
            if not linea_limpia or re.search(TXT_VIAJE, linea_limpia, re.IGNORECASE): continue
            if PATRON_PIE_PAGINA.search(linea_limpia):
                last_record = None
                continue

            match_codigo = PATRON_CODIGO.search(linea_limpia)
            if match_codigo:
                try:
                    codigo_raw = match_codigo.group(0)
                    codigo_completo = normalizar_codigo(codigo_raw)
                    codigo_usuario = codigo_completo.split('.')[0] if '.' in codigo_completo else codigo_completo
                    linea_sin_id = linea_limpia.replace(codigo_raw, "")
                    
                    mes_lectura = "Actual"
                    match_ref = PATRON_REF.search(linea_sin_id)
                    if match_ref:
                        mes_calc = f"{match_ref.group(2)}/{match_ref.group(1)}"
                        if mes_calc != mes_actual_referencia: mes_lectura = mes_calc
                    else:
                        match_mes = PATRON_MES_ATRASADO.search(linea_sin_id)
                        if match_mes:
                            mes_calc = f"{match_mes.group(2)}/{match_mes.group(1)}"
                            if mes_calc != mes_actual_referencia: mes_lectura = mes_calc
                    
                    linea_sin_id = PATRON_MES_ATRASADO.sub('', linea_sin_id)
                    es_anillo = bool(re.search(TXT_ANILLO, linea_sin_id, re.IGNORECASE))
                    es_muneca = bool(re.search(TXT_MUNECA, linea_sin_id, re.IGNORECASE))
                    es_extremidad = es_anillo or es_muneca
                    tipo_dosimetro = "Anillo" if es_anillo else ("Muñeca" if es_muneca else "Solapa")
                    
                    txt_nombres = PATRON_FECHAS.sub('', linea_sin_id)
                    txt_nombres = PATRON_REF.sub('', txt_nombres)
                    txt_nombres = PATRON_DECIMALES.sub('', txt_nombres)
                    txt_nombres = PATRON_NUMEROS.sub('', txt_nombres)
                    txt_nombres = PATRON_RUIDO.sub('', txt_nombres)
                    txt_nombres = re.sub(r'[|\-:]', '', txt_nombres)
                    
                    posible_nombre = PATRON_CABECERAS.sub('', " ".join(txt_nombres.split()).strip()).strip()
                    
                    if es_registro_valido(posible_nombre, codigo_completo):
                        nombres_por_usuario[codigo_usuario] = posible_nombre
                        
                    nombre_final = nombres_por_usuario.get(codigo_usuario, "Desconocido")
                    if re.search(TXT_VIAJE, nombre_final, re.IGNORECASE): continue

                    linea_para_dosis = PATRON_FECHAS.sub('', linea_sin_id)
                    linea_para_dosis = PATRON_REF.sub('', linea_para_dosis)
                    linea_para_dosis = PATRON_DOSIS_CERO_TXT.sub('', linea_para_dosis)
                    linea_para_dosis = PATRON_CSN.sub('', linea_para_dosis)
                    
                    numeros_decimales = PATRON_DECIMALES.findall(linea_para_dosis)
                    if not numeros_decimales:
                        last_record = None
                        continue
                        
                    def to_float(val_str):
                        try: return float(val_str.replace(",", ".").replace(":", "."))
                        except: return 0.0

                    # PARSEO DE DOSIS ESPECÍFICO SEGÚN TIPO DE DOSÍMETRO
                    if es_extremidad:
                        # Extremidades: 1 sola dosis (HSM). HPM es siempre None.
                        if len(numeros_decimales) >= 3:
                            # Formato estándar: [Calculada, Asignada_HSM, HSA]
                            val_dosis = to_float(numeros_decimales[1])
                        elif len(numeros_decimales) == 2:
                            # Formato corto: [Asignada_HSM, HSA] o [Calculada, Asignada_HSM]
                            v0, v1 = to_float(numeros_decimales[0]), to_float(numeros_decimales[1])
                            val_dosis = v0 if (v1 > 10.0 and v0 <= 10.0) or v0 == v1 else v1
                        else:
                            val_dosis = to_float(numeros_decimales[0])

                        hsm_float = val_dosis
                        hpm_float = None
                    else:
                        # Solapa: 2 dosis (HSM y HPM)
                        if len(numeros_decimales) >= 4:
                            hsm_val = to_float(numeros_decimales[-4])
                            hpm_val = to_float(numeros_decimales[-3])
                        elif len(numeros_decimales) == 3:
                            hsm_val = to_float(numeros_decimales[0])
                            hpm_val = to_float(numeros_decimales[1])
                        elif len(numeros_decimales) == 2:
                            hsm_val = to_float(numeros_decimales[0])
                            hpm_val = to_float(numeros_decimales[1])
                        else:
                            hsm_val = to_float(numeros_decimales[0])
                            hpm_val = 0.0

                        hsm_float = hsm_val
                        hpm_float = hpm_val
                    
                    last_record = {
                        "Periodo": mes_informe, "Codigo_Dosimetro": codigo_completo, "Nombre_Apellidos": nombre_final,
                        "Tipo_Dosimetro": tipo_dosimetro, "Centro_PDF": centro_pdf_detectado,
                        "Dosis_HSM": hsm_float, "Dosis_HPM": hpm_float, "Mes_Lectura": mes_lectura, "Asignacion": None
                    }
                    registros_raw.append(last_record)
                except Exception as e:
                    print(f"Error procesando línea '{linea_limpia}': {e}"); last_record = None
            else:
                if last_record:
                    if last_record["Mes_Lectura"] == "Actual":
                        match_ref = PATRON_REF.search(linea_limpia)
                        if match_ref:
                            mes_calc = f"{match_ref.group(2)}/{match_ref.group(1)}"
                            if mes_calc != mes_actual_referencia: last_record["Mes_Lectura"] = mes_calc
                        else:
                            match_mes = PATRON_MES_ATRASADO.search(linea_limpia)
                            if match_mes:
                                mes_calc = f"{match_mes.group(2)}/{match_mes.group(1)}"
                                if mes_calc != mes_actual_referencia: last_record["Mes_Lectura"] = mes_calc
                                
                    match_mas = PATRON_MAS.search(linea_limpia)
                    if match_mas:
                        txt_mas = match_mas.group(1).strip()
                        txt_limpio = txt_mas.lower().replace('ó', 'o').replace('í', 'i')
                        es_leyenda = any(palabra in txt_limpio for palabra in TXT_LEYENDAS_PIE)
                        if not es_leyenda and TXT_IGNORAR_DOSIS_CERO not in txt_limpio:
                            last_record["Asignacion"] = txt_mas

    # --- AGRUPACIÓN Y CONSTRUCCIÓN DE OBSERVACIONES ---
    agrupados = collections.defaultdict(list)
    for reg in registros_raw: agrupados[reg["Codigo_Dosimetro"]].append(reg)
        
    registros_finales = []
    for cod, lista in agrupados.items():
        base, total = lista[0], len(lista)
        codigo_usr = cod.split('.')[0] if '.' in cod else cod
        
        # Heredar el nombre correcto de usuario registrado
        nombre_final = nombres_por_usuario.get(codigo_usr, base["Nombre_Apellidos"])

        lecturas_info = [(r['Mes_Lectura'], r.get('Asignacion')) for r in lista]
        
        obs = ""
        if total == 1:
            mes, asig = lecturas_info[0]
            obs_parts = [FRASES_OBS["lectura_unica_atrasada"].format(mes=mes)] if mes != "Actual" else []
            if asig: obs = f"{obs_parts[0]} ({asig})" if obs_parts else asig
            elif obs_parts: obs = obs_parts[0]
        else:
            conteo = collections.Counter(lecturas_info)
            descripciones = []
            
            for (mes, asig), cant in conteo.items():
                if mes == "Actual":
                    desc = FRASES_OBS["una_actual"] if cant == 1 else FRASES_OBS["varias_actual"].format(cant=cant)
                else:
                    desc = FRASES_OBS["una_atrasada"].format(mes=mes) if cant == 1 else FRASES_OBS["varias_atrasada"].format(cant=cant, mes=mes)
                if asig: desc += f" ({asig})"
                descripciones.insert(0, desc) if mes == "Actual" else descripciones.append(desc)
            
            if len(descripciones) == 1:
                txt_limpio = descripciones[0].replace(*FRASES_OBS["reemplazar_una_por_varias"]).replace(f'{total}{FRASES_OBS["reemplazar_una_por_varias"][1]}', FRASES_OBS["reemplazar_una_por_varias"][1].strip())
                obs = FRASES_OBS["resumen_todas_iguales"].format(total=total, texto=txt_limpio)
            elif len(descripciones) == 2:
                desc_2 = descripciones[1].replace(*FRASES_OBS["reemplazar_otra"], 1) if descripciones[1].startswith(FRASES_OBS["reemplazar_otra"][0]) else descripciones[1]
                obs = FRASES_OBS["resumen_dos_grupos"].format(total=total, grupo1=descripciones[0], grupo2=desc_2)
            else:
                ult = descripciones[-1].replace(*FRASES_OBS["reemplazar_otra"], 1) if descripciones[-1].startswith(FRASES_OBS["reemplazar_otra"][0]) else descripciones[-1]
                obs = FRASES_OBS["resumen_varios_grupos"].format(total=total, grupos=", ".join(descripciones[:-1]), ultimo=ult)
                
        hsm_vals = [r["Dosis_HSM"] for r in lista if r["Dosis_HSM"] is not None]
        hpm_vals = [r["Dosis_HPM"] for r in lista if r["Dosis_HPM"] is not None]
        
        registros_finales.append({
            "Periodo": base["Periodo"], "Codigo_Dosimetro": cod, "Nombre_Apellidos": nombre_final,
            "Tipo_Dosimetro": base["Tipo_Dosimetro"], "Centro_PDF": base["Centro_PDF"],
            "Dosis_HSM": sum(hsm_vals) if hsm_vals else None, "Dosis_HPM": sum(hpm_vals) if hpm_vals else None, "Observaciones_PDF": obs
        })

    return pd.DataFrame(registros_finales)

def obtener_centro_mapeado(cursor, centro_pdf):
    if not centro_pdf or str(centro_pdf).strip() in ['', 'None', 'Centro Desconocido']: return "Centro Desconocido"
    centro_clean = str(centro_pdf).strip()
    num_centro = re.search(r'\b\d{4,6}\b', centro_clean)
    clave_pdf = num_centro.group(0) if num_centro else centro_clean

    try:
        cursor.execute("SELECT nombre_centro_bd FROM mapa_centros WHERE codigo_pdf = %s;", (clave_pdf,))
        res = cursor.fetchone()
        if res and res[0]: return res[0]
    except Exception: pass

    nombre_sin_codigo = re.sub(r'^\d+\s*', '', centro_clean).strip().upper()
    if nombre_sin_codigo:
        cursor.execute("SELECT DISTINCT centro FROM maestro_dosimetros WHERE UPPER(centro) = %s OR UPPER(centro) LIKE %s OR %s LIKE CONCAT('%%', UPPER(centro), '%%');", (nombre_sin_codigo, f"%{nombre_sin_codigo}%", nombre_sin_codigo))
        res_directo = cursor.fetchone()
        if res_directo and res_directo[0]: return res_directo[0]
    return centro_clean

def guardar_dosimetria_pdf_en_bd(df_pdf, db_config):
    if df_pdf.empty: return False

    try:
        conexion = psycopg2.connect(
            host=db_config["host"], database=db_config["database"],
            user=db_config["user"], password=db_config["password"],
            port=db_config["port"], client_encoding="utf8"
        )
        cursor = conexion.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mapa_centros (codigo_pdf VARCHAR PRIMARY KEY, nombre_centro_bd VARCHAR NOT NULL);
            CREATE TABLE IF NOT EXISTS avisos (id SERIAL PRIMARY KEY, codigo_dosimetro VARCHAR NOT NULL, periodo DATE NOT NULL, estado VARCHAR DEFAULT 'Pendiente', tipo_aviso VARCHAR DEFAULT 'Faltan lecturas');
            ALTER TABLE avisos ADD COLUMN IF NOT EXISTS tipo_aviso VARCHAR DEFAULT 'Faltan lecturas';
        """)

        todos_codigos_pdf = [normalizar_codigo(c) for c in df_pdf['Codigo_Dosimetro'].unique()]
        cursor.execute("SELECT centro, COUNT(*) FROM maestro_dosimetros WHERE codigo = ANY(%s) GROUP BY centro ORDER BY COUNT(*) DESC LIMIT 1;", (todos_codigos_pdf,))
        res_centro_dominante = cursor.fetchone()
        centro_dominante_pdf = res_centro_dominante[0] if res_centro_dominante else None

        codigos_leidos, fecha_sql_global = [], None

        for _, fila in df_pdf.iterrows():
            codigo_dosimetro = normalizar_codigo(fila['Codigo_Dosimetro'])
            partes = str(fila['Periodo']).strip().upper().split(" ")
            mes_texto, anio = (partes[0], partes[-1]) if len(partes) >= 2 else ("GENER", "2026")
            mes_num = MESES_DICT.get(mes_texto, '01')
            fecha_sql_global = fecha_sql = f"{anio}-{mes_num}-01"

            obs_pdf = fila.get('Observaciones_PDF', '')
            texto_observacion = obs_pdf

            cursor.execute("SELECT 1 FROM maestro_dosimetros WHERE codigo = %s;", (codigo_dosimetro,))
            if not cursor.fetchone():
                texto_observacion = TXT_ALTA if not obs_pdf else FRASES_OBS["alta_con_obs"].format(obs=obs_pdf)
                apellidos, nombre = separar_nombre_apellidos(fila['Nombre_Apellidos'])
                centro_final = centro_dominante_pdf if centro_dominante_pdf else obtener_centro_mapeado(cursor, fila.get('Centro_PDF', ''))

                cursor.execute("INSERT INTO maestro_dosimetros (codigo, apellidos, nombre, dni, centro, dosimetro, fecha_alta) VALUES (%s, %s, %s, '', %s, %s, %s);", (codigo_dosimetro, apellidos, nombre, centro_final, fila['Tipo_Dosimetro'], fecha_sql))
                cursor.execute("INSERT INTO avisos (codigo_dosimetro, periodo, estado, tipo_aviso) SELECT %s, %s, 'Pendiente', 'Alta nueva' WHERE NOT EXISTS (SELECT 1 FROM avisos WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Alta nueva');", (codigo_dosimetro, fecha_sql, codigo_dosimetro, fecha_sql))

            dosis_hsm = float(fila['Dosis_HSM']) if pd.notnull(fila['Dosis_HSM']) else None
            dosis_hpm = float(fila['Dosis_HPM']) if pd.notnull(fila['Dosis_HPM']) else None

            cursor.execute("INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm, observaciones) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET dosis_hsm = EXCLUDED.dosis_hsm, dosis_hpm = EXCLUDED.dosis_hpm, observaciones = EXCLUDED.observaciones;", (codigo_dosimetro, fecha_sql, dosis_hsm, dosis_hpm, texto_observacion))
            cursor.execute("UPDATE avisos SET estado = 'Resuelto' WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Faltan lecturas' AND estado = 'Pendiente';", (codigo_dosimetro, fecha_sql))
            codigos_leidos.append(codigo_dosimetro)

        if codigos_leidos and fecha_sql_global:
            cursor.execute("SELECT DISTINCT centro FROM maestro_dosimetros WHERE codigo = ANY(%s)", (codigos_leidos,))
            cursor.execute("SELECT codigo FROM maestro_dosimetros WHERE centro = ANY(%s) AND (fecha_baja IS NULL OR fecha_baja > %s)", ([row[0] for row in cursor.fetchall()], fecha_sql_global))
            todos_codigos_centro = [row[0] for row in cursor.fetchall()]
            
            cursor.execute("SELECT DISTINCT codigo_dosimetro FROM registros_dosimetria WHERE periodo = %s", (fecha_sql_global,))
            faltantes = set(todos_codigos_centro) - set(codigos_leidos).union(set([row[0] for row in cursor.fetchall()]))

            for cod in faltantes:
                cursor.execute("INSERT INTO avisos (codigo_dosimetro, periodo, estado, tipo_aviso) SELECT %s, %s, 'Pendiente', 'Faltan lecturas' WHERE NOT EXISTS (SELECT 1 FROM avisos WHERE codigo_dosimetro = %s AND periodo = %s AND tipo_aviso = 'Faltan lecturas');", (cod, fecha_sql_global, cod, fecha_sql_global))

        conexion.commit(); cursor.close(); conexion.close()
        return True
    except Exception as e:
        print(f"Error al guardar dosis del PDF: {e}")
        return False