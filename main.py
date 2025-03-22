import os
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import start_polling

# Получение токена из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# Хранение ID авторизованного пользователя
AUTHORIZED_USER_ID = None
AUTHORIZED_USERNAME = "someeeday"

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    global AUTHORIZED_USER_ID

    # Проверка логина пользователя
    if AUTHORIZED_USER_ID is None:
        if message.from_user.username == AUTHORIZED_USERNAME:
            AUTHORIZED_USER_ID = message.from_user.id
            await message.reply("Вы авторизованы. Бот готов к работе!")
        else:
            await message.reply("У вас нет доступа к этому боту.")
    elif message.from_user.id == AUTHORIZED_USER_ID:
        await message.reply("Бот уже запущен и готов к работе!")
    else:
        # Игнорируем других пользователей
        return

@dp.message_handler()
async def handle_message(message: types.Message):
    # Игнорируем сообщения от неавторизованных пользователей
    if message.from_user.id != AUTHORIZED_USER_ID:
        return

    # Обработка сообщений от авторизованного пользователя
    await message.reply("Ваше сообщение обработано.")

if __name__ == "__main__":
    start_polling(dp, skip_updates=True)
