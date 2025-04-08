#!/usr/bin/env python3
import os
import asyncio
import getpass
import platform
import signal
import sys
import sqlite3
import json
import aiofiles
import csv
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager
from telethon import TelegramClient, functions, types, __version__ as telethon_version
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    RPCError,
    PhoneCodeExpiredError
)
from telethon.sessions import StringSession
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest,
    GetAccountTTLRequest,
    UpdatePasswordSettingsRequest,
    GetPasswordRequest
)
from telethon.tl.functions.auth import ResendCodeRequest
from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import GetDialogsRequest
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
from rich.prompt import Prompt, Confirm
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

# Initialize
console = Console()
executor = ThreadPoolExecutor(max_workers=4)
VERSION = "5.6"

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]',
    handlers=[RotatingFileHandler('logs/session_manager.log', maxBytes=10*1024*1024, backupCount=10)]
)
logger = logging.getLogger(__name__)

class SecureConfig:
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SecureConfig, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.API_POOL = [
            {"API_ID": 23077946, "API_HASH": "b6c2b715121435d4aa285c1fb2bc2220", "limits": {"last_used": None, "count": 0}},
            {"API_ID": 29637547, "API_HASH": "13e303a526522f741c0680cfc8cd9c00", "limits": {"last_used": None, "count": 0}}
        ]
        self.SESSION_FOLDER = Path("sessions")
        self.DB_PATH = Path("sessions.db")
        self.MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
        self.RETRY_DELAY = int(os.getenv("RETRY_DELAY", "5"))
        self.BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
        self.CONCURRENT_CONNECTIONS = int(os.getenv("CONCURRENT_CONNECTIONS", "4"))
        self.TELETHON_VERSION = telethon_version
        self._setup_folders()
        self._migrate_database()
    
    def _setup_folders(self):
        self.SESSION_FOLDER.mkdir(exist_ok=True)
        (self.SESSION_FOLDER / "backups").mkdir(exist_ok=True)
        os.chmod(self.SESSION_FOLDER, 0o700)
        if self.DB_PATH.exists():
            os.chmod(self.DB_PATH, 0o600)
    
    def _migrate_database(self):
        if not self.DB_PATH.exists():
            self._create_database()
            os.chmod(self.DB_PATH, 0o600)
            return
        with sqlite3.connect(self.DB_PATH, timeout=20) as conn:
            conn.execute("PRAGMA busy_timeout = 20000")
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(sessions)")
            columns = {col[1] for col in cursor.fetchall()}
            migrations = [
                ("metadata", "ALTER TABLE sessions ADD COLUMN metadata TEXT"),
                ("session_hash", "ALTER TABLE sessions ADD COLUMN session_hash TEXT"),
                ("status", "ALTER TABLE sessions ADD COLUMN status TEXT DEFAULT 'active'"),
                ("notes", "ALTER TABLE sessions ADD COLUMN notes TEXT")
            ]
            for col_name, sql in migrations:
                if col_name not in columns:
                    cursor.execute(sql)
                    logger.info(f"Database migrated: added {col_name} column")
            conn.commit()

    def _create_database(self):
        with sqlite3.connect(self.DB_PATH, timeout=20) as conn:
            conn.execute("PRAGMA busy_timeout = 20000")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    phone TEXT PRIMARY KEY,
                    path TEXT UNIQUE,
                    created_at TEXT,
                    last_used TEXT,
                    metadata TEXT,
                    session_hash TEXT,
                    status TEXT DEFAULT 'active',
                    notes TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phone ON sessions(phone)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON sessions(status)")
            conn.commit()
            logger.info("Created new sessions database")

    async def get_available_api(self) -> Dict[str, Any]:
        for api in self.API_POOL:
            if api["limits"]["count"] < 100 or (api["limits"]["last_used"] and (datetime.now(timezone.utc) - api["limits"]["last_used"]).total_seconds() > 3600):
                if api["limits"]["last_used"] and (datetime.now(timezone.utc) - api["limits"]["last_used"]).total_seconds() > 3600:
                    api["limits"]["count"] = 0
                api["limits"]["last_used"] = datetime.now(timezone.utc)
                api["limits"]["count"] += 1
                logger.info(f"Using API {api['API_ID']} (count: {api['limits']['count']})")
                return api
        raise Exception("All APIs have reached their limits. Please wait and try again later.")

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
        self._connected = False
        
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
        
    async def connect(self) -> bool:
        if self._connected:
            return True
            
        session = StringSession()
        if os.path.exists(self.session_path):
            try:
                async with aiofiles.open(self.session_path, 'r') as f:
                    session_string = await f.read()
                session = StringSession(session_string.strip())
            except Exception as e:
                console.print(f"[yellow]⚠ Failed to load session file: {e}[/yellow]")
                logger.warning(f"Failed to load {self.session_path}: {e}")
        
        api = await config.get_available_api()
        self.client = TelegramClient(
            session,
            api["API_ID"],
            api["API_HASH"],
            device_model=f"SessionManager-{platform.node()}",
            system_version=platform.system(),
            app_version=VERSION,
            connection_retries=config.MAX_RETRIES,
            retry_delay=config.RETRY_DELAY
        )
        
        for attempt in range(config.MAX_RETRIES):
            try:
                with console.status(f"[cyan]Connecting to Telegram with API {api['API_ID']}...", spinner="dots"):
                    await self.client.connect()
                    if not await self.client.is_user_authorized():
                        console.print(f"[yellow]⚠ Session {self.phone} not authorized[/yellow]")
                        return False
                    self._me = await self.client.get_me()
                    self._connected = True
                    async with db_connection() as conn:
                        conn.execute(
                            "UPDATE sessions SET last_used = ?, session_hash = ? WHERE phone = ?",
                            (datetime.now(timezone.utc).isoformat(), self._generate_session_hash(), self.phone)
                        )
                    console.print(f"[green]✓ Connected as {self._me.first_name} (ID: {self._me.id})[/green]")
                    logger.info(f"Connected to {self.phone} with API {api['API_ID']}")
                    return True
            except Exception as e:
                console.print(f"[red]✗ Attempt {attempt + 1} failed: {e}[/red]")
                logger.error(f"Connection attempt {attempt + 1} failed for {self.phone}: {e}")
                if attempt < config.MAX_RETRIES - 1:
                    await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
        console.print(f"[red]✗ Failed after {config.MAX_RETRIES} attempts[/red]")
        return False
    
    async def disconnect(self):
        if self.client and self._connected:
            try:
                session_string = self.client.session.save()
                async with aiofiles.open(self.session_path, 'w') as f:
                    await f.write(session_string)
                os.chmod(self.session_path, 0o600)
                await self.client.disconnect()
                self._connected = False
                console.print("[blue]ℹ Client disconnected[/blue]")
                logger.info(f"Disconnected {self.phone}")
            except Exception as e:
                console.print(f"[red]✗ Disconnect failed: {e}[/red]")
                logger.error(f"Disconnect failed for {self.phone}: {e}")
        self.client = None
    
    async def safe_execute(self, request: Any, *args, **kwargs) -> Any:
        if not self._connected:
            if not await self.connect():
                return None
        for attempt in range(config.MAX_RETRIES):
            try:
                if callable(request):
                    return await request(*args, **kwargs)
                return await self.client(request)
            except FloodWaitError as e:
                wait = min(e.seconds, 3600)
                console.print(f"[yellow]⚠ Flood wait: {wait}s[/yellow]")
                await asyncio.sleep(wait)
            except Exception as e:
                if attempt == config.MAX_RETRIES - 1:
                    console.print(f"[red]✗ Operation failed: {e}[/red]")
                    logger.error(f"Operation failed for {self.phone}: {e}")
                    raise
                await asyncio.sleep(config.RETRY_DELAY * (2 ** attempt))
        return None
    
    def _generate_session_hash(self) -> str:
        return sha256(f"{self.phone}{datetime.now().isoformat()}".encode()).hexdigest()[:16]

