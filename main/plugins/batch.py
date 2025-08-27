import logging
import time
import os
import asyncio
import json
import re
from datetime import timedelta

from .. import userbot
from .. import Bot
from main.plugins.pyroplug import check, get_msg
from main.plugins.helpers import get_link, screenshot
from main.plugins.db import db

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, PeerIdInvalid, ChatIdInvalid

from config import AUTH, ADMIN_ONLY

MESSAGE_COOLDOWN = 5
CONVERSATION_TIMEOUT = 120

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

batch = []
ids = []
last_message_time = {}

def is_auth(user_id):
    try:
        return user_id in AUTH or db.is_user_authorized(user_id)
    except Exception as e:
        logger.error(f"Error checking auth status: {e}")
        return False

def is_admin(user_id):
    try:
        return user_id in AUTH
    except Exception as e:
        logger.error(f"Error checking auth status: {e}")
        return False

def extract_msg_id(link):
    patterns = [
        r'https://t\.me/(?:c/\d+|[^/]+)/(\d+)$',  # Standard link
        r'https://t\.me/(?:c/\d+|[^/]+)/\d+/(\d+)'  # Thread link format
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return int(match.group(1))
    return None
    
async def extract_chat_info(userbot, base_link):
    """Extract channel name and chat ID from base_link using userbot."""
    try:
        chat_id = None
        channel_name = "Unknown Channel"
        source_chat_type = "public"
        
        # Handle different link formats
        if 'c/' in base_link:
            # Private channel format: https://t.me/c/1234567890
            source_chat_type = "private"
            chat_part = base_link.split('c/')[1].split('/')[0]
            chat_id = int('-100' + chat_part)  # Convert to proper format for private channels
        else:
            # Public channel format: https://t.me/channelname
            source_chat_type = "public"
            username = base_link.replace('https://t.me/', '')
            # Keep username as is for public channels
            chat_id = username
        
        # Try to get channel name
        try:
            chat = await userbot.get_chat(chat_id)
            if hasattr(chat, 'title'):
                channel_name = chat.title
            elif hasattr(chat, 'first_name'):
                channel_name = chat.first_name
        except Exception as e:
            logger.warning(f"Could not get channel name: {e}")
            # For private channels, try to get at least one message to extract the chat info
            if source_chat_type == "private":
                try:
                    # Try to get chat info from message history
                    async for msg in userbot.get_chat_history(chat_id, limit=1):
                        chat = await userbot.get_chat(chat_id)
                        if hasattr(chat, 'title'):
                            channel_name = chat.title
                        break
                except Exception as e2:
                    logger.warning(f"Could not get channel name from history: {e2}")
        
        return {
            "chat_id": chat_id,
            "channel_name": channel_name,
            "chat_type": source_chat_type
        }
    except Exception as e:
        logger.error(f"Error extracting chat info: {e}")
        return {
            "chat_id": None,
            "channel_name": "Unknown Channel",
            "chat_type": "unknown"
        }

def extract_base_link(link):
    patterns = [
        r'(https://t\.me/(?:c/\d+|[^/]+))(?:/\d+)',
        r'(https://t\.me/(?:c/\d+|[^/]+))(?:/\d+/\d+)'  # Added pattern for thread links
    ]
    for pattern in patterns:
        match = re.search(pattern, link)
        if match:
            return match.group(1)
    return None

def parse_set_theory_notation(text, start_msg_id=None):
    message_ids = set()
    
    if text.lower() == "all":
        return "all"
    
    number_match = re.search(r'^\s*(\d+)\s*$', text)
    if number_match and start_msg_id is not None:
        count = int(number_match.group(1))
        message_ids.update(range(start_msg_id, start_msg_id + count))
        return sorted(list(message_ids))
    
    simple_range = re.search(r'^\s*(\d+)\s*-\s*(\d+)\s*$', text)
    if simple_range:
        start, end = map(int, simple_range.groups())
        message_ids.update(range(start, end + 1))
        return sorted(list(message_ids))
    
    end_msg_id = extract_msg_id(text)
    if end_msg_id and start_msg_id:
        if end_msg_id > start_msg_id:
            message_ids.update(range(start_msg_id, end_msg_id + 1))
        else:
            message_ids.update(range(end_msg_id, start_msg_id + 1))
        return sorted(list(message_ids))
    
    parts = re.split(r'\s*U\s*', text)
    
    for part in parts:
        part_ids = set()
        
        range_matches = re.findall(r'\[(\d+),(\d+)\]', part)
        for start, end in range_matches:
            part_ids.update(range(int(start), int(end) + 1))
        
        exclusion_match = re.search(r'-\{([\d,\s]+)\}', part)
        if exclusion_match:
            exclusions = [int(x.strip()) for x in exclusion_match.group(1).split(',')]
            part_ids.difference_update(exclusions)
        
        message_ids.update(part_ids)
    
    return sorted(list(message_ids))

def check_user_limits(user_id):
    try:
        if user_id in AUTH or db.is_user_authorized(user_id):
            return True, None
        
        remaining_msgs = db.get_remaining_messages(user_id)
        if remaining_msgs is not None and remaining_msgs <= 0:
            return False, "You've reached your message limit."
        
        expiration_time = db.get_expiration_time_remaining(user_id)
        if expiration_time is not None and expiration_time.total_seconds() <= 0:
            return False, "Your subscription has expired."
        
        return True, None
    except Exception as e:
        logger.error(f"Error checking user limits: {e}")
        return True, None

def calculate_timer(index):
    if index < 250: return 2
    if index < 1000: return 3
    if index < 10000: return 4
    if index < 50000: return 5
    return 6

def create_progress_bar(current, total, length=20):
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    percent = current / total * 100
    return f"{bar} {percent:.1f}%"

async def update_countdown(client, chat_id, message_id, current, total, stats=None, channel_name=None, total_size=None):
    progress_bar = create_progress_bar(current, total)
    progress = (
        f"**📊 Batch Progress**\n\n"
        f"**⏳ Progress:** {progress_bar}\n"
        f"**✅ Completed:** `{current}/{total}` files\n"
        f"**📈 Completion:** `{(current/total*100):.1f}%`"
    )
    
    if channel_name:
        progress += f"\n**📢 Channel:** `{channel_name}`"
        
    if total_size is not None:
        size_str = format_size(total_size)
        progress += f"\n**💾 Total Size:** `{size_str}`"
    
    if stats:
        progress += "\n\n**📁 File Statistics:**\n"
        for file_type, count in stats.items():
            if count > 0:  # Only show file types that have counts > 0
                progress += f"• **{file_type}:** `{count}`\n"
    
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=progress,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Batch", callback_data="cancel")],
                [InlineKeyboardButton("📢 Join Channel", url="https://t.me/officialharsh_g")]
            ])
        )
    except Exception as e:
        logger.error(f"Error updating countdown: {e}")

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.2f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.2f} GB"

