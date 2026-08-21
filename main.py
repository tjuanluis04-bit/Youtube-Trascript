# -*- coding: utf-8 -*-
"""
Transcripciones YouTube
Obtiene el texto de la transcripción de videos de YouTube (por enlace o de
un canal completo), sin marcas de tiempo, en el idioma original o en
español. Incluye miniatura, caché local, exportación por lotes y descarga
en pares imagen+texto organizados por canal.
"""

import os
import re
import json
import time
import base64
import threading

from kivy.app import App
from kivy.lang import Builder
from kivy.factory import Factory
from kivy.clock import mainthread, Clock
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.loader import Loader
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.uix.screenmanager import ScreenManager, Screen, NoTransition
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
BATCH_SIZE = 30
BATCH_PAUSE_SECONDS = 30
PER_VIDEO_DELAY = 1.5

# PNG transparente de 1x1 usado como marcador mientras carga una imagen,
# para reemplazar el ícono giratorio de "cargando" por defecto de Kivy.
_BLANK_PNG_B64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
    '+A8AAQUBAScY42YAAAAASUVORK5CYII='
)

BRACKET_RE = re.compile(r'\[[^\]]*\]')

COLOR_ERROR = (0.85, 0.30, 0.30, 1)
COLOR_SUCCESS = (0.35, 0.75, 0.45, 1)
COLOR_NEUTRAL = (0.75, 0.75, 0.75, 1)


class QuotaExceededError(Exception):
    pass


KV = '''
<StyledButton@Button>:
    background_normal: ''
    background_down: ''
    background_color: 0, 0, 0, 0
    color: 1, 1, 1, 1
    bold: True
    canvas.before:
        Color:
            rgba: (0.20, 0.20, 0.22, 1) if self.state == 'normal' else (0.30, 0.30, 0.33, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]

<SecondaryButton@Button>:
    background_normal: ''
    background_down: ''
    background_color: 0, 0, 0, 0
    color: 0.85, 0.85, 0.85, 1
    font_size: '12sp'
    canvas.before:
        Color:
            rgba: (0.12, 0.12, 0.14, 1) if self.state == 'normal' else (0.22, 0.22, 0.25, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

<CheckToggle@ToggleButton>:
    background_normal: ''
    background_down: ''
    background_color: 0, 0, 0, 0
    color: 1, 1, 1, 1
    font_size: '13sp'
    padding: [dp(40), 0]
    canvas.before:
        Color:
            rgba: (0.14, 0.14, 0.16, 1) if self.state == 'normal' else (0.16, 0.42, 0.30, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: (0.35, 0.35, 0.38, 1) if self.state == 'normal' else (0.30, 0.85, 0.45, 1)
        Ellipse:
            pos: self.x + dp(12), self.center_y - dp(7)
            size: dp(14), dp(14)

<StatusDot@Widget>:
    dot_color: 0.5, 0.5, 0.5, 1
    canvas:
        Color:
            rgba: self.dot_color
        Ellipse:
            pos: self.pos
            size: self.size

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
            pos: self.x, self.y
            size: self.width * self.fill_x, self.height
            radius: [dp(5)]

<ResultCard>:
    orientation: 'vertical'
    size_hint_y: None
    height: self.minimum_height
    spacing: dp(6)
    padding: [0, dp(4), 0, dp(16)]

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

    StyledButton:
        text: 'Descargar miniatura'
        size_hint_y: None
        height: dp(44)
        on_release: root.download_thumbnail()

    Label:
        text: root.card_status
        size_hint_y: None
        height: dp(20) if root.card_status else 0
        color: root.card_status_color
        font_size: '13sp'

    StyledButton:
        id: copy_button_top
        text: 'Copiar este texto'
        size_hint_y: None
        height: dp(44)
        disabled: not root.transcript_text
        on_release: root.copy_text()

    TextInput:
        text: root.transcript_text
        readonly: True
        multiline: True
        use_bubble: False
        use_handles: False
        selection_color: 0, 0, 0, 0
        cursor_color: 0, 0, 0, 0
        size_hint_y: None
        height: self.minimum_height
        font_size: '14sp'

    StyledButton:
        id: copy_button
        text: 'Copiar este texto'
        size_hint_y: None
        height: dp(44)
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

<ChannelRow>:
    orientation: 'horizontal'
    size_hint_y: None
    height: dp(72)
    spacing: dp(8)
    padding: [0, dp(4)]

    AsyncImage:
        source: root.thumbnail_url
        size_hint_x: None
        width: dp(96)
        allow_stretch: True
        keep_ratio: True

    BoxLayout:
        orientation: 'vertical'
        Label:
            text: root.row_title
            text_size: self.width, None
            halign: 'left'
            valign: 'top'
            font_size: '13sp'
            color: 1, 1, 1, 1
            shorten: True
            shorten_from: 'right'
        BoxLayout:
            size_hint_y: None
            height: dp(20)
            spacing: dp(6)
            StatusDot:
                size_hint_x: None
                width: dp(14)
                dot_color: root.status_color
            Label:
                text: root.status_text
                text_size: self.width, None
                halign: 'left'
                font_size: '11sp'
                color: root.status_color

    StyledButton:
        text: 'Guardar'
        size_hint_x: None
        width: dp(78)
        font_size: '11sp'
        disabled: not root.transcript_text
        on_release: root.save_pair()
'''

Builder.load_string(KV)

