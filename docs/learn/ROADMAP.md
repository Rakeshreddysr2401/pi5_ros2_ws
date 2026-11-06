# Robotics + RL learning roadmap — taught from this rover, aimed at interviews

Written 2026-11-06 for the owner: *"make a syllabus / roadmap for complete robotics, interviews, RL."*

> **One file, two repos:** Jetson `~/rover/docs/learn/ROADMAP.md` and Pi 5
> `~/ros2_ws/docs/learn/ROADMAP.md` hold the same text (like KINO_SYSTEM.md). Paths such as
> `phase3/...` or `RL_PLAN.md` are in the **Jetson's `~/rover`**; Pi 5 paths start with `~/ros2_ws`.
> Change it in one, copy it to the other.

**The idea:** every topic is something this robot already does. Learn the theory, then open the
file that does it, then run the command that shows it live. In an interview, "I built and
debugged this on my own robot, here is the number I measured" beats any textbook answer.

**Pace assumed:** ~10 hours a week → about **24 weeks**. Faster or slower is fine; keep the order.
Each module ends with a **checkpoint**: if you can do it without notes, move on.

---

## The map

| # | module | weeks | your rover |
|---|---|---|---|
| 0 | Foundations: math, Python, Linux, git | 2 | the whole repo |
| 1 | ROS 2 | 2 | `./rover`, every node |
| 2 | Frames, transforms, kinematics | 1.5 | TF tree, `goal_exec.py`, `motion.py` |
| 3 | Sensors and calibration | 1.5 | D555, C1 LiDAR, gyro, encoders, sonars |
| 4 | State estimation and sensor fusion | 2.5 | `fusion2.py` |
| 5 | SLAM and mapping | 2 | slam_toolbox, cuVSLAM, nvblox |
| 6 | Path planning | 2 | Smac lattice, `tight_route.py`, `house_route.py` |
| 7 | Control | 1.5 | ESP32 PID, MPPI, `goal_exec.py` |
| 8 | Navigation systems (nav2, behaviour trees, recovery) | 1 | `reach_node.py`, `phase3/bt/` |
| 9 | Perception and deep learning | 2 | YOLO, `pixel_to_goal.py`, TensorRT |
| 10 | **Reinforcement learning** | 3 | `RL_PLAN.md` |
| 11 | Simulation: Isaac Sim and Isaac Lab | 1.5 | `~/kino_sim` |
| 12 | Robot AI: LLM agents, VLMs, VLAs | 1 | the Pi 5 brain |
| 13 | Systems: embedded, Docker, networks, GPU | 1 | ESP32, micro-ROS, Jetson, DDS |
| 14 | Interview preparation | ongoing, 2 focused | all of the above |

---

## 0. Foundations (2 weeks)

**Learn**
- Linear algebra: vectors, matrices, rotation matrices, eigenvalues, least squares
- Probability: Bayes' rule, Gaussian distributions, covariance, expectation
- Calculus: derivatives, gradients, chain rule (needed for neural networks and optimisation)
- Python + numpy (vectorised code, no loops), Linux shell, git (branch, commit, merge, log)

**Resources**
- 3Blue1Brown, *Essence of Linear Algebra* and *Essence of Calculus* (YouTube, free)
- *Mathematics for Machine Learning*, Deisenroth, Faisal, Ong (free PDF)

**On your rover**
- Read `phase3/nodes/goal_exec.py` lines 1–90: rotation matrices `R(a)`, solving `P = (I - R(a))^-1 d`
- `git log --oneline | head -30` in `~/rover`: read how commits explain *why*

**Checkpoint:** derive the 2D rotation matrix; explain why `P = (I − R(a))⁻¹ d` gives the pivot point.

---

## 1. ROS 2 (2 weeks)

**Learn**
- Nodes, topics, services, actions, parameters, launch files
- Messages: `Twist`, `Odometry`, `LaserScan`, `PoseStamped`, `OccupancyGrid`
- QoS (reliable vs best-effort, durability), DDS, domains (why the twin is domain 42)
- TF2: the transform tree (`map → odom → base_link → laser / camera`)

