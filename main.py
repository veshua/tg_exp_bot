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

# Жестко заданный ID Google таблицы
SPREADSHEET_ID = "12Mjnj2wwVDYZcNMzzZG6FC-qG29IFtdigDFOEHC6590"

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

# Глобальная клавиатура
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[[ "➕ Добавить расход" ]],
    resize_keyboard=True,
    one_time_keyboard=False
)

def create_google_client():
    google_creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not google_creds_json:
        raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
    try:
        creds_info = json.loads(google_creds_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
        return gspread.authorize(creds)
    except json.JSONDecodeError:
        logger.error("Неверный формат JSON в GOOGLE_CREDENTIALS")
        raise
    except Exception as e:
        logger.error(f"Ошибка при загрузке Google credentials: {e}")
        raise

try:
    CLIENT = create_google_client()
    logger.info("✅ Google Sheets authorization successful")
except Exception as e:
    logger.error(f"❌ Google Sheets authorization failed: {e}")
    raise

SPREADSHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
SPREADSHEET = None
CATEGORIES = []

def initialize_spreadsheet():
    global SPREADSHEET, CATEGORIES
    try:
        SPREADSHEET = CLIENT.open_by_key(SPREADSHEET_ID)
        logger.info(f"✅ Spreadsheet initialized: {SPREADSHEET_URL}")
        try:
            cat_sheet = SPREADSHEET.worksheet('cat')
            CATEGORIES = cat_sheet.col_values(1)
            if CATEGORIES and ("category" in CATEGORIES[0].lower() or "категория" in CATEGORIES[0].lower()):
                CATEGORIES = CATEGORIES[1:]
            logger.info(f"Loaded {len(CATEGORIES)} categories")
        except gspread.exceptions.WorksheetNotFound:
            CATEGORIES = []
            logger.warning("Worksheet 'cat' not found")
        except gspread.exceptions.APIError as api_err:
            logger.error(f"Google Sheets API error: {api_err}")
            raise
    except gspread.exceptions.APIError as api_err:
        logger.error(f"Google Sheets API error: {api_err}")
        raise
    except Exception as e:
        logger.error(f"Ошибка при инициализации таблицы: {e}", exc_info=True)
        raise

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "💰 Бот для учета расходов\n\n"
        "Доступные команды:\n"
        "/add_expense - Добавить расход\n"
        "/help - Помощь\n\n"
        f"Используется таблица:\n{SPREADSHEET_URL}",
        reply_markup=main_keyboard
    )

async def start_add_expense(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data="today")],
        [InlineKeyboardButton("Вчера", callback_data="yesterday")],
        [InlineKeyboardButton("Другая дата", callback_data="other")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📅 Выберите дату расхода:", reply_markup=reply_markup)
    return DATE

async def handle_date(update: Update, context: CallbackContext) -> int:
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
    await message.reply_text("📁 Выберите категорию расхода:", reply_markup=reply_markup)

async def handle_category(update: Update, context: CallbackContext) -> int:
    category = update.message.text
    if category not in CATEGORIES:
        await update.message.reply_text("❌ Категория не найдена. Выберите из списка:")
        await show_categories(update.message)
        return CATEGORY
    context.user_data['category'] = category
    await update.message.reply_text("💵 Введите сумму расхода (только цифры):", reply_markup=ReplyKeyboardRemove())
    return AMOUNT

async def handle_amount(update: Update, context: CallbackContext) -> int:
    try:
        amount = float(update.message.text.replace(',', '.'))
        if amount <= 0:
            raise ValueError
        context.user_data['amount'] = amount
        await update.message.reply_text("📝 Введите комментарий (или нажмите /skip чтобы пропустить):")
        return COMMENT
    except ValueError:
        await update.message.reply_text("❌ Неверный формат суммы. Введите число (например: 1500.50):")
        return AMOUNT

async def handle_comment(update: Update, context: CallbackContext) -> int:
    context.user_data['comment'] = update.message.text
    return await save_expense(update, context)

async def skip_comment(update: Update, context: CallbackContext) -> int:
    context.user_data['comment'] = ""
    return await save_expense(update, context)

async def save_expense(update: Update, context: CallbackContext) -> int:
    user_data = context.user_data
    try:
        try:
            exp_sheet = SPREADSHEET.worksheet('exp')
        except gspread.exceptions.WorksheetNotFound:
            exp_sheet = SPREADSHEET.add_worksheet(title='exp', rows=100, cols=4)
            exp_sheet.append_row(['Date', 'Category', 'Sum', 'Comment'])
        row = [
            user_data['date'],
            user_data['category'],
            user_data['amount'],
            user_data.get('comment', '')
        ]
        exp_sheet.append_row(row)
        await update.message.reply_text("✅ Расход успешно сохранен!", reply_markup=main_keyboard)
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
    await update.message.reply_text("❌ Операция отменена", reply_markup=main_keyboard)
    context.user_data.clear()
    return ConversationHandler.END

# Обработка текстовой кнопки "➕ Добавить расход"
async def add_expense_button_handler(update: Update, context: CallbackContext):
    return await start_add_expense(update, context)

def main() -> None:
    try:
        initialize_spreadsheet()
    except Exception as e:
        logger.critical(f"❌ Не удалось инициализировать таблицу. Бот остановлен. Ошибка: {e}")
        return

    application = Application.builder().token(TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))

    # Кнопка "➕ Добавить расход"
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'^➕ Добавить расход$'),
        add_expense_button_handler
    ))

    # Добавление расхода
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_expense", start_add_expense)],
        states={
            DATE: [CallbackQueryHandler(handle_date, pattern="^(today|yesterday|other)$")],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
            COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment),
                CommandHandler("skip", skip_comment)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(conv_handler)

    application.run_polling()

if __name__ == "__main__":
    main()
