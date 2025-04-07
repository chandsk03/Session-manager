#!/usr/bin/env python3
import os
import asyncio
import getpass
import random
import platform
import signal
import sys
import sqlite3
import json
import aiofiles
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import logging
from logging.handlers import RotatingFileHandler
import threading
from importlib.metadata import version
from pathlib import Path
from contextlib import asynccontextmanager
from telethon import TelegramClient, functions, types, __version__ as telethon_version
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest,
    GetAccountTTLRequest,
    UpdatePasswordSettingsRequest,
    GetPasswordRequest
)
from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberInvalidError,
    RPCError
)
from telethon.sessions import StringSession
from colorama import Fore, Style, init
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress
from rich import box
from rich.status import Status
from rich.prompt import Prompt, Confirm

# Initialize
init(autoreset=True)
console = Console()
executor = ThreadPoolExecutor(max_workers=4)

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d] - Thread: %(threadName)s',
    handlers=[
        RotatingFileHandler('logs/session_manager.log', maxBytes=10*1024*1024, backupCount=10),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
for handler in logger.handlers:
    if isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler):
        handler.setLevel(logging.ERROR)

class Theme:
    PRIMARY = Fore.CYAN
    SUCCESS = Fore.GREEN
    ERROR = Fore.RED
    WARNING = Fore.YELLOW
    INFO = Fore.BLUE
    MENU = Fore.MAGENTA
    RESET = Style.RESET_ALL
    BOLD = Style.BRIGHT

