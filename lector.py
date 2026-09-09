import re
import pandas as pd
import pdfplumber
import psycopg2

# --- PATRONES DE EXPRESIONES REGULARES ---
PATRON_CODIGO = re.compile(r"^(\d{6})\.(\d{2})")
PATRON_DECIMALES = re.compile(r"\b\d{1,4}[,.]\d{2}\b")
PATRON_FECHAS = re.compile(r'\b\d{2}/\d{2}/\d{4}\b')
PATRON_REF = re.compile(r'\b\d{6}-\d{4}\b')
PATRON_NUMEROS = re.compile(r'\b\d+\b')
PATRON_RUIDO = re.compile(r'\b(DLA|DILA|MAS|Zero|per|ser|inferior|a|mSv/mes|CSNGS|CSN-GS|Dosimetria|Anell|Canell|SUPLENTE|VIAJE)\b', re.IGNORECASE)


def procesar_excel_maestro(archivo_excel, db_config):
    """
    Carga los trabajadores y sus dosímetros en la tabla 'maestro_dosimetros'
    usando las columnas exactas del Excel Maestro.
    """
    try:
        df = pd.read_excel(archivo_excel)
        # Limpieza y normalización de columnas
        df.columns = df.columns.str.strip().str.upper()

        conexion = psycopg2.connect(
            host=db_config["host"],
            database=db_config["database"],
            user=db_config["user"],
            password=db_config["password"],
            port=db_config["port"],
            client_encoding="utf8"
        )
        cursor = conexion.cursor()

        insertados = 0
        for _, fila in df.iterrows():
            codigo = str(fila.get('CODIGO', '')).strip()
            if not codigo or codigo == "nan":
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
                codigo,
                str(fila.get('APELLIDOS', '')).strip() if pd.notnull(fila.get('APELLIDOS')) else '',
                str(fila.get('NOMBRE', '')).strip() if pd.notnull(fila.get('NOMBRE')) else '',
                str(fila.get('DNI', '')).strip() if pd.notnull(fila.get('DNI')) else '',
                str(fila.get('CENTRO', '')).strip() if pd.notnull(fila.get('CENTRO')) else '',
                str(fila.get('DOSIMETRO', '')).strip() if pd.notnull(fila.get('DOSIMETRO')) else '',
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
    """
    Extrae las lecturas de dosis del PDF mensual usando expresiones regulares.
    """
    registros = []
    nombres_por_usuario = {}
    
    with pdfplumber.open(archivo_pdf) as pdf:
        mes_informe = "GENER 2026"
        
        for pagina in pdf.pages:
            texto = pagina.extract_text(layout=True)
            if not texto: 
                continue
            
            for linea in texto.split('\n'):
                linea_limpia = linea.strip()
                if not linea_limpia: 
                    continue

                # Captura del período del informe
                if "INFORME MENSUAL" in linea_limpia.upper():
                    mes_informe = linea_limpia.split("PERSONAL ")[-1].strip()

                # Procesamiento de líneas de datos por código
                match_codigo = PATRON_CODIGO.match(linea_limpia)
                if match_codigo:
                    try:
                        codigo_completo = match_codigo.group(0) # ej: "123456.01"
                        codigo_usuario, _ = match_codigo.groups()
                        
                        linea_sin_id = linea_limpia.replace(codigo_completo, "")
                        
                        es_anillo = bool(re.search(r'\b(anell|anillo)\b', linea_sin_id, re.IGNORECASE))
                        es_muneca = bool(re.search(r'\b(canell|muñeca)\b', linea_sin_id, re.IGNORECASE))
                        es_extremidad = es_anillo or es_muneca
                        
                        if es_anillo: 
                            tipo_dosimetro = "Anillo"
                        elif es_muneca: 
                            tipo_dosimetro = "Muñeca"
                        else: 
                            tipo_dosimetro = "Solapa"
                        
                        # Limpieza de texto para obtener nombres
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
                        
                        numeros_decimales = PATRON_DECIMALES.findall(linea_sin_id)
                        
                        if not es_extremidad and len(numeros_decimales) >= 2:
                            hsm_str = numeros_decimales[-4] if len(numeros_decimales) >= 4 else numeros_decimales[-2]
                            hpm_str = numeros_decimales[-3] if len(numeros_decimales) >= 4 else numeros_decimales[-1]
                            hpm_float = float(hpm_str.replace(",", "."))
                        elif es_extremidad and len(numeros_decimales) >= 1:
                            hsm_str = numeros_decimales[-1]
                            hpm_float = None
                        else:
                            continue
                                
                        hsm_float = float(hsm_str.replace(",", "."))
                        
                        registros.append({
                            "Periodo": mes_informe,
                            "Codigo_Dosimetro": codigo_completo,  # Se enlaza con el CODIGO del Excel
                            "Nombre_Apellidos": nombre_final,
                            "Tipo_Dosimetro": tipo_dosimetro,
                            "Dosis_HSM": hsm_float,
                            "Dosis_HPM": hpm_float
                        })
                    except Exception as e:
                        print(f"Error procesando línea '{linea_limpia}': {e}")
                            
    return pd.DataFrame(registros)


def guardar_dosimetria_pdf_en_bd(df_pdf, db_config):
    """
    Inserta las dosis leídas del PDF en la tabla 'registros_dosimetria',
    vinculándolas por el campo 'codigo_dosimetro'.
    """
    if df_pdf.empty:
        return False

    try:
        conexion = psycopg2.connect(
            host=db_config["host"],
            database=db_config["database"],
            user=db_config["user"],
            password=db_config["password"],
            port=db_config["port"],
            client_encoding="utf8"
        )
        cursor = conexion.cursor()

        meses = {
            'GENER': '01', 'FEBRER': '02', 'MARÇ': '03', 'ABRIL': '04', 
            'MAIG': '05', 'JUNY': '06', 'JULIOL': '07', 'AGOST': '08', 
            'SETEMBRE': '09', 'OCTUBRE': '10', 'NOVEMBRE': '11', 'DESEMBRE': '12',
            'ENERO': '01', 'FEBRERO': '02', 'MARZO': '03', 'MAYO': '05',
            'JUNIO': '06', 'JULIO': '07', 'AGOSTO': '08', 'SEPTIEMBRE': '09',
            'NOVIEMBRE': '11', 'DICIEMBRE': '12'
        }

        for _, fila in df_pdf.iterrows():
            codigo_dosimetro = str(fila['Codigo_Dosimetro']).strip()
            
            partes = str(fila['Periodo']).strip().upper().split(" ")
            if len(partes) >= 2:
                mes_texto, anio = partes[0], partes[-1]
            else:
                mes_texto, anio = "GENER", "2026"

            mes_num = meses.get(mes_texto, '01')
            fecha_sql = f"{anio}-{mes_num}-01"

            cursor.execute("""
                INSERT INTO registros_dosimetria (codigo_dosimetro, periodo, dosis_hsm, dosis_hpm)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (codigo_dosimetro, periodo) DO UPDATE SET
                    dosis_hsm = EXCLUDED.dosis_hsm,
                    dosis_hpm = EXCLUDED.dosis_hpm;
            """, (codigo_dosimetro, fecha_sql, fila['Dosis_HSM'], fila['Dosis_HPM']))

        conexion.commit()
        cursor.close()
        conexion.close()
        return True
    except Exception as e:
        print(f"Error al guardar dosis del PDF: {e}")
        return False