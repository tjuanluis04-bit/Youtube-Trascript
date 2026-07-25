# Transcripciones YouTube

App para Android (hecha con Python + Kivy) que obtiene el texto de la
transcripción de cualquier video de YouTube a partir de su enlace, **sin
marcas de tiempo**, en el idioma original del video y/o traducido al
**español**.

## Cómo funciona

1. Pegas uno o varios enlaces de video, uno por línea (con o sin
   numeración: "1.- link", "2) link", o el link solo). Funciona con
   `youtube.com/watch?v=...`, `youtu.be/...`, `youtube.com/shorts/...`,
   etc.
2. Eliges: "Idioma original", "Español" o "Ambos" (aplica a todos los
   enlaces del lote).
3. Tocas "Obtener transcripción(es)". Por cada enlace aparece una
   tarjeta independiente con: título, miniatura grande (16:9), botón
   para descargar la miniatura, el texto transcrito y su propio botón
   "Copiar este texto" — cada tarjeta se copia por separado y están
   divididas por una línea.

Internamente usa la librería `youtube-transcript-api`, que lee los
subtítulos/CC del video. Si el video no tiene subtítulos en español pero
sí en cualquier otro idioma, la app primero intenta la traducción
automática de YouTube; si YouTube bloquea esa función temporalmente
(pasa seguido si se piden muchas traducciones seguidas), la app cae
automáticamente a traducir por su cuenta con Google Translate
(`deep-translator`) como alternativa.

**Caché:** cada transcripción ya obtenida se guarda en el propio
teléfono. Si vuelves a pedir el mismo video (con el mismo modo de
idioma), aparece al instante sin volver a consultar YouTube.

**Limitación importante:** si el video tiene los subtítulos deshabilitados
por el creador, no existe transcripción que obtener (esto es una
restricción de YouTube, no de la app).

## Cómo compilar el APK con GitHub Actions

1. Crea un repositorio nuevo en GitHub (puede ser privado).
2. Sube todos estos archivos manteniendo la estructura de carpetas
   (incluida la carpeta `.github/workflows/`).
3. Ve a la pestaña **Actions** de tu repositorio. Al hacer push a la rama
   `main`, el workflow **Build APK** se ejecutará solo.
   - También puedes lanzarlo manualmente desde Actions → Build APK →
     "Run workflow".
4. Espera a que termine (la primera vez tarda entre 20 y 40 minutos
   porque Buildozer descarga el Android SDK/NDK).
5. Cuando termine, entra al workflow finalizado y descarga el artefacto
   **transcripciones-youtube-apk** — dentro está el archivo `.apk`.
6. Pasa el APK a tu teléfono e instálalo (necesitarás permitir
   "instalar apps de origen desconocido" en Android).

### Si el build falla

El workflow compila con Buildozer directamente en el runner de Ubuntu
(sin depender de acciones de Docker de terceros, que a veces se rompen).
Si aun así falla:

1. Abre el run fallido en la pestaña **Actions** y expande el paso
   **"Compilar APK"** — ahí está el error real, normalmente cerca del
   final del log.
2. Copia ese fragmento del error y compártelo para poder ajustar
   `buildozer.spec` o las dependencias del sistema según haga falta.
3. Errores comunes: falta algún paquete del sistema para una librería
   nueva en `requirements`, o una versión de Cython/Kivy incompatible
   entre sí.

### Subir el proyecto a GitHub desde cero (por línea de comandos)

```bash
cd youtube-transcript-app
git init
git add .
git commit -m "Primera versión"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

## Estructura del proyecto

```
youtube-transcript-app/
├── main.py                        # Código de la app (Kivy)
├── buildozer.spec                 # Configuración de compilación Android
├── .github/workflows/build-apk.yml # Workflow de GitHub Actions
└── README.md
```

## Probar en tu computadora antes de compilar (opcional)

Si tienes Python instalado:

```bash
pip install kivy youtube-transcript-api
python main.py
```

Esto abre la app en una ventana de escritorio para probarla antes de
generar el APK.
