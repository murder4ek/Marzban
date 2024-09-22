import io
import math
import os
import random
import re
import string
from datetime import datetime, timedelta

import qrcode
import sqlalchemy
from dateutil.relativedelta import relativedelta
from telebot import types
from telebot.util import extract_arguments, user_link

from app import xray
from app.db import GetDB, crud
from app.models.proxy import ProxyTypes
from app.models.user import (UserCreate, UserModify, UserResponse, UserStatus,
                             UserStatusModify)
from app.models.user_template import UserTemplateResponse
from app.telegram import bot
from app.telegram.utils.custom_filters import (cb_query_equals,
                                               cb_query_startswith)
from app.telegram.utils.keyboard import BotKeyboard
from app.utils.store import MemoryStorage
from app.utils.system import cpu_usage, memory_usage, readable_size

try:
    from app.utils.system import realtime_bandwith as realtime_bandwidth
except ImportError:
    from app.utils.system import realtime_bandwidth

from config import TELEGRAM_DEFAULT_VLESS_FLOW, TELEGRAM_LOGGER_CHANNEL_ID

mem_store = MemoryStorage()


def get_system_info():
    mem = memory_usage()
    cpu = cpu_usage()
    with GetDB() as db:
        bandwidth = crud.get_system_usage(db)
        total_users = crud.get_users_count(db)
        active_users = crud.get_users_count(db, UserStatus.active)
        onhold_users = crud.get_users_count(db, UserStatus.on_hold)
    return """\
🎛 *Ядра ЦП*: `{cpu_cores}`
🖥 *Использование ЦП*: `{cpu_percent}%`
➖➖➖➖➖➖➖
📊 *Общий объем памяти*: `{total_memory}`
📈 *Используется память*: `{used_memory}`
📉 *Свободная память*: `{free_memory}`
➖➖➖➖➖➖➖
⬇️ *Загружено*: `{down_bandwidth}`
⬆️ *Выгружено*: `{up_bandwidth}`
↕️ *Общее*: `{total_bandwidth}`
➖➖➖➖➖➖➖
👥 *Пользователей*: `{total_users}`
🟢 *Активных пользователей*: `{active_users}`
🟣 *Не активных пользователей*: `{onhold_users}`
🔴 *Деактивированных пользователей*: `{deactivate_users}`
➖➖➖➖➖➖➖
⏫ *Скорость выгрузки*: `{up_speed}/s`
⏬ *Скорость загрузки*: `{down_speed}/s`
""".format(
        cpu_cores=cpu.cores,
        cpu_percent=cpu.percent,
        total_memory=readable_size(mem.total),
        used_memory=readable_size(mem.used),
        free_memory=readable_size(mem.free),
        total_bandwidth=readable_size(bandwidth.uplink + bandwidth.downlink),
        up_bandwidth=readable_size(bandwidth.uplink),
        down_bandwidth=readable_size(bandwidth.downlink),
        total_users=total_users,
        active_users=active_users,
        onhold_users=onhold_users,
        deactivate_users=total_users -(active_users + onhold_users),
        up_speed=readable_size(realtime_bandwidth().outgoing_bytes),
        down_speed=readable_size(realtime_bandwidth().incoming_bytes)
    )


def schedule_delete_message(chat_id, *message_ids: int) -> None:
    messages: list[int] = mem_store.get(f"{chat_id}:messages_to_delete", [])
    for mid in message_ids:
        messages.append(mid)
    mem_store.set(f"{chat_id}:messages_to_delete", messages)


def cleanup_messages(chat_id: int) -> None:
    messages: list[int] = mem_store.get(f"{chat_id}:messages_to_delete", [])
    for message_id in messages:
        try:
            bot.delete_message(chat_id, message_id)
        except:
            pass
    mem_store.set(f"{chat_id}:messages_to_delete", [])


@bot.message_handler(commands=['start', 'help'], is_admin=True)
def help_command(message: types.Message):
    cleanup_messages(message.chat.id)
    bot.clear_step_handler_by_chat_id(message.chat.id)
    return bot.reply_to(message, """
{user_link} Добро пожаловать в панель администратора Marzban Telegram-Bot.
Здесь вы можете управлять своими пользователями и прокси.
Чтобы начать, используйте кнопки ниже.
Кроме того, вы можете получать и изменять пользователей с помощью команды /user.
""".format(
        user_link=user_link(message.from_user)
    ), parse_mode="html", reply_markup=BotKeyboard.main_menu())


@bot.callback_query_handler(cb_query_equals('system'), is_admin=True)
def system_command(call: types.CallbackQuery):
    return bot.edit_message_text(
        get_system_info(),
        call.message.chat.id,
        call.message.message_id,
        parse_mode="MarkdownV2",
        reply_markup=BotKeyboard.main_menu()
    )


@bot.callback_query_handler(cb_query_equals('restart'), is_admin=True)
def restart_command(call: types.CallbackQuery):
    bot.edit_message_text(
        '⚠️ Вы уверены? Это перезапустит ядро ​​Xray.',
        call.message.chat.id,
        call.message.message_id,
        reply_markup=BotKeyboard.confirm_action(action='restart')
    )


@bot.callback_query_handler(cb_query_startswith('delete:'), is_admin=True)
def delete_user_command(call: types.CallbackQuery):
    username = call.data.split(':')[1]
    bot.edit_message_text(
        f'⚠️ Вы уверены? Это приведет к удалению пользователя `{username}`.',
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(
            action='delete', username=username)
    )


@bot.callback_query_handler(cb_query_startswith("suspend:"), is_admin=True)
def suspend_user_command(call: types.CallbackQuery):
    username = call.data.split(":")[1]
    bot.edit_message_text(
        f"⚠️ Вы уверены? Это приведет к блокировке пользователя `{username}`.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(
            action="suspend", username=username),
    )


@bot.callback_query_handler(cb_query_startswith("activate:"), is_admin=True)
def activate_user_command(call: types.CallbackQuery):
    username = call.data.split(":")[1]
    bot.edit_message_text(
        f"⚠️ Вы уверены? Это активирует пользователя `{username}`.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(
            action="activate", username=username),
    )


@bot.callback_query_handler(cb_query_startswith("reset_usage:"), is_admin=True)
def reset_usage_user_command(call: types.CallbackQuery):
    username = call.data.split(":")[1]
    bot.edit_message_text(
        f"⚠️ Вы уверены? Это приведет к сбросу использования пользователя `{username}`.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(
            action="reset_usage", username=username),
    )


@bot.callback_query_handler(cb_query_equals('edit_all'), is_admin=True)
def edit_all_command(call: types.CallbackQuery):
    with GetDB() as db:
        total_users = crud.get_users_count(db)
        active_users = crud.get_users_count(db, UserStatus.active)
        disabled_users = crud.get_users_count(db, UserStatus.disabled)
        expired_users = crud.get_users_count(db, UserStatus.expired)
        limited_users = crud.get_users_count(db, UserStatus.limited)
        onhold_users = crud.get_users_count(db, UserStatus.on_hold)
        text = f'''
👥 *Пользователи*: `{total_users}`
✅ *Активных пользователей*: `{active_users}`
❌ *Неактивные пользователи*: `{disabled_users}`
🕰 *Пользователи с истекшим сроком действия*: `{expired_users}`
🪫 *Ограниченные пользователи*: `{limited_users}`
🔌 *Пользователи в режиме ожидания*: `{onhold_users}`'''
    return bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.edit_all_menu()
    )


@bot.callback_query_handler(cb_query_equals('delete_expired'), is_admin=True)
def delete_expired_command(call: types.CallbackQuery):
    bot.edit_message_text(
        f"⚠️ Вы уверены? Это приведет к *УДАЛЕНИЮ всех пользователей с истекшим сроком действия*‼️",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(action="delete_expired"))


@bot.callback_query_handler(cb_query_equals('delete_limited'), is_admin=True)
def delete_limited_command(call: types.CallbackQuery):
    bot.edit_message_text(
        f"⚠️ Вы уверены? Это приведет к *УДАЛЕНИЮ всех пользователей с ограниченными правами*‼️",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(action="delete_limited"))


@bot.callback_query_handler(cb_query_equals('add_data'), is_admin=True)
def add_data_command(call: types.CallbackQuery):
    msg = bot.edit_message_text(
        f"🔋 Введите лимит данных для увеличения или уменьшения (ГБ):",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=BotKeyboard.inline_cancel_action())
    schedule_delete_message(call.message.chat.id, call.message.id)
    schedule_delete_message(call.message.chat.id, msg.id)
    return bot.register_next_step_handler(call.message, add_data_step)


def add_data_step(message):
    try:
        data_limit = float(message.text)
        if not data_limit:
            raise ValueError
    except ValueError:
        wait_msg = bot.send_message(message.chat.id, '❌ Предел данных должен быть числом, а не нулем.')
        schedule_delete_message(message.chat.id, wait_msg.message_id)
        return bot.register_next_step_handler(wait_msg, add_data_step)
    schedule_delete_message(message.chat.id, message.message_id)
    msg = bot.send_message(
        message.chat.id,
        f"⚠️ Вы уверены? это изменит лимит данных для всех пользователей в соответствии с <b>"
        f"{'+' if data_limit > 0 else '-'}{readable_size(abs(data_limit *1024*1024*1024))}</b>",
        parse_mode="html",
        reply_markup=BotKeyboard.confirm_action('add_data', data_limit))
    cleanup_messages(message.chat.id)
    schedule_delete_message(message.chat.id, msg.id)


