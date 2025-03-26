import os
import matplotlib
matplotlib.use('Agg')  # Установка backend до импорта pyplot
from datetime import datetime, timezone, timedelta
from io import BytesIO
from matplotlib.figure import Figure # type: ignore
from aiogram import Bot, Dispatcher, types  # type: ignore
from aiogram.utils.executor import start_polling  # type: ignore
from reportlab.lib.pagesizes import letter  # type: ignore
from reportlab.lib import colors  # type: ignore
from reportlab.lib.units import inch  # type: ignore
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image  # type: ignore
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # type: ignore
from reportlab.lib.enums import TA_CENTER, TA_LEFT  # type: ignore
from reportlab.pdfbase import pdfmetrics  # type: ignore
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore
import paramiko  # type: ignore
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton # type: ignore
from monitoring import SystemMonitor
from logger import logger

# Константы и настройки
PDF_STORAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf-storage")
LOGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
FONTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

MAX_FILES = 10
DEFAULT_FONT = 'DejaVuSans'
ALERT_COOLDOWN = 3600  # 1 час
MAX_FAILED_ATTEMPTS = 3
LOCKOUT_TIME = 300  # 5 минут блокировки

# Создание необходимых директорий
for path in [LOGS_PATH, PDF_STORAGE_PATH]:
    os.makedirs(path, exist_ok=True)

# Сообщения бота
BOT_MESSAGES = {
    'start': ("🤖 *Бот мониторинга серверов*\n\n"
             "📋 Доступные команды:\n"
             "/log - Отчет о системе\n"
             "/ssh - Настройка подключения\n"
             "/start_monitor - Включить мониторинг\n"
             "/stop_monitor - Выключить мониторинг"),
    'ssh_prompt': "Введите данные подключения в формате user@host:",
    'ssh_exists': "Активное подключение: {user}@{host}\nДля новой настройки нажмите кнопку ниже.",
    'ssh_success': "✅ Подключение успешно настроено",
    'ssh_error': "❌ Ошибка подключения. Проверьте данные",
    'monitoring_offer': ("📊 Включить мониторинг системы?\n\n"
                        "Вы будете получать уведомления о критических ситуациях\n"
                        "с рекомендациями по их устранению."),
    'monitoring_enabled': ("✅ Мониторинг включен\n\n"
                         "Вы будете получать уведомления при проблемах.\n"
                         "Для отключения используйте /stop_monitor"),
    'monitoring_exists': "❗ Мониторинг уже запущен",
    'monitoring_disabled': "✅ Мониторинг отключен",
    'monitoring_not_running': "❗ Мониторинг не был включен",
    'no_ssh': "❌ SSH не настроен. Используйте /ssh для настройки",
    'report_generating': "📊 Генерация отчета...",
    'report_error': "❌ Ошибка создания отчета",
    'rate_limit': "⚠️ Слишком много попыток. Подождите {minutes} мин."
}

def register_fonts():
    """Регистрация шрифтов с обработкой ошибок."""
    try:
        font_path = os.path.join(FONTS_PATH, "DejaVuSans.ttf")
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('DejaVuSans', font_path))
            logger.info("Шрифт DejaVuSans успешно зарегистрирован")
            return True
        logger.error("Файл шрифта не найден")
        return False
    except Exception as e:
        logger.error(f"Ошибка регистрации шрифта: {e}")
        return False

if not register_fonts():
    raise ValueError("Не удалось зарегистрировать необходимые шрифты")

# Проверка и получение токена
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logger.error("BOT_TOKEN не задан в переменных окружения")
    raise ValueError("BOT_TOKEN не задан")

# Инициализация бота
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
monitor = SystemMonitor(bot)

# Состояния и кэши
user_states = {}
ssh_connections = {}
failed_attempts = {}
locked_users = {}

# Запрещенные хосты
BLOCKED_HOSTS = {
    'localhost', '127.0.0.1', '::1',
    '0.0.0.0', '0.0.0.0/0',
    '10.0.0.0/8',
    '172.16.0.0/12',
    '192.168.0.0/16',
    'fc00::/7'
}

def cleanup_old_pdfs():
    """Очистка старых PDF файлов с логированием."""
    try:
        files = sorted(
            [os.path.join(PDF_STORAGE_PATH, f) for f in os.listdir(PDF_STORAGE_PATH) 
             if f.endswith('.pdf')],
            key=os.path.getmtime
        )
        if len(files) > MAX_FILES:
            for file in files[:-MAX_FILES]:
                os.remove(file)
                logger.info(f"Удален старый файл: {file}")
    except Exception as e:
        logger.error(f"Ошибка при очистке старых файлов: {e}")