def print_header(title: str) -> None:
    console.print(Panel(
        Text(title, style="bold cyan", justify="center"),
        border_style="blue",
        subtitle=f"v{VERSION} | Telethon {telethon_version}",
        subtitle_align="right",
        padding=(0, 2),
        width=60
    ))

def print_message(style: str, symbol: str, message: str):
    console.print(f"[{style}]{symbol}[/] {message}", width=60)

def validate_phone(phone: str) -> bool:
    phone = phone.strip()
    return phone.startswith('+') and 10 <= len(phone) <= 15 and phone[1:].isdigit()

async def create_session() -> Optional[str]:
    print_header("Create New Session")
    while True:
        phone = Prompt.ask("[cyan]Enter phone number (e.g., +919741023014, 'q' to quit)[/cyan]")
        if phone.lower() == 'q':
            return None
        if not validate_phone(phone):
            print_message("red", "✗", "Invalid format. Use + followed by 10-14 digits")
            continue
        break
    
    session_path = str(config.SESSION_FOLDER / f"{phone[1:]}.session")
    if os.path.exists(session_path):
        print_message("yellow", "⚠", f"Session exists for {phone}")
        if not Confirm.ask("[yellow]Overwrite existing session?[/yellow]"):
            return session_path
    
    api = await config.get_available_api()
    session = StringSession()
    async with TelegramClient(session, api["API_ID"], api["API_HASH"]) as client:
        try:
            with console.status(f"[cyan]Connecting to Telegram with API {api['API_ID']}...", spinner="dots"):
                await client.connect()
            print_message("blue", "ℹ", f"Sending code to {phone}")
            sent_code = await client.send_code_request(phone)
            for attempt in range(3):
                code = Prompt.ask("[yellow]Enter the code you received ('q' to quit, 'r' to resend)[/yellow]")
                if code.lower() == 'q':
                    return None
                elif code.lower() == 'r':
                    try:
                        sent_code = await client(ResendCodeRequest(phone, sent_code.phone_code_hash))
                        print_message("blue", "ℹ", "Code resent successfully")
                        continue
                    except RPCError as e:
                        print_message("red", "✗", f"Failed to resend code: {e}")
                        logger.error(f"Failed to resend code for {phone}: {e}")
                        if "all available options" in str(e).lower():
                            print_message("yellow", "⚠", "All code delivery options exhausted. Wait 5-10 minutes and try again.")
                        return None
                try:
                    await client.sign_in(phone, code, phone_code_hash=sent_code.phone_code_hash)
                    break
                except PhoneCodeInvalidError:
                    print_message("yellow", "⚠", "Invalid code")
                    if attempt == 2:
                        print_message("red", "✗", "Too many invalid attempts")
                        return None
                except PhoneCodeExpiredError:
                    print_message("red", "✗", "Code expired, please resend")
                    continue
                except SessionPasswordNeededError:
                    password = getpass.getpass("Enter 2FA password: ")
                    try:
                        await client.sign_in(password=password)
                        break
                    except Exception as e:
                        print_message("red", "✗", f"Invalid 2FA password: {e}")
                        return None
            
            me = await client.get_me()
            metadata = {
                "username": me.username or "",
                "first_name": me.first_name or "",
                "last_name": me.last_name or "",
                "premium": me.premium,
                "id": str(me.id)
            }
            session_string = client.session.save()
            async with aiofiles.open(session_path, 'w') as f:
                await f.write(session_string)
            os.chmod(session_path, 0o600)
            async with db_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions (phone, path, created_at, last_used, metadata, session_hash, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (phone, session_path, datetime.now(timezone.utc).isoformat(), 
                     datetime.now(timezone.utc).isoformat(), json.dumps(metadata), 
                     sha256(phone.encode()).hexdigest()[:16], "active")
                )
            print_message("green", "✓", f"Signed in successfully as {me.first_name} 💻; remember to not break the ToS or you will risk an account ban!")
            logger.info(f"Session created for {phone} with API {api['API_ID']}")
            return session_path
        except RPCError as e:
            print_message("red", "✗", f"Telegram error: {e}")
            logger.error(f"Telegram error for {phone}: {e}")
            if "all available options" in str(e).lower():
                print_message("yellow", "⚠", "All code delivery options exhausted. Wait 5-10 minutes and try again.")
            return None
        except Exception as e:
            print_message("red", "✗", f"Unexpected error: {e}")
            logger.error(f"Failed to create session for {phone}: {e}")
            return None

