import os
import re
from .. import Bot
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified
from main.plugins.db import db
import cloudinary
import cloudinary.uploader
import logging
from config import CLOUD_NAME, API_KEY, API_SECRET, LOG_GROUP
from datetime import timedelta

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

# Cloudinary configuration
cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# Constants
START_PIC = "https://res.cloudinary.com/drlkucdog/image/upload/v1739358426/k0q70eqfnfkwlj54ydad.jpg"
START_TEXT = """**Send me the link of any message from restricted channels to clone it here.**

• For private channels, send the invite link first
• Use `/batch` for bulk processing (up to 10K files)
• Check `/help` for detailed guides"""

HELP_TEXT = """
**📖 Help Guide**

**Batch Command:**
```/batch [start_link] [range]```
- Supports multiple range formats:
  - `100` (download next 100 messages)
  - `100-200` (specific range)
  - `[100,200]U[300,400]` (multiple ranges)
  - `[100,400]-{150}` (exclude specific messages)

**Other Commands:**
- `/me` : Check your account info
- `/settings` : Configure bot preferences
- `/redeem <key>` : Activate premium features
- `/id` : Get chat/user ID

**Note:** Max 100,000 files per batch. Use `/cancel` to stop ongoing processes.
"""
PLANS_TEXT = """
<b>🌟 PREMIUM PLANS 🌟</b>

<b>🔹 BASIC PLAN 🚀</b> - <i>₹100 for 10 days</i>

✅ Invite link support for private channels
✅ High-speed uploads with priority processing
✅ Unlimited links (public and private)
✅ Forward messages from bots
✅ Public & private groups supported
✅ /batch command for auto-saving up to 99 messages
✅ Support for files up to 4GB
✅ Reduced cooldown timer (only 2 seconds)
✅ Custom thumbnail, watermark & caption editing

<b>🔹 MONTHLY PLAN 💎</b> - <i>₹300 per month</i>

✅ All features of Basic Plan
✅ Priority support from our team
✅ Request custom features (subject to feasibility)
✅ Extended batch support (up to 500 messages)
✅ No daily usage limits
✅ First access to new features

<b>📞 Contact:</b> @its_me_kabir_singh

<b>⚠️ Note:</b> We strive to provide the best possible support and will implement requested features if technically feasible. All purchases are final and non-refundable.

<i>Use /redeem to activate your premium plan after purchase</i>
"""
RULES_TEXT = """
<b>📜 Bot Usage Rules & Terms</b>

<b>🚫 Prohibited Content</b>
• No carding or financial fraud related material
• No sexual, adult, or pornographic content
• No buying/selling of products or services
• No blank/empty groups or channels
• No pirated movies, web series, or anime
• No non-educational entertainment media

<b>✅ Acceptable Content</b>
• Educational materials only
• Academic resources
• Learning tutorials
• Informational content with educational value

<b>📋 Terms and Conditions</b>
By using this bot, you agree:
• To comply with all listed rules
• That your access may be revoked without notice for violations
• To use the bot solely for legitimate, educational purposes
• Not to attempt to bypass restrictions or limitations

<b>⚠️ Liability</b>
• Users are responsible for content they process
• The bot owner assumes no liability for user actions
• We reserve the right to modify these terms at any time

<b>📣 Enforcement</b>
Rules are strictly enforced to maintain the educational integrity of our service. Violations may result in temporary or permanent suspension of access.

<i>Last Updated: March 15, 2025</i>
<i>Team Voice</i>
"""

async def add_user(user):
    try:
        db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
    except Exception as e:
        print(f"Database Error: {e}")