def is_host_allowed(hostname: str) -> bool:
    """Проверка безопасности хоста."""
    try:
        if not hostname or not isinstance(hostname, str):
            return False
            
        if any(char in hostname for char in ';&|`$(){}[]<>\\'):
            return False
            
        for blocked in BLOCKED_HOSTS:
            if hostname.startswith(blocked.split('/')[0]):
                return False
                
        return True
    except Exception:
        return False

def check_rate_limit(user_id: int) -> bool:
    """Проверка ограничения попыток."""
    current_time = datetime.now()
    if user_id in locked_users:
        if (current_time - locked_users[user_id]).total_seconds() < LOCKOUT_TIME:
            return False
        del locked_users[user_id]
        failed_attempts[user_id] = 0
    return True

def record_failed_attempt(user_id: int):
    """Запись неудачной попытки."""
    failed_attempts[user_id] = failed_attempts.get(user_id, 0) + 1
    if failed_attempts[user_id] >= MAX_FAILED_ATTEMPTS:
        locked_users[user_id] = datetime.now()

async def execute_ssh_command(ssh_client: paramiko.SSHClient, command: str, timeout: int = 10) -> str:
    """Выполнение SSH команды с обработкой ошибок."""
    try:
        _, stdout, stderr = ssh_client.exec_command(command, timeout=timeout)
        error = stderr.read().decode().strip()
        if error:
            logger.warning(f"SSH команда вернула ошибку: {error}")
        return stdout.read().decode().strip()
    except Exception as e:
        logger.error(f"Ошибка выполнения '{command}': {e}")
        return "Неизвестно"

async def get_linux_system_info(ssh_client: paramiko.SSHClient) -> dict:
    """Сбор информации о Linux системе."""
    try:
        commands = {
            'cpu_load': "cat /proc/loadavg | awk '{print $1*100/$(nproc)}'",
            'ram_usage': "free -b | awk '/Mem:/ {printf \"%.1f|%.1f\", $3/1024/1024, $2/1024/1024}'",
            'disk_usage': "df -B1 / | awk 'NR==2 {printf \"%.1f|%.1f\", $3/1024/1024, $2/1024/1024}'",
            'os_info': "grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '\"'",
            'kernel': "uname -r",
            'cpu_info': "grep 'model name' /proc/cpuinfo | head -n 1 | cut -d: -f2 | xargs",
            'cpu_cores': "nproc"
        }

        system_data = {}
        for key, cmd in commands.items():
            system_data[key] = await execute_ssh_command(ssh_client, cmd)

        return {
            'Пользователь': await execute_ssh_command(ssh_client, 'whoami'),
            'Операционная система': system_data['os_info'],
            'Версия ОС': system_data['kernel'],
            'Процессор': system_data['cpu_info'],
            'Количество ядер': system_data['cpu_cores'],
            'Загрузка процессора': system_data['cpu_load'],
            'Использование ОЗУ': system_data['ram_usage'].split('|')[0],
            'Использование диска': system_data['disk_usage'].split('|')[0]
        }
    except Exception as e:
        logger.error(f"Ошибка сбора информации Linux: {e}")
        return {}

async def get_windows_system_info(ssh_client: paramiko.SSHClient) -> dict:
    """Сбор информации о Windows системе."""
    try:
        commands = {
            'cpu': 'powershell "$loadAvg=(Get-CimInstance Win32_Processor).LoadPercentage;$loadAvg"',
            'ram': 'powershell "$os=Get-CimInstance Win32_OperatingSystem;[math]::Round(($os.TotalVisibleMemorySize-$os.FreePhysicalMemory)/$os.TotalVisibleMemorySize*100,1)"',
            'disk': 'powershell "$disk=Get-PSDrive C;[math]::Round($disk.Used/($disk.Used+$disk.Free)*100,1)"',
            'os_info': 'powershell "(Get-CimInstance Win32_OperatingSystem).Caption"',
            'os_version': 'powershell "(Get-CimInstance Win32_OperatingSystem).Version"',
            'cpu_info': 'powershell "(Get-CimInstance Win32_Processor).Name"',
            'cpu_cores': 'powershell "(Get-CimInstance Win32_Processor).NumberOfLogicalProcessors"'
        }

        system_data = {}
        for key, cmd in commands.items():
            system_data[key] = await execute_ssh_command(ssh_client, cmd)

        return {
            'Пользователь': await execute_ssh_command(ssh_client, 'powershell "$env:USERNAME"'),
            'Операционная система': system_data['os_info'],
            'Версия ОС': system_data['os_version'],
            'Процессор': system_data['cpu_info'],
            'Количество ядер': system_data['cpu_cores'],
            'Загрузка процессора': system_data['cpu'],
            'Использование ОЗУ': system_data['ram'],
            'Использование диска': system_data['disk']
        }
    except Exception as e:
        logger.error(f"Ошибка сбора информации Windows: {e}")
        return {}

