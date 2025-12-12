"""
Google Chat Webhook con Fast-Agent, PostgreSQL MCP y manejo de timeouts

Este servidor recibe mensajes de Google Chat, los procesa usando un agente de IA
conectado a una base de datos PostgreSQL. Si el agente se demora, envía mensajes
de espera y continúa procesando en background.

Arquitectura:
    Google Chat → FastAPI (webhook) → Background Task → Fast-Agent → Claude + PostgreSQL MCP
                                   ↓
                              Si timeout → Mensaje de espera via API de Google Chat
                                   ↓
                              Respuesta final via API de Google Chat

Requisitos:
    - fast-agent-mcp
    - fastapi
    - uvicorn
    - google-auth
    - google-api-python-client
    - boto3
    - Archivos de configuración: fastagent.config.yaml, fastagent.secrets.yaml

Para correr:
    uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, Request, HTTPException, Header, Depends, status
from fastapi.responses import JSONResponse
from mcp_agent.core.fastagent import FastAgent
from mcp_agent.core.prompt import Prompt
import asyncio
import re
import json
import boto3
from google.oauth2 import service_account, id_token
from googleapiclient.discovery import build
from google.auth.transport import requests as google_requests
from typing import Optional, Tuple
import logging

# =============================================================================
# CONFIGURACIÓN DE LOGGING
# =============================================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

if logger.hasHandlers():
    logger.handlers.clear()

handler = logging.FileHandler("app.log", mode="w")
formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s"
)

handler.setFormatter(formatter)
logger.addHandler(handler)


# =============================================================================
# CONFIGURACIÓN DE JSON DE HISTORIAS
# =============================================================================

import json
from pathlib import Path
from datetime import datetime

HISTORY_DIR = Path("./chat_history")
HISTORY_DIR.mkdir(exist_ok=True)
MAX_MESSAGES = 4  # 2 intercambios completos


def get_history(user_email: str) -> list:
    """Obtiene el historial del usuario."""
    # Sanitizar email para nombre de archivo
    safe_name = user_email.replace("@", "_at_").replace(".", "_")
    file = HISTORY_DIR / f"{safe_name}.json"
    
    if file.exists():
        try:
            return json.loads(file.read_text())
        except json.JSONDecodeError:
            return []
    return []


def save_history(user_email: str, history: list):
    """Guarda el historial, manteniendo solo los últimos N mensajes."""
    safe_name = user_email.replace("@", "_at_").replace(".", "_")
    file = HISTORY_DIR / f"{safe_name}.json"
    
    # Mantener solo los últimos MAX_MESSAGES
    history = history[-MAX_MESSAGES:]
    
    file.write_text(json.dumps(history, indent=2))


def add_to_history(user_email: str, role: str, content: str):
    """Agrega un mensaje al historial."""
    history = get_history(user_email)
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })
    save_history(user_email, history)

# =============================================================================
# CONSTANTES
# =============================================================================

TIMEOUT_SECONDS = 20  # Tiempo antes de enviar mensaje de espera
SECRET_NAME = "SECRET_NAME_AWS"  # Nombre del secret en AWS
ADDEDTOSPACE = "Hi! I’m your assistant. How can I help you?"
NOUSERMESSAGE = "I didn’t receive any message. How can I help you?"
NOSPACEIDENTIFIED = "Internal error: I couldn't identify the chat."
ERROR_MESSAGE = "Sorry, a technical error occurred while processing your request. Please contact support."
WAITING_MESSAGES = [
    "I'm processing your request, give me a moment...",
    "Sorry for the delay, I'm still working on your request.",
    "I'm sorry, I had a technical issue and couldn't process your request. Could you contact support?"
]
ALLOWED_DOMAIN = "test.com"
# Tu número de proyecto de Google Cloud (lo sacas de la consola de GCP)
# Es necesario para verificar que el token fue emitido para TU bot.
PROJECT_URL = "https://url.com/"  # REEMPLAZAR ESTO

# =============================================================================
# INICIALIZACIÓN DE LA APLICACIÓN
# =============================================================================

app = FastAPI()
fast = FastAgent("Internal AI Chat" )

# Cache para las credenciales
SERVICE_ACCOUNT_INFO = None
_google_credentials_cache = None

instructions = """You are a Quotation Assistant designed to help the quotation team retrieve vendor quotation information stored in a PostgreSQL database. You must provide only user-relevant information. Do not reveal internal processes, SQL queries, technical steps, or implementation details.
====================================================================
DATABASE CONTEXT
You only have access to the view detailed here:
Database: postgres  
Schema: public  
View: view_test
Columns:
- column 1 (INT): description.  
- column 2 (JSONB): description:
.....
====================================================================
PRIMARY FUNCTION: 
For each relevant question, return:
-
-
- 
If no information is found, respond:
"text."
Your responses must never include:
- SQL statements  
- Comments about modifying queries  
- Troubleshooting steps  
- Mentions of database structure  
- Explanations of how the search was done  
- Technical commentary  
====================================================================
CRITICAL DATABASE RULES
- ALWAYS use LIMIT 5 in your queries (maximum 10 rows per query). If the user request more, refuse.
- NEVER do SELECT * - always specify only needed columns
- For text searches, use ILIKE with specific terms, never retrieve all data
- Example bad query: SELECT * FROM view
====================================================================
INTERACTION RULES
- Be transparent when no matches are found.
- Never show internal processing, analysis steps, or how the information was retrieved.
- Do not claim to remember anything. Do not fabricate previous context.
====================================================================
SCOPE LIMITATION
You must only respond to topics related to:
- 
-
-
For unrelated requests, respond:
"text"
"""


# =============================================================================
# DEFINICIÓN DEL AGENTE
# =============================================================================

@fast.agent(
    name="db_assistant",
    instruction=instructions,
    servers=["postgres"]
)
async def db_agent():
    pass


# =============================================================================
# FUNCIONES PARA GOOGLE CHAT API
# =============================================================================

def get_service_account_info():
    """Obtiene el JSON de la cuenta de servicio de AWS Secrets Manager."""
    global SERVICE_ACCOUNT_INFO
    if SERVICE_ACCOUNT_INFO:
        return SERVICE_ACCOUNT_INFO

    try:
        session = boto3.session.Session()
        client = session.client(service_name='secretsmanager', region_name='region')
        
        get_secret_value_response = client.get_secret_value(SecretId=SECRET_NAME)
        
        secret_content = get_secret_value_response['SecretString']
        SERVICE_ACCOUNT_INFO = json.loads(secret_content)
        logger.info("Service account info obtenido de Secrets Manager")
        return SERVICE_ACCOUNT_INFO
        
    except Exception as e:
        logger.error(f"ERROR: No se pudo obtener el secreto {SECRET_NAME} de Secrets Manager: {e}")
        raise


def get_google_credentials():
    """Convierte el service account info en credenciales de Google."""
    global _google_credentials_cache
    
    if _google_credentials_cache is not None:
        return _google_credentials_cache
    
    service_account_info = get_service_account_info()
    
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=['https://www.googleapis.com/auth/chat.bot']
    )
    
    _google_credentials_cache = credentials
    logger.info("Credenciales de Google creadas exitosamente")
    return credentials


def get_chat_service():
    """Crea y retorna el servicio de Google Chat API."""
    credentials = get_google_credentials()
    service = build('chat', 'v1', credentials=credentials)
    return service


MAX_MESSAGE_LENGTH = 4000  # Google Chat limit es 4096, dejamos margen


def split_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """
    Divide un mensaje largo en partes más pequeñas.
    Intenta cortar en saltos de línea para no romper oraciones.
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    remaining = text
    
    while len(remaining) > max_length:
        # Buscar el último salto de línea dentro del límite
        cut_point = remaining.rfind('\n', 0, max_length)
        
        # Si no hay salto de línea, buscar el último espacio
        if cut_point == -1:
            cut_point = remaining.rfind(' ', 0, max_length)
        
        # Si tampoco hay espacio, cortar en el límite
        if cut_point == -1:
            cut_point = max_length
        
        parts.append(remaining[:cut_point].strip())
        remaining = remaining[cut_point:].strip()
    
    if remaining:
        parts.append(remaining)
    
    return parts


