import os
import asyncio
import getpass
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, PhoneNumberInvalidError
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
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

def get_session_name(phone):
    """Generate session file name from phone number"""
    return f"session_{phone.replace('+', '')}.session"

async def create_session():
    """Create a new Telegram session with improved handling"""
    while True:
        phone = input(f"{Theme.PRIMARY}📞 Enter your phone number (e.g., +12345678900) or bot token: {Theme.RESET}").strip()
        if not phone:
            print(f"{Theme.ERROR}❌ Phone number cannot be empty. Please try again.{Theme.RESET}")
            continue
        if not phone.startswith('+') and not phone.isalnum():
            print(f"{Theme.ERROR}❌ Invalid format. Use + followed by country code and number, or a valid bot token.{Theme.RESET}")
            continue
        break
    
    session_name = get_session_name(phone)
    
    async with TelegramClient(session_name, API_ID, API_HASH) as client:
        for attempt in range(3):
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    print(f"{Theme.INFO}ℹ️ Sending verification code to {phone}...{Theme.RESET}")
                    await client.send_code_request(phone)
                    code = input(f"{Theme.WARNING}🔑 Enter the code you received (or 'q' to quit): {Theme.RESET}").strip()
                    if code.lower() == 'q':
                        print(f"{Theme.WARNING}ℹ️ Session creation cancelled.{Theme.RESET}")
                        return
                    
                    await client.sign_in(phone, code)
                
                print(f"{Theme.SUCCESS}✅ Signed in successfully! Session saved as {session_name}{Theme.RESET}")
                return session_name
            
            except SessionPasswordNeededError:
                for pwd_attempt in range(3):
                    password = getpass.getpass(f"{Theme.ERROR}🔒 Enter your 2FA password (attempt {pwd_attempt + 1}/3): {Theme.RESET}")
                    try:
                        await client.sign_in(password=password)
                        print(f"{Theme.SUCCESS}✅ Signed in successfully! Session saved as {session_name}{Theme.RESET}")
                        return session_name
                    except Exception as e:
                        print(f"{Theme.ERROR}❌ Invalid password: {str(e)}. Please try again.{Theme.RESET}")
                        if pwd_attempt == 2:
                            print(f"{Theme.ERROR}❌ Maximum password attempts reached.{Theme.RESET}")
                            return
            except PhoneNumberInvalidError:
                print(f"{Theme.ERROR}❌ The phone number is invalid. Please check the format and try again.{Theme.RESET}")
                return
            except TimeoutError:
                print(f"{Theme.WARNING}⏳ Connection timeout (attempt {attempt + 1}/3). Retrying...{Theme.RESET}")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f"{Theme.ERROR}❌ Failed to create session: {str(e)}{Theme.RESET}")
                return
        print(f"{Theme.ERROR}❌ Failed to connect after 3 attempts. Check your network or API credentials.{Theme.RESET}")

def list_sessions():
    """List all saved session files"""
    sessions = [f for f in os.listdir() if f.startswith("session_") and f.endswith(".session")]
    if not sessions:
        print(f"{Theme.WARNING}ℹ️ No saved sessions found.{Theme.RESET}")
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
    
    while True:
        try:
            choice = input(f"{Theme.PRIMARY}\nSelect a session number (1-{len(sessions)}): {Theme.RESET}").strip()
            if not choice:
                print(f"{Theme.ERROR}❌ Selection cannot be empty.{Theme.RESET}")
                continue
            choice = int(choice) - 1
            if 0 <= choice < len(sessions):
                return sessions[choice]
            print(f"{Theme.ERROR}❌ Invalid selection. Choose a number between 1 and {len(sessions)}.{Theme.RESET}")
        except ValueError:
            print(f"{Theme.ERROR}❌ Please enter a valid number.{Theme.RESET}")

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
        await client.connect()
        try:
            contacts = [contact async for contact in client.iter_contacts()]
            if not contacts:
                print(f"{Theme.WARNING}ℹ️ No contacts to clear.{Theme.RESET}")
                return
            await client(DeleteContactsRequest(id=[contact.id for contact in contacts]))
            print(f"{Theme.SUCCESS}✅ Cleared {len(contacts)} contacts.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to clear contacts: {str(e)}{Theme.RESET}")

