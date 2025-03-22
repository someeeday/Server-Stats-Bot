import os
import logging
import platform
import subprocess
from aiogram import Bot, Dispatcher, types  # type: ignore
from aiogram.utils.executor import start_polling  # type: ignore
from fpdf import FPDF  # type: ignore
import psutil  # type: ignore
from datetime import datetime, timezone, timedelta
import paramiko
import socket

# Пути к внешним папкам
PDF_STORAGE_PATH = "/app-pdfs"
LOGS_PATH = "/app/logs"

# Убедимся, что папка для логов существует
if not os.path.exists(LOGS_PATH):
    try:
        os.makedirs(LOGS_PATH)
    except Exception as e:
        print(f"Ошибка при создании папки для логов: {e}")

# Настройка логирования
LOG_FILE = os.path.join(LOGS_PATH, "debug.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levellevelname)s - %(message)s',  # Исправлено с levellevelname
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("server-stats-bot")

# Получение настроек из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logger.error("Переменная окружения BOT_TOKEN не задана!")
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

# Хранение имени авторизованного пользователя
AUTHORIZED_USERNAME = os.getenv("AUTHORIZED_USERNAME", "someeeday")

# Настройки подключения к хост-системе
HOST_IP = os.getenv("HOST_IP", "host.docker.internal")
SSH_USERNAME = os.getenv("SSH_USERNAME", "")
SSH_PASSWORD = os.getenv("SSH_PASSWORD", "")
SSH_PORT = int(os.getenv("SSH_PORT", "22"))

# Другие настройки
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "30"))
MAX_FILES = int(os.getenv("MAX_FILES", "10"))

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Убедимся, что папка для хранения PDF существует
if not os.path.exists(PDF_STORAGE_PATH):
    try:
        os.makedirs(PDF_STORAGE_PATH)
        logger.info(f"Создана папка для хранения PDF: {PDF_STORAGE_PATH}")
    except Exception as e:
        logger.error(f"Ошибка при создании папки {PDF_STORAGE_PATH}: {e}")

def cleanup_old_pdfs():
    """Удаляет старые файлы, оставляя только последние MAX_FILES."""
    files = sorted(
        [os.path.join(PDF_STORAGE_PATH, f) for f in os.listdir(PDF_STORAGE_PATH)],
        key=os.path.getmtime
    )
    if len(files) > MAX_FILES:
        for file in files[:-MAX_FILES]:
            os.remove(file)
            logger.info(f"Удален старый файл: {file}")

def get_host_ip():
    """Определяет IP-адрес хоста."""
    return HOST_IP if HOST_IP else "host.docker.internal"

def get_system_info_ssh():
    """Получает информацию о системе через SSH."""
    host_ip = get_host_ip()
    if not host_ip:
        logger.error("Не удалось определить IP-адрес хоста")
        return None
    
    logger.info(f"Попытка подключения к хосту по SSH: {host_ip}:{SSH_PORT}")
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Пробуем подключиться с паролем или ключом
        if SSH_PASSWORD:
            ssh.connect(host_ip, port=SSH_PORT, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=10)
        elif SSH_KEY_PATH:
            ssh.connect(host_ip, port=SSH_PORT, username=SSH_USERNAME, key_filename=SSH_KEY_PATH, timeout=10)
        else:
            logger.error("Не указаны учетные данные для SSH подключения")
            return None
        
        # Получаем информацию о системе
        system_info = {}
        
        # Определяем ОС
        stdin, stdout, stderr = ssh.exec_command("uname -s")
        os_type = stdout.read().decode().strip()
        
        if os_type == "Linux":
            # Команды для Linux
            stdin, stdout, stderr = ssh.exec_command("free -m | grep Mem")
            memory_output = stdout.read().decode()
            
            stdin, stdout, stderr = ssh.exec_command("top -bn1 | grep load | awk '{printf \"%.2f\", $(NF-2)}'")
            cpu_usage = stdout.read().decode().strip()
            
            # Парсинг данных
            memory_values = memory_output.split()
            if len(memory_values) >= 4:
                system_info["total_memory"] = int(memory_values[1])
                system_info["used_memory"] = int(memory_values[2])
                system_info["free_memory"] = int(memory_values[3])
                system_info["cpu_usage"] = cpu_usage
        
        elif os_type == "Darwin":  # macOS
            # Команды для macOS
            stdin, stdout, stderr = ssh.exec_command("sysctl hw.memsize")
            mem_total = int(stdout.read().decode().split()[1]) // (1024 * 1024)
            
            stdin, stdout, stderr = ssh.exec_command("vm_stat | grep 'Pages free:'")
            page_size = 4096  # Размер страницы по умолчанию в macOS
            free_pages = int(stdout.read().decode().split()[2].replace('.', ''))
            mem_free = (free_pages * page_size) // (1024 * 1024)
            
            stdin, stdout, stderr = ssh.exec_command("top -l 1 | grep 'CPU usage'")
            cpu_line = stdout.read().decode()
            cpu_usage = cpu_line.split(': ')[1].split('%')[0]
            
            system_info["total_memory"] = mem_total
            system_info["free_memory"] = mem_free
            system_info["used_memory"] = mem_total - mem_free
            system_info["cpu_usage"] = cpu_usage
        
        else:  # Windows или другая ОС
            # Для Windows через SSH (если установлен OpenSSH Server)
            stdin, stdout, stderr = ssh.exec_command("systeminfo | findstr \"Total Physical Memory\"")
            mem_line = stdout.read().decode()
            if mem_line:
                mem_total = int(mem_line.split(':')[1].strip().replace(',', '').split()[0])
                system_info["total_memory"] = mem_total
            
            stdin, stdout, stderr = ssh.exec_command("systeminfo | findstr \"Available Physical Memory\"")
            mem_free_line = stdout.read().decode()
            if mem_free_line:
                mem_free = int(mem_free_line.split(':')[1].strip().replace(',', '').split()[0])
                system_info["free_memory"] = mem_free
                system_info["used_memory"] = mem_total - mem_free
            
            stdin, stdout, stderr = ssh.exec_command("wmic cpu get loadpercentage")
            cpu_output = stdout.read().decode()
            if cpu_output:
                cpu_lines = cpu_output.strip().split('\n')
                if len(cpu_lines) >= 2:
                    cpu_usage = cpu_lines[1].strip()
                    system_info["cpu_usage"] = cpu_usage
        
        ssh.close()
        return system_info
    
    except Exception as e:
        logger.error(f"Ошибка при получении информации о системе через SSH: {e}")
        return None

