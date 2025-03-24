import os
import logging
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types # type: ignore
from aiogram.utils.executor import start_polling # type: ignore
from reportlab.lib.pagesizes import letter # type: ignore
from reportlab.lib import colors # type: ignore
from reportlab.lib.units import inch # type: ignore 
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle # type: ignore
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle # type: ignore
from reportlab.lib.enums import TA_CENTER, TA_LEFT # type: ignore
from reportlab.pdfbase import pdfmetrics # type: ignore
from reportlab.pdfbase.ttfonts import TTFont # type: ignore
import paramiko # type: ignore

# Пути к внешним папкам
PDF_STORAGE_PATH = "/app-pdfs"
LOGS_PATH = "/app/logs"
FONTS_PATH = "./fonts"

# Настройки
MAX_FILES = 10
DEFAULT_FONT = 'DejaVuSans'
AUTHORIZED_USERNAME = 'someeeday' # Replace with the actual authorized username

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
    level=logging.INFO,  # Исправлено: корректный уровень логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # Исправлено: корректный формат
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("server-stats-bot")

# Регистрация шрифтов из папки fonts
def register_fonts():
    """Регистрирует шрифты из папки fonts."""
    try:
        font_path = os.path.join(FONTS_PATH, "DejaVuSans.ttf")
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('DejaVuSans', font_path))
            logger.info("DejaVuSans успешно зарегистрирован")
            return True
    except Exception as e:
        logger.error(f"Ошибка при регистрации шрифта: {e}")
        return False

# Регистрируем шрифт
if not register_fonts():
    raise ValueError("Не удалось зарегистрировать шрифт DejaVuSans")

# Получение настроек из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logger.error("Переменная окружения BOT_TOKEN не задана!")
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

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

def execute_ssh_command(ssh_client, command, timeout=10):
    """Выполняет SSH-команду и возвращает результат."""
    try:
        _, stdout, _ = ssh_client.exec_command(command, timeout=timeout)
        return stdout.read().decode().strip()
    except Exception as e:
        logger.error(f"Ошибка выполнения команды '{command}': {e}", exc_info=True)
        return "Неизвестно"

