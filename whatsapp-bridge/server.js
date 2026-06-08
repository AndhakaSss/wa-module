const express = require('express');
const cors = require('cors');
const QRCode = require('qrcode');
const fs = require('fs');
const os = require('os');
const { execSync } = require('child_process');
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const puppeteer = require('puppeteer');
const path = require('path');

const app = express();
const PORT = process.env.PORT || process.env.WA_BRIDGE_PORT || 3001;
const WA_WEB_VERSION = '2.3000.1041023899';
const WA_CACHE_DIR = path.join(__dirname, '.wwebjs_cache');
// Chrome profile paths must stay short on Windows (MAX_PATH). The project folder is very deep.
const AUTH_DATA_DIR = process.env.WA_AUTH_DATA_DIR
    || path.join(process.env.LOCALAPPDATA || os.tmpdir(), 'wa-business-hub', 'wwebjs_auth');
const ACTIVE_SESSIONS_FILE = process.env.ACTIVE_SESSIONS_FILE
    || path.join(__dirname, 'active_sessions.json');

process.on('unhandledRejection', (reason) => {
    const msg = reason?.message || String(reason);
    if (
        msg.includes('Execution context was destroyed')
        || msg.includes('Could not load response body')
        || msg.includes('Protocol error')
        || msg.includes('Failed to launch the browser')
    ) {
        console.error('[bridge] puppeteer warning (ignored):', msg);
        return;
    }
    console.error('[bridge] unhandled rejection:', reason);
});

app.use(cors());
app.use(express.json());

/** @type {Map<string, { client: import('whatsapp-web.js').Client, status: string, qrCode: string|null, phone: string|null, error: string|null }>} */
const clients = new Map();
const startingClients = new Set();
let browserLaunchChain = Promise.resolve();

function sessionDir(clientId) {
    return path.join(AUTH_DATA_DIR, `session-${clientId}`);
}

function cleanupSessionLocks(clientId) {
    const dir = sessionDir(clientId);
    if (!fs.existsSync(dir)) return;
    for (const file of ['DevToolsActivePort', 'SingletonLock', 'SingletonCookie', 'SingletonSocket']) {
        const target = path.join(dir, file);
        if (fs.existsSync(target)) {
            try { fs.unlinkSync(target); } catch (_) {}
        }
    }
}

function resetSessionAuth(clientId) {
    const dir = sessionDir(clientId);
    if (fs.existsSync(dir)) {
        try { fs.rmSync(dir, { recursive: true, force: true }); } catch (_) {}
    }
    cleanupSessionLocks(clientId);
}

function killAllOrphanedBridgeBrowsers() {
    if (process.platform !== 'win32') return;
    const authNeedle = 'wa-business-hub'.replace(/'/g, "''");
    try {
        execSync(
            'powershell -NoProfile -Command "' +
            'Get-Process chrome -ErrorAction SilentlyContinue | ' +
            'Where-Object { $_.Path -like \'*puppeteer*\' -or $_.Path -like \'*chrome-win64*\' } | ' +
            'Stop-Process -Force -ErrorAction SilentlyContinue; ' +
            'Get-CimInstance Win32_Process -Filter \"name=\'chrome.exe\'\" -ErrorAction SilentlyContinue | ' +
            `Where-Object { $_.CommandLine -like '*${authNeedle}*' } | ` +
            'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"',
            { stdio: 'ignore', timeout: 25000 }
        );
    } catch (_) {}
}

function killOrphanedSessionBrowsers(_clientId) {
    killAllOrphanedBridgeBrowsers();
}

function getState(clientId) {
    return clients.get(clientId) || null;
}

function formatPhone(wid) {
    if (!wid) return null;
    const user = typeof wid === 'object' ? wid.user : String(wid).split('@')[0];
    return user ? `+${user}` : null;
}

function getMime(filePath) {
    const ext = path.extname(filePath).toLowerCase();
    const map = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.pdf': 'application/pdf',
        '.apk': 'application/vnd.android.package-archive'
    };
    return map[ext] || 'application/octet-stream';
}