async def send_message_via_api(space_name: str, text: str, thread_name: Optional[str] = None):
    """
    Envía un mensaje usando la API de Google Chat.
    Si el mensaje es muy largo, lo divide en partes.
    NO usa threads - siempre envía como mensaje directo.
    """
    try:
        service = get_chat_service()
        # Dividir mensaje si es muy largo (el texto ya viene limpio de run_agent)
        message_parts = split_message(text)
        
        logger.info(f"Enviando {len(message_parts)} parte(s) de mensaje a {space_name}")
        
        results = []
        for i, part in enumerate(message_parts):
            # Agregar indicador si hay múltiples partes (sin corchetes)
            part_text = part
            
            # NO incluir thread para que sea mensaje directo, no respuesta en thread
            message_body = {"text": part_text}
            
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda mb=message_body: service.spaces().messages().create(
                    parent=space_name,
                    body=mb
                ).execute()
            )
            results.append(result)
            
            # Pequeña pausa entre mensajes para evitar rate limiting
            if i < len(message_parts) - 1:
                await asyncio.sleep(0.5)
        
        logger.info(f"Mensaje(s) enviado(s) exitosamente a {space_name}")
        return results
        
    except Exception as e:
        logger.error(f"Error enviando mensaje via API: {e}")
        raise


