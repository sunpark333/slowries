import asyncio, time, os
import aiofiles
import requests
import gc
from pyrogram.enums import ParseMode, MessageMediaType
from .. import Bot
from main.plugins.progress import progress_for_pyrogram
from main.plugins.helpers import screenshot, video_metadata
from main.plugins.db import db
from config import AUTH
from pyrogram import Client, filters
from pyrogram.errors import ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid, FloodWait, PeerIdInvalid
from urllib.parse import urlparse, parse_qs
from pyrogram.raw import functions
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
import glob
import shutil
import traceback
import logging

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

def is_auth(user_id):
    try:
        if user_id in AUTH:
            return True
        
        if db.is_user_authorized(user_id):
            return True
            
        return False
    except Exception as e:
        logger.error(f"Error checking auth status: {e}")
        return False
        
async def check_channel_content_protection(userbot, chat_id):
    """Check if channel has content protection (forwarding restrictions)"""
    try:
        chat = await userbot.get_chat(chat_id)
        
        # Check if the chat has content protection enabled (case-insensitive)
        if hasattr(chat, 'has_protected_content'):
            protected_value = getattr(chat, 'has_protected_content', False)
            if isinstance(protected_value, str):
                protected_value = protected_value.lower()
                if protected_value in ['true', '1', 'yes']:
                    return True
            elif protected_value:
                return True
            
        # Check for noforwards attribute (case-insensitive)
        if hasattr(chat, 'noforwards'):
            noforwards_value = getattr(chat, 'noforwards', False)
            if isinstance(noforwards_value, str):
                noforwards_value = noforwards_value.lower()
                if noforwards_value in ['true', '1', 'yes']:
                    return True
            elif noforwards_value:
                return True
                
        # Check for restricted attribute (case-insensitive)  
        if hasattr(chat, 'restricted'):
            restricted_value = getattr(chat, 'restricted', False)
            if isinstance(restricted_value, str):
                restricted_value = restricted_value.lower()
                if restricted_value in ['true', '1', 'yes']:
                    return True
            elif restricted_value:
                return True
                
        return False
    except Exception as e:
        logger.error(f"Error checking channel protection: {e}")
        # If we can't determine, assume it's protected to be safe
        return True

async def try_forward_message(userbot, client, sender, chat_id, msg_id, target_chat_id, topic_id):
    """Try to forward/copy message without downloading using dump channel as bridge"""
    
    # Define your dump channel ID here (replace with your actual dump channel ID)
    DUMP_CHANNEL_ID = -1002580594967  # Replace with your dump channel ID
    
    # Create unique identifier for this request
    import time
    import random
    unique_id = f"{sender}_{chat_id}_{msg_id}_{int(time.time())}_{random.randint(1000, 9999)}"
    
    try:
        # First verify userbot can access the source chat
        try:
            await userbot.get_chat(chat_id)
        except (ChannelInvalid, PeerIdInvalid, ChatIdInvalid, Exception) as e:
            logger.error(f"Userbot cannot access source chat {chat_id}: {e}")
            return None
            
        # Step 1: Userbot forwards message to dump channel with unique caption
        dump_message = None
        try:
            # Add unique identifier as caption to ensure we get the right message back
            original_msg = await userbot.get_messages(chat_id, msg_id)
            if not original_msg:
                logger.error("Could not get original message")
                return None
                
            # Forward with unique identifier
            dump_message = await userbot.forward_messages(DUMP_CHANNEL_ID, chat_id, msg_id)
            if not dump_message:
                logger.error("Failed to forward message to dump channel")
                return None
                
            # Get the message ID in dump channel
            dump_msg_id = dump_message.id if hasattr(dump_message, 'id') else dump_message[0].id
            
            # Add a text message with unique identifier right after the forwarded message
            identifier_msg = await userbot.send_message(DUMP_CHANNEL_ID, f"BRIDGE_ID:{unique_id}")
            identifier_msg_id = identifier_msg.id
            
        except FloodWait as e:
            logger.warning(f"FloodWait during dump forward: {e.value} seconds")
            if e.value < 300:  # Wait if less than 5 minutes
                await asyncio.sleep(e.value)
                # Retry the forward operation
                try:
                    original_msg = await userbot.get_messages(chat_id, msg_id)
                    if not original_msg:
                        return None
                    dump_message = await userbot.forward_messages(DUMP_CHANNEL_ID, chat_id, msg_id)
                    if not dump_message:
                        return None
                    dump_msg_id = dump_message.id if hasattr(dump_message, 'id') else dump_message[0].id
                    identifier_msg = await userbot.send_message(DUMP_CHANNEL_ID, f"BRIDGE_ID:{unique_id}")
                    identifier_msg_id = identifier_msg.id
                except Exception as retry_e:
                    logger.error(f"Retry after FloodWait failed: {retry_e}")
                    return None
            else:
                logger.error(f"FloodWait too long ({e.value}s), skipping bridge method")
                return None
        except Exception as e:
            logger.error(f"Failed to forward to dump channel: {e}")
            return None
            
        # Small delay to ensure message is properly saved
        await asyncio.sleep(0.5)
        
        # Step 2: Main bot copies from dump channel to target chat
        try:
            result = await client.copy_message(target_chat_id, DUMP_CHANNEL_ID, dump_msg_id, reply_to_message_id=topic_id)
            if result:
                db.increment_cloned_count(sender)
                
                # Step 3: Clean up - delete both messages from dump channel using main bot
                try:
                    await client.delete_messages(DUMP_CHANNEL_ID, [dump_msg_id, identifier_msg_id])
                except FloodWait as cleanup_flood:
                    logger.warning(f"FloodWait during cleanup: {cleanup_flood.value} seconds")
                    if cleanup_flood.value < 60:  # Wait for cleanup if reasonable
                        await asyncio.sleep(cleanup_flood.value)
                        try:
                            await client.delete_messages(DUMP_CHANNEL_ID, [dump_msg_id, identifier_msg_id])
                        except Exception as cleanup_retry_error:
                            logger.warning(f"Cleanup retry failed: {cleanup_retry_error}")
                    else:
                        logger.warning(f"Cleanup FloodWait too long, messages may remain in dump channel")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup dump messages: {cleanup_error}")
                    
                return result
                
        except FloodWait as e:
            logger.warning(f"FloodWait during copy: {e.value} seconds")
            if e.value < 300:  # Wait if less than 5 minutes
                await asyncio.sleep(e.value)
                # Retry the copy operation
                try:
                    result = await client.copy_message(target_chat_id, DUMP_CHANNEL_ID, dump_msg_id, reply_to_message_id=topic_id)
                    if result:
                        db.increment_cloned_count(sender)
                        # Clean up after successful retry using main bot
                        try:
                            await client.delete_messages(DUMP_CHANNEL_ID, [dump_msg_id, identifier_msg_id])
                        except Exception as cleanup_error:
                            logger.warning(f"Failed to cleanup after retry: {cleanup_error}")
                        return result
                except Exception as retry_e:
                    logger.error(f"Copy retry after FloodWait failed: {retry_e}")
            else:
                logger.error(f"Copy FloodWait too long ({e.value}s), falling back to download method")
            
            # Clean up even if copy failed due to FloodWait using main bot
            try:
                await client.delete_messages(DUMP_CHANNEL_ID, [dump_msg_id, identifier_msg_id])
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup after copy FloodWait: {cleanup_error}")
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to copy from dump channel: {e}")
            
            # Clean up even if copy failed using main bot
            try:
                await client.delete_messages(DUMP_CHANNEL_ID, [dump_msg_id, identifier_msg_id])
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup dump message after copy failure: {cleanup_error}")
            
            return None
            
    except Exception as e:
        logger.error(f"Bridge forward failed: {e}")
        return None
        
    return None
        
