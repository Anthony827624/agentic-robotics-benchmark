# Agentic Robotics Benchmark（中文版）

> English version: [README.md](README.md)（两个版本同步维护，改一处请同步另一处）。

**一个面向自主 agent 的 agentic robotics 任务 benchmark。** 每个任务把一个自主 agent 放进沙箱化的
robotics 问题里（开发控制器、规划操作序列、设计零件或机构、写 code-as-policy、调参规划器……），然后用
**隐藏 grader 在隐藏 seed 上**给它留在 `/home/user/submission/` 里的**任何产物**打分——一个控制器进程、
一份规划或规划器、一个 URDF / mesh / 参数设计、生成的代码、一份报告。`verify/` 按任务作者的定义给产物
打分；`oracle/` 就是能拿满分的参考产物。任务只有一种；`metadata.platform`（[`tasks/platforms.json`](tasks/platforms.json)
里固定的 key 之一）是分类轴。

任务运行在上游 **ALE 引擎**（[AgentsLastExam/ale](https://github.com/AgentsLastExam/ale)）上，
引擎以 submodule 钉在 `vendor/ale`；这里不重新实现它的任何东西。上游的
[task-authoring.md](https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-authoring.md)
与
[task-quality-standard.md](https://github.com/AgentsLastExam/ale/blob/main/docs/specs/task-quality-standard.md)
是任务格式与质量门槛的权威；本仓库只写 robotics 领域在其上增加的部分。网站
<https://agentic-robotics-benchmark.org> 只展示任务，不接收上传，也不接收提交。

本仓库公开且完整：每个任务文件夹整体在此，含 `oracle/` 与 `verify/`。anchor 对*被测 agent* 的隐藏靠
引擎的阶段隔离（agent 工作期间 `verify/` 不在沙箱里），不是对人保密。

## 一个任务 = 一个文件夹

一个任务是一个自包含的 `core/v1` 文件夹，引擎分三个阶段运行它
（`setup` → `agent` → `verify`）；`ale validate` 时 `oracle/` 代替 agent。robotics 领域在其上
增加的部分（三种 verify 模式、评分、vendored kit、probe wire）和模板的逐文件说明都在
[`templates/README.md`](templates/README.md)。

```
tasks/<platform>/<slug>/
├── task.yaml         manifest（严格 core/v1）；agent 不可见
├── instruction.md    提示词——被测 agent 唯一被告知的东西
├── image/Dockerfile  环境；最后一个 stage FROM 官方 ALE base
├── setup/            可信 root，在 agent 之前运行：把 setup/payload/ 放进 agent home
├── oracle/run.sh     产出参考产物；`ale validate` 用它代替 agent
└── verify/           grader（verify.py）、anchor.json、grader_config.json 和 vendored 的
                      robotics_grader/ 包；agent 工作期间**不存在**
```

让 grader 和 anchor 远离 agent 的是**时机，而不是某个开关**：一个阶段的文件夹只在该阶段
运行时才进入沙箱，`/opt/ale` 只有 root 可进，agent 的 solver 以 agent 的 UID 运行。
agent 打印的任何东西都不是评分输入；grader 用自己的仿真器状态计算 metric。

## 评分

```
ratio  = clamp(measured / anchor, 0, cap)     # lower-is-better 指标取倒数；cap = 1.5
reward = clamp(ratio / full_at, 0, 1)         # 唯一受闸门约束的 reward key
```

**anchor** 是参考实现在真实运行中自己测出来的值——绝不是别处印出来的数字；`full_at`
是 reward 饱和到 1.0 的 ratio，按该指标实测的 seed 间波动来定。`reward`（0 到 1）是任务的
得分，也是引擎的闸门信号；带 cap 的 `ratio` 随 verdict 一并记录。指标永远是正尺度的结果指标，绝不是
墙钟时间或吞吐。

`ale validate` 把每个任务跑两遍。未触碰的沙箱必须产出精确的全零 verdict，随后 oracle 必须
在每个 reward key 上精确得 1.0。任一不满足即 validation 失败（`untouched_nonzero`、
`oracle_not_full`；退出码 2）。anchor 为 null 时按设计同样失败；`harness/ale_onboard.py`
读取运行记录，把它报告为 `needs_anchor` 并附上实测值。

## 贡献一个任务

贡献就是向本仓库提交一个 pull request，把一个任务文件夹加在 `tasks/<platform>/<name>/`，其中
`<name>` 是 manifest 的 `name`（任务登记的名称），`<platform>` 是它的 `metadata.platform`。
报名、职责分工、任务各阶段与贡献计分见项目的贡献指南：[中文](https://docs.google.com/document/d/1gX8v-w-idIPLjzoWgRskT0llE7cKXqg9sVLB0cWfZk0/view)、[English](https://docs.google.com/document/d/1s1E3ymIBLujWf2l7rhu4g9Nl4orBfooK0aDWOdum7-Y/view)。

从 `templates/task/` 开始做任务，用 `ale lint` 与 `ale validate` 过闸门，再用 frontier model
测难度（`ale run`；目标是得分大致低于 0.5），然后开 PR。CI 会在 PR 上跑 `ale lint`、领域 lint
与 kit 字节比对；维护者重跑 `ale validate`、merge、导出注册表。短版见
[`CONTRIBUTING.md`](CONTRIBUTING.md)；从模板到 merge 的完整路径见
[`docs/contributing-a-task.md`](docs/contributing-a-task.md)。

## 目录结构

```
vendor/ale/                     上游引擎（submodule，钉 commit）——不要重新实现它
tasks/<platform>/<slug>/        任务本身（目前：space/fault_adaptive_spacecraft_docking）
tasks/platforms.json            平台词表：`metadata.platform` 的 key 及其显示名
tasks/README.md                 说明：哪个任务是完整示例
templates/task/                 唯一模板，出厂即闸门全绿（anchor 为 null 是设计）
templates/README.md             任务契约，逐文件说明（任务形态的事实源）
identity-history.json           每个任务历史上的 (spec_hash, content_digest) 对（registry v2）
shared/robotics_grader/         verify 阶段共享 kit 的 canonical 副本；每个任务 vendor 一份字节一致的副本
harness/
  ale_onboard.py                `ale-onboard`：实例化模板 + 跑真实闸门
scripts/
  export_registry.py            用引擎自己的 loader 导出 registry v2（任务身份）
  lint_domain.py                领域 lint（`ale lint` 不知道的规则），含落地路径检查
schemas/task-registry.schema.json   registry 的契约（归本仓库所有；导出结果按它测试）
skills/onboard-*/SKILL.md       面向 agent 的 onboarding skill（路由 + 每种 verify 模式一个）
tests/                          pytest 套件
docs/                           架构、安全模型、贡献指南、evidence/（真实 verdict 与隔离审计）、
                                dev/（设计记录）
.github/workflows/pr-checks.yml 每个 pull request 都要过的检查
```

## 常用命令

```bash
uv sync                                                    # 宿主侧工具（py3.11）

# 引擎自己的闸门，从钉住的 submodule 里跑（清掉 VIRTUAL_ENV，钉住 docker socket）
cd vendor/ale && VIRTUAL_ENV= uv run ale lint ../../tasks && VIRTUAL_ENV= uv run ale lint ../../templates
cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
    uv run ale validate ../../tasks/<platform>/<slug> --runs-dir /tmp/ale-runs

# 作者闸门：lint -> validate（untouched 全零 / oracle 全一）-> 诚实的结论
uv run ale-onboard <spec>.yaml --json                                      # 实例化 + 闸门
uv run ale-onboard --gate-only tasks/<platform>/<slug> --json
#   built | needs_anchor（报出实测值）| needs_input | gave_up | error

# 领域 lint（platform 与落地路径、指标纪律、anchor 出处、可见面、镜像卫生）+ 共享 kit 自测（不需要 docker）
uv run python scripts/lint_domain.py .
cd shared && python3 -m robotics_grader.selftest

# 用真 agent 跑一个任务（难度测试；verdict 在 <runs> 里）
cd vendor/ale && VIRTUAL_ENV= DOCKER_HOST=unix:///var/run/docker.sock \
    uv run ale run ../../tasks/<platform>/<slug> --agent claude-code --model <model> --runs-dir <runs>

# 任务 registry（任务身份来自引擎 loader；工作树不干净则拒绝；契约见
# schemas/task-registry.schema.json）。platforms 来自 tasks/platforms.json；
# 网站用它自己的同步脚本安装这份导出，只做展示
cd vendor/ale && VIRTUAL_ENV= uv run python ../../scripts/export_registry.py \
    --tasks ../../tasks --history ../../identity-history.json --out registry.json

uv run pytest -q
```

模板和完整示例任务都远不到一分钟就能 validate 完。

## 访问

运行任务需要 **ALE 引擎**和 **ALE base 镜像**。引擎仓库
（[AgentsLastExam/ale](https://github.com/AgentsLastExam/ale)）已公开；它不在 PyPI 上，
也还没有 LICENSE 文件，所以只引用它，绝不把它的代码 vendor 进本仓库。按其 README
安装（`git clone`、`just bootstrap`、`uv run ale --help`；需要 `uv`、`just`、Docker）。
base 镜像 `ghcr.io/agentslastexam/container-ubuntu22-base` 拒绝匿名拉取；在引擎 checkout
里运行 `images/build.sh container-ubuntu22` 几分钟即可本地构建。贡献者在开 PR 前自己运行
`ale lint` 与 `ale validate`；维护者在合并前再跑一遍。

## 文档

- [`docs/architecture.md`](docs/architecture.md)——引擎阶段、阶段隔离、UID 边界、verdict
  信封，以及 registry 导出。
- [`docs/security-model.md`](docs/security-model.md)——阶段隔离对被测 agent 藏了什么，
  一个 verdict 能证明什么、不能证明什么。
- [`docs/contributing-a-task.md`](docs/contributing-a-task.md)——贡献者从模板到 merge 的完整路径。
- [`docs/dev/`](docs/dev/)——带日期的设计记录；其中 2026-10-15 的说明写明了哪些已不再成立
  （网站上传渠道与 leaderboard 之桥已退役）。

## 历史

本仓库于 2026-09-07 随项目从 "ALE Robotics" 更名为 Agentic Robotics Benchmark 而以全新历史创建，
取代两个已归档的仓库：`ale-robotics-benchmark-private`（维护者的任务仓库；ALE 之前的任务形态及其
harness 在其 tag `legacy-harness-final`）与 `ale-robotics-benchmark`（公开镜像，tag
`legacy-v0.5-final`）。这里不复活其中任何一个。

## 约定

写进本仓库的一切——代码、注释、文档、任务文件——都是**英文**；被测 agent 和全球的
benchmark 使用者都会读到。文档与代码不一致时，以代码为准：指出来并修文档。

## 许可

Apache-2.0（见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)）。`vendor/ale` 下的上游引擎是独立项目，
遵循其自身条款。
