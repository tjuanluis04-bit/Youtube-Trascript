# -*- coding: utf-8 -*-
"""
Transcripciones YouTube
Obtiene el texto de la transcripción de un video de YouTube a partir de su
enlace, sin marcas de tiempo. Permite obtenerlo en el idioma original del
video y/o traducido al español. Muestra título y miniatura del video.
"""

import os
import re
import time
import threading

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import mainthread, Clock
from kivy.core.clipboard import Clipboard
from kivy.uix.boxlayout import BoxLayout
from kivy.utils import platform

import requests

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    RequestBlocked,
    CouldNotRetrieveTranscript,
)

KV = '''
<RootWidget>:
    orientation: 'vertical'
    padding: dp(14)
    spacing: dp(8)

    TextInput:
        id: url_input
        hint_text: 'Pega aquí el enlace del video de YouTube'
        multiline: False
        size_hint_y: None
        height: dp(48)
        font_size: '16sp'

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(6)
        ToggleButton:
            id: btn_original
            text: 'Idioma original'
            group: 'lang'
            state: 'down'
        ToggleButton:
            id: btn_spanish
            text: 'Español'
            group: 'lang'
        ToggleButton:
            id: btn_both
            text: 'Ambos'
            group: 'lang'

    Button:
        id: fetch_button
        text: 'Obtener transcripción'
        size_hint_y: None
        height: dp(50)
        font_size: '16sp'
        on_release: root.fetch_transcript()

    Label:
        id: status_label
        text: ''
        size_hint_y: None
        height: dp(26)
        color: 0.75, 0.15, 0.15, 1

    AsyncImage:
        id: thumbnail_image
        size_hint_y: None
        height: 0
        opacity: 0
        allow_stretch: True
        keep_ratio: True

    Button:
        id: download_thumb_button
        text: 'Descargar miniatura'
        size_hint_y: None
        height: 0
        opacity: 0
        disabled: True
        on_release: root.download_thumbnail()

    ScrollView:
        TextInput:
            id: output_text
            readonly: True
            text: ''
            size_hint_y: None
            height: max(self.minimum_height, 500)
            font_size: '14sp'

    Button:
        text: 'Copiar texto'
        size_hint_y: None
        height: dp(50)
        font_size: '16sp'
        on_release: root.copy_text()
'''

Builder.load_string(KV)


