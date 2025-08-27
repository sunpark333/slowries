import logging
import time
from pyrogram import idle

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

botStartTime = time.time()

print("Successfully deployed!")
print("Bot Deployed : Team Voice")

if __name__ == "__main__":
    import glob
    from pathlib import Path
    from main.utils import load_plugins
    
    path = "main/plugins/*.py"
    files = glob.glob(path)
    for name in files:
        with open(name) as a:
            patt = Path(a.name)
            plugin_name = patt.stem
            load_plugins(plugin_name.replace(".py", ""))
    
    logger.info("Bot Started :)")
    
    # Use Pyrogram's idle function instead of Telethon's run_until_disconnected
    idle()