@Bot.on_message(filters.command("msg"))
async def msg_command(client, message):
    """Allow users to send a message to admin log group"""
    user_id = message.from_user.id
    
    # Check if user is banned
    is_banned, ban_reason = db.is_user_banned(user_id)
    if is_banned:
        await message.reply(f"❌ You are not allowed to send messages. Reason: {ban_reason or 'No reason provided'}")
        return
    
    # Check if user is muted
    is_muted, mute_reason, _ = db.is_user_muted(user_id)
    if is_muted:
        mute_time = db.get_mute_time_formatted(user_id)
        mute_text = f"❌ You are currently muted for {mute_time} remaining"
        if mute_reason:
            mute_text += f". Reason: {mute_reason}"
        await message.reply(mute_text)
        return
    
    # Extract message content
    command_text = message.text.strip() if message.text else ""
    
    # Check if this is a reply to an image
    is_reply = message.reply_to_message is not None
    replied_to_image = is_reply and hasattr(message.reply_to_message, 'photo') and message.reply_to_message.photo
    has_image = hasattr(message, 'photo') and message.photo
    
    # For text processing
    try:
        if hasattr(message, 'command') and len(message.command) > 1:
            text_without_command = ' '.join(message.command[1:])
        else:
            # If the message is a caption of an image, extract text after /msg
            if has_image and message.caption and message.caption.startswith("/msg"):
                text_without_command = message.caption[4:].strip()
            else:
                text_without_command = ""
    except Exception as e:
        print(f"Error extracting command text: {e}")
        text_without_command = ""
    
    # If command has no text and is not replying to an image, show usage
    if not text_without_command and not has_image and not replied_to_image:
        await message.reply("Please include a message. Usage: `/msg Your message here` or reply to an image with `/msg` or send an image with caption starting with `/msg`")
        return
    
    # Format the message for the log group
    user_info = db.get_user_info(user_id) or {}
    username = user_info.get('username', 'No username')
    first_name = user_info.get('first_name', '')
    last_name = user_info.get('last_name', '')
    full_name = f"{first_name} {last_name}".strip() or "Unknown"
    
    print(f"Processing /msg command from user {user_id}")
    print(f"Has image: {has_image}, Replied to image: {replied_to_image}")
    
    try:
        # Using LOG_GROUP imported from config.py
        # (No need to define it here as it's already imported)
        
        # Check if log channel/group is defined
        if not LOG_GROUP:
            print("LOG_GROUP not defined for /msg command")
            await message.reply("❌ Message service is currently unavailable. Please try again later.")
            return
        
        sent = False
        
        # Handle different cases with retry logic to address RANDOM_ID_DUPLICATE error
        max_retries = 3
        retry_count = 0
        
        # Import required modules for error handling
        import pyrogram.errors
        import asyncio
        
        while retry_count < max_retries and not sent:
            try:
                # 1. Text message only
                if not has_image and not replied_to_image:
                    log_message = (
                        "📨 **New Message**\n\n"
                        f"**User ID:** `{user_id}`\n"
                        f"**Username:** {('@' + username) if username and username != 'No username' else 'None'}\n"
                        f"**Name:** {full_name}\n\n"
                        f"**Message:**\n{text_without_command}"
                    )
                    await client.send_message(LOG_GROUP, log_message)
                    sent = True
                    print(f"Sent text message to LOG_GROUP: {LOG_GROUP}")
                
                # 2. Message with image attached
                elif has_image:
                    # Get the caption if any
                    caption = message.caption or ""
                    
                    # Remove command from caption if present
                    if caption.startswith("/msg"):
                        caption = caption[4:].strip()
                    
                    # Check if caption is too long for Telegram (4096 character limit)
                    if len(caption) > 1000:  # Setting a safer limit
                        caption = caption[:997] + "..."
                        
                    log_message = (
                        "📨 **New Message with Image**\n\n"
                        f"**User ID:** `{user_id}`\n"
                        f"**Username:** {('@' + username) if username and username != 'No username' else 'None'}\n"
                        f"**Name:** {full_name}\n\n"
                        f"**Caption:**\n{caption}"
                    )
                    
                    try:
                        # Get the largest photo (best quality)
                        photo = message.photo[-1]
                        
                        # Send the photo with the formatted caption
                        await client.send_photo(
                            chat_id=LOG_GROUP,
                            photo=photo.file_id,
                            caption=log_message
                        )
                        sent = True
                        print(f"Sent photo with caption to LOG_GROUP: {LOG_GROUP}")
                    except Exception as img_err:
                        print(f"Error sending image: {img_err}")
                        # Fallback to sending message without image
                        fallback_msg = log_message + "\n\n**Note:** Image could not be forwarded due to an error."
                        await client.send_message(LOG_GROUP, fallback_msg)
                        sent = True
                
                # 3. Reply to an image
                elif replied_to_image:
                    # Get the caption if any from the original image
                    original_caption = message.reply_to_message.caption or ""
                    
                    log_message = (
                        "📨 **Reply to Image**\n\n"
                        f"**User ID:** `{user_id}`\n"
                        f"**Username:** {('@' + username) if username and username != 'No username' else 'None'}\n"
                        f"**Name:** {full_name}\n\n"
                        f"**Original Caption:** {original_caption}\n\n"
                        f"**Reply:**\n{text_without_command}"
                    )
                    
                    try:
                        # Get the largest photo (best quality) from the replied message
                        photo = message.reply_to_message.photo[-1]
                        
                        # Send the photo with the formatted caption
                        await client.send_photo(
                            chat_id=LOG_GROUP,
                            photo=photo.file_id,
                            caption=log_message
                        )
                        sent = True
                        print(f"Sent reply to image to LOG_GROUP: {LOG_GROUP}")
                    except Exception as reply_err:
                        print(f"Error sending replied image: {reply_err}")
                        # Fallback to sending message without image
                        fallback_msg = log_message + "\n\n**Note:** Original image could not be forwarded due to an error."
                        await client.send_message(LOG_GROUP, fallback_msg)
                        sent = True
                
            except pyrogram.errors.FloodWait as e:
                # Handle FloodWait error by waiting
                print(f"FloodWait error: {e}. Waiting for {e.value} seconds")
                await asyncio.sleep(e.value)
                retry_count += 1
            
            except pyrogram.errors.RPCError as e:
                if "RANDOM_ID_DUPLICATE" in str(e):
                    print(f"RANDOM_ID_DUPLICATE error, retrying ({retry_count+1}/{max_retries})")
                    await asyncio.sleep(1)  # Wait a bit before retrying
                    retry_count += 1
                else:
                    print(f"Unhandled RPC error: {e}")
                    raise
        
        # Confirm to user
        if sent:
            await message.reply("✅ Your message has been sent to the admins.")
            print(f"User {user_id} sent message to admins successfully")
        else:
            print(f"Failed to send message for user {user_id} after {max_retries} retries")
            await message.reply("❌ Failed to send your message. Please try again later.")
            
    except Exception as e:
        print(f"Error in msg_command: {e}")
        await message.reply("❌ An error occurred while sending your message. Please try again later.")
        
