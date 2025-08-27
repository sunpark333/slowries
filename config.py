from os import getenv

API_ID = int(getenv("API_ID", ""))
API_HASH = getenv("API_HASH", "")
BOT_TOKEN = getenv("BOT_TOKEN", "")
FORCESUB = getenv("FORCESUB", "").split()
AUTH = list(map(int, getenv("AUTH", "").split()))
SESSION = getenv("SESSION", "")
LOG_GROUP = int(getenv("LOG_GROUP", ""))
ADMIN_ONLY = getenv("ADMIN_ONLY", "False").lower() == "true"
MDB = getenv("MDB", "")

# Cloudnairy Credentials for thumbnail 
CLOUD_NAME=""
API_KEY=""
API_SECRET=""
