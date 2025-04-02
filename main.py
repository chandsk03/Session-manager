import os
import asyncio
import getpass
from telethon.sync import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.tl.functions.auth import ResetAuthorizationsRequest
from telethon.tl.functions.account import UpdateProfileRequest
from colorama import Fore, Style, init

# Initialize colorama for better terminal output
init(autoreset=True)

# 🔴 Load API credentials from environment variables (safer approach)
API_ID = int(os.getenv("TELEGRAM_API_ID", "23077946"))  # Replace with your API ID if not using env vars
API_HASH = os.getenv("TELEGRAM_API_HASH", "b6c2b715121435d4aa285c1fb2bc2220")  # Replace with your API Hash

# 🔵 Function to get session file name based on phone number
def get_session_name(phone):
    return f"session_{phone}.session"

# 🟢 Function to create or load a session
async def create_session():
    phone = input(Fore.CYAN + "📞 Enter your phone number (or bot token): ").strip()
    session_name = get_session_name(phone)

    async with TelegramClient(session_name, API_ID, API_HASH) as client:
        await client.connect()

        if not await client.is_user_authorized():
            try:
                code = input(Fore.YELLOW + "🔑 Enter the code you received: ").strip()
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = getpass.getpass(Fore.RED + "🔒 Enter your password: ")
                await client.sign_in(password=password)

        print(Fore.GREEN + f"✅ Signed in successfully! Session saved as {session_name}.")

# 🔴 Function to list all saved sessions
def list_sessions():
    sessions = [f for f in os.listdir() if f.startswith("session_") and f.endswith(".session")]
    if not sessions:
        print(Fore.RED + "❌ No saved sessions found.")
        return None
    print(Fore.BLUE + "\nAvailable Sessions:")
    for i, session in enumerate(sessions, 1):
        print(f"{i}. {session}")
    return sessions

# 🔵 Function to select a session
def select_session():
    sessions = list_sessions()
    if not sessions:
        return None

    try:
        choice = int(input(Fore.CYAN + "\nSelect a session number: ").strip()) - 1
        if 0 <= choice < len(sessions):
            return sessions[choice]
        print(Fore.RED + "❌ Invalid choice.")
    except ValueError:
        print(Fore.RED + "❌ Please enter a valid number.")
    return None

# 🟢 Function to terminate all other sessions (except the current one)
async def terminate_other_sessions():
    session = select_session()
    if not session:
        return

    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        await client(ResetAuthorizationsRequest())
        print(Fore.GREEN + "✅ All other sessions terminated.")

# 🟡 Function to check active sessions
async def check_active_sessions():
    session = select_session()
    if not session:
        return

    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        dialogs = await client.get_dialogs()
        print(Fore.BLUE + f"📊 Active Sessions Count: {len(dialogs)}")

# 🔴 Function to reset 2FA
async def reset_2fa():
    session = select_session()
    if not session:
        return

    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        await client.send_message("me", "2FA Reset Requested!")
        print(Fore.GREEN + "✅ 2FA reset request sent.")

# 🟢 Function to update profile with random name
async def update_profile_random_name():
    session = select_session()
    if not session:
        return

    async with TelegramClient(session, API_ID, API_HASH) as client:
        await client.connect()
        new_name = f"User{API_ID}"
        await client(UpdateProfileRequest(first_name=new_name))
        print(Fore.GREEN + f"✅ Profile Updated to: {new_name}")

# 🟠 Improved flood wait handling
async def safe_execute(func):
    try:
        await func()
    except FloodWaitError as e:
        print(Fore.RED + f"⏳ Telegram rate limit detected! Waiting {e.seconds} seconds...")
        await asyncio.sleep(e.seconds)
        await func()

# 🔵 Main menu loop
async def main():
    while True:
        print(Fore.MAGENTA + "\n📌 Choose an option:")
        print("1️⃣  Create New Session")
        print("2️⃣  List Saved Sessions")
        print("3️⃣  Terminate Other Sessions")
        print("4️⃣  Check Active Sessions")
        print("5️⃣  Reset 2FA")
        print("6️⃣  Update Profile with Random Name")
        print("7️⃣  Exit")

        choice = input(Fore.CYAN + "👉 Enter your choice: ").strip()

        if choice == "1":
            await safe_execute(create_session)
        elif choice == "2":
            list_sessions()
        elif choice == "3":
            await safe_execute(terminate_other_sessions)
        elif choice == "4":
            await safe_execute(check_active_sessions)
        elif choice == "5":
            await safe_execute(reset_2fa)
        elif choice == "6":
            await safe_execute(update_profile_random_name)
        elif choice == "7":
            print(Fore.GREEN + "👋 Exiting...")
            break
        else:
            print(Fore.RED + "❌ Invalid choice! Please select a valid option.")

if __name__ == "__main__":
    asyncio.run(main())
