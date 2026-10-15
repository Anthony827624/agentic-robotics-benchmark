# Agentic robotics — literature survey (candidate tasks for the `agentic` direction)

Backing research for the `agentic` category added under every platform in
`tasks/<platform>/agentic/`. "Agentic" here means: an **LLM / foundation-model agent**
(especially an LLM *coding* agent, or an agentic framework that plans, reasons,
orchestrates skills/primitives, or self-improves policies via a closed feedback loop)
that drives, composes, or autonomously improves robot policies — **NOT** a plain
end-to-end VLA or RL policy (a bare policy network does not qualify; the
orchestration / planning / self-improvement layer is what does).

Compiled 2026-07 from a multi-source, adversarially-verified survey (each factual claim
confirmed 3-0 against primary sources unless noted). This is a **shortlist for future
onboarding**, not a decision to onboard any of these. When onboarding, follow
`skills/onboard-task/SKILL.md` — a candidate only becomes a task after a real reference
run produces its anchor and every gate is green.

## Two flavors of "agentic" (both in scope)

1. **Runtime skill-orchestration / real-world closed-loop self-improvement** — the strict
   seed definition (ENPIRE, Harness VLA, TypeFly, AerialClaw, Agent-Driver, COME-robot).
2. **LLM-coding-agent that generates reward / environment / curriculum CODE** with a
   training-feedback loop (Eureka, DrEureka, Eurekaverse, Text2Reward, RoboGen). This
   family is inherently **platform-agnostic** — the same method reappears on dexterous
   hands, quadrupeds, and MuJoCo locomotion.

## Per-platform tally

| Platform | Papers found | Open-source w/ code |
|---|---|---|
| Manipulation (arm / dexterous / bimanual) | 4 | 2 (Eureka, RoboGen) |
| Legged / quadruped / humanoid | 3 | 2 (DrEureka, Eurekaverse) |
| UAV / aerial / drone | 3 | 1 confirmed (TypeFly) + 1 stated (AerialClaw) |
| Autonomous driving / wheeled / mobile ground | 2 | 2 (Agent-Driver, OmniDrive) |
| Multi-robot / swarm | 2 | 1 (SMART-LLM) |
| Spacecraft | 1 | code status unverified (LLMSat) |
| Underwater / surgical / soft | 0 | 0 |

Cross-platform note: the driving row corrects the raw survey's "driving = 0" verdict —
Agent-Driver and OmniDrive were found in the fetch phase but dropped from the final top-5
synthesis; both are real open-source driving-agent works.

---

## Manipulation

- **Eureka** — *Human-Level Reward Design via Coding Large Language Models*.
  arXiv:2310.12931, ICLR 2024. **Code: github.com/eureka-research/Eureka (MIT, ~3.2k★).**
  GPT-4 coding agent doing in-context evolutionary optimization over reward-function code
  with a reward-reflection loop; flagship Shadow Hand pen-spinning; spans 29 RL envs / 10
  morphologies. **Best onboarding candidate** (mature open code, clear metric).
- **RoboGen** — *A generative and self-guided robotic agent…*. arXiv:2311.01455, ICML 2024.
  **Code: github.com/Genesis-Embodied-AI/RoboGen (open-source).** Generative agent running
  a propose → generate → learn cycle; decomposes tasks and selects RL / motion-planning /
  traj-opt.
- **ENPIRE** — *Agentic Robot Policy Self-Improvement in the Real World*. arXiv:2606.19980
  (NVIDIA GEAR / UT-RPL, 2026). **No first-party code** (only a third-party reimpl,
  github.com/skr3178/ENPIRE). Harness for coding agents closing a real-world
  reset→execute→verify→refine loop (Environment / Policy-Improvement / Rollout / Evolution
  modules); platform = bimanual 6-DoF YAM, fleet of 8 stations. *Seed example.*
- **Harness VLA** — *Steering Frozen VLAs into Reliable Manipulation Primitives via
  Memory-Guided Agents*. arXiv:2607.08448. **Code status UNVERIFIED.** Memory-augmented
  agentic framework composing a frozen VLA with a fixed analytic-primitive library;
  evaluated on LIBERO-Pro / RoboCasa365 / RoboTwin C2R. *Seed example.*

## Legged / quadruped / humanoid

- **DrEureka** — *Language Model Guided Sim-to-Real Transfer*. RSS 2024, arXiv:2406.01967.
  **Code: github.com/eureka-research/DrEureka (MIT, ~938★).** LLM auto-generates reward +
  domain-randomization config for sim-to-real; primary platform Unitree Go1
  (forward_locomotion + globe_walking), secondary LEAP-Hand.