async def is_message_pinned(client, chat_id, message_id):
    """Check if a message is pinned in a chat"""
    try:
        chat = await client.get_chat(chat_id)
        if chat.pinned_message and chat.pinned_message.id == message_id:
            return True
            
        # For some chats with multiple pinned messages
        try:
            message = await client.get_messages(chat_id, message_id)
            if message and hasattr(message, 'pinned') and message.pinned:
                return True
        except Exception as e:
            logger.error(f"Error checking multiple pinned messages: {e}")
            
        return False
    except Exception as e:
        logger.error(f"Error checking pinned status: {e}")
        return False

async def safe_pin_message(client, chat_id, message_id):
    """Safely pin a message with error handling"""
    try:
        # Pin message without notification
        await client.pin_chat_message(chat_id, message_id, disable_notification=True, both_sides=True)
        
        # Delete service message that appears after pinning
        await asyncio.sleep(1)  # Small delay to ensure service message is created
        
        # Get recent messages to find and delete the service message
        recent_messages = await client.get_history(chat_id, limit=5)
        for msg in recent_messages:
            if msg.service and "pinned a message" in str(msg.text):
                try:
                    await client.delete_messages(chat_id, msg.id)
                except Exception as e:
                    logger.error(f"Failed to delete service message: {e}")
                break
                
        return True
    except FloodWait as e:
        if e.value < 30:
            logger.warning(f"FloodWait detected while pinning. Waiting {e.value}s")
            await asyncio.sleep(e.value)
            return await safe_pin_message(client, chat_id, message_id)
        else:
            logger.warning(f"Long FloodWait while pinning: {e.value}s")
            return False
    except Exception as e:
        logger.error(f"Error pinning message: {e}")
        return False

async def check(userbot, client, link):
    logging.info(link)
    msg_id = 0
    try:
        if '/t.me/' in link and link.count('/') >= 4:
            parts = link.split('/')
            if len(parts) >= 5:
                msg_id = int(parts[-1])
        else:
            msg_id = int(link.split("/")[-1])
    except ValueError:
        if '?single' not in link:
            return False, "**Invalid Link!**"
        link_ = link.split("?single")[0]
        msg_id = int(link_.split("/")[-1])
    if 't.me/c/' in link:
        try:
            chat = int('-100' + str(link.split("/")[-2]))
            await userbot.get_messages(chat, msg_id)
            return True, None
        except ValueError:
            return False, "**Invalid Link!**"
        except Exception as e:
            logging.info(e)
            return False, "Can't access provide post,Please send invite link first."
    else:
        try:
            try:
              chat = str(link.split("/")[-2])
              await client.get_messages(chat, msg_id)
            except Exception:
              chat = str(link.split("/")[-3])
              await client.get_messages(chat, msg_id)
            return True, None
        except Exception as e:
            logging.info(e)
            return False, "Maybe bot is banned from the chat, or your link is invalid!"
            
async def upload_media(client, sender, target_chat_id, file, caption, edit, topic_id):
    thumb_path = None
    try:
        size_limit = 2000 * 1024 * 1024
        file_size = os.path.getsize(file)
        
        if file_size > size_limit:
            await edit.edit("File is too large. Splitting and uploading in parts...")
            await split_and_upload_file(client, sender, target_chat_id, file, caption, topic_id)
            return
            
        video_formats = {'mp4', 'mkv', 'avi', 'mov'}
        document_formats = {'pdf', 'docx', 'txt', 'epub'}
        image_formats = {'jpg', 'png', 'jpeg'}

        if file.split('.')[-1].lower() in video_formats:
            metadata = video_metadata(file)
            width, height, duration = metadata['width'], metadata['height'], metadata['duration']
            
            try:
                thumb_enable = db.get_thumbnail_enabled(sender)
                if thumb_enable:
                    result = db.get_watermark_text(sender)
                    if result is None:
                      watermark_text = "no"
                    else:
                      watermark_text = result
                    thumbnail_url = db.get_thumbnail(sender)
                    
                    if watermark_text.lower() != "no":
                        thumb_path = await screenshot(file, duration, sender)
                    elif thumbnail_url:
                        thumb_path = os.path.join("thumbnail.jpg")
                        try:
                            response = requests.get(thumbnail_url)
                            if response.status_code == 200:
                                with open(thumb_path, 'wb') as f:
                                    f.write(response.content)
                            else:
                                logger.error(f"Failed to download thumbnail: {response.status_code}")
                                thumb_path = await screenshot(file, duration, sender)
                        except Exception as e:
                            logger.error(f"Error downloading thumbnail: {e}")
                            thumb_path = await screenshot(file, duration, sender)
                    else:
                        thumb_path = await screenshot(file, duration, sender)
                else:
                    thumbnail_url = None
                    thumb_path = await screenshot(file, duration, sender)
            except Exception as e:
                logger.error(f"Error setting thumbnail: {e}")
                thumb_path = await screenshot(file, duration, sender)
                
            sent_msg = await client.send_video(
                chat_id=target_chat_id,
                video=file,
                caption=caption,
                height=height,
                width=width,
                duration=duration,
                thumb=thumb_path,
                reply_to_message_id=topic_id,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_for_pyrogram,
                progress_args=(
                    client,
                    "**__Unrestricting__(Uploading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                    edit,
                    time.time()
                )
            )
            db.increment_cloned_count(sender)
            return sent_msg
                
        elif file.split('.')[-1].lower() in image_formats:
            sent_msg = await client.send_photo(
                chat_id=target_chat_id,
                photo=file,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_for_pyrogram,
                reply_to_message_id=topic_id,
                progress_args=(
                    client,
                    "**__Unrestricting__(Uploading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                    edit,
                    time.time()
                )
            )
            db.increment_cloned_count(sender)
            return sent_msg
        else:
            if file.split('.')[-1].lower() in document_formats:
                try:
                    thumbnail_url = db.get_thumbnail(sender)
                    if thumbnail_url:
                        thumb_path = os.path.join("thumbnail.jpg")
                        try:
                            response = requests.get(thumbnail_url)
                            if response.status_code == 200:
                                with open(thumb_path, 'wb') as f:
                                    f.write(response.content)
                            else:
                                logger.error(f"Failed to download thumbnail: {response.status_code}")
                                thumb_path = None
                        except Exception as e:
                            logger.error(f"Error downloading thumbnail: {e}")
                            thumb_path = None
                except Exception:
                    thumb_path = None
                    
            sent_msg = await client.send_document(
                chat_id=target_chat_id,
                document=file,
                caption=caption,
                thumb=thumb_path,
                reply_to_message_id=topic_id,
                parse_mode=ParseMode.MARKDOWN,
                progress=progress_for_pyrogram,
                progress_args=(
                    client,
                    "**__Unrestricting__(Uploading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                    edit,
                    time.time()
                )
            )
            db.increment_cloned_count(sender)
            await asyncio.sleep(2)
            return sent_msg

    except Exception as e:
        logger.error(f"Error during media upload: {e}")
        await client.send_message(target_chat_id, f"**Upload Failed:** {str(e)}")
        return None

    finally:
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception as e:
                logger.error(f"Error removing thumbnail file: {e}")
        gc.collect()

