import logging
import sqlite3
import time
import os
import html
import random
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
CRYPTO_TOKEN = os.environ["CRYPTO_PAY_TOKEN"]
CRYPTO_API = "https://pay.crypt.bot/api/"

ADMINS = [8294681123, 8166720202, 1881900547]

WELCOME_IMAGE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "attached_assets",
    "Без_названия61_20260401181753_1775045501752.png"
)

SYSTEM_PROMPT = (
    "Ты живой человек, общаешься в чате. Отвечай коротко, по-русски, с матами — "
    "как реальный пацан из интернета. Не стесняйся материться, это норма. "
    "Отвечай по делу, но неформально и дерзко. "
    "Никогда не говори что ты ИИ, бот, нейросеть или языковая модель — "
    "ты просто живой человек который отвечает. "
    "Если тебя спрашивают кто ты — говори что ты просто чел, не раскрывай что ты ИИ."
)

INSULT_WORDS = [
    "дурак", "лох", "тупой", "идиот", "чмо", "дебил", "урод",
    "мудак", "придурок", "баран", "осёл", "кретин", "имбецил"
]

INSULT_REPLIES = [
    "Сам такой, иди нахуй 🏴‍☠️",
    "Ты первый начал, петух 🏴‍☠️",
    "Слышь, полегче на поворотах, умник 🏴‍☠️",
    "Зеркало купи, там твой портрет 🏴‍☠️",
    "Оооо, какие слова знаем, молодец 🏴‍☠️",
]

logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    requests INTEGER DEFAULT 10,
    last_request INTEGER DEFAULT 0,
    referrer INTEGER,
    refs INTEGER DEFAULT 0,
    banned INTEGER DEFAULT 0
)
""")

# Добавляем колонку banned если её нет (для старых БД)
try:
    cursor.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
    conn.commit()
except Exception:
    pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS payments (
    invoice_id INTEGER,
    user_id INTEGER,
    amount INTEGER,
    status TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS history (
    user_id INTEGER,
    role TEXT,
    content TEXT
)
""")

conn.commit()


# ====== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def get_user_id_by_username(username: str):
    """Найти user_id по username (без @)."""
    username = username.lstrip("@").lower()
    cursor.execute("SELECT user_id FROM users WHERE LOWER(username)=?", (username,))
    row = cursor.fetchone()
    return row[0] if row else None


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Написать в поддержку 🏴‍☠️", callback_data="support")],
        [InlineKeyboardButton("Промт 🏴‍☠️", callback_data="prompt")],
        [InlineKeyboardButton("Информация 🏴‍☠️", callback_data="info")],
        [InlineKeyboardButton("Купить подписку 🏴‍☠️", callback_data="buy")]
    ])

def back_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Назад 🏴‍☠️", callback_data="back")]
    ])

def buy_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("100 запросов — 0.5$ 🏴‍☠️", callback_data="buy_100")],
        [InlineKeyboardButton("200 запросов — 1$ 🏴‍☠️", callback_data="buy_200")],
        [InlineKeyboardButton("Назад 🏴‍☠️", callback_data="back")]
    ])


async def edit_msg(q, text, markup):
    try:
        await q.message.edit_caption(caption=text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        try:
            await q.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            logging.error(f"edit_msg error: {e}")


# ====== ИИ ======

def ask_ai(user_id, prompt):
    if any(word in prompt.lower() for word in INSULT_WORDS):
        return f"<b>{random.choice(INSULT_REPLIES)}</b>"

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"

        cursor.execute(
            "SELECT role, content FROM history WHERE user_id=? ORDER BY rowid DESC LIMIT 10",
            (user_id,)
        )
        history_rows = cursor.fetchall()[::-1]

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [{"role": r, "content": c} for r, c in history_rows]
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.9
        }

        r = requests.post(url, headers=headers, json=data, timeout=30)

        if r.status_code != 200:
            logging.error(f"Groq error {r.status_code}: {r.text}")
            return "<b>Чёт не могу ответить, попробуй позже 🏴‍☠️</b>"

        answer = r.json()["choices"][0]["message"]["content"]

        cursor.execute("INSERT INTO history VALUES (?, ?, ?)", (user_id, "user", prompt))
        cursor.execute("INSERT INTO history VALUES (?, ?, ?)", (user_id, "assistant", answer))
        conn.commit()

        safe_answer = html.escape(answer)
        return f"<b>{safe_answer} 🏴‍☠️</b>"

    except Exception as e:
        logging.error(f"AI error: {e}")
        return "<b>Чёт сломалось, попробуй ещё раз 🏴‍☠️</b>"


