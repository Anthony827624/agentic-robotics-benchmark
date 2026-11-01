# Task image. Nothing here depends on verify/, oracle/, the anchor or the hidden objects: an image layer is readable
# by anyone who can run the image. The hidden objects' meshes and textures are NOT in the image.
FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt /tmp/requirements.txt
RUN uv venv /opt/venv --python 3.12 \
    && uv pip install --python /opt/venv -r /tmp/requirements.txt \
    && uv pip install --python /opt/venv --no-deps robosuite==1.5.2 \
    && chmod -R a+rX /opt/venv \
    && { [ ! -d /opt/uv-python ] || chmod -R a+rX /opt/uv-python; }

# Software rendering for MuJoCo: Mesa OSMesa (llvmpipe, no display or device needed), checked below as the agent user.
# (EGL fails in the sandbox with MuJoCo 3.16 + Ubuntu 22.04 Mesa: no EGL device; rev-quality R fix 2.)
RUN apt-get update && apt-get install -y --no-install-recommends libosmesa6 libgl1 \
    && rm -rf /var/lib/apt/lists/*

# ---- task assets: fetched at the dataset commit pinned in assets.sha256, checked file by file (docs/task-assets.md):
# the 120 input videos and 80 data arrays of data/objects/ (the agent-visible recordings; the JSON files stay in setup/payload)
COPY fetch_assets.py assets.sha256 /opt/task-assets/
RUN python3 /opt/task-assets/fetch_assets.py /opt/task-assets/assets.sha256 /opt/task-assets/payload \
    && chmod -R a+rX /opt/task-assets

RUN mkdir -p /home/user/submission && chown -R user:user /home/user/submission

ENV MPLBACKEND=Agg OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MUJOCO_GL=osmesa PYOPENGL_PLATFORM=osmesa LP_NUM_THREADS=4

RUN python3 -c "import numpy, scipy, mujoco, cv2, trimesh, skimage; print('[image] mujoco', mujoco.__version__, 'cv2', cv2.__version__)" \
    && python3 -c "import site, sys; assert not site.ENABLE_USER_SITE or sys.prefix != sys.base_prefix" \
    && runuser -u user -- python3 -c "import numpy, scipy, mujoco, cv2; print('[image] agent-UID import OK')" \
    && runuser -u user -- env HOME=/tmp python3 -c "import mujoco; m = mujoco.MjModel.from_xml_string('<mujoco><worldbody><light pos=\"0 0 1\"/><geom type=\"sphere\" size=\".1\"/></worldbody></mujoco>'); d = mujoco.MjData(m); mujoco.mj_forward(m, d); r = mujoco.Renderer(m, 48, 64); r.update_scene(d); im = r.render(); assert im.max() > 0; print('[image] agent-UID MuJoCo OSMesa render OK', im.shape)"
