import math
import os
import time
import json
from main.plugins.helpers import TimeFormatter, humanbytes

# Enhanced progress indicators with gradient-like appearance
FINISHED_PROGRESS_STR = "🟢"  # Green circle for completed portions
UN_FINISHED_PROGRESS_STR = "◯"  # White circle for incomplete portions
DOWNLOAD_LOCATION = "/app"


async def progress_for_pyrogram(
    current,
    total,
    bot,
    ud_type,
    message,
    start
):
    now = time.time()
    diff = now - start
    if round(diff % 10.00) == 0 or current == total:
        percentage = current * 100 / total
        status = f"{DOWNLOAD_LOCATION}/status.json"
        if os.path.exists(status):
            with open(status, 'r+') as f:
                statusMsg = json.load(f)
                if not statusMsg["running"]:
                    bot.stop_transmission()
        speed = current / diff
        elapsed_time = round(diff) * 1
        time_to_completion = round((total - current) / speed) * 1
        estimated_total_time = elapsed_time + time_to_completion

        elapsed_time = TimeFormatter(milliseconds=elapsed_time)
        estimated_total_time = TimeFormatter(milliseconds=estimated_total_time)

        # Calculate percentage for display
        percent_str = f"{percentage:.1f}%"
        
        # Beautiful progress header with decorative elements
        progress = "━━━━━━━━━━━━━━━━━━━━━━━\n"
        progress += f"**✨ 𝐏𝐫𝐨𝐠𝐫𝐞𝐬𝐬 𝐒𝐭𝐚𝐭𝐮𝐬 ✨** `{percent_str}`\n\n"
        
        # Enhanced progress bar
        progress += "**「{0}{1}」**\n".format(
            ''.join(                
                    FINISHED_PROGRESS_STR
                    for _ in range(math.floor(percentage / 10))                
            ),
            ''.join(                
                    UN_FINISHED_PROGRESS_STR
                    for _ in range(10 - math.floor(percentage / 10))               
            )
        )
        
        # Status details with fancy formatting
        tmp = progress + "\n"
        tmp += "**📦 __𝐂𝐨𝐦𝐩𝐥𝐞𝐭𝐞𝐝__:** `{0}` / `{1}`\n".format(
            humanbytes(current),
            humanbytes(total)
        )
        tmp += "**🚀 __𝐒𝐩𝐞𝐞𝐝__:** `{0}/s`\n".format(
            humanbytes(speed)
        )
        tmp += "**⏳ __𝐓𝐢𝐦𝐞 𝐋𝐞𝐟𝐭__:** `{0}`\n".format(
            estimated_total_time if estimated_total_time != '' else "0 s"
        )
        tmp += "━━━━━━━━━━━━━━━━━━━━━━━"
        
        try:
            # Format the message with decorative elements
            header = f"**🔄 {ud_type.upper()} 🔄**\n"
            text = f"{header}{tmp}"
            
            if message.text != text or message.caption != text:
                if not message.photo:
                    await message.edit_text(text=text)
                else:
                    await message.edit_caption(caption=text)
        except:
            pass
