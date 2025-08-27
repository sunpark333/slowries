import time
import os
import logging
import re
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant, ChatAdminRequired, ChannelPrivate
from pyrogram.types import Message
from .. import userbot, Bot
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import FORCESUB ,AUTH, LOG_GROUP, ADMIN_ONLY
from main.plugins.pyroplug import get_msg, is_bot_url
from main.plugins.helpers import get_link, join, screenshot
from main.plugins.db import db

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

COOLDOWN_TIMES = {
    None: 300,
    0: 300,
    1: 40,
    2: 30,
    3: 20,
    4: 10
}

message = "Send me the message link you want to start saving from, as a reply to this message."

process = []
timer = {}
user = []

def is_admin(user_id: int) -> bool:
    return user_id in AUTH

def is_auth(user_id: int) -> bool:
    return user_id in AUTH or db.is_user_authorized(user_id)

async def force_sub(client: Client, channel_list, user_id: int) -> tuple:
    # Convert single string to list for consistent handling
    if isinstance(channel_list, str):
        channel_list = [channel_list]
    
    if not channel_list:
        return False, None, None
    
    buttons = []
    unjoined_channels = []
    
    for channel in channel_list:
        # Remove @ if present
        clean_channel = channel.lstrip('@')
        chat_id = f"@{clean_channel}"
        
        try:
            user = await client.get_chat_member(chat_id, user_id)
            if user.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
                unjoined_channels.append(clean_channel)
                # Get chat info to show proper channel name in button
                chat_info = await client.get_chat(chat_id)
                channel_name = chat_info.title if chat_info.title else clean_channel
                buttons.append([InlineKeyboardButton(f"Join {channel_name}", url=f"https://t.me/{clean_channel}")])
        except UserNotParticipant:
            unjoined_channels.append(clean_channel)
            # Get chat info to show proper channel name in button
            chat_info = await client.get_chat(chat_id)
            channel_name = chat_info.title if chat_info.title else clean_channel
            buttons.append([InlineKeyboardButton(f"Join {channel_name}", url=f"https://t.me/{clean_channel}")])
        except (ChatAdminRequired, ChannelPrivate):
            return True, f"ERROR: Add me as admin in channel @{clean_channel}, or check your channel id.", None
        except ValueError:
            return True, f"ERROR: Invalid channel username @{clean_channel}", None
        except Exception as e:
            logger.error(f"Force sub error for channel @{clean_channel}: {e}")
            return True, "An error occurred while checking subscription.", None
    
    # If there are unjoined channels
    if unjoined_channels:
        if len(unjoined_channels) == 1:
            message = f"Please join the required channel to use this bot"
        else:
            message = f"Please join all the required channels to use this bot"
        
        # Add a "Check Again" button
        buttons.append([InlineKeyboardButton("✅ Check Again", callback_data="checksub")])
        
        return True, message, InlineKeyboardMarkup(buttons)
    
    # If all checks passed
    return False, None, None
  
async def extract_user_id(message: Message) -> int:
    user_id = None
    if message.reply_to_message:
        reply = message.reply_to_message
        if reply.forward_from:
            user_id = reply.forward_from.id
        else:
            user_id = reply.from_user.id
    elif len(message.command) > 1:
        try:
            user_id = int(message.command[1])
        except ValueError:
            pass
    return user_id

async def log_action(action: str, user_id: int = None, admin_id: int = None, 
                    username: str = None, link: str = None, error: str = None, 
                    original_message: Message = None):
    if not LOG_GROUP:
        return
    
    log_text = [f"**{action}**"]
    if user_id:
        log_text.append(f"**User ID:** `{user_id}`")
    if admin_id:
        log_text.append(f"**Admin ID:** `{admin_id}`")
    if username:
        log_text.append(f"**Username:** @{username}")
    if link:
        log_text.append(f"**Link:** {link}")
    if error:
        log_text.append(f"**Error:** `{error}`")
    
    try:
        await Bot.send_message(
            LOG_GROUP,
            "\n".join(log_text),
            disable_web_page_preview=True,
            reply_to_message_id=original_message.id if original_message else None
        )
    except Exception as e:
        logger.error(f"Failed to log action: {e}")

def check_cooldown(user_id: int) -> tuple:
    if user_id in AUTH or db.is_user_authorized(user_id):
        return False, 0
    
    current_time = time.time()
    if user_id in timer:
        premium_level = db.get_user_level(user_id)
        cooldown_time = COOLDOWN_TIMES.get(premium_level, COOLDOWN_TIMES[None])
        time_passed = current_time - timer[user_id]
        if time_passed < cooldown_time:
            return True, cooldown_time - time_passed
    
    timer[user_id] = current_time
    return False, 0

