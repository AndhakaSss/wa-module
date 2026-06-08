from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import sqlite3
import hashlib
import secrets
import re
from datetime import datetime, timedelta
import functools
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app)

# Database setup
def get_db():
    conn = sqlite3.connect('wa_business.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        company_name TEXT,
        phone TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Contacts table
    cursor.execute('''CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        name TEXT,
        vehicle TEXT,
        tags TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    
    # Templates table
    cursor.execute('''CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    
    # WhatsApp sessions table
    cursor.execute('''CREATE TABLE IF NOT EXISTS whatsapp_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        phone_number TEXT,
        status TEXT DEFAULT 'disconnected',
        messages_today INTEGER DEFAULT 0,
        daily_limit INTEGER DEFAULT 50,
        last_active TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    
    # Campaigns table
    cursor.execute('''CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        recipient_count INTEGER DEFAULT 0,
        sent_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

# HTML Template (embedded for simplicity)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WA Business Hub - Enterprise WhatsApp Platform</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.jsdelivr.net/npm/remixicon@4.2.0/fonts/remixicon.css" rel="stylesheet"/>
    <style>
        .sidebar-item:hover, .sidebar-item.active {
            background: linear-gradient(135deg, #075e54 0%, #128c7e 100%);
            color: white;
        }
        .card-hover:hover {
            transform: translateY(-2px);
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
        }
    </style>
</head>
<body class="bg-gray-50">

<!-- Login View -->
<div id="loginView" class="min-h-screen flex items-center justify-center bg-gradient-to-br from-emerald-900 to-teal-700">
    <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md">
        <div class="text-center mb-8">
            <div class="w-16 h-16 bg-gradient-to-br from-emerald-600 to-teal-500 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <i class="ri-whatsapp-line text-white text-3xl"></i>
            </div>
            <h2 class="text-2xl font-bold text-gray-800">WA Business Hub</h2>
            <p class="text-gray-500 text-sm mt-2">Enterprise WhatsApp Marketing Platform</p>
        </div>
        
        <form id="loginForm">
            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-medium mb-2">Email Address</label>
                <input type="email" id="loginEmail" class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500" placeholder="you@example.com" required>
            </div>
            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-medium mb-2">Password</label>
                <input type="password" id="loginPassword" class="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-emerald-500" placeholder="Enter your password" required>
            </div>
            <button type="submit" class="w-full bg-gradient-to-r from-emerald-600 to-teal-500 text-white py-2 rounded-lg font-semibold hover:opacity-90 transition">Sign In</button>
        </form>
        
        <div class="mt-6 text-center text-sm text-gray-500">
            Don't have an account? <a href="#" id="showSignupBtn" class="text-emerald-600 hover:underline">Sign up</a>
        </div>
        
        <div id="loginError" class="mt-4 text-center text-red-500 text-sm hidden"></div>
    </div>
</div>

<!-- Signup View -->
<div id="signupView" class="min-h-screen flex items-center justify-center bg-gradient-to-br from-emerald-900 to-teal-700 hidden">
    <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md">
        <div class="text-center mb-8">
            <div class="w-16 h-16 bg-gradient-to-br from-emerald-600 to-teal-500 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <i class="ri-whatsapp-line text-white text-3xl"></i>
            </div>
            <h2 class="text-2xl font-bold text-gray-800">Create Account</h2>
            <p class="text-gray-500 text-sm mt-2">Start your 14-day free trial</p>
        </div>
        
        <form id="signupForm">
            <div class="mb-3">
                <input type="text" id="signupCompany" placeholder="Company Name" class="w-full px-4 py-2 border border-gray-300 rounded-lg" required>
            </div>
            <div class="mb-3">
                <input type="email" id="signupEmail" placeholder="Email Address" class="w-full px-4 py-2 border border-gray-300 rounded-lg" required>
            </div>
            <div class="mb-3">
                <input type="tel" id="signupPhone" placeholder="Phone Number" class="w-full px-4 py-2 border border-gray-300 rounded-lg" required>
            </div>
            <div class="mb-4">
                <input type="password" id="signupPassword" placeholder="Password (min 6 characters)" class="w-full px-4 py-2 border border-gray-300 rounded-lg" required>
            </div>
            <button type="submit" class="w-full bg-gradient-to-r from-emerald-600 to-teal-500 text-white py-2 rounded-lg font-semibold">Create Account</button>
        </form>
        
        <div class="mt-6 text-center">
            <a href="#" id="showLoginBtn" class="text-sm text-emerald-600 hover:underline">Already have an account? Sign in</a>
        </div>
        
        <div id="signupError" class="mt-4 text-center text-red-500 text-sm hidden"></div>
        <div id="signupSuccess" class="mt-4 text-center text-green-500 text-sm hidden"></div>
    </div>
</div>

<!-- Dashboard View -->
<div id="dashboardView" class="hidden">
    <!-- Sidebar -->
    <div class="fixed left-0 top-0 h-full w-64 bg-white shadow-lg z-10">
        <div class="p-6 border-b">
            <div class="flex items-center gap-2">
                <i class="ri-whatsapp-line text-2xl text-emerald-600"></i>
                <span class="font-bold text-xl text-gray-800">WA Business Hub</span>
            </div>
        </div>
        
        <nav class="p-4">
            <div class="sidebar-item active flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition mb-1" data-page="dashboard">
                <i class="ri-dashboard-line text-lg"></i>
                <span>Dashboard</span>
            </div>
            <div class="sidebar-item flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition mb-1" data-page="campaigns">
                <i class="ri-mail-send-line text-lg"></i>
                <span>Campaigns</span>
            </div>
            <div class="sidebar-item flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition mb-1" data-page="contacts">
                <i class="ri-contacts-book-line text-lg"></i>
                <span>Contacts</span>
            </div>
            <div class="sidebar-item flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition mb-1" data-page="whatsapp">
                <i class="ri-whatsapp-line text-lg"></i>
                <span>WhatsApp Accounts</span>
            </div>
            <div class="sidebar-item flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition mb-1" data-page="templates">
                <i class="ri-file-copy-line text-lg"></i>
                <span>Templates</span>
            </div>
            <div class="sidebar-item flex items-center gap-3 px-4 py-3 rounded-lg cursor-pointer transition mb-1" data-page="settings">
                <i class="ri-settings-3-line text-lg"></i>
                <span>Settings</span>
            </div>
        </nav>
        
        <div class="absolute bottom-0 left-0 right-0 p-4 border-t">
            <div class="flex items-center gap-3">
                <div class="w-8 h-8 bg-gray-200 rounded-full flex items-center justify-center">
                    <i class="ri-user-line text-gray-600"></i>
                </div>
                <div>
                    <p class="text-sm font-medium" id="userName">Loading...</p>
                    <p class="text-xs text-gray-500" id="userEmail">loading@example.com</p>
                </div>
                <i class="ri-logout-box-line ml-auto text-gray-500 cursor-pointer hover:text-red-500" id="logoutBtn"></i>
            </div>
        </div>
    </div>

    <!-- Main Content -->
    <div class="ml-64 p-8">
        <!-- Dashboard Page -->
        <div id="page-dashboard" class="page-content">
            <h1 class="text-2xl font-bold text-gray-800 mb-6">Dashboard</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                <div class="bg-white rounded-xl p-6 shadow-sm card-hover">
                    <div class="flex items-center justify-between mb-3">
                        <span class="text-gray-500 text-sm">Total Contacts</span>
                        <i class="ri-contacts-book-line text-2xl text-emerald-500"></i>
                    </div>
                    <div class="text-3xl font-bold" id="statContacts">0</div>
                </div>
                <div class="bg-white rounded-xl p-6 shadow-sm card-hover">
                    <div class="flex items-center justify-between mb-3">
                        <span class="text-gray-500 text-sm">WhatsApp Accounts</span>
                        <i class="ri-whatsapp-line text-2xl text-emerald-500"></i>
                    </div>
                    <div class="text-3xl font-bold" id="statWhatsApp">0</div>
                </div>
                <div class="bg-white rounded-xl p-6 shadow-sm card-hover">
                    <div class="flex items-center justify-between mb-3">
                        <span class="text-gray-500 text-sm">Campaigns</span>
                        <i class="ri-mail-send-line text-2xl text-emerald-500"></i>
                    </div>
                    <div class="text-3xl font-bold" id="statCampaigns">0</div>
                </div>
                <div class="bg-white rounded-xl p-6 shadow-sm card-hover">
                    <div class="flex items-center justify-between mb-3">
                        <span class="text-gray-500 text-sm">Messages Sent</span>
                        <i class="ri-bar-chart-2-line text-2xl text-emerald-500"></i>
                    </div>
                    <div class="text-3xl font-bold" id="statMessages">0</div>
                </div>
            </div>
        </div>

        <!-- Campaigns Page -->
        <div id="page-campaigns" class="page-content hidden">
            <div class="flex justify-between items-center mb-6">
                <h1 class="text-2xl font-bold text-gray-800">Campaigns</h1>
                <button id="newCampaignBtn" class="bg-gradient-to-r from-emerald-600 to-teal-500 text-white px-4 py-2 rounded-lg flex items-center gap-2">
                    <i class="ri-add-line"></i> New Campaign
                </button>
            </div>
            <div class="bg-white rounded-xl shadow-sm overflow-hidden">
                <table class="w-full">
                    <thead class="bg-gray-50 border-b">
                        <tr class="text-left text-gray-600 text-sm">
                            <th class="px-6 py-3">Name</th>
                            <th class="px-6 py-3">Status</th>
                            <th class="px-6 py-3">Recipients</th>
                            <th class="px-6 py-3">Sent</th>
                            <th class="px-6 py-3">Created</th>
                            <th class="px-6 py-3"></th>
                        </tr>
                    </thead>
                    <tbody id="campaignsList"></tbody>
                </table>
            </div>
        </div>

        <!-- Contacts Page -->
        <div id="page-contacts" class="page-content hidden">
            <div class="flex justify-between items-center mb-6">
                <h1 class="text-2xl font-bold text-gray-800">Contacts</h1>
                <div class="flex gap-3">
                    <button id="importCsvBtn" class="border border-emerald-500 text-emerald-600 px-4 py-2 rounded-lg flex items-center gap-2">
                        <i class="ri-upload-line"></i> Import CSV
                    </button>
                    <button id="addContactBtn" class="bg-gradient-to-r from-emerald-600 to-teal-500 text-white px-4 py-2 rounded-lg flex items-center gap-2">
                        <i class="ri-add-line"></i> Add Contact
                    </button>
                </div>
            </div>
            <div class="bg-white rounded-xl shadow-sm overflow-hidden">
                <table class="w-full">
                    <thead class="bg-gray-50 border-b">
                        <tr class="text-left text-gray-600 text-sm">
                            <th class="px-6 py-3">Phone</th>
                            <th class="px-6 py-3">Name</th>
                            <th class="px-6 py-3">Vehicle</th>
                            <th class="px-6 py-3"></th>
                        </tr>
                    </thead>
                    <tbody id="contactsList"></tbody>
                </table>
            </div>
            <input type="file" id="csvFile" accept=".csv" class="hidden">
        </div>

        <!-- WhatsApp Accounts Page -->
        <div id="page-whatsapp" class="page-content hidden">
            <div class="flex justify-between items-center mb-6">
                <h1 class="text-2xl font-bold text-gray-800">WhatsApp Accounts</h1>
                <button id="addWhatsAppBtn" class="bg-gradient-to-r from-emerald-600 to-teal-500 text-white px-4 py-2 rounded-lg flex items-center gap-2">
                    <i class="ri-add-line"></i> Add Account
                </button>
            </div>
            <div id="whatsappList" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>

        <!-- Templates Page -->
        <div id="page-templates" class="page-content hidden">
            <div class="flex justify-between items-center mb-6">
                <h1 class="text-2xl font-bold text-gray-800">Templates</h1>
                <button id="newTemplateBtn" class="bg-gradient-to-r from-emerald-600 to-teal-500 text-white px-4 py-2 rounded-lg flex items-center gap-2">
                    <i class="ri-add-line"></i> New Template
                </button>
            </div>
            <div id="templatesList" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>

        <!-- Settings Page -->
        <div id="page-settings" class="page-content hidden">
            <h1 class="text-2xl font-bold text-gray-800 mb-6">Settings</h1>
            <div class="bg-white rounded-xl p-6 shadow-sm max-w-2xl">
                <div class="mb-6">
                    <label class="block text-gray-700 font-medium mb-2">Daily Limit per WhatsApp Account</label>
                    <input type="number" id="dailyLimitSetting" value="50" class="w-full px-4 py-2 border border-gray-300 rounded-lg">
                </div>
                <button id="saveSettingsBtn" class="bg-emerald-600 text-white px-6 py-2 rounded-lg">Save Settings</button>
            </div>
        </div>
    </div>
</div>

<!-- Campaign Modal -->
<div id="campaignModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
    <div class="bg-white rounded-2xl p-6 max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <h3 class="text-xl font-semibold mb-4">Create New Campaign</h3>
        <div class="mb-4">
            <label class="block text-gray-700 font-medium mb-2">Campaign Name</label>
            <input type="text" id="campaignName" class="w-full px-4 py-2 border border-gray-300 rounded-lg">
        </div>
        <div class="mb-4">
            <label class="block text-gray-700 font-medium mb-2">Message Template</label>
            <textarea id="campaignMessage" rows="4" class="w-full px-4 py-2 border border-gray-300 rounded-lg" placeholder="Hello {name}, your vehicle {vehicle} has..."></textarea>
            <div class="flex gap-2 mt-2">
                <button type="button" onclick="insertVar('{name}')" class="text-xs bg-gray-100 px-2 py-1 rounded">+ name</button>
                <button type="button" onclick="insertVar('{phone}')" class="text-xs bg-gray-100 px-2 py-1 rounded">+ phone</button>
                <button type="button" onclick="insertVar('{vehicle}')" class="text-xs bg-gray-100 px-2 py-1 rounded">+ vehicle</button>
                <button type="button" onclick="insertVar('{id}')" class="text-xs bg-gray-100 px-2 py-1 rounded">+ id</button>
            </div>
        </div>
        <div class="flex gap-3 mt-6">
            <button id="closeCampaignModal" class="flex-1 border border-gray-300 py-2 rounded-lg">Cancel</button>
            <button id="launchCampaignBtn" class="flex-1 bg-gradient-to-r from-emerald-600 to-teal-500 text-white py-2 rounded-lg">Launch Campaign</button>
        </div>
    </div>
</div>

<!-- Add Contact Modal -->
<div id="contactModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
    <div class="bg-white rounded-2xl p-6 max-w-md w-full">
        <h3 class="text-xl font-semibold mb-4">Add Contact</h3>
        <div class="mb-3">
            <input type="tel" id="contactPhone" placeholder="Phone Number" class="w-full px-4 py-2 border border-gray-300 rounded-lg">
        </div>
        <div class="mb-3">
            <input type="text" id="contactName" placeholder="Name" class="w-full px-4 py-2 border border-gray-300 rounded-lg">
        </div>
        <div class="mb-3">
            <input type="text" id="contactVehicle" placeholder="Vehicle Number" class="w-full px-4 py-2 border border-gray-300 rounded-lg">
        </div>
        <div class="flex gap-3 mt-6">
            <button id="closeContactModal" class="flex-1 border border-gray-300 py-2 rounded-lg">Cancel</button>
            <button id="saveContactBtn" class="flex-1 bg-emerald-600 text-white py-2 rounded-lg">Save</button>
        </div>
    </div>
</div>

<!-- Template Modal -->
<div id="templateModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 hidden">
    <div class="bg-white rounded-2xl p-6 max-w-md w-full">
        <h3 class="text-xl font-semibold mb-4">Save Template</h3>
        <div class="mb-3">
            <input type="text" id="templateName" placeholder="Template Name" class="w-full px-4 py-2 border border-gray-300 rounded-lg">
        </div>
        <div class="mb-3">
            <textarea id="templateContent" rows="4" placeholder="Template content with {name}, {vehicle}..." class="w-full px-4 py-2 border border-gray-300 rounded-lg"></textarea>
        </div>
        <div class="flex gap-3 mt-6">
            <button id="closeTemplateModal" class="flex-1 border border-gray-300 py-2 rounded-lg">Cancel</button>
            <button id="saveTemplateBtn" class="flex-1 bg-emerald-600 text-white py-2 rounded-lg">Save</button>
        </div>
    </div>
</div>

<script>
    // Helper functions
    function getCookie(name) {
        let value = "; " + document.cookie;
        let parts = value.split("; " + name + "=");
        if (parts.length == 2) return parts.pop().split(";").shift();
    }
    
    async function apiCall(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });
        return response.json();
    }
    
    // Check if logged in
    async function checkAuth() {
        const response = await fetch('/api/me');
        if (response.ok) {
            const user = await response.json();
            document.getElementById('userName').innerText = user.company_name || user.email.split('@')[0];
            document.getElementById('userEmail').innerText = user.email;
            document.getElementById('loginView').classList.add('hidden');
            document.getElementById('signupView').classList.add('hidden');
            document.getElementById('dashboardView').classList.remove('hidden');
            loadDashboard();
            loadContacts();
            loadTemplates();
            loadCampaigns();
            loadWhatsApp();
        }
    }
    
    async function loadDashboard() {
        const stats = await apiCall('/api/stats');
        document.getElementById('statContacts').innerText = stats.contacts || 0;
        document.getElementById('statWhatsApp').innerText = stats.whatsapp_sessions || 0;
        document.getElementById('statCampaigns').innerText = stats.campaigns || 0;
        document.getElementById('statMessages').innerText = stats.messages_sent || 0;
    }
    
    async function loadContacts() {
        const data = await apiCall('/api/contacts');
        const tbody = document.getElementById('contactsList');
        if (!data.contacts || data.contacts.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center py-8 text-gray-500">No contacts. Import CSV or add manually.</td></tr>';
            return;
        }
        tbody.innerHTML = data.contacts.map(c => `
            <tr class="border-b">
                <td class="px-6 py-3">${c.phone}</td>
                <td class="px-6 py-3">${c.name || '-'}</td>
                <td class="px-6 py-3">${c.vehicle || '-'}</td>
                <td class="px-6 py-3"><button onclick="deleteContact(${c.id})" class="text-red-500"><i class="ri-delete-bin-line"></i></button></td>
            </tr>
        `).join('');
    }
    
    async function loadTemplates() {
        const data = await apiCall('/api/templates');
        const container = document.getElementById('templatesList');
        if (!data.templates || data.templates.length === 0) {
            container.innerHTML = '<div class="text-center text-gray-500 py-8 col-span-3">No templates. Create your first template.</div>';
            return;
        }
        container.innerHTML = data.templates.map(t => `
            <div class="bg-white rounded-xl p-4 shadow-sm border">
                <h3 class="font-semibold mb-2">${escapeHtml(t.name)}</h3>
                <p class="text-sm text-gray-600">${escapeHtml(t.content.substring(0, 100))}${t.content.length > 100 ? '...' : ''}</p>
                <button onclick="deleteTemplate(${t.id})" class="text-sm text-red-500 mt-2">Delete</button>
            </div>
        `).join('');
    }
    
    async function loadCampaigns() {
        const data = await apiCall('/api/campaigns');
        const tbody = document.getElementById('campaignsList');
        if (!data.campaigns || data.campaigns.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-8 text-gray-500">No campaigns yet</td></tr>';
            return;
        }
        tbody.innerHTML = data.campaigns.map(c => `
            <tr class="border-b">
                <td class="px-6 py-3">${escapeHtml(c.name)}</td>
                <td class="px-6 py-3"><span class="px-2 py-1 rounded text-xs ${c.status === 'completed' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}">${c.status}</span></td>
                <td class="px-6 py-3">${c.recipient_count}</td>
                <td class="px-6 py-3">${c.sent_count}</td>
                <td class="px-6 py-3 text-sm text-gray-500">${new Date(c.created_at).toLocaleDateString()}</td>
                <td class="px-6 py-3"><button onclick="deleteCampaign(${c.id})" class="text-red-500"><i class="ri-delete-bin-line"></i></button></td>
            </tr>
        `).join('');
    }
    
    async function loadWhatsApp() {
        const data = await apiCall('/api/whatsapp');
        const container = document.getElementById('whatsappList');
        if (!data.sessions || data.sessions.length === 0) {
            container.innerHTML = '<div class="text-center text-gray-500 py-8 col-span-3">No WhatsApp accounts. Click "Add Account" to start.</div>';
            return;
        }
        container.innerHTML = data.sessions.map(s => `
            <div class="bg-white rounded-xl p-4 shadow-sm border">
                <div class="flex items-center justify-between mb-3">
                    <div class="flex items-center gap-2">
                        <div class="w-2 h-2 rounded-full ${s.status === 'connected' ? 'bg-green-500' : 'bg-gray-400'}"></div>
                        <span class="font-medium">${s.phone_number || 'Unknown'}</span>
                    </div>
                    <button onclick="deleteWhatsApp(${s.id})" class="text-red-500"><i class="ri-delete-bin-line"></i></button>
                </div>
                <div class="text-sm text-gray-600">Messages today: ${s.messages_today}/${s.daily_limit}</div>
                <div class="text-xs text-gray-400 mt-2">Last active: ${s.last_active ? new Date(s.last_active).toLocaleString() : 'Never'}</div>
            </div>
        `).join('');
    }
    
    // Delete functions
    window.deleteContact = async (id) => {
        await apiCall(`/api/contacts/${id}`, { method: 'DELETE' });
        loadContacts();
        loadDashboard();
    };
    
    window.deleteTemplate = async (id) => {
        await apiCall(`/api/templates/${id}`, { method: 'DELETE' });
        loadTemplates();
    };
    
    window.deleteCampaign = async (id) => {
        await apiCall(`/api/campaigns/${id}`, { method: 'DELETE' });
        loadCampaigns();
        loadDashboard();
    };
    
    window.deleteWhatsApp = async (id) => {
        await apiCall(`/api/whatsapp/${id}`, { method: 'DELETE' });
        loadWhatsApp();
        loadDashboard();
    };
    
    window.insertVar = (varText) => {
        const textarea = document.getElementById('campaignMessage');
        textarea.value += varText;
    };
    
    function escapeHtml(text) {
        if (!text) return '';
        return text.replace(/[&<>]/g, function(m) {
            if (m === '&') return '&amp;';
            if (m === '<') return '&lt;';
            if (m === '>') return '&gt;';
            return m;
        });
    }
    
    // Navigation
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.dataset.page;
            document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');
            document.querySelectorAll('.page-content').forEach(p => p.classList.add('hidden'));
            document.getElementById(`page-${page}`).classList.remove('hidden');
        });
    });
    
    // Login
    document.getElementById('loginForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = document.getElementById('loginEmail').value;
        const password = document.getElementById('loginPassword').value;
        
        const response = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        
        if (response.ok) {
            checkAuth();
        } else {
            const error = await response.json();
            document.getElementById('loginError').innerText = error.error || 'Login failed';
            document.getElementById('loginError').classList.remove('hidden');
        }
    });
    
    // Signup
    document.getElementById('signupForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const company = document.getElementById('signupCompany').value;
        const email = document.getElementById('signupEmail').value;
        const phone = document.getElementById('signupPhone').value;
        const password = document.getElementById('signupPassword').value;
        
        if (password.length < 6) {
            document.getElementById('signupError').innerText = 'Password must be at least 6 characters';
            document.getElementById('signupError').classList.remove('hidden');
            return;
        }
        
        const response = await fetch('/api/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ company_name: company, email, phone, password })
        });
        
        if (response.ok) {
            document.getElementById('signupError').classList.add('hidden');
            document.getElementById('signupSuccess').innerText = 'Account created! Please sign in.';
            document.getElementById('signupSuccess').classList.remove('hidden');
            setTimeout(() => {
                document.getElementById('signupView').classList.add('hidden');
                document.getElementById('loginView').classList.remove('hidden');
            }, 2000);
        } else {
            const error = await response.json();
            document.getElementById('signupError').innerText = error.error || 'Signup failed';
            document.getElementById('signupError').classList.remove('hidden');
        }
    });
    
    // Toggle between login/signup
    document.getElementById('showSignupBtn').addEventListener('click', () => {
        document.getElementById('loginView').classList.add('hidden');
        document.getElementById('signupView').classList.remove('hidden');
    });
    
    document.getElementById('showLoginBtn').addEventListener('click', () => {
        document.getElementById('signupView').classList.add('hidden');
        document.getElementById('loginView').classList.remove('hidden');
    });
    
    // Logout
    document.getElementById('logoutBtn').addEventListener('click', async () => {
        await fetch('/api/logout', { method: 'POST' });
        document.getElementById('dashboardView').classList.add('hidden');
        document.getElementById('loginView').classList.remove('hidden');
        document.getElementById('loginEmail').value = '';
        document.getElementById('loginPassword').value = '';
    });
    
    // New Campaign
    document.getElementById('newCampaignBtn').addEventListener('click', () => {
        document.getElementById('campaignName').value = '';
        document.getElementById('campaignMessage').value = '';
        document.getElementById('campaignModal').classList.remove('hidden');
    });
    
    document.getElementById('closeCampaignModal').addEventListener('click', () => {
        document.getElementById('campaignModal').classList.add('hidden');
    });
    
    document.getElementById('launchCampaignBtn').addEventListener('click', async () => {
        const name = document.getElementById('campaignName').value;
        const message = document.getElementById('campaignMessage').value;
        
        if (!name || !message) {
            alert('Please fill campaign name and message');
            return;
        }
        
        await apiCall('/api/campaigns', {
            method: 'POST',
            body: JSON.stringify({ name, message, recipient_count: 100 })
        });
        
        document.getElementById('campaignModal').classList.add('hidden');
        loadCampaigns();
        loadDashboard();
        alert('Campaign launched!');
    });
    
    // Add Contact
    document.getElementById('addContactBtn').addEventListener('click', () => {
        document.getElementById('contactPhone').value = '';
        document.getElementById('contactName').value = '';
        document.getElementById('contactVehicle').value = '';
        document.getElementById('contactModal').classList.remove('hidden');
    });
    
    document.getElementById('closeContactModal').addEventListener('click', () => {
        document.getElementById('contactModal').classList.add('hidden');
    });
    
    document.getElementById('saveContactBtn').addEventListener('click', async () => {
        const phone = document.getElementById('contactPhone').value;
        const name = document.getElementById('contactName').value;
        const vehicle = document.getElementById('contactVehicle').value;
        
        if (!phone) {
            alert('Phone number required');
            return;
        }
        
        await apiCall('/api/contacts', {
            method: 'POST',
            body: JSON.stringify({ phone, name, vehicle })
        });
        
        document.getElementById('contactModal').classList.add('hidden');
        loadContacts();
        loadDashboard();
    });
    
    // Import CSV
    document.getElementById('importCsvBtn').addEventListener('click', () => {
        document.getElementById('csvFile').click();
    });
    
    document.getElementById('csvFile').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        const text = await file.text();
        const lines = text.split('\n');
        const contacts = [];
        
        for (let i = 1; i < lines.length; i++) {
            const parts = lines[i].split(',');
            if (parts.length >= 1 && parts[0].trim()) {
                contacts.push({
                    phone: parts[0].trim(),
                    name: parts[1] ? parts[1].trim() : '',
                    vehicle: parts[2] ? parts[2].trim() : ''
                });
            }
        }
        
        await apiCall('/api/contacts/import', {
            method: 'POST',
            body: JSON.stringify({ contacts })
        });
        
        loadContacts();
        loadDashboard();
        alert(`Imported ${contacts.length} contacts!`);
        e.target.value = '';
    });
    
    // New Template
    document.getElementById('newTemplateBtn').addEventListener('click', () => {
        document.getElementById('templateName').value = '';
        document.getElementById('templateContent').value = '';
        document.getElementById('templateModal').classList.remove('hidden');
    });
    
    document.getElementById('closeTemplateModal').addEventListener('click', () => {
        document.getElementById('templateModal').classList.add('hidden');
    });
    
    document.getElementById('saveTemplateBtn').addEventListener('click', async () => {
        const name = document.getElementById('templateName').value;
        const content = document.getElementById('templateContent').value;
        
        if (!name || !content) {
            alert('Please fill template name and content');
            return;
        }
        
        await apiCall('/api/templates', {
            method: 'POST',
            body: JSON.stringify({ name, content })
        });
        
        document.getElementById('templateModal').classList.add('hidden');
        loadTemplates();
    });
    
    // Add WhatsApp
    document.getElementById('addWhatsAppBtn').addEventListener('click', async () => {
        const phone = prompt('Enter WhatsApp phone number (with country code):', '+91');
        if (phone) {
            await apiCall('/api/whatsapp', {
                method: 'POST',
                body: JSON.stringify({ phone_number: phone })
            });
            loadWhatsApp();
            loadDashboard();
        }
    });
    
    // Save Settings
    document.getElementById('saveSettingsBtn').addEventListener('click', async () => {
        const dailyLimit = document.getElementById('dailyLimitSetting').value;
        await apiCall('/api/settings', {
            method: 'POST',
            body: JSON.stringify({ daily_limit: dailyLimit })
        });
        alert('Settings saved!');
    });
    
    // Check auth on page load
    checkAuth();