**Resources**
- docs.ros.org: the ROS 2 beginner tutorials (CLI tools, then client libraries)
- `~/rover/ARCHITECTURE.md`: your topics, frames and nodes

**On your rover**
```bash
./rover status            # what is alive
docker exec -it rover bash
ros2 node list; ros2 topic list; ros2 topic hz /scan; ros2 topic echo /odom --once
ros2 run tf2_tools view_frames      # draws your TF tree
```
- Read `phase1/nodes/gyro_node.py`: a small, complete node

**Checkpoint:** write a node that subscribes to `/scan` and publishes the closest obstacle distance at 10 Hz; explain why `/scan` uses best-effort QoS.

---

## 2. Frames, transforms, kinematics (1.5 weeks)

**Learn**
- Homogeneous transforms, composing frames, quaternions vs Euler angles (and gimbal lock)
- Differential drive and **skid-steer** kinematics; why skid-steer slides in turns
- Holonomic vs non-holonomic robots (omni/mecanum wheels)

**Resources**
- *Modern Robotics*, Lynch & Park — chapters 2–3, 13 (free PDF + Coursera course)

**On your rover**
- `~/kino_sim/hardware/motion.py`: your measured motion model (deadband, slide, lag)
- `ROVER_BUILD_PLAN.md` §0, §6: why it slides 6–33 cm per 90° and the physics of it
- `description/params.yaml`: every measured dimension

**Checkpoint:** from wheel speeds (vl, vr) compute (v, ω); explain why your rover turns about a pivot, not its centre, and how goal_exec compensates.

---

## 3. Sensors and calibration (1.5 weeks)

**Learn**
- LiDAR (time of flight, triangulation), stereo depth (baseline, disparity, depth error grows with distance²)
- IMU: gyro bias and drift; wheel encoders: counts → distance, slip
- Ultrasonic: cone width, soft surfaces, floor echoes
- Intrinsic vs extrinsic calibration

**On your rover**
- `lidar/README.md`, `./rover lidar --calibrate` (LiDAR yaw), `./rover camera --floor` / `--lidar`
- `./rover values` — every sensor's reading in one place
- `phase3/nodes/sonar_witness.py` — rules for trusting a sonar
- Your D555 story: jumbo frames over PoE (memory note; `WAVESHARE_SWITCH_TEST.md`)

**Checkpoint:** explain why depth error grows with distance; name three ways each sensor lies and how this rover catches each.

---

## 4. State estimation and sensor fusion (2.5 weeks) — a favourite interview topic

**Learn**
- Bayes filter → Kalman filter → EKF / UKF → particle filter
- Process vs measurement noise, covariance, innovation, outlier gating (Mahalanobis distance)
- Odometry drift; why fuse; complementary filter

**Resources**
- *Kalman and Bayesian Filters in Python*, Roger Labbe (free Jupyter book) — do the notebooks
- *Probabilistic Robotics*, Thrun, Burgard, Fox — chapters 2–4, 8

**On your rover**
- `phase1/nodes/fusion2.py`: LiDAR odometry + gyro + VO + wheels, each **gated**
- `LOCALIZATION.md` (the numbers), `LOCALIZATION_GAPS.md`
- `./rover grade` — scoring each estimate against LiDAR truth

**Checkpoint:** implement a 1D Kalman filter from scratch; explain what fusion2 does when cuVSLAM loses tracking (it drops its fixes by the gate).

---

## 5. SLAM and mapping (2 weeks)

**Learn**
- Scan matching (ICP, correlative), pose graphs, loop closure
- Occupancy grids (log-odds), TSDF / ESDF 3D maps
- Visual odometry: features, stereo, bundle adjustment basics

**Resources**
- Cyrill Stachniss, *SLAM / Mobile Sensing and Robotics* lectures (YouTube, free)
- *Probabilistic Robotics* chapters 9–11

