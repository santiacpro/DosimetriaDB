import io
import unicodedata
import psycopg2
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph
from reportlab.lib.styles import getSampleStyleSheet
from pypdf import PdfReader, PdfWriter

def quitar_tildes(texto):
    if not isinstance(texto, str): return ""
    texto_limpio = "".join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn').upper()
    return texto_limpio.replace('CH', 'CZZZ')

def generar_pdf_fichas_centro(nombre_centro, db_config, ruta_plantilla_pdf):
    # =========================================================================
    # 🎯 COORDENADAS Y CONFIGURACIÓN
    # =========================================================================
    COORD = {
        "centro": (250, 730),
        "nombre": (33, 683),
        "dni": (92, 665),
        "codigo": (92, 649),
        "tipo": (92, 633),
        "alta": (92, 617),
        "baja": (200, 617),
        "fecha_firma": (290, 250)
    }

    X_HSM = 135
    X_HPM = 201
    X_OBS = 250
    ANCHO_OBS = 318

    Y_MESES = [
        555.0, 541.0, 527.0, 513.0, 499.0, 485.0,
        471.0, 457.0, 443.0, 430.0, 416.0, 402.0
    ]
    Y_SUMATORIOS = 365.0

    meses_indices = {
        '01': 0, '02': 1, '03': 2, '04': 3, '05': 4, '06': 5,
        '07': 6, '08': 7, '09': 8, '10': 9, '11': 10, '12': 11
    }

    meses_texto = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    hoy = datetime.now()
    texto_fecha = f"Barcelona, {hoy.day} de {meses_texto[hoy.month - 1]} de {hoy.year}"

    estilos = getSampleStyleSheet()
    estilo_obs = estilos["Normal"]
    estilo_obs.fontSize = 7
    estilo_obs.leading = 8.5

    # =========================================================================
    # 🐘 BASE DE DATOS
    # =========================================================================
    conexion = psycopg2.connect(
        host=db_config["host"], database=db_config["database"],
        user=db_config["user"], password=db_config["password"],
        port=db_config["port"], client_encoding="utf8"
    )
    cursor = conexion.cursor()

    texto_centro_oficial = str(nombre_centro)
    try:
        cursor.execute("SELECT * FROM mapa_centros LIMIT 1;")
        if cursor.description:
            columnas = [desc[0].lower() for desc in cursor.description]
            col_cod = next((c for c in columnas if 'cod' in c), None)
            col_nom = next((c for c in columnas if 'nom' in c or 'centro' in c), None)

            if col_cod and col_nom:
                cursor.execute(f"""
                    SELECT {col_cod}, {col_nom} 
                    FROM mapa_centros 
                    WHERE {col_nom} = %s OR {col_cod} = %s 
                    LIMIT 1;
                """, (nombre_centro, nombre_centro))
                row_c = cursor.fetchone()
                if row_c and row_c[0] and row_c[1]:
                    cod_str, nom_str = str(row_c[0]), str(row_c[1])
                    texto_centro_oficial = nom_str if cod_str in nom_str else f"{cod_str} - {nom_str}"
    except Exception:
        conexion.rollback()
        texto_centro_oficial = str(nombre_centro)

    cursor.execute("""
        SELECT codigo, apellidos, nombre, dni, centro, dosimetro, fecha_alta, fecha_baja 
        FROM maestro_dosimetros WHERE centro = %s;
    """, (nombre_centro,))
    trabajadores = cursor.fetchall()

    # Función auxiliar para identificar usuarios especiales
    def es_especial(nombre_texto):
        txt = str(nombre_texto or '').upper()
        return 1 if ('AREA' in txt or 'SUPLENTE' in txt) else 0

    # --- ORDENACIÓN EXACTA PARA EL PDF MULTIPÁGINA ---
    trabajadores.sort(key=lambda x: (
        es_especial(f"{x[1] or ''} {x[2] or ''}"),                  # 1. AREA / SUPLENTE al final
        quitar_tildes(f"{x[1] or ''}, {x[2] or ''}".strip()),      # 2. Nombre completo alfabetizado
        0 if 'SOLAPA' in str(x[5] or '').strip().upper() else 1,   # 3. Solapa primero siempre
        str(x[0] or '')                                            # 4. Código de dosímetro
    ))

    writer_final = PdfWriter()

    # =========================================================================
    # 🔄 CONSTRUCCIÓN MULTIPÁGINA
    # =========================================================================
    for trab in trabajadores:
        cod, apell, nom, dni, centro_nombre, tipo_dos, alta, baja = trab
        
        # Comprobar si es un dosímetro de extremidad (Anillo, Muñeca, etc.)
        tipo_dos_str = str(tipo_dos or '').upper()
        es_extremidad = any(term in tipo_dos_str for term in ['ANILLO', 'MUÑECA', 'CANELL', 'EXTREMIDAD', 'EXTREMIDAT'])

        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=A4)

        # 1. Cabecera
        can.setFont("Helvetica-Bold", 11)
        can.drawString(COORD["centro"][0], COORD["centro"][1], texto_centro_oficial)
        
        nombre_completo = f"{apell}, {nom}" if apell and nom else str(nom or apell or "")
        can.drawString(COORD["nombre"][0], COORD["nombre"][1], nombre_completo)

        # 2. Datos Maestro
        can.setFont("Helvetica", 10)
        can.drawString(COORD["dni"][0], COORD["dni"][1], str(dni) if dni else "")
        can.drawString(COORD["codigo"][0], COORD["codigo"][1], str(cod) if cod else "")
        can.drawString(COORD["tipo"][0], COORD["tipo"][1], str(tipo_dos) if tipo_dos else "")
        
        str_alta = alta.strftime("%d/%m/%Y") if hasattr(alta, 'strftime') else str(alta or "")
        can.drawString(COORD["alta"][0], COORD["alta"][1], str_alta)
        can.drawString(COORD["fecha_firma"][0], COORD["fecha_firma"][1], texto_fecha)

        if baja and str(baja).strip() not in ["", "None", "nan"]:
            str_baja = baja.strftime("%d/%m/%Y") if hasattr(baja, 'strftime') else str(baja)
            txt_baja = str_baja if str_baja.startswith("Baja:") else f"Baja: {str_baja}"
            can.drawString(COORD["baja"][0], COORD["baja"][1], txt_baja)

        # 3. Dosis Mensuales
        cursor.execute("""
            SELECT periodo, TO_CHAR(periodo, 'MM'), dosis_hsm, dosis_hpm, observaciones 
            FROM registros_dosimetria 
            WHERE codigo_dosimetro = %s AND EXTRACT(YEAR FROM periodo) = 2026;
        """, (cod,))
        
        suma_hsm, suma_hpm = 0.0, 0.0
        hay_hsm, hay_hpm = False, False
        fecha_baja_dt = baja.date() if hasattr(baja, 'date') else baja

        for periodo_dt, mes, hsm, hpm, obs in cursor.fetchall():
            if mes in meses_indices:
                idx = meses_indices[mes]
                y_fila = Y_MESES[idx]

                esta_de_baja = False
                if fecha_baja_dt and periodo_dt:
                    if periodo_dt > fecha_baja_dt:
                        esta_de_baja = True

                hsm_val = float(hsm) if (hsm is not None and not esta_de_baja) else None
                # Si es extremidad, forzamos HPM a None
                hpm_val = float(hpm) if (hpm is not None and not esta_de_baja and not es_extremidad) else None

                can.setFont("Helvetica", 11)
                if hsm_val is not None:
                    txt_hsm = f"{hsm_val:.2f}".replace('.', ',')
                    can.drawString(X_HSM, y_fila, txt_hsm)
                    suma_hsm += hsm_val
                    hay_hsm = True
                    
                if hpm_val is not None:
                    txt_hpm = f"{hpm_val:.2f}".replace('.', ',')
                    can.drawString(X_HPM, y_fila, txt_hpm)
                    suma_hpm += hpm_val
                    hay_hpm = True

                if obs:
                    p = Paragraph(str(obs), estilo_obs)
                    w, h = p.wrap(ANCHO_OBS, 100)
                    p.drawOn(can, X_OBS, y_fila - (h / 2) + 4.0)

        # 4. Totales (Solo se escriben si existieron datos en ese indicador)
        can.setFont("Helvetica", 11)
        if hay_hsm:
            can.drawString(X_HSM, Y_SUMATORIOS, f"{suma_hsm:.2f}".replace('.', ','))
        if hay_hpm:
            can.drawString(X_HPM, Y_SUMATORIOS, f"{suma_hpm:.2f}".replace('.', ','))

        can.save()
        packet.seek(0)

        pdf_plantilla = PdfReader(ruta_plantilla_pdf)
        pdf_datos = PdfReader(packet)

        pagina = pdf_plantilla.pages[0]
        pagina.merge_page(pdf_datos.pages[0])
        
        writer_final.add_page(pagina)

    cursor.close()
    conexion.close()

    salida_pdf_memoria = io.BytesIO()
    writer_final.write(salida_pdf_memoria)
    salida_pdf_memoria.seek(0)
    
    return salida_pdf_memoria.getvalue()