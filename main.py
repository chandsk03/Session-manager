#!/usr/bin/env python3
import os
import asyncio
import getpass
import random
import glob
import platform
import signal
import sys
import sqlite3
import json
import shutil
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from cryptography.fernet import Fernet
from hashlib import sha256
import base64
import logging
from importlib.metadata import version

# Ensure logs directory exists
if not os.path.exists('logs'):
    os.makedirs('logs')

# Telegram Imports
from telethon import TelegramClient, functions, types, __version__ as telethon_version
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest,
    GetAccountTTLRequest,
    UpdatePasswordSettingsRequest
)
from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberInvalidError,
    AuthKeyError,
    RPCError
)

# UI Imports
from colorama import Fore, Style, init
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeRemainingColumn
)
from rich.tree import Tree
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich import box
from rich.markdown import Markdown
from rich.columns import Columns

# Initialize
init(autoreset=True)
console = Console()

# Configure logging with rotation
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]',
    handlers=[
        RotatingFileHandler('logs/session_manager.log', maxBytes=10485760, backupCount=5),
        logging.StreamHandler()
    ]
)

# Custom Theme Configuration
class Theme:
    PRIMARY = Fore.CYAN
    SUCCESS = Fore.GREEN
    ERROR = Fore.RED
    WARNING = Fore.YELLOW
    INFO = Fore.BLUE
    MENU = Fore.MAGENTA
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT
    HIGHLIGHT = Style.BRIGHT + Fore.YELLOW

class SecureConfig:
    """Secure configuration handler with encryption"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SecureConfig, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.API_ID = self._get_env_int("TELEGRAM_API_ID", "23077946")
        self.API_HASH = self._get_env("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")
        self.SESSION_FOLDER = "sessions"
        self.ENCRYPTION_KEY = self._get_encryption_key()
        self.MAX_RETRIES = 5
        self.RETRY_DELAY = 2
        self.DB_PATH = "sessions.db"
        self.LOG_LEVEL = self._get_env("LOG_LEVEL", "INFO")
        self.TELETHON_VERSION = version('telethon')
        self._setup_folders()
        self._migrate_database()
        
    def _get_env(self, var: str, default: str) -> str:
        value = os.getenv(var, default)
        return value.strip() if value else default
    
    def _get_env_int(self, var: str, default: str) -> int:
        try:
            return int(self._get_env(var, default))
        except ValueError:
            console.print(f"[bold red]✗ Invalid value for {var}, using default[/bold red]")
            return int(default)
    
    def _get_encryption_key(self) -> Optional[bytes]:
        key = os.getenv("SESSION_ENCRYPTION_KEY")
        if not key:
            key = base64.urlsafe_b64encode(os.urandom(32)).decode()
            console.print(f"[bold yellow]⚠ Generated new encryption key: {key}[/bold yellow]")
            console.print(f"[bold yellow]⚐ Set this as SESSION_ENCRYPTION_KEY in your environment[/bold yellow]")
        try:
            return base64.urlsafe_b64decode(key.encode())
        except:
            console.print(f"[bold yellow]⚠ Invalid encryption key format - sessions won't be encrypted[/bold yellow]")
            return None
    
    def _setup_folders(self):
        os.makedirs(self.SESSION_FOLDER, exist_ok=True)
    
    def _migrate_database(self):
        """Migrate database schema if needed"""
        if not os.path.exists(self.DB_PATH):
            self._create_database()
            return
            
        # Backup existing database
        backup_path = f"sessions_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2(self.DB_PATH, backup_path)
        console.print(f"[bold blue]ℹ Created database backup: {backup_path}[/bold blue]")
        logging.info(f"Created database backup: {backup_path}")
        
        with sqlite3.connect(self.DB_PATH) as conn:
            cursor = conn.cursor()
            # Check if metadata column exists
            cursor.execute("PRAGMA table_info(sessions)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'metadata' not in columns:
                console.print("[bold yellow]⚠ Migrating database to add metadata column[/bold yellow]")
                cursor.execute("ALTER TABLE sessions ADD COLUMN metadata TEXT")
                conn.commit()
                logging.info("Database migrated: added metadata column")
    
    def _create_database(self):
        with sqlite3.connect(self.DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    phone TEXT PRIMARY KEY,
                    path TEXT,
                    created_at TIMESTAMP,
                    last_used TIMESTAMP,
                    encrypted INTEGER DEFAULT 0,
                    metadata TEXT
                )
            """)
            logging.info("Created new sessions database")