def get_linux_system_info(ssh_client):
    """Собирает информацию о системе Linux."""
    try:
        return {
            'Пользователь': execute_ssh_command(ssh_client, 'whoami'),
            'Хост': execute_ssh_command(ssh_client, 'hostname'),
            'Операционная система': execute_ssh_command(ssh_client, "grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"'"),
            'Версия ОС': execute_ssh_command(ssh_client, 'uname -r'),
            'Процессор': execute_ssh_command(ssh_client, "grep 'model name' /proc/cpuinfo | head -n 1 | cut -d: -f2 | xargs"),
            'Количество ядер': execute_ssh_command(ssh_client, 'nproc'),
            'Оперативная память': execute_ssh_command(ssh_client, "free -h | awk '/^Mem:/ {print $2}'"),
            'Объем диска': execute_ssh_command(ssh_client, "df -h / | awk 'NR==2 {print $2}'"),
        }
    except Exception as e:
        logger.error(f"Ошибка при сборе информации о Linux: {e}", exc_info=True)
        return {}

def get_windows_system_info(ssh_client):
    """Собирает информацию о системе Windows."""
    try:
        return {
            'Пользователь': execute_ssh_command(ssh_client, 'powershell.exe -command "$env:USERNAME"'),
            'Хост': execute_ssh_command(ssh_client, 'powershell.exe -command "$env:COMPUTERNAME"'),
            'Операционная система': execute_ssh_command(ssh_client, 'powershell.exe -command "(Get-WmiObject -Class Win32_OperatingSystem).Caption"'),
            'Версия ОС': execute_ssh_command(ssh_client, 'powershell.exe -command "(Get-WmiObject -Class Win32_OperatingSystem).Version"'),
            'Процессор': execute_ssh_command(ssh_client, 'powershell.exe -command "(Get-WmiObject -Class Win32_Processor).Name"'),
            'Количество ядер': execute_ssh_command(ssh_client, 'powershell.exe -command "(Get-WmiObject -Class Win32_ComputerSystem).NumberOfProcessors"'),
            'Оперативная память': execute_ssh_command(ssh_client, 'powershell.exe -command "(Get-WmiObject -Class Win32_ComputerSystem).TotalPhysicalMemory | ForEach-Object { [Math]::Round($_ / 1MB, 0) }"') + " MB",
            'Объем диска': execute_ssh_command(ssh_client, 'powershell.exe -command "(Get-WmiObject -Class Win32_DiskDrive | Select-Object Size | ForEach-Object { [Math]::Round($_.Size / 1GB, 0) })[0]"') + " GB",
        }
    except Exception as e:
        logger.error(f"Ошибка при сборе информации о Windows: {e}", exc_info=True)
        return {}

def determine_os_type(ssh_client):
    """Определяет тип операционной системы (Linux или Windows)."""
    output = execute_ssh_command(ssh_client, 'ver')
    return "windows" if "windows" in output.lower() else "linux"

def get_system_info_ssh(hostname, port, username, password):
    """Подключается к удаленной системе по SSH и собирает информацию."""
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(hostname=hostname, port=port, username=username, password=password, timeout=30)

        os_type = determine_os_type(ssh_client)
        system_data = get_linux_system_info(ssh_client) if os_type == "linux" else get_windows_system_info(ssh_client)
        system_data.update({'IP-адрес': hostname, 'Порт SSH': port})

        ssh_client.close()
        return system_data
    except Exception as e:
        logger.error(f"Ошибка при подключении по SSH или сборе информации: {e}", exc_info=True)
        return {}

def generate_system_report_pdf(system_data=None):
    """Генерирует PDF-отчет с данными о системе."""
    try:
        # Получение времени в Москве
        moscow_tz = timezone(timedelta(hours=3))  # UTC+3
        now = datetime.now(moscow_tz)
        months_ru = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
        }
        current_date = f"{now.day} {months_ru[now.month]} {now.year} года"
        filename_time = now.strftime("%Y%m%d_%H%M%S")
        
        logger.info("Начало генерации PDF-файла.")
        
        # Путь для сохранения PDF
        pdf_path = os.path.join(PDF_STORAGE_PATH, f"system_report_{filename_time}.pdf")
        
        # Создание PDF
        doc = SimpleDocTemplate(pdf_path, pagesize=letter, encoding='utf-8')
        elements = []
        
        # Получаем шрифт по умолчанию
        logger.info(f"Используем шрифт: {DEFAULT_FONT}")
        
        # Создаем стили с явным указанием шрифта
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            fontName=DEFAULT_FONT,
            fontSize=18,
            alignment=TA_CENTER,
            leading=22  # Увеличиваем межстрочный интервал
        )
        heading_style = ParagraphStyle(
            'CustomHeading',
            fontName=DEFAULT_FONT,
            fontSize=14,
            alignment=TA_LEFT,
            spaceAfter=6,
            leading=18
        )
        normal_style = ParagraphStyle(
            'CustomNormal',
            fontName=DEFAULT_FONT,
            fontSize=12,
            alignment=TA_LEFT,
            leading=14
        )
        
        # Заголовок отчета
        elements.append(Paragraph("Отчет о состоянии системы", title_style))
        elements.append(Spacer(1, 0.25*inch))
        elements.append(Paragraph(f"Сгенерировано: {current_date}", normal_style))
        elements.append(Spacer(1, 0.5*inch))
        
        # Основная информация о системе
        elements.append(Paragraph("Основная информация", heading_style))
        
        # Создаем таблицу с данными
        if system_data:
            system_data_list = [
                ["Параметр", "Значение"],
                ["Пользователь", system_data.get('Пользователь', '—')],
                ["IP-адрес", system_data.get('IP-адрес', '—')],
                ["Порт SSH", system_data.get('Порт SSH', '—')],
                ["Операционная система", system_data.get('Операционная система', '—')],
                ["Версия ОС", system_data.get('Версия ОС', '—')],
                ["Процессор", system_data.get('Процессор', '—')],
                ["Оперативная память", system_data.get('Оперативная память', '—')],
                ["Объем диска", system_data.get('Объем диска', '—')]
            ]
        else:
            system_data_list = [
                ["Параметр", "Значение"],
                ["Пользователь", "—"],
                ["IP-адрес", "—"],
                ["Порт SSH", "—"],
                ["Операционная система", "—"],
                ["Версия ОС", "—"],
                ["Процессор", "—"],
                ["Оперативная память", "—"],
                ["Объем диска", "—"]
            ]
        
        # Создание таблицы
        t = Table(system_data_list, colWidths=[2.5*inch, 4*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), DEFAULT_FONT),  # Применяем шрифт ко всей таблице
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Выравнивание первой колонки по левому краю
        ]))
        
        elements.append(t)
        elements.append(Spacer(1, 0.5*inch))
        
        # Заголовок для раздела с графиками
        elements.append(Paragraph("Использование ресурсов", heading_style))
        elements.append(Spacer(1, 0.1*inch))
        
        # Добавляем заглушку для графиков
        elements.append(Paragraph("Графики временно недоступны", normal_style))
        
        # Создаем PDF
        try:
            doc.build(elements)
            logger.info(f"PDF-файл '{pdf_path}' успешно создан.")
        except Exception as pdf_error:
            logger.error(f"Ошибка при сохранении PDF-файла: {pdf_error}")
        
        # Очистка старых файлов
        cleanup_old_pdfs()
        
        return pdf_path
    except Exception as e:
        logger.error(f"Ошибка при создании PDF: {e}", exc_info=True)  # Добавлено exc_info для стека ошибок
        return None