**On your rover**
- `./rover slam` (slam_toolbox), `./rover slam --check`, saved room `maps/last`
- `./rover map` (nvblox), `phase2/nodes/ghost_clear.py` (erasing what moved)
- `lidar_odom.py`: LiDAR odometry

**Checkpoint:** explain loop closure and why the map → odom transform jumps; why nvblox needs the fused pose, not raw VO ("a teleport smears the map").

---

## 6. Path planning (2 weeks)

**Learn**
- Graph search: BFS, Dijkstra, **A\*** (admissible heuristics), weighted A*
- Lattice planners, Hybrid-A*, sampling planners (RRT, RRT*), configuration space
- Costmaps and inflation

**Resources**
- *Planning Algorithms*, Steven LaValle (free online), chapters 2, 5
- Red Blob Games, *Introduction to A\** (free, interactive)

**On your rover**
- `phase3/nodes/tight_route.py`: A* over (x, y, θ) with real turn/drive moves
- `phase3/nodes/house_route.py`, `widest.py` (max-min path)
- `NAV_GUIDE.md` §2–3: costmaps, inflation table, Smac lattice

**Checkpoint:** code A* on a grid in 30 minutes (interview classic); explain why the planner says "no valid path" for a 42 cm gap when the rover is 38 cm.

---

## 7. Control (1.5 weeks)

**Learn**
- PID (tuning, windup, deadband), feedforward
- Pure pursuit, Stanley; MPC; **MPPI** (sampling-based MPC)
- Stability intuition; latency and its effects

**Resources**
- Brian Douglas, *Control Systems Lectures* and MATLAB Tech Talks (YouTube, free)
- *Underactuated Robotics*, Russ Tedrake (MIT, free online) — selected chapters

**On your rover**
- `phase1/firmware/`: the ESP32 PID per wheel, 500 ms watchdog
- `phase3/nodes/turn_shaper.py` (lifting small turns over the deadband)
- `NAV_GUIDE.md` §4: MPPI critics and every speed limit

**Checkpoint:** explain integral windup and a fix; explain how MPPI picks a command each 0.1 s.

---

## 8. Navigation systems (1 week)

**Learn**
- nav2 architecture: planner server, controller server, behaviour trees, recoveries, collision monitor
- Designing for failure: retries, recoveries, giving up honestly

**On your rover**
- `phase3/bt/navigate_to_pose.xml`, `phase3/config/nav2.yaml` (every value says why)
- `phase3/nodes/reach_node.py` header: the recovery chain
- `NAV_GUIDE.md` §8 exercises — do all five

**Checkpoint:** follow one goal through `/reach/status → /plan → /cmd_vel_nav → /cmd_vel` and say where the speed dropped.

---

## 9. Perception and deep learning (2 weeks)

**Learn**
- Neural networks, backprop, CNNs, training loop, overfitting
- Object detection (YOLO), segmentation; camera model: pixel + depth → 3D point
- Deployment: ONNX, TensorRT, quantisation (FP16/INT8)

**Resources**
- fast.ai *Practical Deep Learning* (free) or Andrej Karpathy, *Neural Networks: Zero to Hero* (YouTube)

**On your rover**
- `phase4/nodes/yolo_backend.py`, `detections_3d.py`, `pixel_to_goal.py`
- `./rover engine` (building a TensorRT engine)

**Checkpoint:** project a pixel (u, v) with depth d into the robot frame using the intrinsics and the camera's TF.

---

## 10. Reinforcement learning (3 weeks) — the next big step for this rover

**Learn, in this order**
1. MDPs: states, actions, rewards, discount, policy, value function, Bellman equation
2. Dynamic programming: value iteration, policy iteration
3. Monte Carlo, TD learning, **Q-learning**, SARSA
4. Deep RL: DQN (replay buffer, target network)
5. Policy gradients: REINFORCE, actor-critic, advantage, GAE
6. **PPO** (what this rover will use), SAC (off-policy)
7. Practical: reward shaping, reward hacking, curriculum, observation normalisation
8. **Sim-to-real:** domain randomisation, system identification, safety shields
9. **Imitation learning:** behaviour cloning, DAgger, demo-augmented RL