class SecureConfig:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SecureConfig, cls).__new__(cls)
                cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.API_ID = self._get_env_int("TELEGRAM_API_ID", "23077946")
        self.API_HASH = self._get_env("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")
        self.SESSION_FOLDER = Path("sessions")
        self.MAX_RETRIES = self._get_env_int("MAX_RETRIES", "5")
        self.RETRY_DELAY = self._get_env_int("RETRY_DELAY", "5")
        self.DB_PATH = Path("sessions.db")
        self.TELETHON_VERSION = version('telethon')
        self.BATCH_SIZE = self._get_env_int("BATCH_SIZE", "100")
        self.CONCURRENT_CONNECTIONS = self._get_env_int("CONCURRENT_CONNECTIONS", "4")
        self._setup_folders()
        self._migrate_database()
        
    def _get_env(self, var: str, default: str) -> str:
        return os.getenv(var, default).strip() if os.getenv(var) else default
    
    def _get_env_int(self, var: str, default: str) -> int:
        try:
            return int(self._get_env(var, default))
        except ValueError:
            console.print(f"[bold red]✗ Invalid value for {var}, using default: {default}[/bold red]")
            logger.warning(f"Invalid value for {var}, using default: {default}")
            return int(default)
    
    def _setup_folders(self):
        self.SESSION_FOLDER.mkdir(exist_ok=True)
        (self.SESSION_FOLDER / "backups").mkdir(exist_ok=True)
        os.chmod(self.SESSION_FOLDER, 0o755)
        if self.DB_PATH.exists():
            os.chmod(self.DB_PATH, 0o664)
    
    def _migrate_database(self):
        if not self.DB_PATH.exists():
            self._create_database()
            os.chmod(self.DB_PATH, 0o664)
            return
            
        with sqlite3.connect(self.DB_PATH, timeout=20) as conn:
            conn.execute("PRAGMA busy_timeout = 20000")
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(sessions)")
            columns = {col[1]: col for col in cursor.fetchall()}
            
            migrations = [
                ("metadata", "TEXT", "ALTER TABLE sessions ADD COLUMN metadata TEXT"),
                ("session_hash", "TEXT", "ALTER TABLE sessions ADD COLUMN session_hash TEXT"),
                ("status", "TEXT", "ALTER TABLE sessions ADD COLUMN status TEXT DEFAULT 'active'")
            ]
            
            for col_name, col_type, sql in migrations:
                if col_name not in columns:
                    console.print(f"[bold yellow]⚠ Migrating database to add {col_name} column[/bold yellow]")
                    cursor.execute(sql)
                    conn.commit()
                    logger.info(f"Database migrated: added {col_name} column")

    def _create_database(self):
        with sqlite3.connect(self.DB_PATH, timeout=20) as conn:
            conn.execute("PRAGMA busy_timeout = 20000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    phone TEXT PRIMARY KEY,
                    path TEXT,
                    created_at TEXT,
                    last_used TEXT,
                    metadata TEXT,
                    session_hash TEXT,
                    status TEXT DEFAULT 'active'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phone ON sessions(phone)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON sessions(status)")
            logger.info("Created new sessions database with indexes")

config = SecureConfig()

@asynccontextmanager
async def db_connection():
    conn = sqlite3.connect(config.DB_PATH, timeout=20)
    conn.execute("PRAGMA busy_timeout = 20000")
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

class AdvancedTelegramClient:
    def __init__(self, session_path: str, phone: str):
        self.session_path = session_path
        self.phone = phone
        self.client = None
        self._me = None
        self._semaphore = asyncio.Semaphore(config.CONCURRENT_CONNECTIONS)
        
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        
    async def connect(self) -> bool:
        async with self._semaphore:
            client_params = {
                'session': StringSession(),
                'api_id': config.API_ID,
                'api_hash': config.API_HASH,
                'device_model': f"SessionManager-{platform.node()}",
                'system_version': platform.platform(),
                'app_version': "5.1",
                'lang_code': "en",
                'system_lang_code': "en-US",
                'connection_retries': config.MAX_RETRIES,
                'retry_delay': config.RETRY_DELAY,
                'auto_reconnect': True,
                'flood_sleep_threshold': 60
            }
            
            self.client = TelegramClient(**client_params)
            if os.path.exists(self.session_path):
                try:
                    os.chmod(self.session_path, 0o664)
                    async with aiofiles.open(self.session_path, 'r') as f:
                        session_string = await f.read()
                    self.client.session.load(session_string)
                except Exception as e:
                    console.print(f"[bold yellow]⚠ Failed to load session file: {str(e)}[/bold yellow]")
                    logger.warning(f"Failed to load session {self.session_path}: {str(e)}")
            
            for attempt in range(config.MAX_RETRIES):
                try:
                    with console.status("[bold cyan]Connecting to Telegram...", spinner="dots") as status:
                        await self.client.connect()
                        if not await self.client.is_user_authorized():
                            console.print("[bold red]✗ Session not authorized[/bold red]")
                            return False
                        self._me = await self.client.get_me()
                        console.print(f"[bold green]✓ Connected as {self._me.first_name} (ID: {self._me.id})[/bold green]")
                        async with db_connection() as conn:
                            conn.execute(
                                "UPDATE sessions SET last_used = ?, session_hash = ? WHERE phone = ?",
                                (datetime.now(timezone.utc).isoformat(), self._generate_session_hash(), self.phone)
                            )
                        logger.info(f"Connected to session: {self.phone}")
                        return True
                except (TimeoutError, ConnectionError) as e:
                    console.print(f"[bold yellow]⚠ Connection attempt {attempt+1}/{config.MAX_RETRIES} failed: {str(e)}[/bold yellow]")
                    logger.warning(f"Connection attempt {attempt+1} failed: {str(e)}")
                    if attempt < config.MAX_RETRIES - 1:
                        await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
                except Exception as e:
                    console.print(f"[bold red]✗ Connection failed: {str(e)}[/bold red]")
                    logger.error(f"Connection failed for {self.session_path}: {str(e)}", exc_info=True)
                    return False
            console.print(f"[bold red]✗ Failed to connect after {config.MAX_RETRIES} attempts[/bold red]")
            return False
    
    async def disconnect(self):
        if self.client and self.client.is_connected():
            try:
                session_string = self.client.session.save()
                async with aiofiles.open(self.session_path, 'w') as f:
                    await f.write(session_string)
                os.chmod(self.session_path, 0o664)
                await self.client.disconnect()
                console.print("[bold blue]ℹ Client disconnected[/bold blue]")
                logger.info(f"Client disconnected: {self.session_path}")
            except Exception as e:
                console.print(f"[bold yellow]⚠ Error during disconnect: {str(e)}[/bold yellow]")
                logger.error(f"Error during disconnect: {str(e)}", exc_info=True)
        self.client = None
    
    async def safe_execute(self, request: Any, *args, **kwargs) -> Any:
        async with self._semaphore:
            for attempt in range(config.MAX_RETRIES):
                try:
                    if callable(request):
                        return await request(*args, **kwargs)
                    return await self.client(request)
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

    def _generate_session_hash(self) -> str:
        return sha256(f"{self.phone}{datetime.now().isoformat()}".encode()).hexdigest()[:16]

def print_header(title: str) -> None:
    console.print(Panel.fit(
        title,
        style="bold cyan",
        border_style="blue",
        subtitle=f"Advanced Session Manager v5.1 (Telethon {telethon_version})"
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
    print_header("Create New Session")
    phone = Prompt.ask("[bold cyan]Enter phone number (e.g., +12345678900)[/bold cyan]", default="+")
    if not validate_phone(phone):
        print_error("Invalid phone number format")
        return None
    
    session_path = str(config.SESSION_FOLDER / f"{phone[1:]}.session")
    if os.path.exists(session_path):
        print_warning(f"Session already exists for {phone}")
        return session_path
    
    async with AdvancedTelegramClient(session_path, phone) as client:
        if not await client.connect():
            async with TelegramClient(StringSession(), config.API_ID, config.API_HASH) as temp_client:
                for attempt in range(config.MAX_RETRIES):
                    try:
                        print_info(f"Sending code to {phone}...")
                        await temp_client.connect()
                        await temp_client.send_code_request(phone)
                        code = Prompt.ask("[bold yellow]Enter code (or 'q' to quit)[/bold yellow]", default="q")
                        if code.lower() == 'q':
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
                            "premium": me.premium,
                            "language": me.lang_code
                        }
                        session_string = temp_client.session.save()
                        async with aiofiles.open(session_path, 'w') as f:
                            await f.write(session_string)
                        os.chmod(session_path, 0o664)
                        async with db_connection() as conn:
                            conn.execute(
                                "INSERT OR REPLACE INTO sessions (phone, path, created_at, last_used, metadata, session_hash) VALUES (?, ?, ?, ?, ?, ?)",
                                (phone, session_path, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), json.dumps(metadata), client._generate_session_hash())
                            )
                        print_success(f"Session created for {me.first_name} {me.last_name or ''} ({me.phone})")
                        logger.info(f"New session created for {phone}")
                        return session_path
                    except RPCError as e:
                        if "FLOOD_WAIT" in str(e):
                            wait = int(str(e).split()[1]) if str(e).split()[1].isdigit() else 300
                            console.print(f"[bold yellow]⚠ Flood wait required: {wait}s. Retrying after delay...[/bold yellow]")
                            await asyncio.sleep(wait)
                        elif "ResendCodeRequest" in str(e):
                            console.print("[bold yellow]⚠ Telegram verification limit reached. Please wait and try again later.[/bold yellow]")
                            return None
                        else:
                            print_error(f"Failed to create session: {str(e)}")
                            return None
                    except Exception as e:
                        print_error(f"Attempt {attempt+1} failed: {str(e)}")
                        if attempt < config.MAX_RETRIES - 1:
                            await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
                        else:
                            print_error(f"Failed to create session after {config.MAX_RETRIES} attempts")
                            return None
        return session_path

async def list_sessions(status_filter: str = "active") -> Optional[List[str]]:
    sessions = [str(p) for p in config.SESSION_FOLDER.glob("*.session")]
    if not sessions:
        print_warning("No saved sessions found")
        return None
    
    # Auto-detect and validate manually added sessions
    async with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone, path FROM sessions")
        known_sessions = {row[1]: row[0] for row in cursor.fetchall()}
        
        for session in sessions:
            if session not in known_sessions:
                phone = f"+{os.path.basename(session).replace('.session', '')}"
                async with AdvancedTelegramClient(session, phone) as client:
                    if await client.connect():
                        me = await client._me
                        metadata = {
                            "username": me.username,
                            "first_name": me.first_name,
                            "last_name": me.last_name,
                            "premium": me.premium,
                            "language": me.lang_code
                        }
                        cursor.execute(
                            "INSERT OR REPLACE INTO sessions (phone, path, created_at, last_used, metadata, session_hash) VALUES (?, ?, ?, ?, ?, ?)",
                            (phone, session, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), json.dumps(metadata), client._generate_session_hash())
                        )
                        conn.commit()
                        print_success(f"Added manually detected session: {phone}")
                        logger.info(f"Manually detected and added session: {phone}")
    
    table = Table(title=f"Available Sessions ({status_filter})", box=box.ROUNDED, header_style="bold magenta")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Phone", style="magenta")
    table.add_column("File", style="yellow")
    table.add_column("Last Used", style="blue")
    table.add_column("Username", style="cyan")
    table.add_column("Hash", style="white")
    
    filtered_sessions = []
    async with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone, path, last_used, metadata, session_hash, status FROM sessions WHERE status = ?", (status_filter,))
        db_sessions = {row[0]: row for row in cursor.fetchall()}
        
        for i, session in enumerate(sorted(sessions, key=os.path.getmtime, reverse=True), 1):
            session_name = os.path.basename(session).replace('.session', '')
            phone = f"+{session_name}"
            if phone in db_sessions:
                row = db_sessions[phone]
                last_used = row[2] if row[2] else "Never"
                metadata = json.loads(row[3]) if row[3] else {}
                username = metadata.get("username", "N/A")
                session_hash = row[4][:8] if row[4] else "N/A"
                table.add_row(str(i), phone, os.path.basename(session), last_used, username, session_hash)
                filtered_sessions.append(session)
    
    console.print(table)
    return filtered_sessions