async def list_sessions(status_filter: str = "active") -> Optional[List[str]]:
    print_header("List Sessions")
    sessions = [str(p) for p in config.SESSION_FOLDER.glob("*.session")]
    if not sessions:
        print_message("yellow", "⚠", "No sessions found")
        return None
    
    async with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone, path, last_used, metadata, session_hash, status FROM sessions WHERE status = ?", (status_filter,))
        db_sessions = {row[0]: row for row in cursor.fetchall()}
        
        for session in sessions[:]:
            phone = f"+{os.path.basename(session).replace('.session', '')}"
            if phone not in db_sessions and validate_phone(phone):
                async with AdvancedTelegramClient(session, phone) as client:
                    if await client.connect():
                        me = client._me
                        metadata = {
                            "username": me.username or "",
                            "first_name": me.first_name or "",
                            "last_name": me.last_name or "",
                            "premium": me.premium,
                            "id": str(me.id)
                        }
                        cursor.execute(
                            "INSERT OR IGNORE INTO sessions (phone, path, created_at, last_used, metadata, session_hash, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (phone, session, datetime.now(timezone.utc).isoformat(), 
                             datetime.now(timezone.utc).isoformat(), json.dumps(metadata), 
                             client._generate_session_hash(), "active")
                        )
                        conn.commit()
                        print_message("green", "✓", f"Added manual session: {phone}")
                        logger.info(f"Added manual session: {phone}")
                    else:
                        print_message("yellow", "⚠", f"Unverified session: {phone}")
                        cursor.execute(
                            "INSERT OR IGNORE INTO sessions (phone, path, created_at, last_used, metadata, session_hash, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (phone, session, datetime.now(timezone.utc).isoformat(), None, '{}', 
                             sha256(phone.encode()).hexdigest()[:16], "inactive")
                        )
                        conn.commit()
    
    table = Table(title=f"[magenta]Sessions ({status_filter})[/magenta]", box=box.ROUNDED, border_style="blue", width=60)
    table.add_column("#", style="cyan", width=4, justify="right")
    table.add_column("Phone", style="magenta", width=15)
    table.add_column("Last Used", style="blue", width=19)
    table.add_column("Username", style="cyan", width=12)
    table.add_column("Status", style="green", width=10)
    
    filtered_sessions = []
    for i, session in enumerate(sorted(sessions, key=os.path.getmtime, reverse=True), 1):
        phone = f"+{os.path.basename(session).replace('.session', '')}"
        if phone in db_sessions:
            row = db_sessions[phone]
            last_used = row[2][:19] if row[2] else "Never"
            metadata = json.loads(row[3] or '{}')
            status = "[green]Active[/green]" if row[5] == "active" else "[red]Inactive[/red]"
            table.add_row(str(i), phone, last_used, metadata.get("username", "N/A"), status)
            filtered_sessions.append(session)
        else:
            table.add_row(str(i), phone, "Pending", "N/A", "[yellow]Unverified[/yellow]")
            filtered_sessions.append(session)
    
    console.print(table)
    return filtered_sessions