ROOT_KV = '''
#:import NoTransition kivy.uix.screenmanager.NoTransition
<RootWidget>:
    orientation: 'vertical'
    padding: [dp(14), dp(10)]
    spacing: dp(6)

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        Button:
            id: tab_links_btn
            text: 'Transcribir por enlace(s)'
            font_size: '13sp'
            bold: True
            background_normal: ''
            background_down: ''
            background_color: 0, 0, 0, 0
            color: 1, 1, 1, 1
            canvas.before:
                Color:
                    rgba: (0.20, 0.62, 0.88, 1) if root.ids.screen_manager.current == 'links' else (0.15, 0.15, 0.17, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size
            on_release: root.ids.screen_manager.current = 'links'
        Button:
            id: tab_channel_btn
            text: 'Transcribir canal completo'
            font_size: '13sp'
            bold: True
            background_normal: ''
            background_down: ''
            background_color: 0, 0, 0, 0
            color: 1, 1, 1, 1
            canvas.before:
                Color:
                    rgba: (0.20, 0.62, 0.88, 1) if root.ids.screen_manager.current == 'channel' else (0.15, 0.15, 0.17, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size
            on_release: root.ids.screen_manager.current = 'channel'

    BoxLayout:
        size_hint_y: None
        height: dp(40)
        CheckToggle:
            id: btn_brackets
            text: 'Incluir texto entre [corchetes]'

    LoadingBar:
        id: loading_bar
        size_hint_y: None
        height: dp(10)
        opacity: 0

    Label:
        id: status_label
        text: ''
        size_hint_y: None
        height: dp(24) if self.text else 0
        color: 0.85, 0.85, 0.85, 1
        text_size: self.width, None
        font_size: '13sp'

    ScreenManager:
        id: screen_manager
        transition: NoTransition()

        Screen:
            name: 'links'
            BoxLayout:
                orientation: 'vertical'
                spacing: dp(6)

                BoxLayout:
                    size_hint_y: None
                    height: dp(90)
                    spacing: dp(6)
                    TextInput:
                        id: url_input
                        hint_text: 'Pega uno o varios enlaces de YouTube (uno por línea, numerados o no)'
                        multiline: True
                        font_size: '15sp'
                    SecondaryButton:
                        text: 'Pegar'
                        size_hint_x: None
                        width: dp(58)
                        on_release: root.paste_link()

                BoxLayout:
                    size_hint_y: None
                    height: dp(44)
                    spacing: dp(8)
                    Label:
                        text: 'Idioma:'
                        size_hint_x: None
                        width: dp(60)
                        color: 0.85, 0.85, 0.85, 1
                    Spinner:
                        id: lang_spinner_links
                        text: 'Original'
                        values: ['Original', 'Español']
                        background_normal: ''
                        background_color: 0.20, 0.20, 0.22, 1
                        color: 1, 1, 1, 1

                StyledButton:
                    id: fetch_button
                    text: 'Obtener transcripción(es)'
                    size_hint_y: None
                    height: dp(52)
                    font_size: '16sp'
                    on_release: root.fetch_transcript()

                ScrollView:
                    BoxLayout:
                        id: results_container_links
                        orientation: 'vertical'
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: dp(4)

                BoxLayout:
                    size_hint_y: None
                    height: dp(38)
                    spacing: dp(6)
                    SecondaryButton:
                        id: export_button_links
                        text: 'Exportar lote a .txt'
                        disabled: not root.ids.results_container_links.children
                        on_release: root.export_batch('links')
                    SecondaryButton:
                        text: 'Vaciar caché'
                        on_release: root.clear_cache()

        Screen:
            name: 'channel'
            BoxLayout:
                orientation: 'vertical'
                spacing: dp(6)

                ScrollView:
                    do_scroll_x: False
                    BoxLayout:
                        orientation: 'vertical'
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: dp(6)

                        TextInput:
                            id: api_key_input
                            hint_text: 'Clave de API de YouTube (se guarda en tu teléfono)'
                            multiline: False
                            password: True
                            size_hint_y: None
                            height: dp(42)
                            font_size: '13sp'

                        TextInput:
                            id: channel_input
                            hint_text: 'Enlace del canal o @usuario (ej. @psychacks)'
                            multiline: False
                            size_hint_y: None
                            height: dp(42)
                            font_size: '13sp'

                        BoxLayout:
                            size_hint_y: None
                            height: dp(44)
                            spacing: dp(8)
                            Label:
                                text: 'Fuente:'
                                size_hint_x: None
                                width: dp(60)
                                color: 0.85, 0.85, 0.85, 1
                            Spinner:
                                id: source_spinner
                                text: 'Videos'
                                values: ['Videos', 'En vivo', 'Lista de reproducción']
                                background_normal: ''
                                background_color: 0.20, 0.20, 0.22, 1
                                color: 1, 1, 1, 1

                        TextInput:
                            id: playlist_input
                            hint_text: 'Enlace o ID de la lista de reproducción'
                            multiline: False
                            size_hint_y: None
                            height: dp(42) if root.ids.source_spinner.text == 'Lista de reproducción' else 0
                            opacity: 1 if root.ids.source_spinner.text == 'Lista de reproducción' else 0
                            font_size: '13sp'

                        BoxLayout:
                            size_hint_y: None
                            height: dp(44)
                            spacing: dp(8)
                            Label:
                                text: 'Idioma:'
                                size_hint_x: None
                                width: dp(60)
                                color: 0.85, 0.85, 0.85, 1
                            Spinner:
                                id: lang_spinner_channel
                                text: 'Original'
                                values: ['Original', 'Español']
                                background_normal: ''
                                background_color: 0.20, 0.20, 0.22, 1
                                color: 1, 1, 1, 1

                        StyledButton:
                            text: 'Analizar canal'
                            size_hint_y: None
                            height: dp(48)
                            font_size: '14sp'
                            on_release: root.analyze_channel()

                        Label:
                            id: channel_info_label
                            text: ''
                            size_hint_y: None
                            height: dp(22) if self.text else 0
                            font_size: '12sp'
                            color: 0.8, 0.8, 0.8, 1
                            text_size: self.width, None

                        StyledButton:
                            id: channel_action_button
                            text: ''
                            size_hint_y: None
                            height: dp(48) if self.text else 0
                            opacity: 1 if self.text else 0
                            font_size: '14sp'
                            disabled: not self.text
                            on_release: root.start_channel_transcription()

                        SecondaryButton:
                            text: 'Descargar completados (imagen + texto)'
                            size_hint_y: None
                            height: dp(38) if root.ids.results_container_channel.children else 0
                            opacity: 1 if root.ids.results_container_channel.children else 0
                            disabled: not root.ids.results_container_channel.children
                            on_release: root.download_all_channel_pairs()

                        BoxLayout:
                            size_hint_y: None
                            height: dp(38) if root.channel_page_count > 1 else 0
                            opacity: 1 if root.channel_page_count > 1 else 0
                            spacing: dp(6)
                            SecondaryButton:
                                text: '< Anterior'
                                disabled: root.channel_page <= 1
                                on_release: root.channel_prev_page()
                            Label:
                                text: f'Pagina {root.channel_page} de {root.channel_page_count}'
                                color: 0.85, 0.85, 0.85, 1
                                font_size: '12sp'
                            SecondaryButton:
                                text: 'Siguiente >'
                                disabled: root.channel_page >= root.channel_page_count
                                on_release: root.channel_next_page()

                        BoxLayout:
                            id: results_container_channel
                            orientation: 'vertical'
                            size_hint_y: None
                            height: self.minimum_height
                            spacing: dp(4)

                BoxLayout:
                    size_hint_y: None
                    height: dp(38)
                    spacing: dp(6)
                    SecondaryButton:
                        id: export_button_channel
                        text: 'Exportar lote a .txt'
                        disabled: not root.ids.results_container_channel.children
                        on_release: root.export_batch('channel')
                    SecondaryButton:
                        text: 'Vaciar caché'
                        on_release: root.clear_cache()
'''

