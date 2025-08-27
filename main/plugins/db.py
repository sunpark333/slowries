import pymongo
import logging
import re
from datetime import datetime, timedelta
from config import MDB, AUTH

# Configure logging
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

class Database:
    def __init__(self):
        """Initialize the database connection"""
        try:
            self.client = pymongo.MongoClient(MDB)
            self.db = self.client["TelegramBot"]
            
            # Collections
            self.users = self.db["users"]
            self.banned = self.db["banned_users"]
            self.authorized = self.db["authorized_users"]
            self.stats = self.db["statistics"]
            self.welcome_log = self.db["welcome_log"]
            self.keys = self.db["keys"]
            self.warnings = self.db["warnings"]
            
            # Create indexes
            self.users.create_index("user_id", unique=True)
            self.banned.create_index("user_id", unique=True)
            self.authorized.create_index("user_id", unique=True)
            self.welcome_log.create_index("user_id", unique=True)
            self.keys.create_index("key", unique=True)
            self.warnings.create_index("user_id")
            
            # Initialize stats collection
            if self.stats.count_documents({}) == 0:
                self.stats.insert_one({
                    "cloned_messages": 0,
                    "downloaded_messages": 0,
                    "thumbnails_set": 0
                })
            
            logger.info("Database connected successfully")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise

    ### Helper Methods ###
    def _is_admin(self, user_id):
        """Check if user is an admin"""
        return user_id in AUTH

    def _validate_thumbnail(self, thumbnail):
        """Validate thumbnail format"""
        if thumbnail.startswith("http"):
            # Check if the URL ends with an image extension
            return bool(re.match(
                r'^https?://(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,9}'
                r'(?:/[^/]*)*\.(jpg|jpeg|png|webp)(?:\?.*)?$',
                thumbnail,
                re.IGNORECASE
            ))
        return False

    ### User Management ###
    def add_user(self, user_id, username=None, first_name=None, last_name=None):
        """Add or update user in database"""
        try:
            # Check if user exists
            user = self.users.find_one({"user_id": user_id})
            
            if user:
                # Update existing user
                update_data = {
                    "$set": {
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "last_activity": datetime.now()
                    }
                }
            else:
                # Create new user with default values
                update_data = {
                    "$set": {
                        "user_id": user_id,
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "last_activity": datetime.now(),
                        "thumbnail": None,
                        "thumbnail_enabled": False,
                        "watermark_text": None,
                        "chat_id": None,
                        "premium_level": 0,
                        "message_limit": None,
                        "last_key_redeem": None,
                        "expiration_time": None
                    }
                }
            
            result = self.users.update_one(
                {"user_id": user_id},
                update_data,
                upsert=True
            )
            return result.upserted_id is not None or result.modified_count > 0
        except Exception as e:
            logger.error(f"Error adding user: {e}")
            return False

    def set_chat_id(self, user_id, chat_id):
      """Set chat ID for user"""
      try:
          # Validate chat_id is an integer
          if not isinstance(chat_id, int):
              logger.error(f"Invalid chat_id: {chat_id}. Must be an integer.")
              return False
            
          result = self.users.update_one(
              {"user_id": user_id},
              {"$set": {"chat_id": chat_id}}
          )
          return result.modified_count > 0
      except Exception as e:
          logger.error(f"Error setting chat ID: {e}")
          return False

    def get_chat_id(self, user_id):
      """Get user's chat ID. Returns user_id if chat_id is not available."""
      try:
          user = self.users.find_one({"user_id": user_id})
          chat_id = user.get("chat_id") if user else None
          return chat_id if chat_id else user_id
      except Exception as e:
          logger.error(f"Error getting chat ID: {e}")
          return user_id

    def remove_chat_id(self, user_id):
      """Remove chat ID for user"""
      try:
          result = self.users.update_one(
              {"user_id": user_id},
              {"$unset": {"chat_id": ""}}
          )
          return result.modified_count > 0
      except Exception as e:
          logger.error(f"Error removing chat ID: {e}")
          return False

    ### Thumbnail Management ###
    def set_thumbnail(self, user_id, thumbnail):
        """Set custom thumbnail for user"""
        try:
            # Get the current thumbnail
            current_doc = self.users.find_one({"user_id": user_id})
            
            # If the user has the same thumbnail already, return True
            # without making an unnecessary DB update
            if current_doc and "thumbnail" in current_doc and current_doc["thumbnail"] == thumbnail:
                return True
                
            if thumbnail.startswith("http"):
                if not self._validate_thumbnail(thumbnail):
                    return False
                    
            result = self.users.update_one(
                {"user_id": user_id},
                {"$set": {"thumbnail": thumbnail}}
            )
            
            # Check if actually modified (will be 0 if same value)
            if result.matched_count > 0:
                # Also update stats if actually changed
                if result.modified_count > 0:
                    self.stats.update_one({}, {"$inc": {"thumbnails_set": 1}})
                return True
            return False
        except Exception as e:
            logger.error(f"Error setting thumbnail: {e}")
            return False

    def get_thumbnail(self, user_id):
        """Get user's thumbnail"""
        try:
            user = self.users.find_one({"user_id": user_id})
            return user.get("thumbnail") if user else None
        except Exception as e:
            logger.error(f"Error getting thumbnail: {e}")
            return None

    def remove_thumbnail(self, user_id):
        """Remove user's thumbnail"""
        try:
            result = self.users.update_one(
                {"user_id": user_id},
                {"$unset": {"thumbnail": ""}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error removing thumbnail: {e}")
            return False

    ### Authorization System ###
    def is_user_authorized(self, user_id):
        """Check if user is authorized"""
        try:
            if self._is_admin(user_id):
                return True
            user = self.users.find_one({"user_id": user_id})
            if not user:
                return False
            if user.get("expiration_time") and user["expiration_time"] < datetime.now():
                self.users.update_one(
                    {"user_id": user_id},
                    {"$set": {"premium_level": 0, "expiration_time": None, "message_limit": None}}
                )
                return False
            return user.get("premium_level", 0) > 0
        except Exception as e:
            logger.error(f"Error checking authorization: {e}")
            return False

    def authorize_user(self, user_id, auth_by=None, expiration_hours=None, message_limit=None, premium_level=1):
        """Authorize a user with optional expiration, message limit, and premium level"""
        try:
            expiration_time = datetime.now() + timedelta(hours=expiration_hours) if expiration_hours else None
            update_data = {
                "premium_level": premium_level,
                "expiration_time": expiration_time,
                "message_limit": message_limit,
                "auth_by": auth_by,
                "timestamp": datetime.now()
            }
            result = self.users.update_one(
                {"user_id": user_id},
                {"$set": update_data}
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f"Error authorizing user: {e}")
            return False

    def unauthorize_user(self, user_id):
        """Unauthorize a user"""
        try:
            result = self.users.update_one(
                {"user_id": user_id},
                {"$set": {"premium_level": 0, "expiration_time": None, "message_limit": None}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error unauthorizing user: {e}")
            return False

    ### Key Management ###
    def create_key(self, key, expiration_time=None, message_limit=None, premium_level=0, created_by=None):
        """Create a new key"""
        try:
            key_data = {
                "key": key,
                "expiration_time": expiration_time,
                "message_limit": message_limit,
                "premium_level": premium_level,
                "created_by": created_by,
                "created_at": datetime.now(),
                "redeemed_by": None,
                "redeemed_at": None
            }
            result = self.keys.insert_one(key_data)
            return result.inserted_id
        except Exception as e:
            logger.error(f"Error creating key: {e}")
            return None

    def get_key(self, key):
        """Get key by key string"""
        try:
            return self.keys.find_one({"key": key})
        except Exception as e:
            logger.error(f"Error getting key: {e}")
            return None
            
    def get_remaining_messages(self, user_id):
        """Get remaining message limit for user
    
    Returns:
        int or None: Number of messages remaining, None if unlimited
    """
        try:
            user = self.users.find_one({"user_id": user_id})
            if not user:
                return None
            return user.get("message_limit")
        except Exception as e:
            logger.error(f"Error getting remaining messages: {e}")
            return None

    def get_expiration_time_remaining(self, user_id):
        """Get time remaining until user's premium expires
    
    Returns:
        timedelta or None: Time remaining until expiration, None if no expiration
    """
        try:
           user = self.users.find_one({"user_id": user_id})
           if not user or not user.get("expiration_time"):
              return None
        
        # Calculate remaining time
           now = datetime.now()
           expiration = user["expiration_time"]
        
        # If already expired, return 0
           if expiration < now:
              return timedelta(0)
            
           return expiration - now
        except Exception as e:
          logger.error(f"Error getting expiration time: {e}")
          return None

    def get_expiration_time_formatted(self, user_id):
        """Get formatted string of time remaining until user's premium expires
    
    Returns:
        str: Formatted time string (e.g. "3 days, 5 hours, 30 minutes") or
             appropriate message if no expiration or expired
    """
        try:
          remaining = self.get_expiration_time_remaining(user_id)
        
          if remaining is None:
            return "No expiration set"
        
          if remaining.total_seconds() <= 0:
            return "Expired"
        
        # Format remaining time
          days = remaining.days
          hours, remainder = divmod(remaining.seconds, 3600)
          minutes, seconds = divmod(remainder, 60)
        
          time_parts = []
          if days > 0:
            time_parts.append(f"{days} day{'s' if days != 1 else ''}")
          if hours > 0:
            time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
          if minutes > 0:
            time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        
        # Only add seconds if less than 1 hour remains
          if not days and hours < 1 and seconds > 0:
            time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
            
          if not time_parts:
            return "Less than a second"
            
          return ", ".join(time_parts)
        except Exception as e:
          logger.error(f"Error formatting expiration time: {e}")
          return "Error determining expiration"

    def redeem_key(self, key, user_id):
       """Redeem a key for a user"""
       try:
        # Get user data first
          user = self.users.find_one({"user_id": user_id})
          if not user:
            # Create user record if it doesn't exist
            self.add_user(user_id)
            user = self.users.find_one({"user_id": user_id})
        
        # Check for cooldown first
          last_redeem = user.get("last_key_redemption")
          if last_redeem and (datetime.now() - last_redeem).total_seconds() < 60:
              return False, "Please wait at least 1 minute between key redemptions."
            
        # Now check if user is already authorized
          if user.get("premium_level", 0) > 0:
            # Update last redemption timestamp even for failed attempts
              self.users.update_one({"user_id": user_id}, {"$set": {"last_key_redemption": datetime.now()}})
              return False, "You are already authorized. You don't need to redeem any key to use features of the bot."
            
          key_data = self.get_key(key)
          if not key_data:
            return False, "Key not found"
          if key_data["redeemed_by"]:
            return False, "Key already redeemed"
          if key_data["expiration_time"] and key_data["expiration_time"] < datetime.now():
            return False, "Key expired"
            
        # Get current values first
          current_level = user.get("premium_level", 0)
          current_expiration = user.get("expiration_time")
          current_msg_limit = user.get("message_limit")
        
        # Calculate new values
          new_level = max(current_level, key_data["premium_level"])
        
        # For expiration time, extend if exists, otherwise use key's expiration time
          if current_expiration and key_data["expiration_time"]:
            # Use the later expiration date
              new_expiration = max(current_expiration, key_data["expiration_time"])
          elif current_expiration:
              new_expiration = current_expiration
          else:
              new_expiration = key_data["expiration_time"]
            
        # For message limit, add if both exist, otherwise use the non-None value
          if current_msg_limit is not None and key_data["message_limit"] is not None:
              new_msg_limit = current_msg_limit + key_data["message_limit"]
          elif current_msg_limit is not None:
              new_msg_limit = current_msg_limit
          else:
              new_msg_limit = key_data["message_limit"]
            
          update_data = {
            "premium_level": new_level,
            "expiration_time": new_expiration,
            "message_limit": new_msg_limit,
            "last_key_redemption": datetime.now()
        }
        
          self.users.update_one({"user_id": user_id}, {"$set": update_data})
          self.keys.update_one(
            {"key": key},
            {"$set": {"redeemed_by": user_id, "redeemed_at": datetime.now()}}
        )
          return True, "Key redeemed successfully"
       except Exception as e:
          logger.error(f"Error redeeming key: {e}")
          return False, "Error redeeming key"

    ### Batch Processing ###
    def set_user_in_batch(self, user_id, in_batch=True):
        """Set user's batch status"""
        try:
            self.users.update_one(
                {"user_id": user_id},
                {"$set": {"in_batch": in_batch}}
            )
            return True
        except Exception as e:
            logger.error(f"Error setting batch status: {e}")
            return False

    def is_user_in_batch(self, user_id):
        """Check if user is in batch"""
        try:
            user = self.users.find_one({"user_id": user_id})
            return user.get("in_batch", False) if user else False
        except Exception as e:
            logger.error(f"Error checking batch status: {e}")
            return False

    ### Statistics ###
    def increment_cloned_count(self, user_id, count=1):
        """Update cloned messages count and decrement message limit if applicable"""
        try:
            user = self.users.find_one({"user_id": user_id})
            if user and user.get("message_limit") is not None:
                if user["message_limit"] <= 0:
                    return False
                self.users.update_one({"user_id": user_id}, {"$inc": {"message_limit": -1}})
            self.stats.update_one({}, {"$inc": {"cloned_messages": count}})
            return True
        except Exception as e:
            logger.error(f"Error updating cloned count: {e}")
            return False

    def increment_downloaded_count(self, count=1):
        """Update downloaded messages count"""
        try:
            self.stats.update_one({}, {"$inc": {"downloaded_messages": count}})
            return True
        except Exception as e:
            logger.error(f"Error updating download count: {e}")
            return False

    def get_stats(self):
        """Get all statistics"""
        try:
            return self.stats.find_one({}) or {}
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}

    def get_user_count(self):
        """Get total number of users"""
        try:
            return self.users.count_documents({})
        except Exception as e:
            logger.error(f"Error getting user count: {e}")
            return 0

    def get_cloned_messages_count(self):
        """Get total cloned messages count"""
        try:
            stats = self.stats.find_one({})
            return stats.get("cloned_messages", 0) if stats else 0
        except Exception as e:
            logger.error(f"Error getting cloned messages count: {e}")
            return 0

    def get_downloaded_messages_count(self):
        """Get total downloaded messages count"""
        try:
            stats = self.stats.find_one({})
            return stats.get("downloaded_messages", 0) if stats else 0
        except Exception as e:
            logger.error(f"Error getting downloaded messages count: {e}")
            return 0

    def get_recent_users(self, limit=5):
        """Get recently active users"""
        try:
            return list(self.users.find().sort("last_activity", -1).limit(limit))
        except Exception as e:
            logger.error(f"Error getting recent users: {e}")
            return []

    ### Ban System ###
    def ban_user(self, user_id, banned_by=None, reason=None):
      """Ban a user"""
      try:
          result = self.banned.update_one(
            {"user_id": user_id},
            {"$set": {
                "banned_by": banned_by, 
                "timestamp": datetime.now(),
                "reason": reason  # Store the reason
            }},
            upsert=True
        )
          return result.upserted_id is not None or result.modified_count > 0
      except Exception as e:
          logger.error(f"Error banning user: {e}")
          return False

    def is_user_banned(self, user_id):
      """Check if user is banned and get reason
    
    Returns:
        tuple: (is_banned, reason) where is_banned is a boolean and reason is a string or None
    """
      try:
          ban_data = self.banned.find_one({"user_id": user_id})
          if ban_data:
              return True, ban_data.get("reason")
          return False, None
      except Exception as e:
          logger.error(f"Error checking ban status: {e}")
          return False, None

    def unban_user(self, user_id):
        """Unban a user"""
        try:
            result = self.banned.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error unbanning user: {e}")
            return False
    
    def get_thumbnail_enabled(self, user_id):
        """Get thumbnail enabled status for user"""
        try:
            user = self.users.find_one({"user_id": user_id})
            # Default to True if not set for backward compatibility
            return user.get("thumbnail_enabled", True) if user else True
        except Exception as e:
            logger.error(f"Error getting thumbnail status: {e}")
            return True  # Default to enabled on error
    
    def set_thumbnail_enabled(self, user_id, enabled=True):
        """Set thumbnail enabled status for user"""
        try:
            result = self.users.update_one(
                {"user_id": user_id},
                {"$set": {"thumbnail_enabled": enabled}}
            )
            return result.modified_count > 0 or result.matched_count > 0
        except Exception as e:
            logger.error(f"Error setting thumbnail status: {e}")
            return False

    ### User Info ###
    def get_user_info(self, user_id):
        """Get user information"""
        try:
            user = self.users.find_one({"user_id": user_id})
            if user:
                return {
                    "user_id": user["user_id"],
                    "username": user.get("username"),
                    "first_name": user.get("first_name"),
                    "last_name": user.get("last_name"),
                    "thumbnail": user.get("thumbnail"),
                    "thumbnail_enabled": user.get("thumbnail_enabled", False),  # Default to True if not set
                    "premium_level": user.get("premium_level", 0),
                    "expiration_time": user.get("expiration_time"),
                    "message_limit": user.get("message_limit"),
                    "chat_id": user.get("chat_id"),
                    "last_activity": user.get("last_activity")
                }
            return None
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            return None

    ### User Lists ###
    def get_all_users(self):
        """Get all users"""
        try:
            return list(self.users.find({}))
        except Exception as e:
            logger.error(f"Error getting users: {e}")
            return []

    def get_authorized_users(self):
        """Get authorized users"""
        try:
            return list(self.users.find({"premium_level": {"$gt": 0}}))
        except Exception as e:
            logger.error(f"Error getting authorized users: {e}")
            return []

    def get_banned_users(self):
        """Get banned users"""
        try:
            return list(self.banned.find({}))
        except Exception as e:
            logger.error(f"Error getting banned users: {e}")
            return []

    def set_watermark_text(self, user_id, text):
       """Set watermark text for user"""
       try:
          result = self.users.update_one(
            {"user_id": user_id},
            {"$set": {"watermark_text": text}}
          )
          return result.modified_count > 0
       except Exception as e:
          logger.error(f"Error setting watermark text: {e}")
          return False

    def get_watermark_text(self, user_id):
       """Get user's watermark text"""
       try:
          user = self.users.find_one({"user_id": user_id})
          return user.get("watermark_text") if user else None
       except Exception as e:
          logger.error(f"Error getting watermark text: {e}")
          return None
      
    def warn_user(self, user_id, warned_by=None, reason=None):
     """Warn a user and return the current warning count"""
     try:
        # Get current warnings
        current_warnings = self.get_user_warnings(user_id)
        
        # Add new warning
        warning_data = {
            "user_id": user_id,
            "warned_by": warned_by,
            "reason": reason,
            "timestamp": datetime.now()
        }
        self.warnings.insert_one(warning_data)
        
        # Return the new warning count
        return current_warnings + 1
     except Exception as e:
        logger.error(f"Error warning user: {e}")
        return 0

    def get_user_warnings(self, user_id):
      """Get number of warnings for a user"""
      try:
          return self.warnings.count_documents({"user_id": user_id})
      except Exception as e:
          logger.error(f"Error getting user warnings: {e}")
          return 0

    def get_user_warnings_details(self, user_id):
      """Get details of all warnings for a user"""
      try:
          return list(self.warnings.find({"user_id": user_id}).sort("timestamp", -1))
      except Exception as e:
          logger.error(f"Error getting warning details: {e}")
          return []

    def remove_warning(self, user_id, warning_id=None):
      """Remove a warning from a user
    
    Args:
        user_id: The user's ID
        warning_id: Specific warning ID to remove (if None, removes most recent)
        
    Returns:
        bool: True if warning was removed, False otherwise
    """
      try:
          if warning_id:
            # Remove specific warning
              result = self.warnings.delete_one({"_id": warning_id, "user_id": user_id})
          else:
            # Remove most recent warning
              most_recent = self.warnings.find_one(
                {"user_id": user_id}, 
                sort=[("timestamp", -1)]
            )
              if most_recent:
                result = self.warnings.delete_one({"_id": most_recent["_id"]})
              else:
                  return False
                
          return result.deleted_count > 0
      except Exception as e:
          logger.error(f"Error removing warning: {e}")
          return False

    def clear_warnings(self, user_id):
      """Remove all warnings for a user"""
      try:
          result = self.warnings.delete_many({"user_id": user_id})
          return result.deleted_count > 0
      except Exception as e:
          logger.error(f"Error clearing warnings: {e}")
          return False
               
    def mute_user(self, user_id, muted_by=None, duration=None, reason=None):
      """Mute a user for a specified duration
    
    Args:
        user_id (int): User ID to mute
        muted_by (int): Admin who issued the mute
        duration (int): Duration in minutes, None for indefinite
        reason (str): Reason for muting
        
    Returns:
        bool: True if muted successfully, False otherwise
    """
      try:
          mute_until = datetime.now() + timedelta(minutes=duration) if duration else None
          result = self.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "muted": True,
                "muted_by": muted_by,
                "mute_reason": reason,
                "mute_timestamp": datetime.now(),
                "mute_until": mute_until
            }}
        )
          return result.modified_count > 0 or result.matched_count > 0
      except Exception as e:
          logger.error(f"Error muting user: {e}")
          return False

    def unmute_user(self, user_id):
      """Unmute a user
    
    Args:
        user_id (int): User ID to unmute
        
    Returns:
        bool: True if unmuted successfully, False otherwise
    """
      try:
          result = self.users.update_one(
            {"user_id": user_id},
            {"$unset": {
                "muted": "",
                "muted_by": "",
                "mute_reason": "",
                "mute_timestamp": "",
                "mute_until": ""
            }}
        )
          return result.modified_count > 0
      except Exception as e:
          logger.error(f"Error unmuting user: {e}")
          return False

    def is_user_muted(self, user_id):
      """Check if user is muted and get reason and remaining time
    
    Returns:
        tuple: (is_muted, reason, time_remaining) where:
               is_muted is a boolean
               reason is a string or None
               time_remaining is a timedelta or None (if mute is indefinite)
    """
      try:
          user = self.users.find_one({"user_id": user_id})
          if not user or "muted" not in user or not user["muted"]:
              return False, None, None
            
        # Check if mute has expired
          if user.get("mute_until"):
              if user["mute_until"] < datetime.now():
                # Mute expired, remove it
                  self.unmute_user(user_id)
                  return False, None, None
              else:
                # Calculate remaining time
                  time_remaining = user["mute_until"] - datetime.now()
                  return True, user.get("mute_reason"), time_remaining
          else:
            # Indefinite mute
              return True, user.get("mute_reason"), None
      except Exception as e:
          logger.error(f"Error checking mute status: {e}")
          return False, None, None

    def get_mute_time_formatted(self, user_id):
      """Get formatted string of time remaining for mute
    
    Returns:
        str: Formatted time string or appropriate message
    """
      try:
          is_muted, _, remaining = self.is_user_muted(user_id)
        
          if not is_muted:
              return "Not muted"
        
          if remaining is None:
              return "Indefinite"
        
        # Format remaining time
          days = remaining.days
          hours, remainder = divmod(remaining.seconds, 3600)
          minutes, seconds = divmod(remainder, 60)
        
          time_parts = []
          if days > 0:
              time_parts.append(f"{days} day{'s' if days != 1 else ''}")
          if hours > 0:
              time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
          if minutes > 0:
              time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        
          if not days and not hours and seconds > 0:
              time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
            
          if not time_parts:
              return "Less than a second"
            
          return ", ".join(time_parts)
      except Exception as e:
          logger.error(f"Error formatting mute time: {e}")
          return "Error determining mute time"
             
    def get_user_level(self, user_id):
       """Get user's premium level
       
       Args:
           user_id (int): The user's ID
           
       Returns:
           int: The user's premium level (0 for free users, 1+ for premium)
       """
       try:
          user = self.users.find_one({"user_id": user_id})
          if not user:
             return 0
          
          # Check if premium has expired
          if user.get("expiration_time") and user["expiration_time"] < datetime.now():
             return 0
             
          return user.get("premium_level", 0)
       except Exception as e:
          logger.error(f"Error getting user level: {e}")
          return 0
  
  

# Initialize database instance
db = Database()
