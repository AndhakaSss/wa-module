import asyncio
import json
import os
import random
import sqlite3
import hashlib
import secrets
import re
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash

import wa_bridge
from wa_bridge import BridgeError

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'wa_business.db')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
ACTIVE_SESSIONS_FILE = os.environ.get(
    'ACTIVE_SESSIONS_FILE',
    os.path.join(DATA_DIR, 'active_sessions.json')
)
MAX_WHATSAPP_ACCOUNTS = 15
RECOMMENDED_WHATSAPP_ACCOUNTS = 5
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.pdf', '.apk'}

JWT_SECRET_FILE = os.path.join(DATA_DIR, '.jwt_secret')

def db_connect():
    return sqlite3.connect(DB_PATH)

def get_jwt_secret():
    if os.environ.get('JWT_SECRET_KEY'):
        return os.environ['JWT_SECRET_KEY']
    if os.path.exists(JWT_SECRET_FILE):
        with open(JWT_SECRET_FILE, 'r', encoding='utf-8') as f:
            secret = f.read().strip()
            if secret:
                return secret
    secret = secrets.token_hex(32)
    with open(JWT_SECRET_FILE, 'w', encoding='utf-8') as f:
        f.write(secret)
    return secret

app.config['JWT_SECRET_KEY'] = get_jwt_secret()
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)

jwt = JWTManager(app)

def get_current_user_id():
    return int(get_jwt_identity())

