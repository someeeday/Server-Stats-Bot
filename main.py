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

# Пути к внешним папкам
PDF_STORAGE_PATH = "/app-pdfs"
LOGS_PATH = "/app/logs"
FONTS_PATH = "./fonts"

# Настройки
MAX_FILES = 10
DEFAULT_FONT = 'DejaVuSans'
AUTHORIZED_USERNAME = 'someeeday'

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

def generate_system_report_pdf():
    """Генерирует PDF-отчет с пустыми данными и графиками."""
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
        
        # Создаем таблицу с пустыми данными
        system_data = [
            ["Параметр", "Значение"],
            ["Пользователь", "—"],
            ["Хост", "—"],
            ["Операционная система", "—"],
            ["Версия ОС", "—"],
            ["Процессор", "—"],
            ["Количество ядер", "—"],
            ["Оперативная память", "—"],
            ["Объем диска", "—"]
        ]
        
        # Создание таблицы
        t = Table(system_data, colWidths=[2.5*inch, 4*inch])
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

        # Создание и отправка отчета
        pdf_file = generate_system_report_pdf()
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