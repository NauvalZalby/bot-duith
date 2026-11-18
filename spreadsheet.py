import csv
import io
import json
import os
import random
import re
import string
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# ----------------------------------------------------
# 1. KONFIGURASI BOT & GOOGLE SHEETS
# ----------------------------------------------------
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8819678108:AAGDTan1sRb9SnwLrMAyjTmxCUOVqoG2uCg')
SERVICE_ACCOUNT_FILE = 'credentials.json'
USER_DATA_FILE = 'user_sheets.json'

CATEGORIES = [
    'Elektronik',
    'Makanan/Minuman',
    'Transportasi',
    'Hiburan',
    'Kesehatan',
    'Tagihan & Rutin',
    'Belanja',
    'Lainnya'
]

CATEGORY_EMOJI = {
    'Elektronik': '💻',
    'Makanan/Minuman': '🍔',
    'Transportasi': '🚘',
    'Hiburan': '🎬',
    'Kesehatan': '🏥',
    'Tagihan & Rutin': '💡',
    'Belanja': '🛍️',
    'Lainnya': '📦'
}

PENDING_TX = {}

HARI_INDONESIA = ['Senin', 'Selasa', 'Rabu', 'Kamis', 'Jumat', 'Sabtu', 'Minggu']
BULAN_INDONESIA = {
    1: 'Januari', 2: 'Februari', 3: 'Maret', 4: 'April',
    5: 'Mei', 6: 'Juni', 7: 'Juli', 8: 'Agustus',
    9: 'September', 10: 'Oktober', 11: 'November', 12: 'Desember'
}

bot = telebot.TeleBot(TELEGRAM_TOKEN)


# ----------------------------------------------------
# HELPER DATA & GOOGLE SHEETS CLIENT
# ----------------------------------------------------
def format_rp(nominal):
    return f"Rp{nominal:,.0f}".replace(",", ".")


def generate_short_id():
    chars = string.ascii_uppercase + string.digits
    length = random.choice([3, 4])
    return ''.join(random.choices(chars, k=length))


def format_sheet_appearance(sheet):
    try:
        sheet.format("A1:F1", {
            "backgroundColor": {
                "red": 0.05,
                "green": 0.45,
                "blue": 0.35
            },
            "textFormat": {
                "bold": True,
                "foregroundColor": {
                    "red": 1.0,
                    "green": 1.0,
                    "blue": 1.0
                },
                "fontSize": 11
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE"
        })
    except Exception:
        pass


def clean_category_text(kat):
    if not kat:
        return 'Lainnya'
    cleaned = re.sub(r'^[^\w\s/&]+', '', str(kat)).strip()
    text = cleaned.lower()
    if 'elektronik' in text or 'software' in text:
        return 'Elektronik'
    elif 'makan' in text or 'minum' in text:
        return 'Makanan/Minuman'
    return cleaned if cleaned else 'Lainnya'


def get_cat_emoji(kat):
    if not kat:
        return '📦'
    text = str(kat).lower()
    if 'elektronik' in text or 'software' in text or '💻' in text:
        return '💻'
    elif 'makan' in text or 'minum' in text or '🍔' in text:
        return '🍔'
    elif 'trans' in text or '🚘' in text or '🚗' in text:
        return '🚘'
    elif 'hiburan' in text or '🎬' in text:
        return '🎬'
    elif 'sehat' in text or 'kesehatan' in text or '🏥' in text:
        return '🏥'
    elif 'tagihan' in text or 'rutin' in text or '💡' in text:
        return '💡'
    elif 'belanja' in text or '🛍️' in text:
        return '🛍️'
    else:
        return '📦'


def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_user_data(data):
    with open(USER_DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)


def get_user_sheet_url(user_id):
    user_data = load_user_data()
    user_id_str = str(user_id)
    if user_id_str in user_data:
        val = user_data[user_id_str]
        if isinstance(val, dict):
            return val.get('sheet_url')
        elif isinstance(val, str):
            return val
    return None


def is_user_connected(user_id):
    url = get_user_sheet_url(user_id)
    return bool(url and str(url).startswith('http'))


def get_bot_service_email():
    try:
        with open(SERVICE_ACCOUNT_FILE) as f:
            cred_json = json.load(f)
            return cred_json.get('client_email', 'email-service-account-kamu')
    except Exception:
        return 'email-service-account-kamu'


def get_gspread_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    return gspread.authorize(creds)


def get_user_worksheet(user_id):
    sheet_url_or_id = get_user_sheet_url(user_id)

    if not sheet_url_or_id:
        raise ValueError("Google Sheet belum terhubung. Silakan gunakan perintah /setsheet terlebih dahulu.")

    client = get_gspread_client()
    if str(sheet_url_or_id).startswith('http'):
        sheet = client.open_by_url(sheet_url_or_id).sheet1
    else:
        sheet = client.open_by_key(sheet_url_or_id).sheet1

    expected_headers = ['User ID', 'ID', 'Tanggal', 'Kategori', 'Keterangan', 'Nominal']
    headers = sheet.row_values(1)
    if not headers or headers != expected_headers:
        if not headers:
            sheet.append_row(expected_headers)
        format_sheet_appearance(sheet)

    return sheet


def get_all_transactions(user_id):
    try:
        sheet = get_user_worksheet(user_id)
        records = sheet.get_all_values()
    except Exception:
        return [], None

    if len(records) <= 1:
        return [], sheet

    data = []
    user_id_str = str(user_id)

    for idx, row in enumerate(records[1:], start=2):
        if len(row) >= 6:
            row_user_id = str(row[0]).strip()
            if row_user_id == user_id_str or not user_id_str:
                try:
                    nominal = int(re.sub(r'[^\d]', '', row[5]))
                except ValueError:
                    nominal = 0

                data.append({
                    'row_idx': idx,
                    'user_id': row_user_id,
                    'id': row[1],
                    'tanggal': row[2],
                    'kategori': row[3],
                    'keterangan': row[4],
                    'nominal': nominal
                })

    return data, sheet


# ----------------------------------------------------
# HELPER KEYBOARDS
# ----------------------------------------------------
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row(KeyboardButton('📊 Rekap Pengeluaran'))
    keyboard.row(KeyboardButton('🗑️ Kelola Transaksi'), KeyboardButton('⚙️ Pengaturan & Bantuan'))
    return keyboard


def get_category_selection_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(text=f"{CATEGORY_EMOJI.get(cat, '📦')} {cat}", callback_data=f"savecat_{idx}")
        for idx, cat in enumerate(CATEGORIES)
    ]
    keyboard.add(*buttons)
    keyboard.add(InlineKeyboardButton('❌ Batal', callback_data='cancel_tx'))
    return keyboard


