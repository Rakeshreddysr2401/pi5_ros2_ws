"""Tool set exports — each agent node binds only the tools it needs."""

from .household import forget, remember, update_list
from .look import look
from .memory import recall_memory
from .movement import (move_robot, navigate_to_pose, navigate_to_visible_object,
                       point_camera, save_location)
from .music import (pause_music, play_music, resume_music, set_music_volume,
                    stop_music)
from .reminders import cancel_reminder, list_reminders, set_reminder
from .announce import announce_at_home
from .knowledge import KNOWLEDGE_TOOLS
from .system import get_current_time, get_robot_status, ros2_publish, set_active_order
from .watch import watch_home
from .handover import handover
from ..services.mcp import load_provider_tools
from .telegram import TELEGRAM_TOOLS
from .web import WEB_TOOLS

# Remote MCP tool sets — [] when the provider has no token / is unreachable
# (services/mcp.py degrades; the owning agent swaps to its unavailable note).
SWIGGY_FOOD_MCP_TOOLS = load_provider_tools("swiggy_food")
SWIGGY_INSTAMART_MCP_TOOLS = load_provider_tools("swiggy_instamart")
SWIGGY_DINEOUT_MCP_TOOLS = load_provider_tools("swiggy_dineout")

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
                      point_camera, save_location, handover] + TELEGRAM_TOOLS
STATUS_TOOLS       = [get_robot_status, ros2_publish, handover]
SUPERVISOR_TOOLS   = [handover]
KNOWLEDGE_AGENT_TOOLS = KNOWLEDGE_TOOLS + [handover]
BRIEFING_TOOLS     = [list_reminders, get_current_time, recall_memory,
                      handover] + WEB_TOOLS
SWIGGY_TOOLS       = [set_active_order, handover] + SWIGGY_FOOD_MCP_TOOLS
INSTAMART_TOOLS    = [set_active_order, handover] + SWIGGY_INSTAMART_MCP_TOOLS
# Reservations aren't deliveries — dineout gets no set_active_order.
DINEOUT_TOOLS      = [handover] + SWIGGY_DINEOUT_MCP_TOOLS
# Tracker follows food AND grocery deliveries — the [SYSTEM] order poll in
# agent_node is provider-agnostic, both arrive at the door. It gets only the
# read-only tracking tools: food and instamart share ordering tool NAMES
# (get_addresses, confirm_order, get_payment_options, …), so splicing both
# full sets would put ambiguous duplicates in one ToolNode. The tracking
# subsets below don't collide, and ordering stays with the owning agent.
_TRACKING_TOOL_NAMES = {
    "get_food_orders", "get_food_order_details",           # food
    "track_food_order", "get_food_delivery_status",
    "get_orders", "track_order", "get_delivery_status",    # instamart
}
TRACKER_TOOLS      = [set_active_order, navigate_to_pose, handover] + [
                      t for t in SWIGGY_FOOD_MCP_TOOLS + SWIGGY_INSTAMART_MCP_TOOLS
                      if t.name in _TRACKING_TOOL_NAMES] + TELEGRAM_TOOLS