function loadMedia(filePath, displayName) {
    if (!filePath || !fs.existsSync(filePath)) return null;
    const data = fs.readFileSync(filePath, { encoding: 'base64' });
    const filename = displayName || path.basename(filePath);
    return new MessageMedia(getMime(filePath), data, filename);
}

function normalizeAttachments(body) {
    if (Array.isArray(body.attachments) && body.attachments.length) {
        return body.attachments.map((item) => ({
            path: item.path || item.attachment_path,
            filename: item.filename || path.basename(item.path || item.attachment_path || '')
        })).filter((item) => item.path);
    }
    const paths = Array.isArray(body.attachment_paths)
        ? body.attachment_paths
        : (body.attachment_path ? [body.attachment_path] : []);
    return paths.map((item) => {
        if (item && typeof item === 'object') {
            const filePath = item.path || item.attachment_path;
            return { path: filePath, filename: item.filename || path.basename(filePath || '') };
        }
        return { path: item, filename: path.basename(String(item)) };
    }).filter((item) => item.path);
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function simulateTypingDelay(message) {
    const text = String(message || '').trim();
    if (!text) return;
    const ms = Math.min(12000, Math.max(1500, text.length * 25));
    await delay(ms);
}

async function ensureWebVersionCache() {
    const cacheFile = path.join(WA_CACHE_DIR, `${WA_WEB_VERSION}.html`);
    if (fs.existsSync(cacheFile)) return;

    fs.mkdirSync(WA_CACHE_DIR, { recursive: true });
    const remoteUrl = `https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/${WA_WEB_VERSION}-alpha.html`;
    console.log(`[bridge] downloading WhatsApp Web ${WA_WEB_VERSION}...`);
    const response = await fetch(remoteUrl);
    if (!response.ok) {
        throw new Error(`Could not download WhatsApp Web version (${response.status})`);
    }
    fs.writeFileSync(cacheFile, await response.text(), 'utf8');
    console.log(`[bridge] cached WhatsApp Web ${WA_WEB_VERSION}`);
}

function isRecoverableInitError(message) {
    const lower = String(message || '').toLowerCase();
    return lower.includes('execution context was destroyed')
        || lower.includes('could not load response body')
        || lower.includes('browser is already running')
        || lower.includes('failed to launch the browser')
        || lower.includes('4294967295')
        || lower.includes('protocol error');
}

function runWithBrowserLaunchLock(task) {
    const next = browserLaunchChain.then(task, task);
    browserLaunchChain = next.catch(() => {});
    return next;
}

function getPuppeteerConfig() {
    let executablePath;
    try {
        executablePath = process.env.PUPPETEER_EXECUTABLE_PATH || puppeteer.executablePath();
    } catch (_) {
        executablePath = undefined;
    }

    return {
        headless: true,
        protocolTimeout: 180000,
        executablePath,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-breakpad',
            '--disable-component-update',
            '--disable-sync',
            '--mute-audio'
        ]
    };
}

function createWhatsAppClient(clientId) {
    fs.mkdirSync(AUTH_DATA_DIR, { recursive: true });
    return new Client({
        authStrategy: new LocalAuth({
            clientId,
            dataPath: AUTH_DATA_DIR
        }),
        puppeteer: getPuppeteerConfig(),
        bypassCSP: true,
        webVersion: WA_WEB_VERSION,
        webVersionCache: {
            type: 'local',
            path: WA_CACHE_DIR
        }
    });
}

async function refreshConnectedState(state) {
    if (!state?.client) return state?.status || 'disconnected';
    if (state.status === 'connected') return 'connected';
    try {
        const waState = await state.client.getState();
        if (waState === 'CONNECTED') {
            state.status = 'connected';
            state.phone = state.client.info?.wid || state.phone;
            state.error = null;
            return 'connected';
        }
    } catch (_) {}
    return state.status;
}