config = SecureConfig()

class SessionSecurity:
    """Handles session encryption/decryption and database management"""
    
    @staticmethod
    def encrypt_session(session_path: str, phone: str) -> None:
        if not config.ENCRYPTION_KEY:
            return
            
        try:
            cipher = Fernet(base64.urlsafe_b64encode(config.ENCRYPTION_KEY))
            with open(session_path, 'rb') as f:
                data = f.read()
            
            encrypted_path = f"{session_path}.enc"
            encrypted = cipher.encrypt(data)
            with open(encrypted_path, 'wb') as f:
                f.write(encrypted)
            
            os.remove(session_path)
            with sqlite3.connect(config.DB_PATH) as conn:
                conn.execute(
                    "UPDATE sessions SET encrypted = 1, path = ? WHERE phone = ?",
                    (encrypted_path, phone)
                )
            console.print("[bold green]✓ Session encrypted successfully[/bold green]")
            logging.info(f"Session encrypted: {session_path}")
        except Exception as e:
            console.print(f"[bold red]✗ Encryption failed: {str(e)}[/bold red]")
            logging.error(f"Encryption failed for {session_path}: {str(e)}")
    
    @staticmethod
    def decrypt_session(session_path: str, phone: str) -> Optional[str]:
        if not session_path.endswith('.enc') or not config.ENCRYPTION_KEY:
            return session_path
            
        try:
            cipher = Fernet(base64.urlsafe_b64encode(config.ENCRYPTION_KEY))
            with open(session_path, 'rb') as f:
                encrypted = f.read()
            
            decrypted = cipher.decrypt(encrypted)
            clean_path = session_path[:-4]
            with open(clean_path, 'wb') as f:
                f.write(decrypted)
            
            with sqlite3.connect(config.DB_PATH) as conn:
                conn.execute(
                    "UPDATE sessions SET encrypted = 0, path = ? WHERE phone = ?",
                    (clean_path, phone)
                )
            logging.info(f"Session decrypted: {session_path}")
            return clean_path
        except Exception as e:
            console.print(f"[bold red]✗ Decryption failed: {str(e)}[/bold red]")
            logging.error(f"Decryption failed for {session_path}: {str(e)}")
            return None

class AdvancedTelegramClient:
    """Enhanced Telegram client with advanced features"""
    
    def __init__(self, session_path: str, phone: str):
        self.session_path = session_path
        self.phone = phone
        self.client = None
        self._me = None
        
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        
    async def connect(self) -> bool:
        """Connect with version-aware parameters"""
        decrypted_path = SessionSecurity.decrypt_session(self.session_path, self.phone)
        if not decrypted_path:
            return False
            
        client_params = {
            'session': decrypted_path,
            'api_id': config.API_ID,
            'api_hash': config.API_HASH,
            'device_model': "Telegram Session Manager Pro",
            'system_version': platform.platform(),
            'app_version': "5.0",
            'lang_code': "en",
            'system_lang_code': "en-US"
        }
        
        # Only add supported parameters based on telethon version
        if config.TELETHON_VERSION >= '1.24.0':
            client_params.update({
                'request_retries': 5,
                'connection_retries': 5,
                'auto_reconnect': True
            })
        
        self.client = TelegramClient(**client_params)
        
        try:
            await self.client.connect()
            if not await self.client.is_user_authorized():
                console.print("[bold red]✗ Session not authorized[/bold red]")
                return False
            self._me = await self.client.get_me()
            console.print(f"[bold green]✓ Connected as {self._me.first_name} (ID: {self._me.id})[/bold green]")
            with sqlite3.connect(config.DB_PATH) as conn:
                conn.execute(
                    "UPDATE sessions SET last_used = ? WHERE phone = ?",
                    (datetime.now(), self.phone)
                )
            return True
        except Exception as e:
            console.print(f"[bold red]✗ Connection failed: {str(e)}[/bold red]")
            logging.error(f"Connection failed for {self.session_path}: {str(e)}")
            return False
    
    async def disconnect(self):
        """Disconnect cleanly"""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            console.print("[bold blue]ℹ Client disconnected[/bold blue]")
            logging.info(f"Client disconnected: {self.session_path}")
        self.client = None
    
    async def safe_execute(self, func, *args, **kwargs):
        """Execute with retry logic and error handling"""
        for attempt in range(config.MAX_RETRIES):
            try:
                return await func(*args, **kwargs)
            except FloodWaitError as e:
                wait = min(e.seconds, 3600)
                console.print(f"[bold yellow]⚠ Flood wait {wait}s (Attempt {attempt+1}/{config.MAX_RETRIES})[/bold yellow]")
                await asyncio.sleep(wait)
            except Exception as e:
                console.print(f"[bold red]✗ Attempt {attempt+1} failed: {str(e)}[/bold red]")
                if attempt == config.MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
        return None