@bot.callback_query_handler(cb_query_equals('add_time'), is_admin=True)
def add_time_command(call: types.CallbackQuery):
    msg = bot.edit_message_text(
        f"📅 Введите дни, чтобы увеличить или уменьшить срок действия:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=BotKeyboard.inline_cancel_action())
    schedule_delete_message(call.message.chat.id, call.message.id)
    schedule_delete_message(call.message.chat.id, msg.id)
    return bot.register_next_step_handler(call.message, add_time_step)


def add_time_step(message):
    try:
        days = int(message.text)
        if not days:
            raise ValueError
    except ValueError:
        wait_msg = bot.send_message(message.chat.id, '❌ Дни должны быть числовыми, а не нулевыми.')
        schedule_delete_message(message.chat.id, wait_msg.message_id)
        return bot.register_next_step_handler(wait_msg, add_time_step)
    schedule_delete_message(message.chat.id, message.message_id)
    msg = bot.send_message(
        message.chat.id,
        f"⚠️ Вы уверены? это изменит срок действия всех пользователей в соответствии с <b>{days} Днями</b>",
        parse_mode="html",
        reply_markup=BotKeyboard.confirm_action('add_time', days))
    cleanup_messages(message.chat.id)
    schedule_delete_message(message.chat.id, msg.id)


@bot.callback_query_handler(cb_query_startswith("inbound"), is_admin=True)
def inbound_command(call: types.CallbackQuery):
    bot.edit_message_text(
        f"Select inbound to *{call.data[8:].title()}* from all users",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.inbounds_menu(call.data, xray.config.inbounds_by_tag))


@bot.callback_query_handler(cb_query_startswith("confirm_inbound"), is_admin=True)
def delete_expired_confirm_command(call: types.CallbackQuery):
    bot.edit_message_text(
        f"⚠️ Вы уверены? Это будет *{call.data[16:].replace(':', ' ')} для всех пользователей*‼️",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(action=call.data[8:]))


@bot.callback_query_handler(cb_query_startswith("edit:"), is_admin=True)
def edit_command(call: types.CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    username = call.data.split(":")[1]
    with GetDB() as db:
        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(
                call.id,
                '❌ Пользователь не найден.',
                show_alert=True
            )
        user = UserResponse.from_orm(db_user)
    mem_store.set(f'{call.message.chat.id}:username', username)
    mem_store.set(f'{call.message.chat.id}:data_limit', db_user.data_limit)
    mem_store.set(f'{call.message.chat.id}:expire_date', datetime.fromtimestamp(
        db_user.expire) if db_user.expire else None)
    mem_store.set(
        f'{call.message.chat.id}:protocols',
        {protocol.value: inbounds for protocol, inbounds in db_user.inbounds.items()})
    bot.edit_message_text(
        f"📝 Редактирование пользователя `{username}`",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.select_protocols(
            user.inbounds,
            "edit",
            username=username,
            data_limit=db_user.data_limit,
            expire_date=mem_store.get(f"{call.message.chat.id}:expire_date"),
        )
    )


@bot.callback_query_handler(cb_query_equals('help_edit'), is_admin=True)
def help_edit_command(call: types.CallbackQuery):
    bot.answer_callback_query(
        call.id,
        text="Нажмите кнопку (✏️ Редактировать), чтобы отредактировать",
        show_alert=True
    )


@bot.callback_query_handler(cb_query_equals('cancel'), is_admin=True)
def cancel_command(call: types.CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    return bot.edit_message_text(
        get_system_info(),
        call.message.chat.id,
        call.message.message_id,
        parse_mode="MarkdownV2",
        reply_markup=BotKeyboard.main_menu()
    )


@bot.callback_query_handler(cb_query_startswith('edit_user:'), is_admin=True)
def edit_user_command(call: types.CallbackQuery):
    _, username, action = call.data.split(":")
    schedule_delete_message(call.message.chat.id, call.message.id)
    cleanup_messages(call.message.chat.id)
    if action == "data":
        msg = bot.send_message(
            call.message.chat.id,
            '⬆️ Введите лимит данных (ГБ):\n⚠️ Отправьте 0 для неограниченного количества данных.',
            reply_markup=BotKeyboard.inline_cancel_action(f'user:{username}')
        )
        mem_store.set(f"{call.message.chat.id}:edit_msg_text", call.message.text)
        bot.clear_step_handler_by_chat_id(call.message.chat.id)
        bot.register_next_step_handler(
            call.message, edit_user_data_limit_step, username)
        schedule_delete_message(call.message.chat.id, msg.message_id)
    elif action == "expire":
        msg = bot.send_message(
            call.message.chat.id,
            '⬆️ Введите дату истечения срока действия (ГГГГ-ММ-ДД)\nИли вы можете использовать символ регулярного выражения: ^[0-9]{1,3}(M|D):\n⚠️ Отправьте 0, чтобы срок действия никогда не истекал.',
            reply_markup=BotKeyboard.inline_cancel_action(f'user:{username}'))
        mem_store.set(f"{call.message.chat.id}:edit_msg_text", call.message.text)
        bot.clear_step_handler_by_chat_id(call.message.chat.id)
        bot.register_next_step_handler(
            call.message, edit_user_expire_step, username=username)
        schedule_delete_message(call.message.chat.id, msg.message_id)


def edit_user_data_limit_step(message: types.Message, username: str):
    try:
        if float(message.text) < 0:
            wait_msg = bot.send_message(message.chat.id, '❌ Предел данных должен быть больше или равен 0.')
            schedule_delete_message(message.chat.id, wait_msg.message_id)
            return bot.register_next_step_handler(wait_msg, edit_user_data_limit_step, username=username)
        data_limit = float(message.text) * 1024 * 1024 * 1024
    except ValueError:
        wait_msg = bot.send_message(message.chat.id, '❌ Предел данных должен быть числом.')
        schedule_delete_message(message.chat.id, wait_msg.message_id)
        return bot.register_next_step_handler(wait_msg, edit_user_data_limit_step, username=username)
    mem_store.set(f'{message.chat.id}:data_limit', data_limit)
    schedule_delete_message(message.chat.id, message.message_id)
    text = mem_store.get(f"{message.chat.id}:edit_msg_text")
    mem_store.delete(f"{message.chat.id}:edit_msg_text")
    bot.send_message(
        message.chat.id,
        text or f"📝 Редактирование пользователя <code>{username}</code>",
        parse_mode="html",
        reply_markup=BotKeyboard.select_protocols(
            mem_store.get(f'{message.chat.id}:protocols'), "edit",
            username=username, data_limit=data_limit, expire_date=mem_store.get(f'{message.chat.id}:expire_date')))
    cleanup_messages(message.chat.id)


def edit_user_expire_step(message: types.Message, username: str):
    try:
        now = datetime.now()
        today = datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=23,
            minute=59,
            second=59
        )
        if re.match(r'^[0-9]{1,3}(M|m|D|d)$', message.text):
            expire_date = today
            number_pattern = r'^[0-9]{1,3}'
            number = int(re.findall(number_pattern, message.text)[0])
            symbol_pattern = r'(M|m|D|d)$'
            symbol = re.findall(symbol_pattern, message.text)[0].upper()
            if symbol == 'M':
                expire_date = today + relativedelta(months=number)
            elif symbol == 'D':
                expire_date = today + relativedelta(days=number)
        elif message.text != '0':
            expire_date = datetime.strptime(message.text, "%Y-%m-%d")
        else:
            expire_date = None
        if expire_date and expire_date < today:
            wait_msg = bot.send_message(message.chat.id, '❌ Дата истечения срока действия должна быть больше, чем сегодня.')
            schedule_delete_message(message.chat.id, wait_msg.message_id)
            return bot.register_next_step_handler(wait_msg, edit_user_expire_step, username=username)
    except ValueError:
        wait_msg = bot.send_message(
            message.chat.id,
            '❌ Дата истечения срока действия должна быть в формате ГГГГ-ММ-ДД.\nИли вы можете использовать символ регулярного выражения: ^[0-9]{1,3}(M|D)')
        schedule_delete_message(message.chat.id, wait_msg.message_id)
        return bot.register_next_step_handler(wait_msg, edit_user_expire_step, username=username)

    mem_store.set(f'{message.chat.id}:expire_date', expire_date)
    schedule_delete_message(message.chat.id, message.message_id)
    text = mem_store.get(f"{message.chat.id}:edit_msg_text")
    mem_store.delete(f"{message.chat.id}:edit_msg_text")
    bot.send_message(
        message.chat.id,
        text or f"📝 Редактирование пользователя <code>{username}</code>",
        parse_mode="html",
        reply_markup=BotKeyboard.select_protocols(
            mem_store.get(f'{message.chat.id}:protocols'), "edit",
            username=username, data_limit=mem_store.get(f'{message.chat.id}:data_limit'), expire_date=expire_date))
    cleanup_messages(message.chat.id)


@bot.callback_query_handler(cb_query_startswith('users:'), is_admin=True)
def users_command(call: types.CallbackQuery):
    page = int(call.data.split(':')[1]) if len(call.data.split(':')) > 1 else 1
    with GetDB() as db:
        total_pages = math.ceil(crud.get_users_count(db) / 10)
        users = crud.get_users(db, offset=(page - 1) * 10, limit=10, sort=[crud.UsersSortingOptions["-created_at"]])
        text = """👥 Пользователи: (Page {page}/{total_pages})
✅ Active
❌ Disabled
🕰 Expired
🪫 Limited
🔌 OnHold""".format(page=page, total_pages=total_pages)

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=BotKeyboard.user_list(
            users, page, total_pages=total_pages)
    )


def get_user_info_text(
        status: str, username: str, sub_url: str, data_limit: int = None,
        usage: int = None, expire: int = None, note: str = None,
        on_hold_expire_duration: int = None, on_hold_timeout: datetime = None) -> str:
    statuses = {
        'active': '✅',
        'expired': '🕰',
        'limited': '🪫',
        'disabled': '❌',
        'on_hold': '🔌',
    }
    text = f'''\
┌─{statuses[status]} <b>Status:</b> <code>{status.title()}</code>
│          └─<b>Пользователи:</b> <code>{username}</code>
│
├─🔋 <b>Data limit:</b> <code>{readable_size(data_limit) if data_limit else 'Unlimited'}</code>
│          └─<b>Data Used:</b> <code>{readable_size(usage) if usage else "-"}</code>
│
'''
    if status == UserStatus.on_hold:
        if on_hold_timeout:
            if isinstance(on_hold_timeout, int):
                timeout_str = datetime.fromtimestamp(on_hold_timeout).strftime("%Y-%m-%d")
            else:
                timeout_str = on_hold_timeout.strftime("%Y-%m-%d")
        else:
            timeout_str = 'Not set'
        
        text += f'''\
├─📅 <b>On Hold Duration:</b> <code>{on_hold_expire_duration // (24*60*60)} days</code>
│           └─<b>On Hold Timeout:</b> <code>{timeout_str}</code>
│
'''
    else:
        if expire:
            expiry_date = datetime.fromtimestamp(expire).date() if isinstance(expire, int) else expire.date()
            days_left = (expiry_date - datetime.now().date()).days
        else:
            expiry_date = 'Never'
            days_left = '-'
        
        text += f'''\
├─📅 <b>Expiry Date:</b> <code>{expiry_date}</code>
│           └─<b>Days left:</b> <code>{days_left}</code>
│
'''
    if note:
        text += f'├─📝 <b>Note:</b> <code>{note}</code>\n│\n'
    text += f'└─🚀 <b><a href="{sub_url}">Subscription</a>:</b> <code>{sub_url}</code>'
    return text


def get_template_info_text(
        id: int, data_limit: int, expire_duration: int, username_prefix: str, username_suffix: str, inbounds: dict):
    protocols = ""
    for p, inbounds in inbounds.items():
        protocols += f"\n├─ <b>{p.upper()}</b>\n"
        protocols += "├───" + ", ".join([f"<code>{i}</code>" for i in inbounds])
    text = f"""
📊 Template Info:
┌ ID: <b>{id}</b>
├ Data Limit: <b>{readable_size(data_limit) if data_limit else 'Unlimited'}</b>
├ Expire Date: <b>{(datetime.now() + relativedelta(seconds=expire_duration)).strftime('%Y-%m-%d') if expire_duration else 'Never'}</b>
├ Username Prefix: <b>{username_prefix if username_prefix else '🚫'}</b>
├ Username Suffix: <b>{username_suffix if username_suffix else '🚫'}</b>
├ Protocols: {protocols}
        """
    return text


@bot.callback_query_handler(cb_query_startswith('edit_note:'), is_admin=True)
def edit_note_command(call: types.CallbackQuery):
    username = call.data.split(':')[1]
    with GetDB() as db:
        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(call.id, '❌ User not found.', show_alert=True)
    schedule_delete_message(call.message.chat.id, call.message.id)
    cleanup_messages(call.message.chat.id)
    msg = bot.send_message(
        call.message.chat.id,
        f'<b>📝 Current Note:</b> <code>{db_user.note}</code>\n\nSend new Note for <code>{username}</code>',
        parse_mode="HTML",
        reply_markup=BotKeyboard.inline_cancel_action(f'user:{username}'))
    mem_store.set(f'{call.message.chat.id}:username', username)
    schedule_delete_message(call.message.chat.id, msg.id)
    bot.register_next_step_handler(msg, edit_note_step)


def edit_note_step(message: types.Message):
    note = message.text or ''
    if len(note) > 500:
        wait_msg = bot.send_message(message.chat.id, '❌ Note can not be more than 500 characters.')
        schedule_delete_message(message.chat.id, wait_msg.id)
        schedule_delete_message(message.chat.id, message.id)
        return bot.register_next_step_handler(wait_msg, edit_note_step)
    with GetDB() as db:
        username = mem_store.get(f'{message.chat.id}:username')
        if not username:
            cleanup_messages(message.chat.id)
            bot.reply_to(message, '❌ Something went wrong!\n restart bot /start')
        db_user = crud.get_user(db, username)
        last_note = db_user.note
        modify = UserModify(note=note)
        db_user = crud.update_user(db, db_user, modify)
        user = UserResponse.from_orm(db_user)
        text = get_user_info_text(
            status=user.status,
            username=user.username,
            sub_url=user.subscription_url,
            expire=user.expire,
            data_limit=user.data_limit,
            usage=user.used_traffic,
            note=note or ' ')
        bot.reply_to(message, text, parse_mode="html", reply_markup=BotKeyboard.user_menu(user_info={
            'status': user.status,
            'username': user.username}, note=note))
        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
📝 <b>#Edit_Note #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username :</b> <code>{user.username}</code>
<b>Last Note :</b> <code>{last_note}</code>
<b>New Note :</b> <code>{user.note}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={message.chat.id}">{message.from_user.full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass


@bot.callback_query_handler(cb_query_startswith('user:'), is_admin=True)
def user_command(call: types.CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    username = call.data.split(':')[1]
    page = int(call.data.split(':')[2]) if len(call.data.split(':')) > 2 else 1
    with GetDB() as db:
        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(
                call.id,
                '❌ User not found.',
                show_alert=True
            )
        user = UserResponse.from_orm(db_user)
    try:
        note = user.note or ' '
    except:
        note = None
    if user.status == UserStatus.on_hold:
        text = get_user_info_text(
            status=user.status,
            username=user.username,
            sub_url=user.subscription_url,
            data_limit=user.data_limit,
            usage=user.used_traffic,
            on_hold_expire_duration=user.on_hold_expire_duration,
            on_hold_timeout=user.on_hold_timeout,
            note=note
            )
    else:
        text = get_user_info_text(
            status=user.status,
            username=user.username,
            sub_url=user.subscription_url,
            data_limit=user.data_limit,
            usage=user.used_traffic,
            expire=user.expire,
            note=note
            )
    bot.edit_message_text(
        text,
        call.message.chat.id, call.message.message_id, parse_mode="HTML",
        reply_markup=BotKeyboard.user_menu(
            {'username': user.username, 'status': user.status},
            page=page, note=note))


@bot.callback_query_handler(cb_query_startswith("revoke_sub:"), is_admin=True)
def revoke_sub_command(call: types.CallbackQuery):
    username = call.data.split(":")[1]
    bot.edit_message_text(
        f"⚠️ Are you sure? This will *Revoke Subscription* link for `{username}`‼️",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="markdown",
        reply_markup=BotKeyboard.confirm_action(action=call.data))


@bot.callback_query_handler(cb_query_startswith("links:"), is_admin=True)
def links_command(call: types.CallbackQuery):
    username = call.data.split(":")[1]

    with GetDB() as db:
        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(call.id, "User not found!", show_alert=True)

        user = UserResponse.from_orm(db_user)

    text = f"<code>{user.subscription_url}</code>\n\n\n"
    for link in user.links:
        if len(text) > 4056 :
            text += '\n\n<b>...</b>'
            break

    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=BotKeyboard.show_links(username)
    )


@bot.callback_query_handler(cb_query_startswith("genqr:"), is_admin=True)
def genqr_command(call: types.CallbackQuery):
    qr_select = call.data.split(":")[1]
    username = call.data.split(":")[2]

    with GetDB() as db:
        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(call.id, "User not found!", show_alert=True)

        user = UserResponse.from_orm(db_user)

    bot.answer_callback_query(call.id, "Generating QR code...")

    if qr_select == 'configs':
        for link in user.links:
            f = io.BytesIO()
            qr = qrcode.QRCode(border=6)
            qr.add_data(link)
            qr.make_image().save(f)
            f.seek(0)
            bot.send_photo(
                call.message.chat.id,
                photo=f,
                caption=f"<code>{link}</code>",
                parse_mode="HTML"
            )
    else:
        with io.BytesIO() as f:
            qr = qrcode.QRCode(border=6)
            qr.add_data(user.subscription_url)
            qr.make_image().save(f)
            f.seek(0)
            bot.send_photo(
                call.message.chat.id,
                photo=f,
                caption=get_user_info_text(
                    status=user.status,
                    username=user.username,
                    sub_url=user.subscription_url,
                    data_limit=user.data_limit,
                    usage=user.used_traffic,
                    expire=user.expire
                ),
                parse_mode="HTML",
                reply_markup=BotKeyboard.subscription_page(user.subscription_url)
            )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

    text = f"<code>{user.subscription_url}</code>\n\n\n"
    for link in user.links:
        if len(text) > 4056 :
            text += '\n\n<b>...</b>'
            break
        text += f"<code>{link}</code>\n\n"

    bot.send_message(
        call.message.chat.id,
        text,
        "HTML",
        reply_markup=BotKeyboard.show_links(username)
    )


@bot.callback_query_handler(cb_query_startswith('template_charge:'), is_admin=True)
def template_charge_command(call: types.CallbackQuery):
    _, template_id, username = call.data.split(":")
    now = datetime.now()
    today = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=23,
        minute=59,
        second=59
    )
    with GetDB() as db:
        template = crud.get_user_template(db, template_id)
        if not template:
            return bot.answer_callback_query(call.id, "Template not found!", show_alert=True)
        template = UserTemplateResponse.from_orm(template)

        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(call.id, "User not found!", show_alert=True)
        user = UserResponse.from_orm(db_user)
        if (user.data_limit and not user.expire) or (not user.data_limit and user.expire):
            try:
                note = user.note or ' '
            except:
                note = None
            text = get_user_info_text(
                status='active', username=username, sub_url=user.subscription_url,
                expire=int(
                    ((datetime.fromtimestamp(user.expire) if user.expire else today) +
                     relativedelta(seconds=template.expire_duration)).timestamp()),
                data_limit=(user.data_limit - user.used_traffic + template.data_limit)
                if user.data_limit else template.data_limit, usage=0, note=note)
            bot.edit_message_text(
                f'''\
‼️ <b>If add template <u>Bandwidth</u> and <u>Time</u> to the user, the user will be this</b>:\n\n\
{text}\n\n\
<b>Add template <u>Bandwidth</u> and <u>Time</u> to user or Reset to <u>Template default</u></b>⁉️''',
                call.message.chat.id, call.message.message_id, parse_mode='html',
                reply_markup=BotKeyboard.charge_add_or_reset(
                    username=username, template_id=template_id))
        elif (not user.data_limit and not user.expire) or (user.used_traffic > user.data_limit) or (now > datetime.fromtimestamp(user.expire)):
            crud.reset_user_data_usage(db, db_user)
            expire_date = None
            if template.expire_duration:
                expire_date = today + relativedelta(seconds=template.expire_duration)
            modify = UserModify(
                status=UserStatusModify.active,
                expire=int(expire_date.timestamp()) if expire_date else 0,
                data_limit=template.data_limit,
            )
            db_user = crud.update_user(db, db_user, modify)
            xray.operations.add_user(db_user)

            try:
                note = user.note or ' '
            except:
                note = None
            text = get_user_info_text(
                status='active',
                username=username,
                sub_url=user.subscription_url,
                expire=int(expire_date.timestamp()),
                data_limit=template.data_limit,
                usage=0, note=note)
            bot.edit_message_text(
                f'🔋 User Successfully Charged!\n\n{text}',
                call.message.chat.id,
                call.message.message_id,
                parse_mode='html',
                reply_markup=BotKeyboard.user_menu(user_info={
                    'status': 'active',
                    'username': user.username}, note=note))
            if TELEGRAM_LOGGER_CHANNEL_ID:
                text = f'''\
🔋 <b>#Charged #Reset #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Template :</b> <code>{template.name}</code>
<b>Username :</b> <code>{user.username}</code>
➖➖➖➖➖➖➖➖➖
<u><b>Last status</b></u>
<b>├Traffic Limit :</b> <code>{readable_size(user.data_limit) if user.data_limit else "Unlimited"}</code>
<b>├Expire Date :</b> <code>\
{datetime.fromtimestamp(user.expire).strftime('%H:%M:%S %Y-%m-%d') if user.expire else "Never"}</code>
➖➖➖➖➖➖➖➖➖
<u><b>New status</b></u>
<b>├Traffic Limit :</b> <code>{readable_size(db_user.data_limit) if db_user.data_limit else "Unlimited"}</code>
<b>├Expire Date :</b> <code>\
{datetime.fromtimestamp(db_user.expire).strftime('%H:%M:%S %Y-%m-%d') if db_user.expire else "Never"}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={call.from_user.id}">{call.from_user.full_name}</a>'''
                try:
                    bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
                except:
                    pass
        else:
            try:
                note = user.note or ' '
            except:
                note = None
            text = get_user_info_text(
                status='active', username=username, sub_url=user.subscription_url,
                expire=int(
                    ((datetime.fromtimestamp(user.expire) if user.expire else today) +
                     relativedelta(seconds=template.expire_duration)).timestamp()),
                data_limit=(user.data_limit - user.used_traffic + template.data_limit)
                if user.data_limit else template.data_limit, usage=0, note=note)
            bot.edit_message_text(
                f'''\
‼️ <b>If add template <u>Bandwidth</u> and <u>Time</u> to the user, the user will be this</b>:\n\n\
{text}\n\n\
<b>Add template <u>Bandwidth</u> and <u>Time</u> to user or Reset to <u>Template default</u></b>⁉️''',
                call.message.chat.id, call.message.message_id, parse_mode='html',
                reply_markup=BotKeyboard.charge_add_or_reset(
                    username=username, template_id=template_id))


