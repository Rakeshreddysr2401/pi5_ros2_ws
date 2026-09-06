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
from .approach import (approach_described_object, list_saved_locations,
                       scan_surroundings)
from .movement import (PAN_TILT_ENABLED, move_robot, navigate_to_pose,
                       point_camera, save_location)
from .system import get_current_time, get_robot_status
from .handover import handover
from .telegram import TELEGRAM_TOOLS
from .web import WEB_TOOLS

# Bound only when there are servos to drive. The ESP32 firmware has three
# subscriptions and none of them are servos (see movement.PAN_TILT_ENABLED), so
# on this robot the tool can only ever refuse — and an always-refusing tool is
# still shipped as a schema on every turn and still tempts the model into
# calling it. Set LANGROBO_PAN_TILT=1 once a mount exists and it reappears for
# both agents at once.
HEAD_TOOLS = [point_camera] if PAN_TILT_ENABLED else []

# ── Per-agent tool sets ─────────────────────────────────────────────────────

# chat — the default responder. Answers anything that is not a camera question
# or a movement command, and hands over when it is.
CHAT_TOOLS = [get_current_time, get_robot_status, handover] + WEB_TOOLS + TELEGRAM_TOOLS

# local_agent — the only multimodal agent. look() puts the current camera
# frame into the conversation as an image; keep_images in its AgentSpec is
# what lets it still see that image on follow-up turns.
LOCAL_AGENT_TOOLS = [look, handover] + HEAD_TOOLS + TELEGRAM_TOOLS

# navigate — everything that moves the wheels.
NAVIGATE_TOOLS = [move_robot, navigate_to_pose, approach_described_object,
                  scan_surroundings, save_location, list_saved_locations,
                  handover] + HEAD_TOOLS + TELEGRAM_TOOLS