def print_header(title: str) -> None:
    console.print(Panel.fit(
        title,
        style="bold cyan",
        border_style="blue",
        subtitle=f"Telegram Session Manager v5.0 (Telethon {telethon_version})"
    ))

def print_success(message: str) -> None:
    console.print(f"[bold green]✓ {message}[/bold green]")

def print_error(message: str) -> None:
    console.print(f"[bold red]✗ {message}[/bold red]")

def print_warning(message: str) -> None:
    console.print(f"[bold yellow]⚠ {message}[/bold yellow]")

def print_info(message: str) -> None:
    console.print(f"[bold blue]ℹ {message}[/bold blue]")

def validate_phone(phone: str) -> bool:
    if not phone.startswith('+'):
        return False
    digits = phone[1:].replace(' ', '')
    return digits.isdigit() and 7 <= len(digits) <= 15

async def create_session() -> Optional[str]:
    """Create a new Telegram session with enhanced security"""
    print_header("Create New Session")
    
    while True:
        phone = console.input("[bold cyan]Enter phone number (e.g., +12345678900): [/bold cyan]").strip()
        if not validate_phone(phone):
            print_error("Invalid phone number format")
            continue
        break
    
    session_path = os.path.join(config.SESSION_FOLDER, f"{phone[1:]}.session")
    if os.path.exists(session_path) or os.path.exists(session_path + '.enc'):
        print_warning(f"Session already exists for {phone}")
        return session_path
    
    async with AdvancedTelegramClient(session_path, phone) as client:
        if not await client.connect():
            async with TelegramClient(session_path, config.API_ID, config.API_HASH) as temp_client:
                try:
                    print_info(f"Sending code to {phone}...")
                    await temp_client.send_code_request(phone)
                    code = console.input("[bold yellow]Enter code (or 'q' to quit): [/bold yellow]").strip()
                    if code.lower() == 'q':
                        if os.path.exists(session_path):
                            os.remove(session_path)
                        return None
                    try:
                        await temp_client.sign_in(phone, code)
                    except SessionPasswordNeededError:
                        password = getpass.getpass("[bold red]Enter 2FA password: [/bold red]")
                        await temp_client.sign_in(password=password)
                    
                    me = await temp_client.get_me()
                    metadata = {
                        "username": me.username,
                        "first_name": me.first_name,
                        "last_name": me.last_name,
                        "premium": me.premium
                    }
                    with sqlite3.connect(config.DB_PATH) as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO sessions (phone, path, created_at, last_used, metadata) VALUES (?, ?, ?, ?, ?)",
                            (phone, session_path, datetime.now(), datetime.now(), json.dumps(metadata))
                        )
                    SessionSecurity.encrypt_session(session_path, phone)
                    print_success(f"Session created for {me.first_name} {me.last_name or ''} ({me.phone})")
                    logging.info(f"New session created for {phone}")
                    return session_path
                except Exception as e:
                    print_error(f"Failed to create session: {str(e)}")
                    if os.path.exists(session_path):
                        os.remove(session_path)
                    return None
        return session_path

