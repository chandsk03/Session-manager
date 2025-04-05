import os
import asyncio
import getpass
import random
import glob
from telethon import TelegramClient, functions, types
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdateProfileRequest,
    GetAccountTTLRequest
)
from telethon.tl.functions.contacts import (
    DeleteContactsRequest,
    GetContactsRequest
)
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberInvalidError,
    AuthKeyError,
    RPCError
)
from colorama import Fore, Style, init
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.tree import Tree
from rich import box
from rich.text import Text
import time
from datetime import datetime
import platform
from typing import List, Optional, Dict, Any, Tuple
import socket
import hashlib

# Initialize colorama and rich console
init(autoreset=True)
console = Console()

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

# API credentials
API_ID = int(os.getenv("TELEGRAM_API_ID", "23077946"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")

# Session folder
SESSION_FOLDER = "sessions"
if not os.path.exists(SESSION_FOLDER):
    os.makedirs(SESSION_FOLDER)

# Device information
DEVICE_INFO = {
    "device_model": "Telegram Session Manager Pro",
    "system_version": platform.platform(),
    "app_version": "3.0",
    "lang_code": "en",
    "system_lang_code": "en-US"
}

# Rate limiting configuration
RATE_LIMIT = {
    "contacts": 1.2,  # seconds between operations
    "chats": 1.5,
    "messages": 0.8
}

def get_session_path(phone: str) -> str:
    """Generate session file path from phone number"""
    sanitized = ''.join(c for c in phone if c.isdigit() or c == '+')
    return os.path.join(SESSION_FOLDER, f"{sanitized.replace('+', '')}.session")

def print_header(title: str) -> None:
    """Print formatted header with rich"""
    console.print(Panel.fit(title, style="bold cyan", border_style="blue"))

def print_success(message: str) -> None:
    """Print success message"""
    console.print(f"[bold green]✓ {message}[/bold green]")

def print_error(message: str) -> None:
    """Print error message"""
    console.print(f"[bold red]✗ {message}[/bold red]")

def print_warning(message: str) -> None:
    """Print warning message"""
    console.print(f"[bold yellow]⚠ {message}[/bold yellow]")

def print_info(message: str) -> None:
    """Print info message"""
    console.print(f"[bold blue]ℹ {message}[/bold blue]")

def validate_phone(phone: str) -> bool:
    """Validate phone number format"""
    if not phone.startswith('+'):
        return False
    digits = phone[1:]
    return digits.isdigit() and len(digits) >= 8

async def login_session(session_path: str) -> Optional[TelegramClient]:
    """Advanced session login with error handling and device spoofing"""
    try:
        client = TelegramClient(
            session_path,
            API_ID,
            API_HASH,
            device_model=DEVICE_INFO["device_model"],
            system_version=DEVICE_INFO["system_version"],
            app_version=DEVICE_INFO["app_version"],
            lang_code=DEVICE_INFO["lang_code"],
            system_lang_code=DEVICE_INFO["system_lang_code"],
            request_retries=5,
            connection_retries=5,
            auto_reconnect=True
        )
        
        await client.connect()
        
        if not await client.is_user_authorized():
            print_error("Session not authorized. Please create a new session.")
            await client.disconnect()
            return None
            
        session_name = os.path.basename(session_path)
        me = await client.get_me()
        print_success(f"Logged in as [bold]{me.first_name or 'Unknown'}[/bold] (ID: {me.id})")
        return client
        
    except AuthKeyError:
        print_error("Session expired or invalid. Please create a new session.")
        try:
            os.remove(session_path)
        except:
            pass
        return None
    except Exception as e:
        print_error(f"Login failed: {str(e)}")
        return None

async def create_session() -> Optional[str]:
    """Create a new Telegram session with enhanced validation"""
    print_header("Create New Session")
    
    while True:
        phone = console.input("[bold cyan]Enter phone number (e.g., +12345678900): [/bold cyan]").strip()
        if not validate_phone(phone):
            print_error("Invalid phone number format. Must start with '+' followed by digits")
            continue
        break
    
    session_path = get_session_path(phone)
    if os.path.exists(session_path):
        print_warning(f"Session already exists for +{phone.replace('+', '')}")
        return session_path
    
    client = TelegramClient(
        session_path,
        API_ID,
        API_HASH,
        device_model=DEVICE_INFO["device_model"],
        system_version=DEVICE_INFO["system_version"],
        app_version=DEVICE_INFO["app_version"],
        lang_code=DEVICE_INFO["lang_code"],
        system_lang_code=DEVICE_INFO["system_lang_code"]
    )
    
    try:
        await client.connect()
        
        if await client.is_user_authorized():
            print_success(f"Session already authorized for +{phone.replace('+', '')}")
            return session_path
            
        print_info(f"Sending code to +{phone.replace('+', '')}...")
        
        with console.status("[bold blue]Sending code...[/bold blue]", spinner="dots"):
            sent_code = await client.send_code_request(phone)
        
        code = console.input("[bold yellow]Enter code (or 'q' to quit): [/bold yellow]").strip()
        if code.lower() == 'q':
            await client.disconnect()
            os.remove(session_path)
            return None
            
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = getpass.getpass("[bold red]Enter 2FA password: [/bold red]")
            await client.sign_in(password=password)
        
        me = await client.get_me()
        print_success(f"Session created for [bold]{me.first_name} {me.last_name or ''}[/bold] (+{me.phone})")
        return session_path
        
    except PhoneNumberInvalidError:
        print_error("Invalid phone number format")
    except Exception as e:
        print_error(f"Failed to create session: {str(e)}")
        if os.path.exists(session_path):
            try:
                os.remove(session_path)
            except:
                pass
    finally:
        await client.disconnect()
    return None

def list_sessions(country_code: Optional[str] = None) -> Optional[List[str]]:
    """List all saved session files with rich table"""
    sessions_pattern = os.path.join(SESSION_FOLDER, "*.session")
    sessions = glob.glob(sessions_pattern)
    
    if country_code:
        country_code = country_code.replace('+', '')
        sessions = [s for s in sessions if os.path.basename(s).startswith(country_code)]
    
    if not sessions:
        print_warning("No saved sessions found")
        return None
    
    table = Table(title="Available Sessions", box=box.ROUNDED)
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Phone Number", style="magenta")
    table.add_column("Session File", style="yellow")
    table.add_column("Size", style="green")
    table.add_column("Last Modified", style="blue")
    
    for i, session in enumerate(sessions, 1):
        session_name = os.path.basename(session).replace('.session', '')
        size = os.path.getsize(session) / 1024  # KB
        mtime = datetime.fromtimestamp(os.path.getmtime(session))
        table.add_row(
            str(i),
            f"+{session_name}",
            os.path.basename(session),
            f"{size:.2f} KB",
            mtime.strftime('%Y-%m-%d %H:%M')
        )
    
    console.print(table)
    return sessions

async def select_and_login() -> Optional[TelegramClient]:
    """Select a session and login with retry logic"""
    sessions = list_sessions()
    if not sessions:
        return None
    
    while True:
        try:
            choice = console.input(
                f"[bold cyan]Select session (1-{len(sessions)}): [/bold cyan]"
            ).strip()
            
            if not choice:
                print_error("Selection cannot be empty")
                continue
                
            choice = int(choice) - 1
            if 0 <= choice < len(sessions):
                session_path = sessions[choice]
                
                with console.status("[bold blue]Connecting...[/bold blue]", spinner="dots"):
                    client = await login_session(session_path)
                
                if client:
                    return client
                
                print_warning("Login failed, please try another session")
                continue
                
            print_error(f"Invalid selection. Choose between 1 and {len(sessions)}")
        except ValueError:
            print_error("Please enter a valid number")

async def terminate_other_sessions() -> None:
    """Terminate all sessions except current one with confirmation"""
    print_header("Terminate Other Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        with console.status("[bold blue]Fetching active sessions...[/bold blue]", spinner="dots"):
            auths = await client(GetAuthorizationsRequest())
        
        if not auths.authorizations:
            print_info("No other active sessions found")
            return
            
        current_session = next((a for a in auths.authorizations if a.current), None)
        other_sessions = [a for a in auths.authorizations if not a.current]
        
        # Display session info
        tree = Tree("[bold cyan]Active Sessions[/bold cyan]")
        
        current_branch = tree.add(f"[green]Current Session[/green]")
        current_branch.add(f"Device: {current_session.device_model}")
        current_branch.add(f"IP: {current_session.ip}")
        current_branch.add(f"Location: {current_session.country}")
        current_branch.add(f"Created: {current_session.date_created.strftime('%Y-%m-%d %H:%M:%S')}")
        
        other_branch = tree.add(f"[red]Other Sessions ({len(other_sessions)})[/red]")
        for auth in other_sessions[:3]:  # Show first 3 for brevity
            other_branch.add(
                f"{auth.device_model} ({auth.ip}) - Last active: {auth.date_active.strftime('%Y-%m-%d %H:%M:%S')}"
            )
        if len(other_sessions) > 3:
            other_branch.add(f"...and {len(other_sessions)-3} more")
        
        console.print(tree)
        
        confirm = console.input("[bold red]Confirm termination of ALL other sessions? (y/n): [/bold red]").strip().lower()
        if confirm != 'y':
            print_info("Operation cancelled")
            return
            
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        ) as progress:
            task = progress.add_task("Terminating sessions...", total=len(other_sessions))
            
            for auth in other_sessions:
                try:
                    await client(ResetAuthorizationRequest(hash=auth.hash))
                    progress.update(task, advance=1)
                except Exception as e:
                    print_warning(f"Failed to terminate session {auth.hash}: {str(e)}")
        
        print_success(f"Terminated {len(other_sessions)} other sessions")
    except Exception as e:
        print_error(f"Failed to terminate sessions: {str(e)}")
    finally:
        await client.disconnect()

async def show_active_sessions() -> None:
    """Show detailed info about active sessions"""
    print_header("Active Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        with console.status("[bold blue]Fetching active sessions...[/bold blue]", spinner="dots"):
            auths = await client(GetAuthorizationsRequest())
        
        if not auths.authorizations:
            print_info("No active sessions found")
            return
            
        table = Table(title=f"Active Sessions ({len(auths.authorizations)})", box=box.ROUNDED)
        table.add_column("Status", style="bold")
        table.add_column("Device", style="cyan")
        table.add_column("IP/Location", style="magenta")
        table.add_column("Last Active", style="green")
        table.add_column("Created", style="yellow")
        
        for auth in auths.authorizations:
            status = "[green]Current[/green]" if auth.current else "[red]Other[/red]"
            table.add_row(
                status,
                f"{auth.device_model}\n{auth.platform}",
                f"{auth.ip}\n{auth.country}",
                auth.date_active.strftime('%Y-%m-%d %H:%M:%S'),
                auth.date_created.strftime('%Y-%m-%d %H:%M:%S')
            )
        
        console.print(table)
    except Exception as e:
        print_error(f"Error fetching sessions: {str(e)}")
    finally:
        await client.disconnect()

async def update_profile_random_name() -> None:
    """Update profile with random name"""
    print_header("Update Profile")
    client = await select_and_login()
    if not client:
        return
    
    try:
        me = await client.get_me()
        old_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        
        # Generate random name with emoji
        emojis = ["🚀", "🌟", "🔥", "💻", "🦾", "🤖", "👾", "🛸"]
        adjectives = ["Cyber", "Digital", "Quantum", "Neon", "Phantom", "Stealth"]
        nouns = ["Hacker", "Agent", "Ghost", "Ninja", "Samurai", "Wizard"]
        
        new_first = f"{random.choice(adjectives)}{random.choice(nouns)}"
        new_last = random.choice(emojis)
        
        with console.status("[bold blue]Updating profile...[/bold blue]", spinner="dots"):
            await client(UpdateProfileRequest(
                first_name=new_first,
                last_name=new_last,
                about=f"Updated by Telegram Session Manager at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ))
        
        print_success(f"Profile updated from '[bold]{old_name}[/bold]' to '[bold]{new_first} {new_last}[/bold]'")
    except Exception as e:
        print_error(f"Failed to update profile: {str(e)}")
    finally:
        await client.disconnect()

async def clear_contacts() -> None:
    """Clear all contacts with confirmation"""
    print_header("Clear Contacts")
    client = await select_and_login()
    if not client:
        return
    
    try:
        with console.status("[bold blue]Fetching contacts...[/bold blue]", spinner="dots"):
            contacts = await client(GetContactsRequest(hash=0))
            contact_list = contacts.contacts if hasattr(contacts, 'contacts') else []
        
        if not contact_list:
            print_info("No contacts to clear")
            return
            
        # Display contacts in a table
        table = Table(title=f"Found {len(contact_list)} Contacts", box=box.ROUNDED)
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Phone", style="green")
        
        for contact in contact_list[:10]:  # Show first 10 contacts
            user = next((u for u in contacts.users if u.id == contact.user_id), None)
            if user:
                name = f"{user.first_name or ''} {user.last_name or ''}".strip()
                phone = getattr(user, 'phone', 'N/A')
                table.add_row(str(user.id), name, phone)
        
        console.print(table)
        if len(contact_list) > 10:
            console.print(f"[yellow]...and {len(contact_list)-10} more contacts[/yellow]")
            
        confirm = console.input("[bold red]Delete ALL contacts? (y/n): [/bold red]").strip().lower()
        if confirm != 'y':
            print_info("Operation cancelled")
            return
            
        # Delete contacts with progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        ) as progress:
            task = progress.add_task("[red]Deleting contacts...[/red]", total=len(contact_list))
            
            # Batch delete to avoid flooding
            batch_size = 100
            for i in range(0, len(contact_list), batch_size):
                batch = contact_list[i:i+batch_size]
                await client(DeleteContactsRequest(id=[c.user_id for c in batch]))
                progress.update(task, advance=len(batch))
                await asyncio.sleep(RATE_LIMIT["contacts"])  # Rate limiting
        
        print_success(f"Deleted {len(contact_list)} contacts")
    except Exception as e:
        print_error(f"Failed to clear contacts: {str(e)}")
    finally:
        await client.disconnect()

async def get_dialogs_advanced(client: TelegramClient) -> List[types.Dialog]:
    """Advanced dialog fetching with pagination"""
    dialogs = []
    offset_date = None
    offset_id = 0
    limit = 100
    
    with console.status("[bold blue]Fetching dialogs...[/bold blue]", spinner="dots"):
        while True:
            result = await client(GetDialogsRequest(
                offset_date=offset_date,
                offset_id=offset_id,
                offset_peer=types.InputPeerEmpty(),
                limit=limit,
                hash=0
            ))
            
            if not result.dialogs:
                break
                
            dialogs.extend(result.dialogs)
            
            if len(result.dialogs) < limit:
                break
                
            last_dialog = result.dialogs[-1]
            offset_date = last_dialog.date
            offset_id = last_dialog.id
            
    return dialogs

async def delete_all_chats_advanced() -> None:
    """Advanced chat deletion with GetDialogsRequest"""
    print_header("Advanced Chat Deletion")
    client = await select_and_login()
    if not client:
        return
    
    try:
        dialogs = await get_dialogs_advanced(client)
        
        # Filter out special chats and empty dialogs
        dialogs = [
            d for d in dialogs 
            if d.id not in (777000, 429000) 
            and hasattr(d, 'entity')
        ]
        
        if not dialogs:
            print_info("No chats/channels found")
            return
            
        # Display chat info
        table = Table(title=f"Found {len(dialogs)} Chats/Channels", box=box.ROUNDED)
        table.add_column("Type", style="cyan")
        table.add_column("Title", style="magenta")
        table.add_column("ID", style="green")
        table.add_column("Messages", style="yellow")
        
        for dialog in dialogs[:10]:  # Show first 10
            chat_type = "Channel" if isinstance(dialog.entity, types.Channel) else "Chat"
            table.add_row(
                chat_type,
                getattr(dialog.entity, 'title', 'Unknown'),
                str(dialog.entity.id),
                str(dialog.top_message)
            )
        
        console.print(table)
        if len(dialogs) > 10:
            console.print(f"[yellow]...and {len(dialogs)-10} more chats/channels[/yellow]")
            
        confirm = console.input("[bold red]Delete ALL chats/channels? (y/n): [/bold red]").strip().lower()
        if confirm != 'y':
            print_info("Operation cancelled")
            return
            
        # Delete with progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        ) as progress:
            task = progress.add_task("[red]Deleting chats...[/red]", total=len(dialogs))
            
            for dialog in dialogs:
                try:
                    if isinstance(dialog.entity, types.Channel):
                        await client(LeaveChannelRequest(dialog.entity))
                    else:
                        await client.delete_dialog(dialog.entity)
                    progress.update(task, advance=1)
                    await asyncio.sleep(RATE_LIMIT["chats"])  # Rate limiting
                except Exception as e:
                    print_warning(f"Failed to delete {getattr(dialog.entity, 'title', 'Unknown')}: {str(e)}")
        
        print_success(f"Deleted {len(dialogs)} chats/channels")
    except Exception as e:
        print_error(f"Error: {str(e)}")
    finally:
        await client.disconnect()

async def check_spam_status() -> None:
    """Check if account is spam-restricted"""
    print_header("Check Spam Status")
    client = await select_and_login()
    if not client:
        return
    
    try:
        with console.status("[bold blue]Checking status...[/bold blue]", spinner="dots"):
            ttl = await client(GetAccountTTLRequest())
            try:
                test_msg = await client.send_message("me", "Spam check message")
                await client.delete_messages("me", [test_msg.id])
                restricted = False
            except Exception:
                restricted = True
        
        table = Table(box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")
        
        table.add_row("Account TTL", f"{ttl.days} days until deletion if inactive")
        table.add_row(
            "Spam Status",
            "[red]RESTRICTED[/red]" if restricted else "[green]UNRESTRICTED[/green]"
        )
        
        console.print(table)
    except Exception as e:
        print_error(f"Error checking status: {str(e)}")
    finally:
        await client.disconnect()

async def read_session_otp() -> None:
    """Read latest OTP from Telegram messages"""
    print_header("Read OTP")
    client = await select_and_login()
    if not client:
        return
    
    try:
        with console.status("[bold blue]Checking messages...[/bold blue]", spinner="dots"):
            messages = await client.get_messages("Telegram", limit=10)
        
        for msg in messages:
            if "login code" in msg.text.lower():
                code = ''.join(filter(str.isdigit, msg.text))
                panel = Panel(
                    f"[bold]Code:[/bold] [green]{code}[/green]\n"
                    f"[bold]Message:[/bold] {msg.text.splitlines()[0]}\n"
                    f"[bold]Received:[/bold] {msg.date.strftime('%Y-%m-%d %H:%M:%S')}",
                    title="OTP Found",
                    border_style="green"
                )
                console.print(panel)
                return
        
        print_info("No recent OTP messages found")
    except Exception as e:
        print_error(f"Failed to read OTP: {str(e)}")
    finally:
        await client.disconnect()

async def get_random_session_by_country() -> Optional[str]:
    """Get random session by country code"""
    print_header("Random Session by Country")
    country_code = console.input("[bold cyan]Enter country code (e.g., +91): [/bold cyan]").strip()
    if not country_code.startswith('+'):
        print_error("Country code must start with '+'")
        return None
    
    sessions = list_sessions(country_code)
    if not sessions:
        return None
        
    session = random.choice(sessions)
    print_success(f"Selected: +{os.path.basename(session).replace('.session', '')}")
    return session

async def safe_execute(func) -> Any:
    """Execute function with advanced error handling"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return await func()
        except FloodWaitError as e:
            wait_time = min(e.seconds, 3600)  # Max 1 hour wait
            print_warning(f"Flood wait: {wait_time} seconds (Attempt {attempt + 1}/{max_attempts})")
            await asyncio.sleep(wait_time)
        except RPCError as e:
            print_error(f"Telegram RPC error: {str(e)}")
            if attempt == max_attempts - 1:
                return None
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            print_error(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_attempts - 1:
                return None
            await asyncio.sleep(1)
    return None

async def main() -> None:
    """Main menu with advanced options"""
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", lambda: safe_execute(list_sessions)),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Show Active Sessions", show_active_sessions),
        "5": ("Update Profile", update_profile_random_name),
        "6": ("Clear Contacts", clear_contacts),
        "7": ("Advanced Chat Deletion", delete_all_chats_advanced),
        "8": ("Check Spam Status", check_spam_status),
        "9": ("Read OTP", read_session_otp),
        "10": ("Random Session by Country", get_random_session_by_country),
        "11": ("Exit", None)
    }
    
    while True:
        print_header("Telegram Advanced Session Manager")
        
        # Create menu table
        menu_table = Table.grid(padding=(1, 3))
        menu_table.add_column(style="cyan", justify="right")
        menu_table.add_column(style="magenta")
        
        for num, (desc, _) in menu_options.items():
            menu_table.add_row(num, desc)
        
        console.print(menu_table)
        
        choice = console.input("[bold cyan]Select option (1-11): [/bold cyan]").strip()
        
        if choice in menu_options:
            if choice == "11":
                print_success("Goodbye!")
                break
            elif choice == "2":
                await menu_options[choice][1]()  # No safe_execute for list_sessions
            else:
                await safe_execute(menu_options[choice][1])
        else:
            print_error("Invalid choice! Please select 1-11")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_error("\nOperation cancelled by user")
    except Exception as e:
        print_error(f"Fatal error: {str(e)}")