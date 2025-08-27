from pyrogram import Client
from pyromod import listen
from config import API_ID, API_HASH, BOT_TOKEN, SESSION, FORCESUB
import logging, time, sys

log_file = "bot_logs.txt"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.INFO)

# Initialize the user client with session string
userbot = Client("myacc", api_id=API_ID, api_hash=API_HASH, session_string=SESSION)

try:
    userbot.start()
except BaseException:
    print("Your session expired please re add that... thanks Team Voice.")
    sys.exit(1)

# Initialize the bot client
Bot = Client(
    "SaveRestricted",
    bot_token=BOT_TOKEN,
    api_id=int(API_ID),
    api_hash=API_HASH,
    workers=50
)    

try:
    Bot.start()
except Exception as e:
    print(f"Error starting bot: {e}")
    sys.exit(1)
