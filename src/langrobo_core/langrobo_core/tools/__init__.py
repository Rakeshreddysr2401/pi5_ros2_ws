"""Tool set exports — each agent node binds only the tools it needs.

Keep these lists short. Every tool in a set is rendered into that agent's
system prompt (prompts.render_tools) and shipped as a JSON schema on every
single request to that agent, so an unused tool costs prompt tokens and
decode-time grammar on every turn, forever.

Speech has a single channel: each agent's reply text IS the speech, published
to TTS once by agent_node after the turn. There is deliberately no speak()
tool — see CLAUDE.md rule 3.
"""

from .look import look
from .locate import locate_object
from .photo_recall import ask_photos
from .approach import (approach_described_object, list_saved_locations,
                       scan_surroundings)
from .movement import (PAN_TILT_ENABLED, move_robot, navigate_to_pose,
                       point_camera, save_location)
from .system import get_current_time, get_robot_status
from .audio import AUDIO_TOOLS
from .reminders import REMINDER_TOOLS
from .lists import LIST_TOOLS
from .memory import MEMORY_TOOLS
from .handover import handover
from .telegram import TELEGRAM_TOOLS, send_telegram_photo
from .web import WEB_TOOLS
from .follow import FOLLOW_ENABLED, follow_person

# Bound only when there are servos to drive. The ESP32 firmware has three
# subscriptions and none of them are servos (see movement.PAN_TILT_ENABLED), so
# on this robot the tool can only ever refuse — and an always-refusing tool is
# still shipped as a schema on every turn and still tempts the model into
# calling it. Set LANGROBO_PAN_TILT=1 once a mount exists and it reappears for
# both agents at once.
HEAD_TOOLS = [point_camera] if PAN_TILT_ENABLED else []

# "follow me" / "come to me" (tools/follow.py, Jetson follow_node). Off by
# default (LANGROBO_FOLLOW=1): with it off the schema is not shipped and every
# prompt stays byte-identical, so nothing changes for the KV cache or the model.
FOLLOW_TOOLS = [follow_person] if FOLLOW_ENABLED else []

# ── Per-agent tool sets ─────────────────────────────────────────────────────

# chat — the default responder. Answers anything that is not a camera question
# or a movement command, and hands over when it is.
# ask_photos: "where did you see my bag?" answered here, from the photo log,
# with no handover (tools/photo_recall.py).
# AUDIO_TOOLS: volume, Bluetooth device, music (tools/audio.py, owner 2026-10-04).
# REMINDER_TOOLS: timers, alarms, reminders -- agent_node rings them (tools/reminders.py).
# LIST_TOOLS: shopping / to-do / named lists (tools/lists.py).
# MEMORY_TOOLS: facts people TELL the robot, across reboots (tools/memory.py).
CHAT_TOOLS = ([get_current_time, get_robot_status, ask_photos, handover] + WEB_TOOLS
              + TELEGRAM_TOOLS + AUDIO_TOOLS + REMINDER_TOOLS + LIST_TOOLS + MEMORY_TOOLS)

# local_agent — the only multimodal agent. look() puts the current camera
# frame into the conversation as an image; keep_images in its AgentSpec is
# what lets it still see that image on follow-up turns.
# locate_object() earns its schema cost: without it this agent has RGB
# pixels and nothing else, so every "how far is that?" is answered by
# invention. It is read-only and never turns the robot -- driving to a
# thing is navigate's job (approach_described_object).
# ask_photos: something seen EARLIER, from every photo of the session, read-only.
LOCAL_AGENT_TOOLS = [look, locate_object, ask_photos, handover] + HEAD_TOOLS + TELEGRAM_TOOLS

# navigate — everything that moves the wheels.
# get_robot_status: "where are you now?" / "did you get there?" right after a
# drive -- the follow-up stays here (sticky), and without it the model guessed
# from its own earlier reply and sent a STOP to "check" (2026-10-04).
# Telegram: the photo only. "Go to the bag and send me a pic" needs it here --
# called mid-drive, it holds the photo until arrival (telegram.py). Messages
# are chat's job: navigate never sent one in 3 weeks of logs, and the schema
# cost ~150 tokens on every move (2026-10-02).
NAVIGATE_TOOLS = ([move_robot, navigate_to_pose, approach_described_object,
                  scan_surroundings, save_location, list_saved_locations,
                  get_robot_status, ask_photos, handover] + HEAD_TOOLS + FOLLOW_TOOLS
                 + [send_telegram_photo])