</script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

# API Routes
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    company_name = data.get('company_name')
    phone = data.get('phone')
    
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if user exists
    cursor.execute('SELECT id FROM users WHERE email = ?', (email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'error': 'Email already exists'}), 409
    
    # Hash password
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    cursor.execute('INSERT INTO users (email, password, company_name, phone) VALUES (?, ?, ?, ?)',
                   (email, password_hash, company_name, phone))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'User created successfully'}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, email, company_name FROM users WHERE email = ? AND password = ?',
                   (email, hashlib.sha256(password.encode()).hexdigest()))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        session['user_id'] = user['id']
        session['email'] = user['email']
        return jsonify({'user': {'id': user['id'], 'email': user['email'], 'company_name': user['company_name']}}), 200
    else:
        return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out'}), 200

@app.route('/api/me')
def me():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, email, company_name FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    conn.close()
    
    return jsonify({'id': user['id'], 'email': user['email'], 'company_name': user['company_name']})

@app.route('/api/stats')
def stats():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM contacts WHERE user_id = ?', (session['user_id'],))
    contacts = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM whatsapp_sessions WHERE user_id = ?', (session['user_id'],))
    whatsapp_sessions = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM campaigns WHERE user_id = ?', (session['user_id'],))
    campaigns = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(sent_count) FROM campaigns WHERE user_id = ?', (session['user_id'],))
    messages_sent = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return jsonify({
        'contacts': contacts,
        'whatsapp_sessions': whatsapp_sessions,
        'campaigns': campaigns,
        'messages_sent': messages_sent
    })

