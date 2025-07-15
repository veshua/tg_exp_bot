import os
import json
import logging
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
SPREADSHEET_ID = "12Mjnj2wwVDYZcNMzzZG6FC-qG29IFtdigDFOEHC6590"

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
DATE, CATEGORY, AMOUNT, COMMENT = range(4)

# Авторизация Google Sheets
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

def create_google_client():
    google_creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not google_creds_json:
        raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
    creds_info = json.loads(google_creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)

try:
    CLIENT = create_google_client()
except Exception as e:
    logger.error(f"Google Sheets auth failed: {e}")
    raise

SPREADSHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
SPREADSHEET = None
CATEGORIES = []

def initialize_spreadsheet():
    global SPREADSHEET, CATEGORIES
    SPREADSHEET = CLIENT.open_by_key(SPREADSHEET_ID)
    try:
        cat_sheet = SPREADSHEET.worksheet('cat')
        CATEGORIES = cat_sheet.col_values(1)
        if CATEGORIES and ("category" in CATEGORIES[0].lower() or "категория" in CATEGORIES[0].lower()):
            CATEGORIES = CATEGORIES[1:]
    except:
        CATEGORIES = []

def default_keyboard():
    return ReplyKeyboardMarkup([["➕ Добавить расход"]], resize_keyboard=True)

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "💰 Бот для учета расходов\n\n"
        "Доступные команды:\n"
        "/add_expense - Добавить расход\n"
        "/help - Помощь\n\n"
        f"Используется таблица:\n{SPREADSHEET_URL}",
        reply_markup=default_keyboard()
    )

async def start_add_expense(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data="today")],
        [InlineKeyboardButton("Вчера", callback_data="yesterday")],
        [InlineKeyboardButton("Другая дата", callback_data="other")],
    ]
    await update.message.reply_text(
        "📅 Выберите дату расхода:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
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

async def show_categories(message):
    if not CATEGORIES:
        await message.reply_text("ℹ️ Список категорий пуст.")
        return
    keyboard = [[cat] for cat in CATEGORIES]
    await message.reply_text(
        "📁 Выберите категорию расхода:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )

async def handle_category(update: Update, context: CallbackContext) -> int:
    category = update.message.text
    if category not in CATEGORIES:
        await update.message.reply_text("❌ Категория не найдена. Повторите выбор:")
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
        await update.message.reply_text("❌ Неверный формат суммы. Введите число:")
        return AMOUNT

async def handle_comment(update: Update, context: CallbackContext) -> int:
    context.user_data['comment'] = update.message.text
    return await save_expense(update, context)

async def skip_comment(update: Update, context: CallbackContext) -> int:
    context.user_data['comment'] = ""
    return await save_expense(update, context)

async def save_expense(update: Update, context: CallbackContext) -> int:
    user_data = context.user_data
    username = update.effective_user.username or update.effective_user.full_name or "Неизвестно"

    try:
        try:
            exp_sheet = SPREADSHEET.worksheet('exp')
        except gspread.exceptions.WorksheetNotFound:
            exp_sheet = SPREADSHEET.add_worksheet(title='exp', rows=100, cols=5)
            exp_sheet.append_row(['Date', 'Category', 'Sum', 'Comment', 'User'])

        row = [
            user_data['date'],
            user_data['category'],
            user_data['amount'],
            user_data.get('comment', ''),
            username
        ]
        exp_sheet.append_row(row)
        await update.message.reply_text("✅ Расход успешно сохранен!", reply_markup=default_keyboard())
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        await update.message.reply_text("⚠️ Ошибка при сохранении в таблицу.", reply_markup=default_keyboard())
    finally:
        context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("❌ Операция отменена", reply_markup=default_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

def main() -> None:
    try:
        initialize_spreadsheet()
    except Exception as e:
        logger.critical(f"❌ Не удалось инициализировать таблицу: {e}")
        return

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_expense", start_add_expense),
                      MessageHandler(filters.Regex("^➕ Добавить расход$"), start_add_expense)],
        states={
            DATE: [CallbackQueryHandler(handle_date, pattern="^(today|yesterday|other)$"),
                   MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date)],
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
