# servo — Feetech STS bus-servo bench tests

Bench tools for the Feetech STS serial bus servos on the SmartElex serial bus
servo board (USB, CH343 chip, `/dev/ttyACM0` on this Pi 5). They use pyserial only;
no ROS and nothing in `langrobo_core`. Not part of the robot's runtime.

**Never point them at `/dev/ttyUSB0` or `/dev/ttyUSB1`.** Those are the sonar
and wheels ESP32s, and `sts_test.py` refuses them by their by-path names.

| script | what it does | moves? |
|---|---|---|
| `sts_test.py PORT` | Finds the servo (baud 1M/500k/115k/57.6k/38.4k, IDs 0–20) and prints model, firmware, voltage, temperature, position, faults and link quality. Shared protocol code for the others. | no (`--move`: ±26°) |
| `sts_bench.py PORT [label]` | One BARE servo: health, link, accuracy, full 0–4095 sweep, repeatability (20 arrivals from both sides). | yes, full circle |
| `sts_range.py PORT [label]` | One servo WITH a bracket: first finds the two stops gently (25% torque, slow, backs off on contact), then tests inside that range only. Also safe on a bare servo. | yes, inside its free range |
| `sts_chain.py PORT` | Every servo on the chain: each one alone (the others hold still) inside its SAVED angle limits, then all together with sync write (±90°). Logs battery voltage. | yes |

Each prints `RESULT: PASS|FAIL` and exits 0/1, and always turns torque off at the end.

## Setting a new servo's ID

New servos all ship as **ID 1**. Two ID 1s on one cable answer at once and their
replies collide, which looks like "no servo answered". Set each new servo's ID
with it **alone** on the cable:

```python
from sts_test import Bus
b = Bus('/dev/ttyACM0', 1000000)
b.write(1, 55, [0])     # unlock EEPROM
b.write(1, 5, [4])      # new ID
b.write(4, 55, [1])     # lock again (now addressed by the NEW id)
```

The same unlock → write → lock applies to the angle limits (addresses 9 and 11)
and the centre offset (address 31; sign is bit 11).

## Current setup (2026-11-12)

| ID | Notes |
|---|---|
| 1 | U-bracket. Offset −1538 so straight = 2048; stops at −133.7° / +136°; saved limits 584..3538 (−128.7° .. +131°) |
| 2 | bare, full range |
| 3 | bare, full range |
| 4 | the fourth servo, not yet tested |

Power is a 3S 18650 pack (2000 mAh, ~12.2 V full-ish). It is fine for light motion;
one stalled servo (2.9 A) sagged it to 10.5 V, so it is weak for heavy loads.
