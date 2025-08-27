from pyrogram.errors import FloodWait, InviteHashInvalid, InviteHashExpired, UserAlreadyParticipant, InviteRequestSent
from pyrogram import errors
from pyrogram.raw import functions, types
from main.plugins.db import db

import asyncio, subprocess, re, os, time
from pathlib import Path
from datetime import datetime as dt
import math
import cv2

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

#to get width, height and duration(in sec) of a video
def video_metadata(file):
    vcap = cv2.VideoCapture(f'{file}')
    width = round(vcap.get(cv2.CAP_PROP_FRAME_WIDTH ))
    height = round(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT ))
    fps = vcap.get(cv2.CAP_PROP_FPS)
    frame_count = vcap.get(cv2.CAP_PROP_FRAME_COUNT)
    duration = round(frame_count / fps)
    return {'width' : width, 'height' : height, 'duration' : duration }

#Join private chat-------------------------------------------------------------------------------------------------------------

async def join(client, link):
    if "addlist" in link:
        try:
            folder_hash = link.replace("https://t.me/addlist/", "")
            
            # Check folder invite status
            result = await client.invoke(functions.chatlists.CheckChatlistInvite(
                slug=folder_hash
            ))
            
            if isinstance(result, types.chatlists.ChatlistInviteAlready):
                return "Folder is already in your chat lists."
                
            # Get chat suggestions from the folder invite
            suggested_peer_ids = []
            if isinstance(result, types.chatlists.ChatlistInvite) and hasattr(result, 'peers') and result.peers:
                # Extract peer IDs from the suggested peers
                for peer in result.peers:
                    if isinstance(peer, types.PeerChat):
                        suggested_peer_ids.append(await client.resolve_peer(f"-{peer.chat_id}"))
                    elif isinstance(peer, types.PeerChannel):
                        suggested_peer_ids.append(await client.resolve_peer(f"-100{peer.channel_id}"))
                    # Add other peer types as needed
            
            # If no peers found or extraction failed, try to get some from dialogs
            if not suggested_peer_ids:
                dialogs = await client.get_dialogs(limit=15)
                for dialog in dialogs:
                    if not dialog.chat.is_private:
                        try:
                            peer = await client.resolve_peer(dialog.chat.id)
                            suggested_peer_ids.append(peer)
                            if len(suggested_peer_ids) >= 5:
                                break
                        except Exception:
                            continue
            
            # Make sure we have at least one peer
            if not suggested_peer_ids:
                return "Couldn't find any suitable chats to include in the folder."
                
            # Get current folders to check limits
            dialog_filters = await client.invoke(functions.messages.GetDialogFilters())
            current_folders = [f for f in dialog_filters.filters if isinstance(f, types.DialogFilter)]
            
            # Check if we need to remove any folders
            removed_msg = ""
            if len(current_folders) >= 2:  # Adjust limit as needed
                folder_to_remove = None
                for f in current_folders:
                    if not hasattr(f, 'is_default') or not f.is_default():
                        folder_to_remove = f.id
                        break
                
                if folder_to_remove:
                    await client.invoke(functions.messages.UpdateDialogFilter(
                        id=folder_to_remove,
                        filter=None
                    ))
                    removed_msg = f"Removed folder ID {folder_to_remove} to make space. "
                else:
                    return "Maximum folder limit reached and no removable folders found."

            # Join the folder with the peer IDs
            join_result = await client.invoke(functions.chatlists.JoinChatlistInvite(
                slug=folder_hash,
                peers=suggested_peer_ids
            ))

            return f"{removed_msg}Successfully joined the folder."

        except Exception as e:
            return f"Folder join failed: {str(e)}"
    else:
        # Your existing code for joining chats
        try:
            await client.join_chat(link)
            return "Successfully joined the channel/group"
        except errors.UserAlreadyParticipant:
            return "Already a member of this chat"
        except errors.InviteHashInvalid:
            return "Invalid or expired invite link"
        except errors.InviteHashExpired:
            return "This invite link has expired"
        except errors.InviteRequestSent:
            return "Join request sent - waiting for approval"
        except errors.FloodWait as e:
            return f"Flood wait: Try again in {e.value} seconds"
        except Exception as e:
            return f"Join failed: {str(e)}"

#------------------------------
def TimeFormatter(milliseconds) -> str:
    milliseconds = int(milliseconds) * 1000
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = (
        (f"{str(days)}d, " if days else "")
        + (f"{str(hours)}h, " if hours else "")
        + (f"{str(minutes)}m, " if minutes else "")
        + (f"{str(seconds)}s, " if seconds else "")
        + (f"{str(milliseconds)}ms, " if milliseconds else "")
    )
    return tmp[:-2]

#--------------------------------------------
def humanbytes(size):
    size = int(size)
    if not size:
        return ""
    power = 2**10
    n = 0
    Dic_powerN = {0: ' ', 1: 'Ki', 2: 'Mi', 3: 'Gi', 4: 'Ti'}
    while size > power:
        size /= power
        n += 1
    return f"{str(round(size, 2))} {Dic_powerN[n]}B"


#Regex---------------------------------------------------------------------------------------------------------------
#to get the url from event

def get_link(string):
    if '?start=' in string and ('t.me/' in string or 'telegram.me/' in string):
        return string.strip()
    regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
    url = re.findall(regex,string)
    try:
        return link if (link := [x[0] for x in url][0]) else False
    except Exception:
        return False
    
#Screenshot---------------------------------------------------------------------------------------------------------------

def hhmmss(seconds):
    return time.strftime('%H:%M:%S',time.gmtime(seconds))

async def screenshot(video, duration, sender):
    """Generate a thumbnail from a video with optional watermark"""
    time_stamp = hhmmss(int(duration)/2)
    out = dt.now().isoformat("_", "seconds") + ".jpg"
    watermark_text = db.get_watermark_text(sender)
    
    if watermark_text:
        # Escape single quotes for ffmpeg
        watermark_text_escaped = watermark_text.replace("'", "'\\''")
        cmd = [
            "ffmpeg",
            "-ss", time_stamp,
            "-i", video,
            "-vf", f"drawtext=text='{watermark_text_escaped}':fontcolor=0xD2691E:fontsize=90:x=(w-text_w)/2:y=h-text_h-50:font=Arial Bold",
            "-frames:v", "1",
            out,
            "-y"
        ]
    else:
        cmd = [
            "ffmpeg",
            "-ss", time_stamp,
            "-i", video,
            "-frames:v", "1",
            out,
            "-y"
        ]
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    x = stderr.decode().strip()
    y = stdout.decode().strip()
    if os.path.isfile(out):
        return out
    else:
        return None