@bot.callback_query_handler(cb_query_startswith('charge:'), is_admin=True)
def charge_command(call: types.CallbackQuery):
    username = call.data.split(":")[1]
    with GetDB() as db:
        templates = crud.get_user_templates(db)
        if not templates:
            return bot.answer_callback_query(call.id, "You don't have any User Templates!")

        db_user = crud.get_user(db, username)
        if not db_user:
            return bot.answer_callback_query(call.id, "User not found!", show_alert=True)

    bot.edit_message_text(
        f"{call.message.html_text}\n\n🔢 Select <b>User Template</b> to charge:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode='html',
        reply_markup=BotKeyboard.templates_menu(
            {template.name: template.id for template in templates},
            username=username,
        )
    )


@bot.callback_query_handler(cb_query_equals('template_add_user'), is_admin=True)
def add_user_from_template_command(call: types.CallbackQuery):
    with GetDB() as db:
        templates = crud.get_user_templates(db)
        if not templates:
            return bot.answer_callback_query(call.id, "You don't have any User Templates!")

    bot.edit_message_text(
        "<b>Select a Template to create user from</b>:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode='html',
        reply_markup=BotKeyboard.templates_menu({template.name: template.id for template in templates})
    )


@bot.callback_query_handler(cb_query_startswith('template_add_user:'), is_admin=True)
def add_user_from_template(call: types.CallbackQuery):
    template_id = int(call.data.split(":")[1])
    with GetDB() as db:
        template = crud.get_user_template(db, template_id)
        if not template:
            return bot.answer_callback_query(call.id, "Template not found!", show_alert=True)
        template = UserTemplateResponse.from_orm(template)

    text = get_template_info_text(
        template_id, data_limit=template.data_limit, expire_duration=template.expire_duration,
        username_prefix=template.username_prefix, username_suffix=template.username_suffix,
        inbounds=template.inbounds)
    if template.username_prefix:
        text += f"\n⚠️ Username will be prefixed with <code>{template.username_prefix}</code>"
    if template.username_suffix:
        text += f"\n⚠️ Username will be suffixed with <code>{template.username_suffix}</code>"

    mem_store.set(f"{call.message.chat.id}:template_id", template.id)
    template_msg = bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML"
    )
    text = '👤 Enter username:\n⚠️ Username only can be 3 to 32 characters and contain a-z, A-Z, 0-9, and underscores in between.'
    msg = bot.send_message(
        call.message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=BotKeyboard.random_username(template_id=template.id)
    )
    schedule_delete_message(call.message.chat.id, template_msg.message_id, msg.id)
    bot.register_next_step_handler(template_msg, add_user_from_template_username_step)