# Database setup
def init_db():
    conn = db_connect()
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  email TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL,
                  company_name TEXT,
                  phone TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # WhatsApp sessions table
    c.execute('''CREATE TABLE IF NOT EXISTS whatsapp_sessions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  phone_number TEXT,
                  session_data TEXT,
                  status TEXT DEFAULT 'disconnected',
                  messages_today INTEGER DEFAULT 0,
                  daily_limit INTEGER DEFAULT 50,
                  last_active TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    
    # Contacts table
    c.execute('''CREATE TABLE IF NOT EXISTS contacts
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  phone TEXT NOT NULL,
                  name TEXT,
                  vehicle TEXT,
                  tags TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    
    # Templates table
    c.execute('''CREATE TABLE IF NOT EXISTS templates
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  name TEXT NOT NULL,
                  content TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    
    # Campaigns table
    c.execute('''CREATE TABLE IF NOT EXISTS campaigns
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  name TEXT NOT NULL,
                  status TEXT DEFAULT 'pending',
                  message TEXT NOT NULL,
                  attachment_path TEXT,
                  recipient_count INTEGER DEFAULT 0,
                  sent_count INTEGER DEFAULT 0,
                  scheduled_for TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  completed_at TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    
    # API keys table
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  api_key TEXT UNIQUE NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    
    conn.commit()
    conn.close()

def migrate_db():
    conn = db_connect()
    c = conn.cursor()
    columns = {row[1] for row in c.execute('PRAGMA table_info(whatsapp_sessions)').fetchall()}
    if 'bridge_client_id' not in columns:
        c.execute('ALTER TABLE whatsapp_sessions ADD COLUMN bridge_client_id TEXT')
        conn.commit()
    user_columns = {row[1] for row in c.execute('PRAGMA table_info(users)').fetchall()}
    if 'country_code' not in user_columns:
        c.execute("ALTER TABLE users ADD COLUMN country_code TEXT DEFAULT '91'")
        conn.commit()
    if 'send_delay_min' not in user_columns:
        c.execute('ALTER TABLE users ADD COLUMN send_delay_min INTEGER DEFAULT 3')
        conn.commit()
    if 'send_delay_max' not in user_columns:
        c.execute('ALTER TABLE users ADD COLUMN send_delay_max INTEGER DEFAULT 8')
        conn.commit()
    if 'rotate_accounts' not in user_columns:
        c.execute('ALTER TABLE users ADD COLUMN rotate_accounts INTEGER DEFAULT 1')
        conn.commit()
    if 'last_count_date' not in columns:
        c.execute('ALTER TABLE whatsapp_sessions ADD COLUMN last_count_date TEXT')
        conn.commit()
    campaign_columns = {row[1] for row in c.execute('PRAGMA table_info(campaigns)').fetchall()}
    if 'error_message' not in campaign_columns:
        c.execute('ALTER TABLE campaigns ADD COLUMN error_message TEXT')
        conn.commit()
    if 'contact_filter' not in campaign_columns:
        c.execute('ALTER TABLE campaigns ADD COLUMN contact_filter TEXT')
        conn.commit()
    if 'current_contact_name' not in campaign_columns:
        c.execute('ALTER TABLE campaigns ADD COLUMN current_contact_name TEXT')
        conn.commit()
    if 'current_contact_phone' not in campaign_columns:
        c.execute('ALTER TABLE campaigns ADD COLUMN current_contact_phone TEXT')
        conn.commit()
    if 'active_whatsapp_phone' not in campaign_columns:
        c.execute('ALTER TABLE campaigns ADD COLUMN active_whatsapp_phone TEXT')
        conn.commit()
    if 'progress_index' not in campaign_columns:
        c.execute('ALTER TABLE campaigns ADD COLUMN progress_index INTEGER DEFAULT 0')
        conn.commit()
    c.execute('''CREATE TABLE IF NOT EXISTS campaign_deliveries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  campaign_id INTEGER NOT NULL,
                  contact_id INTEGER,
                  contact_name TEXT,
                  contact_phone TEXT,
                  whatsapp_phone TEXT,
                  status TEXT NOT NULL,
                  error_message TEXT,
                  sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(id))''')
    conn.commit()
    conn.close()

init_db()
migrate_db()

def sync_active_sessions_file():
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        "SELECT bridge_client_id FROM whatsapp_sessions WHERE status = 'connected' AND bridge_client_id IS NOT NULL"
    )
    ids = [row[0] for row in c.fetchall()]
    conn.close()
    with open(ACTIVE_SESSIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(ids, f)

def fix_stuck_campaigns():
    conn = db_connect()
    c = conn.cursor()
    c.execute("UPDATE campaigns SET status = 'paused', error_message = 'Server restarted while campaign was running. Click Resume to continue.' WHERE status = 'running' AND sent_count > 0")
    c.execute("UPDATE campaigns SET status = 'failed', completed_at = datetime('now'), error_message = 'Campaign interrupted. Please launch again.' WHERE status = 'running' AND sent_count = 0")
    pending = c.execute(
        "SELECT id, user_id FROM campaigns WHERE status = 'pending' AND (scheduled_for IS NULL OR scheduled_for = '')"
    ).fetchall()
    conn.commit()
    conn.close()
    return pending

_stuck_pending_campaigns = fix_stuck_campaigns()
sync_active_sessions_file()

_active_campaign_threads = set()
_campaign_thread_lock = threading.Lock()
_campaign_controls = {}
_campaign_controls_lock = threading.Lock()

def _init_campaign_control(campaign_id):
    with _campaign_controls_lock:
        _campaign_controls[campaign_id] = {'paused': False, 'cancelled': False}

def _get_campaign_control(campaign_id):
    with _campaign_controls_lock:
        return _campaign_controls.setdefault(campaign_id, {'paused': False, 'cancelled': False})

def _clear_campaign_control(campaign_id):
    with _campaign_controls_lock:
        _campaign_controls.pop(campaign_id, None)

def _campaign_thread_active(campaign_id):
    with _campaign_thread_lock:
        return campaign_id in _active_campaign_threads

def _wait_if_paused_or_cancelled(campaign_id):
    while True:
        ctrl = _get_campaign_control(campaign_id)
        if ctrl['cancelled']:
            return True
        if not ctrl['paused']:
            return False
        time.sleep(0.4)

def get_delivered_contact_ids(c, campaign_id, statuses=None):
    if statuses:
        placeholders = ','.join('?' for _ in statuses)
        c.execute(
            f'''SELECT DISTINCT contact_id FROM campaign_deliveries
                WHERE campaign_id = ? AND contact_id IS NOT NULL AND status IN ({placeholders})''',
            (campaign_id, *statuses)
        )
    else:
        c.execute(
            '''SELECT DISTINCT contact_id FROM campaign_deliveries
               WHERE campaign_id = ? AND contact_id IS NOT NULL''',
            (campaign_id,)
        )
    return {row[0] for row in c.fetchall()}

def get_campaign_owner(campaign_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT user_id, status FROM campaigns WHERE id = ?', (campaign_id,))
    row = c.fetchone()
    conn.close()
    return row

# Helper functions
def generate_api_key():
    return 'wa_' + secrets.token_urlsafe(24)

def get_user_settings(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute(
        'SELECT country_code, send_delay_min, send_delay_max, rotate_accounts FROM users WHERE id = ?',
        (user_id,)
    )
    row = c.fetchone()
    c.execute('SELECT daily_limit FROM whatsapp_sessions WHERE user_id = ? LIMIT 1', (user_id,))
    limit_row = c.fetchone()
    conn.close()
    if not row:
        return {'country_code': '91', 'send_delay_min': 3, 'send_delay_max': 8, 'rotate_accounts': True, 'daily_limit': 50}
    return {
        'country_code': row[0] or '91',
        'send_delay_min': max(1, int(row[1] or 3)),
        'send_delay_max': max(1, int(row[2] or 8)),
        'rotate_accounts': bool(row[3] if row[3] is not None else 1),
        'daily_limit': int(limit_row[0]) if limit_row and limit_row[0] else 50,
    }

def ensure_daily_reset(c, user_id):
    today = datetime.now().date().isoformat()
    c.execute(
        '''UPDATE whatsapp_sessions SET messages_today = 0
           WHERE user_id = ? AND (last_count_date IS NULL OR last_count_date != ?)''',
        (user_id, today)
    )
    c.execute(
        'UPDATE whatsapp_sessions SET last_count_date = ? WHERE user_id = ?',
        (today, user_id)
    )

def get_connected_sessions(c, user_id):
    c.execute(
        '''SELECT bridge_client_id, daily_limit, messages_today, phone_number, id
           FROM whatsapp_sessions
           WHERE user_id = ? AND status = 'connected' AND bridge_client_id IS NOT NULL
           ORDER BY id''',
        (user_id,)
    )
    return [
        {
            'bridge_client_id': r[0],
            'daily_limit': r[1] or 50,
            'messages_today': r[2] or 0,
            'phone_number': r[3],
            'id': r[4],
        }
        for r in c.fetchall()
    ]

def pick_session(sessions, start_index):
    if not sessions:
        return None, start_index
    for offset in range(len(sessions)):
        idx = (start_index + offset) % len(sessions)
        session = sessions[idx]
        if session['messages_today'] < session['daily_limit']:
            return session, idx
    return None, start_index

NOT_ON_WHATSAPP_REASON = 'Not on WhatsApp'

def is_non_whatsapp_error(error_message):
    if not error_message:
        return False
    lower = str(error_message).lower()
    markers = (
        'not on whatsapp',
        'not registered',
        'no whatsapp',
        'invalid number',
        'not a whatsapp',
        'phone number is not registered',
    )
    return any(marker in lower for marker in markers)

def get_retryable_failed_contact_ids(c, campaign_id):
    c.execute(
        '''SELECT contact_id, error_message FROM campaign_deliveries
           WHERE campaign_id = ? AND status = 'failed' AND contact_id IS NOT NULL''',
        (campaign_id,)
    )
    return {
        row[0] for row in c.fetchall()
        if not is_non_whatsapp_error(row[1])
    }

def campaign_send_pause(user_id, last_send_failed, sent_count):
    settings = get_user_settings(user_id)
    lo = min(settings['send_delay_min'], settings['send_delay_max'])
    hi = max(settings['send_delay_min'], settings['send_delay_max'])
    total = random.uniform(lo, hi)

    if last_send_failed:
        total += random.uniform(15, 30)

    if sent_count > 0 and sent_count % 20 == 0:
        total += random.uniform(30, 60)

    return total

def parse_contact_filter(stored):
    if not stored:
        return {'type': 'all', 'tag': None}
    if isinstance(stored, dict):
        return stored
    try:
        data = json.loads(stored)
        if isinstance(data, dict):
            return {'type': data.get('type', 'all'), 'tag': data.get('tag')}
    except (json.JSONDecodeError, TypeError):
        pass
    return {'type': 'all', 'tag': None}

def contact_has_tag(tags_value, tag):
    if not tag:
        return True
    parts = [t.strip().lower() for t in str(tags_value or 'general').split(',') if t.strip()]
    return tag.strip().lower() in parts

def get_contacts_for_user(c, user_id, contact_filter=None):
    filt = contact_filter or {'type': 'all', 'tag': None}
    c.execute(
        'SELECT id, phone, name, vehicle, tags FROM contacts WHERE user_id = ? ORDER BY id',
        (user_id,)
    )
    rows = c.fetchall()
    contacts = [
        {'id': r[0], 'phone': r[1], 'name': r[2], 'vehicle': r[3], 'tags': r[4]}
        for r in rows
    ]
    if filt.get('type') == 'tag' and filt.get('tag'):
        tag = filt['tag']
        contacts = [c for c in contacts if contact_has_tag(c.get('tags'), tag)]
    return contacts

def parse_scheduled_datetime(value):
    if not value:
        return None
    text = str(value).strip().replace('Z', '')
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text[:19] if 'T' in text else text[:16], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None

def is_schedule_due(scheduled_for):
    dt = parse_scheduled_datetime(scheduled_for)
    return bool(dt and dt <= datetime.now())

def log_delivery(c, campaign_id, contact, session, status, error_message=None):
    c.execute(
        '''INSERT INTO campaign_deliveries
           (campaign_id, contact_id, contact_name, contact_phone, whatsapp_phone, status, error_message, sent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            campaign_id,
            contact.get('id'),
            contact.get('name'),
            contact.get('phone'),
            session.get('phone_number') if session else None,
            status,
            error_message,
            datetime.now().isoformat(),
        )
    )

def update_campaign_progress(c, campaign_id, progress_index, contact, session):
    c.execute(
        '''UPDATE campaigns SET progress_index = ?, current_contact_name = ?,
           current_contact_phone = ?, active_whatsapp_phone = ? WHERE id = ?''',
        (
            progress_index,
            contact.get('name') if contact else None,
            contact.get('phone') if contact else None,
            session.get('phone_number') if session else None,
            campaign_id,
        )
    )

def clear_campaign_progress(c, campaign_id):
    c.execute(
        '''UPDATE campaigns SET current_contact_name = NULL, current_contact_phone = NULL,
           active_whatsapp_phone = NULL WHERE id = ?''',
        (campaign_id,)
    )

def campaign_progress_payload(row_map):
    return {
        'progress_index': row_map.get('progress_index') or 0,
        'current_contact_name': row_map.get('current_contact_name'),
        'current_contact_phone': row_map.get('current_contact_phone'),
        'active_whatsapp_phone': row_map.get('active_whatsapp_phone'),
    }

def get_user_country_code(user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT country_code FROM users WHERE id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    cc = re.sub(r'\D', '', row[0] if row and row[0] else '') or '91'
    return cc

def detect_country_code_from_phone(phone_number):
    digits = re.sub(r'\D', '', phone_number or '')
    if len(digits) <= 10:
        return '91'
    return digits[:-10] or '91'

def parse_attachment_paths(stored):
    if not stored:
        return []
    if isinstance(stored, list):
        return stored
    text = str(stored).strip()
    if text.startswith('['):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return [text]
    return [text]

def normalize_attachments(stored):
    attachments = []
    for item in parse_attachment_paths(stored):
        if isinstance(item, dict):
            file_path = item.get('path') or item.get('attachment_path')
            filename = item.get('filename') or (os.path.basename(file_path) if file_path else '')
            if file_path:
                attachments.append({'path': file_path, 'filename': filename})
        elif item:
            file_path = str(item)
            attachments.append({'path': file_path, 'filename': os.path.basename(file_path)})
    return attachments

def normalize_phone(phone, country_code='91'):
    if not phone:
        return ''
    digits = re.sub(r'\D', '', str(phone))
    cc = re.sub(r'\D', '', str(country_code or '91')) or '91'
    if not digits:
        return ''
    if digits.startswith('0'):
        digits = digits.lstrip('0')
    if digits.startswith(cc) and len(digits) > len(cc) + 6:
        return digits
    if len(digits) == 10:
        return cc + digits
    if len(digits) > 10:
        return digits
    return cc + digits

def campaign_error_message(exc):
    msg = str(exc).strip()
    if not msg or msg.lower() == 'none':
        return 'WhatsApp send failed. Restart bridge (npm start) and try again.'
    if 'browser is already running' in msg.lower() or 'browser is busy' in msg.lower():
        return 'WhatsApp browser conflict. Run stop-bridge.bat, wait 10 seconds, run run.bat, then retry.'
    return msg

def get_session_row(session_id, user_id):
    conn = db_connect()
    c = conn.cursor()
    c.execute('''SELECT id, bridge_client_id, phone_number, status
                 FROM whatsapp_sessions WHERE id = ? AND user_id = ?''',
              (session_id, user_id))
    row = c.fetchone()
    conn.close()
    return row

def sync_bridge_status(session_id, bridge_client_id):
    bridge = wa_bridge.client_status(bridge_client_id)
    status = bridge.get('status', 'disconnected')
    phone = bridge.get('phone_number')
    db_status = 'connected' if status == 'connected' else (
        'pending' if status in ('qr', 'initializing', 'authenticated', 'starting') else 'disconnected'
    )

    conn = db_connect()
    c = conn.cursor()
    c.execute('''UPDATE whatsapp_sessions
                 SET status = ?, phone_number = COALESCE(?, phone_number), last_active = ?
                 WHERE id = ?''',
              (db_status, phone, datetime.now().isoformat(), session_id))
    if phone and db_status == 'connected':
        cc = detect_country_code_from_phone(phone)
        c.execute('''UPDATE users SET country_code = ?
                     WHERE id = (SELECT user_id FROM whatsapp_sessions WHERE id = ?)''',
                  (cc, session_id))
    conn.commit()
    conn.close()

    if db_status == 'connected':
        sync_active_sessions_file()

    return {
        'status': db_status,
        'bridge_status': status,
        'phone_number': phone,
        'qr_code': bridge.get('qr_code'),
        'error': bridge.get('error')
    }

# API Routes
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/api/bridge/health', methods=['GET'])
def bridge_health():
    config = {
        'WA_BRIDGE_URL': os.environ.get('WA_BRIDGE_URL'),
        'BRIDGE_PORT': os.environ.get('BRIDGE_PORT'),
        'BRIDGE_HTTP_PORT': os.environ.get('BRIDGE_HTTP_PORT'),
        'resolved_url': wa_bridge.get_bridge_url(),
    }
    try:
        payload = wa_bridge.health_check()
        return jsonify({
            'ok': True,
            'bridge_url': wa_bridge.get_bridge_url(),
            'config': config,
            'bridge': payload
        }), 200
    except BridgeError as exc:
        return jsonify({
            'ok': False,
            'bridge_url': wa_bridge.get_bridge_url(),
            'config': config,
            'error': str(exc),
            'hint': (
                'On bridge service add HTTP_PORT=${{PORT}}. '
                'On web service set WA_BRIDGE_URL=http://${{bridge.RAILWAY_PRIVATE_DOMAIN}}:${{bridge.HTTP_PORT}}'
            )
        }), 503

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    company_name = data.get('company_name')
    phone = data.get('phone')
    country_code = re.sub(r'\D', '', data.get('country_code', '91')) or '91'
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    conn = db_connect()
    c = conn.cursor()
    
    try:
        password_hash = generate_password_hash(password)
        normalized_phone = normalize_phone(phone, country_code) if phone else None
        c.execute('INSERT INTO users (email, password_hash, company_name, phone, country_code) VALUES (?, ?, ?, ?, ?)',
                  (email, password_hash, company_name, normalized_phone, country_code))
        user_id = c.lastrowid
        
        # Generate API key for new user
        api_key = generate_api_key()
        c.execute('INSERT INTO api_keys (user_id, api_key) VALUES (?, ?)', (user_id, api_key))
        
        conn.commit()
        return jsonify({'message': 'User created successfully', 'api_key': api_key}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Email already exists'}), 409
    finally:
        conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT id, email, password_hash, company_name FROM users WHERE email = ?', (email,))
    user = c.fetchone()
    conn.close()
    
    if user and check_password_hash(user[2], password):
        access_token = create_access_token(identity=str(user[0]))
        return jsonify({
            'access_token': access_token,
            'user': {'id': user[0], 'email': user[1], 'company_name': user[3]}
        }), 200
    else:
        return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/whatsapp/link', methods=['POST'])
@jwt_required()
def start_whatsapp_link():
    """Start real WhatsApp Web pairing and return session id for QR polling."""
    user_id = get_current_user_id()

    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM whatsapp_sessions WHERE user_id = ?', (user_id,))
    account_count = c.fetchone()[0]
    if account_count >= MAX_WHATSAPP_ACCOUNTS:
        conn.close()
        return jsonify({
            'error': f'Maximum {MAX_WHATSAPP_ACCOUNTS} WhatsApp accounts allowed. Remove an account first.'
        }), 400

    bridge_client_id = secrets.token_hex(16)
    c.execute('''INSERT INTO whatsapp_sessions
                 (user_id, bridge_client_id, status, last_active)
                 VALUES (?, ?, 'pending', ?)''',
              (user_id, bridge_client_id, datetime.now().isoformat()))
    session_id = c.lastrowid
    conn.commit()
    conn.close()

    try:
        wa_bridge.start_client(bridge_client_id)
        time.sleep(1.5)
        payload = sync_bridge_status(session_id, bridge_client_id)
        payload['session_id'] = session_id
        return jsonify(payload), 200
    except BridgeError as exc:
        conn = db_connect()
        c = conn.cursor()
        c.execute('DELETE FROM whatsapp_sessions WHERE id = ?', (session_id,))
        conn.commit()
        conn.close()
        return jsonify({'error': str(exc)}), 503

@app.route('/api/whatsapp/sessions/<int:session_id>/status', methods=['GET'])
@jwt_required()
def whatsapp_link_status(session_id):
    user_id = get_current_user_id()
    row = get_session_row(session_id, user_id)
    if not row:
        return jsonify({'error': 'Session not found'}), 404

    _, bridge_client_id, phone_number, status = row
    if not bridge_client_id:
        return jsonify({'status': status, 'phone_number': phone_number, 'qr_code': None}), 200

    try:
        payload = sync_bridge_status(session_id, bridge_client_id)
    except BridgeError as exc:
        msg = str(exc).lower()
        if 'client not found' in msg or 'not found' in msg:
            try:
                wa_bridge.start_client(bridge_client_id)
                time.sleep(1.5)
                payload = sync_bridge_status(session_id, bridge_client_id)
            except BridgeError as retry_exc:
                return jsonify({'error': str(retry_exc), 'status': 'error'}), 503
        else:
            return jsonify({'error': str(exc), 'status': 'error'}), 503

    payload['session_id'] = session_id
    return jsonify(payload), 200

@app.route('/api/whatsapp/sessions/<int:session_id>/reconnect', methods=['POST'])
@jwt_required()
def reconnect_whatsapp(session_id):
    user_id = get_current_user_id()
    row = get_session_row(session_id, user_id)
    if not row:
        return jsonify({'error': 'Session not found'}), 404

    _, bridge_client_id, _, _ = row
    if not bridge_client_id:
        return jsonify({'error': 'Session cannot be reconnected'}), 400

    conn = db_connect()
    c = conn.cursor()
    c.execute('UPDATE whatsapp_sessions SET status = ?, last_active = ? WHERE id = ?',
              ('pending', datetime.now().isoformat(), session_id))
    conn.commit()
    conn.close()

    try:
        wa_bridge.start_client(bridge_client_id)
        time.sleep(1.5)
        payload = sync_bridge_status(session_id, bridge_client_id)
        payload['session_id'] = session_id
        return jsonify(payload), 200
    except BridgeError as exc:
        return jsonify({'error': str(exc)}), 503

@app.route('/api/whatsapp/qr', methods=['POST'])
@jwt_required()
def generate_qr():
    """Backward-compatible alias for start_whatsapp_link."""
    return start_whatsapp_link()

@app.route('/api/whatsapp/sessions', methods=['GET'])
@jwt_required()
def get_sessions():
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('''SELECT id, phone_number, status, messages_today, daily_limit, last_active, created_at
                 FROM whatsapp_sessions WHERE user_id = ?''', (user_id,))
    sessions = [{'id': row[0], 'phone_number': row[1], 'status': row[2], 'messages_today': row[3],
                 'daily_limit': row[4], 'last_active': row[5], 'created_at': row[6]} for row in c.fetchall()]
    conn.close()
    
    return jsonify({'sessions': sessions}), 200

@app.route('/api/whatsapp/sessions', methods=['POST'])
@jwt_required()
def add_session():
    return jsonify({'error': 'Use WhatsApp QR linking instead (Add Account button)'}), 400

@app.route('/api/whatsapp/sessions/<int:session_id>', methods=['DELETE'])
@jwt_required()
def delete_session(session_id):
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT bridge_client_id FROM whatsapp_sessions WHERE id = ? AND user_id = ?',
              (session_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404

    bridge_client_id = row[0]
    c.execute('DELETE FROM whatsapp_sessions WHERE id = ? AND user_id = ?', (session_id, user_id))
    conn.commit()
    conn.close()

    if bridge_client_id:
        try:
            wa_bridge.destroy_client(bridge_client_id)
        except BridgeError:
            pass

    sync_active_sessions_file()
    
    return jsonify({'message': 'Session deleted'}), 200

@app.route('/api/contacts', methods=['GET'])
@jwt_required()
def get_contacts():
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT id, phone, name, vehicle, tags, created_at FROM contacts WHERE user_id = ?', (user_id,))
    contacts = [{'id': row[0], 'phone': row[1], 'name': row[2], 'vehicle': row[3], 'tags': row[4], 'created_at': row[5]} for row in c.fetchall()]
    conn.close()
    
    return jsonify({'contacts': contacts}), 200

@app.route('/api/contacts', methods=['POST'])
@jwt_required()
def add_contact():
    user_id = get_current_user_id()
    data = request.json
    phone = data.get('phone')
    name = data.get('name')
    vehicle = data.get('vehicle')
    tags = data.get('tags', 'manual')
    
    if not phone:
        return jsonify({'error': 'Phone number required'}), 400
    
    country_code = get_user_country_code(user_id)
    phone = normalize_phone(phone, country_code)
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('INSERT INTO contacts (user_id, phone, name, vehicle, tags) VALUES (?, ?, ?, ?, ?)',
              (user_id, phone, name, vehicle, tags))
    contact_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return jsonify({'id': contact_id, 'message': 'Contact added'}), 201

@app.route('/api/contacts/import', methods=['POST'])
@jwt_required()
def import_contacts():
    user_id = get_current_user_id()
    data = request.json
    contacts_data = data.get('contacts', [])
    country_code = get_user_country_code(user_id)
    
    conn = db_connect()
    c = conn.cursor()
    imported = 0
    for contact in contacts_data:
        phone = contact.get('phone')
        if phone:
            phone = normalize_phone(phone, country_code)
            c.execute('INSERT INTO contacts (user_id, phone, name, vehicle, tags) VALUES (?, ?, ?, ?, ?)',
                      (user_id, phone, contact.get('name'), contact.get('vehicle'), 'imported'))
            imported += 1
    conn.commit()
    conn.close()
    
    return jsonify({'imported': imported}), 200

@app.route('/api/contacts/<int:contact_id>', methods=['DELETE'])
@jwt_required()
def delete_contact(contact_id):
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('DELETE FROM contacts WHERE id = ? AND user_id = ?', (contact_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Contact deleted'}), 200

@app.route('/api/contacts/tags', methods=['GET'])
@jwt_required()
def get_contact_tags():
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT tags FROM contacts WHERE user_id = ?', (user_id,))
    tag_set = set()
    for row in c.fetchall():
        for part in str(row[0] or 'general').split(','):
            part = part.strip()
            if part:
                tag_set.add(part)
    conn.close()
    return jsonify({'tags': sorted(tag_set, key=str.lower)}), 200

@app.route('/api/templates', methods=['GET'])
@jwt_required()
def get_templates():
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT id, name, content, created_at FROM templates WHERE user_id = ?', (user_id,))
    templates = [{'id': row[0], 'name': row[1], 'content': row[2], 'created_at': row[3]} for row in c.fetchall()]
    conn.close()
    
    return jsonify({'templates': templates}), 200

@app.route('/api/templates', methods=['POST'])
@jwt_required()
def create_template():
    user_id = get_current_user_id()
    data = request.json
    name = data.get('name')
    content = data.get('content')
    
    if not name or not content:
        return jsonify({'error': 'Name and content required'}), 400
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('INSERT INTO templates (user_id, name, content) VALUES (?, ?, ?)',
              (user_id, name, content))
    template_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return jsonify({'id': template_id, 'message': 'Template created'}), 201

@app.route('/api/templates/<int:template_id>', methods=['DELETE'])
@jwt_required()
def delete_template(template_id):
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('DELETE FROM templates WHERE id = ? AND user_id = ?', (template_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Template deleted'}), 200

@app.route('/api/campaigns', methods=['GET'])
@jwt_required()
def get_campaigns():
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('''SELECT id, name, status, message, recipient_count, sent_count, scheduled_for, created_at, completed_at,
                        progress_index, current_contact_name, active_whatsapp_phone
                 FROM campaigns WHERE user_id = ? ORDER BY created_at DESC''', (user_id,))
    campaigns = [{'id': row[0], 'name': row[1], 'status': row[2], 'message': row[3],
                  'recipient_count': row[4], 'sent_count': row[5], 'scheduled_for': row[6],
                  'created_at': row[7], 'completed_at': row[8], 'progress_index': row[9] or 0,
                  'current_contact_name': row[10], 'active_whatsapp_phone': row[11]} for row in c.fetchall()]
    conn.close()
    
    return jsonify({'campaigns': campaigns}), 200

@app.route('/api/campaigns/upload', methods=['POST'])
@jwt_required()
def upload_campaign_attachment():
    user_id = get_current_user_id()
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'error': 'Empty file'}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': 'Unsupported file type. Use JPG, PNG, PDF, or APK'}), 400

    safe_name = f'{user_id}_{secrets.token_hex(8)}{ext}'
    save_path = os.path.join(UPLOAD_DIR, safe_name)
    file.save(save_path)

    return jsonify({'attachment_path': save_path, 'filename': file.filename}), 200

@app.route('/api/campaigns/<int:campaign_id>', methods=['GET'])
@jwt_required()
def get_campaign(campaign_id):
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('''SELECT id, name, status, message, attachment_path, recipient_count, sent_count,
                        scheduled_for, created_at, completed_at, error_message, contact_filter,
                        current_contact_name, current_contact_phone, active_whatsapp_phone, progress_index
                 FROM campaigns WHERE id = ? AND user_id = ?''', (campaign_id, user_id))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Campaign not found'}), 404
    attachments = normalize_attachments(row[4])
    return jsonify({
        'id': row[0], 'name': row[1], 'status': row[2], 'message': row[3],
        'attachments': attachments,
        'recipient_count': row[5], 'sent_count': row[6], 'scheduled_for': row[7],
        'created_at': row[8], 'completed_at': row[9], 'error_message': row[10],
        'contact_filter': parse_contact_filter(row[11]),
        'current_contact_name': row[12], 'current_contact_phone': row[13],
        'active_whatsapp_phone': row[14], 'progress_index': row[15] or 0,
    }), 200

@app.route('/api/campaigns/<int:campaign_id>/deliveries', methods=['GET'])
@jwt_required()
def get_campaign_deliveries(campaign_id):
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT id FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, user_id))
    if not c.fetchone():
        conn.close()
        return jsonify({'error': 'Campaign not found'}), 404
    c.execute(
        '''SELECT id, contact_id, contact_name, contact_phone, whatsapp_phone, status, error_message, sent_at
           FROM campaign_deliveries WHERE campaign_id = ? ORDER BY id''',
        (campaign_id,)
    )
    deliveries = [
        {
            'id': r[0], 'contact_id': r[1], 'contact_name': r[2], 'contact_phone': r[3],
            'whatsapp_phone': r[4], 'status': r[5], 'error_message': r[6], 'sent_at': r[7],
        }
        for r in c.fetchall()
    ]
    conn.close()
    return jsonify({'deliveries': deliveries}), 200

@app.route('/api/campaigns', methods=['POST'])
@jwt_required()
def create_campaign():
    user_id = get_current_user_id()
    data = request.json
    name = data.get('name')
    message = data.get('message', '')
    attachment_paths = data.get('attachment_paths') or []
    attachment_path = data.get('attachment_path')
    if attachment_path and not attachment_paths:
        attachment_paths = [attachment_path]
    scheduled_for = data.get('scheduled_for')
    contact_filter = data.get('contact_filter') or {'type': 'all', 'tag': None}
    if contact_filter.get('type') == 'tag' and not contact_filter.get('tag'):
        contact_filter = {'type': 'all', 'tag': None}

    if not name:
        return jsonify({'error': 'Name required'}), 400
    if not message and not attachment_paths:
        return jsonify({'error': 'Message or attachment required'}), 400
    if scheduled_for:
        schedule_dt = parse_scheduled_datetime(scheduled_for)
        if not schedule_dt:
            return jsonify({'error': 'Invalid schedule time'}), 400
        if schedule_dt <= datetime.now():
            return jsonify({'error': 'Schedule time must be in the future'}), 400

    attachments = normalize_attachments(attachment_paths)
    stored_paths = json.dumps(attachments) if attachments else None
    stored_filter = json.dumps(contact_filter)

    conn = db_connect()
    c = conn.cursor()
    target_contacts = get_contacts_for_user(c, user_id, contact_filter)
    recipient_count = len([ct for ct in target_contacts if ct.get('phone')])
    if recipient_count == 0:
        conn.close()
        return jsonify({'error': 'No contacts match this segment'}), 400

    c.execute('''INSERT INTO campaigns (user_id, name, message, attachment_path, scheduled_for, recipient_count, contact_filter, status)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (user_id, name, message, stored_paths, scheduled_for, recipient_count, stored_filter, 'pending'))
    campaign_id = c.lastrowid
    conn.commit()

    if not scheduled_for:
        c.execute('UPDATE campaigns SET status = ? WHERE id = ?', ('running', campaign_id))
        conn.commit()

    conn.close()

    if not scheduled_for:
        start_campaign_background(campaign_id, user_id)

    return jsonify({
        'id': campaign_id,
        'message': 'Campaign created',
        'scheduled': bool(scheduled_for),
        'recipient_count': recipient_count,
    }), 201

@app.route('/api/campaigns/<int:campaign_id>/start', methods=['POST'])
@jwt_required()
def start_campaign(campaign_id):
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT message, recipient_count FROM campaigns WHERE id = ? AND user_id = ?',
              (campaign_id, user_id))
    campaign = c.fetchone()
    conn.close()
    
    if not campaign:
        return jsonify({'error': 'Campaign not found'}), 404
    
    start_campaign_background(campaign_id, user_id)
    
    return jsonify({'message': 'Campaign started'}), 200

@app.route('/api/campaigns/<int:campaign_id>/pause', methods=['POST'])
@jwt_required()
def pause_campaign(campaign_id):
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT status FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Campaign not found'}), 404
    if row[0] != 'running':
        conn.close()
        return jsonify({'error': f'Cannot pause campaign with status: {row[0]}'}), 400
    conn.close()

    ctrl = _get_campaign_control(campaign_id)
    ctrl['paused'] = True
    conn = db_connect()
    c = conn.cursor()
    c.execute('UPDATE campaigns SET status = ? WHERE id = ?', ('paused', campaign_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Campaign paused', 'status': 'paused'}), 200

@app.route('/api/campaigns/<int:campaign_id>/resume', methods=['POST'])
@jwt_required()
def resume_campaign(campaign_id):
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT status FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Campaign not found'}), 404
    if row[0] != 'paused':
        conn.close()
        return jsonify({'error': f'Cannot resume campaign with status: {row[0]}'}), 400
    conn.close()

    ctrl = _get_campaign_control(campaign_id)
    ctrl['paused'] = False
    ctrl['cancelled'] = False
    conn = db_connect()
    c = conn.cursor()
    c.execute('UPDATE campaigns SET status = ?, error_message = NULL WHERE id = ?', ('running', campaign_id))
    conn.commit()
    conn.close()
    if not _campaign_thread_active(campaign_id):
        start_campaign_background(campaign_id, user_id, resume=True)
    return jsonify({'message': 'Campaign resumed', 'status': 'running'}), 200

@app.route('/api/campaigns/<int:campaign_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_campaign(campaign_id):
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT status FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Campaign not found'}), 404
    if row[0] not in ('running', 'paused'):
        conn.close()
        return jsonify({'error': f'Cannot cancel campaign with status: {row[0]}'}), 400
    conn.close()

    ctrl = _get_campaign_control(campaign_id)
    ctrl['cancelled'] = True
    ctrl['paused'] = False
    return jsonify({'message': 'Campaign cancelling...', 'status': 'cancelled'}), 200

@app.route('/api/campaigns/<int:campaign_id>/retry-failed', methods=['POST'])
@jwt_required()
def retry_failed_campaign(campaign_id):
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT status FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Campaign not found'}), 404
    if row[0] in ('running', 'paused'):
        conn.close()
        return jsonify({'error': 'Campaign is still active'}), 400
    failed_count = c.execute(
        'SELECT COUNT(*) FROM campaign_deliveries WHERE campaign_id = ? AND status = ?',
        (campaign_id, 'failed')
    ).fetchone()[0]
    conn.close()
    if not failed_count:
        return jsonify({'error': 'No failed deliveries to retry'}), 400
    start_campaign_background(campaign_id, user_id, retry_failed=True)
    return jsonify({'message': 'Retrying failed contacts', 'status': 'running', 'retry_count': failed_count}), 200

def start_campaign_background(campaign_id, user_id, *, resume=False, retry_failed=False):
    """Send campaign messages through connected WhatsApp bridge clients."""
    with _campaign_thread_lock:
        if campaign_id in _active_campaign_threads:
            return
        _active_campaign_threads.add(campaign_id)

    _init_campaign_control(campaign_id)

    def personalize(text, contact):
        values = {
            'name': contact.get('name') or '',
            'phone': contact.get('phone') or '',
            'vehicle': contact.get('vehicle') or '',
            'id': str(contact.get('id') or '')
        }
        result = text
        for key, value in values.items():
            result = result.replace('{' + key + '}', value)
        return result

    def send():
        conn = db_connect()
        c = conn.cursor()
        sent = 0
        last_error = None
        try:
            c.execute(
                'SELECT message, attachment_path, contact_filter, sent_count, progress_index FROM campaigns WHERE id = ? AND user_id = ?',
                (campaign_id, user_id)
            )
            campaign_row = c.fetchone()
            if not campaign_row:
                return
            message, attachment_path, contact_filter_raw, existing_sent, existing_progress = campaign_row
            attachments = normalize_attachments(attachment_path)
            contact_filter = parse_contact_filter(contact_filter_raw)
            settings = get_user_settings(user_id)
            ensure_daily_reset(c, user_id)
            sessions = get_connected_sessions(c, user_id)
            if not sessions:
                last_error = 'No connected WhatsApp account. Link one under WhatsApp Accounts.'
                c.execute('UPDATE campaigns SET status = ?, error_message = ? WHERE id = ?',
                          ('failed', last_error, campaign_id))
                conn.commit()
                return

            for session in sessions:
                try:
                    wa_bridge.ensure_client_connected(session['bridge_client_id'])
                except BridgeError as exc:
                    last_error = campaign_error_message(exc)

            country_code = get_user_country_code(user_id)
            contacts = get_contacts_for_user(c, user_id, contact_filter)

            if retry_failed:
                retryable_ids = get_retryable_failed_contact_ids(c, campaign_id)
                contacts = [ct for ct in contacts if ct.get('id') in retryable_ids]
                if not contacts:
                    last_error = 'No failed contacts to retry.'
                    c.execute('UPDATE campaigns SET status = ?, error_message = ? WHERE id = ?',
                              ('completed', last_error, campaign_id))
                    conn.commit()
                    return
            elif resume:
                done_ids = get_delivered_contact_ids(c, campaign_id)
                contacts = [ct for ct in contacts if ct.get('id') not in done_ids]

            recipient_count = len([ct for ct in get_contacts_for_user(c, user_id, contact_filter) if ct.get('phone')])
            sent = existing_sent or 0
            progress_idx = existing_progress or 0

            c.execute(
                'UPDATE campaigns SET status = ?, recipient_count = ?, error_message = NULL WHERE id = ?',
                ('running', recipient_count, campaign_id)
            )
            conn.commit()

            session_index = 0
            last_send_failed = False
            for contact in contacts:
                if _wait_if_paused_or_cancelled(campaign_id):
                    break

                if not contact.get('phone'):
                    log_delivery(c, campaign_id, contact, None, 'skipped', 'No phone number')
                    progress_idx += 1
                    conn.commit()
                    continue

                progress_idx += 1
                if settings['rotate_accounts']:
                    session, session_index = pick_session(sessions, session_index + 1)
                else:
                    session = sessions[0] if sessions[0]['messages_today'] < sessions[0]['daily_limit'] else None

                update_campaign_progress(c, campaign_id, progress_idx, contact, session)
                conn.commit()

                if not session:
                    last_error = 'All WhatsApp accounts reached their daily limit'
                    log_delivery(c, campaign_id, contact, None, 'failed', last_error)
                    conn.commit()
                    break

                bridge_client_id = session['bridge_client_id']
                body = personalize(message, contact)
                phone_norm = normalize_phone(contact['phone'], country_code)

                try:
                    validation = wa_bridge.validate_number(bridge_client_id, phone_norm)
                    if not validation.get('registered'):
                        log_delivery(c, campaign_id, contact, session, 'skipped', NOT_ON_WHATSAPP_REASON)
                        conn.commit()
                        continue
                except BridgeError:
                    pass

                try:
                    wa_bridge.send_message(
                        bridge_client_id,
                        phone_norm,
                        body,
                        attachments=attachments
                    )
                    sent += 1
                    last_send_failed = False
                    session['messages_today'] += 1
                    log_delivery(c, campaign_id, contact, session, 'sent')
                    c.execute('UPDATE campaigns SET sent_count = ? WHERE id = ?', (sent, campaign_id))
                    c.execute(
                        '''UPDATE whatsapp_sessions
                           SET messages_today = messages_today + 1, last_active = ?
                           WHERE bridge_client_id = ?''',
                        (datetime.now().isoformat(), bridge_client_id)
                    )
                    conn.commit()
                except BridgeError as exc:
                    last_send_failed = True
                    last_error = campaign_error_message(exc)
                    if is_non_whatsapp_error(last_error):
                        log_delivery(c, campaign_id, contact, session, 'skipped', NOT_ON_WHATSAPP_REASON)
                    else:
                        log_delivery(c, campaign_id, contact, session, 'failed', last_error)
                    conn.commit()
                except Exception as exc:
                    last_send_failed = True
                    last_error = campaign_error_message(exc)
                    if is_non_whatsapp_error(last_error):
                        log_delivery(c, campaign_id, contact, session, 'skipped', NOT_ON_WHATSAPP_REASON)
                    else:
                        log_delivery(c, campaign_id, contact, session, 'failed', last_error)
                    conn.commit()

                if _wait_if_paused_or_cancelled(campaign_id):
                    break
                time.sleep(campaign_send_pause(user_id, last_send_failed, sent))
        except Exception as exc:
            last_error = campaign_error_message(exc)
        finally:
            try:
                ctrl = _get_campaign_control(campaign_id)
                if ctrl.get('cancelled'):
                    final_status = 'cancelled'
                    c.execute(
                        'UPDATE campaigns SET status = ?, sent_count = ?, completed_at = ?, error_message = ? WHERE id = ?',
                        (final_status, sent, datetime.now().isoformat(), last_error or 'Cancelled by user', campaign_id)
                    )
                elif ctrl.get('paused'):
                    final_status = 'paused'
                    c.execute(
                        'UPDATE campaigns SET status = ?, sent_count = ?, error_message = ? WHERE id = ?',
                        (final_status, sent, last_error, campaign_id)
                    )
                else:
                    final_status = 'completed' if sent > 0 else 'failed'
                    clear_campaign_progress(c, campaign_id)
                    c.execute(
                        'UPDATE campaigns SET status = ?, sent_count = ?, completed_at = ?, error_message = ? WHERE id = ?',
                        (final_status, sent, datetime.now().isoformat(), last_error, campaign_id)
                    )
                conn.commit()
            finally:
                conn.close()
                with _campaign_thread_lock:
                    _active_campaign_threads.discard(campaign_id)
                _clear_campaign_control(campaign_id)

    thread = threading.Thread(target=send)
    thread.daemon = True
    thread.start()

def run_campaign_scheduler():
    while True:
        try:
            conn = db_connect()
            c = conn.cursor()
            c.execute(
                '''SELECT id, user_id, scheduled_for FROM campaigns
                   WHERE status = 'pending' AND scheduled_for IS NOT NULL AND TRIM(scheduled_for) != '''''
            )
            due = []
            for campaign_id, uid, scheduled_for in c.fetchall():
                if is_schedule_due(scheduled_for):
                    due.append((campaign_id, uid))
            for campaign_id, uid in due:
                c.execute(
                    "UPDATE campaigns SET status = 'running' WHERE id = ? AND status = 'pending'",
                    (campaign_id,)
                )
                if c.rowcount:
                    conn.commit()
                    start_campaign_background(campaign_id, uid)
            conn.close()
        except Exception as exc:
            print(f'Campaign scheduler error: {exc}')
        time.sleep(60)

_scheduler_started = False

def ensure_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=run_campaign_scheduler, daemon=True).start()

ensure_scheduler()

for _campaign_id, _user_id in _stuck_pending_campaigns:
    start_campaign_background(_campaign_id, _user_id)

@app.route('/api/campaigns/<int:campaign_id>', methods=['DELETE'])
@jwt_required()
def delete_campaign(campaign_id):
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('DELETE FROM campaign_deliveries WHERE campaign_id = ?', (campaign_id,))
    c.execute('DELETE FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Campaign deleted'}), 200

@app.route('/api/stats', methods=['GET'])
@jwt_required()
def get_stats():
    user_id = get_current_user_id()
    
    conn = db_connect()
    c = conn.cursor()
    
    # Count contacts
    c.execute('SELECT COUNT(*) FROM contacts WHERE user_id = ?', (user_id,))
    total_contacts = c.fetchone()[0]
    
    # Count active WhatsApp sessions
    c.execute('SELECT COUNT(*) FROM whatsapp_sessions WHERE user_id = ? AND status = ?', (user_id, 'connected'))
    active_sessions = c.fetchone()[0]
    
    # Total messages sent (from campaigns)
    c.execute('SELECT SUM(sent_count) FROM campaigns WHERE user_id = ?', (user_id,))
    total_messages = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM campaigns WHERE user_id = ? AND status = 'completed'", (user_id,))
    completed_campaigns = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM campaigns WHERE user_id = ? AND status = 'failed'", (user_id,))
    failed_campaigns = c.fetchone()[0]

    c.execute('SELECT COUNT(*) FROM whatsapp_sessions WHERE user_id = ?', (user_id,))
    total_sessions = c.fetchone()[0]

    c.execute('SELECT COALESCE(SUM(sent_count), 0) FROM campaigns WHERE user_id = ? AND created_at >= datetime("now", "-7 days")', (user_id,))
    messages_this_week = c.fetchone()[0] or 0

    conn.close()

    success_rate = 0
    finished = completed_campaigns + failed_campaigns
    if finished > 0:
        success_rate = round((completed_campaigns / finished) * 100)

    return jsonify({
        'total_contacts': total_contacts,
        'active_sessions': active_sessions,
        'total_sessions': total_sessions,
        'total_messages': total_messages,
        'messages_this_week': messages_this_week,
        'completed_campaigns': completed_campaigns,
        'failed_campaigns': failed_campaigns,
        'success_rate': success_rate,
        'max_accounts': MAX_WHATSAPP_ACCOUNTS,
        'recommended_accounts': RECOMMENDED_WHATSAPP_ACCOUNTS,
    }), 200

@app.route('/api/analytics', methods=['GET'])
@jwt_required()
def get_analytics():
    user_id = get_current_user_id()
    conn = db_connect()
    c = conn.cursor()

    labels = []
    daily_counts = []
    for days_ago in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
        labels.append((datetime.now() - timedelta(days=days_ago)).strftime('%a'))
        c.execute(
            '''SELECT COALESCE(SUM(sent_count), 0) FROM campaigns
               WHERE user_id = ? AND date(created_at) = ?''',
            (user_id, day)
        )
        daily_counts.append(c.fetchone()[0] or 0)

    c.execute(
        '''SELECT status, COUNT(*) FROM campaigns WHERE user_id = ? GROUP BY status''',
        (user_id,)
    )
    status_rows = c.fetchall()
    status_map = {row[0]: row[1] for row in status_rows}

    hour_labels = [f'{h}:00' for h in range(8, 22, 2)]
    hour_counts = [0] * len(hour_labels)
    c.execute('SELECT created_at, sent_count FROM campaigns WHERE user_id = ?', (user_id,))
    for created_at, sent_count in c.fetchall():
        if not created_at:
            continue
        try:
            hour = int(str(created_at)[11:13])
        except (ValueError, IndexError):
            continue
        bucket = max(0, min(len(hour_counts) - 1, (hour - 8) // 2))
        hour_counts[bucket] += sent_count or 0

    c.execute(
        '''SELECT phone_number, messages_today, daily_limit FROM whatsapp_sessions
           WHERE user_id = ? AND status = 'connected' ORDER BY id''',
        (user_id,)
    )
    account_usage = [
        {
            'phone_number': row[0] or 'Unknown',
            'messages_today': row[1] or 0,
            'daily_limit': row[2] or 50,
        }
        for row in c.fetchall()
    ]

    conn.close()

    return jsonify({
        'daily_labels': labels,
        'daily_counts': daily_counts,
        'campaign_status': {
            'completed': status_map.get('completed', 0),
            'failed': status_map.get('failed', 0),
            'running': status_map.get('running', 0),
            'pending': status_map.get('pending', 0),
        },
        'hour_labels': hour_labels,
        'hour_counts': hour_counts,
        'account_usage': account_usage,
    }), 200

@app.route('/api/settings', methods=['GET'])
@jwt_required()
def get_settings():
    user_id = get_current_user_id()
    settings = get_user_settings(user_id)

    conn = db_connect()
    c = conn.cursor()
    c.execute('SELECT api_key FROM api_keys WHERE user_id = ?', (user_id,))
    api_key = c.fetchone()
    conn.close()

    return jsonify({
        'api_key': api_key[0] if api_key else None,
        'country_code': settings['country_code'],
        'daily_limit': settings['daily_limit'],
        'send_delay_min': settings['send_delay_min'],
        'send_delay_max': settings['send_delay_max'],
        'rotate_accounts': settings['rotate_accounts'],
        'max_accounts': MAX_WHATSAPP_ACCOUNTS,
        'recommended_accounts': RECOMMENDED_WHATSAPP_ACCOUNTS,
    }), 200

@app.route('/api/settings', methods=['POST'])
@jwt_required()
def update_settings():
    user_id = get_current_user_id()
    data = request.json or {}
    country_code = data.get('country_code')
    daily_limit = data.get('daily_limit')
    send_delay_min = data.get('send_delay_min')
    send_delay_max = data.get('send_delay_max')
    rotate_accounts = data.get('rotate_accounts')

    conn = db_connect()
    c = conn.cursor()

    if country_code is not None:
        cc = re.sub(r'\D', '', str(country_code)) or '91'
        c.execute('UPDATE users SET country_code = ? WHERE id = ?', (cc, user_id))

    if send_delay_min is not None:
        c.execute('UPDATE users SET send_delay_min = ? WHERE id = ?', (max(1, int(send_delay_min)), user_id))

    if send_delay_max is not None:
        c.execute('UPDATE users SET send_delay_max = ? WHERE id = ?', (max(1, int(send_delay_max)), user_id))

    if rotate_accounts is not None:
        c.execute('UPDATE users SET rotate_accounts = ? WHERE id = ?', (1 if rotate_accounts else 0, user_id))

    if daily_limit is not None:
        c.execute('UPDATE whatsapp_sessions SET daily_limit = ? WHERE user_id = ?',
                  (max(1, int(daily_limit)), user_id))

    conn.commit()
    conn.close()

    settings = get_user_settings(user_id)
    return jsonify({
        'message': 'Settings saved',
        'country_code': get_user_country_code(user_id),
        'daily_limit': settings['daily_limit'],
        'send_delay_min': settings['send_delay_min'],
        'send_delay_max': settings['send_delay_max'],
        'rotate_accounts': settings['rotate_accounts'],
    }), 200

@app.route('/api/settings/api-key', methods=['POST'])
@jwt_required()
def regenerate_api_key():
    user_id = get_current_user_id()
    new_api_key = generate_api_key()
    
    conn = db_connect()
    c = conn.cursor()
    c.execute('UPDATE api_keys SET api_key = ? WHERE user_id = ?', (new_api_key, user_id))
    if c.rowcount == 0:
        c.execute('INSERT INTO api_keys (user_id, api_key) VALUES (?, ?)', (user_id, new_api_key))
    conn.commit()
    conn.close()
    
    return jsonify({'api_key': new_api_key}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)