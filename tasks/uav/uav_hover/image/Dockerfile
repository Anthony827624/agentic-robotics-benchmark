# drone_hover's environment image. `image/` is the sole build context; the final stage
# derives directly from the official ALE base, so the sandbox contract (guestd,
# unprivileged `user` account, ale.user label, keep-alive CMD, no ENTRYPOINT) is
# inherited. This layer adds ONLY the simulator + CPU RL toolchain. No method code and
# no answers — the tested agent implements the PPO recipe itself.
FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# pybullet links libGL/libgomp even in headless DIRECT mode. pybullet publishes no cp312
# wheel, so it compiles from source (C++) — and the base's standalone CPython 3.12
# (/opt/python) was built with clang, so its sysconfig hard-names clang/clang++ as the
# compiler the extension build invokes; hence `clang`, not just build-essential's gcc
# (without it the build dies with "No such file or directory: 'clang++'"). build-essential
# still supplies make + the C runtime other source installs need. No python3-dev: the
# standalone interpreter bundles its own Python.h — apt's python3-dev would only supply
# jammy's 3.10 headers, unused here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 build-essential clang \
    && rm -rf /var/lib/apt/lists/*

# The simulator, pinned. The repo's own RL example recipe (examples/, which holds the
# exact StopTrainingOnRewardThreshold(474) PPO script) is stripped so the method is not
# handed to the agent. Clone -> checkout -> strip -> purge .git in ONE layer.
ARG GPD_COMMIT=9bc12bc583fa3b28807b2f90a8cadf09fb06e1ff
RUN git clone https://github.com/utiasDSL/gym-pybullet-drones.git /opt/gym-pybullet-drones \
    && git -C /opt/gym-pybullet-drones checkout ${GPD_COMMIT} \
    && rm -rf /opt/gym-pybullet-drones/gym_pybullet_drones/examples \
    && rm -rf /opt/gym-pybullet-drones/.git

# World-readable venv at /opt/venv: setup (root), the agent (`user`), and verify (root)
# all use the same interpreter. UV_PYTHON_INSTALL_DIR must be world-readable too — the
# default is /root/.local/share/uv, which the agent user cannot traverse, so the venv's
# python symlink silently dies for the very account that must run the solver.
# CPU torch wheel — this is a CPU task.
#
# Python 3.12: the engine stages `ale_verify` into the `python3` it finds and refuses
# anything below 3.12, so an older venv fails every verify stage on this image.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN uv venv /opt/venv --python 3.12 \
    && uv pip install --python /opt/venv \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.2,<2.9" \
    && uv pip install --python /opt/venv -e /opt/gym-pybullet-drones \
    && uv pip install --python /opt/venv "setuptools<81" \
    && chmod -R a+rX /opt/venv \
    && { [ ! -d /opt/uv-python ] || chmod -R a+rX /opt/uv-python; }
# setuptools<81: BaseAviary.py imports pkg_resources, removed in setuptools 82.
# /opt/uv-python only exists when uv had to download a managed CPython; the ALE base
# ships a standalone CPython 3.12 (/opt/python, behind /usr/local/bin/python3), so uv
# links the venv against that instead of downloading one.

# The declared artifact path must exist even when nobody works — the engine captures
# declared artifacts after every episode, untouched validation passes included, and a
# missing path is an explicit episode error, not a zero.
RUN mkdir -p /home/user/submission && chown -R user:user /home/user/submission

ENV MPLBACKEND=Agg
ENV PYBULLET_EGL=0

# Import sanity — fail the build early if the environment is broken, and prove the AGENT
# account can use the venv (the exact failure UV_PYTHON_INSTALL_DIR above prevents).
# One line per -c: a continuation line starting with `from` reads as a Dockerfile FROM
# to the engine's final-stage lint.
RUN python3 -c "import gymnasium, stable_baselines3, pybullet; import gym_pybullet_drones.envs.HoverAviary; print('[drone-hover] sim + sb3 import OK')" \
    && runuser -u user -- python3 -c "import gym_pybullet_drones; print('[drone-hover] agent-UID import OK')"

# NOTE: the base image's guest service runs on the base's own interpreter, not this
# venv — the venv serves the task's own stages and the agent.
