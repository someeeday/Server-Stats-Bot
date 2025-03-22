# Встроенные шрифты - используем самые необходимые участки для Arial

ARIAL_FONT_BASE64 = """
AAEAAAARAQAABAAQR0RFRgASAAMAAAEsAAAAFkdQT1OQxpDHAAABRAAABLBHU1VCgv9Z+gAABfQA
AABgT1MvMnsVX3YAAAKUAAAAYGNtYXAA5QDzAAAC9AAAAGxjdnQgK34mlQAAA2AAAABMZnBnbWf0
XKsAAAd0AAABvGdhc3AACAATAAABJAAAAAhnbHlm0aRXWQAAA7QAAAHMaGVhZBDQy/0AAAJwAAAA
NmhoZWEHwgPJAAACqAAAACRobXR4EV4A+wAAAnAAAAAkbG9jYQBWAQYAAAJ4AAAAFm1heHABFQBG
AAACiAAAACBuYW1lL+JOFgAABTAAAAGocG9zdP9tAGQAAAIwAAAAIHByZXAmkTqXAAADlAAAAJgA
AQAAAAMYZ8F1/8ZfDzz1AAMD6AAAAADXxj6eAAAAANfGPp5f/37+/gOdA6YAAAADAAIAAAAAAAfq
B+oABAAABZoFMwAAAR8FmgUzAAAD0QBmAfEIAgILBgYDBQQCAgSAAAAnAAAAQwAAAAAAAAAAMEFT
QwBAACAiEgYf/hEAhAOiBDkgAAGfAAAAAARIBbYAAAAgAAMAAAACAAAAAwAAABQAAwABAAAAFAAE
AGQAAAACAAIAAAAiACAARQBGAEcAUgBx//8AAAAgAEUARgBHAFIAcf///9//uf+4/7f/rf+QAAEA
AAAAAbgALQAqAGgAKAAyAF4ANgBBAAAADAImAygDQANYA24DhAPuAAEAAAANAAhFjQAIAGEABgAB
AAAAAAAAAAAAAAACAAEAAAAKAF4AAwABBAkAAQASAFwAAwABBAkAAgAOAE4AAwABBAkAAwA0ADQ=
"""

# Функция для декодирования и сохранения шрифта
def get_font_data(font_base64, font_name):
    """Возвращает декодированные данные шрифта."""
    import base64
    import tempfile
    import os
    
    # Декодируем шрифт
    font_data = base64.b64decode(font_base64.strip())
    
    # Сохраняем во временный файл
    temp_dir = tempfile.gettempdir()
    font_path = os.path.join(temp_dir, f"{font_name}.ttf")
    
    with open(font_path, "wb") as f:
        f.write(font_data)
    
    return font_path