# Add buttons for the plans display
@Bot.on_message(filters.command(["plan", "plans", "buy", "premium"]))
async def plans_handler(client, message):
    user = message.from_user
    await add_user(user)
    
    # Check if user is banned
    is_banned, ban_reason = db.is_user_banned(user.id)
    if is_banned:
        await message.reply(f"You are banned from using this bot. Reason: {ban_reason or 'No reason provided'}")
        return

    # Check if user is muted
    is_muted, mute_reason, _ = db.is_user_muted(user.id)
    if is_muted:
        mute_time = db.get_mute_time_formatted(user.id)
        mute_text = f"🔇 You are muted for {mute_time} remaining"
        if mute_reason:
            mute_text += f". Reason: {mute_reason}"
        await message.reply(mute_text)
        return
    
    # Create inline keyboard buttons
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Purchase Basic Plan (₹100)", url="https://t.me/its_me_kabir_singh")],
        [InlineKeyboardButton("💎 Purchase Monthly Plan (₹300)", url="https://t.me/its_me_kabir_singh")],
        [InlineKeyboardButton("❓ FAQ", callback_data="plan_faq"),
         InlineKeyboardButton("📊 Compare Plans", callback_data="plan_compare")]
    ])
    
    try:
        # Use a plan image if available, otherwise just send text
        plan_pic = "https://res.cloudinary.com/drlkucdog/image/upload/v1739358426/premium_plans.jpg"
        try:
            await message.reply_photo(
                photo=plan_pic,
                caption=PLANS_TEXT,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            # Fallback to text-only if image fails
            await message.reply(
                PLANS_TEXT,
                reply_markup=buttons,
                parse_mode=enums.ParseMode.HTML
            )
    except Exception as e:
        print(f"Plans Display Error: {e}")
        await message.reply("🔄 Error displaying plans. Please try again later.")

# Add callbacks for the plan-related buttons
@Bot.on_callback_query(filters.regex(r"^plan_faq$"))
async def plan_faq_callback(client, query):
    faq_text = """
<b>📋 Frequently Asked Questions</b>

<b>Q: How do I activate my premium plan?</b>
A: After purchase, you'll receive a redemption key. Use /redeem &lt;key&gt; to activate.

<b>Q: Can I upgrade from Basic to Monthly?</b>
A: Yes! Contact @its_me_kabir_singh for a pro-rated upgrade.

<b>Q: What happens when my plan expires?</b>
A: You'll revert to free tier. All settings are preserved if you renew.

<b>Q: How can I request a custom feature?</b>
A: Monthly plan members can message @its_me_kabir_singh with feature requests.

<b>Q: Is there a refund policy?</b>
A: All purchases are non-refundable, but we ensure satisfaction with our service.
"""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Plans", callback_data="back_to_plans")]
    ])
    
    try:
        await query.edit_message_text(
            faq_text,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )
    except MessageNotModified:
        pass