async def select_and_login() -> Optional['AdvancedTelegramClient']:
    sessions = await list_sessions()
    if not sessions:
        return None
    while True:
        choice = Prompt.ask(f"[cyan]Select session (1-{len(sessions)}, 'q' to quit)[/cyan]", default="q")
        if choice.lower() == 'q':
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                phone = f"+{os.path.basename(sessions[idx]).replace('.session', '')}"
                client = AdvancedTelegramClient(sessions[idx], phone)
                if await client.connect():
                    return client
                print_message("red", "✗", f"Failed to connect to {phone}")
            else:
                print_message("red", "✗", f"Invalid choice (1-{len(sessions)})")
        except ValueError:
            print_message("red", "✗", "Invalid input")
        return None

async def terminate_other_sessions():
    print_header("Terminate Other Sessions")
    client = await select_and_login()
    if not client:
        return
    try:
        auths = await client.safe_execute(GetAuthorizationsRequest())
        other_sessions = [a for a in auths.authorizations if not a.current]
        if not other_sessions:
            print_message("blue", "ℹ", "No other active sessions found")
            return
        
        table = Table(title="Other Sessions", box=box.ROUNDED, border_style="red", width=60)
        table.add_column("Device", style="cyan", width=15)
        table.add_column("IP", style="magenta", width=15)
        table.add_column("Location", style="yellow", width=15)
        table.add_column("Last Active", style="green", width=15)
        for auth in other_sessions:
            table.add_row(auth.device_model, auth.ip, auth.country, auth.date_active.strftime('%Y-%m-%d %H:%M'))
        console.print(table)
        
        if not Confirm.ask("[red]Terminate all other sessions?[/red]"):
            return
        
        async with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
            task = progress.add_task("[red]Terminating...", total=len(other_sessions))
            for auth in other_sessions:
                await client.safe_execute(ResetAuthorizationRequest, hash=auth.hash)
                progress.update(task, advance=1)
        print_message("green", "✓", f"Terminated {len(other_sessions)} sessions")
        logger.info(f"Terminated {len(other_sessions)} sessions for {client.phone}")
    except Exception as e:
        print_message("red", "✗", f"Failed: {e}")
        logger.error(f"Failed to terminate sessions for {client.phone}: {e}")

async def show_active_sessions():
    print_header("Active Sessions")
    client = await select_and_login()
    if not client:
        return
    try:
        auths = await client.safe_execute(GetAuthorizationsRequest())
        table = Table(title=f"Active Sessions ({len(auths.authorizations)})", box=box.ROUNDED, border_style="cyan", width=60)
        table.add_column("Status", style="bold", width=10)
        table.add_column("Device", style="cyan", width=15)
        table.add_column("IP", style="magenta", width=15)
        table.add_column("Last Active", style="green", width=20)
        for auth in auths.authorizations:
            status = "[green]Current[/green]" if auth.current else "[red]Other[/red]"
            table.add_row(status, auth.device_model, auth.ip, auth.date_active.strftime('%Y-%m-%d %H:%M'))
        console.print(table)
    except Exception as e:
        print_message("red", "✗", f"Error: {e}")
        logger.error(f"Error fetching sessions for {client.phone}: {e}")

async def update_profile_random_name():
    print_header("Update Profile")
    client = await select_and_login()
    if not client:
        return
    try:
        me = await client.client.get_me()
        old_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        adjectives = ["Cyber", "Quantum", "Neon", "Stealth", "Vortex"]
        nouns = ["Hacker", "Sentinel", "Phantom", "Rogue", "Titan"]
        new_name = f"{adjectives[0]}{nouns[0]}{random.randint(1000, 9999)}"
        about = Prompt.ask("[cyan]New about text (Enter to skip)[/cyan]", default="")
        await client.safe_execute(UpdateProfileRequest, first_name=new_name, about=about or None)
        print_message("green", "✓", f"Updated from '{old_name}' to '{new_name}'")
        logger.info(f"Profile updated for {client.phone}: {new_name}")
    except Exception as e:
        print_message("red", "✗", f"Failed: {e}")
        logger.error(f"Failed to update profile for {client.phone}: {e}")

async def clear_contacts():
    print_header("Clear Contacts")
    client = await select_and_login()
    if not client:
        return
    try:
        contacts = await client.safe_execute(GetContactsRequest, hash=0)
        if not contacts.contacts:
            print_message("blue", "ℹ", "No contacts found")
            return
        
        table = Table(title=f"Contacts ({len(contacts.contacts)})", box=box.ROUNDED, border_style="magenta", width=60)
        table.add_column("Name", style="magenta", width=30)
        table.add_column("Phone", style="green", width=30)
        for contact in contacts.contacts[:10]:
            user = next((u for u in contacts.users if u.id == contact.user_id), None)
            if user:
                table.add_row(f"{user.first_name or ''} {user.last_name or ''}".strip(), user.phone or "N/A")
        console.print(table)
        if len(contacts.contacts) > 10:
            print_message("blue", "ℹ", f"...and {len(contacts.contacts) - 10} more")
        
        if not Confirm.ask("[red]Delete all contacts?[/red]"):
            return
        
        async with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
            task = progress.add_task("[red]Deleting...", total=len(contacts.contacts))
            batches = [contacts.contacts[i:i + config.BATCH_SIZE] for i in range(0, len(contacts.contacts), config.BATCH_SIZE)]
            for batch in batches:
                await client.safe_execute(DeleteContactsRequest, id=[c.user_id for c in batch])
                progress.update(task, advance=len(batch))
        print_message("green", "✓", f"Deleted {len(contacts.contacts)} contacts")
        logger.info(f"Deleted {len(contacts.contacts)} contacts for {client.phone}")
    except Exception as e:
        print_message("red", "✗", f"Failed: {e}")
        logger.error(f"Failed to clear contacts for {client.phone}: {e}")

