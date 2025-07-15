import os
import json
import logging
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
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

# === Константы ===
DATE, CATEGORY, AMOUNT, COMMENT = range(4)
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = "12Mjnj2wwVDYZcNMzzZG6FC-qG29IFtdigDFOEHC6590"

# === Загрузка переменных окружения ===
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

# === Логирование ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Глобальные переменные ===
SPREADSHEET = None
SPREADSHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
CATEGORIES = []

# === Авторизация Google Sheets ===
def create_google_client():
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS not set")
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    return gspread.authorize(creds)

# === Инициализация таблицы ===
def initialize_spreadsheet():
    global SPREADSHEET, CATEGORIES
    CLIENT = create_google_client()
    SPREADSHEET = CLIENT.open_by_key(SPREADSHEET_ID)
    try:
        cat_sheet = SPREADSHEET.worksheet('cat')
        CATEGORIES = cat_sheet.col_values(1)
        if CATEGORIES and ("category" in CATEGORIES[0].lower() or "категория" in CATEGORIES[0].lower()):
            CATEGORIES = CATEGORIES[1:]
    except Exception as e:
        logger.warning(f"Не удалось загрузить категории: {e}")
        CATEGORIES = []

# === Обработчики ===

async def send_main_menu(update: Update, context: CallbackContext):
    keyboard = [["➕ Добавить расход"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Выберите действие:", reply_markup=reply_markup
    )

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "💰 Бот для учета расходов\n"
        "Команды:\n"
        "/add_expense — добавить расход\n"
        "/help — помощь\n"
        f"Используется таблица:\n{SPREADSHEET_URL}"
    )
    await send_main_menu(update, context)

async def start_add_expense(update: Update, context: CallbackContext) -> int:
    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data="today")],
        [InlineKeyboardButton("Вчера", callback_data="yesterday")],
        [InlineKeyboardButton("Другая дата", callback_data="other")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text("📅 Выберите дату расхода:", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text("📅 Выберите дату расхода:", reply_markup=reply_markup)

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
        await message.reply_text("Список категорий пуст. Добавьте их в лист 'cat'.")
        return

    keyboard = [[cat] for cat in CATEGORIES]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await message.reply_text("📁 Выберите категорию:", reply_markup=reply_markup)

async def handle_category(update: Update, context: CallbackContext) -> int:
    category = update.message.text
    if category not in CATEGORIES:
        await update.message.reply_text("❌ Категория не найдена. Попробуйте ещё раз.")
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
        await update.message.reply_text("📝 Введите комментарий (или /skip):")
        return COMMENT
    except ValueError:
        await update.message.reply_text("❌ Введите корректную сумму.")
        return AMOUNT

async def handle_comment(update: Update, context: CallbackContext) -> int:
    context.user_data['comment'] = update.message.text
    return await save_expense(update, context)

async def skip_comment(update: Update, context: CallbackContext) -> int:
    context.user_data['comment'] = ""
    return await save_expense(update, context)

async def save_expense(update: Update, context: CallbackContext) -> int:
    try:
        sheet = SPREADSHEET.worksheet('exp')
    except gspread.exceptions.WorksheetNotFound:
        sheet = SPREADSHEET.add_worksheet(title="exp", rows="100", cols="5")
        sheet.append_row(["Date", "Category", "Sum", "Comment", "User"])

    # Получение username
    user = update.effective_user
    username = user.username or f"{user.first_name} {user.last_name or ''}".strip()

    row = [
        context.user_data['date'],
        context.user_data['category'],
        context.user_data['amount'],
        context.user_data['comment'],
        username
    ]

    sheet.append_row(row)
    await update.message.reply_text("✅ Расход добавлен!")
    context.user_data.clear()
    await send_main_menu(update, context)
    return ConversationHandler.END

# === Main ===

def main():
    initialize_spreadsheet()
    application = Application.builder().token(TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))

    # Диалог добавления расхода
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("add_expense", start_add_expense),
            MessageHandler(filters.TEXT & filters.Regex(r'^➕ Добавить расход$'), start_add_expense)
        ],
        states={
            DATE: [CallbackQueryHandler(handle_date, pattern="^(today|yesterday|other)$")],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
            COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment),
                CommandHandler("skip", skip_comment)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == "__main__":
    main()

