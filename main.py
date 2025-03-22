import os
import logging
import re
import tempfile
import locale
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import start_polling
from datetime import datetime, timezone, timedelta
import psutil
import paramiko
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
from matplotlib import rcParams
import matplotlib.font_manager as font_manager
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Устанавливаем кодировку UTF-8
import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

# Настраиваем шрифты для matplotlib
# Добавляем шрифт DejaVu Sans (с поддержкой кириллицы)
rcParams['font.family'] = 'sans-serif'
rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Verdana', 'Tahoma']
rcParams['font.size'] = 10
rcParams['pdf.fonttype'] = 42  # Для более чистого текста

# Импортируем модуль с встроенными шрифтами
try:
    from embedded_fonts import get_font_data, ARIAL_FONT_BASE64
    
    # Извлекаем and используем встроенный шрифт Arial
    arial_font_path = get_font_data(ARIAL_FONT_BASE64, "Arial")
    
    # Регистрируем шрифт для ReportLab
    pdfmetrics.registerFont(TTFont('ArialEmbed', arial_font_path))
    print(f"Зарегистрирован встроенный шрифт Arial из {arial_font_path}")
    
    # Настраиваем matplotlib для использования этого шрифта
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans']
    
    # Устанавливаем его как предпочтительный
    DEFAULT_FONT = 'ArialEmbed'
except Exception as e:
    print(f"Ошибка при загрузке встроенного шрифта: {e}")
    DEFAULT_FONT = 'Helvetica'

# Настройка русской локали
try:
    locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')
except:
    try:
        locale.setlocale(locale.LC_ALL, 'Russian_Russia.1251')
    except:
        print("Не удалось установить русскую локаль")

# Пути к внешним папкам
PDF_STORAGE_PATH = "/app-pdfs"
LOGS_PATH = "/app/logs"

# Создание необходимых папок
for path in [LOGS_PATH, PDF_STORAGE_PATH]:
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except Exception as e:
            print(f"Ошибка при создании папки {path}: {e}")