@bot.callback_query_handler(cb_query_startswith('random'), is_admin=True)
def random_username(call: types.CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    template_id = int(call.data.split(":")[1] or 0)
    mem_store.delete(f'{call.message.chat.id}:template_id')

    username = ''.join([random.choice(string.ascii_letters)] + random.choices(string.ascii_letters + string.digits, k=7))


    schedule_delete_message(call.message.chat.id, call.message.id)
    cleanup_messages(call.message.chat.id)

    if not template_id:
        msg = bot.send_message(call.message.chat.id,
                               '⬆️ Enter Data Limit (GB):\n⚠️ Send 0 for unlimited.',
                               reply_markup=BotKeyboard.inline_cancel_action())
        schedule_delete_message(call.message.chat.id, msg.id)
        return bot.register_next_step_handler(call.message, add_user_data_limit_step, username=username)

    with GetDB() as db:
        template = crud.get_user_template(db, template_id)
        if template.username_prefix:
            username = template.username_prefix + username
        if template.username_suffix:
            username += template.username_suffix

        template = UserTemplateResponse.from_orm(template)
    mem_store.set(f"{call.message.chat.id}:username", username)
    mem_store.set(f"{call.message.chat.id}:data_limit", template.data_limit)
    mem_store.set(f"{call.message.chat.id}:protocols", template.inbounds)
    now = datetime.now()
    today = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=23,
        minute=59,
        second=59)
    expire_date = None
    if template.expire_duration:
        expire_date = today + relativedelta(seconds=template.expire_duration)
    mem_store.set(f"{call.message.chat.id}:expire_date", expire_date)

    text = f"📝 Creating user <code>{username}</code>\n" + get_template_info_text(
        id=template.id, data_limit=template.data_limit, expire_duration=template.expire_duration,
        username_prefix=template.username_prefix, username_suffix=template.username_suffix, inbounds=template.inbounds)

    bot.send_message(
        call.message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=BotKeyboard.select_protocols(
            template.inbounds,
            "create_from_template",
            username=username,
            data_limit=template.data_limit,
            expire_date=expire_date,))