Builder.load_string(ROOT_KV)


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

    def set_progress(self, fraction):
        if self._anim:
            self._anim.cancel(self)
            self._anim = None
        self.opacity = 1
        self.fill_x = max(0.0, min(1.0, fraction))

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
    card_status_color = list(COLOR_ERROR)
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
            saved_where = App.get_running_app().root.save_image_public(
                resp.content, filename
            )
            self._set_status(f'Miniatura guardada en {saved_where}', success=True)
        except Exception as e:
            self._set_status(f'No se pudo descargar la miniatura: {e}', success=False)

    @mainthread
    def _set_status(self, text, success):
        self.card_status_color = list(COLOR_SUCCESS if success else COLOR_ERROR)
        self.card_status = text

    def copy_text(self):
        if not self.transcript_text:
            return
        Clipboard.copy(self.transcript_text)
        self._set_status('Texto copiado', success=True)
        for btn_id in ('copy_button_top', 'copy_button'):
            btn = self.ids.get(btn_id)
            if btn:
                original = btn.text
                btn.text = 'Copiado'
                Clock.schedule_once(lambda dt, b=btn, t=original: setattr(b, 'text', t), 1.4)


class ChannelRow(BoxLayout):
    row_title = StringProperty('')
    thumbnail_url = StringProperty('')
    transcript_text = StringProperty('')
    video_title = StringProperty('')
    video_id = StringProperty('')
    status_text = StringProperty('')
    status_color = list(COLOR_NEUTRAL)
    index = NumericProperty(0)

    def save_pair(self):
        threading.Thread(target=self._save_pair_thread, daemon=True).start()

    def _save_pair_thread(self):
        try:
            img_bytes = None
            if self.thumbnail_url:
                try:
                    img_bytes = requests.get(self.thumbnail_url, timeout=15).content
                except Exception:
                    img_bytes = None
            App.get_running_app().root.save_channel_pair(
                img_bytes, self.transcript_text.encode('utf-8'),
                self.index, self.video_title or self.row_title, self.video_id,
            )
            self._flash_status_ok('Guardado')
        except Exception:
            self._flash_status_ok('Error al guardar')

    @mainthread
    def _flash_status_ok(self, msg):
        App.get_running_app().root._set_status(msg)