# =============================================================================
# PROCESAMIENTO DEL AGENTE
# =============================================================================
async def run_agent(user_email: str, user_message: str) -> str:
    """Ejecuta el agente con historial de conversación."""
    try:
        async with fast.run() as agent:
            # 1. Obtener historial previo
            history = get_history(user_email)
            
            # 2. Construir mensajes para el agente
            messages = []
            for msg in history:
                content = msg["content"][:1500]  # Truncar mensajes largos
                if msg["role"] == "user":
                    messages.append(Prompt.user(content))
                else:
                    messages.append(Prompt.assistant(content))
            
            # 3. Agregar mensaje actual
            messages.append(Prompt.user(user_message))
            # 4. Generar respuesta
            response = await agent.db_assistant.generate(messages)
            response_text = response.content[-1].text if response.content else "No response"
            
            # 5. Guardar ambos mensajes en historial
            add_to_history(user_email, "user", user_message)
            add_to_history(user_email, "assistant", response_text)
            
            logger.info(f"Historial de {user_email}: {len(get_history(user_email))} mensajes")
            
            return response_text
            
    except Exception as e:
        logger.error(f"Error en el agente: {e}")
        return f"Error: {str(e)}"


async def try_quick_response(user_email:str, user_message: str) -> Tuple[bool, str, Optional[asyncio.Task]]:
    """
    Intenta obtener una respuesta rápida del agente.
    
    IMPORTANTE: Usa asyncio.shield() para que la tarea NO se cancele en timeout.
    
    Returns:
        Tuple de (éxito, respuesta_o_mensaje_espera, task_si_continua)
    """
    # Crear la tarea del agente
    agent_task = asyncio.create_task(run_agent(user_email, user_message))
    
    try:
        # CLAVE: asyncio.shield() protege la tarea de ser cancelada
        result = await asyncio.wait_for(
            asyncio.shield(agent_task), 
            timeout=TIMEOUT_SECONDS
        )
        # El agente respondió a tiempo
        logger.info(f"try_quick_response: Agente respondió a tiempo")
        return (True, result, None)
        
    except asyncio.TimeoutError:
        # El agente no respondió a tiempo, pero la tarea SIGUE CORRIENDO
        # gracias a asyncio.shield()
        logger.info(f"Timeout en try_quick_response: {TIMEOUT_SECONDS}s, tarea continúa en background")
        return (False, WAITING_MESSAGES[0], agent_task)