# Настройка логирования
LOG_FILE = os.path.join(LOGS_PATH, "debug.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levellevelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("server-stats-bot")

# Регистрация шрифтов с поддержкой кириллицы
try:
    # Проверяем сначала наши локальные шрифты
    font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
    fonts_registered = False
    
    if os.path.exists(font_dir):
        logger.info(f"Найдена папка со шрифтами: {font_dir}")
        for font_file in os.listdir(font_dir):
            if font_file.lower().endswith('.ttf'):
                try:
                    font_path = os.path.join(font_dir, font_file)
                    font_name = os.path.splitext(font_file)[0]
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    logger.info(f"Зарегистрирован шрифт: {font_name} из {font_path}")
                    fonts_registered = True
                except Exception as e:
                    logger.error(f"Ошибка при регистрации шрифта {font_file}: {e}")
    
    # Если локальные шрифты не найдены или не зарегистрированы, пробуем системные
    if not fonts_registered:
        # Путь к встроенному DejaVu Sans (поддерживает кириллицу)
        dejavu_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.exists(dejavu_path):
            pdfmetrics.registerFont(TTFont('DejaVuSans', dejavu_path))
            logger.info("Зарегистрирован системный шрифт DejaVuSans для PDF")
            fonts_registered = True
    
    # Проверяем, что хоть один шрифт был зарегистрирован
    if not fonts_registered:
        logger.warning("Не удалось зарегистрировать ни один шрифт с поддержкой кириллицы")
        
except Exception as e:
    logger.error(f"Ошибка при регистрации шрифтов: {e}")

# Настройка matplotlib для кириллицы - используем зарегистрированные шрифты
font_list = pdfmetrics.getRegisteredFontNames()
if 'ARIAL' in font_list:
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['ARIAL', 'DejaVu Sans']
elif 'DejaVuSans' in font_list:
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']

# Получение настроек из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logger.error("Переменная окружения BOT_TOKEN не задана!")
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

AUTHORIZED_USERNAME = os.getenv("AUTHORIZED_USERNAME", "someeeday")
HOST_IP = os.getenv("HOST_IP", "host.docker.internal")
SSH_USERNAME = os.getenv("SSH_USERNAME", "")
SSH_PASSWORD = os.getenv("SSH_PASSWORD", "")
SSH_PORT = int(os.getenv("SSH_PORT", "22"))
MAX_FILES = int(os.getenv("MAX_FILES", "10"))

# Инициализация бота
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

def cleanup_old_pdfs():
    """Удаляет старые файлы, оставляя только последние MAX_FILES."""
    try:
        files = sorted(
            [os.path.join(PDF_STORAGE_PATH, f) for f in os.listdir(PDF_STORAGE_PATH) if f.endswith('.pdf')],
            key=os.path.getmtime
        )
        if len(files) > MAX_FILES:
            for file in files[:-MAX_FILES]:
                os.remove(file)
                logger.info(f"Удален старый файл: {file}")
    except Exception as e:
        logger.error(f"Ошибка при очистке старых файлов: {e}")

def get_system_info_ssh():
    """Получает информацию о системе через SSH."""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_IP, port=SSH_PORT, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=10)
        
        system_info = {}
        
        # Определение ОС and выбор команд
        stdin, stdout, stderr = ssh.exec_command("ver || uname -s")
        os_output = stdout.read().decode().strip()
        is_windows = "Windows" in os_output
        
        if is_windows:
            # Команды для Windows
            system_info["os_type"] = "Windows"
            
            # CPU информация
            stdin, stdout, stderr = ssh.exec_command("wmic cpu get Name, NumberOfCores, NumberOfLogicalProcessors /value")
            cpu_output = stdout.read().decode().strip()
            
            cpu_name_match = re.search(r'Name=(.+)', cpu_output)
            if cpu_name_match:
                system_info["cpu_model"] = cpu_name_match.group(1).strip()
            
            cpu_cores_match = re.search(r'NumberOfCores=(\d+)', cpu_output)
            if cpu_cores_match:
                system_info["cpu_cores"] = int(cpu_cores_match.group(1).strip())
                
            cpu_threads_match = re.search(r'NumberOfLogicalProcessors=(\d+)', cpu_output)
            if cpu_threads_match:
                system_info["cpu_threads"] = int(cpu_threads_match.group(1).strip())
            
            # Видеокарта
            stdin, stdout, stderr = ssh.exec_command("wmic path win32_VideoController get Name, AdapterRAM /value")
            gpu_output = stdout.read().decode().strip()
            
            gpu_name_match = re.search(r'Name=(.+)', gpu_output)
            if gpu_name_match:
                system_info["gpu_model"] = gpu_name_match.group(1).strip()
            
            gpu_ram_match = re.search(r'AdapterRAM=(\d+)', gpu_output)
            if gpu_ram_match:
                gpu_ram = int(gpu_ram_match.group(1).strip()) // (1024 * 1024)  # В МБ
                system_info["gpu_ram"] = gpu_ram
            
            # RAM
            stdin, stdout, stderr = ssh.exec_command("wmic ComputerSystem get TotalPhysicalMemory /value")
            mem_output = stdout.read().decode().strip()
            total_mem_match = re.search(r'TotalPhysicalMemory=(\d+)', mem_output)
            
            if total_mem_match:
                total_memory = int(total_mem_match.group(1).strip()) // (1024 * 1024)  # В МБ
                system_info["total_memory"] = total_memory
            
            stdin, stdout, stderr = ssh.exec_command("wmic OS get FreePhysicalMemory /value")
            free_mem_output = stdout.read().decode().strip()
            free_mem_match = re.search(r'FreePhysicalMemory=(\d+)', free_mem_output)
            
            if free_mem_match and "total_memory" in system_info:
                free_memory = int(free_mem_match.group(1).strip()) // 1024  # КБ в МБ
                system_info["free_memory"] = free_memory
                system_info["used_memory"] = system_info["total_memory"] - free_memory
            
            # CPU загрузка
            stdin, stdout, stderr = ssh.exec_command("wmic cpu get LoadPercentage /value")
            cpu_load_output = stdout.read().decode().strip()
            cpu_load_match = re.search(r'LoadPercentage=(\d+)', cpu_load_output)
            
            if cpu_load_match:
                system_info["cpu_usage"] = int(cpu_load_match.group(1).strip())
            
            # Диски
            stdin, stdout, stderr = ssh.exec_command("wmic logicaldisk get DeviceID, Size, FreeSpace /value")
            disk_output = stdout.read().decode().strip()
            
            disks = []
            for disk_section in disk_output.split("\n\n"):
                if not disk_section.strip():
                    continue
                    
                device_id_match = re.search(r'DeviceID=(.+)', disk_section)
                size_match = re.search(r'Size=(\d+)', disk_section)
                free_match = re.search(r'FreeSpace=(\d+)', disk_section)
                
                if device_id_match and size_match and free_match:
                    device_id = device_id_match.group(1).strip()
                    size = int(size_match.group(1).strip()) // (1024 * 1024 * 1024)  # В ГБ
                    free = int(free_match.group(1).strip()) // (1024 * 1024 * 1024)  # В ГБ
                    used = size - free
                    
                    disks.append({
                        "device": device_id,
                        "size": size,
                        "free": free,
                        "used": used
                    })
            
            system_info["disks"] = disks
            
            # Имя хоста
            stdin, stdout, stderr = ssh.exec_command("hostname")
            system_info["hostname"] = stdout.read().decode().strip()
            
            # Версия Windows
            system_info["os_version"] = os_output.strip()
            
            # Время работы
            stdin, stdout, stderr = ssh.exec_command("net statistics workstation | findstr \"Statistics since\"")
            uptime_output = stdout.read().decode().strip()
            if uptime_output:
                system_info["uptime"] = uptime_output
                
        else:
            # Команды для Linux
            system_info["os_type"] = "Linux"
            
            # Получение всех данных о CPU, RAM, дисках and т.д. аналогично Windows...
            # ...
        
        ssh.close()
        return system_info
    except Exception as e:
        logger.error(f"Ошибка при получении информации через SSH: {e}")
        return None