class RootWidget(BoxLayout):

    channel_page = NumericProperty(1)
    channel_page_count = NumericProperty(0)

    _cache = {}
    _cache_path = None
    _channel_state = {}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._channel_state = {}
        self._load_cache()
        settings = self._load_settings()
        if settings.get('api_key'):
            self.ids.api_key_input.text = settings['api_key']

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

    def clear_cache(self):
        self._cache = {}
        self._save_cache()
        self._set_status('Caché vaciado (los próximos videos se volverán a obtener)')

    def _cache_key(self, video_id, mode, keep_brackets):
        return f'{video_id}:{mode}:{"b1" if keep_brackets else "b0"}'

    # ---------- configuración (clave de API) ----------

    def _get_settings_path(self):
        app = App.get_running_app()
        base = app.user_data_dir if app else '.'
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, 'settings.json')

    def _load_settings(self):
        try:
            path = self._get_settings_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_settings(self, api_key):
        try:
            with open(self._get_settings_path(), 'w', encoding='utf-8') as f:
                json.dump({'api_key': api_key}, f)
        except Exception:
            pass

    # ---------- estado de reanudación del canal ----------

    def _get_channel_state_path(self):
        app = App.get_running_app()
        base = app.user_data_dir if app else '.'
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, 'channel_resume.json')

    def _save_channel_resume_state(self):
        try:
            state = self._channel_state
            data = {
                'channel_id': state.get('channel_id'),
                'channel_title': state.get('channel_title'),
                'video_ids': state.get('video_ids'),
                'processed_index': state.get('processed_index', 0),
                'source': state.get('source', 'videos'),
            }
            with open(self._get_channel_state_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load_channel_resume_state(self):
        try:
            path = self._get_channel_state_path()
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    # ---------- utilidades ----------

    def paste_link(self):
        pasted = Clipboard.paste()
        if not pasted:
            return
        current = self.ids.url_input.text
        if current and not current.endswith('\n'):
            current += '\n'
        self.ids.url_input.text = current + pasted.strip()

    def extract_video_ids(self, text):
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

    def _join(self, data, keep_brackets):
        parts = []
        for item in data:
            t = item['text'] if isinstance(item, dict) else item.text
            t = t.replace('\n', ' ').strip()
            if t:
                parts.append(t)
        text = ' '.join(parts)
        if not keep_brackets:
            text = BRACKET_RE.sub('', text)
            text = re.sub(r'\s{2,}', ' ', text).strip()
        return text

    def _resolve_thumbnail_url(self, video_id):
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

    @mainthread
    def _start_progress(self):
        self.ids.loading_bar.start()

    @mainthread
    def _stop_progress(self):
        self.ids.loading_bar.stop()

    @mainthread
    def _set_progress_fraction(self, fraction):
        self.ids.loading_bar.set_progress(fraction)

    @mainthread
    def _set_status(self, text):
        self.ids.status_label.text = text

    # ---------- transcribir por enlace(s) ----------

    def fetch_transcript(self):
        text = self.ids.url_input.text.strip()
        self.ids.results_container_links.clear_widgets()
        video_ids = self.extract_video_ids(text)

        if not video_ids:
            self._set_status('No se encontró ningún enlace válido')
            return

        mode = 'es' if self.ids.lang_spinner_links.text == 'Español' else 'original'
        keep_brackets = self.ids.btn_brackets.state == 'down'

        self.ids.fetch_button.disabled = True
        self._start_progress()
        threading.Thread(
            target=self._process_batch, args=(video_ids, mode, keep_brackets), daemon=True
        ).start()

    def _process_batch(self, video_ids, mode, keep_brackets):
        total = len(video_ids)
        try:
            for i, video_id in enumerate(video_ids, start=1):
                self._set_status(f'Procesando video {i} de {total}...')
                self._process_one(video_id, mode, keep_brackets)
                if i < total:
                    time.sleep(PER_VIDEO_DELAY)
        finally:
            self._stop_progress()
            self._set_fetch_button_enabled()
            self._set_status(f'Listo ({total} video{"s" if total != 1 else ""})')

    @mainthread
    def _set_fetch_button_enabled(self):
        self.ids.fetch_button.disabled = False

    def _process_one(self, video_id, mode, keep_brackets):
        cache_key = self._cache_key(video_id, mode, keep_brackets)
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
                body = self._join(self._with_retry(original.fetch), keep_brackets)
                result = f"{title}\n\n{body}" if title else body
            else:
                body = self._get_spanish(transcript_list, original, keep_brackets)
                title_es = self._translate_title(title) if title else title
                result = f"{title_es}\n\n{body}" if title_es else body

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

    def _get_spanish(self, transcript_list, original, keep_brackets):
        try:
            es_transcript = transcript_list.find_transcript(['es', 'es-ES', 'es-419'])
            return self._join(self._with_retry(es_transcript.fetch), keep_brackets)
        except NoTranscriptFound:
            pass

        original_text = self._join(self._with_retry(original.fetch), keep_brackets)
        try:
            translated = original.translate('es')
            return self._join(self._with_retry(translated.fetch), keep_brackets)
        except RequestBlocked:
            return self._translate_offline(original_text)

    def _translate_title(self, title):
        if not title:
            return title
        try:
            from deep_translator import GoogleTranslator
            return GoogleTranslator(source='auto', target='es').translate(title)
        except Exception:
            return title

    def _translate_offline(self, text):
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
        if error:
            card.card_status_color = list(COLOR_ERROR)
        card.card_status = error
        self.ids.results_container_links.add_widget(card)

    # ---------- analizar canal / lista de reproducción ----------

    def analyze_channel(self):
        api_key = self.ids.api_key_input.text.strip()
        channel_input = self.ids.channel_input.text.strip()
        source = self.ids.source_spinner.text
        playlist_raw = self.ids.playlist_input.text.strip()

        if not api_key:
            self._set_status('Falta la clave de API de YouTube')
            return
        if source == 'Lista de reproducción':
            if not playlist_raw:
                self._set_status('Falta el enlace o ID de la lista de reproducción')
                return
        elif not channel_input:
            self._set_status('Falta el enlace o @usuario del canal')
            return

        self._save_settings(api_key)
        self._set_status('Analizando (puede tardar según el tamaño)...')
        self._set_channel_info('')
        self._set_channel_action('')
        self._start_progress()
        threading.Thread(
            target=self._analyze_channel_thread,
            args=(channel_input, api_key, source, playlist_raw), daemon=True,
        ).start()

    def _analyze_channel_thread(self, channel_input, api_key, source, playlist_raw):
        try:
            folder_tag = None
            if source == 'Lista de reproducción':
                playlist_id = self._extract_playlist_id(playlist_raw)
                playlist_title, channel_id = self._get_playlist_info(playlist_id, api_key)
                video_ids = self._fetch_all_playlist_video_ids(playlist_id, api_key)
                channel_title = playlist_title
                safe_pl = re.sub(r'[\\/*?:"<>|]', '_', playlist_title)[:60]
                folder_tag = f'Listas/{safe_pl}'
            else:
                channel_id = self._resolve_channel_id(channel_input, api_key)
                channel_title = self._get_channel_title(channel_id, api_key)
                uploads_id = self._get_uploads_playlist_id(channel_id, api_key)
                only_live = source == 'En vivo'
                video_ids = self._fetch_all_channel_video_ids(uploads_id, api_key, only_live)
                if only_live:
                    folder_tag = 'En vivo'
        except QuotaExceededError:
            self._stop_progress()
            self._set_status(
                'Se agotó la cuota diaria de la API de YouTube. El progreso ya guardado '
                'se reanuda cuando vuelvas a analizar el mismo canal.'
            )
            return
        except Exception as e:
            self._stop_progress()
            self._set_status(f'No se pudo analizar: {e}')
            return

        self._stop_progress()
        if not video_ids:
            self._set_status('No se encontraron videos con esos criterios')
            return

        saved = self._load_channel_resume_state()
        processed_index = 0
        if saved and saved.get('channel_id') == channel_id and saved.get('video_ids') == video_ids:
            processed_index = saved.get('processed_index', 0)

        self._channel_state = {
            'channel_id': channel_id,
            'channel_title': channel_title,
            'video_ids': video_ids,
            'processed_index': processed_index,
            'results': [None] * len(video_ids),
            'folder_tag': folder_tag,
        }
        self._save_channel_resume_state()

        total = len(video_ids)
        self._set_channel_page(1, max(1, (total + BATCH_SIZE - 1) // BATCH_SIZE))
        self._render_channel_page(1)

        info = f'{channel_title} - {total} video{"s" if total != 1 else ""}'
        if processed_index:
            info += f' - {processed_index} ya procesados anteriormente'
        self._set_channel_info(info)

        if processed_index >= total:
            label = 'Volver a transcribir todos'
        elif processed_index:
            label = f'Reanudar ({total - processed_index} restantes)'
        else:
            label = f'Transcribir los {total} videos'
        self._set_channel_action(label)

    @mainthread
    def _set_channel_info(self, text):
        self.ids.channel_info_label.text = text

    @mainthread
    def _set_channel_action(self, text):
        self.ids.channel_action_button.text = text

    @mainthread
    def _set_channel_action_disabled(self, disabled):
        self.ids.channel_action_button.disabled = disabled

    @mainthread
    def _set_channel_page(self, page, page_count):
        self.channel_page = page
        self.channel_page_count = page_count

    def _raise_if_quota_error(self, data):
        err = data.get('error', {}) if isinstance(data, dict) else {}
        for e in err.get('errors', []):
            if e.get('reason') in ('quotaExceeded', 'dailyLimitExceeded'):
                raise QuotaExceededError()

    def _extract_playlist_id(self, raw):
        raw = raw.strip()
        m = re.search(r'[?&]list=([0-9A-Za-z_-]+)', raw)
        if m:
            return m.group(1)
        return raw

    def _get_playlist_info(self, playlist_id, api_key):
        resp = requests.get(
            'https://www.googleapis.com/youtube/v3/playlists',
            params={'part': 'snippet', 'id': playlist_id, 'key': api_key},
            timeout=15,
        )
        data = resp.json()
        self._raise_if_quota_error(data)
        items = data.get('items', [])
        if not items:
            raise Exception('Lista de reproducción no encontrada')
        sn = items[0]['snippet']
        return sn.get('title', playlist_id), sn.get('channelId', 'canal')

    def _fetch_all_playlist_video_ids(self, playlist_id, api_key):
        result_ids = []
        page_token = None
        pages_scanned = 0
        max_pages = 40
        while pages_scanned < max_pages:
            params = {
                'part': 'contentDetails', 'playlistId': playlist_id,
                'maxResults': 50, 'key': api_key,
            }
            if page_token:
                params['pageToken'] = page_token
            resp = requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params=params, timeout=15,
            )
            data = resp.json()
            self._raise_if_quota_error(data)
            items = data.get('items', [])
            pages_scanned += 1
            if not items:
                break
            for it in items:
                vid = it.get('contentDetails', {}).get('videoId')
                if vid:
                    result_ids.append(vid)
            page_token = data.get('nextPageToken')
            if not page_token:
                break
        return result_ids

    def _resolve_channel_id(self, channel_input, api_key):
        channel_input = channel_input.strip()

        m = re.search(r'channel/(UC[0-9A-Za-z_-]{22})', channel_input)
        if m:
            return m.group(1)

        m = re.search(r'@([0-9A-Za-z_.-]+)', channel_input)
        handle = m.group(1) if m else channel_input.lstrip('@').strip()

        if handle and ' ' not in handle:
            resp = requests.get(
                'https://www.googleapis.com/youtube/v3/channels',
                params={'part': 'id', 'forHandle': handle, 'key': api_key},
                timeout=15,
            )
            data = resp.json()
            self._raise_if_quota_error(data)
            items = data.get('items', [])
            if items:
                return items[0]['id']

            resp2 = requests.get(
                'https://www.googleapis.com/youtube/v3/channels',
                params={'part': 'id', 'forUsername': handle, 'key': api_key},
                timeout=15,
            )
            data2 = resp2.json()
            self._raise_if_quota_error(data2)
            items2 = data2.get('items', [])
            if items2:
                return items2[0]['id']

        resp3 = requests.get(
            'https://www.googleapis.com/youtube/v3/search',
            params={
                'part': 'snippet', 'type': 'channel', 'q': channel_input,
                'key': api_key, 'maxResults': 1,
            },
            timeout=15,
        )
        data3 = resp3.json()
        self._raise_if_quota_error(data3)
        items3 = data3.get('items', [])
        if items3:
            return items3[0]['snippet']['channelId']

        raise Exception('No se pudo identificar el canal (revisa el enlace y la clave de API)')

    def _get_channel_title(self, channel_id, api_key):
        resp = requests.get(
            'https://www.googleapis.com/youtube/v3/channels',
            params={'part': 'snippet', 'id': channel_id, 'key': api_key},
            timeout=15,
        )
        data = resp.json()
        self._raise_if_quota_error(data)
        items = data.get('items', [])
        return items[0]['snippet']['title'] if items else channel_id

    def _get_uploads_playlist_id(self, channel_id, api_key):
        resp = requests.get(
            'https://www.googleapis.com/youtube/v3/channels',
            params={'part': 'contentDetails', 'id': channel_id, 'key': api_key},
            timeout=15,
        )
        data = resp.json()
        self._raise_if_quota_error(data)
        items = data.get('items', [])
        if not items:
            raise Exception('Canal no encontrado')
        return items[0]['contentDetails']['relatedPlaylists']['uploads']

    def _parse_iso8601_duration(self, s):
        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', s or '')
        if not m:
            return 0
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + int(se or 0)

    def _fetch_all_channel_video_ids(self, uploads_id, api_key, only_live=False):
        """Recorre TODA la lista de subidas del canal.
        only_live=False (fuente 'Videos'): excluye Shorts (<=60s) y en vivo.
        only_live=True  (fuente 'En vivo'): conserva solo las transmisiones en vivo.
        Tope de seguridad: 2000 videos revisados."""
        result_ids = []
        page_token = None
        pages_scanned = 0
        max_pages = 40

        while pages_scanned < max_pages:
            params = {
                'part': 'contentDetails', 'playlistId': uploads_id,
                'maxResults': 50, 'key': api_key,
            }
            if page_token:
                params['pageToken'] = page_token

            resp = requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params=params, timeout=15,
            )
            data = resp.json()
            self._raise_if_quota_error(data)
            items = data.get('items', [])
            pages_scanned += 1
            if not items:
                break

            ids = [it['contentDetails']['videoId'] for it in items]

            details_resp = requests.get(
                'https://www.googleapis.com/youtube/v3/videos',
                params={
                    'part': 'contentDetails,liveStreamingDetails',
                    'id': ','.join(ids), 'key': api_key,
                },
                timeout=15,
            )
            details_data = details_resp.json()
            self._raise_if_quota_error(details_data)
            details = {d['id']: d for d in details_data.get('items', [])}

            for vid in ids:
                d = details.get(vid)
                if not d:
                    continue
                is_live = 'liveStreamingDetails' in d
                if only_live:
                    if not is_live:
                        continue
                    result_ids.append(vid)
                else:
                    if is_live:
                        continue
                    duration = self._parse_iso8601_duration(
                        d.get('contentDetails', {}).get('duration', '')
                    )
                    if duration <= 60:
                        continue
                    result_ids.append(vid)

            page_token = data.get('nextPageToken')
            if not page_token:
                break

        return result_ids

    # ---------- transcribir el canal en lotes de 30 ----------

    def start_channel_transcription(self):
        if not self._channel_state.get('video_ids'):
            return
        self._set_channel_action_disabled(True)
        threading.Thread(target=self._channel_transcription_thread, daemon=True).start()

    def _channel_transcription_thread(self):
        state = self._channel_state
        video_ids = state['video_ids']
        total = len(video_ids)
        if not state.get('results') or len(state['results']) != total:
            state['results'] = [None] * total

        mode = 'es' if self.ids.lang_spinner_channel.text == 'Español' else 'original'
        keep_brackets = self.ids.btn_brackets.state == 'down'
        start_index = state.get('processed_index', 0)
        total_batches = max(1, (total + BATCH_SIZE - 1) // BATCH_SIZE)

        for batch_start in range(start_index, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            batch_num = batch_start // BATCH_SIZE + 1

            for i in range(batch_start, batch_end):
                pct = int((i / total) * 100) if total else 0
                self._set_status(
                    f'Lote {batch_num} de {total_batches} - Video {i + 1} de {total} ({pct}%)...'
                )
                self._set_progress_fraction(i / total if total else 0)
                self._process_channel_video(video_ids[i], i, mode, keep_brackets)
                state['processed_index'] = i + 1
                self._save_channel_resume_state()
                if i < batch_end - 1:
                    time.sleep(PER_VIDEO_DELAY)

            if batch_end < total:
                for remaining in range(BATCH_PAUSE_SECONDS, 0, -1):
                    self._set_status(
                        f'Lote {batch_num} de {total_batches} completado. '
                        f'Pausa de {remaining}s antes del siguiente lote...'
                    )
                    time.sleep(1)

        self._set_progress_fraction(0)
        self._stop_progress()
        self._set_channel_action_disabled(False)
        self._set_channel_action('Volver a transcribir todos')
        self._set_status(f'Canal completo: {total} videos procesados')

    def _process_channel_video(self, video_id, index, mode, keep_brackets):
        cache_key = self._cache_key(video_id, mode, keep_brackets)
        cached = self._cache.get(cache_key)

        if cached:
            title, thumbnail_url, text, error = (
                cached['title'], cached['thumbnail'], cached['text'], ''
            )
        else:
            title, thumbnail_url = self._fetch_video_info(video_id)
            text, error = '', ''
            try:
                ytt_api = YouTubeTranscriptApi()
                transcript_list = self._with_retry(ytt_api.list, video_id)
                available_codes = [t.language_code for t in transcript_list]
                if not available_codes:
                    raise NoTranscriptFound(video_id, [], transcript_list)
                original = transcript_list.find_transcript(available_codes)

                if mode == 'original':
                    body = self._join(self._with_retry(original.fetch), keep_brackets)
                    text = f"{title}\n\n{body}" if title else body
                else:
                    body = self._get_spanish(transcript_list, original, keep_brackets)
                    title_es = self._translate_title(title) if title else title
                    text = f"{title_es}\n\n{body}" if title_es else body

                self._cache[cache_key] = {
                    'title': title, 'thumbnail': thumbnail_url, 'text': text,
                }
                self._save_cache()
            except TranscriptsDisabled:
                error = 'Subtítulos deshabilitados'
            except NoTranscriptFound:
                error = 'Sin transcripción disponible'
            except VideoUnavailable:
                error = 'Video no disponible o privado'
            except RequestBlocked:
                error = 'YouTube bloqueó la solicitud temporalmente'
            except CouldNotRetrieveTranscript as e:
                error = f'Error: {e}'
            except Exception as e:
                error = f'Error: {e}'

        entry = {
            'index': index + 1, 'video_id': video_id, 'title': title,
            'thumbnail': thumbnail_url, 'text': text, 'error': error,
        }
        self._channel_state['results'][index] = entry
        self._maybe_refresh_page_for_index(index)
        return entry

    @mainthread
    def _maybe_refresh_page_for_index(self, index):
        results = self._channel_state.get('results', [])
        total = len(results)
        page_count = max(1, (total + BATCH_SIZE - 1) // BATCH_SIZE)
        self.channel_page_count = page_count
        page = index // BATCH_SIZE + 1
        if page == self.channel_page:
            self._render_channel_page(self.channel_page)

    def _render_channel_page(self, page):
        results = self._channel_state.get('results', [])
        total = len(results)
        start = (page - 1) * BATCH_SIZE
        end = min(start + BATCH_SIZE, total)
        self.ids.results_container_channel.clear_widgets()
        for i in range(start, end):
            entry = results[i]
            row = Factory.ChannelRow()
            if entry:
                row.index = entry['index']
                row.row_title = f"{entry['index']:03d} - {entry['title'] or '(sin título)'}"
                row.thumbnail_url = entry['thumbnail']
                row.transcript_text = entry['text']
                row.video_id = entry['video_id']
                row.video_title = entry['title']
                if entry['error']:
                    row.status_text = entry['error']
                    row.status_color = list(COLOR_ERROR)
                elif entry['text']:
                    row.status_text = 'Transcrito con éxito'
                    row.status_color = list(COLOR_SUCCESS)
                else:
                    row.status_text = 'Procesando...'
                    row.status_color = list(COLOR_NEUTRAL)
            else:
                row.index = i + 1
                row.row_title = f'{i + 1:03d} - (en cola)'
                row.status_text = 'En cola'
                row.status_color = list(COLOR_NEUTRAL)
            self.ids.results_container_channel.add_widget(row)

    def channel_next_page(self):
        if self.channel_page < self.channel_page_count:
            self.channel_page += 1
            self._render_channel_page(self.channel_page)

    def channel_prev_page(self):
        if self.channel_page > 1:
            self.channel_page -= 1
            self._render_channel_page(self.channel_page)

    def download_all_channel_pairs(self):
        threading.Thread(target=self._download_all_channel_pairs_thread, daemon=True).start()

    def _download_all_channel_pairs_thread(self):
        results = self._channel_state.get('results', [])
        count, errors = 0, 0
        for entry in results:
            if not entry or not entry.get('text'):
                continue
            try:
                img_bytes = None
                if entry.get('thumbnail'):
                    try:
                        img_bytes = requests.get(entry['thumbnail'], timeout=15).content
                    except Exception:
                        img_bytes = None
                self.save_channel_pair(
                    img_bytes, entry['text'].encode('utf-8'),
                    entry['index'], entry['title'], entry['video_id'],
                )
                count += 1
            except Exception:
                errors += 1
        msg = f'{count} video(s) descargados (imagen+texto)'
        if errors:
            msg += f' ({errors} fallaron)'
        self._set_status(msg)

    # ---------- exportar lote (barra inferior) ----------

    def export_batch(self, which):
        container_id = 'results_container_links' if which == 'links' else 'results_container_channel'
        cards = list(self.ids[container_id].children)
        if not cards:
            return
        threading.Thread(target=self._export_thread, args=(cards, which), daemon=True).start()

    def _export_thread(self, cards, which):
        is_channel = which == 'channel' and bool(self._channel_state.get('channel_id'))
        count, errors = 0, 0
        for card in reversed(cards):
            text = getattr(card, 'transcript_text', '')
            if not text:
                continue
            try:
                if is_channel:
                    idx = getattr(card, 'index', 0) or 0
                    title = getattr(card, 'video_title', '') or getattr(card, 'row_title', '')
                    vid = getattr(card, 'video_id', '')
                    self.save_channel_pair(None, text.encode('utf-8'), idx, title, vid)
                else:
                    safe_name = re.sub(
                        r'[\\/*?:"<>|]', '_',
                        getattr(card, 'video_title', '') or getattr(card, 'video_id', '') or 'transcripcion',
                    )
                    safe_name = safe_name.strip()[:80] or 'transcripcion'
                    self.save_text_public(text.encode('utf-8'), f'{safe_name}.txt')
                count += 1
            except Exception:
                errors += 1
        msg = f'{count} archivo(s) exportado(s)'
        if errors:
            msg += f' ({errors} fallaron)'
        self._set_status(msg)

    # ---------- almacenamiento ----------

    def save_image_public(self, data, filename):
        if platform == 'android':
            try:
                return self._save_to_media_store_images(data, filename)
            except Exception:
                pass
            try:
                return self._save_to_app_storage(data, filename)
            except Exception as e:
                raise e
        path = os.path.join(os.getcwd(), filename)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def save_text_public(self, data, filename):
        if platform == 'android':
            try:
                return self._save_to_media_store_downloads(data, filename)
            except Exception:
                pass
            try:
                return self._save_to_app_storage(data, filename)
            except Exception as e:
                raise e
        path = os.path.join(os.getcwd(), 'Videos', filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def _channel_relative_path(self, channel_id, lang_folder):
        """Construye Canales/<id>/[En vivo|Listas/<nombre>/]<Idioma X>."""
        state = self._channel_state
        tag = state.get('folder_tag')
        if tag:
            return f'Canales/{channel_id}/{tag}/{lang_folder}'
        return f'Canales/{channel_id}/{lang_folder}'

    def save_channel_pair(self, image_bytes, text_bytes, index, title, video_id):
        state = self._channel_state
        channel_id = state.get('channel_id', 'canal')
        lang_folder = 'Idioma Español' if self.ids.lang_spinner_channel.text == 'Español' else 'Idioma Original'
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', title or video_id or 'video').strip()[:80]
        base_name = f'{index:03d} - {safe_title}'
        rel_path = self._channel_relative_path(channel_id, lang_folder)

        if platform == 'android':
            try:
                self._save_channel_pair_media_store(image_bytes, text_bytes, rel_path, base_name)
                return rel_path
            except Exception:
                pass
            base = self._app_storage_dir()
            folder = os.path.join(base, *rel_path.split('/'))
            os.makedirs(folder, exist_ok=True)
            if image_bytes:
                with open(os.path.join(folder, base_name + '.jpg'), 'wb') as f:
                    f.write(image_bytes)
            with open(os.path.join(folder, base_name + '.txt'), 'wb') as f:
                f.write(text_bytes)
            return folder

        folder = os.path.join(os.getcwd(), *rel_path.split('/'))
        os.makedirs(folder, exist_ok=True)
        if image_bytes:
            with open(os.path.join(folder, base_name + '.jpg'), 'wb') as f:
                f.write(image_bytes)
        with open(os.path.join(folder, base_name + '.txt'), 'wb') as f:
            f.write(text_bytes)
        return folder

    def _app_storage_dir(self):
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = PythonActivity.mActivity
        ext_dir = context.getExternalFilesDir(None)
        return ext_dir.getAbsolutePath() if ext_dir is not None else os.getcwd()

    def _request_legacy_storage_permission(self, VERSION_SDK_INT):
        try:
            from android.permissions import request_permissions, Permission, check_permission
            if VERSION_SDK_INT < 29:
                if not check_permission(Permission.WRITE_EXTERNAL_STORAGE):
                    request_permissions([Permission.WRITE_EXTERNAL_STORAGE])
                    time.sleep(1.0)
        except Exception:
            pass

    def _save_to_media_store_images(self, data, filename):
        from jnius import autoclass

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = PythonActivity.mActivity
        VERSION = autoclass('android.os.Build$VERSION')
        self._request_legacy_storage_permission(VERSION.SDK_INT)

        ContentValues = autoclass('android.content.ContentValues')
        MediaColumns = autoclass('android.provider.MediaStore$MediaColumns')
        MediaImages = autoclass('android.provider.MediaStore$Images$Media')

        values = ContentValues()
        values.put(MediaColumns.DISPLAY_NAME, filename)
        values.put(MediaColumns.MIME_TYPE, 'image/jpeg')
        if VERSION.SDK_INT >= 29:
            values.put(MediaColumns.RELATIVE_PATH, 'Pictures/TranscripcionesYoutube')

        resolver = context.getContentResolver()
        uri = resolver.insert(MediaImages.EXTERNAL_CONTENT_URI, values)
        if uri is None:
            raise Exception('No se pudo crear el archivo en la galería')

        out_stream = resolver.openOutputStream(uri)
        out_stream.write(bytearray(data))
        out_stream.flush()
        out_stream.close()
        return 'la galería (Fotos > TranscripcionesYoutube)'

    def _save_to_media_store_downloads(self, data, filename):
        from jnius import autoclass

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = PythonActivity.mActivity
        VERSION = autoclass('android.os.Build$VERSION')
        self._request_legacy_storage_permission(VERSION.SDK_INT)

        if VERSION.SDK_INT >= 29:
            ContentValues = autoclass('android.content.ContentValues')
            MediaColumns = autoclass('android.provider.MediaStore$MediaColumns')
            Downloads = autoclass('android.provider.MediaStore$Downloads')
            values = ContentValues()
            values.put(MediaColumns.DISPLAY_NAME, filename)
            values.put(MediaColumns.MIME_TYPE, 'text/plain')
            values.put(MediaColumns.RELATIVE_PATH, 'Download/TranscripcionesYoutube/Videos')
            resolver = context.getContentResolver()
            uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
            if uri is None:
                raise Exception('No se pudo crear el archivo')
            out_stream = resolver.openOutputStream(uri)
            out_stream.write(bytearray(data))
            out_stream.flush()
            out_stream.close()
            return 'Descargas/TranscripcionesYoutube/Videos'
        else:
            Environment = autoclass('android.os.Environment')
            downloads_dir = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS
            )
            base = os.path.join(downloads_dir.getAbsolutePath(), 'TranscripcionesYoutube', 'Videos')
            os.makedirs(base, exist_ok=True)
            path = os.path.join(base, filename)
            with open(path, 'wb') as f:
                f.write(data)
            return path

    def _save_channel_pair_media_store(self, image_bytes, text_bytes, rel_path, base_name):
        from jnius import autoclass

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = PythonActivity.mActivity
        VERSION = autoclass('android.os.Build$VERSION')
        self._request_legacy_storage_permission(VERSION.SDK_INT)

        relative_path = f'Download/TranscripcionesYoutube/{rel_path}'

        if VERSION.SDK_INT >= 29:
            ContentValues = autoclass('android.content.ContentValues')
            MediaColumns = autoclass('android.provider.MediaStore$MediaColumns')
            Downloads = autoclass('android.provider.MediaStore$Downloads')
            resolver = context.getContentResolver()

            if image_bytes:
                values_img = ContentValues()
                values_img.put(MediaColumns.DISPLAY_NAME, base_name + '.jpg')
                values_img.put(MediaColumns.MIME_TYPE, 'image/jpeg')
                values_img.put(MediaColumns.RELATIVE_PATH, relative_path)
                uri_img = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values_img)
                if uri_img is not None:
                    out = resolver.openOutputStream(uri_img)
                    out.write(bytearray(image_bytes))
                    out.flush()
                    out.close()

            values_txt = ContentValues()
            values_txt.put(MediaColumns.DISPLAY_NAME, base_name + '.txt')
            values_txt.put(MediaColumns.MIME_TYPE, 'text/plain')
            values_txt.put(MediaColumns.RELATIVE_PATH, relative_path)
            uri_txt = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values_txt)
            if uri_txt is None:
                raise Exception('No se pudo crear el archivo de texto')
            out2 = resolver.openOutputStream(uri_txt)
            out2.write(bytearray(text_bytes))
            out2.flush()
            out2.close()
        else:
            Environment = autoclass('android.os.Environment')
            downloads_dir = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS
            )
            folder = os.path.join(
                downloads_dir.getAbsolutePath(), 'TranscripcionesYoutube', *rel_path.split('/')
            )
            os.makedirs(folder, exist_ok=True)
            if image_bytes:
                with open(os.path.join(folder, base_name + '.jpg'), 'wb') as f:
                    f.write(image_bytes)
            with open(os.path.join(folder, base_name + '.txt'), 'wb') as f:
                f.write(text_bytes)

    def _save_to_app_storage(self, data, filename):
        from jnius import autoclass
        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        context = PythonActivity.mActivity
        ext_dir = context.getExternalFilesDir(None)
        base = ext_dir.getAbsolutePath() if ext_dir is not None else os.getcwd()
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, filename)
        with open(path, 'wb') as f:
            f.write(data)
        return path