@Bot.on_callback_query(filters.regex(r"^plan_compare$"))
async def plan_compare_callback(client, query):
    compare_text = """
<b>📊 Plan Comparison</b>

<b>Feature | Free | Basic | Monthly</b>
---------------------------------
Public Links     | ✅ | ✅ | ✅
Private Links    | ❌ | ✅ | ✅
Batch Limit      | 0 | 99 | 10K
Bot Forwards     | ❌ | ✅ | ✅
File Size Limit  | 2GB | 4GB | 4GB
Cooldown Timer   | 60s | 2s | 2s
Thumbnail        | ❌ | ✅ | ✅
Watermark        | ❌ | ✅ | ✅
Caption Editing  | ❌ | ✅ | ✅
Custom Features  | ❌ | ❌ | ✅
Price            | Free | ₹100/10d | ₹300/mo
"""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Plans", callback_data="back_to_plans")]
    ])
    
    try:
        await query.edit_message_text(
            compare_text,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )
    except MessageNotModified:
        pass

@Bot.on_callback_query(filters.regex(r"^back_to_plans$"))
async def back_to_plans_callback(client, query):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Purchase Basic Plan (₹100)", url="https://t.me/its_me_kabir_singh")],
        [InlineKeyboardButton("💎 Purchase Monthly Plan (₹300)", url="https://t.me/its_me_kabir_singh")],
        [InlineKeyboardButton("❓ FAQ", callback_data="plan_faq"),
         InlineKeyboardButton("📊 Compare Plans", callback_data="plan_compare")]
    ])
    
    try:
        await query.edit_message_text(
            PLANS_TEXT,
            reply_markup=buttons,
            parse_mode=enums.ParseMode.HTML
        )
    except MessageNotModified:
        pass

@Bot.on_message(filters.command("start"))
async def start_handler(client, message):
    user = message.from_user
    user_id = message.from_user.id
    await add_user(user)
    
    # Check if user is banned
    is_banned, ban_reason = db.is_user_banned(user_id)
    if is_banned:
      await message.reply(f"You are banned from using this bot. Reason: {ban_reason or 'No reason provided'}")
      return

    # Check if user is muted
    is_muted, mute_reason, _ = db.is_user_muted(user_id)
    if is_muted:
      mute_time = db.get_mute_time_formatted(user_id)
      mute_text = f"🔇 You are muted for {mute_time} remaining"
      if mute_reason:
        mute_text += f". Reason: {mute_reason}"
      await message.reply(mute_text)
      return
        
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Channel", url="https://t.me/officialharsh_g")]
    ])
    
    try:
        await message.reply_photo(
            photo=START_PIC,
            caption=START_TEXT,
            reply_markup=buttons
        )
    except Exception as e:
        print(f"Start Error: {e}")
        await message.reply("🔄 Please try again later.")

