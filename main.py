# -*- coding: utf-8 -*-
"""
Transcripciones YouTube
Obtiene el texto de la transcripción de un video de YouTube a partir de su
enlace, sin marcas de tiempo. Permite obtenerlo en el idioma original del
video y/o traducido al español.
"""

import re
import threading

from kivy.app import App
from kivy.lang import Builder
from kivy.clock import mainthread
from kivy.core.clipboard import Clipboard
from kivy.uix.boxlayout import BoxLayout

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    CouldNotRetrieveTranscript,
)

KV = '''
<RootWidget>:
    orientation: 'vertical'
    padding: dp(14)
    spacing: dp(10)

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

    # ---------- lógica principal ----------

    def fetch_transcript(self):
        url = self.ids.url_input.text.strip()
        self.ids.status_label.text = ''
        self.ids.output_text.text = ''

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

        self.ids.status_label.text = 'Obteniendo transcripción...'
        threading.Thread(
            target=self._fetch_thread, args=(video_id, mode), daemon=True
        ).start()

    def _fetch_thread(self, video_id, mode):
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            available_codes = [t.language_code for t in transcript_list]
            if not available_codes:
                raise NoTranscriptFound(video_id, [], transcript_list)

            original = transcript_list.find_transcript(available_codes)

            if mode == 'original':
                text = self._join(original.fetch())
                result = text

            elif mode == 'es':
                result = self._get_spanish(transcript_list, original)

            else:  # both
                text_o = self._join(original.fetch())
                text_e = self._get_spanish(transcript_list, original)
                result = (
                    f"--- IDIOMA ORIGINAL ({original.language_code}) ---\n\n"
                    f"{text_o}\n\n"
                    f"--- ESPAÑOL ---\n\n"
                    f"{text_e}"
                )

            self._update_ui(result, '')

        except TranscriptsDisabled:
            self._update_ui('', 'Este video tiene los subtítulos deshabilitados')
        except NoTranscriptFound:
            self._update_ui('', 'No se encontró transcripción para este video')
        except VideoUnavailable:
            self._update_ui('', 'Video no disponible o privado')
        except CouldNotRetrieveTranscript as e:
            self._update_ui('', f'No se pudo obtener la transcripción: {e}')
        except Exception as e:
            self._update_ui('', f'Error: {e}')

    def _get_spanish(self, transcript_list, original):
        """Intenta obtener subtítulos en español ya existentes; si no,
        traduce automáticamente los del idioma original."""
        try:
            es_transcript = transcript_list.find_transcript(['es', 'es-ES', 'es-419'])
            return self._join(es_transcript.fetch())
        except NoTranscriptFound:
            translated = original.translate('es')
            return self._join(translated.fetch())

    @mainthread
    def _update_ui(self, text, error):
        self.ids.output_text.text = text
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
