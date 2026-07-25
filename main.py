# -*- coding: utf-8 -*-
"""
Transcripciones YouTube
Obtiene el texto de la transcripción de uno o varios videos de YouTube a
partir de sus enlaces, sin marcas de tiempo, en el idioma original y/o
traducido al español. Incluye miniatura, caché local y modo por lotes.
"""

import os
import re
import json
import time
import threading

from kivy.app import App
from kivy.lang import Builder
from kivy.factory import Factory
from kivy.clock import mainthread, Clock
from kivy.core.clipboard import Clipboard
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.properties import StringProperty, NumericProperty
from kivy.animation import Animation
from kivy.metrics import dp
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

CACHE_LOCK = threading.Lock()

KV = '''
<LoadingBar>:
    canvas:
        Color:
            rgba: 0.14, 0.16, 0.20, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(5)]
        Color:
            rgba: 0.20, 0.62, 0.88, 1
        RoundedRectangle:
            pos: self.x + self.fill_x * max(self.width - dp(70), 0), self.y
            size: dp(70), self.height
            radius: [dp(5)]

<ResultCard>:
    orientation: 'vertical'
    size_hint_y: None
    height: self.minimum_height
    spacing: dp(6)
    padding: [0, dp(4), 0, dp(14)]

    Label:
        text: root.video_title or '(sin título)'
        size_hint_y: None
        height: self.texture_size[1] + dp(6)
        text_size: self.width, None
        bold: True
        font_size: '15sp'
        color: 1, 1, 1, 1

    AsyncImage:
        source: root.thumbnail_url
        size_hint_y: None
        height: self.width * 9 / 16
        allow_stretch: True
        keep_ratio: True

    Button:
        text: 'Descargar miniatura'
        size_hint_y: None
        height: dp(42)
        on_release: root.download_thumbnail()

    Label:
        text: root.card_status
        size_hint_y: None
        height: dp(20) if root.card_status else 0
        color: 0.75, 0.15, 0.15, 1
        font_size: '13sp'

    TextInput:
        text: root.transcript_text
        readonly: True
        size_hint_y: None
        height: self.minimum_height
        font_size: '14sp'

    Button:
        text: 'Copiar este texto'
        size_hint_y: None
        height: dp(42)
        disabled: not root.transcript_text
        on_release: root.copy_text()

    Widget:
        size_hint_y: None
        height: dp(2)
        canvas:
            Color:
                rgba: 0.32, 0.32, 0.32, 1
            Rectangle:
                pos: self.pos
                size: self.size

<RootWidget>:
    orientation: 'vertical'
    padding: dp(14)
    spacing: dp(8)

    TextInput:
        id: url_input
        hint_text: 'Pega uno o varios enlaces de YouTube (uno por línea, numerados o no)'
        multiline: True
        size_hint_y: None
        height: dp(90)
        font_size: '15sp'

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
        text: 'Obtener transcripción(es)'
        size_hint_y: None
        height: dp(50)
        font_size: '16sp'
        on_release: root.fetch_transcript()

    LoadingBar:
        id: loading_bar
        size_hint_y: None
        height: dp(10)
        opacity: 0

    Label:
        id: status_label
        text: ''
        size_hint_y: None
        height: dp(26)
        color: 0.85, 0.85, 0.85, 1

    ScrollView:
        BoxLayout:
            id: results_container
            orientation: 'vertical'
            size_hint_y: None
            height: self.minimum_height
            spacing: dp(4)
'''

Builder.load_string(KV)


class LoadingBar(Widget):
    fill_x = NumericProperty(0)
    _anim = None

    def start(self):
        self.opacity = 1
        self.fill_x = 0
        self._anim = Animation(fill_x=1, duration=0.9, t='in_out_sine') + \
            Animation(fill_x=0, duration=0.9, t='in_out_sine')
        self._anim.repeat = True
        self._anim.start(self)

    def stop(self):
        if self._anim:
            self._anim.cancel(self)
            self._anim = None
        self.opacity = 0


class ResultCard(BoxLayout):
    video_title = StringProperty('')
    thumbnail_url = StringProperty('')
    transcript_text = StringProperty('')
    card_status = StringProperty('')
    video_id = StringProperty('')

    def download_thumbnail(self):
        if not self.thumbnail_url:
            return
        threading.Thread(target=self._download_thread, daemon=True).start()

    def _download_thread(self):
        try:
            resp = requests.get(self.thumbnail_url, timeout=15)
            resp.raise_for_status()
            filename = f'miniatura_{self.video_id or "youtube"}.jpg'
            save_dir = App.get_running_app().root.get_downloads_dir()
            path = os.path.join(save_dir, filename)
            with open(path, 'wb') as f:
                f.write(resp.content)
            self._set_status(f'Miniatura guardada: {path}')
        except Exception as e:
            self._set_status(f'No se pudo descargar la miniatura: {e}')

    @mainthread
    def _set_status(self, text):
        self.card_status = text

    def copy_text(self):
        if self.transcript_text:
            Clipboard.copy(self.transcript_text)
            self.card_status = 'Texto copiado ✓'


