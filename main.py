import os
import asyncio
import getpass
import random
import glob
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, PhoneNumberInvalidError
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationsRequest
from telethon.tl.functions.account import UpdateProfileRequest, GetAccountTTLRequest
from telethon.tl.functions.contacts import DeleteContactsRequest
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.types import PeerChat, PeerChannel
from colorama import Fore, Style, init
import time

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

# API credentials
API_ID = int(os.getenv("TELEGRAM_API_ID", "23077946"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")

# Session folder
SESSION_FOLDER = "sessions"
if not os.path.exists(SESSION_FOLDER):
    os.makedirs(SESSION_FOLDER)

def get_session_path(phone):
    """Generate session file path from phone number"""
    return os.path.join(SESSION_FOLDER, f"session_{phone.replace('+', '')}.session")

async def login_session(session_path):
    """Attempt to automatically login using existing session"""
    async with TelegramClient(session_path, API_ID, API_HASH) as client:
        try:
            await client.connect()
            if await client.is_user_authorized():
                print(f"{Theme.SUCCESS}✅ Automatically logged in using {session_path}{Theme.RESET}")
                return True
            return False
        except Exception as e:
            print(f"{Theme.ERROR}❌ Login failed for {session_path}: {str(e)}{Theme.RESET}")
            return False

def list_sessions(country_code=None):
    """List all saved session files from both root and sessions folder"""
    root_pattern = "session_*.session"
    sessions_pattern = os.path.join(SESSION_FOLDER, "session_*.session")
    
    root_sessions = glob.glob(root_pattern)
    folder_sessions = glob.glob(sessions_pattern)
    
    sessions = list(set(root_sessions + folder_sessions))
    
    if country_code:
        sessions = [s for s in sessions if os.path.basename(s).replace('.session', '').split('_')[1].startswith(country_code.replace('+', ''))]
    
    if not sessions:
        print(f"{Theme.WARNING}ℹ️ No saved sessions found.{Theme.RESET}")
        return None
    
    print(f"{Theme.INFO}\nAvailable Sessions:{Theme.RESET}")
    for i, session in enumerate(sessions, 1):
        print(f"{Theme.INFO}{i}. {session}{Theme.RESET}")
    return sessions

def select_session(country_code=None):
    """Select a session from available ones with optional country code filter"""
    sessions = list_sessions(country_code)
    if not sessions:
        return None
    
    while True:
        try:
            choice = input(f"{Theme.PRIMARY}\nSelect a session number (1-{len(sessions)}): {Theme.RESET}").strip()
            if not choice:
                print(f"{Theme.ERROR}❌ Selection cannot be empty.{Theme.RESET}")
                continue
            choice = int(choice) - 1
            if 0 <= choice < len(sessions):
                return sessions[choice]
            print(f"{Theme.ERROR}❌ Invalid selection. Choose between 1 and {len(sessions)}.{Theme.RESET}")
        except ValueError:
            print(f"{Theme.ERROR}❌ Please enter a valid number.{Theme.RESET}")

async def create_session():
    """Create a new Telegram session with improved handling"""
    while True:
        phone = input(f"{Theme.PRIMARY}📞 Enter phone number (e.g., +12345678900) or bot token: {Theme.RESET}").strip()
        if not phone:
            print(f"{Theme.ERROR}❌ Phone number cannot be empty.{Theme.RESET}")
            continue
        if not phone.startswith('+') and not phone.isalnum():
            print(f"{Theme.ERROR}❌ Invalid format.{Theme.RESET}")
            continue
        break
    
    session_path = get_session_path(phone)
    
    async with TelegramClient(session_path, API_ID, API_HASH) as client:
        for attempt in range(3):
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    print(f"{Theme.INFO}ℹ️ Sending code to {phone}...{Theme.RESET}")
                    await client.send_code_request(phone)
                    code = input(f"{Theme.WARNING}🔑 Enter code (or 'q' to quit): {Theme.RESET}").strip()
                    if code.lower() == 'q':
                        return None
                    await client.sign_in(phone, code)
                print(f"{Theme.SUCCESS}✅ Session saved: {session_path}{Theme.RESET}")
                return session_path
            except SessionPasswordNeededError:
                password = getpass.getpass(f"{Theme.ERROR}🔒 Enter 2FA password: {Theme.RESET}")
                await client.sign_in(password=password)
                print(f"{Theme.SUCCESS}✅ Session saved: {session_path}{Theme.RESET}")
                return session_path
            except Exception as e:
                print(f"{Theme.ERROR}❌ Error: {str(e)}{Theme.RESET}")
                return None

async def terminate_other_sessions():
    """Terminate all sessions except current one"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        for attempt in range(3):
            try:
                await client.connect()
                await client(ResetAuthorizationsRequest())
                print(f"{Theme.SUCCESS}✅ All other sessions terminated.{Theme.RESET}")
                return
            except TimeoutError:
                print(f"{Theme.WARNING}⏳ Timeout (attempt {attempt + 1}/3). Retrying...{Theme.RESET}")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f"{Theme.ERROR}❌ Failed to terminate sessions: {str(e)}{Theme.RESET}")
                return

async def show_active_sessions():
    """Show detailed info about active sessions"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            auths = await client(GetAuthorizationsRequest())
            if not auths.authorizations:
                print(f"{Theme.WARNING}ℹ️ No active sessions found.{Theme.RESET}")
                return
            print(f"{Theme.INFO}📊 Active Sessions ({len(auths.authorizations)}):{Theme.RESET}")
            for i, auth in enumerate(auths.authorizations, 1):
                print(f"{Theme.INFO}{i}. Device: {auth.device_model} ({auth.platform})")
                print(f"   IP: {auth.ip}")
                print(f"   Location: {auth.country}")
                print(f"   App: {auth.app_name} v{auth.app_version}")
                print(f"   Last Active: {auth.date_active.strftime('%Y-%m-%d %H:%M:%S')}{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Error fetching sessions: {str(e)}{Theme.RESET}")

async def update_profile_random_name():
    """Update profile with random name"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            new_name = f"User_{int(time.time())}"
            await client(UpdateProfileRequest(first_name=new_name))
            print(f"{Theme.SUCCESS}✅ Profile updated to: {new_name}{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to update profile: {str(e)}{Theme.RESET}")

async def clear_contacts():
    """Clear all contacts"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        for attempt in range(3):
            try:
                await client.connect()
                contacts = [contact async for contact in client.iter_contacts()]
                if not contacts:
                    print(f"{Theme.WARNING}ℹ️ No contacts to clear.{Theme.RESET}")
                    return
                await client(DeleteContactsRequest(id=[contact.id for contact in contacts]))
                print(f"{Theme.SUCCESS}✅ Cleared {len(contacts)} contacts.{Theme.RESET}")
                return
            except TimeoutError:
                print(f"{Theme.WARNING}⏳ Timeout (attempt {attempt + 1}/3). Retrying...{Theme.RESET}")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f"{Theme.ERROR}❌ Failed to clear contacts: {str(e)}{Theme.RESET}")
                return

async def delete_all_chats():
    """Delete all chats, groups, and channels"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            dialogs = await client.get_dialogs()
            if not dialogs:
                print(f"{Theme.WARNING}ℹ️ No chats/channels found.{Theme.RESET}")
                return
            total_cleared = 0
            for dialog in dialogs:
                try:
                    if isinstance(dialog.entity, PeerChat):
                        await client(DeleteHistoryRequest(peer=dialog.entity, max_id=0, just_clear=True))
                        print(f"{Theme.SUCCESS}✅ Cleared chat: {dialog.title}{Theme.RESET}")
                    elif isinstance(dialog.entity, PeerChannel):
                        await client(LeaveChannelRequest(channel=dialog.entity))
                        print(f"{Theme.SUCCESS}✅ Left channel: {dialog.title}{Theme.RESET}")
                    total_cleared += 1
                    await asyncio.sleep(1)
                except Exception as e:
                    print(f"{Theme.WARNING}⚠️ Skipped {dialog.title}: {str(e)}{Theme.RESET}")
            print(f"{Theme.SUCCESS}✅ Total {total_cleared} chats/channels wiped.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Error: {str(e)}{Theme.RESET}")

async def check_spam_status():
    """Check if account is spam-restricted"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            ttl = await client(GetAccountTTLRequest())
            print(f"{Theme.INFO}📋 Account Status:{Theme.RESET}")
            print(f"{Theme.INFO}   Account TTL: {ttl.days} days")
            test_msg = await client.send_message("me", "Spam check test")
            await client.delete_messages("me", [test_msg.id])
            print(f"{Theme.SUCCESS}✅ Account appears unrestricted.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.WARNING}⚠️ Possible spam restriction: {str(e)}{Theme.RESET}")

async def read_session_otp():
    """Read latest OTP from Telegram messages"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            messages = await client.get_messages("Telegram", limit=10)
            for msg in messages:
                if "login code" in msg.text.lower():
                    code = ''.join(filter(str.isdigit, msg.text))
                    print(f"{Theme.SUCCESS}✅ OTP: {code} (Received: {msg.date}){Theme.RESET}")
                    return
            print(f"{Theme.WARNING}ℹ️ No recent OTP found.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to read OTP: {str(e)}{Theme.RESET}")

async def get_random_session_by_country():
    """Get random session by country code"""
    country_code = input(f"{Theme.PRIMARY}Enter country code (e.g., +91): {Theme.RESET}").strip()
    if not country_code.startswith('+'):
        print(f"{Theme.ERROR}❌ Country code must start with '+'.{Theme.RESET}")
        return None
    
    sessions = list_sessions(country_code)
    if not sessions:
        return None
    session = random.choice(sessions)
    print(f"{Theme.SUCCESS}✅ Selected: {session}{Theme.RESET}")
    return session

async def safe_execute(func):
    """Execute function with flood wait handling"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return await func()
        except FloodWaitError as e:
            wait_time = min(e.seconds * (2 ** attempt), 3600)
            print(f"{Theme.WARNING}⏳ Waiting {wait_time}s...{Theme.RESET}")
            await asyncio.sleep(wait_time)
        except Exception as e:
            print(f"{Theme.ERROR}❌ Error: {str(e)}{Theme.RESET}")
            break
    print(f"{Theme.ERROR}❌ Failed after {max_attempts} attempts.{Theme.RESET}")

async def main():
    """Main menu loop with auto-login"""
    # Attempt to auto-login all sessions first
    all_sessions = list_sessions()
    if all_sessions:
        for session in all_sessions:
            await login_session(session)
    
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", lambda: list_sessions()),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Show Active Sessions", show_active_sessions),
        "5": ("Update Profile with Random Name", update_profile_random_name),
        "6": ("Clear Contacts", clear_contacts),
        "7": ("Delete All Chats/Channels", delete_all_chats),
        "8": ("Check Spam Status", check_spam_status),
        "9": ("Read Session OTP", read_session_otp),
        "10": ("Get Random Session by Country", get_random_session_by_country),
        "11": ("Exit", None)
    }
    
    while True:
        print(f"{Theme.TITLE}\n=== Advanced Telegram Session Manager ==={Theme.RESET}")
        for key, (desc, _) in menu_options.items():
            print(f"{Theme.MENU}{key}. {desc}{Theme.RESET}")
        
        choice = input(f"{Theme.PRIMARY}👉 Enter your choice (1-{len(menu_options)}): {Theme.RESET}").strip()
        
        if choice in menu_options:
            if choice == "11":
                print(f"{Theme.SUCCESS}👋 Exiting...{Theme.RESET}")
                break
            elif choice == "2":
                menu_options[choice][1]()
            else:
                await safe_execute(menu_options[choice][1])
        else:
            print(f"{Theme.ERROR}❌ Invalid choice!{Theme.RESET}")

if __name__ == "__main__":
    asyncio.run(main())