def create_memory_chart(total, used, free, filename='memory_usage.png'):
    """Создает график использования памяти."""
    # Установка русского текста для matplotlib
    plt.rcParams['font.family'] = 'DejaVu Sans'
    
    labels = ['Используется', 'Свободно']
    sizes = [used, free]
    colors = ['#ff9999', '#66b3ff']
    
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
    ax.axis('equal')
    
    plt.title(f'Использование памяти (Всего: {total} МБ)')
    
    # Сохраняем во временный файл
    temp_filename = os.path.join(tempfile.gettempdir(), filename)
    plt.savefig(temp_filename, dpi=100, bbox_inches='tight')
    plt.close()
    
    return temp_filename

def create_cpu_chart(cpu_usage, filename='cpu_usage.png'):
    """Создает график использования процессора."""
    fig, ax = plt.subplots(figsize=(6, 3))
    
    categories = ['Загрузка CPU']
    values = [cpu_usage]
    
    ax.barh(categories, values, color='#ff9999')
    ax.barh(categories, [100 - cpu_usage], left=values, color='#66b3ff')
    
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(PercentFormatter())
    ax.set_title('Загрузка процессора')
    
    for i, v in enumerate(values):
        ax.text(v/2, i, f"{v}%", va='center', ha='center', color='white', fontweight='bold')
    
    temp_filename = os.path.join(tempfile.gettempdir(), filename)
    plt.savefig(temp_filename, dpi=100, bbox_inches='tight')
    plt.close()
    
    return temp_filename

def create_disk_chart(disks, filename='disk_usage.png'):
    """Создает график использования дисков."""
    if not disks:
        return None
    
    labels = []
    used_values = []
    free_values = []
    
    for disk in disks:
        if isinstance(disk["device"], str) and disk["device"].strip():
            labels.append(disk["device"])
            used_values.append(disk["used"])
            free_values.append(disk["free"])
    
    if not labels:
        return None
    
    fig, ax = plt.subplots(figsize=(8, 4))
    
    x = range(len(labels))
    width = 0.35
    
    ax.bar(x, used_values, width, label='Используется', color='#ff9999')
    ax.bar([i + width for i in x], free_values, width, label='Свободно', color='#66b3ff')
    
    ax.set_ylabel('Размер (ГБ)')
    ax.set_title('Использование дисковой памяти')
    ax.set_xticks([i + width/2 for i in x])
    ax.set_xticklabels(labels)
    ax.legend()
    
    for i, v in enumerate(used_values):
        ax.text(i, v, str(v), ha='center', va='bottom')
    
    for i, v in enumerate(free_values):
        ax.text(i + width, v, str(v), ha='center', va='bottom')
    
    temp_filename = os.path.join(tempfile.gettempdir(), filename)
    plt.savefig(temp_filename, dpi=100, bbox_inches='tight')
    plt.close()
    
    return temp_filename