async def continue_processing_in_background(
    agent_task: asyncio.Task,
    space_name: str,
    thread_name: Optional[str],
    sender_name: str
):
    """
    Continúa esperando la respuesta del agente en background.
    Envía mensajes de espera adicionales si sigue tardando.
    Maneja errores y notifica al usuario si algo falla.
    """
    message_index = 1  # Ya enviamos el primer mensaje (índice 0) via webhook entonces el siguiente sería el índice 1
    
    logger.info(f"Iniciando procesamiento en background para {sender_name}")
    logger.info(f"Estado de agent_task: done={agent_task.done()}, cancelled={agent_task.cancelled()}")
    
    try:
        while not agent_task.done() and message_index < len(WAITING_MESSAGES)-1:
            try:
                logger.info(f"Background: Esperando {TIMEOUT_SECONDS}s para {sender_name} (intento {message_index})")
                
                # Esperar la tarea directamente, sin shield adicional
                # La tarea ya está protegida desde try_quick_response
                result = await asyncio.wait_for(
                    asyncio.shield(agent_task),
                    timeout=TIMEOUT_SECONDS
                )
                
                # El agente terminó, verificar el resultado
                logger.info(f"Background: Agente completó para {sender_name}")
                logger.info(f"Background: Longitud de result: {len(result) if result else 0}")
                logger.info(f"Background: Primeros 200 chars: {result[:200] if result else 'VACIO'}...")
                
                # Enviar respuesta final
                try:
                    await send_message_via_api(space_name, result, thread_name)
                    logger.info(f"Background: Respuesta enviada exitosamente para {sender_name}")
                except Exception as api_error:
                    logger.error(f"Error enviando respuesta final via API para {sender_name}: {api_error}")
                    try:
                        await send_message_via_api(space_name, ERROR_MESSAGE, thread_name)
                    except:
                        logger.error(f"No se pudo notificar el error al usuario {sender_name}")
                return
                
            except asyncio.TimeoutError:
                # Enviar siguiente mensaje de espera
                waiting_msg = WAITING_MESSAGES[message_index]
                logger.info(f"Background: Timeout #{message_index + 1} para {sender_name}, enviando: {waiting_msg}")
                
                try:
                    await send_message_via_api(space_name, waiting_msg, thread_name)
                except Exception as e:
                    logger.error(f"Error enviando mensaje de espera para {sender_name}: {e}")
                
                message_index += 1
                
                # Si llegamos al último mensaje (error técnico), cancelar tarea
                if message_index >= len(WAITING_MESSAGES)-1:
                    logger.warning(f"Cancelando tarea para {sender_name} después de todos los timeouts")
                    agent_task.cancel()
                    return
        
        # Verificar si la tarea terminó mientras procesábamos
        if agent_task.done() and not agent_task.cancelled():
            try:
                result = agent_task.result()
                logger.info(f"Background: Tarea completó durante verificación para {sender_name}")
                logger.info(f"Background: result from agent_task.result(): {result[:200] if result else 'VACIO'}...")
                await send_message_via_api(space_name, result, thread_name)
            except asyncio.CancelledError:
                logger.warning(f"Tarea fue cancelada para {sender_name}")
            except Exception as e:
                logger.error(f"Error obteniendo resultado del agente para {sender_name}: {e}")
                try:
                    await send_message_via_api(space_name, ERROR_MESSAGE, thread_name)
                except:
                    logger.error(f"No se pudo notificar el error al usuario {sender_name}")
                    
    except Exception as e:
        # Capturar cualquier error no manejado
        logger.error(f"Error inesperado en background processing para {sender_name}: {e}")
        try:
            await send_message_via_api(space_name, ERROR_MESSAGE, thread_name)
        except:
            logger.error(f"No se pudo notificar el error al usuario {sender_name}")

# =============================================================================
# FUNCIONES DE SEGURIDAD
# =============================================================================

async def verify_google_chat_token(authorization: str = Header(None)):
    """
    Valida criptográficamente que la petición viene de Google.
    Actúa como 'bouncer': si falla, el webhook nunca se ejecuta.
    """
    if not authorization:
        logger.warning("Intento de acceso sin header de autorización")
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    
    try:
        # Extraer token "Bearer <token>"
        token_parts = authorization.split(" ")
        if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
            raise HTTPException(status_code=401, detail="Invalid Header Format")
        token = token_parts[1]
        # Verificar la firma del token con los certificados de Google
        request = google_requests.Request()
        id_info = id_token.verify_oauth2_token(
            token, 
            request, 
            audience=PROJECT_URL
        )
        # Verificar que el emisor sea Google Chat
        VALID_ISSUERS = [
        'chat@system.gserviceaccount.com',
        'https://accounts.google.com'
        ]

        if id_info.get('iss') not in VALID_ISSUERS:
            logger.error(f"Issuer invalido: {id_info}")
            raise HTTPException(status_code=403, detail="Invalid issuer")
            
    except Exception as e:
        logger.error(f"Error de autenticación: {e}")
        raise HTTPException(status_code=403, detail="Invalid Token")