@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    """Проверка работоспособности бота."""
    await message.reply("Бот активен и готов к работе. Используйте /log для получения отчета.")

@dp.message_handler(commands=["log"])
async def log_command(message: types.Message):
    """Генерация и отправка отчета о системе."""
    try:
        # Проверка авторизации
        if message.from_user.username != AUTHORIZED_USERNAME:
            await message.reply("У вас нет доступа к этой команде.")
            logger.warning(f"Попытка доступа от неавторизованного пользователя: {message.from_user.username}")
            return

        # Отправка уведомления
        wait_message = await message.reply("Генерирую отчет о системе...")

        # Параметры для подключения по SSH
        ssh_hostname = os.getenv("SSH_HOSTNAME")
        ssh_port = int(os.getenv("SSH_PORT", 22))
        ssh_username = os.getenv("SSH_USERNAME")
        ssh_password = os.getenv("SSH_PASSWORD")

        if not all([ssh_hostname, ssh_username, ssh_password]):
            await wait_message.edit_text("Необходимо задать параметры SSH в переменных окружения.")
            logger.error("Необходимо задать параметры SSH в переменных окружения.")
            return

        # Получение данных о системе через SSH
        system_data = get_system_info_ssh(ssh_hostname, ssh_port, ssh_username, ssh_password)

        # Создание и отправка отчета
        pdf_file = generate_system_report_pdf(system_data)
        if pdf_file and os.path.exists(pdf_file):
            with open(pdf_file, "rb") as file:
                await message.answer_document(file, caption="Отчет о системе")
                await wait_message.delete()
        else:
            await wait_message.edit_text("Не удалось создать отчет. Проверьте логи.")
            logger.error("Не удалось создать отчет. Проверьте логи.")
    except Exception as e:
        await message.reply("Произошла ошибка при выполнении команды. Проверьте логи.")
        logger.error(f"Ошибка при выполнении команды /log: {e}", exc_info=True)

if __name__ == "__main__":
    logger.info("Бот запущен")
    start_polling(dp, skip_updates=True)