# Определяем месяцы на русском для ручного форматирования дат
MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 
    5: "мая", 6: "июня", 7: "июля", 8: "августа", 
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

def format_date_ru(date):
    """Форматирует дату на русском языке."""
    try:
        # Пробуем использовать локаль
        formatted = date.strftime("%d %B %Y %H:%M")
        # Проверка наличия английских символов (если локаль не работает)
        if any(c in formatted for c in 'abcdefghijklmnopqrstuvwxyz'):
            raise ValueError("Английские буквы в дате")
        return formatted
    except:
        # Ручное форматирование
        return f"{date.day} {MONTHS_RU[date.month]} {date.year} {date.hour:02d}:{date.minute:02d}"

def generate_pdf():
    """Генерирует PDF-файл с информацией о системе and графиками."""
    try:
        # Получение времени в Москве
        moscow_tz = timezone(timedelta(hours=3))  # UTC+3
        now = datetime.now(moscow_tz)
        current_time = format_date_ru(now)
        filename_time = now.strftime("%Y%m%d_%H%M%S")
        
        # Путь для сохранения PDF
        pdf_path = os.path.join(PDF_STORAGE_PATH, f"system_report_{filename_time}.pdf")
        
        # Получение информации о системе
        host_info = get_system_info_ssh()
        
        # Создание графиков
        cpu_chart = create_cpu_chart(host_info.get("cpu_usage", 0) if host_info else 0)
        
        memory_chart = None
        if host_info and "total_memory" in host_info and "used_memory" in host_info and "free_memory" in host_info:
            memory_chart = create_memory_chart(
                host_info["total_memory"],
                host_info["used_memory"],
                host_info["free_memory"]
            )
        
        disk_chart = None
        if host_info and "disks" in host_info:
            disk_chart = create_disk_chart(host_info["disks"])
        
        # Создание PDF
        doc = SimpleDocTemplate(pdf_path, pagesize=letter)
        elements = []
        
        # Определяем доступные шрифты and выбираем шрифт с поддержкой кириллицы
        font_name = DEFAULT_FONT  # Встроенный шрифт с кириллицей
        
        # Стили для текста с поддержкой кириллицы
        styles = getSampleStyleSheet()
        
        # Создаем стили с поддержкой кириллицы
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles["Title"],
            fontName=font_name,
            fontSize=18,
            alignment=TA_CENTER
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles["Heading1"],
            fontName=font_name,
            fontSize=14
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=10
        )
        
        # Заголовок отчета
        elements.append(Paragraph("Отчет о состоянии системы", title_style))
        elements.append(Spacer(1, 0.25*inch))
        elements.append(Paragraph(f"Сгенерировано: {current_time}", normal_style))
        elements.append(Spacer(1, 0.5*inch))
        
        # Информация о системе
        if host_info:
            # Основная информация
            elements.append(Paragraph("Информация о системе", heading_style))
            elements.append(Spacer(1, 0.15*inch))
            
            system_data = [
                ["Параметр", "Значение"],
                ["Имя хоста", host_info.get("hostname", "Н/Д")],
                ["Операционная система", host_info.get("os_version", host_info.get("os_type", "Н/Д"))],
                ["Время работы", host_info.get("uptime", "Н/Д")],
            ]
            
            t = Table(system_data, colWidths=[2*inch, 4*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), font_name),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            
            elements.append(t)
            elements.append(Spacer(1, 0.25*inch))
            
            # Информация о процессоре
            elements.append(Paragraph("Процессор and память", heading_style))
            elements.append(Spacer(1, 0.15*inch))
            
            cpu_data = [
                ["Параметр", "Значение"],
                ["Процессор", host_info.get("cpu_model", "Н/Д")],
                ["Ядра / Потоки", f"{host_info.get('cpu_cores', 'Н/Д')} / {host_info.get('cpu_threads', 'Н/Д')}"],
                ["Загрузка CPU", f"{host_info.get('cpu_usage', 'Н/Д')}%"],
                ["Оперативная память", f"{host_info.get('total_memory', 'Н/Д')} МБ ({host_info.get('total_memory', 0) // 1024} ГБ)"],
                ["Использовано RAM", f"{host_info.get('used_memory', 'Н/Д')} МБ ({host_info.get('used_memory', 0) * 100 // host_info.get('total_memory', 1)}%)"],
                ["Свободно RAM", f"{host_info.get('free_memory', 'Н/Д')} МБ ({host_info.get('free_memory', 0) * 100 // host_info.get('total_memory', 1)}%)"]
            ]
            
            if "gpu_model" in host_info:
                cpu_data.append(["Видеокарта", host_info.get("gpu_model", "Н/Д")])
                if "gpu_ram" in host_info:
                    cpu_data.append(["Память GPU", f"{host_info.get('gpu_ram', 'Н/Д')} МБ"])
            
            t = Table(cpu_data, colWidths=[2*inch, 4*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), font_name),
                ('FONTSIZE', (0, 0), (-1, 0), 12),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]))
            
            elements.append(t)
            elements.append(Spacer(1, 0.25*inch))
            
            # Диски
            if "disks" in host_info and host_info["disks"]:
                elements.append(Paragraph("Дисковое пространство", heading_style))
                elements.append(Spacer(1, 0.15*inch))
                
                disk_headers = ["Диск", "Размер (ГБ)", "Использовано (ГБ)", "Свободно (ГБ)", "% использования"]
                disk_data = [disk_headers]
                
                for disk in host_info["disks"]:
                    device = disk["device"]
                    size = disk["size"]
                    used = disk["used"]
                    free = disk["free"]
                    
                    # Вычисляем процент использования
                    if size > 0:
                        usage_percent = f"{used * 100 // size}%"
                    else:
                        usage_percent = "Н/Д"
                    
                    disk_data.append([device, str(size), str(used), str(free), usage_percent])
                
                t = Table(disk_data, colWidths=[1.5*inch, 1*inch, 1.5*inch, 1*inch, 1*inch])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
                    ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, 0), font_name),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.white),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                
                elements.append(t)
                elements.append(Spacer(1, 0.25*inch))
        else:
            elements.append(Paragraph("Не удалось получить информацию о системе", heading_style))
            elements.append(Spacer(1, 0.25*inch))
        
        # Добавление графиков
        elements.append(Paragraph("Графики использования ресурсов", heading_style))
        elements.append(Spacer(1, 0.15*inch))
        
        if cpu_chart:
            elements.append(Paragraph("Загрузка процессора", normal_style))
            elements.append(Image(cpu_chart, width=6*inch, height=3*inch))
            elements.append(Spacer(1, 0.25*inch))
        
        if memory_chart:
            elements.append(Paragraph("Использование оперативной памяти", normal_style))
            elements.append(Image(memory_chart, width=5*inch, height=4*inch))
            elements.append(Spacer(1, 0.25*inch))
        
        if disk_chart:
            elements.append(Paragraph("Использование дискового пространства", normal_style))
            elements.append(Image(disk_chart, width=6*inch, height=4*inch))
        
        # Создаем PDF
        doc.build(elements)
        
        # Очистка старых файлов
        cleanup_old_pdfs()
        
        return pdf_path
    except Exception as e:
        logger.error(f"Ошибка при создании PDF: {e}")
        return None

@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    """Проверка работоспособности бота."""
    await message.reply("Бот активен and готов к работе. Используйте /log для получения отчета о системе.")

@dp.message_handler(commands=["log"])
async def log_command(message: types.Message):
    """Генерация and отправка отчета о системе."""
    # Проверка авторизации
    if message.from_user.username != AUTHORIZED_USERNAME:
        await message.reply("У вас нет доступа к этой команде.")
        logger.warning(f"Попытка доступа от неавторизованного пользователя: {message.from_user.username}")
        return

    # Отправка уведомления
    wait_message = await message.reply("Генерирую отчет о системе...")
    
    try:
        # Создание and отправка отчета
        pdf_file = generate_pdf()
        if pdf_file and os.path.exists(pdf_file):
            with open(pdf_file, "rb") as file:
                await message.reply_document(file, caption="Отчет о системе")
                await wait_message.delete()
        else:
            await wait_message.edit_text("Не удалось создать отчет. Проверьте логи.")
    except Exception as e:
        await wait_message.edit_text(f"Ошибка: {e}")
        logger.error(f"Ошибка при выполнении команды /log: {e}")

if __name__ == "__main__":
    logger.info("Бот запущен")
    start_polling(dp, skip_updates=True)