import os
import json
import logging
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для ConversationHandler
DATE, CATEGORY, AMOUNT, COMMENT = range(4)

# Константы для Google Sheets
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Авторизация Google Sheets через переменные окружения
def create_google_client():
    # Получаем значение переменной окружения
    google_creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not google_creds_json:
        raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
    
    try:
        # Прямая загрузка JSON из строки
        creds_info = json.loads(google_creds_json)
        
        # Исправление формата приватного ключа
        if 'private_key' in creds_info:
            # Удаляем лишние экранирования
            creds_info['private_key'] = creds_info['private_key'].replace('\\n', '\n')
            
            # Убедимся, что ключ начинается с корректного заголовка
            if not creds_info['private_key'].startswith('-----BEGIN PRIVATE KEY-----'):
                # Восстанавливаем формат PEM
                creds_info['private_key'] = (
                    "-----BEGIN PRIVATE KEY-----\n" +
                    creds_info['private_key'] +
                    "\n-----END PRIVATE KEY-----\n"
                )
        
        # Создаем Credentials объект напрямую
        creds = Credentials.from_service_account_info(creds_info)
        return gspread.authorize(creds)
    except json.JSONDecodeError:
        logger.error("Неверный формат JSON в GOOGLE_CREDENTIALS")
        raise
    except Exception as e:
        logger.error(f"Ошибка при загрузке Google credentials: {e}")
        raise

# Инициализация клиента
try:
    CLIENT = create_google_client()
    logger.info("✅ Google Sheets authorization successful")
except Exception as e:
    logger.error(f"❌ Google Sheets authorization failed: {e}")
    raise

# Глобальные переменные
SPREADSHEET_URL = None
SPREADSHEET = None
CATEGORIES = []


async def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    await update.message.reply_text(
        "💰 Бот для учета расходов\n\n"
        "Доступные команды:\n"
        "/add_expense - Добавить расход\n"
        "/set_sheet - Установить Google таблицу\n"
        "/help - Помощь"
    )

async def set_spreadsheet(update: Update, context: CallbackContext) -> None:
    """Установка Google таблицы"""
    global SPREADSHEET, SPREADSHEET_URL, CATEGORIES
    
    logger.info(f"Command received: {update.message.text}")
    
    try:
        # Используем контекст для получения аргументов
        if not context.args:
            await update.message.reply_text(
                "❌ Не указана ссылка на таблицу.\n"
                "Используйте: /set_sheet <ссылка_на_таблицу>"
            )
            return
            
        url = context.args[0]
        logger.info(f"Processing URL: {url}")
        
        # Извлечение ID таблицы с помощью регулярного выражения
        match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
        if not match:
            raise ValueError("Invalid Google Sheets URL format")
        
        spreadsheet_id = match.group(1)
        logger.info(f"Extracted spreadsheet ID: {spreadsheet_id}")
        
        # Открываем таблицу
        SPREADSHEET = CLIENT.open_by_key(spreadsheet_id)
        SPREADSHEET_URL = url
        
        # Загрузка категорий
        try:
            cat_sheet = SPREADSHEET.worksheet('cat')
            CATEGORIES = cat_sheet.col_values(1)
            
            # Автоматически определяем заголовок
            if CATEGORIES and ("category" in CATEGORIES[0].lower() or "категория" in CATEGORIES[0].lower()):
                CATEGORIES = CATEGORIES[1:]
                
            logger.info(f"Loaded {len(CATEGORIES)} categories")
        except gspread.exceptions.WorksheetNotFound:
            CATEGORIES = []
            logger.warning("Worksheet 'cat' not found")
        except gspread.exceptions.APIError as api_err:
            logger.error(f"Google Sheets API error: {api_err}")
            raise
        
        await update.message.reply_text(
            f"✅ Таблица установлена!\n"
            f"Ссылка: {url}\n"
            f"Загружено категорий: {len(CATEGORIES)}"
        )
        
    except ValueError as ve:
        logger.warning(f"Invalid URL: {str(ve)}")
        await update.message.reply_text(
            "❌ Неверная ссылка. Пример правильной ссылки:\n"
            "https://docs.google.com/spreadsheets/d/abc123xyz/edit\n\n"
            "Повторите команду: /set_sheet <ссылка>"
        )
    except gspread.exceptions.APIError as api_err:
        logger.error(f"Google Sheets API error: {api_err}")
        if api_err.response.status_code == 404:
            error_msg = ("⚠️ Таблица не найдена. Проверьте:\n"
                         "1. Правильность ссылки\n"
                         "2. Доступ сервисного аккаунта к таблице\n"
                         f"Ошибка: {str(api_err)}")
        else:
            error_msg = f"⚠️ Ошибка Google Sheets API: {str(api_err)}"
        await update.message.reply_text(error_msg)
    except Exception as e:
        logger.error(f"Ошибка при установке таблицы: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Произошла ошибка: {str(e)}")