class RootWidget(BoxLayout):

    _spinner_event = None
    _spinner_index = 0
    _cache = {}
    _cache_path = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._load_cache()

    # ---------- caché ----------

    def _get_cache_path(self):
        if not self._cache_path:
            app = App.get_running_app()
            base = app.user_data_dir if app else '.'
            os.makedirs(base, exist_ok=True)
            self._cache_path = os.path.join(base, 'transcript_cache.json')
        return self._cache_path

    def _load_cache(self):
        try:
            path = self._get_cache_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
        except Exception:
            self._cache = {}

    def _save_cache(self):
        try:
            with CACHE_LOCK:
                with open(self._get_cache_path(), 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _cache_key(self, video_id, mode):
        return f'{video_id}:{mode}'

    # ---------- utilidades ----------

    def extract_video_ids(self, text):
        """Extrae los IDs de video de una o varias líneas, con o sin
        numeración ('1.- link', '2) link', o solo el link)."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=)([0-9A-Za-z_-]{11})',
            r'youtu\.be\/([0-9A-Za-z_-]{11})',
            r'youtube\.com\/shorts\/([0-9A-Za-z_-]{11})',
            r'youtube\.com\/embed\/([0-9A-Za-z_-]{11})',
            r'youtube\.com\/live\/([0-9A-Za-z_-]{11})',
        ]
        ids = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            for p in patterns:
                m = re.search(p, line)
                if m:
                    vid = m.group(1)
                    if vid not in ids:
                        ids.append(vid)
                    break
        return ids

    def _join(self, data):
        parts = []
        for item in data:
            t = item['text'] if isinstance(item, dict) else item.text
            t = t.replace('\n', ' ').strip()
            if t:
                parts.append(t)
        return ' '.join(parts)

    def _resolve_thumbnail_url(self, video_id):
        """Prueba varias resoluciones de miniatura, ya que 'maxresdefault'
        no existe para todos los videos (causaba error 404)."""
        candidates = [
            f'https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg',
            f'https://i.ytimg.com/vi/{video_id}/sddefault.jpg',
            f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
        ]
        for url in candidates:
            try:
                r = requests.head(url, timeout=6, allow_redirects=True)
                if r.status_code == 200:
                    return url
            except Exception:
                continue
        return candidates[-1]

    def _fetch_video_info(self, video_id):
        try:
            resp = requests.get(
                'https://www.youtube.com/oembed',
                params={'url': f'https://www.youtube.com/watch?v={video_id}', 'format': 'json'},
                timeout=10,
            )
            title = resp.json().get('title', '') if resp.status_code == 200 else ''
        except Exception:
            title = ''
        return title, self._resolve_thumbnail_url(video_id)

    # ---------- indicador de progreso ----------

    def _start_progress(self):
        self.ids.fetch_button.disabled = True
        self.ids.loading_bar.start()

    def _stop_progress(self):
        self.ids.fetch_button.disabled = False
        self.ids.loading_bar.stop()

    @mainthread
    def _set_status(self, text):
        self.ids.status_label.text = text

    # ---------- lógica principal ----------

    def fetch_transcript(self):
        text = self.ids.url_input.text.strip()
        self.ids.results_container.clear_widgets()
        video_ids = self.extract_video_ids(text)

        if not video_ids:
            self._set_status('No se encontró ningún enlace válido')
            return

        if self.ids.btn_spanish.state == 'down':
            mode = 'es'
        elif self.ids.btn_both.state == 'down':
            mode = 'both'
        else:
            mode = 'original'

        self._start_progress()
        threading.Thread(
            target=self._process_batch, args=(video_ids, mode), daemon=True
        ).start()

    def _process_batch(self, video_ids, mode):
        total = len(video_ids)
        for i, video_id in enumerate(video_ids, start=1):
            self._set_status(f'Procesando video {i} de {total}...')
            self._process_one(video_id, mode)
            if i < total:
                time.sleep(1.5)  # ser gentil con YouTube entre peticiones
        self._stop_progress()
        self._set_status(f'Listo ✓ ({total} video{"s" if total != 1 else ""})')

    def _process_one(self, video_id, mode):
        cache_key = self._cache_key(video_id, mode)
        cached = self._cache.get(cache_key)

        if cached:
            self._add_card(cached['title'], cached['thumbnail'], cached['text'], video_id, '')
            return

        title, thumbnail_url = self._fetch_video_info(video_id)

        try:
            ytt_api = YouTubeTranscriptApi()
            transcript_list = self._with_retry(ytt_api.list, video_id)
            available_codes = [t.language_code for t in transcript_list]
            if not available_codes:
                raise NoTranscriptFound(video_id, [], transcript_list)

            original = transcript_list.find_transcript(available_codes)

            if mode == 'original':
                result = self._join(self._with_retry(original.fetch))
            elif mode == 'es':
                result = self._get_spanish(transcript_list, original)
            else:
                text_o = self._join(self._with_retry(original.fetch))
                text_e = self._get_spanish(transcript_list, original)
                result = (
                    f"--- IDIOMA ORIGINAL ({original.language_code}) ---\n\n"
                    f"{text_o}\n\n--- ESPAÑOL ---\n\n{text_e}"
                )

            self._cache[cache_key] = {
                'title': title, 'thumbnail': thumbnail_url, 'text': result,
            }
            self._save_cache()
            self._add_card(title, thumbnail_url, result, video_id, '')

        except TranscriptsDisabled:
            self._add_card(title, thumbnail_url, '', video_id,
                            'Este video tiene los subtítulos deshabilitados')
        except NoTranscriptFound:
            self._add_card(title, thumbnail_url, '', video_id,
                            'No se encontró transcripción para este video')
        except VideoUnavailable:
            self._add_card(title, thumbnail_url, '', video_id,
                            'Video no disponible o privado')
        except RequestBlocked:
            self._add_card(
                title, thumbnail_url, '', video_id,
                'YouTube bloqueó esta solicitud temporalmente. Intenta de nuevo en unos minutos.',
            )
        except CouldNotRetrieveTranscript as e:
            self._add_card(title, thumbnail_url, '', video_id,
                            f'No se pudo obtener la transcripción: {e}')
        except Exception as e:
            self._add_card(title, thumbnail_url, '', video_id, f'Error: {e}')

    def _with_retry(self, func, *args, attempts=2, delay=2, **kwargs):
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
        """Subtítulos ya en español si existen; si no, traduce. Primero
        intenta la traducción propia de YouTube y, si esta es bloqueada,
        usa Google Translate como alternativa."""
        try:
            es_transcript = transcript_list.find_transcript(['es', 'es-ES', 'es-419'])
            return self._join(self._with_retry(es_transcript.fetch))
        except NoTranscriptFound:
            pass

        original_text = self._join(self._with_retry(original.fetch))
        try:
            translated = original.translate('es')
            return self._join(self._with_retry(translated.fetch))
        except RequestBlocked:
            return self._translate_offline(original_text)

    def _translate_offline(self, text):
        """Alternativa de traducción cuando YouTube bloquea su propio
        endpoint: usa Google Translate a través de deep-translator."""
        from deep_translator import GoogleTranslator
        translator = GoogleTranslator(source='auto', target='es')
        chunks = self._chunk_text(text, 4500)
        parts = []
        for chunk in chunks:
            try:
                parts.append(translator.translate(chunk))
            except Exception:
                parts.append(chunk)
        return ' '.join(parts)

    def _chunk_text(self, text, max_len):
        words = text.split(' ')
        chunks, current, current_len = [], [], 0
        for w in words:
            if current_len + len(w) + 1 > max_len and current:
                chunks.append(' '.join(current))
                current, current_len = [w], len(w)
            else:
                current.append(w)
                current_len += len(w) + 1
        if current:
            chunks.append(' '.join(current))
        return chunks or ['']

    @mainthread
    def _add_card(self, title, thumbnail_url, text, video_id, error):
        card = Factory.ResultCard()
        card.video_title = title
        card.thumbnail_url = thumbnail_url
        card.transcript_text = text
        card.video_id = video_id
        card.card_status = error
        self.ids.results_container.add_widget(card)

    # ---------- almacenamiento ----------

    def get_downloads_dir(self):
        if platform == 'android':
            try:
                from jnius import autoclass
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                context = PythonActivity.mActivity
                ext_dir = context.getExternalFilesDir(None)
                if ext_dir is not None:
                    path = ext_dir.getAbsolutePath()
                    os.makedirs(path, exist_ok=True)
                    return path
            except Exception:
                pass
        return os.getcwd()


class TranscriptApp(App):
    title = 'Transcripciones YouTube'

    def build(self):
        return RootWidget()


if __name__ == '__main__':
    TranscriptApp().run()