async def select_and_login() -> Optional['AdvancedTelegramClient']:
    sessions = await list_sessions()
    if not sessions:
        return None
    
    while True:
        choice = Prompt.ask(f"[bold cyan]Select session (1-{len(sessions)} or 'q' to quit)[/bold cyan]", default="q")
        if choice.lower() == 'q':
            return None
        try:
            choice = int(choice) - 1
            if 0 <= choice < len(sessions):
                phone = f"+{os.path.basename(sessions[choice]).replace('.session', '')}"
                client = AdvancedTelegramClient(sessions[choice], phone)
                if await client.connect():
                    return client
            print_error(f"Invalid selection. Choose between 1 and {len(sessions)}")
        except ValueError:
            print_error("Please enter a valid number or 'q'")

async def terminate_other_sessions() -> None:
    print_header("Terminate Other Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        auths = await client.safe_execute(GetAuthorizationsRequest())
        other_sessions = [a for a in auths.authorizations if not a.current]
        
        if not other_sessions:
            print_info("No other active sessions found")
            return
            
        table = Table(title="Other Sessions", box=box.ROUNDED, header_style="bold red")
        table.add_column("Device", style="cyan")
        table.add_column("IP", style="magenta")
        table.add_column("Location", style="yellow")
        table.add_column("Last Active", style="green")
        for auth in other_sessions:
            table.add_row(auth.device_model, auth.ip, auth.country, auth.date_active.strftime('%Y-%m-%d %H:%M:%S'))
        console.print(table)
        
        if not Confirm.ask("[bold red]Confirm termination of ALL other sessions?[/bold red]"):
            return
            
        async with Progress() as progress:
            task = progress.add_task("[red]Terminating sessions...", total=len(other_sessions))
            tasks = [client.safe_execute(ResetAuthorizationRequest, hash=auth.hash) for auth in other_sessions]
            await asyncio.gather(*tasks)
            progress.update(task, completed=len(other_sessions))
        
        print_success(f"Terminated {len(other_sessions)} other sessions")
        logger.info(f"Terminated {len(other_sessions)} other sessions for {client.phone}")
    except Exception as e:
        print_error(f"Failed to terminate sessions: {str(e)}")
        logger.error(f"Failed to terminate sessions: {str(e)}", exc_info=True)

async def show_active_sessions() -> None:
    print_header("Active Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        auths = await client.safe_execute(GetAuthorizationsRequest())
        table = Table(title=f"Active Sessions ({len(auths.authorizations)})", box=box.ROUNDED, header_style="bold cyan")
        table.add_column("Status", style="bold")
        table.add_column("Device", style="cyan")
        table.add_column("IP", style="magenta")
        table.add_column("Location", style="yellow")
        table.add_column("Last Active", style="green")
        table.add_column("App Version", style="white")
        
        for auth in auths.authorizations:
            status = "[green]Current[/green]" if auth.current else "[red]Other[/red]"
            table.add_row(
                status, 
                auth.device_model, 
                auth.ip, 
                auth.country, 
                auth.date_active.strftime('%Y-%m-%d %H:%M:%S'),
                auth.app_version
            )
        console.print(table)
    except Exception as e:
        print_error(f"Error fetching sessions: {str(e)}")
        logger.error(f"Error fetching sessions: {str(e)}", exc_info=True)

async def update_profile_random_name() -> None:
    print_header("Update Profile")
    client = await select_and_login()
    if not client:
        return
    
    try:
        me = await client.client.get_me()
        old_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        adjectives = ["Cyber", "Quantum", "Neon", "Stealth", "Vortex"]
        nouns = ["Hacker", "Sentinel", "Phantom", "Rogue", "Titan"]
        new_name = f"{random.choice(adjectives)}{random.choice(nouns)}{random.randint(1000, 9999)}"
        about = Prompt.ask("[bold cyan]Enter new about text (optional)[/bold cyan]", default=f"Managed by Session Manager {datetime.now().strftime('%Y-%m-%d')}")
        await client.safe_execute(UpdateProfileRequest, first_name=new_name, about=about)
        print_success(f"Profile updated from '{old_name}' to '{new_name}'")
        logger.info(f"Profile updated for {client.phone}: {new_name}")
    except Exception as e:
        print_error(f"Failed to update profile: {str(e)}")
        logger.error(f"Failed to update profile: {str(e)}", exc_info=True)

async def clear_contacts() -> None:
    print_header("Clear Contacts")
    client = await select_and_login()
    if not client:
        return
    
    try:
        contacts = await client.safe_execute(GetContactsRequest, hash=0)
        if not contacts.contacts:
            print_info("No contacts to clear")
            return
            
        table = Table(title=f"Found {len(contacts.contacts)} Contacts", box=box.ROUNDED)
        table.add_column("Name", style="magenta")
        table.add_column("Phone", style="green")
        table.add_column("ID", style="cyan")
        for contact in contacts.contacts[:10]:
            user = next((u for u in contacts.users if u.id == contact.user_id), None)
            if user:
                table.add_row(
                    f"{user.first_name or ''} {user.last_name or ''}".strip(), 
                    user.phone or "N/A",
                    str(user.id)
                )
        console.print(table)
        if len(contacts.contacts) > 10:
            print_info(f"...and {len(contacts.contacts)-10} more contacts")
        
        if not Confirm.ask("[bold red]Delete ALL contacts?[/bold red]"):
            return
            
        async with Progress() as progress:
            task = progress.add_task("[red]Deleting contacts...", total=len(contacts.contacts))
            batches = [contacts.contacts[i:i + config.BATCH_SIZE] for i in range(0, len(contacts.contacts), config.BATCH_SIZE)]
            for batch in batches:
                await client.safe_execute(DeleteContactsRequest, id=[c.user_id for c in batch])
                progress.update(task, advance=len(batch))
                await asyncio.sleep(0.5)
        
        print_success(f"Deleted {len(contacts.contacts)} contacts")
        logger.info(f"Deleted {len(contacts.contacts)} contacts for {client.phone}")
    except Exception as e:
        print_error(f"Failed to clear contacts: {str(e)}")
        logger.error(f"Failed to clear contacts: {str(e)}", exc_info=True)

async def delete_all_chats_advanced() -> None:
    print_header("Advanced Chat Deletion")
    client = await select_and_login()
    if not client:
        return
    
    try:
        dialogs = await client.safe_execute(GetDialogsRequest, offset_date=None, offset_id=0, offset_peer=types.InputPeerEmpty(), limit=500, hash=0)
        if not dialogs.dialogs:
            print_info("No chats/channels found")
            return
            
        table = Table(title=f"Found {len(dialogs.dialogs)} Chats/Channels", box=box.ROUNDED)
        table.add_column("Type", style="cyan")
        table.add_column("Title", style="magenta")
        table.add_column("ID", style="yellow")
        table.add_column("Members", style="white")
        for dialog in dialogs.dialogs[:10]:
            entity = dialog.entity
            chat_type = "Channel" if isinstance(entity, types.Channel) else "Chat"
            members = getattr(entity, 'participants_count', 'N/A')
            table.add_row(chat_type, getattr(entity, 'title', 'Unknown'), str(entity.id), str(members))
        console.print(table)
        if len(dialogs.dialogs) > 10:
            print_info(f"...and {len(dialogs.dialogs)-10} more items")
        
        if not Confirm.ask("[bold red]Delete ALL chats/channels?[/bold red]"):
            return
            
        async with Progress() as progress:
            task = progress.add_task("[red]Deleting chats...", total=len(dialogs.dialogs))
            tasks = []
            for dialog in dialogs.dialogs:
                if isinstance(dialog.entity, types.Channel):
                    tasks.append(client.safe_execute(LeaveChannelRequest, channel=dialog.entity))
                else:
                    tasks.append(client.client.delete_dialog(dialog.entity))
            await asyncio.gather(*tasks)
            progress.update(task, completed=len(dialogs.dialogs))
        
        print_success(f"Deleted {len(dialogs.dialogs)} chats/channels")
        logger.info(f"Deleted {len(dialogs.dialogs)} chats/channels for {client.phone}")
    except Exception as e:
        print_error(f"Error: {str(e)}")
        logger.error(f"Error deleting chats: {str(e)}", exc_info=True)

async def check_spam_status() -> None:
    print_header("Check Spam Status")
    client = await select_and_login()
    if not client:
        return
    
    try:
        ttl = await client.safe_execute(GetAccountTTLRequest)
        test_msg = await client.safe_execute(client.client.send_message, "me", f"Spam check {datetime.now().isoformat()}")
        status = "[green]UNRESTRICTED[/green]" if test_msg else "[red]RESTRICTED[/red]"
        if test_msg:
            await client.safe_execute(client.client.delete_messages, "me", [test_msg.id])
        
        password_info = await client.safe_execute(GetPasswordRequest)
        has_2fa = "Yes" if password_info.has_password else "No"
        
        table = Table(title="Account Status", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")
        table.add_row("Account TTL", f"{ttl.days} days")
        table.add_row("Spam Status", status)
        table.add_row("2FA Enabled", has_2fa)
        console.print(table)
    except Exception as e:
        print_warning(f"Possible spam restriction: {str(e)}")
        logger.warning(f"Spam check failed: {str(e)}", exc_info=True)

async def read_session_otp() -> None:
    print_header("Read OTP")
    client = await select_and_login()
    if not client:
        return
    
    try:
        messages = await client.safe_execute(client.client.get_messages, "Telegram", limit=20)
        otps = []
        for msg in messages:
            if "login code" in msg.text.lower():
                code = ''.join(filter(str.isdigit, msg.text))
                otps.append((code, msg.date))
        
        if otps:
            for code, date in sorted(otps, key=lambda x: x[1], reverse=True)[:3]:
                console.print(Panel(
                    f"OTP: [bold green]{code}[/bold green]\nReceived: {date}",
                    title="Login Code",
                    border_style="green"
                ))
                logger.info(f"OTP read: {code} for {client.phone}")
        else:
            print_info("No recent OTPs found")
    except Exception as e:
        print_error(f"Failed to read OTP: {str(e)}")
        logger.error(f"Failed to read OTP: {str(e)}", exc_info=True)

async def manage_2fa() -> None:
    print_header("2FA Management")
    client = await select_and_login()
    if not client:
        return
    
    async def get_password_hash(password: str) -> bytes:
        password_info = await client.safe_execute(GetPasswordRequest)
        if not password_info.current_algo:
            return None
        return await asyncio.get_event_loop().run_in_executor(
            executor,
            lambda: client.client._get_password_hash(password, password_info.current_algo)
        )
    
    async def enable_2fa():
        password = getpass.getpass("[bold]Enter new 2FA password (min 8 chars): [/bold]")
        if len(password) < 8:
            print_error("Password must be at least 8 characters")
            return
        hint = Prompt.ask("[bold]Enter password hint (optional)[/bold]", default="")
        email = Prompt.ask("[bold]Enter recovery email (optional)[/bold]", default="")
        
        try:
            await client.client(functions.account.UpdatePasswordSettingsRequest(
                password=types.InputCheckPasswordEmpty(),
                new_settings=types.account.PasswordInputSettings(
                    new_algo=types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
                        salt1=os.urandom(32),
                        salt2=os.urandom(32),
                        g=2,
                        p=bytes.fromhex('c5')
                    ),
                    new_password_hash=await get_password_hash(password),
                    hint=hint,
                    email=email if email else None
                )
            ))
            print_success("2FA enabled successfully")
            logger.info(f"2FA enabled for {client.phone}")
        except Exception as e:
            print_error(f"Failed to enable 2FA: {str(e)}")
            logger.error(f"Failed to enable 2FA: {str(e)}", exc_info=True)
    
    async def disable_2fa():
        if not Confirm.ask("[bold red]Are you sure you want to disable 2FA?[/bold red]"):
            return
        password = getpass.getpass("[bold red]Enter current 2FA password: [/bold red]")
        try:
            await client.client(functions.account.UpdatePasswordSettingsRequest(
                password=await get_password_hash(password),
                new_settings=types.account.PasswordInputSettings(
                    new_algo=types.PasswordKdfAlgoUnknown(),
                    new_password_hash=b'',
                    hint=''
                )
            ))
            print_success("2FA disabled successfully")
            logger.info(f"2FA disabled for {client.phone}")
        except Exception as e:
            print_error(f"Failed to disable 2FA: {str(e)}")
            logger.error(f"Failed to disable 2FA: {str(e)}", exc_info=True)
    
    async def change_2fa_password():
        current = getpass.getpass("[bold]Enter current 2FA password: [/bold]")
        new_pass = getpass.getpass("[bold]Enter new 2FA password (min 8 chars): [/bold]")
        if len(new_pass) < 8:
            print_error("New password must be at least 8 characters")
            return
        hint = Prompt.ask("[bold]Enter new password hint (optional)[/bold]", default="")
        
        try:
            await client.client(functions.account.UpdatePasswordSettingsRequest(
                password=await get_password_hash(current),
                new_settings=types.account.PasswordInputSettings(
                    new_algo=types.PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow(
                        salt1=os.urandom(32),
                        salt2=os.urandom(32),
                        g=2,
                        p=bytes.fromhex('c5')
                    ),
                    new_password_hash=await get_password_hash(new_pass),
                    hint=hint
                )
            ))
            print_success("2FA password changed successfully")
            logger.info(f"2FA password changed for {client.phone}")
        except Exception as e:
            print_error(f"Failed to change 2FA password: {str(e)}")
            logger.error(f"Failed to change 2FA password: {str(e)}", exc_info=True)
    
    async def check_2fa_status():
        try:
            password_info = await client.safe_execute(GetPasswordRequest)
            table = Table(title="2FA Status", box=box.ROUNDED)
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="magenta")
            table.add_row("Enabled", "Yes" if password_info.has_password else "No")
            table.add_row("Hint", password_info.hint or "None")
            table.add_row("Email", "Set" if password_info.has_recovery else "Not set")
            console.print(table)
        except Exception as e:
            print_error(f"Failed to check 2FA status: {str(e)}")
            logger.error(f"Failed to check 2FA status: {str(e)}", exc_info=True)

    menu_options = {
        "1": ("Enable 2FA", enable_2fa),
        "2": ("Disable 2FA", disable_2fa),
        "3": ("Change 2FA Password", change_2fa_password),
        "4": ("Check 2FA Status", check_2fa_status),
        "5": ("Back", lambda: None)
    }
    
    while True:
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Option", style="cyan")
        table.add_column("Action", style="magenta")
        for num, (desc, _) in menu_options.items():
            table.add_row(num, desc)
        console.print(table)
        
        choice = Prompt.ask("[bold cyan]Select option[/bold cyan]", choices=list(menu_options.keys()))
        if choice == "5":
            break
        await menu_options[choice][1]()

async def export_sessions() -> None:
    print_header("Export Sessions")
    sessions = await list_sessions()
    if not sessions:
        return
    
    try:
        async with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, path, created_at, last_used, metadata, session_hash, status FROM sessions")
            data = cursor.fetchall()
        
        export_path = f"sessions_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        async with aiofiles.open(export_path, 'w', newline='') as f:
            await f.write("Phone,Path,Created At,Last Used,Metadata,Session Hash,Status\n")
            for row in data:
                metadata = json.dumps(json.loads(row[4]) if row[4] else {})
                await f.write(f"{row[0]},{row[1]},{row[2]},{row[3]},{metadata},{row[5]},{row[6]}\n")
        
        print_success(f"Exported {len(data)} sessions to {export_path}")
        logger.info(f"Exported {len(data)} sessions to {export_path}")
    except Exception as e:
        print_error(f"Failed to export sessions: {str(e)}")
        logger.error(f"Failed to export sessions: {str(e)}", exc_info=True)

async def session_statistics() -> None:
    print_header("Session Statistics")
    try:
        async with db_connection() as conn:
            cursor = conn.cursor()
            stats = {
                "Total Sessions": cursor.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "Active Sessions": cursor.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'").fetchone()[0],
                "Premium Accounts": cursor.execute("SELECT COUNT(*) FROM sessions WHERE json_extract(metadata, '$.premium') = 1").fetchone()[0],
                "Recently Used": cursor.execute("SELECT COUNT(*) FROM sessions WHERE last_used > ?", (datetime.now(timezone.utc).isoformat(timespec='hours'),)).fetchone()[0]
            }
        
        table = Table(title="Session Statistics", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")
        for metric, value in stats.items():
            table.add_row(metric, str(value))
        console.print(table)
    except Exception as e:
        print_error(f"Failed to get statistics: {str(e)}")
        logger.error(f"Failed to get statistics: {str(e)}", exc_info=True)

async def backup_sessions() -> None:
    print_header("Backup Sessions")
    sessions = await list_sessions()
    if not sessions:
        return
    
    backup_dir = config.SESSION_FOLDER / "backups" / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(exist_ok=True)
    
    try:
        async with Progress() as progress:
            task = progress.add_task("[green]Backing up sessions...", total=len(sessions))
            tasks = []
            async def backup_file(src, dest):
                async with aiofiles.open(src, 'rb') as s, aiofiles.open(dest, 'wb') as d:
                    await d.write(await s.read())
                progress.update(task, advance=1)
            
            for session in sessions:
                dest = backup_dir / os.path.basename(session)
                tasks.append(backup_file(session, dest))
            await asyncio.gather(*tasks)
        
        print_success(f"Backed up {len(sessions)} sessions to {backup_dir}")
        logger.info(f"Backed up {len(sessions)} sessions to {backup_dir}")
    except Exception as e:
        print_error(f"Failed to backup sessions: {str(e)}")
        logger.error(f"Failed to backup sessions: {str(e)}", exc_info=True)

async def cleanup_sessions() -> None:
    print_header("Cleanup Sessions")
    sessions = [str(p) for p in config.SESSION_FOLDER.glob("*.session")]
    
    if not sessions:
        print_info("No sessions to clean")
        return
    
    async with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM sessions WHERE status = 'active'")
        db_paths = set(row[0] for row in cursor.fetchall())
    
    orphaned = [s for s in sessions if s not in db_paths]
    if not orphaned:
        print_info("No orphaned sessions found")
        return
    
    table = Table(title=f"Found {len(orphaned)} Orphaned Sessions", box=box.ROUNDED)
    table.add_column("File", style="yellow")
    table.add_column("Size (KB)", style="white")
    for session in orphaned[:10]:
        size = os.path.getsize(session) / 1024
        table.add_row(os.path.basename(session), f"{size:.2f}")
    console.print(table)
    if len(orphaned) > 10:
        print_info(f"...and {len(orphaned)-10} more")
    
    if not Confirm.ask("[bold red]Delete all orphaned sessions?[/bold red]"):
        return
    
    async with Progress() as progress:
        task = progress.add_task("[red]Cleaning up...", total=len(orphaned))
        tasks = [asyncio.get_event_loop().run_in_executor(executor, os.remove, session) for session in orphaned]
        await asyncio.gather(*tasks)
        progress.update(task, completed=len(orphaned))
    
    print_success(f"Cleaned up {len(orphaned)} orphaned sessions")
    logger.info(f"Cleaned up {len(orphaned)} orphaned sessions")

async def bulk_session_check() -> None:
    print_header("Bulk Session Health Check")
    sessions = await list_sessions()
    if not sessions:
        return
    
    async def check_session(session: str) -> Dict[str, Any]:
        phone = f"+{os.path.basename(session).replace('.session', '')}"
        async with AdvancedTelegramClient(session, phone) as client:
            try:
                connected = await client.connect()
                status = "Healthy" if connected else "Invalid"
                name = client._me.first_name if connected else "N/A"
                return {"phone": phone, "status": status, "name": name}
            except Exception as e:
                return {"phone": phone, "status": f"Error: {str(e)}", "name": "N/A"}
    
    async with Progress() as progress:
        task = progress.add_task("[cyan]Checking sessions...", total=len(sessions))
        tasks = [check_session(session) for session in sessions]
        results = await asyncio.gather(*tasks)
        progress.update(task, completed=len(sessions))
    
    table = Table(title="Session Health Check", box=box.ROUNDED)
    table.add_column("Phone", style="magenta")
    table.add_column("Status", style="green")
    table.add_column("Name", style="cyan")
    for result in results:
        table.add_row(result["phone"], result["status"], result["name"])
    console.print(table)
    
    unhealthy = [r["phone"] for r in results if "Healthy" not in r["status"]]
    if unhealthy and Confirm.ask("[bold yellow]Mark unhealthy sessions as inactive?[/bold yellow]"):
        async with db_connection() as conn:
            conn.executemany("UPDATE sessions SET status = 'inactive' WHERE phone = ?", [(p,) for p in unhealthy])
        print_success(f"Marked {len(unhealthy)} sessions as inactive")
        logger.info(f"Marked {len(unhealthy)} sessions as inactive")

async def main() -> None:
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
        "10": ("2FA Management", manage_2fa),
        "11": ("Export Sessions", export_sessions),
        "12": ("Session Statistics", session_statistics),
        "13": ("Backup Sessions", backup_sessions),
        "14": ("Cleanup Sessions", cleanup_sessions),
        "15": ("Bulk Session Check", bulk_session_check),
        "16": ("Exit", lambda: None)
    }
    
    while True:
        print_header("Telegram Advanced Session Manager")
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Option", style="cyan", justify="right")
        table.add_column("Description", style="magenta")
        for num, (desc, _) in menu_options.items():
            table.add_row(num, desc)
        console.print(table)
        
        choice = Prompt.ask("[bold cyan]Select option (1-16)[/bold cyan]", choices=list(menu_options.keys()))
        if choice == "16":
            print_success("Goodbye!")
            break
        await menu_options[choice][1]()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[bold red]✗ Operation cancelled by user[/bold red]")
        sys.exit(0)
    except Exception as e:
        print_error(f"Fatal error: {str(e)}")
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        executor.shutdown()