async def copy_message_with_chat_id(app, userbot, sender, chat_id, message_id, edit):
    try:
        target_chat_id = db.get_chat_id(sender)
    except Exception as e:
        logger.error(f"Error getting chat_id from database: {e}")
        target_chat_id = sender
        
    file = None
    result = None
    size_limit = 2 * 1024 * 1024 * 1024

    try:
        msg = await app.get_messages(chat_id, message_id)
        caption = msg.caption
        
        # Check if message is pinned - improved method
        is_pinned = await is_message_pinned(app, chat_id, message_id)

        topic_id = None
        if isinstance(target_chat_id, str) and '/' in target_chat_id:
            target_chat_id, topic_id = map(int, target_chat_id.split('/', 1))

        if msg.media:
            result = await send_media_message(app, target_chat_id, msg, caption, topic_id)
            if result and is_pinned:
                await safe_pin_message(app, target_chat_id, result.id)
            db.increment_cloned_count(sender)
            return
        elif msg.text:
            result = await app.copy_message(target_chat_id, chat_id, message_id, reply_to_message_id=topic_id)
            if result and is_pinned:
                await safe_pin_message(app, target_chat_id, result.id)
            db.increment_cloned_count(sender)
            return

        if result is None:
            try:
                await safe_edit_message(edit, "Trying if it is a group...")
            except Exception as e:
                logger.error(f"Error editing message: {e}")
                
            try:
                await userbot.join_chat(chat_id)
            except Exception as e:
                print(e)
                pass
                
            chat_id = (await userbot.get_chat(f"@{chat_id}")).id
            msg = await userbot.get_messages(chat_id, message_id)
            caption = msg.caption
            if not msg or msg.service or not msg:
                return
                
            # Check if message is pinned (userbot)
            is_pinned = await is_message_pinned(userbot, chat_id, message_id)

            # Check if channel has content protection
            is_protected = await check_channel_content_protection(userbot, chat_id)
            
            if msg.text:
                result = await app.send_message(target_chat_id, msg.text.markdown, reply_to_message_id=topic_id)
                if result and is_pinned:
                    await safe_pin_message(app, target_chat_id, result.id)
                db.increment_cloned_count(sender)
                return

            # For media messages in public channels, try direct copy first if not protected
            if not is_protected:
                try:
                    await safe_edit_message(edit, "**Attempting direct copy...**")
                    result = await try_forward_message(userbot, app, sender, chat_id, message_id, target_chat_id, topic_id)
                    if result:
                        if is_pinned:
                            await safe_pin_message(app, target_chat_id, result.id)
                        return
                except Exception as e:
                    logger.error(f"Direct copy failed, falling back to download: {e}")

            # If protected or direct copy failed, use download method
            try:
              file = await userbot.download_media(
                msg,
                progress=progress_for_pyrogram,
                progress_args=(
                    app,
                    "**__Unrestricting__(Downloading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                    edit,
                    time.time()
                )
              )
              db.increment_downloaded_count()
            except FloodWait as e:
              print(f"Flood wait: {e.value} seconds")
              if e.value < 300:
                  await asyncio.sleep(e.value)
                  await safe_edit_message(edit, "Retrying after FloodWait...")
              else:
                  await safe_edit_message(edit, f"⚠️ **Telegram Rate Limit Detected** ⚠️\n\nPlease try again after {e.value} seconds. Telegram has temporary restrictions on downloading this content.")
              return

            if msg.photo:
                result = await app.send_photo(target_chat_id, file, caption=caption, reply_to_message_id=topic_id)
                if result and is_pinned:
                    await safe_pin_message(app, target_chat_id, result.id)
                db.increment_cloned_count(sender)
            elif msg.video or msg.document:
                file_size = get_message_file_size(msg)
                if file_size > size_limit:
                    await safe_edit_message(edit, "File is too large. Splitting and uploading in parts...")
                    await split_and_upload_file(app, sender, target_chat_id, file, caption, topic_id)
                    return
                result = await upload_media(app, sender, target_chat_id, file, caption, edit, topic_id)
                if result and is_pinned:
                    await safe_pin_message(app, target_chat_id, result.id)
            elif msg.audio:
                result = await app.send_audio(target_chat_id, file, caption=caption, reply_to_message_id=topic_id)
                if result and is_pinned:
                    await safe_pin_message(app, target_chat_id, result.id)
                db.increment_cloned_count(sender)
            elif msg.voice:
                result = await app.send_voice(target_chat_id, file, reply_to_message_id=topic_id)
                if result and is_pinned:
                    await safe_pin_message(app, target_chat_id, result.id)
                db.increment_cloned_count(sender)
            elif msg.sticker:
                result = await app.send_sticker(target_chat_id, msg.sticker.file_id, reply_to_message_id=topic_id)
                if result and is_pinned:
                    await safe_pin_message(app, target_chat_id, result.id)
                db.increment_cloned_count(sender)
            else:
                await safe_edit_message(edit, "Unsupported media type.")

    except Exception as e:
        print(f"Error : {e}")
        pass

    finally:
        if file and os.path.exists(file):
            os.remove(file)
            
async def safe_edit_message(message, text):
    try:
        await message.edit(text)
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            await safe_edit_message(message, text)
        else:
            logger.warning(f"FloodWait: {e.value} seconds. Skipping edit.")
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")

async def safe_send_message(client, chat_id, text):
    try:
        return await client.send_message(chat_id, text)
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            return await safe_send_message(client, chat_id, text)
        else:
            logger.warning(f"FloodWait: {e.value} seconds. Skipping message.")
            return None
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return None
            