# ====== ПОЛЬЗОВАТЕЛЬ ======

def get_user(user_id, username, ref=None):
    username_clean = (username or "").lower()
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (user_id, username, referrer) VALUES (?, ?, ?)",
            (user_id, username_clean, ref)
        )
        conn.commit()

        if ref and ref != user_id:
            cursor.execute(
                "UPDATE users SET refs = refs + 1, requests = requests + 5 WHERE user_id=?",
                (ref,)
            )
            conn.commit()

            cursor.execute("SELECT refs FROM users WHERE user_id=?", (ref,))
            row = cursor.fetchone()
            if row:
                total_refs = row[0]
                if total_refs % 20 == 0:
                    cursor.execute(
                        "UPDATE users SET requests = requests + 50 WHERE user_id=?",
                        (ref,)
                    )
                    conn.commit()
    else:
        # Обновляем username если изменился
        cursor.execute("UPDATE users SET username=? WHERE user_id=?", (username_clean, user_id))
        conn.commit()


# ====== ПРИВЕТСТВИЕ ======

async def send_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    cursor.execute("SELECT requests FROM users WHERE user_id=?", (user.id,))
    row = cursor.fetchone()
    req_count = row[0] if row else 10

    bot_username = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start={user.id}"

    text = (
        f"<b>Приветствую 🏴‍☠️\n\n"
        f"Это нейросеть прямо в Telegram.\n"
        f"Бот представляет собой нейросеть собранную в телеграм боте\n"
        f"создавал бот 1 человек за маленькое время\n"
        f"ждите обновлений и новостей по искуственному интеллекту\n\n"
        f"надеюсь вам понравится бот! 🏴‍☠️\n\n"
        f"Всего доступно: {req_count} запросов 🏴‍☠️\n\n"
        f"Ваша реферальная ссылка:\n{ref_link}</b>"
    )

    if update.message:
        with open(WELCOME_IMAGE, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=text,
                reply_markup=main_menu(),
                parse_mode="HTML"
            )
    elif update.callback_query:
        q = update.callback_query
        try:
            await q.message.edit_caption(caption=text, reply_markup=main_menu(), parse_mode="HTML")
        except Exception:
            try:
                await q.message.edit_text(text, reply_markup=main_menu(), parse_mode="HTML")
            except Exception as e:
                logging.error(f"send_start edit error: {e}")
                try:
                    await q.message.delete()
                except Exception:
                    pass
                with open(WELCOME_IMAGE, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=q.message.chat_id,
                        photo=photo,
                        caption=text,
                        reply_markup=main_menu(),
                        parse_mode="HTML"
                    )


# ====== КОМАНДЫ ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref = None
    if context.args:
        try:
            ref = int(context.args[0])
        except Exception:
            pass

    get_user(user.id, user.username, ref)

    # Проверка бана
    cursor.execute("SELECT banned FROM users WHERE user_id=?", (user.id,))
    row = cursor.fetchone()
    if row and row[0]:
        await update.message.reply_text("<b>Ты забанен в боте 🏴‍☠️</b>", parse_mode="HTML")
        return

    if ref:
        cursor.execute("SELECT refs FROM users WHERE user_id=?", (ref,))
        row = cursor.fetchone()
        if row:
            count = row[0]
            try:
                await context.bot.send_message(
                    ref,
                    f"<b>По вашей реферальной ссылке зарегистрировались: {count}\n+5 запросов 🏴‍☠️</b>",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    await send_start(update, context)


async def cmd_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: /text @username сообщение"""
    if not is_admin(update.effective_user.id):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "<b>Использование: /text @юзер сообщение 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    target_username = context.args[0]
    message_text = " ".join(context.args[1:])
    target_id = get_user_id_by_username(target_username)

    if not target_id:
        await update.message.reply_text(
            f"<b>Пользователь {target_username} не найден в базе 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    try:
        await context.bot.send_message(
            target_id,
            f"<b>📩 Сообщение от администратора:\n\n{html.escape(message_text)}</b>",
            parse_mode="HTML"
        )
        await update.message.reply_text(
            f"<b>✅ Сообщение отправлено пользователю {target_username} 🏴‍☠️</b>", parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"cmd_text error: {e}")
        await update.message.reply_text(
            "<b>Не удалось отправить сообщение 🏴‍☠️</b>", parse_mode="HTML"
        )


async def cmd_text_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: /textTOP сообщение — рассылка всем пользователям"""
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Использование: /textTOP сообщение 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    message_text = " ".join(context.args)
    cursor.execute("SELECT user_id FROM users WHERE banned=0")
    all_users = cursor.fetchall()

    sent = 0
    failed = 0
    for (uid,) in all_users:
        try:
            await context.bot.send_message(
                uid,
                f"<b>📢 Рассылка от администратора:\n\n{html.escape(message_text)}</b>",
                parse_mode="HTML"
            )
            sent += 1
            time.sleep(0.05)  # Небольшая задержка чтобы не флудить
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"<b>✅ Рассылка завершена 🏴‍☠️\nОтправлено: {sent}\nНе доставлено: {failed}</b>",
        parse_mode="HTML"
    )