async def delete_all_chats_advanced():
    print_header("Advanced Chat Deletion")
    client = await select_and_login()
    if not client:
        return
    try:
        dialogs = await client.safe_execute(GetDialogsRequest, offset_date=None, offset_id=0, offset_peer=types.InputPeerEmpty(), limit=500, hash=0)
        if not dialogs.dialogs:
            print_message("blue", "ℹ", "No chats or channels found")
            return
        
        table = Table(title=f"Chats/Channels ({len(dialogs.dialogs)})", box=box.ROUNDED, border_style="yellow", width=60)
        table.add_column("Type", style="cyan", width=15)
        table.add_column("Title", style="magenta", width=30)
        table.add_column("Members", style="white", width=15)
        for dialog in dialogs.dialogs[:10]:
            entity = dialog.entity
            chat_type = "Channel" if isinstance(entity, types.Channel) else "Chat"
            members = getattr(entity, 'participants_count', 'N/A')
            table.add_row(chat_type, getattr(entity, 'title', 'Unknown'), str(members))
        console.print(table)
        if len(dialogs.dialogs) > 10:
            print_message("blue", "ℹ", f"...and {len(dialogs.dialogs) - 10} more")
        
        if not Confirm.ask("[red]Delete all chats and channels?[/red]"):
            return
        
        async with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
            task = progress.add_task("[red]Deleting...", total=len(dialogs.dialogs))
            tasks = []
            for dialog in dialogs.dialogs:
                if isinstance(dialog.entity, types.Channel):
                    tasks.append(client.safe_execute(LeaveChannelRequest, channel=dialog.entity))
                else:
                    tasks.append(client.client.delete_dialog(dialog.entity))
                progress.update(task, advance=1)
            await asyncio.gather(*tasks)
        print_message("green", "✓", f"Deleted {len(dialogs.dialogs)} chats/channels")
        logger.info(f"Deleted {len(dialogs.dialogs)} chats/channels for {client.phone}")
    except Exception as e:
        print_message("red", "✗", f"Error: {e}")
        logger.error(f"Error deleting chats for {client.phone}: {e}")

async def check_spam_status():
    print_header("Check Spam Status")
    client = await select_and_login()
    if not client:
        return
    try:
        ttl = await client.safe_execute(GetAccountTTLRequest)
        test_msg = await client.safe_execute(client.client.send_message, "me", f"Spam check {datetime.now().isoformat()}")
        status = "[green]Unrestricted[/green]" if test_msg else "[red]Restricted[/red]"
        if test_msg:
            await client.safe_execute(client.client.delete_messages, "me", [test_msg.id])
        
        password_info = await client.safe_execute(GetPasswordRequest)
        has_2fa = "Yes" if password_info.has_password else "No"
        
        table = Table(title="Account Status", box=box.ROUNDED, border_style="green", width=60)
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", style="magenta", width=40)
        table.add_row("Account TTL", f"{ttl.days} days")
        table.add_row("Spam Status", status)
        table.add_row("2FA Enabled", has_2fa)
        console.print(table)
    except Exception as e:
        print_message("yellow", "⚠", f"Possible restriction: {e}")
        logger.warning(f"Spam check failed for {client.phone}: {e}")

async def read_session_otp():
    print_header("Read OTP")
    client = await select_and_login()
    if not client:
        return
    try:
        messages = await client.safe_execute(client.client.get_messages, "Telegram", limit=20)
        otps = []
        for msg in messages:
            if msg and "login code" in msg.text.lower():
                code = ''.join(filter(str.isdigit, msg.text))
                if code:
                    otps.append((code, msg.date))
        
        if otps:
            for code, date in sorted(otps, key=lambda x: x[1], reverse=True)[:3]:
                console.print(Panel(
                    f"OTP: [bold green]{code}[/bold green]\nReceived: {date.strftime('%Y-%m-%d %H:%M:%S')}",
                    title="Login Code",
                    border_style="green",
                    width=60
                ))
                logger.info(f"OTP found for {client.phone}: {code}")
        else:
            print_message("blue", "ℹ", "No recent OTPs found")
    except Exception as e:
        print_message("red", "✗", f"Failed: {e}")
        logger.error(f"Failed to read OTP for {client.phone}: {e}")