@Bot.on_callback_query(filters.regex("^checksub$"))
async def check_subscription_callback(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    need_sub, msg, markup = await force_sub(client, FORCESUB, user_id)
    
    if need_sub:
        await callback_query.answer("You still haven't joined all channels!")
        await callback_query.message.edit(
            text=msg,
            reply_markup=markup,
            disable_web_page_preview=True
        )
    else:
        await callback_query.answer("Thank you for joining! Now you can use the bot.", show_alert=True)
        # Delete the warning message
        await callback_query.message.delete()
        
@Bot.on_message(
    filters.regex(r'https?://(?:www\.)?t\.me/[^\s]+|tg://openmessage\?user_id=\w+&message_id=\d+')
    & filters.private
)
async def clone(client: Client, message: Message):
    if ADMIN_ONLY:
      if not is_admin(message.from_user.id):
        await message.reply("You are not authorised to use this bot please contact Admin(@B34STXBOT)")
        return
    user_id = message.from_user.id
    user_info = message.from_user
    
    if db.is_user_in_batch(user_id):
        return
    
    try:
        db.add_user(
            user_id=user_info.id,
            username=user_info.username,
            first_name=user_info.first_name,
            last_name=user_info.last_name
        )
    except Exception as e:
        logger.error(f"Error adding user to DB: {e}")

    is_banned, ban_reason = db.is_user_banned(user_id)
    if is_banned:
        await message.reply(f"You are banned. Reason: {ban_reason or 'No reason provided'}")
        return

    is_muted, mute_reason, _ = db.is_user_muted(user_id)
    if is_muted:
        mute_time = db.get_mute_time_formatted(user_id)
        mute_text = f"🔇 Muted for {mute_time} remaining"
        if mute_reason:
            mute_text += f". Reason: {mute_reason}"
        await message.reply(mute_text)
        return

    in_cooldown, remaining = check_cooldown(user_id)
    if in_cooldown:
        level = db.get_user_level(user_id)
        level_info = f"Premium Level {level}" if level else "Free User"
        await message.reply(f"Please try again after {int(remaining)}s. ({level_info})")
        return

    if LOG_GROUP:
        await log_action(
            "New Link Request",
            user_id=user_id,
            username=user_info.username,
            link=message.text,
            original_message=message
        )

    links = message.text.split("\n")
    max_links = 10 if is_auth(user_id) else 1
    
    if len(links) > max_links:
        msg = "Max 10 links" if is_auth(user_id) else "Unauthorized: 1 link max"
        await message.reply(msg)
        return

    for link in links:
        link = link.strip()
        if not link:
            continue
            
        try:
            
            if is_bot_url(link):
                clean_link = link  # Keep the original link for bot URLs
            else:
                clean_link = get_link(link)
                
            if not clean_link:
                await message.reply(f"Invalid link format: {link}")
                continue
        except Exception as e:
            logger.error(f"Link parsing error: {e}")
            await message.reply(f"Could not parse link: {link}")
            continue

        force_sub_result, force_sub_msg, markup = await force_sub(client, FORCESUB, user_id)
        if force_sub_result:
          await message.reply(
             text=force_sub_msg,
             reply_markup=markup,
             disable_web_page_preview=True
             )
          return

        if str(user_id) in user:
            await message.reply("You already have an active process. Please wait and try again after ongoing process is completed.")
            return

        user.append(str(user_id))
        edit = await message.reply("Processing your request...")
        
        try:
            if 't.me/+' in clean_link or 'addlist' in clean_link:
                join_result = await join(userbot, clean_link)
                await edit.edit(join_result)
                user.remove(str(user_id))
                return

            file_name = None
            if '|' in link:
                parts = link.split('|')
                if len(parts) == 2:
                    file_name = parts[1].strip()

            await get_msg(userbot, Bot, user_id, edit.id, clean_link, 0)

        except FloodWait as fw:
            await Bot.send_message(user_id, f'FloodWait: Try after {fw.value}s')
            await log_action("FloodWait Error", user_id=user_id, error=f"{fw.value}s")
        except Exception as e:
            logger.error(f"Clone error: {e}")
            await Bot.send_message(user_id, f"Error cloning {clean_link}\n\n{e}")
            await log_action("Clone Error", user_id=user_id, error=str(e))
        finally:
            if str(user_id) in user:
                user.remove(str(user_id))
            await asyncio.sleep(30)  # Keep message visible for 30 seconds
            try:
                await edit.delete()
            except:
                pass
