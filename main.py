from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List, Dict, Any
from pathlib import Path
import sys
import os

# --- IMPORTACIONES DE LA LÓGICA DE NEGOCIO ---
from db_manager import (
    iniciar_conexion, inicializar_base_datos, cargar_datos_completos,
    obtener_avisos_pendientes, guardar_cambios_tabla, ejecutar_borrado_datos
)
from lector import (
    extraer_dosimetria_optimizada, guardar_dosimetria_pdf_en_bd, 
    procesar_excel_maestro, procesar_excel_centros
)
from generador import generar_pdf_fichas_centro

# --- LECTURA DE SECRETOS ---
def cargar_todos_los_secretos():
    """Lee el archivo .streamlit/secrets.toml completo forzando UTF-8."""
    secrets_path = Path(".streamlit/secrets.toml")
    if secrets_path.exists():
        if sys.version_info >= (3, 11):
            import tomllib
            with open(secrets_path, "rb") as f:
                return tomllib.load(f)
        else:
            import toml
            with open(secrets_path, "r", encoding="utf-8") as f:
                return toml.load(f)
    return {}

TODOS_LOS_SECRETOS = cargar_todos_los_secretos()

PG_SECRETS = TODOS_LOS_SECRETOS.get("postgres", {
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "postgres")
})

# --- INICIALIZACIÓN DE LA BASE DE DATOS ---
engine = iniciar_conexion(PG_SECRETS)
inicializar_base_datos(engine)

# --- CONFIGURACIÓN DE FASTAPI ---
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Añadir esto para permitir que Vercel se conecte sin ser bloqueado
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite cualquier web (o pon la URL de Vercel)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MODELOS DE DATOS (PYDANTIC) ---
class LoginRequest(BaseModel):
    password: str

class GuardarCambiosRequest(BaseModel):
    datos_antiguos: List[Dict[str, Any]]
    datos_nuevos: List[Dict[str, Any]]

class BorrarRequest(BaseModel):
    mes: str
    centro: str


# --- ENDPOINTS REST ---

@app.post("/api/login")
def verificar_password(req: LoginRequest):
    # Intentar obtener la clave desde la variable de entorno de Render, o fallback a secretos locales
    clave_real = os.getenv("APP_PASSWORD") or TODOS_LOS_SECRETOS.get("app_password") or PG_SECRETS.get("app_password")
    
    if not clave_real:
        print("⚠️ ALERTA: No se ha encontrado ninguna variable APP_PASSWORD configurada.")
        raise HTTPException(status_code=500, detail="Error de configuración en el servidor")

    # Comparación limpia sin espacios ni saltos de línea
    if req.password.strip() == str(clave_real).strip():
        return {"status": "success"}
    else:
        print(f"❌ Intento de login fallido. Recibido: '{req.password.strip()}' | Esperado: '{str(clave_real).strip()}'")
        raise HTTPException(status_code=401, detail="Contraseña incorrecta")

@app.get("/api/dosimetria")
def obtener_tabla_completa():
    """Devuelve los datos de la tabla plana y los avisos pendientes en 1 sola petición."""
    df_flat, meses_nombres = cargar_datos_completos(engine)
    df_avisos = obtener_avisos_pendientes(engine)
    
    records = df_flat.fillna("").to_dict(orient="records") if not df_flat.empty else []
    avisos = df_avisos.fillna("").to_dict(orient="records") if not df_avisos.empty else []
    
    return {
        "data": records,
        "avisos": avisos,
        "meses_nombres": meses_nombres
    }

@app.post("/api/dosimetria/guardar")
def guardar_ediciones(payload: GuardarCambiosRequest):
    """Guarda en PostgreSQL los cambios vectoriales realizados desde el frontend."""
    import pandas as pd
    df_old = pd.DataFrame(payload.datos_antiguos)
    df_new = pd.DataFrame(payload.datos_nuevos)
    
    meses_nombres = {1: 'ENERO', 2: 'FEBRERO', 3: 'MARZO', 4: 'ABRIL', 5: 'MAYO', 6: 'JUNIO', 7: 'JULIO', 8: 'AGOSTO', 9: 'SEPTIEMBRE', 10: 'OCTUBRE', 11: 'NOVIEMBRE', 12: 'DICIEMBRE'}
    
    ok, error, hubo_cambios = guardar_cambios_tabla(PG_SECRETS, df_old, df_new, meses_nombres)
    if not ok:
        raise HTTPException(status_code=500, detail=error)
        
    return {"status": "success", "hubo_cambios": hubo_cambios}

@app.post("/api/pdf/procesar")
async def procesar_pdf(file: UploadFile = File(...)):
    """Inyecta la información de un informe PDF de dosimetría."""
    contenido = await file.read()
    temp_path = f"temp_{file.filename}"
    
    with open(temp_path, "wb") as f:
        f.write(contenido)
        
    try:
        df_ext = extraer_dosimetria_optimizada(temp_path)
        if not df_ext.empty:
            guardar_dosimetria_pdf_en_bd(df_ext, PG_SECRETS)
            return {"status": "success", "filename": file.filename}
        else:
            raise HTTPException(status_code=400, detail="No se pudieron extraer datos del PDF")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.get("/api/fichas/exportar/{centro}")
def descargar_pdf_centro(centro: str):
    """Genera y sirve el PDF consolidado de un centro."""
    try:
        pdf_bytes = generar_pdf_fichas_centro(centro, PG_SECRETS, "plantillas/ficha.pdf")
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename=Fichas_{centro}.pdf"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINTS DE ADMINISTRACIÓN ---

@app.post("/api/admin/maestro")
async def admin_maestro(file: UploadFile = File(...)):
    """Carga y procesa el Excel del Maestro de Trabajadores."""
    contenido = await file.read()
    temp_path = f"temp_maestro_{file.filename}"
    with open(temp_path, "wb") as f: 
        f.write(contenido)
    try:
        ok, msg = procesar_excel_maestro(temp_path, PG_SECRETS)
        if not ok: 
            raise HTTPException(status_code=500, detail=msg)
        return {"status": "success", "msg": msg}
    finally:
        if os.path.exists(temp_path): 
            os.remove(temp_path)

@app.post("/api/admin/centros")
async def admin_centros(file: UploadFile = File(...)):
    """Carga y procesa el Excel de Centros."""
    contenido = await file.read()
    temp_path = f"temp_centros_{file.filename}"
    with open(temp_path, "wb") as f: 
        f.write(contenido)
    try:
        ok, msg = procesar_excel_centros(temp_path, PG_SECRETS)
        if not ok: 
            raise HTTPException(status_code=500, detail=msg)
        return {"status": "success", "msg": msg}
    finally:
        if os.path.exists(temp_path): 
            os.remove(temp_path)

@app.post("/api/admin/borrar")
def admin_borrar(req: BorrarRequest):
    """Ejecuta el borrado controlado de datos en base de datos."""
    ok, err = ejecutar_borrado_datos(PG_SECRETS, req.mes, req.centro)
    if not ok: 
        raise HTTPException(status_code=500, detail=err)
    return {"status": "success"}

@app.post("/api/admin/restore")
async def admin_restore(file: UploadFile = File(...)):
    """Restaura una copia de seguridad en CSV directamente a PostgreSQL."""
    import pandas as pd
    contenido = await file.read()
    temp_path = f"temp_restore.csv"
    with open(temp_path, "wb") as f: 
        f.write(contenido)
    try:
        pd.read_csv(temp_path).to_sql('registros_dosimetria', engine, if_exists='append', index=False)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): 
            os.remove(temp_path)