@Bot.on_message(filters.command("redeem"))
async def redeem_handler(client, message):
    if len(message.command) < 2:
        return await message.reply(
            "<b>Usage:</b> <code>/redeem &lt;key&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    
    key = message.command[1]
    success, response = db.redeem_key(key, message.from_user.id)
    await message.reply(response)

@Bot.on_message(filters.command("me"))
async def me_handler(client, message):
    user_id = message.from_user.id
    user_info = db.get_user_info(user_id)
    
    if not user_info:
        return await message.reply("❌ User data not found.")
    
    expiry = db.get_expiration_time_remaining(user_id)
    expiry_str = "Lifetime" if not expiry else (
        "Expired" if expiry.days < 0 else
        f"{expiry.days}d {expiry.seconds//3600}h {(expiry.seconds%3600)//60}m"
    )
    
    response = f"""
**🆔 ID:** `{user_info['user_id']}`
**👤 Name:** {user_info.get('first_name', '')} {user_info.get('last_name', '')}
**🌟 Premium:** Tier {user_info.get('premium_level', 0)}
**⏳ Expiry:** {expiry_str}
**🔐 Status:** {'✅ Authorized' if db.is_user_authorized(user_id) else '❌ Unauthorized'}
"""
    await message.reply(response)

@Bot.on_message(filters.command("id"))
async def id_handler(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else None
    
    if message.chat.type == enums.ChatType.PRIVATE:
        if user_id == chat_id:
            response = f"**👤 Your ID:** `{user_id}`"
        else:
            response = f"**💬 Chat ID:** `{chat_id}`\n**👤 Your ID:** `{user_id}`"
    else:
        response = f"**💬 Chat ID:** `{chat_id}`"
    
    await message.reply(response)

@Bot.on_message(filters.command("settings"))
async def settings_handler(client, message):
    user_id = message.from_user.id
    if not db.is_user_authorized(user_id):
        return await message.reply("🔒 Please authenticate first.")
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Thumbnail", callback_data="thumb_settings"),
         InlineKeyboardButton("📝 File Name", callback_data="file_settings")],
        [InlineKeyboardButton("💬 Caption", callback_data="caption_settings"),
         InlineKeyboardButton("🔑 Auth", callback_data="auth_settings")],
        [InlineKeyboardButton("💭 Chat ID", callback_data="chatid_settings"),
         InlineKeyboardButton("❌ Close", callback_data="close_settings")]
    ])
    
    await message.reply("**⚙️ Settings Panel**", reply_markup=buttons)

@Bot.on_callback_query(filters.regex(r"^thumb_settings$"))
async def thumb_settings(client, query):
    user_id = query.from_user.id
    thumb = db.get_thumbnail(user_id)
    enabled = db.get_thumbnail_enabled(user_id)
    watermark = db.get_watermark_text(user_id) or "Not set"
    
    text = f"""
**🖼 Thumbnail Settings**

• Current: {"✅ Set" if thumb else "❌ None"}
• Status: {"🟢 Enabled" if enabled else "🔴 Disabled"}
• Watermark: {watermark}

**Priority Order:**
1. Watermarked Thumbnail
2. Custom Image/URL
3. Auto-generated
"""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Set Thumbnail", callback_data="set_thumb"),
         InlineKeyboardButton("🔄 Toggle", callback_data="toggle_thumb")],
        [InlineKeyboardButton("✍️ Set Watermark", callback_data="set_watermark"),
         InlineKeyboardButton("🗑 Remove", callback_data="remove_thumb")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_settings")]
    ])
    
    try:
        await query.edit_message_text(text, reply_markup=buttons)
    except MessageNotModified:
        pass

@Bot.on_callback_query(filters.regex(r"^toggle_thumb$"))
async def toggle_thumbnail(client, query):
    user_id = query.from_user.id
    new_state = not db.get_thumbnail_enabled(user_id)
    db.set_thumbnail_enabled(user_id, new_state)
    status = "enabled ✅" if new_state else "disabled ❌"
    await query.answer(f"Thumbnail {status}")

@Bot.on_callback_query(filters.regex(r"^remove_thumb$"))
async def remove_thumbnail(client, query):
    user_id = query.from_user.id
    if db.remove_thumbnail(user_id):
        await query.answer("✅ Thumbnail removed")
    else:
        await query.answer("❌ No thumbnail exists")

async def upload_thumbnail(file_path, user_id):
    try:
        result = cloudinary.uploader.upload(file_path)
        if result and "secure_url" in result:
            db.set_thumbnail(user_id, result["secure_url"])
            return True, "✅ Thumbnail uploaded successfully"
        return False, "❌ Cloud upload failed"
    except Exception as e:
        print(f"Cloudinary Error: {e}")
        return False, "🚫 Error processing thumbnail"
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