async def get_msg(userbot, client, sender, edit_id, msg_link, i):
    try:
        msg_link = msg_link.split("?single")[0]
        chat, msg_id = None, None
        size_limit = 2 * 1024 * 1024 * 1024
        file = ''
        
        try:
            edit = await client.edit_message_text(sender, edit_id, "**Processing your request...**")
        except FloodWait as e:
            if e.value < 30:
                await asyncio.sleep(e.value)
                edit = await client.edit_message_text(sender, edit_id, "**Processing your request...**")
            else:
                edit = await client.send_message(sender, "**Processing your request...**")
        except Exception as e:
            edit = await client.send_message(sender, "**Processing your request...**")
        
        if is_bot_url(msg_link):
          if not is_auth(sender):
            await client.send_message(sender, "**Only for premium users.**")
            return
          else:
            await safe_edit_message(edit, "**Bot link detected. Preparing to interact with bot...**")
            bot_username, start_param = parse_bot_url(msg_link)
            
            # Start the bot and get its responses
            bot_messages = await start_bot_and_get_messages(userbot, bot_username, start_param, sender, edit)
            
            # Process the messages from the bot
            await process_bot_messages(userbot, client, sender, edit, bot_messages)
            return

        if 't.me/c/' in msg_link or 't.me/b/' in msg_link:
            parts = msg_link.split("/")
            if 't.me/b/' in msg_link:
                chat = parts[-2]
                msg_id = int(parts[-1]) + i
            else:
                chat = int('-100' + parts[parts.index('c') + 1])
                msg_id = int(parts[-1]) + i
                
            try:
                await safe_edit_message(edit, "**Accessing private channel...**")
                try:
                    await userbot.get_chat(chat)
                except Exception:
                    await safe_edit_message(edit, "**Cannot access this private channel.** Please send the invitation link of this channel first so the bot can join.")
                    return
                
                msg = await userbot.get_messages(chat, msg_id)
                if not msg or msg.service or not msg:
                    await safe_edit_message(edit, "**Message not found or is a service message.**")
                    return
                
                # Check if message is pinned - improved method
                is_pinned = await is_message_pinned(userbot, chat, msg_id)
                
                try:
                    target_chat_id = db.get_chat_id(sender)
                    topic_id = None
                    if isinstance(target_chat_id, str) and '/' in target_chat_id:
                        target_chat_id, topic_id = map(int, target_chat_id.split('/', 1))
                except (PeerIdInvalid, ChatIdInvalid, Exception) as e:
                    logger.error(f"Error with chat_id: {e}. Using sender as chat_id.")
                    target_chat_id = sender
                    topic_id = None

                # Check if channel has content protection
                await safe_edit_message(edit, "**Checking channel content protection...**")
                is_protected = await check_channel_content_protection(userbot, chat)
                
                if hasattr(msg, 'web_preview') and msg.web_preview:
                    result = await clone_message(client, msg, target_chat_id, topic_id, edit_id)
                    if result and is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    return

                if msg.text:
                    await safe_edit_message(edit, "**Cloning text message...**")
                    result = await client.send_message(target_chat_id, msg.text.markdown if hasattr(msg.text, 'markdown') else msg.text, reply_to_message_id=topic_id)
                    if is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    db.increment_cloned_count(sender)
                    await safe_edit_message(edit, "**Message cloned successfully!**")
                    await asyncio.sleep(2)
                    await edit.delete()
                    return

                if msg.sticker:
                    result = await handle_sticker(client, msg, target_chat_id, topic_id, edit_id)
                    if result and is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    return

                # For media messages, check protection and try appropriate method
                if not is_protected:
                    # Channel allows forwarding, try to copy/forward first
                    await safe_edit_message(edit, "**Channel allows forwarding. Attempting direct copy...**")
                    result = await try_forward_message(userbot, client, sender, chat, msg_id, target_chat_id, topic_id)
                    if result:
                        if is_pinned:
                            await safe_pin_message(client, target_chat_id, result.id)
                        await safe_edit_message(edit, "**Message copied successfully!**")
                        await asyncio.sleep(2)
                        await edit.delete()
                        return
                    else:
                        await safe_edit_message(edit, "**Direct copy failed. Switching to download method...**")

                # If channel is protected or direct copy failed, use download method
                await safe_edit_message(edit, "**Using download and upload method...**")
                
                file_size = get_message_file_size(msg)
                file_name = await get_media_filename(msg)
                await safe_edit_message(edit, "**Downloading...**")
                
                try:
                    file = await userbot.download_media(
                        msg,
                        file_name=file_name,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            client,
                            "**__Unrestricting__(Downloading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                            edit,
                            time.time()
                        )
                    )
                    db.increment_downloaded_count()
                except FloodWait as e:
                    if e.value < 300:
                        await safe_edit_message(edit, f"Flood wait detected. Waiting for {e.value} seconds...")
                        await asyncio.sleep(e.value)
                        await safe_edit_message(edit, "Retrying download...")
                        file = await userbot.download_media(
                            msg,
                            file_name=file_name,
                            progress=progress_for_pyrogram,
                            progress_args=(
                                client,
                                "**__Unrestricting__(Downloading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                                edit,
                                time.time()
                            )
                        )
                        db.increment_downloaded_count()
                    else:
                        await safe_edit_message(edit, f"⚠️ **Telegram Rate Limit Detected** ⚠️\n\nPlease try again after {e.value} seconds. Telegram has temporary restrictions on downloading this content.")
                        return
                
                caption = msg.caption if msg.caption else ""

                if msg.audio:
                    result = await client.send_audio(target_chat_id, file, caption=caption, reply_to_message_id=topic_id)
                    if is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    db.increment_cloned_count(sender)
                    await edit.delete()
                    os.remove(file)
                    return
                
                if msg.voice:
                    result = await client.send_voice(target_chat_id, file, reply_to_message_id=topic_id)
                    if is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    db.increment_cloned_count(sender)
                    await edit.delete()
                    os.remove(file)
                    return

                if msg.video_note:
                    result = await client.send_video_note(target_chat_id, file, reply_to_message_id=topic_id)
                    if is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    db.increment_cloned_count(sender)
                    await edit.delete()
                    os.remove(file)
                    return

                if msg.photo:
                    result = await client.send_photo(target_chat_id, file, caption=caption, reply_to_message_id=topic_id)
                    if is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                    db.increment_cloned_count(sender)
                    await edit.delete()
                    os.remove(file)
                    return
                else:
                    result = await upload_media(client, sender, target_chat_id, file, caption, edit, topic_id)
                    if result and is_pinned:
                        await safe_pin_message(client, target_chat_id, result.id)
                
            except (ChannelBanned, ChannelInvalid, ChannelPrivate, ChatIdInvalid, ChatInvalid) as e:
                await safe_edit_message(edit, f"**Cannot access this channel:** {str(e)}\n\nPlease send the invitation link of this channel first so the bot can join.")
                return
            except FloodWait as e:
                await safe_edit_message(edit, f"⚠️ **Telegram Rate Limit Detected** ⚠️\n\nPlease try again after {e.value} seconds.")
                return
            except Exception as e:
                logger.error(f"Error accessing private channel: {e}")
                await safe_edit_message(edit, f"**Error:** {str(e)}")
                return
        else:
            try:
                await safe_edit_message(edit, "**Public link detected...**")
                chat = msg_link.split("t.me/")[1].split("/")[0]
                msg_id = int(msg_link.split("/")[-1])
                
                # Check if message is pinned in public chat - improved method
                is_pinned = await is_message_pinned(client, chat, msg_id)
                    
                await copy_message_with_chat_id(client, userbot, sender, chat, msg_id, edit)
                await edit.delete()
                return
            except FloodWait as e:
                if e.value < 300:
                    await safe_send_message(client, sender, f"⚠️ **Rate limit detected. Waiting for {e.value} seconds before retrying.**")
                    await asyncio.sleep(e.value)
                    chat = msg_link.split("t.me/")[1].split("/")[0]
                    msg_id = int(msg_link.split("/")[-1])
                    await copy_message_with_chat_id(client, userbot, sender, chat, msg_id, edit)
                    await edit.delete()
                    return
                else:
                    await safe_send_message(client, sender, f"⚠️ **Telegram Rate Limit Detected** ⚠️\n\nPlease try again after {e.value} seconds.")
                    return
            
    except FloodWait as e:
        logger.warning(f"FloodWait: {e.value} seconds")
        try:
            if e.value < 300:
                await safe_send_message(client, sender, f"⚠️ **Rate limit detected. Waiting for {e.value} seconds before retrying.**")
                await asyncio.sleep(e.value)
                await get_msg(userbot, client, sender, edit_id, msg_link, i)
            else:
                await safe_send_message(client, sender, f"⚠️ **Telegram Rate Limit Detected** ⚠️\n\nPlease try again after {e.value} seconds.")
        except Exception as inner_e:
            logger.error(f"Error handling FloodWait: {inner_e}")
            await safe_send_message(client, sender, f"⚠️ **Error:** {str(inner_e)}")
    except Exception as e:
        logger.error(f"Error in get_msg: {e}")
        try:
            await safe_send_message(client, sender, f"**Error processing request:** {str(e)}")
        except Exception as send_error:
            logger.error(f"Failed to send error message: {send_error}")
    finally:
        if file and os.path.exists(file):
            os.remove(file)
        
