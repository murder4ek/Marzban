import datetime

from app import logger
from app.telegram import bot
from telebot.apihelper import ApiTelegramException
from datetime import datetime
from app.telegram.utils.keyboard import BotKeyboard
from app.utils.system import readable_size
from config import TELEGRAM_ADMIN_ID, TELEGRAM_LOGGER_CHANNEL_ID
from telebot.formatting import escape_html
from app.models.admin import Admin
from app.models.user import UserDataLimitResetStrategy


def report(admin_id: int, message: str, parse_mode="html", keyboard=None):
    if bot and (TELEGRAM_ADMIN_ID or TELEGRAM_LOGGER_CHANNEL_ID):
        try:
            if TELEGRAM_LOGGER_CHANNEL_ID:
                bot.send_message(TELEGRAM_LOGGER_CHANNEL_ID, message, parse_mode=parse_mode)
            else:
                for admin in TELEGRAM_ADMIN_ID:
                    bot.send_message(admin, message, parse_mode=parse_mode, reply_markup=keyboard)
            if admin_id:
                bot.send_message(admin_id, message, parse_mode=parse_mode)
        except ApiTelegramException as e:
            logger.error(e)


def report_new_user(user_id: int, username: str, by: str, expire_date: int, data_limit: int, proxies: list, 
                    data_limit_reset_strategy:UserDataLimitResetStrategy, admin: Admin = None):
    text = '''\
🆕 <b>#Создать</b>
➖➖➖➖➖➖➖➖➖
<b>Никнейм :</b> <code>{username}</code>
<b>Лимит трафика :</b> <code>{data_limit}</code>
<b>Дата истечения срока действия :</b> <code>{expire_date}</code>
<b>Прокси :</b> <code>{proxies}</code>
<b>Стратегия сброса лимита данных :</b> <code>{data_limit_reset_strategy}</code>
➖➖➖➖➖➖➖➖➖
<b>Принадлежит :</b> <code>{belong_to}</code>
<b>К :</b> <b>#{by}</b>'''.format(
        belong_to=escape_html(admin.username) if admin else None,
        by=escape_html(by),
        username=escape_html(username),
        data_limit=readable_size(data_limit) if data_limit else "Unlimited",
        expire_date=datetime.fromtimestamp(expire_date).strftime("%H:%M:%S %Y-%m-%d") if expire_date else "Never",
        proxies="" if not proxies else ", ".join([escape_html(proxy) for proxy in proxies]),
        data_limit_reset_strategy=escape_html(data_limit_reset_strategy),
    )

    return report(
        admin_id=admin.telegram_id if admin and admin.telegram_id else None,
        message=text,
        keyboard=BotKeyboard.user_menu({
            'username': username,
            'id': user_id,
            'status': 'active'
        }, with_back=False)
    )


def report_user_modification(username: str, expire_date: int, data_limit: int, proxies: list, by: str, 
                             data_limit_reset_strategy:UserDataLimitResetStrategy, admin: Admin = None):
    text = '''\
✏️ <b>#Редактировать</b>
➖➖➖➖➖➖➖➖➖
<b>Никнейм :</b> <code>{username}</code>
<b>Лимит трафика :</b> <code>{data_limit}</code>
<b>Дата истечения срока действия :</b> <code>{expire_date}</code>
<b>Протоколы :</b> <code>{protocols}</code>
<b>Стратегия сброса лимита данных :</b> <code>{data_limit_reset_strategy}</code>
➖➖➖➖➖➖➖➖➖
<b>Принадлежит :</b> <code>{belong_to}</code>
<b>к :</b> <b>#{by}</b>\
    '''.format(
        belong_to=escape_html(admin.username) if admin else None,
        by=escape_html(by),
        username=escape_html(username),
        data_limit=readable_size(data_limit) if data_limit else "Unlimited",
        expire_date=datetime.fromtimestamp(expire_date).strftime("%H:%M:%S %Y-%m-%d") if expire_date else "Never",
        protocols=', '.join([p for p in proxies]),
        data_limit_reset_strategy=escape_html(data_limit_reset_strategy),
    )

    return report(
        admin_id=admin.telegram_id if admin and admin.telegram_id else None,
        message=text, 
        keyboard=BotKeyboard.user_menu({
        'username': username,
        'status': 'active'
    }, with_back=False))


def report_user_deletion(username: str, by: str, admin: Admin = None):
    text = '''\
🗑 <b>#Удалить</b>
➖➖➖➖➖➖➖➖➖
<b>Никнейм</b> : <code>{username}</code>
➖➖➖➖➖➖➖➖➖
<b>Принадлежит :</b> <code>{belong_to}</code>
<b>к</b> : <b>#{by}</b>\
    '''.format(
        belong_to=escape_html(admin.username) if admin else None,
        by=escape_html(by),
        username=escape_html(username)
    )
    return report(
        admin_id=admin.telegram_id if admin and admin.telegram_id else None,
        message=text
        )


def report_status_change(username: str, status: str, admin: Admin = None):
    _status = {
        'active': '✅ <b>#Активированный</b>',
        'disabled': '❌ <b>#Заблокированный</b>',
        'limited': '🪫 <b>#Лимит</b>',
        'expired': '🕔 <b>#Время</b>'
    }
    text = '''\
{status}
➖➖➖➖➖➖➖➖➖
<b>Никнейм</b> : <code>{username}</code>
<b>Принадлежит :</b> <code>{belong_to}</code>\
    '''.format(
        belong_to=escape_html(admin.username) if admin else None,
        username=escape_html(username),
        status=_status[status]
    )
    return report(
        admin_id=admin.telegram_id if admin and admin.telegram_id else None,
        message=text
        )


def report_user_usage_reset(username: str, by: str, admin: Admin = None):
    text = """  
🔁 <b>#Перезагрузить</b>
➖➖➖➖➖➖➖➖➖
<b>Никнейм</b> : <code>{username}</code>
➖➖➖➖➖➖➖➖➖
<b>Принадлежит :</b> <code>{belong_to}</code>
<b>К</b> : <b>#{by}</b>\
    """.format(
        belong_to=escape_html(admin.username) if admin else None,
        by=escape_html(by),
        username=escape_html(username)
    )

    return report(
        admin_id=admin.telegram_id if admin and admin.telegram_id else None,
        message=text
        )


def report_user_subscription_revoked(username: str, by: str, admin: Admin = None):
    text = """  
🔁 <b>#Отозван</b>
➖➖➖➖➖➖➖➖➖
<b>Никнейм</b> : <code>{username}</code>
➖➖➖➖➖➖➖➖➖
<b>Принадлежит :</b> <code>{belong_to}</code>
<b>К</b> : <b>#{by}</b>\
    """.format(
        belong_to=escape_html(admin.username) if admin else None,
        by=escape_html(by),
        username=escape_html(username)
    )

    return report(
        admin_id=admin.telegram_id if admin and admin.telegram_id else None,
        message=text
        )

def report_login(username: str, password: str, client_ip: str, status: str):
    text = """  
🔐 <b>#Авторизация</b>
➖➖➖➖➖➖➖➖➖
<b>Никнейм</b> : <code>{username}</code>
<b>Пароль</b> : <code>{password}</code>
<b>IP клиента </b>: <code>{client_ip}</code>
➖➖➖➖➖➖➖➖➖
<b>login status </b>: <code>{status}</code>  
    """.format(
        username=escape_html(username),
        password=escape_html(password),
        status=escape_html(status),
        client_ip=escape_html(client_ip)
    )

    return report(
        admin_id=None,
        message=text
        )