def format_stats_summary(stats, total_size=None):
    summary = "**📁 File Statistics Summary:**\n"
    total_files = sum(stats.values())
    
    for file_type, count in stats.items():
        if count > 0:  # Only show file types that have counts > 0
            percentage = (count / total_files * 100) if total_files > 0 else 0
            summary += f"• **{file_type}:** `{count}` ({percentage:.1f}%)\n"
    
    if total_size is not None:
        summary += f"\n**💾 Total Size:** `{format_size(total_size)}`"
        
    return summary

async def handle_floodwait(client, sender, wait_time):
    wait_msg = await client.send_message(
        sender, 
        f"⏱️ **FloodWait Detected**\n\nWaiting `{wait_time}s` due to Telegram's rate limit..."
    )
    # Add extra time to the wait to be safer
    await asyncio.sleep(wait_time + 5)
    try:
        await client.delete_messages(sender, wait_msg.id)
    except Exception as e:
        logger.error(f"Error deleting wait message: {e}")
      
async def run_batch(userbot, client, sender, countdown_msg, base_link, message_ids=None, fetch_all=False):
    file_stats = {
        "Videos": 0,
        "Photos": 0,
        "Documents": 0,
        "PDFs": 0,
        "Audio": 0,
        "Stickers": 0,
        "Links": 0,
        "Text": 0,
        "Service": 0,
        "Other": 0
    }
    
    total_size = 0
    processed_count = 0
    
    is_authorized = sender in AUTH or db.is_user_authorized(sender)
    
    # Get chat_id where to send messages
    dest_chat_id = None
    try:
        dest_chat_id = db.get_chat_id(sender)
        # Validate chat_id
        if dest_chat_id:
            try:
                await client.get_chat(dest_chat_id)
            except (PeerIdInvalid, ChatIdInvalid):
                logger.warning(f"Invalid destination chat_id {dest_chat_id} for user {sender}. Using sender's ID instead.")
                dest_chat_id = sender
            except Exception as e:
                logger.error(f"Error validating destination chat_id: {e}")
                dest_chat_id = sender
    except Exception as e:
        logger.error(f"Error getting destination chat_id: {e}")
        dest_chat_id = sender
    
    # If chat_id is None or invalid, use sender's ID
    if not dest_chat_id:
        dest_chat_id = sender
    
    # Extract chat info
    chat_info = await extract_chat_info(userbot, base_link)
    channel_name = chat_info["channel_name"]
    source_chat_id = chat_info["chat_id"]
    source_chat_type = chat_info["chat_type"]
    
    if fetch_all:
        try:
            history = []
            history_msg = None
            
            # Get history with pagination to handle large channels
            async for message in userbot.get_chat_history(source_chat_id):
                if f'{sender}' not in batch:
                    logger.info(f"Batch cancelled by user {sender} during history fetch")
                    break
                    
                history.append(message.id)
                if len(history) >= 100000:
                    await client.send_message(sender, 
                                             "⚠️ **Reached maximum limit of 100,000 messages.**\n"
                                             "Will process these first.")
                    break
                
                # Update progress periodically (edit the message instead of sending new ones)
                if len(history) % 1000 == 0:
                    fetch_text = f"🔍 **Fetched {len(history)} messages so far...**\n\nStill scanning the channel."
                    if history_msg:
                        try:
                            await client.edit_message_text(
                                chat_id=sender,
                                message_id=history_msg.id,
                                text=fetch_text
                            )
                        except Exception as edit_err:
                            logger.error(f"Error editing history message: {edit_err}")
                            history_msg = await client.send_message(sender, fetch_text)
                    else:
                        history_msg = await client.send_message(sender, fetch_text)
                    
                    # Add a sleep after each 1000 messages to avoid FloodWait
                    await asyncio.sleep(2)
                
            if len(history) == 0:
                await client.send_message(sender, "⚠️ **No messages found in the channel.**")
                return
                
            message_ids = sorted(history)
            
            if history_msg:
                await client.edit_message_text(
                    chat_id=sender,
                    message_id=history_msg.id,
                    text=f"✅ **Successfully fetched {len(message_ids)} messages.**\n"
                         f"Starting batch download now."
                )
            else:
                await client.send_message(
                    sender,
                    f"✅ **Successfully fetched {len(message_ids)} messages.**\n"
                    f"Starting batch download now."
                )
                
        except FloodWait as fw:
            logger.warning(f"FloodWait during history fetch: {fw.value}s")
            await handle_floodwait(client, sender, fw.value)
            await client.send_message(
                sender, 
                "⚠️ **Rate limit hit while fetching messages.**\n"
                "Please try again with a smaller batch or specific message IDs."
            )
            return
        except Exception as e:
            logger.error(f"Error fetching all messages: {e}")
            await client.send_message(sender, f"⚠️ **Error fetching all messages:** `{str(e)}`")
            return
    
    if not message_ids or len(message_ids) == 0:
        await client.send_message(sender, "⚠️ **No messages to process.**")
        return
        
    total = len(message_ids)
    
    # Pin a message in the destination chat with channel name and message count
    pin_text = (
        f"📥 **Batch Download Started**\n\n"
        f"📢 **Channel:** `{channel_name}`\n"
        f"📊 **Total Messages:** `{total}`\n\n"
        f"ℹ️ This batch process was initiated at `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
    )
    
    try:
        # Send the initial pin message to the destination chat
        pin_msg = await client.send_message(dest_chat_id, pin_text)
        
        # Pin the message and delete the service message
        xy = await client.pin_chat_message(
            dest_chat_id, 
            pin_msg.id, 
            disable_notification=True, 
            both_sides=True
        )
        
        # Delete the service message for pinning
        if xy:
            await client.delete_messages(dest_chat_id, xy.id)
        
        # Inform the user that we've pinned a message
        await client.send_message(
            sender,
            f"📌 **Pinned information message in destination chat**\n\n"
            f"🔄 Starting batch processing now..."
        )
    except Exception as e:
        logger.error(f"Error pinning message: {e}")
        await client.send_message(
            sender,
            "⚠️ **Could not pin message in destination chat**\n\n"
            "Continuing with batch processing..."
        )
    
    for i, msg_id in enumerate(message_ids):
        if f'{sender}' not in batch:
            logger.info(f"Batch cancelled by user {sender}")
            break
        
        can_continue, limit_msg = check_user_limits(sender)
        if not can_continue:
            await client.send_message(sender, f"⚠️ **Batch cancelled:** {limit_msg}")
            if f'{sender}' in batch:
                batch.remove(f'{sender}')
            db.set_user_in_batch(sender, False)
            await client.edit_message_text(
                chat_id=sender,
                message_id=countdown_msg.id,
                text=f"**❌ Batch process stopped.**\n\n**Reason:** {limit_msg}",
                reply_markup=None
            )
            break
            
        try:
            current_link = f"{base_link}/{msg_id}"
            # Calculate timer based on index and add more time
            timer = calculate_timer(i)
            
            status_msg = await client.send_message(
                dest_chat_id, 
                f"🔄 **Processing** `{i+1}/{total}` (ID: `{msg_id}`)..."
            )
            
            # Try to get the original message to determine type before processing
            try:
                # First try using check(userbot, client, current_link) to verify if accessible
                is_accessible, _ = await check(userbot, client, current_link)
                
                if is_accessible:
                    # Use proper error handling for getting the message
                    try:
                        # For private channels, we need to use the proper format
                        if source_chat_type == "private":
                            original_message = await userbot.get_messages(source_chat_id, msg_id)
                        else:
                            original_message = await userbot.get_messages(source_chat_id, msg_id)
                        
                        # Process message and update statistics
                        if original_message:
                            # Detect message type and update stats before sending
                            if original_message.video:
                                file_stats["Videos"] += 1
                                if hasattr(original_message.video, 'file_size'):
                                    total_size += original_message.video.file_size
                                    
                            elif original_message.photo:
                                file_stats["Photos"] += 1
                                
                            elif original_message.document:
                                file_stats["Documents"] += 1
                                if hasattr(original_message.document, 'file_size'):
                                    total_size += original_message.document.file_size
                                
                                # Check if it's a PDF
                                if hasattr(original_message.document, 'mime_type') and original_message.document.mime_type == 'application/pdf':
                                    file_stats["PDFs"] += 1
                                    
                            elif original_message.audio:
                                file_stats["Audio"] += 1
                                if hasattr(original_message.audio, 'file_size'):
                                    total_size += original_message.audio.file_size
                                    
                            elif original_message.sticker:
                                file_stats["Stickers"] += 1
                                
                            elif original_message.text:
                                if re.search(r'https?://\S+', original_message.text):
                                    file_stats["Links"] += 1
                                else:
                                    file_stats["Text"] += 1
                                    
                            elif original_message.service:
                                file_stats["Service"] += 1
                                
                            else:
                                file_stats["Other"] += 1
                    except Exception as get_msg_err:
                        logger.error(f"Error getting original message: {get_msg_err}")
                
                # Now call get_msg to process and forward the message
                await get_msg(userbot, client, dest_chat_id, status_msg.id, current_link, 0)
                processed_count += 1
                try:
                    await status_msg.delete()
                except:
                    pass
                
                # Update the countdown with the latest stats after each successful processing
                await update_countdown(client, sender, countdown_msg.id, i+1, total, file_stats, channel_name, total_size)
                
            except Exception as msg_error:
                logger.error(f"Error in get_msg for ID {msg_id}: {msg_error}")
                await client.send_message(
                    sender,
                    f"⚠️ **Error processing message** `{msg_id}`: `{str(msg_error)}`\n"
                    f"Continuing with next message..."
                )
            
            if not is_authorized:
                remaining_msgs = db.get_remaining_messages(sender)
                if remaining_msgs is not None:
                    db.decrement_message_limit(sender)
                    
                    if i % 10 == 0 or remaining_msgs <= 5:
                        remaining = max(0, remaining_msgs - 1)
                        expiry_str = db.get_expiration_time_formatted(sender)
                        if remaining > 0:
                            await client.send_message(
                                sender, 
                                f"📊 **Status Update**\n\n"
                                f"• **Remaining messages:** `{remaining}`\n"
                                f"• **Subscription expires in:** `{expiry_str}`"
                            )
                        elif remaining == 0:
                            await client.send_message(
                                sender,
                                "⚠️ **Warning: This is your last message!**\n\n"
                                f"• **Subscription expires in:** `{expiry_str}`"
                            )
            
            # Add more sleep time to avoid FloodWait
            await asyncio.sleep(MESSAGE_COOLDOWN + 1)
            try:
                await client.delete_messages(sender, status_msg.id)
            except Exception as e:
                pass
            
            # Add dynamic sleep time based on index + extra time to avoid floodwait
            await asyncio.sleep(timer + 1)
            
        except FloodWait as fw:
            if fw.value > 300:
                await client.send_message(
                    sender, 
                    f'⚠️ **FloodWait too long** (`{fw.value}s`), cancelling batch'
                )
                break
            await handle_floodwait(client, sender, fw.value)
        except Exception as e:
            logger.error(f"Error processing {msg_id}: {e}")
            await client.send_message(
                sender, 
                f"⚠️ **Skipped** `{msg_id}` due to error: `{str(e)}`"
            )
        
        if f'{sender}' not in batch:
            break
    
    # Update the pinned message with completion info
    try:
        # Prepare completion stats
        stats_summary = "\n".join([f"• **{ftype}:** `{count}`" for ftype, count in file_stats.items() if count > 0])
        
        complete_pin_text = (
            f"✅ **Batch Download Completed**\n\n"
            f"📢 **Channel:** `{channel_name}`\n"
            f"📊 **Total Messages:** `{total}`\n"
            f"✅ **Successfully Processed:** `{processed_count}`\n"
            f"💾 **Total Size:** `{format_size(total_size)}`\n\n"
            f"**📁 File Statistics:**\n{stats_summary}\n\n"
            f"ℹ️ This batch process was completed at `{time.strftime('%Y-%m-%d %H:%M:%S')}`"
        )
        
        # Update the pinned message
        await client.edit_message_text(
            chat_id=dest_chat_id,
            message_id=pin_msg.id,
            text=complete_pin_text
        )
    except Exception as e:
        logger.error(f"Error updating pinned message: {e}")
    try:
        await client.send_message(
            dest_chat_id,
            "✅ **Done**"
        )
        logger.info(f"Sent completion message to destination chat {dest_chat_id}")
    except Exception as e:
        logger.error(f"Error sending completion message to destination chat: {e}")
    
    # Return the stats for use in completion message
    return {
        "file_stats": file_stats,
        "total_size": total_size,
        "processed_count": processed_count,
        "dest_chat_id": dest_chat_id
    }

