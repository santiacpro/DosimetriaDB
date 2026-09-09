import pdfplumber
import pandas as pd
import re

# 1. Precompilación de patrones
PATRON_CODIGO = re.compile(r"^(\d{6})\.(\d{2})")
PATRON_DECIMALES = re.compile(r"\b\d{1,4}[,.]\d{2}\b")
PATRON_FECHAS = re.compile(r'\b\d{2}/\d{2}/\d{4}\b')
PATRON_REF = re.compile(r'\b\d{6}-\d{4}\b')
PATRON_NUMEROS = re.compile(r'\b\d+\b')
PATRON_RUIDO = re.compile(r'\b(DLA|DILA|MAS|Zero|per|ser|inferior|a|mSv/mes|CSNGS|CSN-GS|Dosimetria|Anell|Canell|SUPLENTE|VIAJE)\b', re.IGNORECASE)

def extraer_dosimetria_optimizada(ruta_archivo):
    registros = []
    nombres_por_usuario = {}
    
    with pdfplumber.open(ruta_archivo) as pdf:
        # Variables globales para todo el documento
        mes_informe = "Desconocido"
        empresa_codigo = "Desconocido"
        empresa_nombre = "Desconocido"
        centro_codigo = "Desconocido"
        centro_nombre = "Desconocido"
        
        for pagina in pdf.pages:
            texto = pagina.extract_text(layout=True)
            if not texto: continue
            
            for linea in texto.split('\n'):
                linea_limpia = linea.strip()
                if not linea_limpia: continue

                # A. Captura de Metadatos Globales
                if "INFORME MENSUAL" in linea_limpia.upper():
                    mes_informe = linea_limpia.split("PERSONAL ")[-1].strip()
                
                # ¡NUEVO! Extracción de Empresa
                if linea_limpia.startswith("Empresa:"):
                    match_empresa = re.search(r'Empresa:\s*(\d+)\s*(.*)', linea_limpia)
                    if match_empresa:
                        empresa_codigo = match_empresa.group(1).strip()
                        empresa_nombre = match_empresa.group(2).strip()

                # ¡NUEVO! Extracción de Centro
                if linea_limpia.startswith("Centre:"):
                    match_centro = re.search(r'Centre:\s*(\d+)\s*(.*)', linea_limpia)
                    if match_centro:
                        centro_codigo = match_centro.group(1).strip()
                        centro_nombre = match_centro.group(2).strip()

                # B. Procesamiento de Usuarios
                match_codigo = PATRON_CODIGO.match(linea_limpia)
                if match_codigo:
                    try:
                        codigo_completo = match_codigo.group(0)
                        codigo_usuario, codigo_dosimetro = match_codigo.groups()
                        
                        linea_sin_id = linea_limpia.replace(codigo_completo, "")
                        
                        es_anillo = bool(re.search(r'\b(anell|anillo)\b', linea_sin_id, re.IGNORECASE))
                        es_muneca = bool(re.search(r'\b(canell|muñeca)\b', linea_sin_id, re.IGNORECASE))
                        es_extremidad = es_anillo or es_muneca
                        
                        if es_anillo: tipo_dosimetro = "Anillo"
                        elif es_muneca: tipo_dosimetro = "Muñeca"
                        else: tipo_dosimetro = "Solapa"
                        
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
                            "Empresa_Codigo": empresa_codigo,
                            "Empresa_Nombre": empresa_nombre,
                            "Centro_Codigo": centro_codigo,
                            "Centro_Nombre": centro_nombre,
                            "Periodo": mes_informe,
                            "Codigo_Usuario": codigo_usuario,
                            "Codigo_Dosimetro": codigo_dosimetro,
                            "Nombre_Apellidos": nombre_final,
                            "Tipo_Dosimetro": tipo_dosimetro,
                            "Dosis_HSM": hsm_float,
                            "Dosis_HPM": hpm_float
                        })
                    except Exception as e:
                        print(f"Error procesando línea '{linea_limpia}': {e}")
                            
    return pd.DataFrame(registros)

import psycopg2
from psycopg2 import sql

def guardar_en_bd(df, db_config):
    # 1. Conexión a la base de datos usando la configuración dinámica
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
        
        for index, fila in df.iterrows():
            cursor.execute("""
                INSERT INTO empresas (codigo, nombre) 
                VALUES (%s, %s) ON CONFLICT (codigo) DO NOTHING;
            """, (fila['Empresa_Codigo'], fila['Empresa_Nombre']))
            
            cursor.execute("""
                INSERT INTO centros (codigo, codigo_empresa, nombre) 
                VALUES (%s, %s, %s) ON CONFLICT (codigo) DO NOTHING;
            """, (fila['Centro_Codigo'], fila['Empresa_Codigo'], fila['Centro_Nombre']))
            
            cursor.execute("""
                INSERT INTO trabajadores (codigo, nombre_apellidos) 
                VALUES (%s, %s) 
                ON CONFLICT (codigo) DO UPDATE SET nombre_apellidos = EXCLUDED.nombre_apellidos;
            """, (fila['Codigo_Usuario'], fila['Nombre_Apellidos']))
            
            meses = {'GENER': '01', 'FEBRER': '02', 'MARÇ': '03', 'ABRIL': '04', 'MAIG': '05', 'JUNY': '06', 
                     'JULIOL': '07', 'AGOST': '08', 'SETEMBRE': '09', 'OCTUBRE': '10', 'NOVEMBRE': '11', 'DESEMBRE': '12'}
            mes_texto, anio = fila['Periodo'].split(" ")
            fecha_sql = f"{anio}-{meses.get(mes_texto, '01')}-01"
            
            cursor.execute("""
                INSERT INTO registros_dosimetria 
                (codigo_trabajador, codigo_centro, codigo_dosimetro, tipo_dosimetro, periodo, dosis_hsm, dosis_hpm) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING;
            """, (fila['Codigo_Usuario'], fila['Centro_Codigo'], fila['Codigo_Dosimetro'], 
                  fila['Tipo_Dosimetro'], fecha_sql, fila['Dosis_HSM'], fila['Dosis_HPM']))
            
        conexion.commit()
        cursor.close()
        conexion.close()
        print("✅ Datos guardados en PostgreSQL correctamente.")
        
    except Exception as e:
        error_limpio = str(e).encode('latin-1', 'ignore').decode('utf-8', 'ignore')
        print(f"❌ Error al conectar a la base de datos: {error_limpio}")



# Ejecutamos la función
#df_resultados = extraer_dosimetria_optimizada('Enero.pdf')
#pd.set_option('display.max_columns', None)
#print(df_resultados.head(5))
#guardar_en_bd(df_resultados, "acprosimetria")