async def manage_2fa():
    print_header("2FA Management")
    client = await select_and_login()
    if not client:
        return
    
    async def get_password_hash(password: str) -> bytes:
        password_info = await client.safe_execute(GetPasswordRequest)
        if not password_info or not password_info.current_algo:
            return None
        return await asyncio.get_event_loop().run_in_executor(
            executor,
            lambda: client.client._get_password_hash(password, password_info.current_algo)
        )
    
    async def enable_2fa():
        password = getpass.getpass("Enter new 2FA password (min 8 chars): ")
        if len(password) < 8:
            print_message("red", "✗", "Password must be at least 8 characters")
            return
        confirm_password = getpass.getpass("Confirm password: ")
        if password != confirm_password:
            print_message("red", "✗", "Passwords do not match")
            return
        hint = Prompt.ask("[cyan]Enter password hint (optional)[/cyan]", default="")
        email = Prompt.ask("[cyan]Enter recovery email (optional)[/cyan]", default="")
        
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
            print_message("green", "✓", "2FA enabled successfully")
            logger.info(f"2FA enabled for {client.phone}")
        except Exception as e:
            print_message("red", "✗", f"Failed to enable 2FA: {e}")
            logger.error(f"Failed to enable 2FA for {client.phone}: {e}")
    
    async def disable_2fa():
        if not Confirm.ask("[red]Are you sure you want to disable 2FA?[/red]"):
            return
        password = getpass.getpass("Enter current 2FA password: ")
        try:
            await client.client(functions.account.UpdatePasswordSettingsRequest(
                password=await get_password_hash(password),
                new_settings=types.account.PasswordInputSettings(
                    new_algo=types.PasswordKdfAlgoUnknown(),
                    new_password_hash=b'',
                    hint=''
                )
            ))
            print_message("green", "✓", "2FA disabled successfully")
            logger.info(f"2FA disabled for {client.phone}")
        except Exception as e:
            print_message("red", "✗", f"Failed to disable 2FA: {e}")
            logger.error(f"Failed to disable 2FA for {client.phone}: {e}")
    
    async def change_2fa_password():
        current = getpass.getpass("Enter current 2FA password: ")
        new_pass = getpass.getpass("Enter new 2FA password (min 8 chars): ")
        if len(new_pass) < 8:
            print_message("red", "✗", "New password must be at least 8 characters")
            return
        confirm_new = getpass.getpass("Confirm new password: ")
        if new_pass != confirm_new:
            print_message("red", "✗", "Passwords do not match")
            return
        hint = Prompt.ask("[cyan]Enter new password hint (optional)[/cyan]", default="")
        
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
            print_message("green", "✓", "2FA password changed successfully")
            logger.info(f"2FA password changed for {client.phone}")
        except Exception as e:
            print_message("red", "✗", f"Failed to change 2FA password: {e}")
            logger.error(f"Failed to change 2FA password for {client.phone}: {e}")
    
    async def check_2fa_status():
        try:
            password_info = await client.safe_execute(GetPasswordRequest)
            table = Table(title="2FA Status", box=box.ROUNDED, border_style="cyan", width=60)
            table.add_column("Property", style="cyan", width=20)
            table.add_column("Value", style="magenta", width=40)
            table.add_row("Enabled", "Yes" if password_info.has_password else "No")
            table.add_row("Hint", password_info.hint or "None")
            table.add_row("Email", "Set" if password_info.has_recovery else "Not set")
            console.print(table)
        except Exception as e:
            print_message("red", "✗", f"Failed to check 2FA status: {e}")
            logger.error(f"Failed to check 2FA status for {client.phone}: {e}")

    menu_options = {
        "1": ("Enable 2FA", enable_2fa),
        "2": ("Disable 2FA", disable_2fa),
        "3": ("Change 2FA Password", change_2fa_password),
        "4": ("Check 2FA Status", check_2fa_status),
        "5": ("Back", lambda: None)
    }
    
    while True:
        print_header("2FA Management Menu")
        table = Table(box=box.ROUNDED, show_header=False, border_style="magenta", width=60)
        table.add_column("Option", style="cyan", width=10, justify="right")
        table.add_column("Action", style="magenta", width=50)
        for num, (desc, _) in menu_options.items():
            table.add_row(num, desc)
        console.print(table)
        
        choice = Prompt.ask("[cyan]Select option[/cyan]", choices=list(menu_options.keys()))
        if choice == "5":
            break
        await menu_options[choice][1]()

async def export_sessions():
    print_header("Export Sessions")
    sessions = await list_sessions()
    if not sessions:
        return
    try:
        export_path = f"sessions_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        async with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT phone, path, created_at, last_used, metadata, session_hash, status FROM sessions")
            data = cursor.fetchall()
        
        async with aiofiles.open(export_path, 'w', newline='') as f:
            writer = csv.writer(f)
            await writer.writerow(["Phone", "Path", "Created At", "Last Used", "Metadata", "Session Hash", "Status"])
            for row in data:
                metadata = json.dumps(json.loads(row[4]) if row[4] else {})
                await writer.writerow([row[0], row[1], row[2], row[3], metadata, row[5], row[6]])
        
        print_message("green", "✓", f"Exported {len(data)} sessions to {export_path}")
        logger.info(f"Exported {len(data)} sessions to {export_path}")
    except Exception as e:
        print_message("red", "✗", f"Failed to export: {e}")
        logger.error(f"Failed to export sessions: {e}")