class TranscriptApp(App):
    title = 'Transcripciones YouTube'

    def build(self):
        try:
            self._disable_default_loading_spinner()
            return RootWidget()
        except Exception:
            import traceback
            from kivy.uix.scrollview import ScrollView
            from kivy.uix.label import Label
            error_text = traceback.format_exc()
            try:
                with open(os.path.join(self.user_data_dir, 'crash_log.txt'), 'w', encoding='utf-8') as f:
                    f.write(error_text)
            except Exception:
                pass
            scroll = ScrollView()
            label = Label(
                text=error_text,
                size_hint_y=None,
                text_size=(400, None),
                halign='left',
                valign='top',
                color=(1, 1, 1, 1),
            )
            label.bind(texture_size=lambda inst, val: setattr(label, 'height', val[1]))
            scroll.add_widget(label)
            return scroll

    def _disable_default_loading_spinner(self):
        try:
            path = os.path.join(self.user_data_dir, 'blank.png')
            os.makedirs(self.user_data_dir, exist_ok=True)
            if not os.path.exists(path):
                with open(path, 'wb') as f:
                    f.write(base64.b64decode(_BLANK_PNG_B64))
            blank = CoreImage(path)
            Loader.loading_image = blank
            Loader.error_image = blank
        except Exception:
            pass


if __name__ == '__main__':
    TranscriptApp().run()
