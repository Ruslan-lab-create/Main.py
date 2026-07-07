import asyncio
import csv
import datetime
import io
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, BufferedInputFile
)
import aiosqlite

logging.basicConfig(level=logging.INFO)

# ---------------- CONFIG ----------------

BOT_TOKEN = "8929198324:AAHW0LVljuTmmU-0JEnkonCSddpBiK5jtWg"
ADMIN_IDS = [8061549073]
DB_PATH = "bot.db"

INTERNAL_OFFSET = 1000

# ---------------- DATABASE ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE NOT NULL,
    first_name TEXT,
    username TEXT,
    first_seen TEXT,
    last_seen TEXT,
    is_banned INTEGER DEFAULT 0,
    is_favorite INTEGER DEFAULT 0,
    is_muted INTEGER DEFAULT 0,
    is_archived INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    msg_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    content TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS templates (
    slot INTEGER PRIMARY KEY,
    text TEXT
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


def now():
    return datetime.datetime.utcnow().isoformat()


def display_id(row_id: int) -> int:
    return INTERNAL_OFFSET + row_id


async def get_or_create_user(tg_id, first_name, username):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        if row:
            await db.execute(
                "UPDATE users SET last_seen=?, first_name=?, username=? WHERE tg_id=?",
                (now(), first_name, username, tg_id),
            )
            await db.commit()
            return dict(row), False
        ts = now()
        cur = await db.execute(
            "INSERT INTO users (tg_id, first_name, username, first_seen, last_seen) VALUES (?,?,?,?,?)",
            (tg_id, first_name, username, ts, ts),
        )
        await db.commit()
        cur2 = await db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,))
        row2 = await cur2.fetchone()
        return dict(row2), True


