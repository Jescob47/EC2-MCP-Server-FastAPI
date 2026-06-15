Este proyecto implementa un servidor Webhook para Google Chat utilizando FastAPI y Fast-Agent. Su función principal es actuar como un asistente de IA que consulta una base de datos PostgreSQL mediante el protocolo MCP (Model Context Protocol) para responder dudas de usuarios corporativos.

🚀 Características Principales
Arquitectura Resiliente a Timeouts: Maneja automáticamente el límite de tiempo de respuesta de Google Chat. Si el agente tarda más de 20 segundos, el servidor responde inmediatamente con un mensaje de "espera" y continúa el procesamiento en segundo plano, enviando la respuesta final a través de la API asíncrona.

Seguridad Enterprise:

Verificación de firma de tokens de Google (evita peticiones falsas).

Gestión de credenciales mediante AWS Secrets Manager.

Restricción de acceso por dominio de correo (ALLOWED_DOMAIN).

Historial de Chat: Mantiene un contexto limitado de la conversación (últimos 4 mensajes) almacenado en archivos JSON locales.

Integration con MCP: Utiliza fast-agent-mcp para conectar con PostgreSQL de forma segura sin exponer queries SQL al usuario final.

📋 Requisitos Previos
Python 3.10+

Cuenta de AWS (para Secrets Manager y Boto3).

Proyecto en Google Cloud Platform (API de Google Chat habilitada).

Servidor PostgreSQL accesible.

🛠️ Instalación
Clonar el repositorio:
git clone https://github.com/Jescob47/EC2-MCP-Server-FastAPI.git
cd tu-repo

Crear entorno virtual:
python3 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
Instalar dependencias: 
pip install -r requirements.txt

⚙️ Configuración
1. Archivos de Configuración de Fast-Agent
El sistema requiere dos archivos YAML en la raíz para la configuración del agente MCP:

fastagent.config.yaml
fastagent.secrets.yaml

2. Variables de Entorno y Constantes
Edita el archivo main.py para ajustar las siguientes constantes a tu entorno:

Python
# En main.py

SECRET_NAME = "SECRET_NAME_AWS"  # Nombre de tu secreto en AWS Secrets Manager
ALLOWED_DOMAIN = "tu-empresa.com" # Dominio de correo permitido
PROJECT_URL = "https://..."       # Audience URL de tu proyecto Google Cloud

3. AWS Secrets Manager
El código espera encontrar un secreto en AWS con el nombre definido en SECRET_NAME. Este secreto debe contener el JSON de la Cuenta de Servicio de Google (Service Account Key) necesaria para usar la API de Chat.

4. Permisos IAM (Si despliegas en EC2)
Asegúrate de que el Rol IAM adjunto a tu instancia EC2 tenga permisos para leer el secreto:

{
    "Effect": "Allow",
    "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:region:account:secret:SECRET_NAME-??????"
}

🚀 Ejecución
Para desarrollo local o producción con el entorno virtual encendido:
uvicorn main:app --host 0.0.0.0 --port 8000

🧠 Arquitectura de Flujo
El sistema utiliza un enfoque híbrido (Sincrónico/Asincrónico) para garantizar una buena experiencia de usuario:

Recepción: Llega el Webhook desde Google Chat.

Validación: Se verifica el Token y el Dominio del email.

Intento Rápido (Shielded Task):

Se lanza el agente de IA.

Si responde en < 20 segundos, se devuelve la respuesta directamente al Webhook.

Fallback (Timeout):

Si pasan 20 segundos, el servidor responde al Webhook con "I'm processing your request...".

La tarea del agente continúa en segundo plano (background task).

Una vez el agente termina, el sistema usa la Google Chat API para enviar la respuesta final de forma proactiva.