async def list_sessions(country_code: Optional[str] = None) -> Optional[List[str]]:
    """List sessions with detailed info"""
    sessions = glob.glob(os.path.join(config.SESSION_FOLDER, "*.session*"))
    
    if country_code:
        country_code = country_code.replace('+', '')
        sessions = [s for s in sessions if os.path.basename(s).startswith(country_code)]
    
    if not sessions:
        print_warning("No saved sessions found")
        return None
    
    table = Table(
        title="Available Sessions",
        box=box.ROUNDED,
        header_style="bold magenta",
        row_styles=["dim", ""]
    )
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Phone", style="magenta")
    table.add_column("File", style="yellow")
    table.add_column("Status", style="green")
    table.add_column("Last Used", style="blue")
    table.add_column("Username", style="cyan")
    
    with sqlite3.connect(config.DB_PATH) as conn:
        cursor = conn.cursor()
        for i, session in enumerate(sorted(sessions, key=os.path.getmtime, reverse=True), 1):
            session_name = os.path.basename(session).replace('.session', '').replace('.enc', '')
            status = "🔒 Encrypted" if session.endswith('.enc') else "🔓 Normal"
            cursor.execute("SELECT last_used, metadata FROM sessions WHERE phone = ?", (f"+{session_name}",))
            result = cursor.fetchone()
            last_used_str = result[0] if result and result[0] else "Never"
            metadata = json.loads(result[1]) if result and result[1] else {}
            username = metadata.get("username", "N/A")
            table.add_row(
                str(i),
                f"+{session_name}",
                os.path.basename(session),
                status,
                last_used_str,
                username
            )
    
    console.print(table)
    return sessions

async def select_and_login() -> Optional[AdvancedTelegramClient]:
    """Select a session and login"""
    sessions = await list_sessions()
    if not sessions:
        return None
    
    while True:
        try:
            choice = console.input(f"[bold cyan]Select session (1-{len(sessions)} or 'q' to quit): [/bold cyan]").strip()
            if choice.lower() == 'q':
                return None
            choice = int(choice) - 1
            if 0 <= choice < len(sessions):
                phone = f"+{os.path.basename(sessions[choice]).replace('.session', '').replace('.enc', '')}"
                client = AdvancedTelegramClient(sessions[choice], phone)
                if await client.connect():
                    return client
            print_error(f"Invalid selection. Choose between 1 and {len(sessions)}")
        except ValueError:
            print_error("Please enter a valid number or 'q'")