async def clone_message(app, msg, target_chat_id, topic_id, edit_id):
    try:
        edit = await app.edit_message_text(target_chat_id, edit_id, "Cloning...")
        result = await app.send_message(target_chat_id, msg.text.markdown, reply_to_message_id=topic_id)
        db.increment_cloned_count(msg.from_user.id if msg.from_user else target_chat_id)
        await edit.delete()
        return result
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            return await clone_message(app, msg, target_chat_id, topic_id, edit_id)
        else:
            await safe_send_message(app, target_chat_id, f"⚠️ **Rate limit detected: {e.value}s. Your message will be cloned when the rate limit expires.**")
            return None

async def clone_text_message(app, msg, target_chat_id, topic_id, edit_id):
    try:
        edit = await app.edit_message_text(target_chat_id, edit_id, "Cloning text message...")
        result = await app.send_message(target_chat_id, msg.text.markdown, reply_to_message_id=topic_id)
        db.increment_cloned_count(msg.from_user.id if msg.from_user else target_chat_id)
        await edit.delete()
        return result
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            return await clone_text_message(app, msg, target_chat_id, topic_id, edit_id)
        else:
            await safe_send_message(app, target_chat_id, f"⚠️ **Rate limit detected: {e.value}s. Your message will be cloned when the rate limit expires.**")

async def handle_sticker(app, msg, target_chat_id, topic_id, edit_id):
    try:
        edit = await app.edit_message_text(target_chat_id, edit_id, "Handling sticker...")
        result = await app.send_sticker(target_chat_id, msg.sticker.file_id, reply_to_message_id=topic_id)
        db.increment_cloned_count(msg.from_user.id if msg.from_user else target_chat_id)
        await edit.delete()
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            await handle_sticker(app, msg, target_chat_id, topic_id, edit_id)
        else:
            await safe_send_message(app, target_chat_id, f"⚠️ **Rate limit detected: {e.value}s. Your sticker will be sent when the rate limit expires.**")

async def send_media_message(app, target_chat_id, msg, caption, topic_id):
    try:
        result = None
        if msg.video:
            result = await app.send_video(target_chat_id, msg.video.file_id, caption=caption, reply_to_message_id=topic_id)
        elif msg.document:
            result = await app.send_document(target_chat_id, msg.document.file_id, caption=caption, reply_to_message_id=topic_id)
        elif msg.photo:
            result = await app.send_photo(target_chat_id, msg.photo.file_id, caption=caption, reply_to_message_id=topic_id)
        elif msg.audio:
            result = await app.send_audio(target_chat_id, msg.audio.file_id, caption=caption, reply_to_message_id=topic_id)
        elif msg.voice:
            result = await app.send_voice(target_chat_id, msg.voice.file_id, caption=caption, reply_to_message_id=topic_id)
        elif msg.sticker:
            result = await app.send_sticker(target_chat_id, msg.sticker.file_id, reply_to_message_id=topic_id)
        
        if result:
            db.increment_cloned_count(msg.from_user.id if msg.from_user else target_chat_id)
            return result
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            return await send_media_message(app, target_chat_id, msg, caption, topic_id)
        else:
            await safe_send_message(app, target_chat_id, f"⚠️ **Rate limit detected: {e.value}s. Media will be sent when the rate limit expires.**")
            return None
    except Exception as e:
        print(f"Error while sending media: {e}")
    
    try:
        result = await app.copy_message(target_chat_id, msg.chat.id, msg.id, reply_to_message_id=topic_id)
        db.increment_cloned_count(msg.from_user.id if msg.from_user else target_chat_id)
        return result
    except FloodWait as e:
        if e.value < 30:
            await asyncio.sleep(e.value)
            return await send_media_message(app, target_chat_id, msg, caption, topic_id)
        else:
            await safe_send_message(app, target_chat_id, f"⚠️ **Rate limit detected: {e.value}s. Message will be copied when the rate limit expires.**")
            return None
    
async def get_media_filename(msg):
    if msg.document:
        return msg.document.file_name if msg.document.file_name else "document.file"
    if msg.video:
        return msg.video.file_name if msg.video.file_name else "video.mp4"
    if msg.photo:
        return "photo.jpg"
    if msg.audio:
        return msg.audio.file_name if msg.audio.file_name else "audio.mp3"
    if msg.voice:
        return "voice.ogg"
    if msg.video_note:
        return "video_note.mp4"
    return "unknown_file"

def get_message_file_size(msg):
    if msg.document and hasattr(msg.document, 'file_size'):
        return msg.document.file_size
    if msg.photo and hasattr(msg.photo, 'file_size'):
        return msg.photo.file_size
    if msg.video and hasattr(msg.video, 'file_size'):
        return msg.video.file_size
    if msg.audio and hasattr(msg.audio, 'file_size'):
        return msg.audio.file_size
    if msg.voice and hasattr(msg.voice, 'file_size'):
        return msg.voice.file_size
    if msg.video_note and hasattr(msg.video_note, 'file_size'):
        return msg.video_note.file_size
    return 1