@app.route('/api/contacts', methods=['GET'])
def get_contacts():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, phone, name, vehicle FROM contacts WHERE user_id = ? ORDER BY created_at DESC', (session['user_id'],))
    contacts = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'contacts': contacts})

@app.route('/api/contacts', methods=['POST'])
def add_contact():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    phone = data.get('phone')
    name = data.get('name')
    vehicle = data.get('vehicle')
    
    if not phone:
        return jsonify({'error': 'Phone required'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO contacts (user_id, phone, name, vehicle) VALUES (?, ?, ?, ?)',
                   (session['user_id'], phone, name, vehicle))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Contact added'}), 201

@app.route('/api/contacts/import', methods=['POST'])
def import_contacts():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    contacts = data.get('contacts', [])
    
    conn = get_db()
    cursor = conn.cursor()
    for contact in contacts:
        cursor.execute('INSERT INTO contacts (user_id, phone, name, vehicle) VALUES (?, ?, ?, ?)',
                       (session['user_id'], contact.get('phone'), contact.get('name'), contact.get('vehicle')))
    conn.commit()
    conn.close()
    
    return jsonify({'imported': len(contacts)})

@app.route('/api/contacts/<int:contact_id>', methods=['DELETE'])
def delete_contact(contact_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM contacts WHERE id = ? AND user_id = ?', (contact_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Deleted'})