**Resources**
- *Reinforcement Learning: An Introduction*, Sutton & Barto (free PDF) — chapters 1–6, 13
- David Silver's *RL course* (UCL / DeepMind, YouTube, free) — the classic intro lectures
- OpenAI *Spinning Up in Deep RL* (free) — clear PPO / SAC explanations with code
- Hugging Face *Deep RL Course* (free, hands-on)
- Sergey Levine, *CS 285 Deep RL* (Berkeley, YouTube) — for depth after the basics

**Hands-on, in order**
1. Gridworld value iteration in numpy (one evening)
2. Q-learning on gymnasium `FrozenLake`, then DQN on `CartPole`
3. PPO on `CartPole` with Stable-Baselines3 (installed on the laptop)
4. Isaac Lab cartpole with RSL-RL (§11)

**On your rover**
- `RL_PLAN.md` — read it again after this module; every word in §15 should be easy
- Map each term to the plan: observation (§4.1), action (§4.2), reward (§6.8), curriculum (§6.9), PPO settings (§6.10), domain randomisation (§6.6), shield (§5), behaviour cloning (R0.5)

**Checkpoint:** explain PPO's clipped objective in 2 minutes; explain why on-robot RL from scratch is not done and how sim-to-real is handled; write the reward for "drive through a door without touching".

---

## 11. Simulation: Isaac Sim and Isaac Lab (1.5 weeks)

**Learn**
- What a physics simulator does; rendering vs physics; headless runs
- Isaac Lab workflows: direct vs manager-based environments; vectorised envs on the GPU
- Digital twins and parity testing

**On your rover**
```bash
ssh chotesh@kino.local
cd ~/kino_sim && ./sim            # all commands
./sim selftest door80             # the twin alone, sensors vs the world file
cd ~/IsaacLab
./isaaclab.sh -p scripts/reinforcement_learning/train.py --rl_library rsl_rl \
    --task Isaac-Cartpole-Direct --num_envs 1024 --viz none
```
- Read `~/IsaacLab/source/isaaclab_tasks/isaaclab_tasks/core/cartpole/cartpole_direct_env.py`
  — the template RL_PLAN's environment will follow
- `~/kino_sim/CLAUDE.md`, `docs/IMPROVEMENT_LOOP.md`

**Checkpoint:** train cartpole, play it, export ONNX; explain what `_get_observations`, `_get_rewards`, `_get_dones`, `_reset_idx` each do.

---

## 12. Robot AI: LLM agents, VLMs, VLAs (1 week)

**Learn**
- LLM tool calling and agents (LangGraph), prompt and state design
- VLMs for grounding ("where is the bottle in this image?")
- **VLAs** (RT-2, OpenVLA, π0, GR00T N1): what they are, why they need big GPUs and many demos
- System 2 / System 1: slow thinking decides *what*, fast control does *how*
- Semantic maps: VLMaps, ConceptGraphs, OK-Robot

**On your rover**
- Pi 5 `~/ros2_ws`: `agent_node`, the three agents; LangSmith traces (project `pi5`)
- `INTELLIGENCE_PLAN.md` §1.2, `RL_PLAN.md` §14a

**Checkpoint:** explain why the LLM is never inside the 10 Hz motion loop; when a VLA would be the right tool (the arm).

---

## 13. Systems: embedded, Docker, networks, GPU (1 week)

**Learn**
- Microcontrollers, real-time loops, watchdogs; micro-ROS
- Docker for robots (mounts, `--network=host`, GPUs); DDS discovery, multicast, MTU
- Jetson: CUDA, shared CPU/GPU memory, load and its effects

