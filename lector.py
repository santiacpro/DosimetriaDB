import pandas as pd
import pdfplumber
import psycopg2

def procesar_excel_maestro(archivo_excel, db_config):
    """Carga los trabajadores desde las columnas exactas del Excel Maestro."""
    try:
        df = pd.read_excel(archivo_excel)
        # Normalizar nombres de columnas a mayúsculas y sin espacios
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
    """Extracción de lecturas mensuales desde los PDFs."""
    registros = []
    with pdfplumber.open(archivo_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if not texto:
                continue
            # Lógica de extracción del PDF según tu formato habitual
            # Debe devolver un DataFrame con: Codigo_Dosimetro, Periodo, Dosis_HSM, Dosis_HPM
            pass
    return pd.DataFrame(registros)


def guardar_dosimetria_pdf_en_bd(df_pdf, db_config):
    """Vincula las dosis del PDF al usuario mediante CODIGO."""
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

        meses = {'GENER': '01', 'FEBRER': '02', 'MARÇ': '03', 'ABRIL': '04', 'MAIG': '05', 'JUNY': '06', 
                 'JULIOL': '07', 'AGOST': '08', 'SETEMBRE': '09', 'OCTUBRE': '10', 'NOVEMBRE': '11', 'DESEMBRE': '12'}

        for _, fila in df_pdf.iterrows():
            codigo_dosimetro = str(fila['Codigo_Dosimetro']).strip()
            mes_texto, anio = str(fila['Periodo']).split(" ")
            fecha_sql = f"{anio}-{meses.get(mes_texto, '01')}-01"

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