[app]

title = Transcripciones YouTube
package.name = transcripcionesyoutube
package.domain = org.miapp

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 1.0

requirements = python3==3.11.8,hostpython3==3.11.8,kivy==2.3.1,youtube-transcript-api,requests,certifi,charset-normalizer,idna,urllib3,defusedxml

orientation = portrait
fullscreen = 0

android.permissions = INTERNET

android.api = 34
android.minapi = 21
android.ndk = 28c
android.accept_sdk_license = True
android.archs = arm64-v8a, armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1