def get_system_info():
    """
    Получает информацию о системе в зависимости от способа доступа.
    """
    system = platform.system()
    logger.info(f"Определена операционная система: {system}")
    
    system_info = {}
    
    try:
        # Общая информация, доступная на всех платформах через psutil
        memory = psutil.virtual_memory()
        total_memory = memory.total // (1024 * 1024)  # в MB
        used_memory = memory.used // (1024 * 1024)  # в MB
        free_memory = memory.available // (1024 * 1024)  # в MB
        cpu_usage = psutil.cpu_percent(interval=1)
        
        system_info["psutil"] = {
            "total_memory": total_memory,
            "used_memory": used_memory,
            "free_memory": free_memory,
            "cpu_usage": cpu_usage
        }
        
        # Проверка на WSL
        is_wsl = False
        try:
            with open("/proc/version", "r") as f:
                if "microsoft" in f.read().lower():
                    is_wsl = True
                    logger.info("Обнаружена WSL")
        except:
            pass
        
        # Если мы в WSL, пытаемся получить информацию о хост-системе Windows
        if is_wsl:
            try:
                # Используем WSL-specific способ для получения доступа к хосту
                # Пробуем PowerShell через wsl.exe
                powershell_cmd = "powershell.exe -Command \"Get-CimInstance Win32_ComputerSystem | Select-Object TotalPhysicalMemory\""
                output = subprocess.check_output(powershell_cmd, shell=True).decode('utf-8')
                
                # Пример вывода:
                # TotalPhysicalMemory
                # -------------------
                #       34226671616
                
                memory_lines = output.strip().split('\n')
                if len(memory_lines) >= 3:
                    win_total_memory = int(memory_lines[2].strip()) // (1024 * 1024)  # bytes to MB
                
                # Получаем информацию о свободной памяти
                memory_cmd = "powershell.exe -Command \"Get-Counter '\\Memory\\Available MBytes'\""
                memory_output = subprocess.check_output(memory_cmd, shell=True).decode('utf-8')
                
                # Парсим вывод для получения доступной памяти
                import re
                win_free_memory = re.search(r'(\d+)', memory_output)
                if win_free_memory:
                    win_free_memory = int(win_free_memory.group(1))
                    win_used_memory = win_total_memory - win_free_memory
                
                # Получаем загрузку CPU
                cpu_cmd = "powershell.exe -Command \"Get-Counter '\\Processor(_Total)\\% Processor Time'\""
                cpu_output = subprocess.check_output(cpu_cmd, shell=True).decode('utf-8')
                
                win_cpu_usage = re.search(r'(\d+\.\d+)', cpu_output)
                if win_cpu_usage:
                    win_cpu_usage = float(win_cpu_usage.group(1))
                
                system_info["host_windows"] = {
                    "total_memory": win_total_memory,
                    "free_memory": win_free_memory,
                    "used_memory": win_used_memory,
                    "cpu_usage": win_cpu_usage
                }
            except Exception as e:
                logger.error(f"Ошибка при получении информации о хост-системе Windows из WSL: {e}")
                system_info["host_windows_error"] = str(e)
        
        # Специфичная для Linux информация
        if system == "Linux":
            try:
                # Используем команды Linux
                memory_cmd = "free -m | grep Mem"
                memory_output = subprocess.check_output(memory_cmd, shell=True).decode('utf-8')
                
                # Получение загрузки CPU
                load_cmd = "top -bn1 | grep load | awk '{printf \"%.2f\", $(NF-2)}'"
                linux_cpu_usage = subprocess.check_output(load_cmd, shell=True).decode('utf-8').strip()
                
                # Парсинг выходных данных
                memory_values = memory_output.split()
                if len(memory_values) >= 4:
                    linux_total_memory = int(memory_values[1])
                    linux_used_memory = int(memory_values[2])
                    linux_free_memory = int(memory_values[3])
                
                system_info["linux"] = {
                    "total_memory": linux_total_memory,
                    "used_memory": linux_used_memory,
                    "free_memory": linux_free_memory,
                    "cpu_usage": linux_cpu_usage
                }
            except Exception as e:
                logger.error(f"Ошибка при получении информации Linux: {e}")
                system_info["linux_error"] = str(e)
    
    except Exception as e:
        logger.error(f"Ошибка при получении общей информации о системе: {e}")
        system_info["error"] = str(e)
    
    # Пробуем получить информацию через SSH
    ssh_info = get_system_info_ssh()
    if ssh_info:
        system_info["ssh"] = ssh_info
    
    return system_info

