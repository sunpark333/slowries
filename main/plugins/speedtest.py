from time import time
from speedtest import Speedtest
import math
from main.__main__ import botStartTime
from pyrogram import filters
from .. import Bot
from config import AUTH

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

def get_readable_time(seconds: int) -> str:
    result = ''
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f'{days}d'
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f'{hours}h'
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f'{minutes}m'
    seconds = int(seconds)
    result += f'{seconds}s'
    return result

def get_readable_file_size(size_in_bytes) -> str:
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024:
        size_in_bytes /= 1024
        index += 1
    try:
        return f'{round(size_in_bytes, 2)}{SIZE_UNITS[index]}'
    except IndexError:
        return 'File too large'

def speed_convert(size, byte=True):
    if not byte: size = size / 8
    power = 2 ** 10
    zero = 0
    units = {0: "B/s", 1: "KB/s", 2: "MB/s", 3: "GB/s", 4: "TB/s"}
    while size > power:
        size /= power
        zero += 1
    return f"{round(size, 2)} {units[zero]}"

@Bot.on_message(filters.command("speedtest") & filters.user(AUTH))
async def speedtest_cmd(client, message):
    speed = await message.reply_text("Running Speed Test. Wait about some secs.")
    
    try:
        test = Speedtest()
        test.get_best_server()
        test.download()
        test.upload()
        test.results.share()
        result = test.results.dict()
        path = result['share']
        
        currentTime = get_readable_time(time() - botStartTime)
        string_speed = f'''
╭─《 🚀 SPEEDTEST INFO 》
├ **Upload:** `{speed_convert(result['upload'], False)}`
├ **Download:** `{speed_convert(result['download'], False)}`
├ **Ping:** `{result['ping']} ms`
├ **Time:** `{result['timestamp']}`
├ **Data Sent:** `{get_readable_file_size(int(result['bytes_sent']))}`
╰ **Data Received:** `{get_readable_file_size(int(result['bytes_received']))}`

╭─《 🌐 SPEEDTEST SERVER 》
├ **Name:** `{result['server']['name']}`
├ **Country:** `{result['server']['country']}, {result['server']['cc']}`
├ **Sponsor:** `{result['server']['sponsor']}`
├ **Latency:** `{result['server']['latency']}`
├ **Latitude:** `{result['server']['lat']}`
╰ **Longitude:** `{result['server']['lon']}`

╭─《 👤 CLIENT DETAILS 》
├ **IP Address:** `{result['client']['ip']}`
├ **Latitude:** `{result['client']['lat']}`
├ **Longitude:** `{result['client']['lon']}`
├ **Country:** `{result['client']['country']}`
├ **ISP:** `{result['client']['isp']}`
╰ **ISP Rating:** `{result['client']['isprating']}`
'''
        try:
            await message.reply_photo(
                photo=path,
                caption=string_speed
            )
        except Exception:
            await message.reply_text(
                text=string_speed
            )
    except Exception as e:
        await message.reply_text(f"An error occurred: {str(e)}")
    
    finally:
        await speed.delete()