async def session_statistics():
    print_header("Session Statistics")
    try:
        async with db_connection() as conn:
            cursor = conn.cursor()
            stats = {
                "Total Sessions": cursor.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "Active Sessions": cursor.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'").fetchone()[0],
                "Premium Accounts": cursor.execute("SELECT COUNT(*) FROM sessions WHERE json_extract(metadata, '$.premium') = 1").fetchone()[0],
                "Recently Used (24h)": cursor.execute("SELECT COUNT(*) FROM sessions WHERE last_used > ?", (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat(),)).fetchone()[0]
            }
        
        table = Table(title="Session Statistics", box=box.ROUNDED, border_style="blue", width=60)
        table.add_column("Metric", style="cyan", width=20)
        table.add_column("Value", style="magenta", width=40)
        for metric, value in stats.items():
            table.add_row(metric, str(value))
        console.print(table)
    except Exception as e:
        print_message("red", "✗", f"Failed: {e}")
        logger.error(f"Failed to get statistics: {e}")

async def backup_sessions():
    print_header("Backup Sessions")
    sessions = await list_sessions()
    if not sessions:
        return
    
    backup_dir = config.SESSION_FOLDER / "backups" / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(exist_ok=True)
    
    try:
        async with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
            task = progress.add_task("[green]Backing up...", total=len(sessions))
            for session in sessions:
                dest = backup_dir / os.path.basename(session)
                async with aiofiles.open(session, 'rb') as src, aiofiles.open(dest, 'wb') as dst:
                    await dst.write(await src.read())
                os.chmod(dest, 0o600)
                progress.update(task, advance=1)
        
        print_message("green", "✓", f"Backed up {len(sessions)} sessions to {backup_dir}")
        logger.info(f"Backed up {len(sessions)} sessions to {backup_dir}")
    except Exception as e:
        print_message("red", "✗", f"Failed: {e}")
        logger.error(f"Failed to backup sessions: {e}")

async def cleanup_sessions():
    print_header("Cleanup Sessions")
    sessions = [str(p) for p in config.SESSION_FOLDER.glob("*.session")]
    if not sessions:
        print_message("blue", "ℹ", "No sessions to clean")
        return
    
    async with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM sessions WHERE status = 'active'")
        db_paths = set(row[0] for row in cursor.fetchall())
    
    orphaned = [s for s in sessions if s not in db_paths]
    if not orphaned:
        print_message("blue", "ℹ", "No orphaned sessions found")
        return
    
    table = Table(title=f"Orphaned Sessions ({len(orphaned)})", box=box.ROUNDED, border_style="yellow", width=60)
    table.add_column("File", style="yellow", width=40)
    table.add_column("Size (KB)", style="white", width=20)
    for session in orphaned[:10]:
        size = os.path.getsize(session) / 1024
        table.add_row(os.path.basename(session), f"{size:.2f}")
    console.print(table)
    if len(orphaned) > 10:
        print_message("blue", "ℹ", f"...and {len(orphaned) - 10} more")
    
    if not Confirm.ask("[red]Delete all orphaned sessions?[/red]"):
        return
    
    async with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("[red]Cleaning...", total=len(orphaned))
        for session in orphaned:
            await asyncio.get_event_loop().run_in_executor(executor, os.remove, session)
            progress.update(task, advance=1)
    
    print_message("green", "✓", f"Cleaned up {len(orphaned)} orphaned sessions")
    logger.info(f"Cleaned up {len(orphaned)} orphaned sessions")

async def bulk_session_check():
    print_header("Bulk Session Check")
    sessions = await list_sessions()
    if not sessions:
        return
    
    async def check_session(session: str) -> Dict[str, Any]:
        phone = f"+{os.path.basename(session).replace('.session', '')}"
        async with AdvancedTelegramClient(session, phone) as client:
            try:
                connected = await client.connect()
                return {
                    "phone": phone,
                    "status": "[green]Healthy[/green]" if connected else "[red]Invalid[/red]",
                    "name": client._me.first_name if connected else "N/A"
                }
            except Exception as e:
                return {"phone": phone, "status": f"[red]Error: {e}[/red]", "name": "N/A"}
    
    async with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("[cyan]Checking...", total=len(sessions))
        results = await asyncio.gather(*[check_session(s) for s in sessions])
        progress.update(task, completed=len(sessions))
    
    table = Table(title="Session Health", box=box.ROUNDED, border_style="cyan", width=60)
    table.add_column("Phone", style="magenta", width=15)
    table.add_column("Status", style="green", width=25)
    table.add_column("Name", style="cyan", width=20)
    for result in results:
        table.add_row(result["phone"], result["status"], result["name"])
    console.print(table)
    
    unhealthy = [r["phone"] for r in results if "Healthy" not in r["status"]]
    if unhealthy and Confirm.ask("[yellow]Mark unhealthy sessions as inactive?[/yellow]"):
        async with db_connection() as conn:
            conn.executemany("UPDATE sessions SET status = 'inactive' WHERE phone = ?", [(p,) for p in unhealthy])
        print_message("green", "✓", f"Marked {len(unhealthy)} sessions as inactive")
        logger.info(f"Marked {len(unhealthy)} sessions as inactive")

