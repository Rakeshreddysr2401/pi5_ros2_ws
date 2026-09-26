"""The one owner of the robot's speaker and mic.

Before this node, stt_node and tts_node each connected the Bluetooth speaker
themselves at startup — and raced: tts_node's profile switch landed ~90 ms
after stt_node's and re-created the PipeWire mic with a fresh volume, wiping
the gain stt_node had just set. After a reboot the speaker had to be paired and
connected by hand, and if it slept or wandered off nothing reconnected it.

Now nothing but this process touches bluetoothctl / pactl / wpctl. It:

* watches every Bluetooth audio device bluez knows (paired or trusted), so the
  owner can use the boAt Stone one day and a pair of headphones the next —
  whichever is switched on wins, `bt_devices` only says who to prefer;
* connects it (re-pairs if the link key was lost and we are allowed to),
  picks HFP when the device has a mic we want, makes it PipeWire's default
  sink + source, applies the mic gain — and re-does all of that on its own
  whenever the device drops and comes back;
* falls back to a wired device (`wired_fallback`) when no Bluetooth audio
  device is reachable, so voice still works with a USB headset plugged in;
* tells the voice nodes when the audio path is usable on /voice/audio_ready
  (latched Bool) and what it is on /voice/audio_device (latched JSON).

stt_node and tts_node open the `pipewire` device and follow /voice/audio_ready;
they never look at Bluetooth state. Everything here degrades: no bluetoothctl,
no device in range, pairing refused — it logs and keeps trying (CLAUDE.md #5).
"""

import json
import threading
import time
import traceback

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String

from . import bt_audio

# Late subscribers (a voice node restarted on its own) must see the current
# state, not wait for the next edge.
LATCHED = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
# Consecutive failed health checks before the device counts as gone.
LOST_AFTER_MISSES = 2
# Let bluez tear the old SCO link down before asking for a fresh one.
BOUNCE_SETTLE_S = 1.5


