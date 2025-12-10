🧹 EC2 Maintenance Scripts — README

Este repositorio contiene dos scripts diseñados para realizar mantenimiento automático en servidores Ubuntu que corren en instancias EC2 de AWS. Estos scripts ayudan a liberar espacio en disco y garantizar que el servidor se mantenga estable con el paso del tiempo.

Nota:
Este repositorio no incluye el contenido interno de los scripts, solo su propósito y las instrucciones para configurarlos en una instancia EC2.

📌 Scripts incluidos
1. cache_cleaning.sh

Realiza tareas de mantenimiento general en el servidor, como:

Limpieza de cachés del sistema (ej. apt).

Eliminación de archivos temporales.

Reducción del tamaño de logs pesados.

Remoción de paquetes obsoletos.

Este script está pensado para ejecutarse manualmente o de forma mensual, ya que algunas de sus operaciones son agresivas si se ejecutan muy seguido.

2. snap_cleanup.sh

Este script elimina únicamente las versiones deshabilitadas de snaps, las cuales son versiones antiguas que Ubuntu conserva innecesariamente.

Esto es especialmente útil porque en servidores pequeños el directorio /snap puede crecer rápidamente y consumir varios gigabytes.

Este script se puede automatizar de forma segura para que se ejecute una vez al mes.

🖥️ Cómo configurarlos en una instancia EC2

Sigue estos pasos desde tu sesión SSH en el servidor EC2.

1. Conectarse al servidor EC2
ssh -i /ruta/tu-llave.pem ubuntu@<PUBLIC_IP>

2. Crear el directorio donde vivirán los scripts
sudo mkdir -p /usr/local/bin/maintenance
sudo chown ubuntu:ubuntu /usr/local/bin/maintenance


Se recomienda usar /usr/local/bin/maintenance ya que es un estándar para scripts personalizados del sistema.

3. Subir los scripts al repositorio de GitHub

Estos scripts deben vivir en tu repo GitHub dentro de scripts/.

Cuando los clones directamente en tu EC2, se copiarán automáticamente.

Ejemplo (cambia la URL por tu repo):

git clone https://github.com/tuusuario/tu-repo.git

4. Mover los scripts del repositorio al directorio del sistema

Asumiendo que el repo contiene:

scripts/cache_cleaning.sh
scripts/snap_cleanup.sh


Entonces:

cd tu-repo/scripts

sudo mv cache_cleaning.sh /usr/local/bin/maintenance/cache_cleaning.sh
sudo mv snap_cleanup.sh  /usr/local/bin/maintenance/snap_cleanup.sh

5. Dar permisos de ejecución
sudo chmod 750 /usr/local/bin/maintenance/cache_cleaning.sh
sudo chmod 750 /usr/local/bin/maintenance/snap_cleanup.sh


Opcional pero recomendado:

sudo chown root:root /usr/local/bin/maintenance/*.sh

6. Probar los scripts manualmente

Ejecuta cada uno para confirmar que funcionan sin errores:

sudo /usr/local/bin/maintenance/snap_cleanup.sh
sudo /usr/local/bin/maintenance/cache_cleaning.sh

⏱️ Programar ejecución automática (cron)

Para que los scripts se ejecuten automáticamente cada mes:

sudo crontab -e


Agregar al final:

# Limpieza de snaps — día 1 del mes a las 3:00 AM
0 3 1 * * /usr/local/bin/maintenance/snap_cleanup.sh >> /var/log/snap_cleanup.log 2>&1

# Limpieza general — día 1 del mes a las 4:00 AM
0 4 1 * * /usr/local/bin/maintenance/cache_cleaning.sh >> /var/log/cache_cleaning.log 2>&1


Esto:

Automatiza ambas limpiezas.

Divide las tareas para evitar saturar el servidor.

Guarda logs persistentes en /var/log/.

📊 Verificar espacio liberado

Después de que corran los scripts o cuando quieras:

df -h


Usa esto para ver qué directorios ocupan más:

sudo du -h --max-depth=1 / 2>/dev/null