async def determine_os_type(ssh_client: paramiko.SSHClient) -> str:
    """Определение типа ОС."""
    try:
        output = await execute_ssh_command(ssh_client, 'ver')
        return "windows" if "windows" in output.lower() else "linux"
    except:
        return "linux"

async def get_system_info_ssh(hostname: str, port: int, username: str, password: str) -> dict:
    """Подключение и сбор информации о системе."""
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(
            hostname=hostname,
            port=port,
            username=username,
            password=password,
            timeout=30
        )

        os_type = await determine_os_type(ssh_client)
        system_data = (await get_windows_system_info(ssh_client) if os_type == "windows" 
                      else await get_linux_system_info(ssh_client))
        
        system_data.update({'IP-адрес': hostname, 'Порт SSH': port})
        
        ssh_client.close()
        return system_data
    except Exception as e:
        logger.error(f"Ошибка SSH подключения: {e}")
        return {}

def add_resource_charts(elements: list, system_data: dict):
    """Создание графиков использования ресурсов."""
    try:
        resources = [
            ('Загрузка процессора', float(system_data.get('Загрузка процессора', 0))),
            ('Использование ОЗУ', float(system_data.get('Использование ОЗУ', 0))),
            ('Использование диска', float(system_data.get('Использование диска', 0)))
        ]

        fig = Figure(figsize=(12, 4))
        colors = ['#FFB3BA', '#BAFFC9', '#BAE1FF']
        bg_colors = ['#FFE5E8', '#E8FFE5', '#E5F2FF']

        for idx, (title, value) in enumerate(resources):
            value = max(0, min(value, 100))  # Нормализация значений
            ax = fig.add_subplot(131 + idx)
            sizes = [value, 100 - value]
            
            ax.pie(sizes, colors=[colors[idx], bg_colors[idx]], 
                  startangle=90, autopct='%1.1f%%',
                  pctdistance=0.85,
                  wedgeprops={'edgecolor': 'white', 'linewidth': 1})
            ax.set_title(title, pad=20)

        fig.tight_layout(pad=3.0)
        
        buf = BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight', dpi=300)
        buf.seek(0)

        img = Image(buf, width=7*inch, height=2.3*inch)
        elements.append(img)
        elements.append(Spacer(1, 0.2*inch))
        
    except Exception as e:
        logger.error(f"Ошибка создания графиков: {e}")
        elements.append(Paragraph(
            "Не удалось создать графики использования ресурсов", 
            ParagraphStyle('Error', fontName=DEFAULT_FONT, fontSize=12, textColor=colors.red)
        ))