def add_user_from_template_username_step(message: types.Message):
    template_id = mem_store.get(f"{message.chat.id}:template_id")
    if template_id is None:
        return bot.send_message(message.chat.id, "An error occured in the process! try again.")

    if not message.text:
        wait_msg = bot.send_message(message.chat.id, '❌ Username can not be empty.')
        schedule_delete_message(message.chat.id, wait_msg.message_id, message.message_id)
        return bot.register_next_step_handler(wait_msg, add_user_from_template_username_step)

    with GetDB() as db:
        username = message.text

        template = crud.get_user_template(db, template_id)
        if template.username_prefix:
            username = template.username_prefix + username
        if template.username_suffix:
            username += template.username_suffix

        match = re.match(r"^(?=\w{3,32}\b)[a-zA-Z0-9-_@.]+(?:_[a-zA-Z0-9-_@.]+)*$", username)
        if not match:
            wait_msg = bot.send_message(
                message.chat.id,
                '❌ Username only can be 3 to 32 characters and contain a-z, A-Z, 0-9, and underscores in between.')
            schedule_delete_message(message.chat.id, wait_msg.message_id, message.message_id)
            return bot.register_next_step_handler(wait_msg, add_user_from_template_username_step)

        if len(username) < 3:
            wait_msg = bot.send_message(
                message.chat.id,
                f"❌ Username can't be generated because is shorter than 32 characters! username: <code>{username}</code>",
                parse_mode="HTML")
            schedule_delete_message(message.chat.id, wait_msg.message_id, message.message_id)
            return bot.register_next_step_handler(wait_msg, add_user_from_template_username_step)
        elif len(username) > 32:
            wait_msg = bot.send_message(
                message.chat.id,
                f"❌ Username can't be generated because is longer than 32 characters! username: <code>{username}</code>",
                parse_mode="HTML")
            schedule_delete_message(message.chat.id, wait_msg.message_id, message.message_id)
            return bot.register_next_step_handler(wait_msg, add_user_from_template_username_step)

        if crud.get_user(db, username):
            wait_msg = bot.send_message(message.chat.id, '❌ Username already exists.')
            schedule_delete_message(message.chat.id, wait_msg.message_id, message.message_id)
            return bot.register_next_step_handler(wait_msg, add_user_from_template_username_step)
        template = UserTemplateResponse.from_orm(template)
    mem_store.set(f"{message.chat.id}:username", username)
    mem_store.set(f"{message.chat.id}:data_limit", template.data_limit)
    mem_store.set(f"{message.chat.id}:protocols", template.inbounds)
    now = datetime.now()
    today = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=23,
        minute=59,
        second=59
    )
    expire_date = None
    if template.expire_duration:
        expire_date = today + relativedelta(seconds=template.expire_duration)
    mem_store.set(f"{message.chat.id}:expire_date", expire_date)

    text = f"📝 Creating user <code>{username}</code>\n" + get_template_info_text(
        id=template.id, data_limit=template.data_limit, expire_duration=template.expire_duration,
        username_prefix=template.username_prefix, username_suffix=template.username_suffix, inbounds=template.inbounds)

    bot.send_message(
        message.chat.id,
        text,
        parse_mode="HTML",
        reply_markup=BotKeyboard.select_protocols(
            template.inbounds,
            "create_from_template",
            username=username,
            data_limit=template.data_limit,
            expire_date=expire_date,
        )
    )
    schedule_delete_message(message.chat.id, message.id)
    cleanup_messages(message.chat.id)


@bot.callback_query_handler(cb_query_equals('add_user'), is_admin=True)
def add_user_command(call: types.CallbackQuery):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:  # noqa
        pass
    username_msg = bot.send_message(
        call.message.chat.id,
        '👤 Enter username:\n⚠️Username only can be 3 to 32 characters and contain a-z, A-Z 0-9, and underscores in '
        'between.',
        reply_markup=BotKeyboard.random_username())
    schedule_delete_message(call.message.chat.id, username_msg.id)
    bot.register_next_step_handler(username_msg, add_user_username_step)


def add_user_username_step(message: types.Message):
    username = message.text
    if not username:
        wait_msg = bot.send_message(message.chat.id, '❌ Username can not be empty.')
        schedule_delete_message(message.chat.id, wait_msg.id)
        schedule_delete_message(message.chat.id, message.id)
        return bot.register_next_step_handler(wait_msg, add_user_username_step)
    if not re.match(r"^(?=\w{3,32}\b)[a-zA-Z0-9-_@.]+(?:_[a-zA-Z0-9-_@.]+)*$", username):
        wait_msg = bot.send_message(
            message.chat.id,
            '❌ Username only can be 3 to 32 characters and contain a-z, A-Z, 0-9, and underscores in between.')
        schedule_delete_message(message.chat.id, wait_msg.id)
        schedule_delete_message(message.chat.id, message.id)
        return bot.register_next_step_handler(wait_msg, add_user_username_step)
    with GetDB() as db:
        if crud.get_user(db, username):
            wait_msg = bot.send_message(message.chat.id, '❌ Username already exists.')
            schedule_delete_message(message.chat.id, wait_msg.id)
            schedule_delete_message(message.chat.id, message.id)
            return bot.register_next_step_handler(wait_msg, add_user_username_step)
    schedule_delete_message(message.chat.id, message.id)
    cleanup_messages(message.chat.id)
    msg = bot.send_message(message.chat.id,
                           '⬆️ Enter Data Limit (GB):\n⚠️ Send 0 for unlimited.',
                           reply_markup=BotKeyboard.inline_cancel_action())
    schedule_delete_message(message.chat.id, msg.id)
    bot.register_next_step_handler(msg, add_user_data_limit_step, username=username)


def add_user_data_limit_step(message: types.Message, username: str):
    try:
        if float(message.text) < 0:
            wait_msg = bot.send_message(message.chat.id, '❌ Data limit must be greater or equal to 0.')
            schedule_delete_message(message.chat.id, wait_msg.id)
            schedule_delete_message(message.chat.id, message.id)
            return bot.register_next_step_handler(wait_msg, add_user_data_limit_step, username=username)
        data_limit = float(message.text) * 1024 * 1024 * 1024
    except ValueError:
        wait_msg = bot.send_message(message.chat.id, '❌ Data limit must be a number.')
        schedule_delete_message(message.chat.id, wait_msg.id)
        schedule_delete_message(message.chat.id, message.id)
        return bot.register_next_step_handler(wait_msg, add_user_data_limit_step, username=username)
    
    schedule_delete_message(message.chat.id, message.id)
    cleanup_messages(message.chat.id)
    msg = bot.send_message(
        message.chat.id,
        '⚡ Select User Status:\nOn Hold: Expiration starts after the first connection\nActive: Expiration starts from now',
        reply_markup=BotKeyboard.user_status_select())
    schedule_delete_message(message.chat.id, msg.id)
    
    mem_store.set(f'{message.chat.id}:data_limit', data_limit)
    mem_store.set(f'{message.chat.id}:username', username)


@bot.callback_query_handler(cb_query_startswith('status:'), is_admin=True)
def add_user_status_step(call: types.CallbackQuery):
    user_status = call.data.split(':')[1]
    username = mem_store.get(f'{call.message.chat.id}:username')
    data_limit = mem_store.get(f'{call.message.chat.id}:data_limit')
    
    if user_status not in ['active', 'onhold']:
        return bot.answer_callback_query(call.id, '❌ Invalid status. Please choose Active or OnHold.')
    
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    
    if user_status == 'onhold':
        expiry_message = '⬆️ Enter Expire Days\nYou Can Use Regex Symbol: ^[0-9]{1,3}(M|D) :\n⚠️ Send 0 for never expire.'
    else:
        expiry_message = '⬆️ Enter Expire Date (YYYY-MM-DD)\nOr You Can Use Regex Symbol: ^[0-9]{1,3}(M|D) :\n⚠️ Send 0 for never expire.'
    
    msg = bot.send_message(
        call.message.chat.id,
        expiry_message,
        reply_markup=BotKeyboard.inline_cancel_action())
    schedule_delete_message(call.message.chat.id, msg.id)
    bot.register_next_step_handler(msg, add_user_expire_step, username=username, data_limit=data_limit, user_status=user_status)


def add_user_expire_step(message: types.Message, username: str, data_limit: int, user_status: str):
    try:
        now = datetime.now()
        today = datetime(year=now.year, month=now.month, day=now.day, hour=23, minute=59, second=59)
        
        if re.match(r'^[0-9]{1,3}(M|m|D|d)$', message.text):
            number_pattern = r'^[0-9]{1,3}'
            number = int(re.findall(number_pattern, message.text)[0])
            symbol_pattern = r'(M|m|D|d)$'
            symbol = re.findall(symbol_pattern, message.text)[0].upper()
            
            if user_status == 'onhold':
                if symbol == 'M':
                    expire_date = number * 30
                elif symbol == 'D':
                    expire_date = number
            else:  # active
                if symbol == 'M':
                    expire_date = today + relativedelta(months=number)
                elif symbol == 'D':
                    expire_date = today + relativedelta(days=number)
        elif message.text == '0':
            expire_date = None
        elif user_status == 'active':
            expire_date = datetime.strptime(message.text, "%Y-%m-%d")
            if expire_date < today:
                raise ValueError("Expire date must be greater than today.")
        else:
            raise ValueError("Invalid input for onhold status.")
    except ValueError as e:
        error_message = str(e) if str(e) != "Invalid input for onhold status." else "Invalid input. Please try again."
        wait_msg = bot.send_message(message.chat.id, f'❌ {error_message}')
        schedule_delete_message(message.chat.id, wait_msg.id)
        schedule_delete_message(message.chat.id, message.id)
        return bot.register_next_step_handler(wait_msg, add_user_expire_step, username=username, data_limit=data_limit, user_status=user_status)

    mem_store.set(f'{message.chat.id}:username', username)
    mem_store.set(f'{message.chat.id}:data_limit', data_limit)
    mem_store.set(f'{message.chat.id}:user_status', user_status)
    mem_store.set(f'{message.chat.id}:expire_date', expire_date)

    schedule_delete_message(message.chat.id, message.id)
    cleanup_messages(message.chat.id)
    bot.send_message(
        message.chat.id,
        'Select Protocols:\nUsername: {}\nData Limit: {}\nStatus: {}\nExpiry Date: {}'.format(
            mem_store.get(f'{message.chat.id}:username'),
            readable_size(mem_store.get(f'{message.chat.id}:data_limit')) if mem_store.get(f'{message.chat.id}:data_limit') else "Unlimited",
            mem_store.get(f'{message.chat.id}:user_status'),
            mem_store.get(f'{message.chat.id}:expire_date').strftime("%Y-%m-%d") if isinstance(mem_store.get(f'{message.chat.id}:expire_date'), datetime) else mem_store.get(f'{message.chat.id}:expire_date') if mem_store.get(f'{message.chat.id}:expire_date') else 'Never'
        ),
        reply_markup=BotKeyboard.select_protocols({}, action="create")
    )