async function waitUntilConnected(state, maxWaitMs = 90000) {
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
        const status = await refreshConnectedState(state);
        if (status === 'connected' && state.client) {
            return true;
        }
        if (['auth_failure', 'error', 'disconnected'].includes(state.status)) {
            return false;
        }
        await delay(1000);
    }
    return (await refreshConnectedState(state)) === 'connected' && Boolean(state.client);
}

async function destroyClientState(clientId, state) {
    clients.delete(clientId);
    if (state?.client) {
        try {
            await state.client.destroy();
        } catch (_) {}
    }
    killOrphanedSessionBrowsers(clientId);
    cleanupSessionLocks(clientId);
}

function attachClientEvents(clientId, client, state) {
    client.on('qr', async (qr) => {
        state.status = 'qr';
        state.qrCode = await QRCode.toDataURL(qr, { width: 280, margin: 1 });
        state.error = null;
    });

    client.on('authenticated', () => {
        state.status = 'authenticated';
        state.qrCode = null;
        setTimeout(async () => {
            const current = clients.get(clientId);
            if (current === state && current.status === 'authenticated') {
                console.log(`[${clientId}] stuck on authenticated, restarting session...`);
                await destroyClientState(clientId, state);
                await beginClientSession(clientId);
            }
        }, 45000);
    });

    client.on('ready', () => {
        state.status = 'connected';
        state.phone = client.info?.wid || null;
        state.qrCode = null;
        state.error = null;
        console.log(`[${clientId}] connected as ${formatPhone(state.phone)}`);
    });

    client.on('auth_failure', (msg) => {
        state.status = 'auth_failure';
        state.error = String(msg);
        console.error(`[${clientId}] auth failure:`, msg);
    });

    client.on('disconnected', (reason) => {
        state.status = 'disconnected';
        state.error = String(reason || 'disconnected');
        console.log(`[${clientId}] disconnected:`, reason);
    });
}

async function initializeClient(clientId, state, { allowRetry = true, resetAuthOnFailure = true } = {}) {
    cleanupSessionLocks(clientId);
    killOrphanedSessionBrowsers(clientId);

    try {
        await runWithBrowserLaunchLock(async () => {
            await state.client.initialize();
        });
    } catch (err) {
        const msg = err.message || String(err);
        const browserLaunchFailure = msg.toLowerCase().includes('failed to launch the browser');
        if (allowRetry && isRecoverableInitError(msg)) {
            console.log(`[${clientId}] recoverable init error, cleaning up and retrying...`);
            try { await state.client.destroy(); } catch (_) {}
            killOrphanedSessionBrowsers(clientId);
            cleanupAllSessionLocks();
            await delay(browserLaunchFailure ? 3500 : 2000);

            const client = createWhatsAppClient(clientId);
            state.client = client;
            attachClientEvents(clientId, client, state);
            await initializeClient(clientId, state, { allowRetry: false, resetAuthOnFailure });
            return;
        }
        if (resetAuthOnFailure && isRecoverableInitError(msg) && fs.existsSync(sessionDir(clientId))) {
            console.log(`[${clientId}] clearing corrupt session data and retrying...`);
            try { await state.client.destroy(); } catch (_) {}
            killOrphanedSessionBrowsers(clientId);
            resetSessionAuth(clientId);
            await delay(browserLaunchFailure ? 3500 : 2000);

            state.status = 'initializing';
            state.error = null;
            state.qrCode = null;
            const client = createWhatsAppClient(clientId);
            state.client = client;
            attachClientEvents(clientId, client, state);
            await initializeClient(clientId, state, { allowRetry: false, resetAuthOnFailure: false });
            return;
        }
        state.status = 'error';
        state.error = msg;
        console.error(`[${clientId}] init error:`, msg);
    }
}