async def get_user_by_id(row_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE id=?", (row_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def find_users(query: str):
    q = f"%{query}%"
    clean_id = query.replace("#", "").strip()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM users WHERE
               CAST(id AS TEXT)=? OR CAST(tg_id AS TEXT)=? OR
               username LIKE ? OR first_name LIKE ?""",
            (clean_id, query.strip(), q, q),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_users(offset=0, limit=20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users ORDER BY last_seen DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cur.fetchall()
        cur2 = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur2.fetchone())[0]
        return [dict(r) for r in rows], total


async def set_field(row_id, field, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {field}=? WHERE id=?", (value, row_id))
        await db.commit()


async def clear_history(row_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages WHERE user_id=?", (row_id,))
        await db.commit()


async def add_message(user_id, direction, msg_type, content):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (user_id, direction, msg_type, content, created_at) VALUES (?,?,?,?,?)",
            (user_id, direction, msg_type, content, now()),
        )
        if direction == "in":
            await db.execute(
                "UPDATE users SET msg_count = msg_count + 1, last_seen=? WHERE id=?",
                (now(), user_id),
            )
        await db.commit()


async def get_history(user_id, limit=30):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def get_stats():
    today = datetime.date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        total_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        banned = (await (await db.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")).fetchone())[0]
        new_today = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE first_seen LIKE ?", (today + "%",)
        )).fetchone())[0]
        active_today = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen LIKE ?", (today + "%",)
        )).fetchone())[0]
        msgs_today = (await (await db.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at LIKE ?", (today + "%",)
        )).fetchone())[0]
        msgs_total = (await (await db.execute("SELECT COUNT(*) FROM messages")).fetchone())[0]
        return dict(total_users=total_users, banned=banned, new_today=new_today,
                     active_today=active_today, msgs_today=msgs_today, msgs_total=msgs_total)


async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users ORDER BY id")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def set_template(slot, text):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO templates (slot, text) VALUES (?,?) "
            "ON CONFLICT(slot) DO UPDATE SET text=excluded.text",
            (slot, text),
        )
        await db.commit()


async def get_template(slot):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT text FROM templates WHERE slot=?", (slot,))
        row = await cur.fetchone()
        return row[0] if row else None


# ---------------- KEYBOARDS ----------------

def user_card_kb(row_id: int, is_banned: bool, is_muted: bool):
    ban_btn = (
        InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"unban:{row_id}")
        if is_banned else
        InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"ban:{row_id}")
    )
    mute_btn = (
        InlineKeyboardButton(text="🔔 Включить уведомления", callback_data=f"unmute:{row_id}")
        if is_muted else
        InlineKeyboardButton(text="🔕 Отключить уведомления", callback_data=f"mute:{row_id}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:{row_id}"),
         InlineKeyboardButton(text="ℹ️ Информация", callback_data=f"info:{row_id}")],
        [ban_btn, mute_btn],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data=f"fav:{row_id}"),
         InlineKeyboardButton(text="🗑 Очистить историю", callback_data=f"clear:{row_id}")],
    ])


def users_list_kb(users, offset, total, limit=20):
    rows = []
    for u in users:
        label = f"#{display_id(u['id'])} {u['first_name'] or ''}"
        if u["is_banned"]:
            label = "🚫 " + label
        rows.append([InlineKeyboardButton(text=label, callback_data=f"info:{u['id']}")])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"page:{max(0, offset-limit)}"))
    if offset + limit < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"page:{offset+limit}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def close_chat_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Закрыть чат", callback_data="closechat")]
    ])


# ---------------- STATES ----------------

class BroadcastState(StatesGroup):
    waiting = State()


# ---------------- ADMIN ROUTER ----------------

admin_router = Router()
admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

open_chats: dict[int, int] = {}

HELP_TEXT = (
    "Админ-команды:\n"
    "/users — список пользователей\n"
    "/find <запрос> — поиск\n"
    "/chat <id> — открыть чат\n"
    "/close — закрыть текущий чат\n"
    "/ban <id> — заблокировать\n"
    "/unban <id> — разблокировать\n"
    "/info <id> — информация\n"
    "/broadcast — рассылка всем\n"
    "/stats — статистика\n"
    "/export — экспорт в CSV\n"
    "/note <id> <текст> — заметка\n"
    "/tag <id> <теги> — теги\n"
    "/settpl <n> <текст> — задать шаблон ответа\n"
    "/tpl<n> — отправить шаблон в открытом чате\n"
)


def resolve_row_id(raw: str) -> int:
    n = int(raw)
    return n - INTERNAL_OFFSET if n >= INTERNAL_OFFSET else n


async def resolve_id_arg(command: CommandObject, message: Message):
    if not command.args:
        await message.answer("Укажи ID, например: /info 1001")
        return None
    try:
        row_id = resolve_row_id(command.args.split()[0])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return None
    user = await get_user_by_id(row_id)
    if not user:
        await message.answer("Пользователь не найден.")
        return None
    return user


def format_user_info(u: dict) -> str:
    return (
        f"ID: #{display_id(u['id'])}\n"
        f"Telegram ID: {u['tg_id']}\n"
        f"Имя: {u['first_name'] or '-'}\n"
        f"Username: {'@' + u['username'] if u['username'] else '-'}\n"
        f"Профиль: tg://user?id={u['tg_id']}\n"
        f"Регистрация: {u['first_seen']}\n"
        f"Последнее сообщение: {u['last_seen']}\n"
        f"Сообщений: {u['msg_count']}\n"
        f"Статус: {'🚫 заблокирован' if u['is_banned'] else '✅ активен'}\n"
        f"Избранный: {'⭐' if u['is_favorite'] else '—'}\n"
        f"Уведомления: {'🔕 выключены' if u['is_muted'] else '🔔 включены'}\n"
        f"Теги: {u['tags'] or '-'}\n"
        f"Заметка: {u['note'] or '-'}"
    )


@admin_router.message(Command("admin", "help"))
async def cmd_admin(message: Message):
    await message.answer(HELP_TEXT)


@admin_router.message(Command("users"))
async def cmd_users(message: Message):
    users, total = await list_users(0, 20)
    if not users:
        await message.answer("Пользователей пока нет.")
        return
    await message.answer(f"Всего пользователей: {total}", reply_markup=users_list_kb(users, 0, total))


@admin_router.callback_query(F.data.startswith("page:"))
async def cb_page(call: CallbackQuery):
    offset = int(call.data.split(":")[1])
    users, total = await list_users(offset, 20)
    await call.message.edit_text(f"Всего пользователей: {total}", reply_markup=users_list_kb(users, offset, total))
    await call.answer()


@admin_router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Использование: /find запрос")
        return
    results = await find_users(command.args.strip())
    if not results:
        await message.answer("Ничего не найдено.")
        return
    await message.answer(f"Найдено: {len(results)}", reply_markup=users_list_kb(results, 0, len(results)))


@admin_router.message(Command("info"))
async def cmd_info(message: Message, command: CommandObject):
    user = await resolve_id_arg(command, message)
    if not user:
        return
    kb = user_card_kb(user["id"], bool(user["is_banned"]), bool(user["is_muted"]))
    await message.answer(format_user_info(user), reply_markup=kb)


@admin_router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject):
    user = await resolve_id_arg(command, message)
    if not user:
        return
    await set_field(user["id"], "is_banned", 1)
    await message.answer(f"Пользователь #{display_id(user['id'])} заблокирован.")


@admin_router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
    user = await resolve_id_arg(command, message)
    if not user:
        return
    await set_field(user["id"], "is_banned", 0)
    await message.answer(f"Пользователь #{display_id(user['id'])} разблокирован.")


@admin_router.message(Command("chat"))
async def cmd_chat(message: Message, command: CommandObject):
    user = await resolve_id_arg(command, message)
    if not user:
        return
    open_chats[message.from_user.id] = user["id"]
    history = await get_history(user["id"], 30)
    lines = [f"История с #{display_id(user['id'])} ({user['first_name']}):\n"]
    for m in history:
        prefix = "👤" if m["direction"] == "in" else "🧑‍💼"
        lines.append(f"{prefix} [{m['msg_type']}] {m['content']}")
    lines.append("\n✍️ Чат открыт. Пиши сообщения — они уйдут пользователю. /close для выхода.")
    await message.answer("\n".join(lines)[-4000:], reply_markup=close_chat_kb())


@admin_router.message(Command("close"))
async def cmd_close(message: Message):
    open_chats.pop(message.from_user.id, None)
    await message.answer("Чат закрыт.")


@admin_router.callback_query(F.data == "closechat")
async def cb_closechat(call: CallbackQuery):
    open_chats.pop(call.from_user.id, None)
    await call.message.answer("Чат закрыт.")
    await call.answer()


@admin_router.callback_query(F.data.startswith("chat:"))
async def cb_chat(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    open_chats[call.from_user.id] = row_id
    user = await get_user_by_id(row_id)
    history = await get_history(row_id, 30)
    lines = [f"История с #{display_id(row_id)} ({user['first_name']}):\n"]
    for m in history:
        prefix = "👤" if m["direction"] == "in" else "🧑‍💼"
        lines.append(f"{prefix} [{m['msg_type']}] {m['content']}")
    lines.append("\n✍️ Чат открыт. Пиши сообщения — они уйдут пользователю. /close для выхода.")
    await call.message.answer("\n".join(lines)[-4000:], reply_markup=close_chat_kb())
    await call.answer()


@admin_router.callback_query(F.data.startswith("info:"))
async def cb_info(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    user = await get_user_by_id(row_id)
    kb = user_card_kb(row_id, bool(user["is_banned"]), bool(user["is_muted"]))
    await call.message.answer(format_user_info(user), reply_markup=kb)
    await call.answer()


@admin_router.callback_query(F.data.startswith("ban:"))
async def cb_ban(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    await set_field(row_id, "is_banned", 1)
    await call.answer("Заблокирован")
    await call.message.answer(f"#{display_id(row_id)} заблокирован.")


@admin_router.callback_query(F.data.startswith("unban:"))
async def cb_unban(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    await set_field(row_id, "is_banned", 0)
    await call.answer("Разблокирован")
    await call.message.answer(f"#{display_id(row_id)} разблокирован.")


@admin_router.callback_query(F.data.startswith("mute:"))
async def cb_mute(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    await set_field(row_id, "is_muted", 1)
    await call.answer("Уведомления выключены")


@admin_router.callback_query(F.data.startswith("unmute:"))
async def cb_unmute(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    await set_field(row_id, "is_muted", 0)
    await call.answer("Уведомления включены")


@admin_router.callback_query(F.data.startswith("fav:"))
async def cb_fav(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    user = await get_user_by_id(row_id)
    await set_field(row_id, "is_favorite", 0 if user["is_favorite"] else 1)
    await call.answer("Обновлено")


@admin_router.callback_query(F.data.startswith("clear:"))
async def cb_clear(call: CallbackQuery):
    row_id = int(call.data.split(":")[1])
    await clear_history(row_id)
    await call.answer("История очищена")


@admin_router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Использование: /note id текст")
        return
    parts = command.args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /note id текст")
        return
    try:
        row_id = resolve_row_id(parts[0])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    await set_field(row_id, "note", parts[1])
    await message.answer("Заметка сохранена.")


@admin_router.message(Command("tag"))
async def cmd_tag(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Использование: /tag id VIP,Покупатель")
        return
    parts = command.args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /tag id VIP,Покупатель")
        return
    try:
        row_id = resolve_row_id(parts[0])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    await set_field(row_id, "tags", parts[1])
    await message.answer("Теги сохранены.")


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    s = await get_stats()
    text = (
        f"📊 Статистика\n"
        f"Всего пользователей: {s['total_users']}\n"
        f"Активных сегодня: {s['active_today']}\n"
        f"Новых сегодня: {s['new_today']}\n"
        f"Заблокировано: {s['banned']}\n"
        f"Сообщений сегодня: {s['msgs_today']}\n"
        f"Сообщений всего: {s['msgs_total']}"
    )
    await message.answer(text)


@admin_router.message(Command("export"))
async def cmd_export(message: Message, bot: Bot):
    users = await get_all_users()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["internal_id", "tg_id", "name", "username", "first_seen", "last_seen", "banned", "msg_count"])
    for u in users:
        writer.writerow([display_id(u["id"]), u["tg_id"], u["first_name"], u["username"],
                          u["first_seen"], u["last_seen"], u["is_banned"], u["msg_count"]])
    data = buf.getvalue().encode("utf-8-sig")
    file = BufferedInputFile(data, filename="users_export.csv")
    await bot.send_document(message.chat.id, file)


@admin_router.message(Command("broadcast"))
async def cmd_broadcast_start(message: Message, state: FSMContext):
    await state.set_state(BroadcastState.waiting)
    await message.answer("Пришли сообщение для рассылки. /cancel — отменить.")


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@admin_router.message(BroadcastState.waiting)
async def do_broadcast(message: Message, state: FSMContext):
    await state.clear()
    users = await get_all_users()
    sent, failed = 0, 0
    for u in users:
        if u["is_banned"]:
            continue
        try:
            await message.copy_to(u["tg_id"])
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"Рассылка завершена. Успешно: {sent}, ошибок: {failed}")


@admin_router.message(Command("settpl"))
async def cmd_settpl(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Использование: /settpl 1 текст шаблона")
        return
    parts = command.args.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("Использование: /settpl 1 текст шаблона")
        return
    slot, text = int(parts[0]), parts[1]
    await set_template(slot, text)
    await message.answer(f"Шаблон /tpl{slot} сохранён.")


@admin_router.message(F.text.regexp(r"^/tpl(\d+)$"))
async def cmd_use_template(message: Message, bot: Bot):
    slot = int(message.text[4:])
    admin_id = message.from_user.id
    if admin_id not in open_chats:
        await message.answer("Сначала открой чат: /chat id")
        return
    text = await get_template(slot)
    if not text:
        await message.answer(f"Такой шаблон не задан. /settpl {slot} текст")
        return
    row_id = open_chats[admin_id]
    user = await get_user_by_id(row_id)
    await bot.send_message(user["tg_id"], text)
    await add_message(row_id, "out", "text", text)
    await message.answer("Шаблон отправлен.")


@admin_router.message(F.text.regexp(r"^/") == False)
async def relay_to_user(message: Message):
    admin_id = message.from_user.id
    if admin_id not in open_chats:
        return
    row_id = open_chats[admin_id]
    user = await get_user_by_id(row_id)
    if not user:
        open_chats.pop(admin_id, None)
        return
    try:
        await message.copy_to(user["tg_id"])
        content = message.text or message.caption or "[файл]"
        await add_message(row_id, "out", "text", content)
    except Exception as e:
        await message.answer(f"Не удалось отправить: {e}")


# ---------------- USER ROUTER ----------------

user_router = Router()

MSG_TYPE_MAP = {
    "text": "text", "photo": "photo", "video": "video", "voice": "voice",
    "document": "document", "sticker": "sticker", "animation": "gif", "video_note": "video_note",
}


def detect_type(message: Message) -> str:
    for attr, name in MSG_TYPE_MAP.items():
        if getattr(message, attr, None):
            return name
    return "other"


def user_header(u: dict, is_new: bool) -> str:
    title = "🆕 Новый пользователь" if is_new else "📩 Новое сообщение"
    lines = [
        title,
        f"ID: #{display_id(u['id'])}",
        f"Имя: {u['first_name'] or '-'}",
        f"Username: {'@' + u['username'] if u['username'] else '-'}",
        f"Профиль: tg://user?id={u['tg_id']}",
    ]
    if u.get("tags"):
        lines.append(f"Теги: {u['tags']}")
    if u.get("note"):
        lines.append(f"Заметка: {u['note']}")
    return "\n".join(lines)


@user_router.message(F.chat.type == "private", ~F.from_user.id.in_(ADMIN_IDS))
async def handle_user_message(message: Message, bot: Bot):
    tg_user = message.from_user
    user, is_new = await get_or_create_user(tg_user.id, tg_user.full_name, tg_user.username)

    if user["is_banned"]:
        await message.answer("Вы заблокированы администрацией.")
        return

    msg_type = detect_type(message)
    content = message.text or message.caption or f"[{msg_type}]"
    await add_message(user["id"], "in", msg_type, content)

    if user["is_muted"]:
        return

    header = user_header(user, is_new)
    kb = user_card_kb(user["id"], bool(user["is_banned"]), bool(user["is_muted"]))

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, header, reply_markup=kb)
            await message.copy_to(admin_id)
        except Exception:
            pass


# ---------------- MAIN ----------------

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
