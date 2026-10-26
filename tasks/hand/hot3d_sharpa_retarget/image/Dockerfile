FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/flashdex/src \
    MUJOCO_GL=disabled \
    MPLBACKEND=Agg \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    OMNI_KIT_ACCEPT_EULA=1

ARG ACCEPT_NVIDIA_EULA=1
RUN test "$ACCEPT_NVIDIA_EULA" = "1"

COPY requirements.txt requirements-isaac.txt requirements-perception.txt /tmp/
RUN uv venv --seed /opt/venv --python 3.12 \
    && uv venv --seed /opt/isaac-venv --python 3.11 \
    && uv pip install --python /opt/venv -r /tmp/requirements.txt \
    && chmod -R a+rX /opt/venv /opt/isaac-venv /opt/python /opt/uv-python

RUN /opt/isaac-venv/bin/python -m pip install --no-deps "setuptools<81" wheel \
    && /opt/isaac-venv/bin/python -m pip install --no-deps --no-build-isolation flatdict==4.0.1
RUN /opt/isaac-venv/bin/python -m pip install --no-deps --prefer-binary \
    -r /tmp/requirements-isaac.txt \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    --extra-index-url https://pypi.nvidia.com

RUN apt-get update && apt-get install -y --no-install-recommends libglu1-mesa \
    && rm -rf /var/lib/apt/lists/*

# General-purpose perception tools, offline: OpenCV, MediaPipe and its hand-landmarker
# model (pinned by sha256), projectaria-tools for the Aria camera models, MuJoCo.
ARG HAND_LANDMARKER_URL=https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
ARG HAND_LANDMARKER_SHA256=fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1
RUN uv venv --seed /opt/perception-venv --python 3.12 \
    && uv pip install --python /opt/perception-venv -r /tmp/requirements-perception.txt \
    && mkdir -p /opt/models \
    && /opt/perception-venv/bin/python -c "import urllib.request; urllib.request.urlretrieve('${HAND_LANDMARKER_URL}', '/opt/models/hand_landmarker.task')" \
    && echo "${HAND_LANDMARKER_SHA256}  /opt/models/hand_landmarker.task" | sha256sum -c - \
    && chmod -R a+rX /opt/perception-venv /opt/models /opt/uv-python

COPY flashdex /opt/flashdex
# The clips (frames, calibration, headset pose), restored into image/assets/ by `ale assets pull`.
COPY --chown=user:user assets/clips /home/user/clips
RUN install -d -o user -g user /home/user/submission /home/user/.cache/ov \
    && chmod -R a+rX /opt/flashdex /opt/venv /opt/isaac-venv /opt/python /opt/uv-python \
    && for name in cache data logs; do \
      mkdir -p /opt/isaac-venv/lib/python3.11/site-packages/isaacsim/kit/$name; \
      chown -R user:user /opt/isaac-venv/lib/python3.11/site-packages/isaacsim/kit/$name; \
    done

RUN runuser -u user -- /opt/venv/bin/python3 -c "import sys, numpy; assert sys.version_info >= (3, 12); print('[image] ALE Python and numpy OK')" \
    && runuser -u user -- /opt/isaac-venv/bin/python3 -c "import isaacsim, torch, hydra, tensordict; print('[image] agent-UID Isaac imports OK')" \
    && runuser -u user -- /opt/perception-venv/bin/python3 -c "import mediapipe, cv2, projectaria_tools, scipy, mujoco, skimage, open3d; print('[image] agent-UID perception imports OK')" \
    && runuser -u user -- /opt/isaac-venv/bin/python3 -c "import coacd; print('[image] agent-UID coacd OK')" \
    && runuser -u user -- test -r /home/user/clips/P0002_016222d1_seg016/frames.jsonl