@bot.callback_query_handler(cb_query_startswith('select_inbound:'), is_admin=True)
def select_inbounds(call: types.CallbackQuery):
    if not (username := mem_store.get(f'{call.message.chat.id}:username')):
        return bot.answer_callback_query(call.id, '❌ No user selected.', show_alert=True)
    protocols: dict[str, list[str]] = mem_store.get(f'{call.message.chat.id}:protocols', {})
    _, inbound, action = call.data.split(':')
    for protocol, inbounds in xray.config.inbounds_by_protocol.items():
        for i in inbounds:
            if i['tag'] != inbound:
                continue
            if not inbound in protocols[protocol]:
                protocols[protocol].append(inbound)
            else:
                protocols[protocol].remove(inbound)
            if len(protocols[protocol]) < 1:
                del protocols[protocol]

    mem_store.set(f'{call.message.chat.id}:protocols', protocols)

    if action in ["edit", "create_from_template"]:
        return bot.edit_message_text(
            call.message.text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=BotKeyboard.select_protocols(
                protocols,
                "edit",
                username=username,
                data_limit=mem_store.get(f"{call.message.chat.id}:data_limit"),
                expire_date=mem_store.get(f"{call.message.chat.id}:expire_date"))
        )
    bot.edit_message_text(
        call.message.text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=BotKeyboard.select_protocols(protocols, "create")
    )


@bot.callback_query_handler(cb_query_startswith('select_protocol:'), is_admin=True)
def select_protocols(call: types.CallbackQuery):
    if not (username := mem_store.get(f'{call.message.chat.id}:username')):
        return bot.answer_callback_query(call.id, '❌ No user selected.', show_alert=True)
    protocols: dict[str, list[str]] = mem_store.get(f'{call.message.chat.id}:protocols', {})
    _, protocol, action = call.data.split(':')
    if protocol in protocols:
        del protocols[protocol]
    else:
        protocols.update(
            {protocol: [inbound['tag'] for inbound in xray.config.inbounds_by_protocol[protocol]]})
    mem_store.set(f'{call.message.chat.id}:protocols', protocols)

    if action == ["edit", "create_from_template"]:
        return bot.edit_message_text(
            call.message.text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=BotKeyboard.select_protocols(
                protocols,
                "edit",
                username=username,
                data_limit=mem_store.get(f"{call.message.chat.id}:data_limit"),
                expire_date=mem_store.get(f"{call.message.chat.id}:expire_date"))
        )
    bot.edit_message_text(
        call.message.text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=BotKeyboard.select_protocols(protocols, action="create")
    )


@bot.callback_query_handler(cb_query_startswith('confirm:'), is_admin=True)
def confirm_user_command(call: types.CallbackQuery):
    data = call.data.split(':')[1]
    chat_id = call.from_user.id
    full_name = call.from_user.full_name
    now = datetime.now()
    today = datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=23,
        minute=59,
        second=59)
    if data == 'delete':
        username = call.data.split(':')[2]
        with GetDB() as db:
            db_user = crud.get_user(db, username)
            crud.remove_user(db, db_user)
            xray.operations.remove_user(db_user)

        bot.edit_message_text(
            '✅ User deleted.',
            call.message.chat.id,
            call.message.message_id,
            reply_markup=BotKeyboard.main_menu()
        )
        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
🗑 <b>#Deleted #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username :</b> <code>{db_user.username}</code>
<b>Traffic Limit :</b> <code>{readable_size(db_user.data_limit) if db_user.data_limit else "Unlimited"}</code>
<b>Expire Date :</b> <code>\
{datetime.fromtimestamp(db_user.expire).strftime('%H:%M:%S %Y-%m-%d') if db_user.expire else "Never"}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass
    elif data == "suspend":
        username = call.data.split(":")[2]
        with GetDB() as db:
            db_user = crud.get_user(db, username)
            crud.update_user(db, db_user, UserModify(
                status=UserStatusModify.disabled))
            xray.operations.remove_user(db_user)
            user = UserResponse.from_orm(db_user)
            try:
                note = user.note or ' '
            except:
                note = None
        bot.edit_message_text(
            get_user_info_text(
                status='disabled',
                username=username,
                sub_url=user.subscription_url,
                data_limit=db_user.data_limit,
                usage=db_user.used_traffic,
                expire=db_user.expire,
                note=note
            ),
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=BotKeyboard.user_menu(user_info={
                'status': 'disabled',
                'username': db_user.username
            }, note=note))
        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
❌ <b>#Disabled  #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username</b> : <code>{username}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass
    elif data == "activate":
        username = call.data.split(":")[2]
        with GetDB() as db:
            db_user = crud.get_user(db, username)
            crud.update_user(db, db_user, UserModify(
                status=UserStatusModify.active))
            xray.operations.add_user(db_user)
            user = UserResponse.from_orm(db_user)
            try:
                note = user.note or ' '
            except:
                note = None
        bot.edit_message_text(
            get_user_info_text(
                status='active',
                username=username,
                sub_url=user.subscription_url,
                data_limit=db_user.data_limit,
                usage=db_user.used_traffic,
                expire=db_user.expire,
                note=note
            ),
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=BotKeyboard.user_menu(user_info={
                'status': 'active',
                'username': db_user.username
            }, note=note))
        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
✅ <b>#Activated  #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username</b> : <code>{username}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass
    elif data == 'reset_usage':
        username = call.data.split(":")[2]
        with GetDB() as db:
            db_user = crud.get_user(db, username)
            crud.reset_user_data_usage(db, db_user)
            user = UserResponse.from_orm(db_user)
            try:
                note = user.note or ' '
            except:
                note = None
        bot.edit_message_text(
            get_user_info_text(
                status=user.status,
                username=username,
                sub_url=user.subscription_url,
                data_limit=user.data_limit,
                usage=user.used_traffic,
                expire=user.expire,
                note=note
            ),
            call.message.chat.id,
            call.message.message_id,
            parse_mode='HTML',
            reply_markup=BotKeyboard.user_menu(user_info={
                'status': user.status,
                'username': user.username
            }, note=note))
        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
