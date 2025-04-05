import os
import asyncio
import getpass
import random
import glob
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
from telethon.tl.functions.account import UpdateProfileRequest, GetAccountTTLRequest
from telethon.tl.functions.contacts import DeleteContactsRequest
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.types import PeerChat, PeerChannel
from colorama import Fore, Style, init
import time
from datetime import datetime

# Initialize colorama
init(autoreset=True)

# Custom Theme Configuration
class Theme:
    PRIMARY = Fore.CYAN
    SUCCESS = Fore.GREEN
    ERROR = Fore.RED
    WARNING = Fore.YELLOW
    INFO = Fore.BLUE
    MENU = Fore.MAGENTA
    RESET = Style.RESET_ALL
    TITLE = Style.BRIGHT + Fore.CYAN
    BOLD = Style.BRIGHT
    HIGHLIGHT = Style.BRIGHT + Fore.YELLOW

# API credentials
API_ID = int(os.getenv("TELEGRAM_API_ID", "23077946"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")

# Session folder
SESSION_FOLDER = "sessions"
if not os.path.exists(SESSION_FOLDER):
    os.makedirs(SESSION_FOLDER)

def get_session_path(phone):
    """Generate session file path from phone number"""
    return os.path.join(SESSION_FOLDER, f"{phone.replace('+', '')}.session")

def print_header(title):
    """Print formatted header"""
    print(f"\n{Theme.TITLE}=== {title} ==={Theme.RESET}")

def print_success(message):
    """Print success message"""
    print(f"{Theme.SUCCESS}✅ {message}{Theme.RESET}")

def print_error(message):
    """Print error message"""
    print(f"{Theme.ERROR}❌ {message}{Theme.RESET}")

def print_warning(message):
    """Print warning message"""
    print(f"{Theme.WARNING}⚠️ {message}{Theme.RESET}")

def print_info(message):
    """Print info message"""
    print(f"{Theme.INFO}ℹ️ {message}{Theme.RESET}")

async def login_session(session_path):
    """Login to a session with improved error handling"""
    try:
        client = TelegramClient(session_path, API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            print_error("Session not authorized. Please create a new session.")
            await client.disconnect()
            return None
            
        session_name = os.path.basename(session_path)
        print_success(f"Logged in as {session_name}")
        return client
        
    except Exception as e:
        print_error(f"Login failed: {str(e)}")
        return None

def list_sessions(country_code=None):
    """List all saved session files with pretty formatting"""
    sessions_pattern = os.path.join(SESSION_FOLDER, "*.session")
    sessions = glob.glob(sessions_pattern)
    
    if country_code:
        country_code = country_code.replace('+', '')
        sessions = [s for s in sessions if os.path.basename(s).startswith(country_code)]
    
    if not sessions:
        print_warning("No saved sessions found")
        return None
    
    print_header("Available Sessions")
    for i, session in enumerate(sessions, 1):
        session_name = os.path.basename(session).replace('.session', '')
        print(f"{Theme.HIGHLIGHT}{i}. +{session_name}{Theme.RESET}")
    return sessions

async def select_and_login():
    """Select a session and login with retry logic"""
    sessions = list_sessions()
    if not sessions:
        return None
    
    while True:
        try:
            choice = input(f"{Theme.PRIMARY}Select session (1-{len(sessions)}): {Theme.RESET}").strip()
            if not choice:
                print_error("Selection cannot be empty")
                continue
                
            choice = int(choice) - 1
            if 0 <= choice < len(sessions):
                session_path = sessions[choice]
                client = await login_session(session_path)
                if client:
                    return client
                print_warning("Login failed, please try another session")
                continue
                
            print_error(f"Invalid selection. Choose between 1 and {len(sessions)}")
        except ValueError:
            print_error("Please enter a valid number")

async def create_session():
    """Create a new Telegram session with enhanced validation"""
    print_header("Create New Session")
    
    while True:
        phone = input(f"{Theme.PRIMARY}Enter phone number (e.g., +12345678900): {Theme.RESET}").strip()
        if not phone:
            print_error("Phone number cannot be empty")
            continue
        if not phone.startswith('+'):
            print_error("Invalid format. Must start with '+'")
            continue
        break
    
    session_path = get_session_path(phone)
    if os.path.exists(session_path):
        print_warning(f"Session already exists for +{phone.replace('+', '')}")
        return session_path
    
    client = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await client.connect()
        
        if await client.is_user_authorized():
            print_success(f"Session already authorized for +{phone.replace('+', '')}")
            return session_path
            
        print_info(f"Sending code to +{phone.replace('+', '')}...")
        await client.send_code_request(phone)
        
        while True:
            code = input(f"{Theme.WARNING}Enter code (or 'q' to quit): {Theme.RESET}").strip()
            if code.lower() == 'q':
                await client.disconnect()
                os.remove(session_path)
                return None
                
            try:
                await client.sign_in(phone, code)
                break
            except SessionPasswordNeededError:
                password = getpass.getpass(f"{Theme.ERROR}Enter 2FA password: {Theme.RESET}")
                await client.sign_in(password=password)
                break
            except Exception as e:
                print_error(f"Invalid code: {str(e)}")
                continue
                
        print_success(f"Session created for +{phone.replace('+', '')}")
        return session_path
        
    except Exception as e:
        print_error(f"Failed to create session: {str(e)}")
        if os.path.exists(session_path):
            os.remove(session_path)
        return None
    finally:
        await client.disconnect()

async def terminate_other_sessions():
    """Terminate all sessions except current one with confirmation"""
    print_header("Terminate Other Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        auths = await client(GetAuthorizationsRequest())
        if not auths.authorizations:
            print_info("No other active sessions found")
            return
            
        current_session = next((a for a in auths.authorizations if a.current), None)
        other_sessions = [a for a in auths.authorizations if not a.current]
        
        print_header("Active Sessions")
        print(f"{Theme.INFO}Current session:")
        print(f"  Device: {current_session.device_model}")
        print(f"  IP: {current_session.ip}")
        print(f"  Last active: {current_session.date_active.strftime('%Y-%m-%d %H:%M:%S')}")
        
        print(f"\n{Theme.WARNING}Other sessions to terminate ({len(other_sessions)}):")
        for i, auth in enumerate(other_sessions, 1):
            print(f"  {i}. Device: {auth.device_model} ({auth.ip})")
        
        confirm = input(f"\n{Theme.WARNING}Confirm termination? (y/n): {Theme.RESET}").strip().lower()
        if confirm != 'y':
            print_info("Operation cancelled")
            return
            
        for auth in other_sessions:
            try:
                await client(ResetAuthorizationRequest(hash=auth.hash))
                print_success(f"Terminated session from {auth.device_model}")
            except Exception as e:
                print_error(f"Failed to terminate session: {str(e)}")
                
        print_success("All other sessions terminated")
    except Exception as e:
        print_error(f"Failed to terminate sessions: {str(e)}")
    finally:
        await client.disconnect()

async def show_active_sessions():
    """Show detailed info about active sessions"""
    print_header("Active Sessions")
    client = await select_and_login()
    if not client:
        return
    
    try:
        auths = await client(GetAuthorizationsRequest())
        if not auths.authorizations:
            print_info("No active sessions found")
            return
            
        print(f"{Theme.INFO}Active Sessions ({len(auths.authorizations)}):")
        for i, auth in enumerate(auths.authorizations, 1):
            status = "CURRENT" if auth.current else "Other"
            color = Theme.SUCCESS if auth.current else Theme.INFO
            print(f"{color}{i}. {status} session:")
            print(f"   Device: {auth.device_model} ({auth.platform})")
            print(f"   IP: {auth.ip} ({auth.country})")
            print(f"   App: {auth.app_name} v{auth.app_version}")
            print(f"   Last Active: {auth.date_active.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"   Created: {auth.date_created.strftime('%Y-%m-%d %H:%M:%S')}{Theme.RESET}")
    except Exception as e:
        print_error(f"Error fetching sessions: {str(e)}")
    finally:
        await client.disconnect()

async def update_profile_random_name():
    """Update profile with random name"""
    print_header("Update Profile")
    client = await select_and_login()
    if not client:
        return
    
    try:
        me = await client.get_me()
        old_name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        
        new_name = f"User_{random.randint(1000, 9999)}"
        await client(UpdateProfileRequest(first_name=new_name))
        print_success(f"Profile updated from '{old_name}' to '{new_name}'")
    except Exception as e:
        print_error(f"Failed to update profile: {str(e)}")
    finally:
        await client.disconnect()

async def clear_contacts():
    """Clear all contacts with confirmation"""
    print_header("Clear Contacts")
    client = await select_and_login()
    if not client:
        return
    
    try:
        contacts = await client.get_contacts()
        if not contacts:
            print_info("No contacts to clear")
            return
            
        print(f"{Theme.WARNING}Found {len(contacts)} contacts:")
        for i, contact in enumerate(contacts[:5], 1):
            print(f"  {i}. {contact.first_name} {contact.last_name or ''} ({contact.phone or 'no phone'})")
        if len(contacts) > 5:
            print(f"  ...and {len(contacts)-5} more")
            
        confirm = input(f"\n{Theme.WARNING}Delete ALL contacts? (y/n): {Theme.RESET}").strip().lower()
        if confirm != 'y':
            print_info("Operation cancelled")
            return
            
        await client(DeleteContactsRequest(id=[contact.id for contact in contacts]))
        print_success(f"Deleted {len(contacts)} contacts")
    except Exception as e:
        print_error(f"Failed to clear contacts: {str(e)}")
    finally:
        await client.disconnect()

async def delete_all_chats():
    """Delete all chats, groups, and channels with confirmation"""
    print_header("Delete All Chats")
    client = await select_and_login()
    if not client:
        return
    
    try:
        dialogs = await client.get_dialogs(limit=None)
        if not dialogs:
            print_info("No chats/channels found")
            return
            
        # Filter out saved messages and other special chats
        dialogs = [d for d in dialogs if d.id not in (777000, 429000)]
        
        print(f"{Theme.WARNING}Found {len(dialogs)} chats/channels:")
        for i, dialog in enumerate(dialogs[:5], 1):
            print(f"  {i}. {dialog.name} ({'group' if dialog.is_group else 'channel' if dialog.is_channel else 'chat'})")
        if len(dialogs) > 5:
            print(f"  ...and {len(dialogs)-5} more")
            
        confirm = input(f"\n{Theme.WARNING}Delete ALL chats/channels? (y/n): {Theme.RESET}").strip().lower()
        if confirm != 'y':
            print_info("Operation cancelled")
            return
            
        deleted_count = 0
        for dialog in dialogs:
            try:
                if dialog.is_channel:
                    await client(LeaveChannelRequest(dialog.entity))
                    print_success(f"Left channel: {dialog.name}")
                else:
                    await client.delete_dialog(dialog)
                    print_success(f"Deleted chat: {dialog.name}")
                deleted_count += 1
                await asyncio.sleep(1)  # Rate limiting
            except Exception as e:
                print_warning(f"Failed to delete {dialog.name}: {str(e)}")
                
        print_success(f"Deleted {deleted_count} chats/channels")
    except Exception as e:
        print_error(f"Error: {str(e)}")
    finally:
        await client.disconnect()

async def check_spam_status():
    """Check if account is spam-restricted"""
    print_header("Check Spam Status")
    client = await select_and_login()
    if not client:
        return
    
    try:
        ttl = await client(GetAccountTTLRequest())
        print(f"{Theme.INFO}Account Status:")
        print(f"  TTL: {ttl.days} days until deletion if inactive")
        
        try:
            test_msg = await client.send_message("me", "Spam check message")
            await client.delete_messages("me", [test_msg.id])
            print_success("Account appears unrestricted")
        except Exception as e:
            print_warning(f"Possible restriction: {str(e)}")
            
    except Exception as e:
        print_error(f"Error checking status: {str(e)}")
    finally:
        await client.disconnect()

async def read_session_otp():
    """Read latest OTP from Telegram messages"""
    print_header("Read OTP")
    client = await select_and_login()
    if not client:
        return
    
    try:
        messages = await client.get_messages("Telegram", limit=10)
        for msg in messages:
            if "login code" in msg.text.lower():
                code = ''.join(filter(str.isdigit, msg.text))
                print_success(f"OTP found: {code}")
                print(f"  Message: {msg.text.split('\n')[0]}")
                print(f"  Received: {msg.date.strftime('%Y-%m-%d %H:%M:%S')}")
                return
        print_info("No recent OTP messages found")
    except Exception as e:
        print_error(f"Failed to read OTP: {str(e)}")
    finally:
        await client.disconnect()

async def get_random_session_by_country():
    """Get random session by country code"""
    print_header("Random Session by Country")
    country_code = input(f"{Theme.PRIMARY}Enter country code (e.g., +91): {Theme.RESET}").strip()
    if not country_code.startswith('+'):
        print_error("Country code must start with '+'")
        return None
    
    sessions = list_sessions(country_code)
    if not sessions:
        return None
        
    session = random.choice(sessions)
    print_success(f"Selected: +{os.path.basename(session).replace('.session', '')}")
    return session

async def safe_execute(func):
    """Execute function with advanced error handling"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return await func()
        except FloodWaitError as e:
            wait_time = min(e.seconds, 3600)  # Max 1 hour wait
            print_warning(f"Flood wait: {wait_time} seconds (Attempt {attempt + 1}/{max_attempts})")
            await asyncio.sleep(wait_time)
        except Exception as e:
            print_error(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt == max_attempts - 1:
                return None
            await asyncio.sleep(1)

async def main():
    """Main menu loop"""
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", lambda: list_sessions()),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Show Active Sessions", show_active_sessions),
        "5": ("Update Profile", update_profile_random_name),
        "6": ("Clear Contacts", clear_contacts),
        "7": ("Delete All Chats", delete_all_chats),
        "8": ("Check Spam Status", check_spam_status),
        "9": ("Read OTP", read_session_otp),
        "10": ("Random Session by Country", get_random_session_by_country),
        "11": ("Exit", None)
    }
    
    while True:
        print_header("Telegram Session Manager")
        for key, (desc, _) in menu_options.items():
            print(f"{Theme.MENU}{key}. {desc}{Theme.RESET}")
        
        choice = input(f"\n{Theme.PRIMARY}Select option (1-11): {Theme.RESET}").strip()
        
        if choice in menu_options:
            if choice == "11":
                print_success("Goodbye!")
                break
            elif choice == "2":
                menu_options[choice][1]()
            else:
                await safe_execute(menu_options[choice][1])
        else:
            print_error(f"Invalid choice! Please select 1-11")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_error("\nOperation cancelled by user")
    except Exception as e:
        print_error(f"Fatal error: {str(e)}")