class RootWidget(BoxLayout):

    _spinner_frames = ['Obteniendo transcripción', 'Obteniendo transcripción.',
                        'Obteniendo transcripción..', 'Obteniendo transcripción...']
    _spinner_event = None
    _spinner_index = 0
    _thumbnail_url = None
    _video_title = ''

    # ---------- utilidades ----------

    def get_video_id(self, url):
        """Extrae el ID del video de distintos formatos de enlace de YouTube."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=)([0-9A-Za-z_-]{11})',
            r'youtu\.be\/([0-9A-Za-z_-]{11})',
            r'youtube\.com\/shorts\/([0-9A-Za-z_-]{11})',
            r'youtube\.com\/embed\/([0-9A-Za-z_-]{11})',
            r'youtube\.com\/live\/([0-9A-Za-z_-]{11})',
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    def _join(self, data):
        """Une los fragmentos de texto de la transcripción, sin timestamps."""
        parts = []
        for item in data:
            text = item['text'] if isinstance(item, dict) else item.text
            text = text.replace('\n', ' ').strip()
            if text:
                parts.append(text)
        return ' '.join(parts)

    def _fetch_video_info(self, video_id):
        """Obtiene título y miniatura del video usando el oEmbed público de YouTube
        (no requiere API key)."""
        try:
            resp = requests.get(
                'https://www.youtube.com/oembed',
                params={'url': f'https://www.youtube.com/watch?v={video_id}', 'format': 'json'},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                title = data.get('title', '')
            else:
                title = ''
        except Exception:
            title = ''
        thumbnail_url = f'https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg'
        return title, thumbnail_url

    # ---------- spinner de carga ----------

    def _start_spinner(self):
        self._spinner_index = 0
        self.ids.fetch_button.disabled = True
        self.ids.status_label.color = (0.85, 0.85, 0.85, 1)
        self._spinner_event = Clock.schedule_interval(self._tick_spinner, 0.4)

    def _tick_spinner(self, dt):
        self.ids.status_label.text = self._spinner_frames[self._spinner_index % 4]
        self._spinner_index += 1

    def _stop_spinner(self):
        if self._spinner_event:
            self._spinner_event.cancel()
            self._spinner_event = None
        self.ids.fetch_button.disabled = False

    # ---------- lógica principal ----------

    def fetch_transcript(self):
        url = self.ids.url_input.text.strip()
        self.ids.status_label.text = ''
        self.ids.output_text.text = ''
        self._hide_thumbnail()

        video_id = self.get_video_id(url)
        if not video_id:
            self.ids.status_label.text = 'Enlace no válido'
            return

        if self.ids.btn_spanish.state == 'down':
            mode = 'es'
        elif self.ids.btn_both.state == 'down':
            mode = 'both'
        else:
            mode = 'original'

        self._start_spinner()
        threading.Thread(
            target=self._fetch_thread, args=(video_id, mode), daemon=True
        ).start()

    def _fetch_thread(self, video_id, mode):
        title, thumbnail_url = self._fetch_video_info(video_id)
        self._video_title = title
        self._thumbnail_url = thumbnail_url
        self._show_thumbnail(thumbnail_url)

        try:
            ytt_api = YouTubeTranscriptApi()
            transcript_list = self._with_retry(ytt_api.list, video_id)
            available_codes = [t.language_code for t in transcript_list]
            if not available_codes:
                raise NoTranscriptFound(video_id, [], transcript_list)

            original = transcript_list.find_transcript(available_codes)

            if mode == 'original':
                text = self._join(self._with_retry(original.fetch))
                result = text

            elif mode == 'es':
                result = self._get_spanish(transcript_list, original)

            else:  # both
                text_o = self._join(self._with_retry(original.fetch))
                text_e = self._get_spanish(transcript_list, original)
                result = (
                    f"--- IDIOMA ORIGINAL ({original.language_code}) ---\n\n"
                    f"{text_o}\n\n"
                    f"--- ESPAÑOL ---\n\n"
                    f"{text_e}"
                )

            if title:
                result = f"{title}\n\n{result}"

            self._update_ui(result, '')

        except TranscriptsDisabled:
            self._update_ui('', 'Este video tiene los subtítulos deshabilitados')
        except NoTranscriptFound:
            self._update_ui('', 'No se encontró transcripción para este video')
        except VideoUnavailable:
            self._update_ui('', 'Video no disponible o privado')
        except RequestBlocked:
            self._update_ui(
                '',
                'YouTube está bloqueando esta solicitud temporalmente '
                '(pasa sobre todo al pedir traducciones seguidas). '
                'Espera unos minutos y vuelve a intentar.',
            )
        except CouldNotRetrieveTranscript as e:
            self._update_ui('', f'No se pudo obtener la transcripción: {e}')
        except Exception as e:
            self._update_ui('', f'Error: {e}')

    def _with_retry(self, func, *args, attempts=2, delay=2, **kwargs):
        """Reintenta una vez ante un bloqueo temporal de YouTube antes de
        propagar el error."""
        last_exc = None
        for i in range(attempts):
            try:
                return func(*args, **kwargs)
            except RequestBlocked as e:
                last_exc = e
                if i + 1 < attempts:
                    time.sleep(delay)
        raise last_exc

    def _get_spanish(self, transcript_list, original):
        """Intenta obtener subtítulos en español ya existentes; si no,
        traduce automáticamente los del idioma original."""
        try:
            es_transcript = transcript_list.find_transcript(['es', 'es-ES', 'es-419'])
            return self._join(self._with_retry(es_transcript.fetch))
        except NoTranscriptFound:
            translated = original.translate('es')
            return self._join(self._with_retry(translated.fetch))

    # ---------- miniatura ----------

    @mainthread
    def _show_thumbnail(self, url):
        img = self.ids.thumbnail_image
        img.source = url
        img.height = 200
        img.opacity = 1
        btn = self.ids.download_thumb_button
        btn.height = 44
        btn.opacity = 1
        btn.disabled = False

    @mainthread
    def _hide_thumbnail(self):
        img = self.ids.thumbnail_image
        img.source = ''
        img.height = 0
        img.opacity = 0
        btn = self.ids.download_thumb_button
        btn.height = 0
        btn.opacity = 0
        btn.disabled = True

    def download_thumbnail(self):
        if not self._thumbnail_url:
            return
        threading.Thread(target=self._download_thumbnail_thread, daemon=True).start()

    def _download_thumbnail_thread(self):
        try:
            resp = requests.get(self._thumbnail_url, timeout=15)
            resp.raise_for_status()
            filename = 'miniatura_youtube.jpg'
            save_dir = self._get_downloads_dir()
            path = os.path.join(save_dir, filename)
            with open(path, 'wb') as f:
                f.write(resp.content)
            self._notify(f'Miniatura guardada en: {path}')
        except Exception as e:
            self._notify(f'No se pudo descargar la miniatura: {e}')

    def _get_downloads_dir(self):
        """Devuelve una carpeta accesible para guardar archivos, tanto en
        Android (sin requerir permisos especiales) como en escritorio."""
        if platform == 'android':
            try:
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                context = PythonActivity.mActivity
                ext_dir = context.getExternalFilesDir(None)
                path = ext_dir.getAbsolutePath()
                os.makedirs(path, exist_ok=True)
                return path
            except Exception:
                return os.getcwd()
        return os.getcwd()

    @mainthread
    def _notify(self, message):
        self.ids.status_label.text = message

    @mainthread
    def _update_ui(self, text, error):
        self._stop_spinner()
        self.ids.output_text.text = text
        self.ids.status_label.color = (0.75, 0.15, 0.15, 1)
        if error:
            self.ids.status_label.text = error
        else:
            self.ids.status_label.text = 'Listo ✓' if text else 'La transcripción está vacía'

    def copy_text(self):
        text = self.ids.output_text.text
        if text:
            Clipboard.copy(text)
            self.ids.status_label.text = 'Texto copiado al portapapeles ✓'


class TranscriptApp(App):
    title = 'Transcripciones YouTube'

    def build(self):
        return RootWidget()


if __name__ == '__main__':
    TranscriptApp().run()