**On your rover**
- `phase1/firmware/`, `phase1/firmware/HARDWARE.md`; `./rover wheels`
- `rover` script lines 370–392 (the `docker run`); `docker/2.0/Dockerfile`
- `./rover net`, `./rover health`; the D555 jumbo-frame story

**Checkpoint:** explain why the ESP32 has a 500 ms watchdog and what happens to `/cmd_vel` with no subscriber.

---

## 14. Interview preparation

### 14.1 What robotics interviews usually have

| round | what | prepare with |
|---|---|---|
| coding | arrays, graphs (BFS / Dijkstra / A*), geometry (point in polygon, segment intersection), matrices | LeetCode graph + geometry sets; code A*, ray–segment intersection, a Kalman filter from memory |
| robotics fundamentals | transforms, kinematics, KF/EKF, SLAM, planning, control | modules 2–7 checkpoints |
| ML / RL | training loop, overfitting, PPO, sim-to-real, reward design | modules 9–10 |
| system design | "design the navigation stack for a home robot", "how would you make a robot reach a cup 1 cm accurately" | your own architecture: `SYSTEM_OVERVIEW.md`, `NAV_GUIDE.md` §1, `RL_PLAN.md` §3 |
| debugging / behavioural | "a hard bug you solved", "a time the data proved you wrong" | §14.3 stories |

### 14.2 Question bank (answer each in 2 minutes, out loud)

**Estimation:** Why an EKF over a KF? What is the innovation? How do you reject an outlier? Why does odometry drift? Particle filter vs EKF?
**SLAM:** What is loop closure? Why does map→odom jump? Occupancy grid vs TSDF? What breaks visual odometry?
**Planning:** A* vs Dijkstra? Admissible heuristic? Why can a lattice planner fail where a gap exists? RRT vs A*?
**Control:** PID terms and tuning? Integral windup? Pure pursuit vs MPC? What does latency do to a controller?
**RL:** On- vs off-policy? Why PPO clips? What is GAE? What is reward hacking, with an example? How do you cross the sim-to-real gap? When is imitation better than RL?
**Systems:** ROS 2 QoS choices for sensor data? How do you debug "the robot does not move but everything says OK"? How do you make a robot fail safe?

### 14.3 Your stories (STAR: situation, task, action, result) — all from this repo

| story | where it is written down |
|---|---|
| the turn slide: 6–33 cm per 90°, learning the pivot from every turn, 1.4 cm goals | `ROVER_BUILD_PLAN.md` §0, `goal_exec.py` header |
| the D555 needs jumbo frames; the switch caps 1500 bytes | `WAVESHARE_SWITCH_TEST.md` |
| "every gate green, rover never moves": `/cmd_vel` with no subscriber, the motor supply off | `STARTUP.md` §4, rover-start notes |
| gated sensor fusion: cuVSLAM lost, the pose stays LiDAR-anchored | `fusion2.py`, `LOCALIZATION.md` |
| the bedroom door: nav2 "no path", tight_route found turn-left-and-go in 0.4 s | `tight_route.py` header |
| ghost specks from people closing a corridor; `ghost_clear` | `NAV_GUIDE.md` §6 L3 |
| a digital twin running the real code, judged on true pose | `~/kino_sim/README.md` |
| planning RL with a safety shield and a nightly gate | `RL_PLAN.md` |

### 14.4 Portfolio

- A short README-style page or video per story above (the numbers make it credible)
- After R1–R5 of RL_PLAN: a before/after video of door49 / the real door — the strongest item

---

## Weekly rhythm (suggested)

| day | do |
|---|---|
| 2 evenings | theory (book / lecture) |
| 1 evening | read the rover's file for that topic, run its command |
| weekend | the checkpoint + 3 questions from §14.2 out loud + 2 coding problems |

Keep a log: `docs/learn/notes/<module>.md` — what you learned, what confused you, your answer to
the checkpoint. Ask Claude in this repo to quiz you on any module ("grill me on module 4").