async def start_add_expense(update: Update, context: CallbackContext) -> int:
    """Начало процесса добавления расхода"""
    global SPREADSHEET
    
    if not SPREADSHEET:
        await update.message.reply_text(
            "❌ Google таблица не установлена!\n"
            "Сначала выполните: /set_sheet <ссылка_на_таблицу>"
        )
        return ConversationHandler.END
    
    # Создаем клавиатуру для выбора даты
    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data="today")],
        [InlineKeyboardButton("Вчера", callback_data="yesterday")],
        [InlineKeyboardButton("Другая дата", callback_data="other")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📅 Выберите дату расхода:",
        reply_markup=reply_markup
    )
    return DATE

async def handle_date(update: Update, context: CallbackContext) -> int:
    """Обработка выбора даты"""
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    if choice == "today":
        selected_date = datetime.now().date()
    elif choice == "yesterday":
        selected_date = datetime.now().date() - timedelta(days=1)
    else:
        await query.edit_message_text("✏️ Введите дату в формате ДД.ММ.ГГГГ (например, 25.12.2023)")
        return DATE
    
    context.user_data['date'] = selected_date.strftime("%d.%m.%Y")
    await show_categories(query.message)
    return CATEGORY

async def show_categories(message) -> None:
    """Показать категории расходов"""
    global CATEGORIES
    
    if not CATEGORIES:
        await message.reply_text("ℹ️ Список категорий пуст. Добавьте категории на лист 'cat' вашей таблицы.")
        return
    
    keyboard = [[cat] for cat in CATEGORIES]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, 
        one_time_keyboard=True,
        resize_keyboard=True
    )
    
    await message.reply_text(
        "📁 Выберите категорию расхода:",
        reply_markup=reply_markup
    )

async def handle_category(update: Update, context: CallbackContext) -> int:
    """Обработка выбора категории"""
    category = update.message.text
    if category not in CATEGORIES:
        await update.message.reply_text("❌ Категория не найдена. Выберите из списка:")
        await show_categories(update.message)
        return CATEGORY
    
    context.user_data['category'] = category
    await update.message.reply_text(
        "💵 Введите сумму расхода (только цифры):",
        reply_markup=ReplyKeyboardRemove()
    )
    return AMOUNT

async def handle_amount(update: Update, context: CallbackContext) -> int:
    """Обработка ввода суммы"""
    try:
        amount = float(update.message.text.replace(',', '.'))
        if amount <= 0:
            raise ValueError
        context.user_data['amount'] = amount
        await update.message.reply_text(
            "📝 Введите комментарий (или нажмите /skip чтобы пропустить):"
        )
        return COMMENT
    except ValueError:
        await update.message.reply_text("❌ Неверный формат суммы. Введите число (например: 1500.50):")
        return AMOUNT

async def handle_comment(update: Update, context: CallbackContext) -> int:
    """Обработка комментария"""
    context.user_data['comment'] = update.message.text
    return await save_expense(update, context)

async def skip_comment(update: Update, context: CallbackContext) -> int:
    """Пропуск комментария"""
    context.user_data['comment'] = ""
    return await save_expense(update, context)

async def save_expense(update: Update, context: CallbackContext) -> int:
    """Сохранение расхода в Google Sheets"""
    user_data = context.user_data
    try:
        # Проверяем наличие листа расходов
        try:
            exp_sheet = SPREADSHEET.worksheet('exp')
        except gspread.exceptions.WorksheetNotFound:
            # Создаем лист, если он не существует
            exp_sheet = SPREADSHEET.add_worksheet(title='exp', rows=100, cols=4)
            exp_sheet.append_row(['Date', 'Category', 'Sum', 'Comment'])
        
        # Подготавливаем данные
        row = [
            user_data['date'],
            user_data['category'],
            user_data['amount'],
            user_data.get('comment', '')
        ]
        
        # Добавляем новую строку
        exp_sheet.append_row(row)
        
        await update.message.reply_text("✅ Расход успешно сохранен!")
    except gspread.exceptions.APIError as api_err:
        logger.error(f"Google Sheets API error: {api_err}")
        await update.message.reply_text("⚠️ Ошибка при сохранении в таблицу. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при сохранении.")
    finally:
        context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена операции"""
    await update.message.reply_text(
        "❌ Операция отменена",
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    """Запуск бота"""
    application = Application.builder().token(TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("set_sheet", set_spreadsheet))
    
    # Обработчик добавления расходов
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_expense", start_add_expense)],
        states={
            DATE: [
                CallbackQueryHandler(handle_date, pattern="^(today|yesterday|other)$")
            ],
            CATEGORY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)
            ],
            AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)
            ],
            COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment),
                CommandHandler("skip", skip_comment)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)
    
    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