- **Eurekaverse** — *Environment Curriculum Generation via LLMs*. CoRL 2024 Oral,
  arXiv:2411.01775. **Code: github.com/eureka-research/Eurekaverse (MIT, ~115★).** LLM
  samples progressively harder training environments as code (adaptive curriculum);
  quadruped parkour, Go1 deployment.
- **Agent-Driven Autonomous RL Research** — arXiv:2603.27416 (Mar 2026). **No public code.**
  An agentic coding environment runs the RL research loop (reads code, diagnoses failures,
  edits rewards/terrain, launches/monitors jobs) on a DHAV1 12-DoF quadruped in Isaac Lab;
  framed as an empirical case study, not a novel policy.
- *(No humanoid-specific agentic paper surfaced; humanoid inherits the legged family.)*

## UAV / aerial / drone

- **TypeFly** — *Power the Drone with Large Language Model*. arXiv:2312.14950, IEEE Trans.
  Mobile Computing 2025. **Code: github.com/typefly/TypeFly (Apache-2.0, 109★).** GPT turns
  instruction + scene into an executable plan built from the robot's registered skills via
  the MiniSpec DSL; DJI Tello (now also Unitree Go2 / Petoi — multi-platform).
- **AerialClaw** — *An Open-Source Framework for LLM-Driven Autonomous Aerial Agents*.
  arXiv:2606.12142 (Jun 2026). **Stated open-source, repo URL/stars NOT verified.**
  Brain-skill-runtime architecture, memory-driven reflection, closed
  invoke→observe→update loop; supports mock / PX4-SITL-Gazebo / AirSim.
- **UAV-CodeAgents** — arXiv:2505.07236 (May 2025, cs.RO). **Code + benchmark promised,
  not confirmed released.** Scalable multi-agent framework, ReAct reasoning loop,
  fine-tuned Qwen2.5VL-7B; mission planning from satellite imagery + natural language.
  (Also the main multi-robot/swarm signal.)

## Autonomous driving / wheeled / mobile ground

- **Agent-Driver** — *A Language Agent for Autonomous Driving*. COLM 2024, arXiv:2311.10813.
  **Code: github.com/USC-GVL/Agent-Driver (public; live star count unreliable — repo
  redirects).** LLM cognitive agent replacing the classic perception→prediction→planning
  pipeline.
- **OmniDrive** — *A Holistic LLM-Agent Framework for End-to-End Autonomous Driving*.
  arXiv:2405.01392. **Code: github.com/NVlabs/OmniDrive (open-source; code + dataset +
  checkpoints).** Drive LLM-agent framework.
- **COME-robot** — GPT-4V closed-loop planning agent for open-ended real-world tasks (mobile
  manipulation). ICRA 2025, arXiv:2404.10220. Project: come-robot.github.io.

## Multi-robot / swarm

- **SMART-LLM** — *Smart Multi-Agent Robot Task Planning using LLMs*. arXiv:2309.10062,
  ICRA 2024. **Code: github.com/SMARTlab-Purdue/SMART-LLM.** LLM converts a high-level
  instruction into a multi-robot plan via decomposition → coalition formation → allocation.
- **UAV-CodeAgents** — see UAV above (multi-UAV coordination).

## Spacecraft

- **LLMSat** — an LLM as the high-level autonomous control/reasoning engine of a spacecraft.
  arXiv (2024). **Code status unverified.** The only spacecraft-agentic signal found.

## Cross-cutting resources

- **Text2Reward** — arXiv:2309.11489, ICLR 2024 Spotlight. **Code: github.com/xlang-ai/
  text2reward (~210★).** LLM-generated dense reward CODE for RL; evaluated on ManiSkill2 /
  MetaWorld manipulation AND MuJoCo locomotion (with real-world transfer) — evidence the
  agentic axis is orthogonal to platform.
- Curated lists: github.com/WeisonWEileen/awesome-agentic-robot-learning;
  github.com/GT-RIPL/Awesome-LLM-Robotics.

---

## Onboarding candidacy (when we decide to add real tasks)

- **Strong (mature open code + clear scored metric):** Eureka, DrEureka, Eurekaverse,
  RoboGen, TypeFly, Text2Reward, SMART-LLM, OmniDrive, Agent-Driver.
- **Weak / blocked (no or unverified first-party code):** ENPIRE (3rd-party reimpl only),
  Harness VLA (unverified), AerialClaw (stated only), UAV-CodeAgents (unreleased), LLMSat
  (unverified). Prefer the strong list unless code is confirmed.

Gaps with no agentic paper found: **underwater, surgical, soft** (their `agentic` folders
are forward-placed placeholders).