def generate_pdf():
    """Генерирует PDF-файл с информацией о системе."""
    try:
        logger.info("Начало генерации PDF-файла.")

        # Проверка существования папки
        if not os.path.exists(PDF_STORAGE_PATH):
            try:
                os.makedirs(PDF_STORAGE_PATH, exist_ok=True)
                logger.info(f"Папка {PDF_STORAGE_PATH} успешно создана.")
            except Exception as e:
                logger.error(f"Ошибка при создании папки {PDF_STORAGE_PATH}: {e}")
                raise

        # Получение текущего времени с учетом часового пояса (Москва)
        moscow_tz = timezone(timedelta(hours=3))  # UTC+3
        current_time = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")
        filename_time = datetime.now(moscow_tz).strftime("%Y%m%d_%H%M%S")
        logger.info(f"Текущее время (Москва): {current_time}")

        # Создание PDF
        try:
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            pdf.cell(200, 10, txt="System Information Report", ln=True, align="C")
            pdf.ln(10)
            pdf.cell(200, 10, txt=f"Generated on: {current_time} (Moscow time)", ln=True)
            
            # Получение информации о системе
            system_info = get_system_info()
            if not system_info:
                pdf.ln(10)
                pdf.cell(200, 10, txt="Failed to get system information.", ln=True)
            else:
                # Информация от psutil (работает на всех платформах)
                if "psutil" in system_info:
                    pdf.ln(10)
                    pdf.cell(200, 10, txt="Container System Statistics:", ln=True)
                    info = system_info["psutil"]
                    pdf.cell(200, 10, txt=f"Total Memory: {info['total_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Used Memory: {info['used_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Free Memory: {info['free_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"CPU Usage: {info['cpu_usage']}%", ln=True)
                
                # Информация о хост-системе Windows из WSL
                if "host_windows" in system_info:
                    pdf.ln(10)
                    pdf.cell(200, 10, txt="Host Windows System Statistics (from WSL):", ln=True)
                    info = system_info["host_windows"]
                    pdf.cell(200, 10, txt=f"Total Memory: {info['total_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Used Memory: {info['used_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Free Memory: {info['free_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"CPU Usage: {info['cpu_usage']}%", ln=True)
                elif "host_windows_error" in system_info:
                    pdf.ln(10)
                    pdf.cell(200, 10, txt="Host Windows System Statistics Error:", ln=True)
                    pdf.cell(200, 10, txt=system_info["host_windows_error"], ln=True)
                
                # Информация Linux (если доступна)
                if "linux" in system_info:
                    pdf.ln(10)
                    pdf.cell(200, 10, txt="Linux System Statistics:", ln=True)
                    info = system_info["linux"]
                    pdf.cell(200, 10, txt=f"Total Memory: {info['total_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Used Memory: {info['used_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Free Memory: {info['free_memory']} MB", ln=True)
                    pdf.cell(200, 10, txt=f"CPU Load: {info['cpu_usage']}", ln=True)
                elif "linux_error" in system_info:
                    pdf.ln(10)
                    pdf.cell(200, 10, txt="Linux System Statistics Error:", ln=True)
                    pdf.cell(200, 10, txt=system_info["linux_error"], ln=True)
                
                # Информация, полученная через SSH
                if "ssh" in system_info:
                    pdf.ln(10)
                    pdf.cell(200, 10, txt="Host System Statistics (SSH):", ln=True)
                    info = system_info["ssh"]
                    pdf.cell(200, 10, txt=f"Total Memory: {info.get('total_memory', 'N/A')} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Used Memory: {info.get('used_memory', 'N/A')} MB", ln=True)
                    pdf.cell(200, 10, txt=f"Free Memory: {info.get('free_memory', 'N/A')} MB", ln=True)
                    pdf.cell(200, 10, txt=f"CPU Usage: {info.get('cpu_usage', 'N/A')}%", ln=True)
            
            logger.info("PDF объект создан в памяти.")
        except Exception as e:
            logger.error(f"Ошибка при создании PDF объекта: {e}")
            raise

        # Сохранение PDF в целевую папку
        pdf_file = os.path.join(PDF_STORAGE_PATH, f"system_stats_{filename_time}.pdf")
        try:
            pdf.output(pdf_file)