@app.route('/api/templates', methods=['GET'])
def get_templates():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, content FROM templates WHERE user_id = ? ORDER BY created_at DESC', (session['user_id'],))
    templates = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'templates': templates})

@app.route('/api/templates', methods=['POST'])
def add_template():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    name = data.get('name')
    content = data.get('content')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO templates (user_id, name, content) VALUES (?, ?, ?)',
                   (session['user_id'], name, content))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Template added'}), 201

@app.route('/api/templates/<int:template_id>', methods=['DELETE'])
def delete_template(template_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM templates WHERE id = ? AND user_id = ?', (template_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Deleted'})

@app.route('/api/campaigns', methods=['GET'])
def get_campaigns():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, status, recipient_count, sent_count, created_at FROM campaigns WHERE user_id = ? ORDER BY created_at DESC', (session['user_id'],))
    campaigns = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'campaigns': campaigns})

@app.route('/api/campaigns', methods=['POST'])
def add_campaign():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    name = data.get('name')
    message = data.get('message')
    recipient_count = data.get('recipient_count', 0)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO campaigns (user_id, name, message, recipient_count, status) VALUES (?, ?, ?, ?, ?)',
                   (session['user_id'], name, message, recipient_count, 'running'))
    campaign_id = cursor.lastrowid
    conn.commit()
    
    # Simulate sending (update sent_count)
    cursor.execute('UPDATE campaigns SET sent_count = ?, status = ? WHERE id = ?', (recipient_count, 'completed', campaign_id))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Campaign created'}), 201

@app.route('/api/campaigns/<int:campaign_id>', methods=['DELETE'])
def delete_campaign(campaign_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM campaigns WHERE id = ? AND user_id = ?', (campaign_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Deleted'})

@app.route('/api/whatsapp', methods=['GET'])
def get_whatsapp():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT id, phone_number, status, messages_today, daily_limit, last_active FROM whatsapp_sessions WHERE user_id = ?', (session['user_id'],))
    sessions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({'sessions': sessions})

@app.route('/api/whatsapp', methods=['POST'])
def add_whatsapp():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    phone_number = data.get('phone_number')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO whatsapp_sessions (user_id, phone_number, status, last_active) VALUES (?, ?, ?, ?)',
                   (session['user_id'], phone_number, 'connected', datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'WhatsApp account added'}), 201

@app.route('/api/whatsapp/<int:session_id>', methods=['DELETE'])
def delete_whatsapp(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM whatsapp_sessions WHERE id = ? AND user_id = ?', (session_id, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Deleted'})

@app.route('/api/settings', methods=['POST'])
def update_settings():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    daily_limit = data.get('daily_limit')
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE whatsapp_sessions SET daily_limit = ? WHERE user_id = ?', (daily_limit, session['user_id']))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'Settings updated'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)