def get_settings_inline_keyboard(sheet_url=None):
    keyboard = InlineKeyboardMarkup(row_width=1)
    btn_sheet = InlineKeyboardButton('🔗 Hubungkan / Ganti Sheet', callback_data='set_sheet_guide')
    btn_guide = InlineKeyboardButton('💡 Panduan & Format Catat', callback_data='set_guide')

    if sheet_url and str(sheet_url).startswith('http'):
        btn_open_sheet = InlineKeyboardButton('📄 Buka Google Sheet Saya', url=str(sheet_url))
    else:
        btn_open_sheet = InlineKeyboardButton('📄 Buka Google Sheet Saya', callback_data='no_sheet_info')

    btn_export = InlineKeyboardButton('📥 Export Data (CSV)', callback_data='set_export')
    btn_close = InlineKeyboardButton('❌ Tutup', callback_data='close_rekap')

    keyboard.add(btn_sheet, btn_guide, btn_open_sheet, btn_export, btn_close)
    return keyboard


def get_settings_back_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton('🔙 Kembali ke Pengaturan', callback_data='set_main'),
        InlineKeyboardButton('❌ Tutup', callback_data='close_rekap')
    )
    return keyboard


def get_rekap_inline_keyboard(active_mode=None):
    keyboard = InlineKeyboardMarkup(row_width=2)
    btn_1hari = InlineKeyboardButton(
        f"📅 1 Hari{' ✅' if active_mode == 'rekap_1hari' else ''}",
        callback_data='rekap_1hari',
    )
    btn_1minggu = InlineKeyboardButton(
        f"🗓️ 1 Minggu{' ✅' if active_mode == 'rekap_1minggu' else ''}",
        callback_data='rekap_1minggu',
    )
    btn_1bulan = InlineKeyboardButton(
        f"📆 1 Bulan{' ✅' if active_mode == 'rekap_1bulan' else ''}",
        callback_data='rekap_1bulan',
    )
    btn_semua = InlineKeyboardButton(
        f"📊 Semua{' ✅' if active_mode == 'rekap_semua' else ''}",
        callback_data='rekap_semua',
    )

    keyboard.add(btn_1hari, btn_1minggu)
    keyboard.add(btn_1bulan, btn_semua)
    keyboard.add(InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'))
    return keyboard


def send_not_connected_warning(chat_id):
    bot_email = get_bot_service_email()
    pesan = (
        '⚠️ *GOOGLE SHEET BELUM TERHUBUNG!*\n'
        '───────────────\n'
        'Kamu belum bisa mencatat transaksi karena akun kamu belum terhubung dengan Google Sheet.\n\n'
        '📌 *Cara Hubungkan Google Sheet:*\n'
        '1. Buat Google Sheet baru di Google Drive kamu.\n'
        '2. Bagikan (*Share*) Google Sheet tersebut ke email bot berikut sebagai *Editor*:\n'
        f'`{bot_email}`\n\n'
        '3. Kirim perintah ini di chat bot:\n'
        '`/setsheet LINK_GOOGLE_SHEET_KAMU`'
    )
    bot.send_message(chat_id, pesan, parse_mode='Markdown')


# ----------------------------------------------------
# KELOLA TRANSAKSI LIFETIME BUILDERS
# ----------------------------------------------------
def build_delete_month_menu(user_id):
    transactions, _ = get_all_transactions(user_id)

    if not transactions:
        return (
            '🗑️ *KELOLA TRANSAKSI*\n───────────────\nBelum ada catatan transaksi di Google Sheet kamu.',
            InlineKeyboardMarkup().add(
                InlineKeyboardButton('❌ Tutup', callback_data='close_rekap')
            ),
        )

    month_map = {}
    for tx in transactions:
        try:
            tgl_obj = datetime.strptime(tx['tanggal'], '%Y-%m-%d')
            m_key = tgl_obj.strftime('%Y-%m')
        except Exception:
            m_key = datetime.now().strftime('%Y-%m')

        if m_key not in month_map:
            month_map[m_key] = {'count': 0, 'total': 0}
        month_map[m_key]['count'] += 1
        month_map[m_key]['total'] += tx['nominal']

    pesan = (
        '🗑️ *KELOLA TRANSAKSI - PILIH BULAN*\n'
        '───────────────\n'
        'Pilih bulan transaksi yang ingin kamu kelola:\n'
    )

    keyboard = InlineKeyboardMarkup(row_width=1)
    sorted_months = sorted(month_map.keys(), reverse=True)
    for month_key in sorted_months:
        thn, bln = month_key.split('-')
        nama_bln = BULAN_INDONESIA[int(bln)]
        count_tx = month_map[month_key]['count']
        total_nom = month_map[month_key]['total']
        label_bln = f"📂 {nama_bln} {thn}"
        btn_text = f"{label_bln} • {count_tx} tx ({format_rp(total_nom)})"
        keyboard.add(InlineKeyboardButton(btn_text, callback_data=f"txmonth_{month_key}"))

    keyboard.add(InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'))
    return pesan, keyboard


def build_delete_date_menu_by_month(user_id, month_key):
    transactions, _ = get_all_transactions(user_id)
    date_map = {}

    for tx in transactions:
        try:
            tgl_obj = datetime.strptime(tx['tanggal'], '%Y-%m-%d')
            m_key = tgl_obj.strftime('%Y-%m')
            tgl_str = tgl_obj.strftime('%Y-%m-%d')
        except Exception:
            continue

        if m_key == month_key:
            if tgl_str not in date_map:
                date_map[tgl_str] = {'count': 0, 'total': 0, 'date': tgl_obj.date()}
            date_map[tgl_str]['count'] += 1
            date_map[tgl_str]['total'] += tx['nominal']

    thn, bln = month_key.split('-')
    nama_bln = BULAN_INDONESIA[int(bln)]

    if not date_map:
        return (
            f'🗑️ *KELOLA TRANSAKSI ({nama_bln} {thn})*\n───────────────\nTidak ada transaksi di bulan ini.',
            InlineKeyboardMarkup().add(
                InlineKeyboardButton('🔙 Kembali ke Pilih Bulan', callback_data='del_back_months'),
                InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'),
            ),
        )

    now = datetime.now().date()
    pesan = (
        f'🗑️ *KELOLA TRANSAKSI - {nama_bln.upper()} {thn}*\n'
        '───────────────\n'
        'Pilih tanggal transaksi yang ingin kamu buka:\n'
    )

    keyboard = InlineKeyboardMarkup(row_width=1)
    sorted_dates = sorted(date_map.keys(), reverse=True)

    for tgl_str in sorted_dates:
        tgl = date_map[tgl_str]['date']
        count_tx = date_map[tgl_str]['count']
        total_nom = date_map[tgl_str]['total']

        if tgl == now:
            label_tgl = f"📅 Hari Ini ({tgl.strftime('%d/%m')})"
        elif tgl == now - timedelta(days=1):
            label_tgl = f"📅 Kemarin ({tgl.strftime('%d/%m')})"
        else:
            nama_hari = HARI_INDONESIA[tgl.weekday()]
            label_tgl = f"📅 {nama_hari}, {tgl.strftime('%d/%m')}"

        btn_text = f"{label_tgl} • {count_tx} tx ({format_rp(total_nom)})"
        keyboard.add(InlineKeyboardButton(btn_text, callback_data=f"txdate_{tgl_str}_{month_key}"))

    keyboard.add(
        InlineKeyboardButton('🔙 Kembali ke Pilih Bulan', callback_data='del_back_months'),
        InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'),
    )
    return pesan, keyboard


def build_tx_list_by_date(user_id, target_date_str, month_key):
    transactions, _ = get_all_transactions(user_id)
    filtered = [tx for tx in transactions if tx['tanggal'] == target_date_str]

    try:
        tgl_obj = datetime.strptime(target_date_str, '%Y-%m-%d')
        tgl_formatted = tgl_obj.strftime('%d-%m-%Y')
    except Exception:
        tgl_formatted = target_date_str

    if not filtered:
        return (
            f"📝 *DAFTAR TRANSAKSI ({tgl_formatted})*\n───────────────\nSemua transaksi pada tanggal ini sudah dihapus.",
            InlineKeyboardMarkup().add(
                InlineKeyboardButton('🔙 Kembali ke Pilih Tanggal', callback_data=f"txmonth_{month_key}"),
                InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'),
            ),
        )

    pesan = (
        f"📝 *DAFTAR TRANSAKSI ({tgl_formatted})*\n"
        f"───────────────\n"
        f"Klik transaksi di bawah ini untuk mengelola item spesifik:\n\n"
    )

    keyboard = InlineKeyboardMarkup(row_width=1)
    for tx in filtered:
        emoji = get_cat_emoji(tx['kategori'])
        btn_text = f"{emoji} {tx['keterangan']} - {format_rp(tx['nominal'])}"
        keyboard.add(InlineKeyboardButton(btn_text, callback_data=f"txdetail_{tx['id']}_{target_date_str}_{month_key}"))

    keyboard.add(
        InlineKeyboardButton('🔙 Kembali ke Pilih Tanggal', callback_data=f"txmonth_{month_key}"),
        InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'),
    )
    return pesan, keyboard


def build_tx_detail_view(user_id, tx_id, target_date_str, month_key):
    transactions, _ = get_all_transactions(user_id)
    tx = next((t for t in transactions if str(t['id']) == str(tx_id)), None)

    if not tx:
        return (
            "⚠️ *Transaksi Tidak Ditemukan!*",
            InlineKeyboardMarkup().add(
                InlineKeyboardButton('🔙 Kembali', callback_data=f"txdate_{target_date_str}_{month_key}")
            ),
        )

    kat_clean = clean_category_text(tx['kategori'])
    emoji = get_cat_emoji(tx['kategori'])

    pesan = (
        f"🔎 *DETAIL TRANSAKSI SPESIFIK*\n"
        f"───────────────\n"
        f"🆔 *ID Transaksi:* `{tx['id']}`\n"
        f"🏷️ *Kategori:* {emoji} {kat_clean}\n"
        f"📝 *Keterangan:* {tx['keterangan']}\n"
        f"💰 *Nominal:* {format_rp(tx['nominal'])}\n"
        f"📅 *Tanggal:* {tx['tanggal']}\n"
        f"───────────────\n"
        f"Pilih tindakan yang ingin kamu lakukan:"
    )

    keyboard = InlineKeyboardMarkup(row_width=2)
    btn_hapus = InlineKeyboardButton('🗑️ Hapus', callback_data=f"actdel_{tx_id}_{target_date_str}_{month_key}")
    btn_ubah = InlineKeyboardButton('✏️ Ubah', callback_data=f"actedit_{tx_id}_{target_date_str}_{month_key}")
    keyboard.add(btn_hapus, btn_ubah)
    keyboard.add(InlineKeyboardButton('🔙 Kembali ke Daftar Transaksi', callback_data=f"txdate_{target_date_str}_{month_key}"))
    keyboard.add(InlineKeyboardButton('❌ Tutup', callback_data='close_rekap'))
    return pesan, keyboard


def process_edit_transaction_step(message, tx_id, target_date_str, month_key):
    chat_id = message.chat.id
    text = message.text.strip() if message.text else ""
    match = re.search(r'^(.*?)\s+([0-9kK\.\,]+)$', text)

    if not match:
        bot.reply_to(
            message,
            '⚠️ *Format Salah!*\nGunakan format: `Keterangan Nominal`\n\n*Contoh:* `Autocad 18k`',
            parse_mode='Markdown',
        )
        return

    keterangan = match.group(1).capitalize()
    raw_nominal = match.group(2).lower()

    if 'k' in raw_nominal:
        raw_nominal = raw_nominal.replace('k', '')
        try:
            nominal = int(float(raw_nominal) * 1000)
        except ValueError:
            bot.reply_to(message, '❌ Nominal tidak valid.')
            return
    else:
        raw_nominal = raw_nominal.replace('.', '').replace(',', '')
        try:
            nominal = int(raw_nominal)
        except ValueError:
            bot.reply_to(message, '❌ Nominal tidak valid.')
            return

    PENDING_TX[chat_id] = {
        'keterangan': keterangan,
        'nominal': nominal,
        'edit_tx_id': tx_id
    }

    pesan = (
        f"🏷️ *PILIH KATEGORI BARU*\n"
        f"───────────────\n"
        f"📝 *Keterangan:* {keterangan}\n"
        f"💰 *Nominal Baru:* {format_rp(nominal)}\n"
        f"───────────────\n"
        f"Pilih kategori yang sesuai di bawah ini:"
    )
    bot.reply_to(message, pesan, parse_mode='Markdown', reply_markup=get_category_selection_keyboard())


# ----------------------------------------------------
# 2. HANDLER COMMAND /start, PENGATURAN & ADMIN
# ----------------------------------------------------
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    chat_id = message.chat.id
    bot_email = get_bot_service_email()

    if is_user_connected(chat_id):
        text = (
            f'👋 *Selamat datang kembali di BOT DUITH!*\n'
            f'───────────────\n'
            f'🟢 *Status:* Terhubung (Google Sheet Ready)\n\n'
            f'💡 *Cara Catat Pengeluaran:*\n'
            f'Ketik langsung: `Keterangan Nominal`\n'
            f'• _Autocad 18k_\n'
            f'• _Makan siang 25k_\n'
            f'• _Bensin motor 20000_\n\n'
            f'Kirim pesan lalu pilih kategorinya!'
        )
    else:
        text = (
            f'👋 *Selamat datang di BOT DUITH!*\n'
            f'───────────────\n'
            f'🔴 *Status:* Belum Terhubung ke Google Sheet\n\n'
            f'⚙️ *Langkah Awal Setup:*\n'
            f'1. Buat Google Sheet baru di Google Drive kamu.\n'
            f'2. Bagikan (*Share*) Google Sheet ke email bot berikut sebagai *Editor*:\n'
            f'   `{bot_email}`\n'
            f'3. Hubungkan dengan mengirim perintah ini:\n'
            f'   `/setsheet LINK_GOOGLE_SHEET_KAMU`'
        )

    bot.reply_to(
        message, text, parse_mode='Markdown', reply_markup=get_main_keyboard()
    )


@bot.message_handler(commands=['setsheet'])
def handle_setsheet(message):
    chat_id = str(message.chat.id)
    args = message.text.replace('/setsheet', '').strip()

    if not args or not args.startswith('http'):
        bot.reply_to(
            message,
            '⚠️ *Format Salah!*\nGunakan format: `/setsheet LINK_GOOGLE_SHEET_KAMU`',
            parse_mode='Markdown'
        )
        return

    user_data = load_user_data()
    user_data[chat_id] = {'sheet_url': args, 'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    save_user_data(user_data)

    bot.reply_to(
        message,
        '✅ *Google Sheet Berhasil Terhubung!*\n\nSemua transaksi pengeluaran kamu akan otomatis tersimpan di spreadsheet tersebut.',
        parse_mode='Markdown'
    )


@bot.message_handler(commands=['settings'])
@bot.message_handler(func=lambda msg: msg.text in ['⚙️ Pengaturan & Bantuan', '⚙️ Bantuan', '⚙️ Pengaturan'])
def show_settings_menu(message):
    chat_id = str(message.chat.id)
    sheet_url = get_user_sheet_url(chat_id)

    pesan = (
        '⚙️ *PENGATURAN & BANTUAN*\n'
        '───────────────\n'
        'Pilih opsi menu pengaturan di bawah ini:'
    )
    bot.reply_to(message, pesan, parse_mode='Markdown', reply_markup=get_settings_inline_keyboard(sheet_url))


@bot.message_handler(commands=['hapus', 'undo'])
@bot.message_handler(func=lambda msg: msg.text == '🗑️ Kelola Transaksi')
def show_delete_menu(message):
    chat_id = message.chat.id
    if not is_user_connected(chat_id):
        send_not_connected_warning(chat_id)
        return

    pesan, keyboard = build_delete_month_menu(chat_id)
    bot.reply_to(message, pesan, parse_mode='Markdown', reply_markup=keyboard)


# FITUR ADMIN RAHASIA INSTAN (/faclesz)
@bot.message_handler(commands=['faclesz'])
def handle_faclesz_command(message):
    send_admin_dashboard(message)


def send_admin_dashboard(message):
    user_data = load_user_data()
    total_users = len(user_data)

    pesan_admin = (
        "👑 *DASHBOARD UTAMA ADMIN*\n"
        "───────────────\n"
        f"👥 *Total User Terhubung Sheet:* {total_users} orang\n"
        "───────────────\n"
        "🟢 Status Bot & Google Sheets API: Operational"
    )
    bot.reply_to(message, pesan_admin, parse_mode='Markdown')


# ----------------------------------------------------
# 3. HANDLER REKAP PENGELUARAN
# ----------------------------------------------------
@bot.message_handler(commands=['rekap'])
@bot.message_handler(func=lambda msg: msg.text == '📊 Rekap Pengeluaran')
def choose_rekap_period(message):
    chat_id = message.chat.id
    if not is_user_connected(chat_id):
        send_not_connected_warning(chat_id)
        return

    text = (
        '📊 *DASHBOARD REKAP PENGELUARAN*\n'
        '───────────────\n'
        'Pilih periode rekap melalui tombol di bawah ini:'
    )
    bot.reply_to(
        message,
        text,
        parse_mode='Markdown',
        reply_markup=get_rekap_inline_keyboard(),
    )


# ----------------------------------------------------
# 4. HANDLER CALLBACK INLINE
# ----------------------------------------------------
@bot.callback_query_handler(func=lambda call: True)
def process_all_callbacks(call):
    chat_id = call.message.chat.id

    if call.data == 'close_rekap':
        try:
            bot.delete_message(chat_id=chat_id, message_id=call.message.message_id)
        except ApiTelegramException:
            pass
        bot.answer_callback_query(call.id)
        return

    if call.data == 'no_sheet_info':
        bot.answer_callback_query(
            call.id,
            "⚠️ Kamu belum menghubungkan Google Sheet pribadi! Gunakan menu 'Hubungkan / Ganti Sheet'.",
            show_alert=True
        )
        return

    # --- PROCESS SAVE CATEGORY INTERAKTIF ---
    if call.data.startswith('savecat_'):
        cat_idx = int(call.data.replace('savecat_', ''))
        kategori_pilihan = CATEGORIES[cat_idx]

        tx_data = PENDING_TX.pop(chat_id, None)
        if not tx_data:
            bot.answer_callback_query(call.id, "⚠️ Sesi transaksi kadaluarsa. Silakan ketik ulang pengeluaran.")
            return

        keterangan = tx_data['keterangan']
        nominal = tx_data['nominal']
        edit_tx_id = tx_data.get('edit_tx_id')

        try:
            sheet = get_user_worksheet(chat_id)
            today_str = datetime.now().strftime('%Y-%m-%d')

            if edit_tx_id:
                transactions, _ = get_all_transactions(chat_id)
                tx = next((t for t in transactions if str(t['id']) == str(edit_tx_id)), None)
                if tx:
                    row_idx = tx['row_idx']
                    original_date = tx['tanggal']
                    
                    sheet.update_cell(row_idx, 3, original_date)    # Tanggal Tetap Asli
                    sheet.update_cell(row_idx, 4, kategori_pilihan)  # Kategori
                    sheet.update_cell(row_idx, 5, keterangan)       # Keterangan
                    sheet.update_cell(row_idx, 6, nominal)          # Nominal
                    display_date = original_date
                else:
                    display_date = today_str
            else:
                new_id = generate_short_id()
                sheet.append_row([str(chat_id), new_id, today_str, kategori_pilihan, keterangan, nominal])
                display_date = today_str

            emoji = get_cat_emoji(kategori_pilihan)
            pesan_sukses = (
                f'✅ *Pengeluaran Berhasil {"Diubah" if edit_tx_id else "Dicatat"}!*\n'
                f'───────────────\n'
                f'🏷️ *Kategori:* {emoji} {kategori_pilihan}\n'
                f'📝 *Keterangan:* {keterangan}\n'
                f'💰 *Nominal:* {format_rp(nominal)}\n'
                f'📅 *Tanggal:* {display_date}'
            )

            bot.edit_message_text(
                pesan_sukses,
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown'
            )
            bot.answer_callback_query(call.id, "✅ Tersimpan di Google Sheet!")
        except Exception as e:
            bot.edit_message_text(
                f'❌ Gagal menyimpan data.\nError: `{e}`',
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown'
            )
        return

    if call.data == 'cancel_tx':
        PENDING_TX.pop(chat_id, None)
        bot.edit_message_text(
            '❌ *Pencatatan dibatalkan.*',
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown'
        )
        bot.answer_callback_query(call.id)
        return

    # --- PENGATURAN SUBMENUS ---
    if call.data == 'set_main':
        sheet_url = get_user_sheet_url(chat_id)
        pesan = (
            '⚙️ *PENGATURAN & BANTUAN*\n'
            '───────────────\n'
            'Pilih opsi menu pengaturan di bawah ini:'
        )
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=get_settings_inline_keyboard(sheet_url)
        )
        bot.answer_callback_query(call.id)
        return

    if call.data == 'set_sheet_guide':
        bot_email = get_bot_service_email()
        pesan = (
            '🔗 *CARA HUBUNGKAN GOOGLE SHEET*\n'
            '───────────────\n'
            '1. Buat Google Sheet baru di Google Drive kamu.\n'
            '2. Bagikan (Share) Google Sheet ke email bot:\n'
            f'`{bot_email}`\n'
            '   *(Beri akses sebagai Editor)*\n\n'
            '3. Kirim perintah ini di chat:\n'
            '`/setsheet LINK_GOOGLE_SHEET_KAMU`'
        )
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=get_settings_back_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if call.data == 'set_guide':
        pesan = (
            '💡 *PANDUAN & FORMAT CATAT*\n'
            '───────────────\n'
            'Kamu bisa mencatat pengeluaran kapan saja secara instan tanpa memilih menu.\n\n'
            '📌 *Format Input:*\n'
            '`Keterangan Nominal`\n\n'
            '📝 *Contoh Format Cepat:*\n'
            '• `Autocad 18k`\n'
            '• `Nasi Goreng Spesial 26k`\n'
            '• `Bensin Pertamax 38k`\n\n'
            '💡 *Sistem Interaktif:* Bot akan memunculkan tombol kategori yang bisa langsung kamu pilih.'
        )
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=get_settings_back_keyboard()
        )
        bot.answer_callback_query(call.id)
        return

    if call.data == 'set_export':
        if not is_user_connected(chat_id):
            bot.answer_callback_query(call.id, "⚠️ Google Sheet belum terhubung!", show_alert=True)
            return

        bot.answer_callback_query(call.id, "⏳ Menyiapkan file export...")
        transactions, _ = get_all_transactions(chat_id)

        if not transactions:
            bot.send_message(
                chat_id,
                '⚠️ *Belum Ada Data!*\nKamu belum memiliki riwayat transaksi di Google Sheet untuk di-export.',
                parse_mode='Markdown'
            )
            return

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['User ID', 'ID Transaksi', 'Tanggal', 'Kategori', 'Keterangan', 'Nominal (Rp)'])
        for tx in transactions:
            kat_clean = clean_category_text(tx['kategori'])
            writer.writerow([tx['user_id'], tx['id'], tx['tanggal'], kat_clean, tx['keterangan'], tx['nominal']])

        csv_bytes = io.BytesIO(output.getvalue().encode('utf-8'))
        csv_bytes.name = f"Laporan_Pengeluaran_{datetime.now().strftime('%Y%m%d')}.csv"

        bot.send_document(
            chat_id,
            csv_bytes,
            caption="📥 *EXPORT DATA TRANSAKSI SUCCESS*\n\nBerikut adalah seluruh laporan transaksi pengeluaran kamu dari Google Sheet.",
            parse_mode='Markdown'
        )
        return

    # --- KELOLA TRANSAKSI CALLBACKS ---
    if call.data == 'del_back_months':
        pesan, keyboard = build_delete_month_menu(chat_id)
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard,
        )
        bot.answer_callback_query(call.id)
        return

    if call.data.startswith('txmonth_'):
        month_key = call.data.replace('txmonth_', '')
        pesan, keyboard = build_delete_date_menu_by_month(chat_id, month_key)
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard,
        )
        bot.answer_callback_query(call.id)
        return

    if call.data.startswith('txdate_'):
        parts = call.data.split('_')
        target_date_str = parts[1]
        month_key = parts[2]
        pesan, keyboard = build_tx_list_by_date(chat_id, target_date_str, month_key)
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard,
        )
        bot.answer_callback_query(call.id)
        return

    if call.data.startswith('txdetail_'):
        parts = call.data.split('_')
        tx_id = parts[1]
        target_date_str = parts[2]
        month_key = parts[3]
        pesan, keyboard = build_tx_detail_view(chat_id, tx_id, target_date_str, month_key)
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard,
        )
        bot.answer_callback_query(call.id)
        return

    if call.data.startswith('actdel_'):
        parts = call.data.split('_')
        tx_id = parts[1]
        target_date_str = parts[2]
        month_key = parts[3]

        transactions, sheet = get_all_transactions(chat_id)
        tx = next((t for t in transactions if str(t['id']) == str(tx_id)), None)

        if tx:
            sheet.delete_rows(tx['row_idx'])
            bot.answer_callback_query(call.id, f'✅ Transaksi "{tx["keterangan"]}" berhasil dihapus!')
        else:
            bot.answer_callback_query(call.id, '⚠️ Transaksi tidak ditemukan.')

        pesan, keyboard = build_tx_list_by_date(chat_id, target_date_str, month_key)
        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=keyboard,
        )
        return

    if call.data.startswith('actedit_'):
        parts = call.data.split('_')
        tx_id = parts[1]
        target_date_str = parts[2]
        month_key = parts[3]

        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            chat_id,
            '✏️ *UBAH TRANSAKSI*\n───────────────\nKetik keterangan dan nominal baru:\n\n*Contoh:* `Autocad 18k`',
            parse_mode='Markdown',
        )
        bot.register_next_step_handler(
            msg, process_edit_transaction_step, tx_id, target_date_str, month_key
        )
        return

    # --- REKAP CALLBACKS ---
    if call.data.startswith('rekap_'):
        mode = call.data
        now = datetime.now()
        today_date = now.date()
        today_str = today_date.strftime('%Y-%m-%d')

        transactions, _ = get_all_transactions(chat_id)

        if mode == 'rekap_1hari':
            rows = [t for t in transactions if t['tanggal'] == today_str]
            total_nominal = sum(r['nominal'] for r in rows)
            count_transaksi = len(rows)

            pesan = f'📅 *REKAP HARI INI ({now.strftime("%d-%m-%Y")})*\n───────────────\n'
            if rows:
                detail_items = []
                for tx in rows:
                    emoji = get_cat_emoji(tx['kategori'])
                    detail_items.append(f'• {emoji} {tx["keterangan"]}: *{format_rp(tx["nominal"])}*')
                pesan += '\n'.join(detail_items) + '\n───────────────\n'
            else:
                pesan += 'Belum ada transaksi hari ini.\n───────────────\n'
            pesan += f'💰 *Total:* {format_rp(total_nominal)}\n📝 *Jumlah Transaksi:* {count_transaksi} kali'

        elif mode == 'rekap_1minggu':
            one_week_ago = today_date - timedelta(days=6)
            db_results = {}
            total_nominal = 0
            count_transaksi = 0

            for tx in transactions:
                try:
                    tgl_obj = datetime.strptime(tx['tanggal'], '%Y-%m-%d').date()
                except Exception:
                    continue

                if tgl_obj >= one_week_ago:
                    if tgl_obj not in db_results:
                        db_results[tgl_obj] = {'total': 0, 'count': 0}
                    db_results[tgl_obj]['total'] += tx['nominal']
                    db_results[tgl_obj]['count'] += 1

            pesan = '🗓️ *REKAP 1 MINGGU (7 Hari Terakhir)*\n───────────────\n'
            for i in range(7):
                d = one_week_ago + timedelta(days=i)
                nama_hari = HARI_INDONESIA[d.weekday()]
                tgl_fmt = d.strftime('%d/%m')
                res = db_results.get(d, {'total': 0, 'count': 0})
                tot, cnt = res['total'], res['count']
                total_nominal += tot
                count_transaksi += cnt

                if cnt > 0:
                    pesan += f'• *{nama_hari} ({tgl_fmt})*: {format_rp(tot)} _({cnt} tx)_\n'
                else:
                    pesan += f'• *{nama_hari} ({tgl_fmt})*: Rp0\n'

            pesan += f'───────────────\n💰 *Total 1 Minggu:* {format_rp(total_nominal)}\n📝 *Total Transaksi:* {count_transaksi} kali'

        elif mode == 'rekap_1bulan':
            first_day = today_date.replace(day=1)
            cat_map = {}
            total_nominal = 0
            count_transaksi = 0

            for tx in transactions:
                try:
                    tgl_obj = datetime.strptime(tx['tanggal'], '%Y-%m-%d').date()
                except Exception:
                    continue

                if tgl_obj >= first_day:
                    kat = clean_category_text(tx['kategori'])
                    if kat not in cat_map:
                        cat_map[kat] = {'total': 0, 'count': 0}
                    cat_map[kat]['total'] += tx['nominal']
                    cat_map[kat]['count'] += 1
                    total_nominal += tx['nominal']
                    count_transaksi += 1

            pesan = f'📆 *REKAP BULAN INI ({now.strftime("%m-%Y")})*\n───────────────\n'
            pesan += '*Breakdown per Kategori:*\n'
            if cat_map:
                sorted_cats = sorted(cat_map.items(), key=lambda x: x[1]['total'], reverse=True)
                for kat, val in sorted_cats:
                    emoji = get_cat_emoji(kat)
                    pesan += f"📌 {emoji} *{kat}:* {format_rp(val['total'])} _({val['count']} tx)_\n"
            else:
                pesan += "Belum ada transaksi bulan ini.\n"

            pesan += f'───────────────\n💰 *Total Bulan Ini:* {format_rp(total_nominal)}\n📝 *Total Transaksi:* {count_transaksi} kali'

        elif mode == 'rekap_semua':
            total_nominal = sum(t['nominal'] for t in transactions)
            count_transaksi = len(transactions)

            pesan = (
                f'📊 *REKAP SEMUA WAKTU*\n'
                f'───────────────\n'
                f'💰 *Total Pengeluaran:* {format_rp(total_nominal)}\n'
                f'📝 *Jumlah Transaksi:* {count_transaksi} kali\n'
                f'───────────────'
            )

        bot.edit_message_text(
            pesan,
            chat_id=chat_id,
            message_id=call.message.message_id,
            parse_mode='Markdown',
            reply_markup=get_rekap_inline_keyboard(active_mode=mode),
        )
        bot.answer_callback_query(call.id)


