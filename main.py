from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp_agent.core.fastagent import FastAgent
import re
import asyncio
import json
import os

# --- Importaciones de AWS y Google ---
import boto3
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# ------------------------------------

# =============================================================================
# CONFIGURACIÓN (AJUSTA ESTOS VALORES)
# =============================================================================

app = FastAPI()
fast = FastAgent("Google Chat DB Agent")

# **IMPORTANTE: Define el nombre de tu secreto en AWS Secrets Manager**
SECRET_NAME = "google_chat_service_account_key" 

CHAT_SCOPES = ['https://www.googleapis.com/auth/chat.bot']

# Lista de frases de progreso
PROGRESS_PHRASES = [
    "Estoy procesando tu mensaje...",
    "Disculpa la demora, puede que me tome un poco más de tiempo.",
    "Sigo analizando los datos, casi tengo la respuesta.",
    "Paciencia, ya estoy terminando el análisis.",
]
PROGRESS_INTERVAL = 15 # Intervalo en segundos entre cada frase

# Almacén global para las credenciales para evitar llamadas repetidas a Secrets Manager
SERVICE_ACCOUNT_INFO = None

# =============================================================================
# FUNCIONES DE SEGURIDAD Y GOOGLE CHAT API
# =============================================================================

def get_secret_json():
    """Obtiene el JSON de la cuenta de servicio de AWS Secrets Manager."""
    global SERVICE_ACCOUNT_INFO
    if SERVICE_ACCOUNT_INFO:
        return SERVICE_ACCOUNT_INFO

    try:
        session = boto3.session.Session()
        client = session.client(service_name='secretsmanager')
        
        get_secret_value_response = client.get_secret_value(SecretId=SECRET_NAME)
        
        # El valor del secreto es una cadena JSON, lo parseamos
        secret_content = get_secret_value_response['SecretString']
        SERVICE_ACCOUNT_INFO = json.loads(secret_content)
        return SERVICE_ACCOUNT_INFO
        
    except Exception as e:
        print(f"ERROR: No se pudo obtener el secreto {SECRET_NAME} de Secrets Manager: {e}")
        return None

async def send_proactive_message(space_name: str, text: str):
    """Envía un mensaje asíncrono usando la API REST de Google Chat."""
    secret_info = get_secret_json()
    if not secret_info:
        print("No se pudo enviar el mensaje: credenciales no disponibles.")
        return

    try:
        # 1. Autenticación con el JSON cargado
        credentials = Credentials.from_service_account_info(secret_info, scopes=CHAT_SCOPES)
        service = build('chat', 'v1', credentials=credentials, cache_discovery=False)
        
        message_body = {"text": text}
        
        # 2. Función para ejecutar la llamada bloqueante en un hilo separado
        def _execute_api_call():
            # space_name debe ser el 'name' del hilo o espacio
            service.spaces().messages().create(
                parent=space_name,
                body=message_body
            ).execute()

        await asyncio.to_thread(_execute_api_call)
        
    except HttpError as err:
        print(f"Error HTTP al enviar mensaje proactivo: {err}")
    except Exception as e:
        print(f"Error general en send_proactive_message: {e}")

# =============================================================================
# LÓGICA DEL WEBHOOK PRINCIPAL (Rápido)
# =============================================================================

# Esta función de construcción ya no es necesaria para la respuesta asíncrona,
# solo para respuestas inmediatas de error o saludos.
def build_simple_response(text: str) -> JSONResponse:
    """Construye una respuesta simple para el webhook."""
    return JSONResponse(
        content={"text": text},
        status_code=200,
        headers={"Content-Type": "application/json"}
    )

@app.post("/")
async def google_chat_webhook(request: Request):
    """Recibe el webhook de Google Chat y lanza el procesamiento asíncrono."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(content={"text": "Error: Invalid JSON"}, status_code=400)
    
    # --- 1. Extracción de datos ---
    user_message = body.get("message", {}).get("text", "")
    
    # Determinar el identificador de respuesta: spaces/{spaceId}/threads/{threadId}
    if 'thread' in body.get("message", {}):
        space_name = body["message"]["thread"]["name"]
    else:
        # En mensajes directos o el primer mensaje de un espacio
        space_name = body["space"]["name"] 
        
    if not user_message:
        # Respuesta inmediata si no hay mensaje válido (p. ej., solo un usuario se unió)
        return build_simple_response("Hola, ¿en qué puedo ayudarte?")

    # --- 2. Tarea de procesamiento asíncrono ---
    async def run_agent_and_respond_with_progress():
        response_text = ""
        agent_finished = asyncio.Event()
        
        # Tarea A: El Agente de IA (la parte lenta)
        async def agent_task():
            nonlocal response_text
            try:
                async with fast.run() as agent:
                    # El procesamiento lento ocurre aquí
                    response_text = await agent.db_assistant.send(user_message)
                    response_text = re.sub(r'\n\s*\n', '\n\n', response_text).strip()
            except Exception as e:
                response_text = f"Error del Agente: {str(e)}"
            finally:
                agent_finished.set()

        # Tarea B: Los mensajes de progreso
        async def progress_task():
            for phrase in PROGRESS_PHRASES:
                if agent_finished.is_set():
                    break
                
                await send_proactive_message(space_name, phrase)
                
                try:
                    # Espera hasta el intervalo o hasta que el agente termine
                    await asyncio.wait_for(agent_finished.wait(), timeout=PROGRESS_INTERVAL)
                except asyncio.TimeoutError:
                    continue # El agente sigue corriendo, enviamos la siguiente frase
                else:
                    break # El agente terminó

        # Ejecutar A y B concurrentemente
        await asyncio.gather(agent_task(), progress_task())
        
        # --- Envío de la respuesta final ---
        await send_proactive_message(space_name, response_text)

    # --- 3. Lanzar la tarea y responder inmediatamente al webhook ---
    # ¡Esta es la clave para evitar el timeout!
    asyncio.create_task(run_agent_and_respond_with_progress())

    # Respuesta de éxito inmediata (200 OK) con cuerpo vacío o mínimo
    return JSONResponse(content={}, status_code=200)

# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    # En producción, usa un servidor Gunicorn o similar y configura el logging
    uvicorn.run(app, host="0.0.0.0", port=8000)