@Bot.on_message(filters.command("batch") & filters.private)
async def batch_handler(client, message):
        
    if ADMIN_ONLY:
      if not is_admin(message.from_user.id):
        await message.reply("You are not authorised to use this bot please contact Admin(@B34STXBOT)")
        return
    user_id = message.from_user.id
    is_authorized = user_id in AUTH or db.is_user_authorized(user_id)
    if not is_authorized:
        time_remaining = db.get_expiration_time_remaining(user_id)
        if time_remaining is None or time_remaining == timedelta(0):
            return await message.reply("🚫 **You are not authorized to use batch!**")
    
    if f'{user_id}' in batch:
        return await message.reply("⚠️ You've already started one batch, wait for it to complete or use `/cancel` to cancel it!")
    
    current_time = time.time()
    if user_id in last_message_time and (current_time - last_message_time[user_id]) < MESSAGE_COOLDOWN:
        wait_time = round(MESSAGE_COOLDOWN - (current_time - last_message_time[user_id]))
        return await message.reply(f"⏱️ Please wait `{wait_time}` seconds before sending another message.")
    
    last_message_time[user_id] = current_time
    db.set_user_in_batch(user_id, True)
    batch.append(f'{user_id}')
    
    try:
        # Check for destination chat ID
        dest_chat_id = None
        try:
            dest_chat_id = db.get_chat_id(user_id)
            if dest_chat_id:
                try:
                    chat = await client.get_chat(dest_chat_id)
                    await message.reply(f"🔄 **Batch messages will be sent to:** `{chat.title if hasattr(chat, 'title') else 'your selected chat'}`")
                except (PeerIdInvalid, ChatIdInvalid):
                    await message.reply("⚠️ **Your selected destination chat is invalid. Messages will be sent to your DM instead.**")
                    dest_chat_id = user_id
                except Exception as e:
                    logger.error(f"Error checking destination chat: {e}")
                    await message.reply("⚠️ **Error checking destination chat. Messages will be sent to your DM instead.**")
                    dest_chat_id = user_id
        except Exception as e:
            logger.error(f"Error getting destination chat_id: {e}")
            dest_chat_id = user_id
        
        link_msg = await message.reply(
            "🔗 **Send me the starting message link** in one of these formats:\n\n"
            "• `https://t.me/c/channel_id/message_id`\n"
            "• `https://t.me/channel_username/message_id`\n\n"
            "📝 **Reply to this message with your link.**"
        )
        
        input_message = await client.listen(link_msg.chat.id, timeout=CONVERSATION_TIMEOUT)
        
        start_link = input_message.text.strip()
        
        start_msg_id = extract_msg_id(start_link)
        base_link = extract_base_link(start_link)
        
        if not base_link or not start_msg_id:
            await client.send_message(user_id, "❌ **Invalid link format.** Please provide a valid Telegram message link.")
            batch.remove(f'{user_id}')
            db.set_user_in_batch(user_id, False)
            return
        
        s, r = await check(userbot, client, start_link)
        if not s:
            await client.send_message(user_id, f"❌ **Link verification failed:** {r}")
            batch.remove(f'{user_id}')
            db.set_user_in_batch(user_id, False)
            return
        
        range_msg = await client.send_message(
            user_id,
            "🔢 **Now specify which messages you want to download.** You can:\n\n"
            "1️⃣ Enter `all` to download all messages in the chat *(BETA - may be unstable)*\n"
            "2️⃣ Enter a **single number** to download that many messages from the starting link\n"
            "3️⃣ Send another link to download all messages between start and end\n"
            "4️⃣ Enter a simple range like `100-200`\n"
            "5️⃣ Use set notation like:\n"
            "   • `[100,200]` - Range from 100 to 200\n"
            "   • `[100,200]U[300,400]` - Union of two ranges\n" 
            "   • `[100,200]U[300,400]-{150,160,350}` - Union with exclusions\n\n"
            "📝 **Reply to this message with your choice:**"
        )
        
        input_message = await client.listen(range_msg.chat.id, timeout=CONVERSATION_TIMEOUT)
        
        range_text = input_message.text.strip()
        
        fetch_all = False
        if range_text.lower() == "all":
            fetch_all = True
            message_ids = []
            await client.send_message(
                user_id,
                "🔍 **Fetching all messages in the chat... (BETA feature)**\n\n"
                "⚠️ **Note:** This feature is experimental and may not work for all channels.\n"
                "This may take some time depending on the chat size.\n\n"
                "If you experience issues, consider using a specific range instead."
            )
        else:
            message_ids = parse_set_theory_notation(range_text, start_msg_id)
        
        if not fetch_all and not message_ids:
            await client.send_message(
                user_id, 
                "❌ **Could not understand the range specification.** Please try again with a valid format."
            )
            batch.remove(f'{user_id}')
            db.set_user_in_batch(user_id, False)
            return
        
        if not fetch_all and len(message_ids) > 100000:
            await client.send_message(user_id, "⚠️ **Maximum 100,000 files per batch.**")
            batch.remove(f'{user_id}')
            db.set_user_in_batch(user_id, False)
            return
        
        if not fetch_all:
            ids.extend(message_ids)
        
        cd_text = "🚀 **Batch process started**\n\n"
        if fetch_all:
            cd_text += "📑 **Downloading all messages in the chat (BETA feature)**"
        else:
            cd_text += (
                f"📑 **Total files:** `{len(message_ids)}`\n"
                f"📌 **Starting with:** `{message_ids[0]}`\n"
                f"📍 **Ending with:** `{message_ids[-1]}`"
            )
        
        cd = await client.send_message(
            user_id,
            cd_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Batch", callback_data="cancel")],
                [InlineKeyboardButton("📢 Join Channel", url="https://t.me/officialharsh_g")]
            ])
        )
        
        batch_results = None
        
        if fetch_all:
            # Get chat info to prepare for batch processing
            chat_info = await extract_chat_info(userbot, base_link)
                
            batch_results = await run_batch(userbot, client, user_id, cd, base_link, fetch_all=True)
        else:
            batch_results = await run_batch(userbot, client, user_id, cd, base_link, message_ids=message_ids)
        
        # Generate completion message with statistics if batch_results exists
        completion_message = "✅ **Batch completed successfully!**"
        
        if batch_results:
            stats_summary = format_stats_summary(batch_results["file_stats"], batch_results["total_size"])
            completion_message += f"\n\n{stats_summary}\n\n**✅ Total Processed:** `{batch_results['processed_count']}` files"
            
            # Add destination information
            if batch_results.get("dest_chat_id") and batch_results["dest_chat_id"] != user_id:
                try:
                    dest_chat = await client.get_chat(batch_results["dest_chat_id"])
                    completion_message += f"\n\n**📤 Files sent to:** `{dest_chat.title if hasattr(dest_chat, 'title') else 'your selected chat'}`"
                except Exception as e:
                    logger.error(f"Error getting destination chat info: {e}")
        
        await client.send_message(user_id, completion_message)
        
        if fetch_all:
            final_text = f"✅ **Batch process completed.**\n\n"
            if batch_results:
                final_text += f"📊 Downloaded `{batch_results['processed_count']}` messages successfully."
            else:
                final_text += "📊 Downloaded messages successfully."
                
            await client.edit_message_text(
                chat_id=user_id,
                message_id=cd.id,
                text=final_text,
                reply_markup=None
            )
        else:
            final_text = f"✅ **Batch process completed.**\n\n"
            if batch_results:
                final_text += f"📊 Processed `{batch_results['processed_count']}` out of `{len(message_ids)}` files."
            else:
                final_text += f"📊 Processed `{len(message_ids)}` files."
                
            await client.edit_message_text(
                chat_id=user_id,
                message_id=cd.id,
                text=final_text,
                reply_markup=None
            )
        
    except asyncio.TimeoutError:
        await client.send_message(user_id, "⏱️ **Response timed out after 2 minutes!**")
    except Exception as e:
        logger.error(f"Batch error: {e}")
        await client.send_message(user_id, f"⚠️ **Error processing batch:** `{str(e)}`")
    finally:
        if ids:
            ids.clear()
        if f'{user_id}' in batch:
            batch.remove(f'{user_id}')
        db.set_user_in_batch(user_id, False)
        