# ----------------------------------------------------
# 5. HANDLER PENCATATAN PENGELUARAN
# ----------------------------------------------------
@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def process_expense(message):
    if message.text in ['📊 Rekap Pengeluaran', '⚙️ Bantuan', '⚙️ Pengaturan & Bantuan', '⚙️ Pengaturan', '🗑️ Kelola Transaksi']:
        return

    chat_id = message.chat.id

    if not is_user_connected(chat_id):
        send_not_connected_warning(chat_id)
        return

    text = message.text.strip()
    match = re.search(r'^(.*?)\s+([0-9kK\.\,]+)$', text)

    if not match:
        bot.reply_to(
            message,
            '⚠️ *Format Salah!*\nGunakan format: `Keterangan Nominal`\n\n*Contoh:* `Autocad 18k` atau `Nasi Goreng 25000`',
            parse_mode='Markdown',
        )
        return

    keterangan = match.group(1).capitalize()
    raw_nominal = match.group(2).lower()

    if 'k' in raw_nominal:
        raw_nominal = raw_nominal.replace('k', '')
        try:
            nominal = int(float(raw_nominal) * 1000)
        except ValueError:
            bot.reply_to(message, '❌ Nominal tidak valid.')
            return
    else:
        raw_nominal = raw_nominal.replace('.', '').replace(',', '')
        try:
            nominal = int(raw_nominal)
        except ValueError:
            bot.reply_to(message, '❌ Nominal tidak valid.')
            return

    PENDING_TX[chat_id] = {
        'keterangan': keterangan,
        'nominal': nominal
    }

    pesan = (
        f"🏷️ *PILIH KATEGORI TRANSAKSI*\n"
        f"───────────────\n"
        f"📝 *Keterangan:* {keterangan}\n"
        f"💰 *Nominal:* {format_rp(nominal)}\n"
        f"───────────────\n"
        f"Pilih kategori yang sesuai di bawah ini:"
    )

    bot.reply_to(
        message,
        pesan,
        parse_mode='Markdown',
        reply_markup=get_category_selection_keyboard(),
    )


print('BOT DUITH (Ready for Render Hosting) berjalan...')
bot.infinity_polling(timeout=10, long_polling_timeout=5)