async def split_and_upload_file(app, sender, target_chat_id, file_path, caption, topic_id):
    try:
        if not os.path.exists(file_path):
            await app.send_message(sender, "❌ File not found!")
            return

        file_size = os.path.getsize(file_path)
        PART_SIZE = int(1.5 * 1024 * 1024 * 1024)
        BUFFER_SIZE = 8 * 1024 * 1024
        total_parts = (file_size + PART_SIZE - 1) // PART_SIZE

        start = await app.send_message(sender, 
            f"📦 **File Size:** {file_size/1024/1024/1024:.2f}GB\n"
            f"🔢 **Splitting into:** {total_parts} parts (1.5GB each)"
        )

        for part_number in range(total_parts):
            part_file = f"{file_path}.part{part_number:03d}"
            bytes_written = 0
            
            progress_msg = await app.send_message(sender, f"📝 Creating part {part_number+1}/{total_parts}")
            
            try:
                async with aiofiles.open(file_path, "rb") as source_file:
                    await source_file.seek(part_number * PART_SIZE)
                    
                    async with aiofiles.open(part_file, "wb") as part_f:
                        while bytes_written < PART_SIZE:
                            read_size = min(BUFFER_SIZE, PART_SIZE - bytes_written)
                            buffer = await source_file.read(read_size)
                            
                            if not buffer:
                                break
                                
                            await part_f.write(buffer)
                            bytes_written += len(buffer)
                            
                            if bytes_written % (100 * 1024 * 1024) < BUFFER_SIZE:
                                await progress_msg.edit(
                                    f"📝 Creating part {part_number+1}/{total_parts}\n"
                                    f"Progress: {bytes_written/PART_SIZE*100:.1f}%"
                                )
                
                await progress_msg.edit(f"⏫ Uploading part {part_number+1}/{total_parts}")
                
                file_ext = os.path.splitext(file_path)[1].lower()
                
                how_to_watch = ""
                if file_ext in ['.mp4', '.mkv', '.mov', '.avi', '.wmv', '.m4v']:
                    how_to_watch = (
                        "\n\n📱 **How to watch this split video:**\n"
                        "**For Android:** Download all parts, then use MX Player or VLC and select 'Open as > Combine files'\n"
                        "**For PC:** Download all parts, then use HJSplit or 7-Zip to join files before playing"
                    )
                
                part_caption = f"{caption}\n\nPart {part_number+1}/{total_parts}{how_to_watch}" if caption else f"Part {part_number+1}/{total_parts}{how_to_watch}"
                
                await app.send_document(
                    chat_id=target_chat_id,
                    document=part_file,
                    caption=part_caption,
                    reply_to_message_id=topic_id,
                    progress=progress_for_pyrogram,
                    progress_args=(
                        app,
                        f"**Uploading Part {part_number+1}/{total_parts}**",
                        progress_msg,
                        time.time()
                    )
                )
                
                await progress_msg.delete()
                
            except Exception as e:
                logger.error(f"Error processing part {part_number+1}: {str(e)}\n{traceback.format_exc()}")
                await app.send_message(sender, f"❌ Error on part {part_number+1}: {str(e)}")
                raise
            finally:
                if os.path.exists(part_file):
                    os.remove(part_file)
                gc.collect()

        await start.edit(f"✅ Successfully uploaded {total_parts} parts!")
        os.remove(file_path)

    except Exception as e:
        error_msg = f"❌ Error: {str(e)}\n\n{traceback.format_exc()}"
        logger.error(f"Split error: {error_msg}")
        
        for part_file in glob.glob(f"{file_path}.part*"):
            try:
                os.remove(part_file)
            except Exception as cleanup_error:
                logger.error(f"Cleanup error: {cleanup_error}")

        await app.send_message(sender, f"❌ Upload failed: {str(e)}")

    finally:
        if 'temp_dir' in locals() and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        gc.collect()

def is_bot_url(url: str) -> bool:
    """Check if the URL is a Telegram bot URL with start parameter."""
    if not url.startswith('https://t.me/') and not url.startswith('t.me/'):
        return False
    
    parsed = urlparse(url if url.startswith('https://') else f'https://{url}')
    query_params = parse_qs(parsed.query)
    
    # Bot URLs have a 'start' parameter
    return 'start' in query_params and not parsed.path.endswith('/addlist')

def parse_bot_url(url: str) -> tuple:
    """Parse a Telegram bot URL with start parameter."""
    parsed = urlparse(url if url.startswith('https://') else f'https://{url}')
    path = parsed.path.strip('/')
    query_params = parse_qs(parsed.query)
    
    # Get the first value of 'start' parameter
    start_param = query_params.get('start', [''])[0]
    
    return path, start_param

