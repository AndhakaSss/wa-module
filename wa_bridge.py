import os
import requests

WA_BRIDGE_URL = os.environ.get('WA_BRIDGE_URL', 'http://localhost:3001')
REQUEST_TIMEOUT = 15
SEND_TIMEOUT = 180


class BridgeError(Exception):
    pass


def _request(method, path, **kwargs):
    url = f'{WA_BRIDGE_URL}{path}'
    timeout = kwargs.pop('timeout', REQUEST_TIMEOUT)
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise BridgeError(
            'WhatsApp bridge is not running. Start it with: cd whatsapp-bridge && npm start'
        ) from exc

    if response.status_code >= 400:
        try:
            data = response.json()
            detail = data.get('error') or data.get('message') or response.text
        except ValueError:
            detail = response.text
        if not detail or str(detail).strip().lower() == 'none':
            detail = f'Bridge error (HTTP {response.status_code})'
        raise BridgeError(detail or 'Bridge request failed')

    if response.content:
        return response.json()
    return {}


def health_check():
    return _request('GET', '/health')


def start_client(client_id):
    return _request('POST', f'/clients/{client_id}/start')


def client_status(client_id):
    return _request('GET', f'/clients/{client_id}/status')


def destroy_client(client_id):
    return _request('DELETE', f'/clients/{client_id}')


def recover_client(client_id):
    return _request('POST', f'/clients/{client_id}/recover', timeout=120)


def validate_number(client_id, phone):
    return _request('POST', f'/clients/{client_id}/validate', json={'phone': phone})


def send_message(client_id, phone, message, attachments=None, attachment_paths=None, attachment_path=None):
    items = attachments or []
    if not items:
        paths = attachment_paths or ([attachment_path] if attachment_path else [])
        items = [{'path': p, 'filename': os.path.basename(p)} for p in paths]
    payload = {'phone': phone, 'message': message or ''}
    if items:
        payload['attachments'] = items
    timeout = SEND_TIMEOUT + max(0, len(items) - 1) * 45 if items else REQUEST_TIMEOUT
    return _request('POST', f'/clients/{client_id}/send', json=payload, timeout=timeout)


def ensure_client_connected(client_id, wait_seconds=90):
    import time

    status = None
    try:
        status = client_status(client_id)
    except BridgeError:
        start_client(client_id)
        status = {'status': 'starting'}
    else:
        if status.get('status') == 'connected':
            return status
        # Session exists but still connecting — wait, do not start a second browser
        if status.get('status') in ('starting', 'initializing', 'authenticated', 'qr'):
            pass
        else:
            start_client(client_id)

    for _ in range(wait_seconds):
        time.sleep(1)
        try:
            status = client_status(client_id)
        except BridgeError:
            continue
        if status.get('status') == 'connected':
            return status
        err = (status.get('error') or '').lower()
        if 'browser is already running' in err and status.get('status') == 'error':
            try:
                recover_client(client_id)
            except BridgeError:
                pass

    err = (status.get('error') if status else None) or ''
    if 'browser is already running' in err.lower():
        raise BridgeError(
            'WhatsApp browser is busy. Run stop-bridge.bat, wait 10 seconds, run run.bat, then retry.'
        )

    raise BridgeError(
        err or f'WhatsApp not ready (status: {status.get("status") if status else "unknown"}). Re-link your account.'
    )
