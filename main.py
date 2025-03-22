import os
from aiogram import Bot, Dispatcher, types # type: ignore
from aiogram.utils import executor # type: ignore

# Получение токена из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.reply("Бот успешно запущен и готов к работе!")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