async def start_bot_and_get_messages(userbot, bot_username: str, start_param: str, sender_id: int, edit_msg: Message) -> list:
    messages = []
    chat_id = None
    user_id_str = f'{sender_id}'
    
    # Add user to active batch set
    if user_id_str not in batchx:
        batchx.add(user_id_str)
    
    try:
        # Add cancel button to the message
        await edit_msg.edit(
            "Resolving bot username...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
            ]])
        )
        
        # Check if batch was cancelled
        if user_id_str not in batchx:
            await edit_msg.edit("❌ Process cancelled")
            return []
        
        bot_user = await userbot.get_users(bot_username)
        chat_id = bot_user.id
        
        await edit_msg.edit(
            f"Starting bot @{bot_username} with parameter...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
            ]])
        )
        
        # Check if batch was cancelled
        if user_id_str not in batchx:
            await edit_msg.edit("❌ Process cancelled")
            return []
        
        start_command = f"/start {start_param}"
        
        try:
            # Use get_chat_history instead of get_history
            async for message in userbot.get_chat_history(chat_id, limit=10):
                if message.from_user and message.from_user.is_bot:
                    await message.delete()
        except Exception as e:
            logger.warning(f"Could not clear chat history: {e}")
        
        sent_msg = await userbot.send_message(chat_id, start_command)
        
        await edit_msg.edit(
            "Waiting for bot response...",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
            ]])
        )
        
        last_message_id = sent_msg.id
        received_count = 0
        last_check_time = time.time()
        last_message_time = time.time()
        
        while time.time() - last_message_time < 30 and user_id_str in batchx:
            if time.time() - last_check_time < 2:
                await asyncio.sleep(0.5)
                continue
                
            last_check_time = time.time()
            
            # Check if batch was cancelled
            if user_id_str not in batchx:
                await edit_msg.edit("❌ Process cancelled")
                return []
            
            new_messages = []
            async for message in userbot.get_chat_history(chat_id, limit=30):
                if message.id <= last_message_id:
                    continue
                if message.from_user and message.from_user.is_bot:
                    new_messages.append(message)
                    last_message_id = max(last_message_id, message.id)
            
            if new_messages:
                received_count += len(new_messages)
                messages.extend(new_messages)
                await edit_msg.edit(
                    f"Received {received_count} messages from bot...",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                    ]])
                )
                
                # Reset timer whenever we receive new messages
                last_message_time = time.time()
                
                # Check for force subscription in the latest message
                latest_msg = new_messages[0]
                
                # Try general message click approach for all buttons
                force_sub_detected = False
                force_sub_keywords = ["join", "channel", "backup", "try again", "subscribe", "member"]
                
                # Check if batch was cancelled
                if user_id_str not in batchx:
                    await edit_msg.edit("❌ Process cancelled")
                    return []
                
                # First, let's check if the message has any buttons at all
                if hasattr(latest_msg, 'reply_markup') and latest_msg.reply_markup:
                    # Check the message text/caption for force subscribe clues
                    msg_text = latest_msg.text or ""
                    caption_text = latest_msg.caption or ""
                    
                    if any(keyword in msg_text.lower() for keyword in force_sub_keywords) or \
                       any(keyword in caption_text.lower() for keyword in force_sub_keywords):
                        force_sub_detected = True
                    
                    # Scan all buttons for keywords and attempt to click appropriate ones
                    for row_idx, row in enumerate(latest_msg.reply_markup.inline_keyboard):
                        for col_idx, button in enumerate(row):
                            button_text = button.text.lower()
                            
                            # Check if batch was cancelled
                            if user_id_str not in batchx:
                                await edit_msg.edit("❌ Process cancelled")
                                return []
                            
                            # Check if this button appears to be a 'join' or 'try again' button
                            if any(keyword in button_text for keyword in force_sub_keywords):
                                force_sub_detected = True
                                
                                try:
                                    await edit_msg.edit(
                                        f"Found potential force-subscribe button: '{button.text}'. Attempting to click...",
                                        reply_markup=InlineKeyboardMarkup([[
                                            InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                        ]])
                                    )
                                    
                                    # Try to click the button using the simpler click() method
                                    result = await latest_msg.click(row_idx, col_idx)
                                    await edit_msg.edit(
                                        f"Button clicked. Waiting for response...",
                                        reply_markup=InlineKeyboardMarkup([[
                                            InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                        ]])
                                    )
                                    await asyncio.sleep(3)
                                    
                                    # Check if batch was cancelled
                                    if user_id_str not in batchx:
                                        await edit_msg.edit("❌ Process cancelled")
                                        return []
                                    
                                    # If clicking returned a URL, and it's a Telegram link, try to join
                                    if isinstance(result, str) and "t.me/" in result:
                                        await edit_msg.edit(
                                            f"Button returned a Telegram link. Attempting to join...",
                                            reply_markup=InlineKeyboardMarkup([[
                                                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                            ]])
                                        )
                                        try:
                                            await userbot.join_chat(result)
                                            await edit_msg.edit(
                                                f"Joined channel via button link. Retrying bot start...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            return await start_bot_and_get_messages(userbot, bot_username, start_param, sender_id, edit_msg)
                                        except Exception as e:
                                            await edit_msg.edit(
                                                f"Could not join via button link: {str(e)}",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                except Exception as e:
                                    await edit_msg.edit(
                                        f"Could not click button: {str(e)}",
                                        reply_markup=InlineKeyboardMarkup([[
                                            InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                        ]])
                                    )
                                    
                                    # Check if batch was cancelled
                                    if user_id_str not in batchx:
                                        await edit_msg.edit("❌ Process cancelled")
                                        return []
                                    
                                    # Method 1: Try to extract and join channel from the button URL
                                    if hasattr(button, 'url') and button.url:
                                        try:
                                            await edit_msg.edit(
                                                f"Attempting to join channel via URL...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            join_url = button.url
                                            
                                            # Extract channel username or invite link
                                            if "t.me/" in join_url:
                                                # Get channel username or invite code from t.me link
                                                channel_identifier = join_url.split("t.me/")[1].split("?")[0]
                                                if channel_identifier.startswith("+") or channel_identifier.startswith("joinchat/"):
                                                    # This is an invite link
                                                    await userbot.join_chat(join_url)
                                                else:
                                                    # This is a public username
                                                    await userbot.join_chat(channel_identifier)
                                            else:
                                                # Direct join with whatever URL was provided
                                                await userbot.join_chat(join_url)
                                                
                                            await edit_msg.edit(
                                                f"Joined channel via URL. Retrying bot start...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            
                                            # Restart the bot with same parameters
                                            return await start_bot_and_get_messages(userbot, bot_username, start_param, sender_id, edit_msg)
                                        except Exception as e:
                                            await edit_msg.edit(
                                                f"Could not join channel via URL: {str(e)}",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                    
                                    # Method 3: Extract text from button and check for channel mentions
                                    try:
                                        if button.text:
                                            # Look for channel mentions in the button text
                                            import re
                                            # Match @username or t.me/username patterns
                                            channel_matches = re.findall(r'@(\w+)|t\.me/(\w+)', button.text)
                                            for match in channel_matches:
                                                username = next((x for x in match if x), None)
                                                if username:
                                                    await edit_msg.edit(
                                                        f"Found channel mention: @{username}. Attempting to join...",
                                                        reply_markup=InlineKeyboardMarkup([[
                                                            InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                        ]])
                                                    )
                                                    try:
                                                        await userbot.join_chat(f"@{username}")
                                                        await edit_msg.edit(
                                                            f"Joined @{username}. Retrying bot start...",
                                                            reply_markup=InlineKeyboardMarkup([[
                                                                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                            ]])
                                                        )
                                                        return await start_bot_and_get_messages(userbot, bot_username, start_param, sender_id, edit_msg)
                                                    except Exception as channel_err:
                                                        await edit_msg.edit(
                                                            f"Could not join @{username}: {str(channel_err)}",
                                                            reply_markup=InlineKeyboardMarkup([[
                                                                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                            ]])
                                                        )
                                    except Exception as e:
                                        await edit_msg.edit(
                                            f"Error extracting channel from button text: {str(e)}",
                                            reply_markup=InlineKeyboardMarkup([[
                                                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                            ]])
                                        )
                
                # Check if batch was cancelled
                if user_id_str not in batchx:
                    await edit_msg.edit("❌ Process cancelled")
                    return []
                
                if force_sub_detected and time.time() - last_message_time > 10 and len(new_messages) == 1:
                    # Last-ditch effort: scan the entire message for any links or entities
                    for msg in new_messages:
                        # Check for message entities (links, mentions, etc.)
                        if hasattr(msg, 'entities') and msg.entities:
                            for entity in msg.entities:
                                if entity.type in ('url', 'text_link', 'mention'):
                                    try:
                                        if entity.type == 'url':
                                            url = msg.text[entity.offset:entity.offset + entity.length]
                                            if 't.me/' in url:
                                                await edit_msg.edit(
                                                    f"Found channel URL in message. Attempting to join...",
                                                    reply_markup=InlineKeyboardMarkup([[
                                                        InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                    ]])
                                                )
                                                await userbot.join_chat(url)
                                                await edit_msg.edit(
                                                    f"Joined channel. Retrying bot start...",
                                                    reply_markup=InlineKeyboardMarkup([[
                                                        InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                    ]])
                                                )
                                                return await start_bot_and_get_messages(userbot, bot_username, start_param, sender_id, edit_msg)
                                        elif entity.type == 'text_link' and entity.url:
                                            await edit_msg.edit(
                                                f"Found text link in message. Attempting to join...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            await userbot.join_chat(entity.url)
                                            await edit_msg.edit(
                                                f"Joined channel. Retrying bot start...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            return await start_bot_and_get_messages(userbot, bot_username, start_param, sender_id, edit_msg)
                                        elif entity.type == 'mention':
                                            mention = msg.text[entity.offset:entity.offset + entity.length]
                                            await edit_msg.edit(
                                                f"Found mention: {mention}. Attempting to join...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            await userbot.join_chat(mention)
                                            await edit_msg.edit(
                                                f"Joined channel. Retrying bot start...",
                                                reply_markup=InlineKeyboardMarkup([[
                                                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                                ]])
                                            )
                                            return await start_bot_and_get_messages(userbot, bot_username, start_param, sender_id, edit_msg)
                                    except Exception as e:
                                        await edit_msg.edit(
                                            f"Failed to join from entity: {str(e)}",
                                            reply_markup=InlineKeyboardMarkup([[
                                                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                                            ]])
                                        )
                    
                    # If we get here, we couldn't automatically join
                    await edit_msg.edit(
                        "Force subscription detected but couldn't automatically join. Please send the channel link to join manually.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                        ]])
                    )
                    # Continue waiting for new messages in case there's more info
            
            await asyncio.sleep(1)
        
        # Check if batch was cancelled
        if user_id_str not in batchx:
            await edit_msg.edit("❌ Process cancelled")
            return []
        
        if not messages:
            await edit_msg.edit(
                "No response received from bot. Moving on...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                ]])
            )
        else:
            await edit_msg.edit(
                f"Received {len(messages)} messages from bot. Processing...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                ]])
            )
            
        return messages
    
    except Exception as e:
        logger.error(f"Error starting bot {bot_username}: {e}")
        await edit_msg.edit(
            f"Error interacting with bot: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
            ]])
        )
        return []
    finally:
        # Ensure user is removed from batch set if there was an exception
        if user_id_str in batchx:
            batchx.remove(user_id_str)

async def process_bot_messages(userbot, bot, sender_id, edit_msg, messages, target_chat_id=None):
    user_id_str = f'{sender_id}'
    
    # Add user to active batch set
    if user_id_str not in batchx:
        batchx.add(user_id_str)
    
    try:
        if not messages:
            await edit_msg.edit(
                "No messages to process from bot.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                ]])
            )
            return
        
        # Check if batch was cancelled
        if user_id_str not in batchx:
            await edit_msg.edit("❌ Process cancelled")
            return
        
        if target_chat_id is None:
            from main.plugins.db import db
            target_chat_id = db.get_chat_id(sender_id)
            
        topic_id = None
        if isinstance(target_chat_id, str) and '/' in target_chat_id:
            target_chat_id, topic_id = map(int, target_chat_id.split('/', 1))
        
        # Send initial message with bot details and pin it
        bot_username = messages[0].from_user.username if messages[0].from_user else "Unknown"
        initial_msg = await bot.send_message(
            target_chat_id,
            f"🤖 **Bot Batch Process Started**\n\n"
            f"**Bot:** @{bot_username}\n"
            f"**Total Messages:** {len(messages)}\n"
            f"**Message ID:** {messages[0].id}\n"
            f"**Status:** Processing...\n\n"
            f"_This message will be updated as processing continues._",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
            ]]),
            reply_to_message_id=topic_id
        )
        
        # Pin the initial message
        pin_result = await safe_pin_message(bot, target_chat_id, initial_msg.id)
        
        # Try to delete service message if pin_result exists and has a message_id
        try:
            if pin_result and hasattr(pin_result, 'message_id'):
                await bot.delete_messages(target_chat_id, pin_result.message_id)
            elif pin_result and isinstance(pin_result, int):
                await bot.delete_messages(target_chat_id, pin_result)
        except Exception as e:
            logger.warning(f"Could not delete pin service message: {e}")
            
        for i, msg in enumerate(messages):
            # Check if batch was cancelled
            if user_id_str not in batchx:
                await edit_msg.edit("❌ Process cancelled")
                await initial_msg.edit(
                    f"❌ **Bot Batch Process Cancelled**\n\n"
                    f"**Bot:** @{bot_username}\n"
                    f"**Total Messages:** {len(messages)}\n"
                    f"**Processed Messages:** {i}/{len(messages)}\n"
                    f"**Status:** Cancelled by user"
                )
                return
                
            await edit_msg.edit(
                f"Processing message {i+1}/{len(messages)} from bot...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                ]])
            )
            
            # Update the pinned initial message with current progress
            await initial_msg.edit(
                f"🤖 **Bot Batch Process Started**\n\n"
                f"**Bot:** @{bot_username}\n"
                f"**Total Messages:** {len(messages)}\n"
                f"**Message ID:** {messages[0].id}\n"
                f"**Current Progress:** {i+1}/{len(messages)}\n"
                f"**Status:** Processing message {i+1}...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                ]])
            )
            
            if not (msg.text or msg.media or msg.document or msg.photo or msg.video or msg.audio):
                continue
                
            if msg.media:
                from main.plugins.pyroplug import upload_media, get_media_filename
                
                await edit_msg.edit(
                    f"Downloading file from message {i+1}/{len(messages)}...",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
                    ]])
                )
                
                # Check if batch was cancelled
                if user_id_str not in batchx:
                    await edit_msg.edit("❌ Process cancelled")
                    await initial_msg.edit(
                        f"❌ **Bot Batch Process Cancelled**\n\n"
                        f"**Bot:** @{bot_username}\n"
                        f"**Total Messages:** {len(messages)}\n"
                        f"**Processed Messages:** {i}/{len(messages)}\n"
                        f"**Status:** Cancelled by user"
                    )
                    return
                
                file = await userbot.download_media(
                    msg,
                    progress=progress_for_pyrogram,
                    progress_args=(
                        bot,
                        "**__Unrestricting__(Downloading): __[Team Voice](https://t.me/officialharsh_g)__**\n ",
                        edit_msg,
                        time.time()
                    )
                )
                
                if file:
                    caption = msg.caption if msg.caption else ""
                    await upload_media(bot, sender_id, target_chat_id, file, caption, edit_msg, topic_id)
                    
            elif msg.text:
                await bot.send_message(
                    target_chat_id,
                    msg.text.markdown if hasattr(msg.text, "markdown") else msg.text,
                    reply_to_message_id=topic_id
                )
        
        # Update final status on the pinned message and remove cancel button
        await initial_msg.edit(
            f"✅ **Bot Batch Process Completed**\n\n"
            f"**Bot:** @{bot_username}\n"
            f"**Total Messages:** {len(messages)}\n"
            f"**Message ID:** {messages[0].id}\n"
            f"**Status:** All messages processed successfully!"
        )
                
        await edit_msg.edit("All bot messages processed successfully!")
        await asyncio.sleep(2)
        await edit_msg.delete()
        
    except Exception as e:
        logger.error(f"Error processing bot messages: {e}")
        await edit_msg.edit(
            f"Error processing bot messages: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancelx")
            ]])
        )
    finally:
        # Ensure user is removed from batch set
        if user_id_str in batchx:
            batchx.remove(user_id_str)

# Define global variables for batch tracking
batchx = set()
idsx = []

@Bot.on_callback_query(filters.regex("^cancelx$"))
async def cancel_callback(client, callback_query):
    user_id = callback_query.from_user.id
    user_id_str = f'{user_id}'
    
    if user_id_str not in batchx:
        await callback_query.answer("❌ No active batch to cancel!", show_alert=True)
        return
    
    if idsx:
        idsx.clear()
    if user_id_str in batchx:
        batchx.remove(user_id_str)
    
    await callback_query.answer("✅ Batch cancelled successfully!", show_alert=True)
    await callback_query.edit_message_text("❌ **Batch process cancelled!**")
