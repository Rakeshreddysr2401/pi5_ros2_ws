"""Tool set exports — each agent node binds only the tools it needs."""

from .household import forget, remember, update_list
from .look import look
from .memory import recall_memory
from .movement import (move_robot, navigate_to_pose,
                       navigate_to_visible_object, save_location)
from .music import (pause_music, play_music, resume_music, set_music_volume,
                    stop_music)
from .reminders import cancel_reminder, list_reminders, set_reminder
from .announce import announce_at_home
from .knowledge import KNOWLEDGE_TOOLS
from .system import get_current_time, get_robot_status, ros2_publish, set_active_order
from .watch import watch_home
from .handover import handover
from .swiggy_mcp import SWIGGY_FOOD_TOOLS
from .telegram import TELEGRAM_TOOLS
from .web import WEB_TOOLS

# Per-agent tool sets
# Speech has a single channel: each agent's final reply text is published to TTS
# once by agent_node after the turn. There is no speak() tool — that removes the
# double-speak where a live speak() filler and the final answer both reached TTS.
# During slow tools the /brain/thinking Bool signals a non-verbal "thinking" cue.
#
# Visual Q&A is handled by look() (camera frame → Gemma multimodal). The old
# query_vision() (Jetson Moondream) was retired — no local VLM fits the 8GB Jetson.
CHAT_TOOLS         = [get_current_time, get_robot_status, set_reminder,
                      list_reminders, cancel_reminder, update_list, remember,
                      forget, recall_memory, play_music, stop_music,
                      pause_music, resume_music, set_music_volume,
                      watch_home, announce_at_home,
                      handover] + WEB_TOOLS + TELEGRAM_TOOLS
LOCAL_AGENT_TOOLS  = [look, handover] + TELEGRAM_TOOLS
NAVIGATE_TOOLS     = [move_robot, navigate_to_pose, navigate_to_visible_object,
                      save_location, handover] + TELEGRAM_TOOLS
STATUS_TOOLS       = [get_robot_status, ros2_publish, handover]
SUPERVISOR_TOOLS   = [handover]
KNOWLEDGE_AGENT_TOOLS = KNOWLEDGE_TOOLS + [handover]
BRIEFING_TOOLS     = [list_reminders, get_current_time, recall_memory,
                      handover] + WEB_TOOLS
SWIGGY_TOOLS       = [set_active_order, handover] + SWIGGY_FOOD_TOOLS
TRACKER_TOOLS      = [set_active_order, navigate_to_pose, handover
                      ] + SWIGGY_FOOD_TOOLS + TELEGRAM_TOOLS
