import os
import json
import logging
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
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Жестко заданный ID таблицы
SPREADSHEET_ID = "12Mjnj2wwVDYZcNMzzZG6FC-qG29lFtdigDFOEHC6590"

# Авторизация Google Sheets через переменные окружения
def create_google_client():
    google_creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not google_creds_json:
        raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
    
    try:
        # Загружаем JSON без дополнительной обработки
        creds_info = json.loads(google_creds_json)
        
        # Создаем Credentials с явным указанием SCOPES
        creds = Credentials.from_service_account_info(
            creds_info, 
            scopes=SCOPES
        )
        return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        # Для диагностики логируем часть ключа
        if 'private_key' in creds_info:
            logger.info(f"Private key start: {creds_info['private_key'][:50]}")
        raise

# Инициализация клиента и таблицы
try:
    CLIENT = create_google_client()
    logger.info("✅ Google Sheets authorization successful")
    
    # Открываем таблицу по жестко заданному ID
    SPREADSHEET = CLIENT.open_by_key(SPREADSHEET_ID)
    logger.info(f"✅ Spreadsheet loaded: {SPREADSHEET.title}")
    
    # Загрузка категорий
    try:
        cat_sheet = SPREADSHEET.worksheet('cat')
        CATEGORIES = cat_sheet.col_values(1)
        if CATEGORIES and CATEGORIES[0].lower() == "category":
            CATEGORIES = CATEGORIES[1:]  # Пропуск заголовка
        logger.info(f"Loaded {len(CATEGORIES)} categories")
    except gspread.WorksheetNotFound:
        CATEGORIES = []
        logger.warning("Worksheet 'cat' not found")
        
except Exception as e:
    logger.error(f"❌ Initialization failed: {e}")
    raise

# Глобальные переменные
CATEGORIES = CATEGORIES if CATEGORIES else []

async def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    await update.message.reply_text(
        "💰 Бот для учета расходов\n\n"
        "Доступные команды:\n"
        "/add_expense - Добавить расход\n"
        "/help - Помощь\n\n"
        f"Используется таблица: {SPREADSHEET.title}"
    )

async def start_add_expense(update: Update, context: CallbackContext) -> int:
    """Начало процесса добавления расхода"""
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
        # Получаем лист для расходов
        exp_sheet = SPREADSHEET.worksheet('exp')
        
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
    except Exception as e:
        logger.error(f"Ошибка при сохранении: {e}")
        await update.message.reply_text("⚠️ Ошибка при сохранении в таблицу. Попробуйте позже.")
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
