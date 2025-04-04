import os
import asyncio
import getpass
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.account import UpdateProfileRequest
from colorama import Fore, Style, init
import time

# Initialize colorama for cross-platform colored output
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

# API credentials from environment variables
API_ID = int(os.getenv("TELEGRAM_API_ID", "23077946"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")

def get_session_name(phone):
    """Generate session file name from phone number"""
    return f"session_{phone.replace('+', '')}.session"

async def create_session():
    """Create a new Telegram session"""
    phone = input(f"{Theme.PRIMARY}📞 Enter your phone number (or bot token): {Theme.RESET}").strip()
    session_name = get_session_name(phone)
    
    async with TelegramClient(session_name, API_ID, API_HASH) as client:
        await client.connect()
        
        if not await client.is_user_authorized():
            try:
                await client.send_code_request(phone)
                code = input(f"{Theme.WARNING}🔑 Enter the code you received: {Theme.RESET}").strip()
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = getpass.getpass(f"{Theme.ERROR}🔒 Enter your 2FA password: {Theme.RESET}")
                await client.sign_in(password=password)
            except Exception as e:
                print(f"{Theme.ERROR}❌ Error during sign-in: {str(e)}{Theme.RESET}")
                return
        
        print(f"{Theme.SUCCESS}✅ Signed in successfully! Session saved as {session_name}{Theme.RESET}")
        return session_name

def list_sessions():
    """List all saved session files"""
    sessions = [f for f in os.listdir() if f.startswith("session_") and f.endswith(".session")]
    if not sessions:
        print(f"{Theme.ERROR}❌ No saved sessions found.{Theme.RESET}")
        return None
    
    print(f"{Theme.INFO}\nAvailable Sessions:{Theme.RESET}")
    for i, session in enumerate(sessions, 1):
        print(f"{Theme.INFO}{i}. {session.replace('.session', '')}{Theme.RESET}")
    return sessions

def select_session():
    """Select a session from available ones"""
    sessions = list_sessions()
    if not sessions:
        return None
    
    try:
        choice = int(input(f"{Theme.PRIMARY}\nSelect a session number: {Theme.RESET}").strip()) - 1
        if 0 <= choice < len(sessions):
            return sessions[choice]
        print(f"{Theme.ERROR}❌ Invalid selection. Please choose a number between 1 and {len(sessions)}.{Theme.RESET}")
    except ValueError:
        print(f"{Theme.ERROR}❌ Please enter a valid number.{Theme.RESET}")
    return None

async def terminate_other_sessions():
    """Terminate all sessions except current one"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            await client(ResetAuthorizationsRequest())
            print(f"{Theme.SUCCESS}✅ All other sessions terminated successfully.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to terminate sessions: {str(e)}{Theme.RESET}")

async def check_active_sessions():
    """Check active sessions and display count"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            dialogs = await client.get_dialogs()
            print(f"{Theme.INFO}📊 Active Sessions Count: {len(dialogs)}{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Error checking sessions: {str(e)}{Theme.RESET}")

async def reset_2fa():
    """Reset 2FA with proper instructions"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            await client.send_message("me", "2FA Reset Requested!")
            print(f"{Theme.SUCCESS}✅ 2FA reset request sent.{Theme.RESET}")
            print(f"{Theme.WARNING}ℹ️ Note: This will disable 2FA after a 7-day waiting period if not cancelled.")
            print(f"Check your Telegram app under Settings > Privacy and Security to manage this request.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to request 2FA reset: {str(e)}{Theme.RESET}")

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

async def safe_execute(func):
    """Execute function with exponential backoff for flood waits"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return await func()
        except FloodWaitError as e:
            wait_time = min(e.seconds * (2 ** attempt), 3600)  # Cap at 1 hour
            print(f"{Theme.WARNING}⏳ Rate limit hit! Waiting {wait_time} seconds (Attempt {attempt + 1}/{max_attempts})...{Theme.RESET}")
            await asyncio.sleep(wait_time)
        except Exception as e:
            print(f"{Theme.ERROR}❌ Error: {str(e)}{Theme.RESET}")
            break
    print(f"{Theme.ERROR}❌ Operation failed after {max_attempts} attempts.{Theme.RESET}")

def cleanup_sessions():
    """Remove old session files older than 30 days"""
    sessions = list_sessions()
    if not sessions:
        return
    
    current_time = time.time()
    for session in sessions:
        file_time = os.path.getmtime(session)
        if (current_time - file_time) > (30 * 24 * 3600):  # 30 days
            os.remove(session)
            print(f"{Theme.WARNING}🗑️ Removed old session: {session}{Theme.RESET}")

async def main():
    """Main menu loop"""
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", list_sessions),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Check Active Sessions", check_active_sessions),
        "5": ("Reset 2FA", reset_2fa),
        "6": ("Update Profile with Random Name", update_profile_random_name),
        "7": ("Exit", None)
    }
    
    while True:
        print(f"{Theme.TITLE}\n=== Telegram Session Manager ==={Theme.RESET}")
        for key, (desc, _) in menu_options.items():
            print(f"{Theme.MENU}{key}. {desc}{Theme.RESET}")
        
        choice = input(f"{Theme.PRIMARY}👉 Enter your choice: {Theme.RESET}").strip()
        
        if choice in menu_options:
            if choice == "7":
                print(f"{Theme.SUCCESS}👋 Exiting...{Theme.RESET}")
                cleanup_sessions()
                break
            elif choice == "2":
                menu_options[choice][1]()
            else:
                await safe_execute(menu_options[choice][1])
        else:
            print(f"{Theme.ERROR}❌ Invalid choice! Please select 1-{len(menu_options)}.{Theme.RESET}")

if __name__ == "__main__":
    asyncio.run(main())