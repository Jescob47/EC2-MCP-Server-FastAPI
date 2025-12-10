🧹 EC2 Maintenance Scripts — README

Este repositorio contiene dos scripts diseñados para realizar mantenimiento automático en servidores Ubuntu que corren en instancias EC2 de AWS. Estos scripts ayudan a liberar espacio en disco y garantizar que el servidor se mantenga estable con el paso del tiempo.


📌 Scripts incluidos
cache_cleaning.sh

Realiza tareas de mantenimiento general, como:

Limpieza de cachés del sistema (apt).

Eliminación de archivos temporales.

Reducción del tamaño de logs grandes.

Remoción de paquetes obsoletos.

Este script está pensado para uso manual o ejecución mensual. Algunas tareas pueden afectar rendimiento si se ejecutan demasiado seguido.

snap_cleanup.sh

Realiza una limpieza segura del sistema Snap:

Elimina revisiones deshabilitadas.

Limpia la caché de Snapd (/var/lib/snapd/cache).

Identifica archivos .snap huérfanos (no montados).

Elimina únicamente los .snap huérfanos.

Esto es muy útil porque /snap y /var/lib/snapd suelen ocupar varios GB en servidores pequeños.

⚠️ Requisito

snapd debe estar instalado. Para verificar:

snap --version

🖥️ Configuración en una instancia EC2

Sigue estos pasos desde tu sesión SSH en el servidor.

1. Conectarte al servidor EC2
ssh -i /ruta/tu-llave.pem ubuntu@<PUBLIC_IP>

2. Crear el directorio donde vivirán los scripts
sudo mkdir -p /home/ubuntu/maintenance
sudo chown ubuntu:ubuntu /home/ubuntu/maintenance

3. Clonar el repositorio de GitHub
git clone https://github.com/Jescob47/Cache_Snap_Cleaning.git

4. Dar permisos de ejecución
sudo chmod 750 /home/ubuntu/maintenance/cache_cleaning.sh
sudo chmod 750 /home/ubuntu/maintenance/snap_cleanup.sh

5. Probar los scripts manualmente
sudo /home/ubuntu/maintenance/snap_cleanup.sh
sudo /home/ubuntu/maintenance/cache_cleaning.sh

⏱️ Programar ejecución automática (cron)

Editar crontab:

sudo crontab -e


Agregar:

# Limpieza de snaps — día 1 de cada mes a las 3:00 AM
0 3 1 * * /home/ubuntu/maintenance/snap_cleanup.sh >> /home/ubuntu/maintenance/snap_cleanup.log 2>&1

# Limpieza general — día 1 de cada mes a las 4:00 AM
0 4 1 * * /home/ubuntu/maintenance/cache_cleaning.sh >>/home/ubuntu/maintenance/cache_cleaning.log 2>&1


Esto:

Automatiza ambas limpiezas.

Distribuye carga.

Guarda logs persistentes.

📊 Verificar espacio liberado

Ver uso general:

df -h


Ver qué directorios ocupan más:

sudo du -h --max-depth=1 / 2>/dev/null