@Bot.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client, message):
    user_id = message.from_user.id
    user_id_str = f'{user_id}'
    
    if user_id_str not in batch:
        await message.reply("❌ **No active batch to cancel!**")
        return
    
    if ids:
        ids.clear()
    if user_id_str in batch:
        batch.remove(user_id_str)
    db.set_user_in_batch(user_id, False)
    
    await message.reply("✅ **Batch cancelled successfully!**")

@Bot.on_callback_query(filters.regex("^cancel$"))
async def cancel_callback(client, callback_query):
    user_id = callback_query.from_user.id
    user_id_str = f'{user_id}'
    
    if user_id_str not in batch:
        await callback_query.answer("❌ No active batch to cancel!", show_alert=True)
        return
    
    if ids:
        ids.clear()
    if user_id_str in batch:
        batch.remove(user_id_str)
    db.set_user_in_batch(user_id, False)
    
    await callback_query.answer("✅ Batch cancelled successfully!", show_alert=True)
    await callback_query.edit_message_text("❌ **Batch process cancelled!**")

C = "/cancel"
START_PIC = "https://res.cloudinary.com/drlkucdog/image/upload/v1739358426/k0q70eqfnfkwlj54ydad.jpg"
TEXT = "👋 Hi, This is **'Paid Restricted Content Saver'** bot Made with ❤️ by __**Team Voice**__."