async def terminate_other_sessions() -> None:
    """Terminate all sessions except current one"""
    print_header("Terminate Other Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        auths = await client.safe_execute(client.client, GetAuthorizationsRequest)
        other_sessions = [a for a in auths.authorizations if not a.current]
        
        if not other_sessions:
            print_info("No other active sessions found")
            return
            
        table = Table(title="Other Sessions", box=box.ROUNDED, header_style="bold red")
        table.add_column("Device", style="cyan")
        table.add_column("IP", style="magenta")
        table.add_column("Last Active", style="yellow")
        for auth in other_sessions:
            table.add_row(
                auth.device_model,
                auth.ip,
                auth.date_active.strftime('%Y-%m-%d %H:%M:%S')
            )
        console.print(table)
        
        confirm = console.input("[bold red]Confirm termination of ALL other sessions? (y/n): [/bold red]").lower()
        if confirm != 'y':
            return
            
        with Progress() as progress:
            task = progress.add_task("[red]Terminating sessions...[/red]", total=len(other_sessions))
            for auth in other_sessions:
                await client.safe_execute(client.client, ResetAuthorizationRequest, hash=auth.hash)
                progress.update(task, advance=1)
                await asyncio.sleep(0.5)
        
        print_success(f"Terminated {len(other_sessions)} other sessions")
        logging.info(f"Terminated {len(other_sessions)} other sessions for {client.phone}")
    except Exception as e:
        print_error(f"Failed to terminate sessions: {str(e)}")
        logging.error(f"Failed to terminate sessions: {str(e)}")

async def show_active_sessions() -> None:
    """Show detailed info about active sessions"""
    print_header("Active Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        auths = await client.safe_execute(client.client, GetAuthorizationsRequest)
        table = Table(
            title=f"Active Sessions ({len(auths.authorizations)})",
            box=box.ROUNDED,
            header_style="bold cyan"
        )
        table.add_column("Status", style="bold")
        table.add_column("Device", style="cyan")
        table.add_column("IP/Location", style="magenta")
        table.add_column("Last Active", style="green")
        
        for auth in auths.authorizations:
            status = "[green]Current[/green]" if auth.current else "[red]Other[/red]"
            table.add_row(
                status,
                auth.device_model,
                f"{auth.ip}\n[dim]{auth.country}[/dim]",
                auth.date_active.strftime('%Y-%m-%d %H:%M:%S')
            )
        console.print(table)
    except Exception as e:
        print_error(f"Error fetching sessions: {str(e)}")
        logging.error(f"Error fetching sessions: {str(e)}")

async def update_profile_random_name() -> None:
    """Update profile with random name"""
    print_header("Update Profile")
    client = await select_and_login()
    if not client:
        return
    
    try:
        me = await client.client.get_me()
        old_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        adjectives = ["Cool", "Swift", "Silent", "Mystic"]
        nouns = ["Ninja", "Wizard", "Ghost", "Phoenix"]
        new_name = f"{random.choice(adjectives)}{random.choice(nouns)}_{random.randint(100, 999)}"
        await client.safe_execute(
            client.client,
            UpdateProfileRequest,
            first_name=new_name,
            about=f"Updated by Session Manager on {datetime.now().strftime('%Y-%m-%d')}"
        )
        print_success(f"Profile updated from '{old_name}' to '{new_name}'")
        logging.info(f"Profile updated for {client.phone}: {new_name}")
    except Exception as e:
        print_error(f"Failed to update profile: {str(e)}")
        logging.error(f"Failed to update profile: {str(e)}")

async def clear_contacts() -> None:
    """Clear all contacts with confirmation"""
    print_header("Clear Contacts")
    client = await select_and_login()
    if not client:
        return
    
    try:
        contacts = await client.safe_execute(client.client, GetContactsRequest, hash=0)
        if not contacts.contacts:
            print_info("No contacts to clear")
            return
            
        table = Table(title=f"Found {len(contacts.contacts)} Contacts", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Phone", style="green")
        for contact in contacts.contacts[:5]:
            user = next((u for u in contacts.users if u.id == contact.user_id), None)
            if user:
                table.add_row(
                    f"{user.first_name or ''} {user.last_name or ''}".strip(),
                    user.phone or "N/A"
                )
        console.print(table)
        if len(contacts.contacts) > 5:
            print_info(f"...and {len(contacts.contacts)-5} more contacts")
        
        confirm = console.input("[bold red]Delete ALL contacts? (y/n): [/bold red]").lower()
        if confirm != 'y':
            return
            
        with Progress() as progress:
            task = progress.add_task("[red]Deleting contacts...[/red]", total=len(contacts.contacts))
            batch_size = 100
            for i in range(0, len(contacts.contacts), batch_size):
                batch = contacts.contacts[i:i + batch_size]
                await client.safe_execute(
                    client.client,
                    DeleteContactsRequest,
                    id=[c.user_id for c in batch]
                )
                progress.update(task, advance=len(batch))
                await asyncio.sleep(1)
        
        print_success(f"Deleted {len(contacts.contacts)} contacts")
        logging.info(f"Deleted {len(contacts.contacts)} contacts for {client.phone}")
    except Exception as e:
        print_error(f"Failed to clear contacts: {str(e)}")
        logging.error(f"Failed to clear contacts: {str(e)}")

async def delete_all_chats_advanced() -> None:
    """Advanced chat deletion"""
    print_header("Advanced Chat Deletion")
    client = await select_and_login()
    if not client:
        return
    
    try:
        dialogs = await client.safe_execute(
            client.client,
            GetDialogsRequest,
            offset_date=None,
            offset_id=0,
            offset_peer=types.InputPeerEmpty(),
            limit=200,
            hash=0
        )
        
        if not dialogs.dialogs:
            print_info("No chats/channels found")
            return
            
        table = Table(title=f"Found {len(dialogs.dialogs)} Chats/Channels", box=box.ROUNDED)
        table.add_column("Type", style="cyan")
        table.add_column("Title", style="magenta")
        for dialog in dialogs.dialogs[:5]:
            entity = dialog.entity
            chat_type = "Channel" if isinstance(entity, types.Channel) else "Chat"
            table.add_row(chat_type, getattr(entity, 'title', 'Unknown'))
        console.print(table)
        if len(dialogs.dialogs) > 5:
            print_info(f"...and {len(dialogs.dialogs)-5} more items")
        
        confirm = console.input("[bold red]Delete ALL chats/channels? (y/n): [/bold red]").lower()
        if confirm != 'y':
            return
            
        with Progress() as progress:
            task = progress.add_task("[red]Deleting chats...[/red]", total=len(dialogs.dialogs))
            for dialog in dialogs.dialogs:
                if isinstance(dialog.entity, types.Channel):
                    await client.safe_execute(client.client, LeaveChannelRequest, channel=dialog.entity)
                else:
                    await client.client.delete_dialog(dialog.entity)
                progress.update(task, advance=1)
                await asyncio.sleep(1)
        
        print_success(f"Deleted {len(dialogs.dialogs)} chats/channels")
        logging.info(f"Deleted {len(dialogs.dialogs)} chats/channels for {client.phone}")
    except Exception as e:
        print_error(f"Error: {str(e)}")
        logging.error(f"Error deleting chats: {str(e)}")

async def check_spam_status() -> None:
    """Check if account is spam-restricted"""
    print_header("Check Spam Status")
    client = await select_and_login()
    if not client:
        return
    
    try:
        ttl = await client.safe_execute(client.client, GetAccountTTLRequest)
        test_msg = await client.safe_execute(client.client.send_message, "me", "Spam check")
        if test_msg:
            await client.safe_execute(client.client.delete_messages, "me", [test_msg.id])
            status = "[green]UNRESTRICTED[/green]"
        else:
            status = "[red]RESTRICTED[/red]"
        
        table = Table(box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")
        table.add_row("Account TTL", f"{ttl.days} days")
        table.add_row("Spam Status", status)
        console.print(table)
    except Exception as e:
        print_warning(f"Possible spam restriction: {str(e)}")
        logging.warning(f"Spam check failed: {str(e)}")

async def read_session_otp() -> None:
    """Read latest OTP from Telegram messages"""
    print_header("Read OTP")
    client = await select_and_login()
    if not client:
        return
    
    try:
        messages = await client.safe_execute(client.client.get_messages, "Telegram", limit=10)
        for msg in messages:
            if "login code" in msg.text.lower():
                code = ''.join(filter(str.isdigit, msg.text))
                console.print(Panel(
                    f"OTP: [bold green]{code}[/bold green]\nReceived: {msg.date}",
                    title="Latest OTP",
                    border_style="green"
                ))
                logging.info(f"OTP read: {code} for {client.phone}")
                return
        print_info("No recent OTP found")
    except Exception as e:
        print_error(f"Failed to read OTP: {str(e)}")
        logging.error(f"Failed to read OTP: {str(e)}")

async def get_random_session_by_country() -> None:
    """Get random session by country code"""
    print_header("Random Session by Country")
    country_code = console.input("[bold cyan]Enter country code (e.g., +91): [/bold cyan]").strip()
    if not country_code.startswith('+'):
        print_error("Country code must start with '+'")
        return
    
    sessions = await list_sessions(country_code)
    if not sessions:
        return
    session = random.choice(sessions)
    phone = f"+{os.path.basename(session).replace('.session', '').replace('.enc', '')}"
    client = AdvancedTelegramClient(session, phone)
    if await client.connect():
        print_success(f"Selected and logged into: {os.path.basename(session)}")
        logging.info(f"Random session selected and logged in: {session}")
    else:
        print_error("Failed to login to selected session")

async def manage_2fa() -> None:
    """2FA management menu"""
    print_header("2FA Management")
    client = await select_and_login()
    if not client:
        return
    
    async def enable_2fa():
        password = getpass.getpass("[bold]Enter new 2FA password (min 8 chars): [/bold]")
        if len(password) < 8:
            print_error("Password must be at least 8 characters")
            return
        hint = console.input("[bold]Enter password hint (optional): [/bold]")
        email = console.input("[bold]Enter recovery email (optional): [/bold]")
        
        try:
            await client.safe_execute(
                client.client,
                UpdatePasswordSettingsRequest,
                password=None,
                new_settings=types.account.PasswordInputSettings(
                    new_algo=types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
                        salt1=os.urandom(32),
                        salt2=os.urandom(32),
                        g=2,
                        p=bytes.fromhex('c5')
                    ),
                    hint=hint,
                    email=email if email else None
                )
            )
            print_success("2FA enabled successfully")
            logging.info(f"2FA enabled for {client.phone}")
        except Exception as e:
            print_error(f"Failed to enable 2FA: {str(e)}")
            logging.error(f"Failed to enable 2FA: {str(e)}")
    
    async def disable_2fa():
        confirm = console.input("[bold red]Are you sure? (y/n): [/bold red]").lower()
        if confirm != 'y':
            return
        password = getpass.getpass("[bold red]Enter current 2FA password: [/bold red]")
        try:
            await client.safe_execute(
                client.client,
                UpdatePasswordSettingsRequest,
                password=password,
                new_settings=types.account.PasswordInputSettings(
                    new_algo=None,
                    hint=None,
                    email=None
                )
            )
            print_success("2FA disabled successfully")
            logging.info(f"2FA disabled for {client.phone}")
        except Exception as e:
            print_error(f"Failed to disable 2FA: {str(e)}")
            logging.error(f"Failed to disable 2FA: {str(e)}")
    
    async def change_2fa_password():
        current = getpass.getpass("[bold]Enter current 2FA password: [/bold]")
        new_pass = getpass.getpass("[bold]Enter new 2FA password (min 8 chars): [/bold]")
        if len(new_pass) < 8:
            print_error("New password must be at least 8 characters")
            return
        hint = console.input("[bold]Enter new password hint (optional): [/bold]")
        
        try:
            await client.safe_execute(
                client.client,
                UpdatePasswordSettingsRequest,
                password=current,
                new_settings=types.account.PasswordInputSettings(
                    new_algo=types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
                        salt1=os.urandom(32),
                        salt2=os.urandom(32),
                        g=2,
                        p=bytes.fromhex('c5')
                    ),
                    hint=hint,
                    email=None
                )
            )
            print_success("2FA password changed successfully")
            logging.info(f"2FA password changed for {client.phone}")
        except Exception as e:
            print_error(f"Failed to change 2FA password: {str(e)}")
            logging.error(f"Failed to change 2FA password: {str(e)}")
    
    menu_options = {
        "1": ("Enable 2FA", enable_2fa),
        "2": ("Disable 2FA", disable_2fa),
        "3": ("Change 2FA Password", change_2fa_password),
        "4": ("Back", lambda: None)
    }
    
    while True:
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Option", style="cyan")
        table.add_column("Action", style="magenta")
        for num, (desc, _) in menu_options.items():
            table.add_row(num, desc)
        console.print(table)
        
        choice = console.input("[bold cyan]Select option: [/bold cyan]").strip()
        if choice in menu_options:
            if choice == "4":
                break
            await menu_options[choice][1]()
        else:
            print_error("Invalid choice")

async def export_sessions() -> None:
    """Export session data to CSV"""
    print_header("Export Sessions")
    sessions = await list_sessions()
    if not sessions:
        return
    
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, path, created_at, last_used, encrypted, metadata FROM sessions")
            data = cursor.fetchall()
        
        import csv
        with open('sessions_export.csv', 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Phone', 'Path', 'Created At', 'Last Used', 'Encrypted', 'Metadata'])
            writer.writerows([(row[0], row[1], row[2], row[3], row[4], json.dumps(json.loads(row[5]) if row[5] else {})) for row in data])
        
        print_success(f"Exported {len(data)} sessions to sessions_export.csv")
        logging.info(f"Exported {len(data)} sessions to CSV")
    except Exception as e:
        print_error(f"Failed to export sessions: {str(e)}")
        logging.error(f"Failed to export sessions: {str(e)}")

async def session_statistics() -> None:
    """Display session statistics"""
    print_header("Session Statistics")
    try:
        with sqlite3.connect(config.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sessions")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE encrypted = 1")
            encrypted = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE last_used IS NOT NULL")
            active = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE json_extract(metadata, '$.premium') = 1")
            premium = cursor.fetchone()[0]
        
        table = Table(title="Session Statistics", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")
        table.add_row("Total Sessions", str(total))
        table.add_row("Encrypted Sessions", str(encrypted))
        table.add_row("Active Sessions", str(active))
        table.add_row("Premium Accounts", str(premium))
        console.print(table)
    except Exception as e:
        print_error(f"Failed to get statistics: {str(e)}")
        logging.error(f"Failed to get statistics: {str(e)}")

async def backup_sessions() -> None:
    """Backup all session files"""
    print_header("Backup Sessions")
    sessions = await list_sessions()
    if not sessions:
        return
    
    backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)
    
    try:
        with Progress() as progress:
            task = progress.add_task("[green]Backing up sessions...[/green]", total=len(sessions))
            for session in sessions:
                dest = os.path.join(backup_dir, os.path.basename(session))
                with open(session, 'rb') as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                progress.update(task, advance=1)
        
        print_success(f"Backed up {len(sessions)} sessions to {backup_dir}")
        logging.info(f"Backed up {len(sessions)} sessions to {backup_dir}")
    except Exception as e:
        print_error(f"Failed to backup sessions: {str(e)}")
        logging.error(f"Failed to backup sessions: {str(e)}")

async def cleanup_sessions() -> None:
    """Cleanup unused session files"""
    print_header("Cleanup Sessions")
    sessions = glob.glob(os.path.join(config.SESSION_FOLDER, "*.session*"))
    
    if not sessions:
        print_info("No sessions to clean")
        return
    
    with sqlite3.connect(config.DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM sessions")
        db_paths = set(row[0] for row in cursor.fetchall())
    
    orphaned = [s for s in sessions if s not in db_paths]
    if not orphaned:
        print_info("No orphaned sessions found")
        return
    
    table = Table(title=f"Found {len(orphaned)} Orphaned Sessions", box=box.ROUNDED)
    table.add_column("File", style="yellow")
    for session in orphaned[:5]:
        table.add_row(os.path.basename(session))
    console.print(table)
    if len(orphaned) > 5:
        print_info(f"...and {len(orphaned)-5} more")
    
    confirm = console.input("[bold red]Delete all orphaned sessions? (y/n): [/bold red]").lower()
    if confirm != 'y':
        return
    
    with Progress() as progress:
        task = progress.add_task("[red]Cleaning up...[/red]", total=len(orphaned))
        for session in orphaned:
            os.remove(session)
            progress.update(task, advance=1)
    
    print_success(f"Cleaned up {len(orphaned)} orphaned sessions")
    logging.info(f"Cleaned up {len(orphaned)} orphaned sessions")

def signal_handler(sig, frame):
    """Handle interrupt signals"""
    console.print("\n[bold red]✗ Received shutdown signal[/bold red]")
    sys.exit(0)

async def main() -> None:
    """Main menu with all features"""
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", list_sessions),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Show Active Sessions", show_active_sessions),
        "5": ("Update Profile", update_profile_random_name),
        "6": ("Clear Contacts", clear_contacts),
        "7": ("Advanced Chat Deletion", delete_all_chats_advanced),
        "8": ("Check Spam Status", check_spam_status),
        "9": ("Read OTP", read_session_otp),
        "10": ("Random Session by Country", get_random_session_by_country),
        "11": ("2FA Management", manage_2fa),
        "12": ("Export Sessions", export_sessions),
        "13": ("Session Statistics", session_statistics),
        "14": ("Backup Sessions", backup_sessions),
        "15": ("Cleanup Sessions", cleanup_sessions),
        "16": ("Exit", None)
    }
    
    while True:
        print_header("Telegram Advanced Session Manager")
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Option", style="cyan", justify="right")
        table.add_column("Description", style="magenta")
        for num, (desc, _) in menu_options.items():
            table.add_row(num, desc)
        console.print(table)
        
        choice = console.input("[bold cyan]Select option (1-16): [/bold cyan]").strip()
        if choice in menu_options:
            if choice == "16":
                print_success("Goodbye!")
                sys.exit(0)
            elif choice in ("2", "13"):
                await menu_options[choice][1]()
            else:
                await menu_options[choice][1]()
        else:
            print_error("Invalid choice! Select 1-16")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[bold red]✗ Operation cancelled by user[/bold red]")
        sys.exit(0)
    except Exception as e:
        print_error(f"Fatal error: {str(e)}")
        logging.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)