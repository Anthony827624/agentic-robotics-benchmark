# Robot service

Use the declared MCP tools for direct image interaction.
For tasks allowing programs, import `Client` from `client.py` and call
`observe()`, `act(action)`, `advance()`, or `report(frame_id, point)`.
Run code with `/opt/venv/bin/python3`; do not rely on the harness's PATH.
The endpoint is `/run/visual_robotics/world.sock`.
The first two methods return base64 PNG images and remaining budgets.
There is no reset, state inspection, file-read or code-execution operation.
Keep deliverables only under `/home/user/submission`.