🔁 <b>#Reset_usage  #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username</b> : <code>{username}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass
    elif data == 'restart':
        m = bot.edit_message_text(
            '🔄 Restarting XRay core...', call.message.chat.id, call.message.message_id)
        config = xray.config.include_db_users()
        xray.core.restart(config)
        for node_id, node in list(xray.nodes.items()):
            if node.connected:
                xray.operations.restart_node(node_id, config)
        bot.edit_message_text(
            '✅ XRay core restarted successfully.',
            m.chat.id, m.message_id,
            reply_markup=BotKeyboard.main_menu()
        )

    elif data in ['charge_add', 'charge_reset']:
        _, _, username, template_id = call.data.split(":")
        with GetDB() as db:
            template = crud.get_user_template(db, template_id)
            if not template:
                return bot.answer_callback_query(call.id, "Template not found!", show_alert=True)
            template = UserTemplateResponse.from_orm(template)

            db_user = crud.get_user(db, username)
            if not db_user:
                return bot.answer_callback_query(call.id, "User not found!", show_alert=True)
            user = UserResponse.from_orm(db_user)

            inbounds = template.inbounds
            proxies = {p.type.value: p.settings for p in db_user.proxies}

            for protocol in xray.config.inbounds_by_protocol:
                if protocol in inbounds and protocol not in db_user.inbounds:
                    proxies.update({protocol: {}})
                elif protocol in db_user.inbounds and protocol not in inbounds:
                    del proxies[protocol]

            crud.reset_user_data_usage(db, db_user)
            if data == 'charge_reset':
                expire_date = None
                if template.expire_duration:
                    expire_date = today + relativedelta(seconds=template.expire_duration)
                modify = UserModify(
                    status=UserStatus.active,
                    expire=int(expire_date.timestamp()) if expire_date else 0,
                    data_limit=template.data_limit,
                )
            else:
                expire_date = None
                if template.expire_duration:
                    expire_date = (datetime.fromtimestamp(user.expire)
                                   if user.expire else today) + relativedelta(seconds=template.expire_duration)
                modify = UserModify(
                    status=UserStatus.active,
                    expire=int(expire_date.timestamp()) if expire_date else 0,
                    data_limit=(user.data_limit or 0) - user.used_traffic + template.data_limit,
                )
            db_user = crud.update_user(db, db_user, modify)
            xray.operations.add_user(db_user)

            try:
                note = user.note or ' '
            except:
                note = None
            text = get_user_info_text(
                status=db_user.status,
                username=username,
                sub_url=user.subscription_url,
                expire=db_user.expire,
                data_limit=db_user.data_limit,
                usage=db_user.used_traffic,
                note=note)

            bot.edit_message_text(
                f'🔋 User Successfully Charged!\n\n{text}',
                call.message.chat.id,
                call.message.message_id,
                parse_mode='html',
                reply_markup=BotKeyboard.user_menu(user_info={
                    'status': user.status,
                    'username': user.username
                }, note=note))
            if TELEGRAM_LOGGER_CHANNEL_ID:
                text = f'''\
🔋 <b>#Charged #{data.split('_')[1].title()} #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Template :</b> <code>{template.name}</code>
<b>Username :</b> <code>{user.username}</code>
➖➖➖➖➖➖➖➖➖
<u><b>Last status</b></u>
<b>├Traffic Limit :</b> <code>{readable_size(user.data_limit) if user.data_limit else "Unlimited"}</code>
<b>├Expire Date :</b> <code>\
{datetime.fromtimestamp(user.expire).strftime('%H:%M:%S %Y-%m-%d') if user.expire else "Never"}</code>
➖➖➖➖➖➖➖➖➖
<u><b>New status</b></u>
<b>├Traffic Limit :</b> <code>{readable_size(db_user.data_limit) if db_user.data_limit else "Unlimited"}</code>
<b>├Expire Date :</b> <code>\
{datetime.fromtimestamp(db_user.expire).strftime('%H:%M:%S %Y-%m-%d') if db_user.expire else "Never"}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>\
'''
                try:
                    bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
                except:
                    pass

    elif data == 'edit_user':
        if (username := mem_store.get(f'{call.message.chat.id}:username')) is None:
            try:
                bot.delete_message(call.message.chat.id,
                                   call.message.message_id)
            except Exception:
                pass
            return bot.send_message(
                call.message.chat.id,
                '❌ Bot reload detected. Please start over.',
                reply_markup=BotKeyboard.main_menu()
            )

        if not mem_store.get(f'{call.message.chat.id}:protocols'):
            return bot.answer_callback_query(
                call.id,
                '❌ No inbounds selected.',
                show_alert=True
            )

        inbounds: dict[str, list[str]] = {
            k: v for k, v in mem_store.get(f'{call.message.chat.id}:protocols').items() if v}

        with GetDB() as db:
            db_user = crud.get_user(db, username)
            if not db_user:
                return bot.answer_callback_query(call.id, text=f"User not found!", show_alert=True)

            proxies = {p.type.value: p.settings for p in db_user.proxies}

            for protocol in xray.config.inbounds_by_protocol:
                if protocol in inbounds and protocol not in db_user.inbounds:
                    proxies.update({protocol: {'flow': TELEGRAM_DEFAULT_VLESS_FLOW} if
                                    TELEGRAM_DEFAULT_VLESS_FLOW and protocol == ProxyTypes.VLESS else {}})
                elif protocol in db_user.inbounds and protocol not in inbounds:
                    del proxies[protocol]

            modify = UserModify(
                expire=int(mem_store.get(f'{call.message.chat.id}:expire_date').timestamp())
                if mem_store.get(f'{call.message.chat.id}:expire_date') else 0, data_limit=mem_store.get(
                    f"{call.message.chat.id}:data_limit"),
                proxies=proxies, inbounds=inbounds)
            last_user = UserResponse.from_orm(db_user)
            db_user = crud.update_user(db, db_user, modify)

            user = UserResponse.from_orm(db_user)

        if user.status == UserStatus.active:
            xray.operations.update_user(db_user)
        else:
            xray.operations.remove_user(db_user)

        bot.answer_callback_query(call.id, "✅ User updated successfully.")

        try:
            note = user.note or ' '
        except:
            note = None
        text = get_user_info_text(
            status=user.status,
            username=user.username,
            sub_url=user.subscription_url,
            data_limit=user.data_limit,
            usage=user.used_traffic,
            expire=user.expire,
            note=note
        )
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=BotKeyboard.user_menu({
                'username': db_user.username,
                'status': db_user.status},
                note=note)
        )
        if TELEGRAM_LOGGER_CHANNEL_ID:
            tag = f'\n➖➖➖➖➖➖➖➖➖ \n<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'
            if last_user.data_limit != user.data_limit:
                text = f'''\
📶 <b>#Traffic_Change #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username :</b> <code>{user.username}</code>
<b>Last Traffic Limit :</b> <code>{readable_size(last_user.data_limit) if last_user.data_limit else "Unlimited"}</code>
<b>New Traffic Limit :</b> <code>{readable_size(user.data_limit) if user.data_limit else "Unlimited"}</code>{tag}'''
                try:
                    bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
                except:
                    pass
            if last_user.expire != user.expire:
                text = f'''\
📅 <b>#Expiry_Change #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username :</b> <code>{user.username}</code>
<b>Last Expire Date :</b> <code>\
{datetime.fromtimestamp(last_user.expire).strftime('%H:%M:%S %Y-%m-%d') if last_user.expire else "Never"}</code>
<b>New Expire Date :</b> <code>\
{datetime.fromtimestamp(user.expire).strftime('%H:%M:%S %Y-%m-%d') if user.expire else "Never"}</code>{tag}'''
                try:
                    bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
                except:
                    pass
            if list(last_user.inbounds.values())[0] != list(user.inbounds.values())[0]:
                text = f'''\
⚙️ <b>#Inbounds_Change #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username :</b> <code>{user.username}</code>
<b>Last Proxies :</b> <code>{", ".join(list(last_user.inbounds.values())[0])}</code>
<b>New Proxies :</b> <code>{", ".join(list(user.inbounds.values())[0])}</code>{tag}'''
                try:
                    bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
                except:
                    pass

    elif data == 'add_user':
        if mem_store.get(f'{call.message.chat.id}:username') is None:
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            return bot.send_message(
                call.message.chat.id,
                '❌ Bot reload detected. Please start over.',
                reply_markup=BotKeyboard.main_menu()
            )

        if not mem_store.get(f'{call.message.chat.id}:protocols'):
            return bot.answer_callback_query(
                call.id,
                '❌ No inbounds selected.',
                show_alert=True
            )

        inbounds: dict[str, list[str]] = {
            k: v for k, v in mem_store.get(f'{call.message.chat.id}:protocols').items() if v}
        proxies = {p: ({'flow': TELEGRAM_DEFAULT_VLESS_FLOW} if
                    TELEGRAM_DEFAULT_VLESS_FLOW and p == ProxyTypes.VLESS else {}) for p in inbounds}

        user_status = mem_store.get(f'{call.message.chat.id}:user_status')
        
        if user_status == 'active':
            new_user = UserCreate(
                username=mem_store.get(f'{call.message.chat.id}:username'),
                status='active',
                expire=int(mem_store.get(f'{call.message.chat.id}:expire_date').timestamp())
                if mem_store.get(f'{call.message.chat.id}:expire_date') else None,
                data_limit=mem_store.get(f'{call.message.chat.id}:data_limit')
                if mem_store.get(f'{call.message.chat.id}:data_limit') else None,
                proxies=proxies,
                inbounds=inbounds)
        elif user_status == 'onhold':
            expire_days = mem_store.get(f'{call.message.chat.id}:expire_date')
            
            new_user = UserCreate(
                username=mem_store.get(f'{call.message.chat.id}:username'),
                status='on_hold',
                on_hold_expire_duration=int(expire_days) * 24 * 60 * 60,
                on_hold_timeout=datetime.now() + timedelta(days=365),
                data_limit=mem_store.get(f'{call.message.chat.id}:data_limit')
                if mem_store.get(f'{call.message.chat.id}:data_limit') else None,
                proxies=proxies,
                inbounds=inbounds)
        else:
            return bot.answer_callback_query(
                call.id,
                '❌ Invalid user status.',
                show_alert=True
            )

        for proxy_type in new_user.proxies:
            if not xray.config.inbounds_by_protocol.get(proxy_type):
                return bot.answer_callback_query(
                    call.id,
                    f'❌ Protocol {proxy_type} is disabled on your server',
                    show_alert=True
                )

        try:
            with GetDB() as db:
                db_user = crud.create_user(db, new_user)
                proxies = db_user.proxies
                user = UserResponse.from_orm(db_user)
        except sqlalchemy.exc.IntegrityError:
            db.rollback()
            return bot.answer_callback_query(
                call.id,
                '❌ Username already exists.',
                show_alert=True
            )

        xray.operations.add_user(db_user)

        try:
            note = user.note or ' '
        except:
            note = None
        
        if user.status == 'on_hold':
            text = get_user_info_text(
                status=user.status,
                username=user.username,
                sub_url=user.subscription_url,
                data_limit=user.data_limit,
                usage=user.used_traffic,
                on_hold_expire_duration=user.on_hold_expire_duration,
                on_hold_timeout=user.on_hold_timeout,
                note=note
                )
        else:
            text = get_user_info_text(
                status=user.status,
                username=username,
                sub_url=user.subscription_url,
                data_limit=user.data_limit,
                usage=user.used_traffic,
                expire=user.expire,
                note=note
                )


        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=BotKeyboard.user_menu(user_info={'status': user.status, 'username': user.username}, note=note))

        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
    🆕 <b>#Created #From_Bot</b>
    ➖➖➖➖➖➖➖➖➖
    <b>Username :</b> <code>{user.username}</code>
    <b>Status :</b> <code>{'Active' if user_status == 'active' else 'On Hold'}</code>
    <b>Traffic Limit :</b> <code>{readable_size(user.data_limit) if user.data_limit else "Unlimited"}</code>
    '''
            if user_status == 'active':
                text += f'<b>Expire Date :</b> <code>{datetime.fromtimestamp(user.expire).strftime("%H:%M:%S %Y-%m-%d") if user.expire else "Never"}</code>\n'
            else:
                text += f'<b>On Hold Expire Duration :</b> <code>{new_user.on_hold_expire_duration // (24*60*60)} days</code>\n'
                text += f'<b>On Hold Timeout :</b> <code>{datetime.fromtimestamp(new_user.on_hold_timeout).strftime("%H:%M:%S %Y-%m-%d")}</code>\n'
            
            text += f'''\
    <b>Proxies :</b> <code>{"" if not proxies else ", ".join([proxy.type for proxy in proxies])}</code>
    ➖➖➖➖➖➖➖➖➖
    <b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass

    elif data in ['delete_expired', 'delete_limited']:
        bot.edit_message_text(
            '⏳ <b>In Progress...</b>',
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML")
        with GetDB() as db:
            depleted_users = crud.get_users(
                db, status=[UserStatus.limited if data == 'delete_limited' else UserStatus.expired])
            file_name = f'{data[8:]}_users_{int(now.timestamp()*1000)}.txt'
            with open(file_name, 'w') as f:
                f.write('USERNAME\tEXIPRY\tUSAGE/LIMIT\tSTATUS\n')
                deleted = 0
                for user in depleted_users:
                    try:
                        crud.remove_user(db, user)
                        xray.operations.remove_user(user)
                        deleted += 1
                        f.write(
                            f'{user.username}\
\t{datetime.fromtimestamp(user.expire) if user.expire else "never"}\
\t{readable_size(user.used_traffic) if user.used_traffic else 0}\
/{readable_size(user.data_limit) if user.data_limit else "Unlimited"}\
\t{user.status}\n')
                    except:
                        db.rollback()
            bot.edit_message_text(
                f'✅ <code>{deleted}</code>/<code>{len(depleted_users)}</code> <b>{data[7:].title()} Users Deleted</b>',
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=BotKeyboard.main_menu())
            if TELEGRAM_LOGGER_CHANNEL_ID:
                text = f'''\
🗑 <b>#Delete #{data[7:].title()} #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Count:</b> <code>{deleted}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
                try:
                    bot.send_document(TELEGRAM_LOGGER_CHANNEL_ID, open(
                        file_name, 'rb'), caption=text, parse_mode='HTML')
                    os.remove(file_name)
                except:
                    pass
    elif data == 'add_data':
        schedule_delete_message(
            call.message.chat.id,
            bot.send_message(chat_id, '⏳ <b>In Progress...</b>', 'HTML').id)
        data_limit = float(call.data.split(":")[2]) * 1024 * 1024 * 1024
        with GetDB() as db:
            users = crud.get_users(db)
            counter = 0
            file_name = f'new_data_limit_users_{int(now.timestamp()*1000)}.txt'
            with open(file_name, 'w') as f:
                f.write('USERNAME\tEXIPRY\tUSAGE/LIMIT\tSTATUS\n')
                for user in users:
                    try:
                        if user.data_limit and user.status not in [UserStatus.limited, UserStatus.expired]:
                            user = crud.update_user(db, user, UserModify(data_limit=(user.data_limit + data_limit)))
                            counter += 1
                            f.write(
                                f'{user.username}\
\t{datetime.fromtimestamp(user.expire) if user.expire else "never"}\
\t{readable_size(user.used_traffic) if user.used_traffic else 0}\
/{readable_size(user.data_limit) if user.data_limit else "Unlimited"}\
\t{user.status}\n')
                    except:
                        db.rollback()
            cleanup_messages(chat_id)
            bot.send_message(
                chat_id,
                f'✅ <b>{counter}/{len(users)} Users</b> Data Limit according to <code>{"+" if data_limit > 0 else "-"}{readable_size(abs(data_limit))}</code>',
                'HTML',
                reply_markup=BotKeyboard.main_menu())
            if TELEGRAM_LOGGER_CHANNEL_ID:
                text = f'''\
📶 <b>#Traffic_Change #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>According to:</b> <code>{"+" if data_limit > 0 else "-"}{readable_size(abs(data_limit))}</code>
<b>Count:</b> <code>{counter}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
                try:
                    bot.send_document(TELEGRAM_LOGGER_CHANNEL_ID, open(
                        file_name, 'rb'), caption=text, parse_mode='HTML')
                    os.remove(file_name)
                except:
                    pass

    elif data == 'add_time':
        schedule_delete_message(
            call.message.chat.id,
            bot.send_message(chat_id, '⏳ <b>In Progress...</b>', 'HTML').id)
        days = int(call.data.split(":")[2])
        with GetDB() as db:
            users = crud.get_users(db)
            counter = 0
            file_name = f'new_expiry_users_{int(now.timestamp()*1000)}.txt'
            with open(file_name, 'w') as f:
                f.write('USERNAME\tEXIPRY\tUSAGE/LIMIT\tSTATUS\n')
                for user in users:
                    try:
                        if user.expire and user.status not in [UserStatus.limited, UserStatus.expired]:
                            user = crud.update_user(
                                db, user,
                                UserModify(
                                    expire=int(
                                        (datetime.fromtimestamp(user.expire) + relativedelta(days=days)).timestamp())))
                            counter += 1
                            f.write(
                                f'{user.username}\
\t{datetime.fromtimestamp(user.expire) if user.expire else "never"}\
\t{readable_size(user.used_traffic) if user.used_traffic else 0}\
/{readable_size(user.data_limit) if user.data_limit else "Unlimited"}\
\t{user.status}\n')
                    except:
                        db.rollback()
            cleanup_messages(chat_id)
            bot.send_message(
                chat_id,
                f'✅ <b>{counter}/{len(users)} Users</b> Expiry Changes according to {days} Days',
                'HTML',
                reply_markup=BotKeyboard.main_menu())
            if TELEGRAM_LOGGER_CHANNEL_ID:
                text = f'''\
📅 <b>#Expiry_Change #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>According to:</b> <code>{days} Days</code>
<b>Count:</b> <code>{counter}</code>
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
                try:
                    bot.send_document(TELEGRAM_LOGGER_CHANNEL_ID, open(
                        file_name, 'rb'), caption=text, parse_mode='HTML')
                    os.remove(file_name)
                except:
                    pass
    elif data in ['inbound_add', 'inbound_remove']:
        bot.edit_message_text(
            '⏳ <b>In Progress...</b>',
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML")
        inbound = call.data.split(":")[2]
        with GetDB() as db:
            users = crud.get_users(db)
            unsuccessful = 0
            for user in users:
                inbound_tags = [j for i in user.inbounds for j in user.inbounds[i]]
                protocol = xray.config.inbounds_by_tag[inbound]['protocol']
                new_inbounds = user.inbounds
                if data == 'inbound_add':
                    if inbound not in inbound_tags:
                        if protocol in list(new_inbounds.keys()):
                            new_inbounds[protocol].append(inbound)
                        else:
                            new_inbounds[protocol] = [inbound]
                elif data == 'inbound_remove':
                    if inbound in inbound_tags:
                        if len(new_inbounds[protocol]) == 1:
                            del new_inbounds[protocol]
                        else:
                            new_inbounds[protocol].remove(inbound)
                if (data == 'inbound_remove' and inbound in inbound_tags)\
                        or (data == 'inbound_add' and inbound not in inbound_tags):
                    proxies = {p.type.value: p.settings for p in user.proxies}
                    for protocol in xray.config.inbounds_by_protocol:
                        if protocol in new_inbounds and protocol not in user.inbounds:
                            proxies.update({protocol: {'flow': TELEGRAM_DEFAULT_VLESS_FLOW} if
                                            TELEGRAM_DEFAULT_VLESS_FLOW and protocol == ProxyTypes.VLESS else {}})
                        elif protocol in user.inbounds and protocol not in new_inbounds:
                            del proxies[protocol]
                    try:
                        user = crud.update_user(db, user, UserModify(inbounds=new_inbounds, proxies=proxies))
                        if user.status == UserStatus.active:
                            xray.operations.update_user(user)
                    except:
                        db.rollback()
                        unsuccessful += 1

            bot.edit_message_text(
                f'✅ <b>{data[8:].title()}</b> <code>{inbound}</code> <b>Users Successfully</b>' +
                (f'\n Unsuccessful: <code>{unsuccessful}</code>' if unsuccessful else ''),
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=BotKeyboard.main_menu())

            if TELEGRAM_LOGGER_CHANNEL_ID:
                text = f'''\
✏️ <b>#Modified #Inbound_{data[8:].title()} #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Inbound:</b> <code>{inbound}</code> 
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
                try:
                    bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
                except:
                    pass

    elif data == 'revoke_sub':
        username = call.data.split(":")[2]
        with GetDB() as db:
            db_user = crud.get_user(db, username)
            if not db_user:
                return bot.answer_callback_query(call.id, text=f"User not found!", show_alert=True)
            db_user = crud.revoke_user_sub(db, db_user)
            user = UserResponse.from_orm(db_user)
            try:
                note = user.note or ' '
            except:
                note = None
        text = get_user_info_text(
            status=user.status,
            username=user.username,
            sub_url=user.subscription_url,
            expire=user.expire,
            data_limit=user.data_limit,
            usage=user.used_traffic,
            note=note)
        bot.edit_message_text(
            f'✅ Subscription Successfully Revoked!\n\n{text}',
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=BotKeyboard.user_menu(user_info={'status': user.status, 'username': user.username}, note=note))

        if TELEGRAM_LOGGER_CHANNEL_ID:
            text = f'''\
🚫 <b>#Revoke_sub #From_Bot</b>
➖➖➖➖➖➖➖➖➖
<b>Username:</b> <code>{username}</code> 
➖➖➖➖➖➖➖➖➖
<b>By :</b> <a href="tg://user?id={chat_id}">{full_name}</a>'''
            try:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, text, 'HTML')
            except:
                pass


@bot.message_handler(commands=['user'], is_admin=True)
def search_user(message: types.Message):
    args = extract_arguments(message.text)
    if not args:
        return bot.reply_to(message,
                            "❌ You must pass some usernames\n\n"
                            "<b>Usage:</b> <code>/user username1 username2</code>",
                            parse_mode='HTML')

    usernames = args.split()

    with GetDB() as db:
        for username in usernames:
            db_user = crud.get_user(db, username)
            if not db_user:
                bot.reply_to(message, f'❌ User «{username}» not found.')
                continue
            user = UserResponse.from_orm(db_user)
            try:
                note = user.note or ' '
            except:
                note = None

            text = get_user_info_text(
                status=user.status,
                username=user.username,
                sub_url=user.subscription_url,
                expire=user.expire,
                data_limit=user.data_limit,
                usage=user.used_traffic,
                note=note)
            bot.reply_to(message, text, parse_mode="html", reply_markup=BotKeyboard.user_menu(user_info={
                'status': user.status,
                'username': user.username
            }, note=note))