async def add_session_note():
    print_header("Add Session Note")
    sessions = await list_sessions()
    if not sessions:
        return
    
    choice = Prompt.ask(f"[cyan]Select session (1-{len(sessions)}, 'q' to quit)[/cyan]", default="q")
    if choice.lower() == 'q':
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            phone = f"+{os.path.basename(sessions[idx]).replace('.session', '')}"
            note = Prompt.ask("[cyan]Enter note for this session[/cyan]")
            async with db_connection() as conn:
                conn.execute("UPDATE sessions SET notes = ? WHERE phone = ?", (note, phone))
            print_message("green", "✓", f"Added note to {phone}")
            logger.info(f"Added note to {phone}: {note}")
        else:
            print_message("red", "✗", f"Invalid choice (1-{len(sessions)})")
    except ValueError:
        print_message("red", "✗", "Invalid input")

async def view_session_notes():
    print_header("View Session Notes")
    async with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone, notes FROM sessions WHERE notes IS NOT NULL")
        notes = cursor.fetchall()
    
    if not notes:
        print_message("blue", "ℹ", "No notes found")
        return
    
    table = Table(title="Session Notes", box=box.ROUNDED, border_style="blue", width=60)
    table.add_column("Phone", style="magenta", width=15)
    table.add_column("Note", style="white", width=45)
    for phone, note in notes:
        table.add_row(phone, note)
    console.print(table)

async def delete_session():
    print_header("Delete Session")
    sessions = await list_sessions()
    if not sessions:
        return
    
    choice = Prompt.ask(f"[cyan]Select session to delete (1-{len(sessions)}, 'q' to quit)[/cyan]", default="q")
    if choice.lower() == 'q':
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(sessions):
            phone = f"+{os.path.basename(sessions[idx]).replace('.session', '')}"
            if Confirm.ask(f"[red]Delete session {phone}? This cannot be undone![/red]"):
                async with db_connection() as conn:
                    conn.execute("DELETE FROM sessions WHERE phone = ?", (phone,))
                os.remove(sessions[idx])
                print_message("green", "✓", f"Deleted session {phone}")
                logger.info(f"Deleted session {phone}")
        else:
            print_message("red", "✗", f"Invalid choice (1-{len(sessions)})")
    except ValueError:
        print_message("red", "✗", "Invalid input")

def create_main_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3)
    )
    layout["main"].split_row(
        Layout(name="menu", ratio=1),
        Layout(name="status", ratio=1)
    )
    return layout

async def main():
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", list_sessions),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Show Active Sessions", show_active_sessions),
        "5": ("Update Profile", update_profile_random_name),
        "6": ("Clear Contacts", clear_contacts),
        "7": ("Delete Chats/Channels", delete_all_chats_advanced),
        "8": ("Check Spam Status", check_spam_status),
        "9": ("Read OTP", read_session_otp),
        "10": ("2FA Management", manage_2fa),
        "11": ("Export Sessions", export_sessions),
        "12": ("Session Statistics", session_statistics),
        "13": ("Backup Sessions", backup_sessions),
        "14": ("Cleanup Sessions", cleanup_sessions),
        "15": ("Bulk Session Check", bulk_session_check),
        "16": ("Add Session Note", add_session_note),
        "17": ("View Session Notes", view_session_notes),
        "18": ("Delete Session", delete_session),
        "19": ("Exit", lambda: None)
    }
    
    layout = create_main_layout()
    status_messages = []
    
    async def update_header():
        async with db_connection() as conn:
            total_sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE status = 'active'").fetchone()[0]
        layout["header"].update(Panel(
            f"[bold cyan]Telegram Session Manager[/bold cyan] | Active Sessions: {total_sessions}",
            border_style="blue",
            subtitle=f"v{VERSION} | Telethon {telethon_version}",
            subtitle_align="right"
        ))
    
    async def update_footer():
        layout["footer"].update(Panel(
            f"[blue]Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Ctrl+C to Exit | Log: logs/session_manager.log[/blue]",
            border_style="blue"
        ))
    
    async def update_status():
        status_table = Table(box=box.MINIMAL, show_header=False, width=30)
        status_table.add_column("Status", style="white")
        for msg in status_messages[-5:]:
            status_table.add_row(Text(msg, overflow="fold"))
        layout["status"].update(Panel(status_table, title="Recent Activity", border_style="green"))

    def add_status_message(style: str, message: str):
        status_messages.append(f"[{style}]{message}[/{style}]")
    
    global print_message
    def print_message(style: str, symbol: str, message: str):
        full_message = f"{symbol} {message}"
        console.print(f"[{style}]{full_message}[/{style}]", width=60)
        add_status_message(style, full_message)

    while True:
        await update_header()
        menu_table = Table(box=box.ROUNDED, header_style="bold magenta", border_style="magenta", width=30)
        menu_table.add_column("Opt", style="cyan", width=5, justify="right")
        menu_table.add_column("Action", style="magenta", width=25)
        for num, (action, _) in menu_options.items():
            menu_table.add_row(num, action)
        layout["menu"].update(menu_table)
        
        await update_status()
        with Live(layout, console=console, refresh_per_second=1):
            await update_footer()
            choice = Prompt.ask("[cyan]Select option (1-19)[/cyan]", choices=list(menu_options.keys()))
        
        if choice == "19":
            print_message("green", "✓", "Goodbye!")
            break
        await menu_options[choice][1]()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[red]✗ Operation cancelled[/red]")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        console.print(f"[red]✗ Fatal error: {e}[/red]")
        sys.exit(1)
    finally:
        executor.shutdown(wait=False)