# =============================================================================
# ENDPOINT PRINCIPAL - WEBHOOK DE GOOGLE CHAT
# =============================================================================

@app.post("/")
async def google_chat_webhook(request: Request, auth: None = Depends(verify_google_chat_token)):
    """
    Endpoint que recibe los mensajes de Google Chat.
    
    Flujo:
    1. Si el agente responde en menos de TIMEOUT_SECONDS → responde al webhook
    2. Si no → responde con mensaje de espera y continúa en background via API
    """
    
    body = await request.json()
    logger.info(f"""Mensaje recibido: {json.dumps(body.get("chat", {}).get("messagePayload", {}).get("message", {}), indent=2)}""")
    
    chat_data = body.get("chat", {})
    
    # -------------------------------------------------------------------------
    # Evento: Bot agregado a un espacio/chat
    # -------------------------------------------------------------------------
    if chat_data.get("addedToSpacePayload"):
        return build_response(ADDEDTOSPACE)
    
    # -------------------------------------------------------------------------
    # Evento: Bot removido de un espacio/chat
    # -------------------------------------------------------------------------
    if chat_data.get("removedFromSpacePayload"):
        return JSONResponse(content={}, status_code=200)
    
    # -------------------------------------------------------------------------
    # Extraer información del mensaje
    # -------------------------------------------------------------------------
    message_payload = chat_data.get("messagePayload", {})
    message = message_payload.get("message", {})
    user_message = message.get("text", "")
    
    # Información del remitente
    sender = message.get("sender", {})
    sender_name = sender.get("displayName", "Usuario")
    user_email = sender.get("email", "")
    
    if not user_email.endswith(f"@{ALLOWED_DOMAIN}"):
        logger.warning(f"Acceso denegado: Email {user_email} no pertenece a {ALLOWED_DOMAIN}")
        return build_response(f"Service not allowed")
    # Información del espacio y thread
    space = message_payload.get("space", {}) or message.get("space", {})
    space_name = space.get("name", "")
    
    thread = message.get("thread", {})
    thread_name = thread.get("name", "")
    
    # -------------------------------------------------------------------------
    # Validaciones
    # -------------------------------------------------------------------------
    if not user_message:
        return build_response(NOUSERMESSAGE)
    
    if not space_name:
        logger.error("No se pudo obtener el space_name del mensaje")
        return build_response(NOSPACEIDENTIFIED)
    
    # -------------------------------------------------------------------------
    # Intentar respuesta rápida
    # -------------------------------------------------------------------------
    success, response_text, pending_task = await try_quick_response(user_email, user_message)
    
    if success:
        # El agente respondió a tiempo, devolver respuesta directamente al webhook
        logger.info(f"Respuesta rápida para {sender_name}")
        return build_response(response_text)
    else:
        # El agente no respondió a tiempo
        # Lanzar procesamiento en background (sin await para no bloquear)
        logger.warning(f"Lanzando tarea en background para {sender_name}")
        asyncio.create_task(
            continue_processing_in_background(
                pending_task,
                space_name,
                thread_name if thread_name else None,
                sender_name
            )
        )
        
        # Responder al webhook con el primer mensaje de espera
        logger.info(f"Respondiendo webhook con mensaje de espera: {response_text} para {sender_name}")
        return build_response(response_text)


# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================

def build_response(text: str) -> JSONResponse:
    """Construye la respuesta en el formato que espera Google Chat."""
    # El texto ya viene limpio de run_agent
    # Truncar si es muy largo (el webhook solo puede enviar 1 mensaje)
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH - 100] + "\n\n... (messaje truncated, long response)"
    
    response_data = {
        "hostAppDataAction": {
            "chatDataAction": {
                "createMessageAction": {
                    "message": {
                        "text": text
                    }
                }
            }
        }
    }
    
    return JSONResponse(
        content=response_data,
        status_code=200,
        headers={"Content-Type": "application/json"}
    )


# =============================================================================
# ENDPOINT DE HEALTH CHECK
# =============================================================================

@app.get("/health")
async def health_check():
    """Endpoint para verificar que el servidor está funcionando."""
    return {"status": "healthy", "service": "google-chat-webhook"}


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