def generate_system_report_pdf(system_data=None):
    """
    Генерирует PDF-отчет о состоянии системы.
    
    Создает структурированный отчет, включающий:
    - Основную информацию о системе
    - Графики использования ресурсов
    - Подробные метрики работы
    
    Args:
        system_data: Словарь с данными о системе
        
    Returns:
        str: Путь к сгенерированному PDF-файлу или None при ошибке
    """
    try:
        moscow_tz = timezone(timedelta(hours=3))
        now = datetime.now(moscow_tz)
        months_ru = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
        }
        current_date = f"{now.day} {months_ru[now.month]} {now.year} года"
        filename_time = now.strftime("%Y%m%d_%H%M%S")
        
        logger.info("Начало генерации PDF-файла.")
        
        pdf_path = os.path.join(PDF_STORAGE_PATH, f"system_report_{filename_time}.pdf")
        
        doc = SimpleDocTemplate(pdf_path, pagesize=letter, encoding='utf-8')
        elements = []
        
        logger.info(f"Используем шрифт: {DEFAULT_FONT}")
        
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'CustomTitle',
            fontName=DEFAULT_FONT,
            fontSize=18,
            alignment=TA_CENTER,
            leading=22
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
        
        elements.append(Paragraph("Отчет о состоянии системы", title_style))
        elements.append(Spacer(1, 0.25*inch))
        elements.append(Paragraph(f"Сгенерировано: {current_date}", normal_style))
        elements.append(Spacer(1, 0.5*inch))
        
        elements.append(Paragraph("Основная информация", heading_style))  # Убрана лишняя скобка
        
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
        
        t = Table(system_data_list, colWidths=[2.5*inch, 4*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, -1), DEFAULT_FONT),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ]))
        
        elements.append(t)
        elements.append(Spacer(1, 0.5*inch))
        
        elements.append(Paragraph("Использование ресурсов", heading_style))
        elements.append(Spacer(1, 0.1*inch))
        
        add_resource_charts(elements, system_data)
        
        try:
            doc.build(elements)
            logger.info(f"PDF-файл '{pdf_path}' успешно создан.")
        except Exception as pdf_error:
            logger.error(f"Ошибка при сохранении PDF-файла: {pdf_error}")
        
        cleanup_old_pdfs()
        
        return pdf_path
    except Exception as e:
        logger.error(f"Ошибка при создании PDF: {e}", exc_info=True)
        return None

@dp.message_handler(commands=["start"])
async def start_command(message: types.Message):
    """Начальное приветствие и список команд."""
    await message.answer(BOT_MESSAGES['start'], parse_mode="Markdown")

@dp.message_handler(commands=["ssh"])
async def ssh_command(message: types.Message):
    """Запрос SSH данных у пользователя."""
    user_id = message.from_user.id
    
    if not check_rate_limit(user_id):
        remaining_time = int((LOCKOUT_TIME - (datetime.now() - locked_users[user_id]).total_seconds()) / 60)
        await message.answer(BOT_MESSAGES['rate_limit'].format(minutes=remaining_time))
        return
    
    if message.from_user.id in ssh_connections:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("Отменить", callback_data="cancel_ssh"))
        
        conn = ssh_connections[message.from_user.id]
        await message.answer(
            BOT_MESSAGES['ssh_exists'].format(
                user=conn['username'],
                host=conn['hostname']
            ),
            reply_markup=keyboard
        )
        return
    
    sent_msg = await message.answer(BOT_MESSAGES['ssh_prompt'])
    user_states[message.from_user.id] = {
        "state": "waiting_ssh",
        "message_id": sent_msg.message_id
    }

@dp.callback_query_handler(lambda c: c.data == 'cancel_ssh')
async def cancel_ssh(callback_query: types.CallbackQuery):
    """Обработка отмены существующего SSH подключения."""
    try:
        if callback_query.from_user.id in ssh_connections:
            del ssh_connections[callback_query.from_user.id]
        
        await callback_query.message.delete()
        
        sent_msg = await callback_query.message.answer(BOT_MESSAGES['ssh_prompt'])
        user_states[callback_query.from_user.id] = {
            "state": "waiting_ssh",
            "message_id": sent_msg.message_id
        }
        
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при отмене SSH подключения: {e}")
        await callback_query.message.answer("Произошла ошибка при отмене подключения")

@dp.message_handler(lambda message: isinstance(user_states.get(message.from_user.id), dict) and 
                   user_states.get(message.from_user.id, {}).get("state") == "waiting_ssh")
async def process_ssh_input(message: types.Message):
    """Обработка введенных SSH данных."""
    try:
        if '@' not in message.text:
            await message.answer("Неверный формат. Используйте формат: user@host")
            return

        username, hostname = message.text.split('@')
        
        if not is_host_allowed(hostname):
            await message.answer("⚠️ Подключение к этому хосту запрещено по соображениям безопасности.")
            return

        orig_message_id = user_states[message.from_user.id]["message_id"]
        
        user_states[message.from_user.id].update({
            "state": "waiting_password",
            "username": username,
            "hostname": hostname
        })
        
        await message.delete()
        
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=orig_message_id,
            text=f"Введите пароль для {username}@{hostname}:"
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке SSH данных: {e}")
        await message.answer("Произошла ошибка при обработке данных")
        user_states.pop(message.from_user.id, None)

@dp.message_handler(lambda message: isinstance(user_states.get(message.from_user.id), dict) and
                   user_states.get(message.from_user.id, {}).get("state") == "waiting_password")