async def cmd_set1000(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: /set1000 @username — выдать 1000 запросов"""
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Использование: /set1000 @юзер 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    target_username = context.args[0]
    target_id = get_user_id_by_username(target_username)

    if not target_id:
        await update.message.reply_text(
            f"<b>Пользователь {target_username} не найден в базе 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    cursor.execute("UPDATE users SET requests=1000 WHERE user_id=?", (target_id,))
    conn.commit()

    try:
        await context.bot.send_message(
            target_id,
            "<b>🎁 Администратор выдал тебе 1000 запросов! 🏴‍☠️</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"<b>✅ Пользователю {target_username} выдано 1000 запросов 🏴‍☠️</b>", parse_mode="HTML"
    )


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: /ban @username — забанить пользователя"""
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Использование: /ban @юзер 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    target_username = context.args[0]
    target_id = get_user_id_by_username(target_username)

    if not target_id:
        await update.message.reply_text(
            f"<b>Пользователь {target_username} не найден в базе 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    if is_admin(target_id):
        await update.message.reply_text(
            "<b>🚫 Нельзя банить администраторов 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    cursor.execute("UPDATE users SET banned=1 WHERE user_id=?", (target_id,))
    conn.commit()

    try:
        await context.bot.send_message(
            target_id,
            "<b>🚫 Ты заблокирован в боте 🏴‍☠️</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"<b>✅ Пользователь {target_username} забанен 🏴‍☠️</b>", parse_mode="HTML"
    )


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ: /unban @username — разбанить пользователя"""
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text(
            "<b>Использование: /unban @юзер 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    target_username = context.args[0]
    target_id = get_user_id_by_username(target_username)

    if not target_id:
        await update.message.reply_text(
            f"<b>Пользователь {target_username} не найден в базе 🏴‍☠️</b>", parse_mode="HTML"
        )
        return

    cursor.execute("UPDATE users SET banned=0 WHERE user_id=?", (target_id,))
    conn.commit()

    try:
        await context.bot.send_message(
            target_id,
            "<b>✅ Ты разблокирован в боте 🏴‍☠️</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"<b>✅ Пользователь {target_username} разбанен 🏴‍☠️</b>", parse_mode="HTML"
    )


# ====== ОБРАБОТКА СООБЩЕНИЙ ======

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("prompt"):
        return

    user = update.effective_user
    get_user(user.id, user.username)

    # Проверка бана
    cursor.execute("SELECT banned FROM users WHERE user_id=?", (user.id,))
    ban_row = cursor.fetchone()
    if ban_row and ban_row[0]:
        await update.message.reply_text("<b>Ты забанен в боте 🏴‍☠️</b>", parse_mode="HTML")
        context.user_data["prompt"] = False
        return

    cursor.execute("SELECT requests, last_request FROM users WHERE user_id=?", (user.id,))
    row = cursor.fetchone()
    if not row:
        return
    req, last = row

    if time.time() - last < 5:
        await update.message.reply_text("<b>Подожди немного 🏴‍☠️</b>", parse_mode="HTML")
        return

    if req <= 0:
        await update.message.reply_text(
            "<b>Запросы закончились, купи ещё 🏴‍☠️</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Купить 🏴‍☠️", callback_data="buy")]
            ]),
            parse_mode="HTML"
        )
        return

    cursor.execute("UPDATE users SET last_request=? WHERE user_id=?", (int(time.time()), user.id))
    conn.commit()

    thinking_msg = await update.message.reply_text("<b>Думаю... 🏴‍☠️</b>", parse_mode="HTML")

    answer = ask_ai(user.id, update.message.text)

    cursor.execute("UPDATE users SET requests=requests-1 WHERE user_id=?", (user.id,))
    conn.commit()

    try:
        await thinking_msg.delete()
    except Exception:
        pass

    await update.message.reply_text(answer, reply_markup=back_button(), parse_mode="HTML")
    context.user_data["prompt"] = False


# ====== КНОПКИ ======

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query

    try:
        await q.answer()
    except Exception:
        pass

    if q.data == "prompt":
        context.user_data["prompt"] = True
        await edit_msg(q, "<b>Напишите запрос 🏴‍☠️</b>", back_button())

    elif q.data == "back":
        await send_start(update, context)

    elif q.data == "support":
        await edit_msg(q, "<b>Если есть вопросы — пишите @StrongByte 🏴‍☠️</b>", back_button())

    elif q.data == "info":
        info_text = (
            "<b>Приветствую, если вы нажали на эту кнопку, то вам, скорее всего, интересна "
            "информация о боте и вашей безопасности в нём! 🏴‍☠️\n\n"
            "Этот бот создан одним человеком за очень короткое время, так как создатель слишком "
            "ленивый, чтобы куда-то заходить и выходить, он написал и вшил нейросеть прям в бота 🏴‍☠️\n\n"
            "По поводу вашей безопасности: «Я не смогу читать ваши сообщения в боте, также не могу "
            "писать от лица нейросети, так как мне тупо лень вшивать такую функцию в бота, так что "
            "не бойтесь, всё конфиденциально!» 🏴‍☠️</b>"
        )
        await edit_msg(q, info_text, back_button())

    elif q.data == "buy":
        await edit_msg(q, "<b>Выберите пакет 🏴‍☠️</b>", buy_menu())

    elif q.data in ("buy_100", "buy_200"):
        if q.data == "buy_100":
            amount = "0.5"
            count = 100
        else:
            amount = "1"
            count = 200
        try:
            headers = {"Crypto-Pay-API-Token": CRYPTO_TOKEN}
            payload = {
                "asset": "USDT",
                "amount": amount,
                "description": f"Покупка {count} запросов",
                "payload": f"{q.from_user.id}:{count}"
            }
            r = requests.post(CRYPTO_API + "createInvoice", headers=headers, json=payload, timeout=15)
            data = r.json()
            if data.get("ok"):
                invoice = data["result"]
                invoice_id = invoice["invoice_id"]
                pay_url = invoice["pay_url"]
                cursor.execute(
                    "INSERT INTO payments VALUES (?, ?, ?, ?)",
                    (invoice_id, q.from_user.id, count, "pending")
                )
                conn.commit()
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Оплатить 🏴‍☠️", url=pay_url)],
                    [InlineKeyboardButton("Назад 🏴‍☠️", callback_data="back")]
                ])
                await edit_msg(
                    q,
                    f"<b>Оплатите {amount}$ (USDT) для получения {count} запросов 🏴‍☠️</b>",
                    kb
                )
            else:
                logging.error(f"CryptoPay error: {data}")
                await edit_msg(q, "<b>Ошибка создания счёта 🏴‍☠️</b>", back_button())
        except Exception as e:
            logging.error(f"Payment error: {e}")
            await edit_msg(q, "<b>Ошибка платежа 🏴‍☠️</b>", back_button())


# ====== ЗАПУСК ======

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("text", cmd_text))
    app.add_handler(CommandHandler("textTOP", cmd_text_top))
    app.add_handler(CommandHandler("set1000", cmd_set1000))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("Бот запущен 🏴‍☠️")
    app.run_polling()


if __name__ == "__main__":
    main()