@Bot.on_callback_query(filters.regex(r"^set_thumb$"))
async def set_thumbnail(client, query):
    await query.message.delete()
    msg = await client.send_message(query.from_user.id, "**📤 Send image/URL (JPEG/PNG/WEBP, max 5MB):**")
    
    try:
        response = await client.listen(
            chat_id=query.from_user.id,
            filters=filters.photo | filters.text,
            timeout=60
        )
        
        if response.text:
            if validate_thumbnail(response.text):
                db.set_thumbnail(query.from_user.id, response.text.strip())
                await msg.edit("✅ Thumbnail URL set")
            else:
                await msg.edit("❌ Invalid image URL format")
        elif response.photo:
            file_path = await response.download()
            success, message = await upload_thumbnail(file_path, query.from_user.id)
            await msg.edit(message)
    except TimeoutError:
        await msg.edit("⏰ Response timed out")

def validate_thumbnail(url):
    return re.match(
        r'^https?://.*\.(jpeg|jpg|png|webp)(\?.*)?$', 
        url, 
        re.IGNORECASE
    )

@Bot.on_callback_query(filters.regex(r"^set_watermark$"))
async def set_watermark(client, query):
    await query.message.delete()
    msg = await client.send_message(query.from_user.id, "**✍️ Enter watermark text (or 'no' to remove):**")
    
    try:
        response = await client.listen(
            chat_id=query.from_user.id,
            filters=filters.text,
            timeout=60
        )
        text = response.text.strip().lower()
        
        if text == "no":
            db.set_watermark_text(response.from_user.id, None)
            await msg.edit("✅ Watermark removed")
        else:
            db.set_watermark_text(response.from_user.id, text)
            await msg.edit("✅ Watermark updated")
    except TimeoutError:
        await msg.edit("⏰ Response timed out")

@Bot.on_callback_query(filters.regex(r"^close_settings$"))
async def close_settings(client, query):
    await query.message.delete()

@Bot.on_callback_query(filters.regex(r"^main_settings$"))
async def main_settings(client, query):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Thumbnail", callback_data="thumb_settings"),
         InlineKeyboardButton("📝 File Name", callback_data="file_settings")],
        [InlineKeyboardButton("💬 Caption", callback_data="caption_settings"),
         InlineKeyboardButton("🔑 Auth", callback_data="auth_settings")],
        [InlineKeyboardButton("💭 Chat ID", callback_data="chatid_settings"),
         InlineKeyboardButton("❌ Close", callback_data="close_settings")]
    ])
    
    try:
        await query.edit_message_text("**⚙️ Settings Panel**", reply_markup=buttons)
    except MessageNotModified:
        pass

@Bot.on_callback_query(filters.regex(r"^(file_settings|caption_settings|auth_settings)$"))
async def coming_soon(client, query):
    feature = query.data.split("_")[0]
    await query.answer(f"🛠 {feature} settings coming soon!", show_alert=True)

@Bot.on_callback_query(filters.regex(r"^chatid_settings$"))
async def chatid_settings(client, query):
    user_id = query.from_user.id
    chat_id = db.get_chat_id(user_id)
    
    chat_info = "Not set"
    if chat_id:
        try:
            # Attempt to get chat information if available
            chat = await client.get_chat(chat_id)
            if chat.type == "private":
                chat_info = f"Private: {chat.first_name}"
            elif chat.type == "channel":
                chat_info = f"Channel: {chat.title}"
            elif chat.type in ["group", "supergroup"]:
                chat_info = f"Group: {chat.title}"
            else:
                chat_info = f"Type: {chat.type}, ID: {chat_id}"
        except Exception as e:
            chat_info = f"ID: {chat_id} (Unable to fetch details)"
    
    text = f"""
**💭 Chat ID Settings**

• Current: {"✅ Set" if chat_id else "❌ None"}
• Details: {chat_info}

**Instructions:**
• You can set a default chat where files will be sent
• Send channel/group username, link, or forward a message
"""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💭 Set Chat ID", callback_data="set_chatid")],
        [InlineKeyboardButton("🗑 Remove Chat ID", callback_data="remove_chatid") if chat_id else InlineKeyboardButton("ℹ️ Help", callback_data="chatid_help")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_settings")]
    ])
    
    try:
        await query.edit_message_text(text, reply_markup=buttons)
    except MessageNotModified:
        pass