function listSavedSessionIds() {
    if (fs.existsSync(ACTIVE_SESSIONS_FILE)) {
        try {
            const ids = JSON.parse(fs.readFileSync(ACTIVE_SESSIONS_FILE, 'utf8'));
            if (Array.isArray(ids) && ids.length) {
                return ids.filter((id) => fs.existsSync(sessionDir(id)));
            }
        } catch (_) {}
    }

    if (!fs.existsSync(AUTH_DATA_DIR)) return [];
    const sessions = fs.readdirSync(AUTH_DATA_DIR)
        .filter((name) => name.startsWith('session-'))
        .map((name) => {
            const clientId = name.slice('session-'.length);
            const dir = path.join(AUTH_DATA_DIR, name);
            return { clientId, mtime: fs.statSync(dir).mtimeMs };
        })
        .sort((a, b) => b.mtime - a.mtime);
    // Only restore the most recent session — multiple browsers cause profile lock conflicts
    return sessions.length ? [sessions[0].clientId] : [];
}

function cleanupAllSessionLocks() {
    if (!fs.existsSync(AUTH_DATA_DIR)) return;
    for (const name of fs.readdirSync(AUTH_DATA_DIR)) {
        if (name.startsWith('session-')) {
            cleanupSessionLocks(name.slice('session-'.length));
        }
    }
}

async function beginClientSession(clientId) {
    if (startingClients.has(clientId)) {
        return clients.get(clientId);
    }

    const existing = clients.get(clientId);
    if (existing) {
        if (existing.status === 'connected') {
            return existing;
        }
        if (['authenticated', 'initializing', 'qr'].includes(existing.status)) {
            return existing;
        }
        if (['error', 'disconnected', 'auth_failure'].includes(existing.status)) {
            console.log(`[${clientId}] restarting stuck session (${existing.status})`);
            await destroyClientState(clientId, existing);
        } else {
            return existing;
        }
    }

    startingClients.add(clientId);
    try {
        const state = {
            client: null,
            status: 'initializing',
            qrCode: null,
            phone: null,
            error: null
        };
        clients.set(clientId, state);

        const client = createWhatsAppClient(clientId);

        state.client = client;
        attachClientEvents(clientId, client, state);
        await initializeClient(clientId, state);
        return state;
    } finally {
        startingClients.delete(clientId);
    }
}

app.get('/health', (_req, res) => {
    res.json({ ok: true, clients: clients.size });
});

app.post('/clients/:clientId/start', async (req, res) => {
    const clientId = req.params.clientId;
    const existing = clients.get(clientId);
    if (existing?.status === 'connected') {
        return res.json({
            status: existing.status,
            phone_number: formatPhone(existing.phone),
            qr_code: existing.qrCode,
            error: existing.error
        });
    }

    await beginClientSession(clientId);
    res.json({ status: 'starting' });
});

app.post('/clients/:clientId/recover', async (req, res) => {
    const clientId = req.params.clientId;
    const existing = clients.get(clientId);
    if (existing) {
        await destroyClientState(clientId, existing);
    }
    killOrphanedSessionBrowsers(clientId);
    cleanupSessionLocks(clientId);
    await delay(1500);
    await beginClientSession(clientId);
    res.json({ status: 'recovering' });
});

app.get('/clients/:clientId/status', async (req, res) => {
    const state = getState(req.params.clientId);
    if (!state) {
        return res.status(404).json({ error: 'Client not found' });
    }

    const status = await refreshConnectedState(state);

    res.json({
        status,
        phone_number: formatPhone(state.phone),
        qr_code: state.qrCode,
        error: state.error
    });
});

app.delete('/clients/:clientId', async (req, res) => {
    const clientId = req.params.clientId;
    const state = clients.get(clientId);
    if (!state) {
        return res.json({ message: 'Already removed' });
    }

    clients.delete(clientId);

    try {
        if (state.client) {
            await state.client.logout().catch(() => {});
            await state.client.destroy().catch(() => {});
        }
    } catch (err) {
        console.error(`[${clientId}] destroy error:`, err.message);
    }

    res.json({ message: 'Client removed' });
});

