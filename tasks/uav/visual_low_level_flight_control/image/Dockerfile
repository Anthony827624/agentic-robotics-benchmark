FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 \
    && rm -rf /var/lib/apt/lists/*
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=/opt/venv/bin:$PATH
ENV MUJOCO_GL=osmesa LIBGL_ALWAYS_SOFTWARE=1 LP_NUM_THREADS=2
ENV OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONNOUSERSITE=1
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && chmod -R a+rX /opt/venv
RUN mkdir -p /home/user/submission && chown user:user /home/user/submission
RUN runuser -u user -- python3 -c "import mujoco; m=mujoco.MjModel.from_xml_string('<mujoco/>'); r=mujoco.Renderer(m,64,64); r.close()"
# The onboard camera's renderer (world.py, fil_render.py): MuJoCo's Filament module (in the mujoco wheel) on Vulkan;
# Mesa's lavapipe is the CPU Vulkan driver (the task has no GPU). Proved at build: a Filament context on lavapipe.
RUN apt-get update && apt-get install -y --no-install-recommends mesa-vulkan-drivers libvulkan1 \
    && rm -rf /var/lib/apt/lists/*
RUN env -u DISPLAY /opt/venv/bin/python3 -c "import mujoco._render_filament as F, mujoco.experimental.studio.window; F.Context(F.ContextConfig(graphics_api=F.GraphicsApi.GRAPHICS_API_VULKAN)); print('[image] Filament on Vulkan OK')"
# ---- task assets: fetched at the dataset commit pinned in assets.sha256, checked file by file (docs/task-assets.md):
# render/, the onboard camera's scenery (the warehouse hall, the course's textures) and its image light and sky maps;
# fil_render.py reads /opt/task-assets/payload/render (root-owned, not staged into the agent home)
COPY fetch_assets.py assets.sha256 /opt/task-assets/
RUN python3 /opt/task-assets/fetch_assets.py /opt/task-assets/assets.sha256 /opt/task-assets/payload \
    && chmod -R a+rX /opt/task-assets
ENV VISUAL_ROBOTICS_RENDER=/opt/task-assets/payload/render
COPY visual_assets.json fetch_visual_assets.py /tmp/visual_assets/
RUN /opt/venv/bin/python3 /tmp/visual_assets/fetch_visual_assets.py /tmp/visual_assets/visual_assets.json /opt/visual_robotics/assets \
    && chmod -R a+rX /opt/visual_robotics/assets