@Bot.on_callback_query(filters.regex(r"^set_chatid$"))
async def set_chatid(client, query):
    await query.message.delete()
    msg = await client.send_message(
        query.from_user.id, 
        "**💭 Send one of the following:**\n\n"
        "• Channel/Group username (e.g., @channelname)\n"
        "• Channel/Group invite link\n"
        "• Forward a message from the target chat\n"
        "• Direct chat ID if you know it"
    )
    
    try:
        response = await client.listen(
            chat_id=query.from_user.id,
            timeout=60
        )
        
        chat_id = None
        error_msg = None
        
        if response.forward_from_chat:
            # Message was forwarded from a channel or group
            chat_id = response.forward_from_chat.id
        elif response.text:
            text = response.text.strip()
            
            # Handle direct chat ID input
            if text.startswith("-100") and text[4:].isdigit():
                chat_id = int(text)
            elif text.startswith("-") and text[1:].isdigit():
                chat_id = int(text)
            elif text.isdigit():
                # For user IDs or non-supergroup chats
                chat_id = int(text)
            else:
                # Handle username or link
                try:
                    # Try to resolve username or link to a chat
                    chat = await client.get_chat(text)
                    chat_id = chat.id
                except Exception as e:
                    error_msg = f"❌ Couldn't resolve chat: {str(e)}"
        
        if chat_id and not error_msg:
            # For channels and supergroups, ensure the ID starts with -100
            if isinstance(chat_id, int) and chat_id < 0:
                str_id = str(chat_id)
                if not str_id.startswith("-100") and len(str_id) < 14:
                    # Convert to supergroup format if needed
                    chat_id = int(f"-100{str_id[1:]}")
            
            # Try to get chat info to verify access
            try:
                chat = await client.get_chat(chat_id)
                
                # Set the chat ID in database
                if db.set_chat_id(query.from_user.id, chat_id):
                    chat_type = chat.type
                    chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or "Unknown"
                    
                    await msg.edit(
                        f"✅ Chat ID set successfully!\n\n"
                        f"• Type: {chat_type}\n"
                        f"• Name: {chat_name}\n"
                        f"• ID: `{chat_id}`"
                    )
                else:
                    await msg.edit("❌ Failed to save chat ID to database")
            except Exception as e:
                await msg.edit(f"❌ Error accessing chat: {str(e)}\n\nMake sure the bot is a member of the channel/group.")
        else:
            await msg.edit(error_msg or "❌ Invalid input. Please try again with a valid chat.")
    except TimeoutError:
        await msg.edit("⏰ Response timed out")

@Bot.on_callback_query(filters.regex(r"^remove_chatid$"))
async def remove_chatid(client, query):
    user_id = query.from_user.id
    
    if db.remove_chat_id(user_id):
        await query.answer("✅ Chat ID removed successfully")
    else:
        await query.answer("❌ Failed to remove Chat ID")
    
    # Refresh the settings page
    await chatid_settings(client, query)

@Bot.on_callback_query(filters.regex(r"^chatid_help$"))
async def chatid_help(client, query):
    text = """
**💭 How to set a Chat ID**

You can set a default chat where all files will be sent:

1. **Public Channels/Groups**: Send the username (e.g., @channelname)

2. **Private Chats**: Forward any message from the target chat

3. **Using Chat ID**: If you know the ID:
   • User chats: Just the number (e.g., 123456789)
   • Groups: Starts with - (e.g., -1001234567890) 
   • Channels: Starts with -100 (e.g., -1001234567890)

Note: The bot must be a member of the channel/group
"""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="chatid_settings")]
    ])
    
    try:
        await query.edit_message_text(text, reply_markup=buttons)
    except MessageNotModified:
        pass

@Bot.on_message(filters.command("help"))
async def help_handler(client, message):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Channel", url="https://t.me/officialharsh_g")]
    ])
    await message.reply(HELP_TEXT, reply_markup=buttons)

@Bot.on_message(filters.command(["rules", "terms", "conditions"]))
async def rules_handler(client, message):
    user = message.from_user
    await add_user(user)
    
    if db.is_user_banned(user.id):
        return await message.reply("🚫 You are banned from using this bot.")   
    try:
        await message.reply(
            RULES_TEXT,
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        print(f"Rules Error: {e}")
        await message.reply("🔄 Please try again later.")