async def process_password(message: types.Message):
    """Обработка введенного пароля и проверка подключения."""
    try:
        user_data = user_states[message.from_user.id]
        username = user_data["username"]
        hostname = user_data["hostname"]
        password = message.text

        await message.delete()

        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh_client.connect(
                hostname=hostname,
                username=username,
                password=password,
                timeout=10
            )
            ssh_client.close()
            
            failed_attempts[message.from_user.id] = 0
            
            ssh_connections[message.from_user.id] = {
                "hostname": hostname,
                "username": username,
                "password": password,
                "port": 22
            }
            
            await message.answer(BOT_MESSAGES['ssh_success'])
        except Exception as ssh_error:
            record_failed_attempt(message.from_user.id)
            logger.error(f"Ошибка SSH подключения: {ssh_error}")
            await message.answer(BOT_MESSAGES['ssh_error'])
    except Exception as e:
        logger.error(f"Ошибка при обработке пароля: {e}")
        await message.answer("Произошла ошибка при обработке данных")
    finally:
        user_states.pop(message.from_user.id, None)

@dp.message_handler(commands=["log"])
async def log_command(message: types.Message):
    """Генерация и отправка отчета о системе."""
    try:
        if message.from_user.id not in ssh_connections:
            await message.answer(BOT_MESSAGES['no_ssh'])
            return

        wait_message = await message.answer(BOT_MESSAGES['report_generating'])

        conn = ssh_connections[message.from_user.id]
        
        system_data = await get_system_info_ssh(
            conn["hostname"],
            conn.get("port", 22),
            conn["username"],
            conn["password"]
        )

        pdf_file = generate_system_report_pdf(system_data)
        if pdf_file and os.path.exists(pdf_file):
            with open(pdf_file, "rb") as file:
                await message.answer_document(file, caption="Отчет о системе")
                await wait_message.delete()
        else:
            await wait_message.edit_text(BOT_MESSAGES['report_error'])
            logger.error("Не удалось создать отчет. Проверьте логи.")
        
        if not monitor.is_monitoring(message.from_user.id):
            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("✅ Да", callback_data="monitor_start"),
                InlineKeyboardButton("❌ Нет", callback_data="monitor_cancel")
            )
            await message.answer(
                BOT_MESSAGES['monitoring_offer'],
                reply_markup=keyboard
            )
    except Exception as e:
        await message.answer("Произошла ошибка при выполнении команды. Проверьте логи.")
        logger.error(f"Ошибка при выполнении команды /log: {e}", exc_info=True)

@dp.callback_query_handler(lambda c: c.data.startswith('monitor_'))
async def process_monitor_callback(callback_query: types.CallbackQuery):
    """Обработка ответа на предложение мониторинга"""
    user_id = callback_query.from_user.id
    
    if callback_query.data == "monitor_start":
        if user_id in ssh_connections:
            started = await monitor.start_monitoring(user_id, ssh_connections[user_id])
            if started:
                await callback_query.message.edit_text(BOT_MESSAGES['monitoring_enabled'])
            else:
                await callback_query.message.edit_text(BOT_MESSAGES['monitoring_exists'])
        else:
            await callback_query.message.edit_text(BOT_MESSAGES['no_ssh'])
    else:
        await callback_query.message.edit_text(
            "👌 Хорошо, мониторинг не будет включен.\n"
            "Вы всегда можете включить его позже командой /start_monitor"
        )

@dp.message_handler(commands=["start_monitor"])
async def start_monitor_command(message: types.Message):
    """Команда для включения мониторинга"""
    user_id = message.from_user.id
    if user_id in ssh_connections:
        if await monitor.start_monitoring(user_id, ssh_connections[user_id]):
            await message.answer(BOT_MESSAGES['monitoring_enabled'])
        else:
            await message.answer(BOT_MESSAGES['monitoring_exists'])
    else:
        await message.answer(BOT_MESSAGES['no_ssh'])

@dp.message_handler(commands=["stop_monitor"])
async def stop_monitor_command(message: types.Message):
    """Команда для выключения мониторинга"""
    if await monitor.stop_monitoring(message.from_user.id):
        await message.answer(BOT_MESSAGES['monitoring_disabled'])
    else:
        await message.answer(BOT_MESSAGES['monitoring_not_running'])

if __name__ == "__main__":
    logger.info("Бот запущен")
    start_polling(dp, skip_updates=True)