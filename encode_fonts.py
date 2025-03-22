import os
import base64

def encode_font_to_base64(font_path):
    """Кодирует файл шрифта в base64 для встраивания в код."""
    with open(font_path, 'rb') as f:
        font_data = f.read()
    
    encoded = base64.b64encode(font_data)
    return encoded.decode('ascii')

def save_encoded_fonts():
    """Сохраняет закодированные шрифты в файл Python."""
    fonts_dir = "fonts"
    encoded_fonts = {}
    
    for font_file in os.listdir(fonts_dir):
        if font_file.lower().endswith('.ttf'):
            font_path = os.path.join(fonts_dir, font_file)
            font_name = os.path.splitext(font_file)[0]
            print(f"Кодирую шрифт: {font_file}")
            encoded = encode_font_to_base64(font_path)
            encoded_fonts[font_name] = encoded
            
    # Создаем Python модуль с закодированными шрифтами
    with open('embedded_fonts.py', 'w') as f:
        f.write('# Файл содержит шрифты в формате base64\n\n')
        f.write('EMBEDDED_FONTS = {\n')
        for name, data in encoded_fonts.items():
            f.write(f'    "{name}": """{data}""",\n')
        f.write('}\n')

if __name__ == "__main__":
    save_encoded_fonts()
    print("Шрифты закодированы и сохранены в embedded_fonts.py")
