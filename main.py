import discord
import logging
import sqlite3
from pyunsplash import PyUnsplash
import requests

# Настройка логгирования для отладки
logger = logging.getLogger("discord")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
)
logger.addHandler(handler)

# Импорт токена Discord и ключа доступа Unsplash из файла config.py
from config import TOKEN, UNSPLASH_ACCESS_KEY

# Функция для перевода текста на указанный язык с помощью публичного API Google Translate.
# По умолчанию переводит на английский язык.
def translate_text(text, target_lang="en"):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        # Автоматическое определение исходного языка
        "sl": "auto",
        # Язык, на который нужно перевести
        "tl": target_lang,
        # Тип данных - текст
        "dt": "t",
        # Текст для перевода
        "q": text,
    }
    response = requests.get(url, params=params)
    # Если запрос выполнен успешно
    if response.status_code == 200:
        try:
            # Извлечение переведенного текста из ответа
            translation = response.json()[0][0][0]
            return translation
        # Если ответ не соответствует ожидаемому формату
        except (IndexError, KeyError):
            # Возвращаем исходный текст
            return text
    else:
        # Возвращаем исходный текст в случае ошибки
        return text

class YLBotClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Инициализация клиента Unsplash
        self.unsplash = PyUnsplash(api_key=UNSPLASH_ACCESS_KEY)
        # Подключение к базе данных SQLite
        self.conn = sqlite3.connect("image_database.db")
        self.cursor = self.conn.cursor()
        # Создание таблицы для хранения ссылок на отправленные изображения для каждого пользователя, если она не существует
        self.cursor.execute("CREATE TABLE IF NOT EXISTS user_images (user_id INTEGER, url TEXT)")
        # Список для хранения результатов поиска изображений
        self.search_results = []
        # Индекс текущего изображения в списке результатов
        self.current_index = 0
        # Количество изображений для отправки по умолчанию
        self.image_count = 1
        # Последний запрос на поиск изображений
        self.last_query = None

    # Метод, вызываемый при успешном подключении бота к Discord.
    # Выводит информацию о подключении в логи.
    async def on_ready(self):
        logger.info(f"{self.user} has connected to Discord!")

        for guild in self.guilds:
            logger.info(
                f"{self.user} подключились к чату:\n" f"{guild.name}(id: {guild.id})"
            )

        # Отправляем приветственное сообщение с описанием функционала бота
        await self.send_help_message()

    # Метод для отправки сообщения с описанием функционала бота
    async def send_help_message(self):
        help_message = "**Функционал бота:**\n" \
                       "!search <запрос> - Найти и отправить изображения по запросу\n" \
                       "!next - Отправить следующие изображения по предыдущему запросу\n" \
                       "!settings <количество> - Установить количество отправляемых изображений (по умолчанию 1)\n" \
                       "!help - Показать это сообщение"

        for guild in self.guilds:
            for channel in guild.text_channels:
                await channel.send(help_message)

    # Метод, вызываемый при получении нового сообщения в Discord.
    # Обрабатывает команды !search, !next, !settings и !help.
    async def on_message(self, message):
        # Игнорируем сообщения, отправленные самим ботом
        if message.author == self.user:
            return

        if message.content.startswith("!search"):
            # Извлечение запроса из сообщения
            query = message.content[8:]

            # Определение языка запроса
            source_language = translate_text(query, "en")

            # Перевод запроса на английский язык, если он не на английском
            if source_language != query:
                translated_query = source_language

            else:
                translated_query = query

            if translated_query:
                # Выполнение поиска изображений на Unsplash с переведенным запросом
                search = self.unsplash.search(type_="photos", query=translated_query)
                self.search_results = [photo for photo in search.entries]
                self.current_index = 0
                self.last_query = translated_query  # Сохранение последнего запроса
                # Отправка изображений
                await self.send_next_images(message)

            else:
                await message.channel.send(
                    "Введите свой запрос после команды '!search'."
                )

        elif message.content.startswith("!next"):
            # Если есть результаты предыдущего поиска
            if self.search_results:
                # Отправка следующих изображений по последнему запросу
                await self.send_next_images(message)

            else:
                await message.channel.send("Нет результатов предыдущего поиска.")

        elif message.content.startswith("!settings"):
            # Извлечение количества изображений из сообщения
            try:
                image_count = int(message.content[10:])
                self.image_count = image_count
                await message.channel.send(f"Количество отправляемых изображений установлено на {image_count}")

            except ValueError:
                await message.channel.send("Некорректное значение количества изображений. Используйте целое число.")

        elif message.content.startswith("!help"):
            # Отправка сообщения с описанием функционала бота
            await self.send_help_message()

    # Метод для отправки указанного количества изображений из результатов поиска.
    async def send_next_images(self, message):
        # Проверяем, есть ли неотправленные изображения
        if self.current_index < len(self.search_results):
            for _ in range(self.image_count):
                # Вызываем метод send_next_image для отправки каждого изображения
                await self.send_next_image(message)

        else:
            await message.channel.send("Нет изображений по данному запросу.")

    # Метод для отправки следующего изображения из результатов поиска.
    # Проверяет, не было ли это изображение отправлено ранее этому пользователю, и добавляет его ссылку в базу данных.
    async def send_next_image(self, message):
        # Если есть неотправленные изображения
        if self.current_index < len(self.search_results):
            photo = self.search_results[self.current_index]
            # Проверка, было ли изображение отправлено этому пользователю ранее
            self.cursor.execute(
                "SELECT url FROM user_images WHERE user_id = ? AND url = ?",
                (message.author.id, photo.link_download),
            )
            # Если изображение не было отправлено этому пользователю ранее
            if not self.cursor.fetchone():
                # Добавление ссылки на изображение в базу данных
                self.cursor.execute(
                    "INSERT INTO user_images VALUES (?, ?)",
                    (message.author.id, photo.link_download),
                )
                self.conn.commit()
                # Отправка изображения в канал Discord
                await message.channel.send(photo.link_download)
                # Переход к следующему изображению в списке
                self.current_index += 1
            else:
                # Переход к следующему изображению в списке
                self.current_index += 1
                # Рекурсивный вызов метода для отправки следующего изображения
                await self.send_next_image(message)

# Создание интентов Discord и инициализация клиента
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = YLBotClient(intents=intents)

# Запуск бота с использованием токена Discord
client.run(TOKEN)