app.post('/clients/:clientId/validate', async (req, res) => {
    const clientId = req.params.clientId;
    let state = getState(clientId);
    if (!state || !state.client) {
        await beginClientSession(clientId);
        state = getState(clientId);
    }
    if (!state || !state.client) {
        return res.status(404).json({ error: 'WhatsApp session not loaded. Restart bridge or re-link account.' });
    }

    if (state.status !== 'connected') {
        const ready = await waitUntilConnected(state, 90000);
        if (!ready) {
            return res.status(400).json({
                error: `WhatsApp not ready (status: ${state.status}). Re-link under WhatsApp Accounts.`
            });
        }
    }

    const digits = String(req.body?.phone || '').replace(/\D/g, '');
    if (!digits) {
        return res.status(400).json({ error: 'phone required' });
    }

    try {
        const registered = await state.client.isRegisteredUser(`${digits}@c.us`);
        res.json({ registered: Boolean(registered), phone: digits });
    } catch (err) {
        res.status(500).json({ error: err.message || 'Validation failed', registered: false });
    }
});

app.post('/clients/:clientId/send', async (req, res) => {
    const clientId = req.params.clientId;
    let state = getState(clientId);
    if (!state || !state.client) {
        await beginClientSession(clientId);
        state = getState(clientId);
    }
    if (!state || !state.client) {
        return res.status(404).json({ error: 'WhatsApp session not loaded. Restart bridge or re-link account.' });
    }

    if (state.status === 'error' && String(state.error || '').toLowerCase().includes('browser is already running')) {
        await destroyClientState(clientId, state);
        await beginClientSession(clientId);
        state = getState(clientId);
    }

    if (state.status !== 'connected') {
        const ready = await waitUntilConnected(state, 90000);
        if (!ready) {
            return res.status(400).json({
                error: `WhatsApp not ready (status: ${state.status}). Re-link under WhatsApp Accounts.`
            });
        }
    }

    const { phone, message } = req.body;
    const attachments = normalizeAttachments(req.body);

    if (!phone) {
        return res.status(400).json({ error: 'phone required' });
    }
    if (!message && attachments.length === 0) {
        return res.status(400).json({ error: 'message or attachment required' });
    }

    const digits = String(phone).replace(/\D/g, '');
    if (!digits) {
        return res.status(400).json({ error: 'Invalid phone number' });
    }

    const chatId = `${digits}@c.us`;

    try {
        let sentParts = 0;

        if (message && String(message).trim()) {
            await simulateTypingDelay(message);
            await state.client.sendMessage(chatId, message);
            sentParts++;
            if (attachments.length) await delay(1200);
        }

        for (const attachment of attachments) {
            const filePath = attachment.path;
            if (!fs.existsSync(filePath)) {
                return res.status(400).json({ error: `Attachment not found: ${path.basename(filePath)}` });
            }
            const media = loadMedia(filePath, attachment.filename);
            if (media) {
                await state.client.sendMessage(chatId, media);
                sentParts++;
                await delay(1500);
            }
        }

        res.json({ success: true, parts_sent: sentParts, attachments_sent: attachments.length });
    } catch (err) {
        res.status(500).json({ error: err.message || 'Send failed' });
    }
});

const server = app.listen(PORT, '0.0.0.0', async () => {
    console.log(`WhatsApp bridge running on http://localhost:${PORT}`);
    console.log(`[bridge] auth data: ${AUTH_DATA_DIR}`);
    try {
        await ensureWebVersionCache();
    } catch (err) {
        console.error('[bridge] WhatsApp Web cache check failed:', err.message);
    }
    killAllOrphanedBridgeBrowsers();
    cleanupAllSessionLocks();
    await delay(2000);
    for (const clientId of listSavedSessionIds()) {
        console.log(`[startup] restoring WhatsApp session ${clientId}`);
        beginClientSession(clientId).catch((err) => {
            console.error(`[${clientId}] restore failed:`, err.message);
        });
    }
});

server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
        console.error(`\nPort ${PORT} is already in use.`);
        console.error('The WhatsApp bridge is probably already running.');
        console.error(`Open http://localhost:${PORT}/health to check.`);
        console.error('Do NOT run "npm start" again if run.bat already started it.\n');
        process.exit(1);
    }
    throw err;
});