class AudioDeviceNode(Node):
    def __init__(self):
        super().__init__('pi5_audio_device')
        # Preferred order only — any other paired/trusted Bluetooth audio
        # device that is switched on is used when none of these is reachable.
        self.declare_parameter('bt_devices', [''])
        # Use the Bluetooth device's own mic (HFP) when it has one. False keeps
        # A2DP (better playback) and leaves the mic to the wired fallback.
        self.declare_parameter('bt_prefer_mic', True)
        # Software gain on the HFP mic; PipeWire resets it on every reconnect.
        # bt_mic_gain is the default; bt_mic_gains overrides it per device
        # ("MAC=gain") — a gain that suits one mic pins another at full scale.
        self.declare_parameter('bt_mic_gain', 1.0)
        self.declare_parameter('bt_mic_gains', [''])
        # Substring of a wired sink/source name to use when no Bluetooth
        # device is up ('' = none: stay not-ready until Bluetooth appears).
        self.declare_parameter('wired_fallback', '')
        self.declare_parameter('poll_period_s', 3.0)
        # A device that is off takes several seconds to fail a connect; don't
        # hammer it every poll.
        self.declare_parameter('connect_retry_s', 10.0)

        self._preferred = [m.strip().upper() for m in self.get_parameter('bt_devices').value
                           if m and bt_audio.is_mac(m)]
        self._prefer_mic = bool(self.get_parameter('bt_prefer_mic').value)
        self._mic_gain = float(self.get_parameter('bt_mic_gain').value)
        self._mic_gains = bt_audio.parse_gain_overrides(self.get_parameter('bt_mic_gains').value)
        self._wired = (self.get_parameter('wired_fallback').value or '').strip()
        self._poll_s = float(self.get_parameter('poll_period_s').value)
        self._retry_s = float(self.get_parameter('connect_retry_s').value)

        self._ready_pub = self.create_publisher(Bool, '/voice/audio_ready', LATCHED)
        self._device_pub = self.create_publisher(String, '/voice/audio_device', LATCHED)

        self._active: dict | None = None      # the device currently routed
        self._ready = False
        # bluetoothctl/wpctl can time out for one tick while whisper pegs the
        # CPU; one bad read must not close the mic and drop a reply in flight.
        self._misses = 0
        self._last_attempt: dict[str, float] = {}
        self._pair_warned = False
        self._publish_state()                  # latch "not ready" immediately

        self.get_logger().info(
            f'owning audio: prefer {self._preferred or "any paired device"}, '
            f'mic via bluetooth={self._prefer_mic}, gain={self._mic_gain:.2f} '
            f'(per device: {self._mic_gains or "none"}), '
            f'wired fallback={self._wired or "none"}')
        threading.Thread(target=self._loop, daemon=True).start()

    # ── Main loop (own thread: every step here shells out and may block) ──

    def _loop(self):
        while rclpy.ok():
            try:
                self._tick()
            except Exception:
                self.get_logger().error(f'audio device loop failed\n{traceback.format_exc()}')
            time.sleep(self._poll_s)

    def _tick(self):
        devices = [bt_audio.device_info(d['mac']) | {'name': d['name']}
                   for d in bt_audio.known_devices()]
        current = self._active['mac'] if self._active and self._active['kind'] == 'bt' else None
        ranked = bt_audio.rank_devices(devices, self._preferred, current)

        if self._active and self._active['kind'] == 'bt':
            still = next((d for d in ranked if d['mac'] == current), None)
            if still and still['connected'] and self._reassert_bt(self._active):
                self._misses = 0
                return                          # healthy: nothing to do
            self._misses += 1
            if self._misses < LOST_AFTER_MISSES:
                return                          # could be a slow tool, not a lost device
            self._lost(f"{self._active['name']} disconnected")

        for d in ranked:
            if not d['connected']:
                if time.monotonic() - self._last_attempt.get(d['mac'], 0.0) < self._retry_s:
                    continue
                self._last_attempt[d['mac']] = time.monotonic()
                if not self._connect(d):
                    continue
            routed = self._route_bt(d)
            if routed:
                self._activate(routed)
                return

        if self._active and self._active['kind'] == 'wired':
            if self._wired_nodes() is not None:
                return                          # wired path still there
            self._lost('wired device vanished')
        if self._wired and not self._active:
            wired = self._wired_nodes()
            if wired:
                self._activate(wired)

    # ── Bluetooth ─────────────────────────────────────────────────────────

    def _connect(self, d: dict) -> bool:
        if not d['paired']:
            # Trusted but the link key is gone (the Stone after a reboot,
            # earbuds left in pairing mode). Re-pair — verified to work
            # unattended 2026-09-20 (OnePlus Buds Z2). Only a refusal is
            # worth a line; an unreachable device is just switched off.
            ok, msg = bt_audio.bt_pair(d['mac'])
            if not ok:
                if 'AuthenticationFailed' in msg or 'NotAuthorized' in msg or 'Rejected' in msg:
                    if not self._pair_warned:
                        self._pair_warned = True
                        self.get_logger().warning(
                            f"{d['name']} refused re-pairing ({msg}) — put it in pairing "
                            f"mode and run `./scripts/bt_speaker.sh pair {d['mac']}`")
                else:
                    self.get_logger().debug(f"{d['name']}: {msg}")
                return False
            self.get_logger().info(f"re-paired {d['name']}")
        ok, msg = bt_audio.bt_connect(d['mac'])
        if ok:
            self.get_logger().info(f"connected {d['name']} ({d['mac']})")
        else:
            self.get_logger().debug(f"{d['name']}: {msg}")
        return ok

    def _route_bt(self, d: dict) -> dict | None:
        """Make a connected device PipeWire's default sink (+ source). Returns
        the routed description, or None when PipeWire never showed its nodes."""
        profile = bt_audio.profile_for(d, self._prefer_mic)
        if profile == 'hfp' and bt_audio.active_profile(d['mac']).startswith('headset'):
            # Already in HFP, so setting it again changes nothing — and that
            # is how a DEAD link survives: the SCO stream can go one-way
            # (source RUNNING, not muted, gain fine, and pure digital silence
            # out of the mic — seen live 2026-09-26 after a service restart).
            # Drop to A2DP and back so the link is rebuilt from scratch.
            bt_audio.set_profile(d['mac'], 'a2dp')
            time.sleep(BOUNCE_SETTLE_S)
        ok, msg = bt_audio.set_profile(d['mac'], profile)
        if not ok and profile == 'hfp':
            self.get_logger().warning(f"could not switch {d['name']} to HFP ({msg}); using A2DP")
            profile = 'a2dp'
        sink = bt_audio.await_bt_node('Sinks', d['mac'])
        if not sink:
            self.get_logger().warning(f"{d['name']} connected but PipeWire shows no sink yet")
            return None
        bt_audio.set_default(sink['id'])
        routed = {'kind': 'bt', 'mac': d['mac'], 'name': d['name'], 'profile': profile,
                  'sink': sink['name'], 'sink_id': sink['id'], 'source': None, 'source_id': None}
        if profile == 'hfp':
            source = bt_audio.await_bt_node('Sources', d['mac'])
            if source:
                self._apply_source(source, routed)
            else:
                self.get_logger().warning(f"{d['name']} is in HFP but PipeWire shows no mic")
        if routed['source'] is None and self._wired:
            # A2DP speaker (or HFP without a mic): the wired device is the mic.
            wired = bt_audio.find_node(bt_audio.wpctl_status(), 'Sources', self._wired)
            if wired:
                bt_audio.set_default(wired['id'])
                routed['source'], routed['source_id'] = wired['name'], wired['id']
                routed['mic'] = 'wired'
            else:
                self.get_logger().warning(
                    f"no mic: {d['name']} has none in {profile} and no wired source matches "
                    f"{self._wired!r}")
        return routed

    def _apply_source(self, source: dict, routed: dict) -> None:
        bt_audio.set_default(source['id'])
        routed['source'], routed['source_id'] = source['name'], source['id']
        gain = self._mic_gains.get((routed.get('mac') or '').upper(), self._mic_gain)
        routed['mic_gain'] = gain
        ok, msg = bt_audio.set_volume(source['id'], gain)
        if not ok:
            self.get_logger().warning(f'mic gain {gain:.2f} failed: {msg}')

    def _reassert_bt(self, active: dict) -> bool:
        """The device is still connected — but PipeWire may have re-created
        its nodes (profile bounce, brief link loss). Re-apply defaults + gain
        when the node ids changed; report False if the sink is gone."""
        status = bt_audio.wpctl_status()
        sink = bt_audio.find_bt_node(status, 'Sinks', active['mac'])
        if not sink:
            return False
        if sink['id'] != active['sink_id']:
            bt_audio.set_default(sink['id'])
            active['sink_id'] = sink['id']
            self.get_logger().info('sink re-created by PipeWire — default re-applied')
        if active['profile'] == 'hfp':
            source = bt_audio.find_bt_node(status, 'Sources', active['mac'])
            if source and source['id'] != active['source_id']:
                self._apply_source(source, active)
                self.get_logger().info('mic re-created by PipeWire — default + gain re-applied')
                self._publish_state()
        return True

    # ── Wired fallback ────────────────────────────────────────────────────

    def _wired_nodes(self) -> dict | None:
        status = bt_audio.wpctl_status()
        sink = bt_audio.find_node(status, 'Sinks', self._wired)
        source = bt_audio.find_node(status, 'Sources', self._wired)
        if not sink and not source:
            return None
        if sink and not sink['default']:
            bt_audio.set_default(sink['id'])
        if source and not source['default']:
            bt_audio.set_default(source['id'])
        return {'kind': 'wired', 'mac': None, 'name': self._wired, 'profile': None,
                'sink': sink['name'] if sink else None, 'sink_id': sink['id'] if sink else None,
                'source': source['name'] if source else None,
                'source_id': source['id'] if source else None}

    # ── State ─────────────────────────────────────────────────────────────

    def _activate(self, routed: dict) -> None:
        self._active = routed
        self._ready = True
        self.get_logger().info(
            f"audio ready: {routed['name']} [{routed['kind']}"
            f"{'/' + routed['profile'] if routed['profile'] else ''}] "
            f"sink={routed['sink']!r} source={routed['source']!r}")
        self._publish_state()

    def _lost(self, why: str) -> None:
        self.get_logger().warning(f'audio lost: {why} — searching')
        self._active = None
        self._ready = False
        self._misses = 0
        self._publish_state()

    def _publish_state(self) -> None:
        self._ready_pub.publish(Bool(data=self._ready))
        payload = {'ready': self._ready, **(self._active or {})}
        self._device_pub.publish(String(data=json.dumps(payload)))


def main():
    rclpy.init()
    node = AudioDeviceNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
