# The task's environment image. `image/` is the sole build context, and the final stage
# must derive directly from an official ALE base — lint enforces the FROM. The engine
# builds it on demand; nothing here is pushed by hand.
#
# What belongs here: the simulator and every stable dependency, installed for an
# interpreter every identity can use. What must NOT be here: verify/, oracle/, the
# anchor, or anything derived from them — an image layer is readable by anyone who can
# run the image.
FROM ghcr.io/agentslastexam/container-ubuntu22-base:latest

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# A world-readable venv, and a world-TRAVERSABLE interpreter behind it. Setup and verify
# run as root, the agent and the oracle run unprivileged, and all four use this one
# interpreter — so `uv`'s default managed-Python location (/root/.local/share/uv) breaks
# the venv for exactly the account that has to run the solver, and it surfaces as a
# ModuleNotFoundError with world-readable site-packages sitting right there.
#
# Python 3.12, not a style choice: at verify time the engine stages its own `ale_verify`
# package into whatever interpreter answers `python3`, and refuses anything below 3.12.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH=/opt/venv/bin:$PATH
# requirements.txt is the ONE list of Python dependencies: this image and the local
# dry-run (`_tools/dryrun.py`) both install from it.
COPY requirements.txt /tmp/requirements.txt
RUN uv venv /opt/venv --python 3.12 \
    && uv pip install --python /opt/venv -r /tmp/requirements.txt \
    && chmod -R a+rX /opt/venv \
    && { [ ! -d /opt/uv-python ] || chmod -R a+rX /opt/uv-python; }
# /opt/uv-python only exists when uv had to download a managed CPython; the ALE base
# ships a standalone CPython 3.12 (at /opt/python, behind /usr/local/bin/python3), so uv
# links the venv against that instead of downloading one.

# The declared artifact path must exist even when nobody works — the engine captures
# declared artifacts after every episode, untouched validation passes included, and a
# missing path is an explicit episode error, not a zero.
RUN mkdir -p /home/user/submission && chown -R user:user /home/user/submission

# Headless by default: a task that renders opts in, rather than every task inheriting a
# display it does not have.
ENV MPLBACKEND=Agg

# Fail the build early if the venv is broken — and prove the AGENT account can use it,
# which is the exact failure the UV_PYTHON_INSTALL_DIR line above prevents.
RUN python3 -c "import numpy; print('[image] numpy', numpy.__version__)" \
    && runuser -u user -- python3 -c "import numpy; print('[image] agent-UID import OK')"