async def delete_chat():
    """Delete a selected chat or leave a channel"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            dialogs = await client.get_dialogs()
            if not dialogs:
                print(f"{Theme.WARNING}ℹ️ No chats or channels available.{Theme.RESET}")
                return
            
            print(f"{Theme.INFO}\nAvailable Chats/Channels:{Theme.RESET}")
            for i, dialog in enumerate(dialogs, 1):
                print(f"{Theme.INFO}{i}. {dialog.title} ({'Chat' if isinstance(dialog.entity, PeerChat) else 'Channel/Group'}){Theme.RESET}")
            
            choice = int(input(f"{Theme.PRIMARY}Select chat/channel number to delete (1-{len(dialogs)}): {Theme.RESET}").strip()) - 1
            if 0 <= choice < len(dialogs):
                dialog = dialogs[choice]
                if isinstance(dialog.entity, PeerChat):
                    await client(DeleteHistoryRequest(peer=dialog.entity, max_id=0, just_clear=True))
                    print(f"{Theme.SUCCESS}✅ Chat '{dialog.title}' history cleared.{Theme.RESET}")
                elif isinstance(dialog.entity, PeerChannel):
                    await client(LeaveChannelRequest(channel=dialog.entity))
                    print(f"{Theme.SUCCESS}✅ Left channel/group '{dialog.title}'.{Theme.RESET}")
                else:
                    print(f"{Theme.ERROR}❌ Unsupported dialog type for '{dialog.title}'.{Theme.RESET}")
            else:
                print(f"{Theme.ERROR}❌ Invalid selection. Choose a number between 1 and {len(dialogs)}.{Theme.RESET}")
        except ValueError:
            print(f"{Theme.ERROR}❌ Please enter a valid number.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to delete chat/channel: {str(e)}{Theme.RESET}")

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
            print(f"{Theme.SUCCESS}✅ Account appears unrestricted (can send messages).{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.WARNING}⚠️ Possible spam restriction detected: {str(e)}{Theme.RESET}")

async def read_session_otp():
    """Read latest OTP from Telegram messages"""
    session = select_session()
    if not session:
        return
    
    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        try:
            messages = await client.get_messages("me", limit=10)
            for msg in messages:
                if "code" in msg.text.lower() or "otp" in msg.text.lower():
                    print(f"{Theme.SUCCESS}✅ Found OTP: {msg.text} (Received: {msg.date}){Theme.RESET}")
                    return
            print(f"{Theme.WARNING}ℹ️ No recent OTP found in last 10 messages.{Theme.RESET}")
        except Exception as e:
            print(f"{Theme.ERROR}❌ Failed to read OTP: {str(e)}{Theme.RESET}")

async def safe_execute(func):
    """Execute function with exponential backoff for flood waits"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return await func()
        except FloodWaitError as e:
            wait_time = min(e.seconds * (2 ** attempt), 3600)
            print(f"{Theme.WARNING}⏳ Rate limit hit! Waiting {wait_time} seconds (Attempt {attempt + 1}/{max_attempts})...{Theme.RESET}")
            await asyncio.sleep(wait_time)
        except Exception as e:
            print(f"{Theme.ERROR}❌ Error: {str(e)}{Theme.RESET}")
            break
    print(f"{Theme.ERROR}❌ Operation failed after {max_attempts} attempts.{Theme.RESET}")

async def main():
    """Main menu loop"""
    menu_options = {
        "1": ("Create New Session", create_session),
        "2": ("List Saved Sessions", list_sessions),
        "3": ("Terminate Other Sessions", terminate_other_sessions),
        "4": ("Show Active Sessions", show_active_sessions),
        "5": ("Update Profile with Random Name", update_profile_random_name),
        "6": ("Clear Contacts", clear_contacts),
        "7": ("Delete Chat/Channel", delete_chat),
        "8": ("Check Spam Status", check_spam_status),
        "9": ("Read Session OTP", read_session_otp),
        "10": ("Exit", None)
    }
    
    while True:
        print(f"{Theme.TITLE}\n=== Telegram Session Manager ==={Theme.RESET}")
        for key, (desc, _) in menu_options.items():
            print(f"{Theme.MENU}{key}. {desc}{Theme.RESET}")
        
        choice = input(f"{Theme.PRIMARY}👉 Enter your choice (1-{len(menu_options)}): {Theme.RESET}").strip()
        
        if choice in menu_options:
            if choice == "10":
                print(f"{Theme.SUCCESS}👋 Exiting Telegram Session Manager...{Theme.RESET}")
                break
            elif choice == "2":
                menu_options[choice][1]()
            else:
                await safe_execute(menu_options[choice][1])
        else:
            print(f"{Theme.ERROR}❌ Invalid choice! Please select a number between 1 and {len(menu_options)}.{Theme.RESET}")

if __name__ == "__main__":
    asyncio.run(main())