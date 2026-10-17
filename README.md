# 景程口语 · 可迁移的教学视频工作流

本仓库提供制作脚本、课件、提示词规范和验收流程，并包含项目所有者同意公开的 [Windows 交接包](handoff/jingcheng-windows-setup.zip)：实际服务器配置、建国音色参考，以及检测/降噪模型。人物参考、原始笔记、具体课程素材和成片另行准备。

**Windows 可以作为控制端。** 配音由已有的 Linux / L40S 服务器完成；本机提交 Grok 任务、抠像、渲染 Remotion 和检测成片。仓库不包含任何账号登录状态或 SSH 私钥。

这次迁移没有重新生成付费视频，也没有修改原生产目录。通用抠像/检测后端与旧 Mac 后端不同，需要新电脑做一次校准和人工预览；不能把“代码运行成功”理解为“新片已验收”。

## 1. Windows 安装

建议使用 **Windows + WSL2 Ubuntu 24.04** 运行整套命令，避免 Grok CLI 和降噪依赖在原生 Windows 上的差异。项目放在 WSL 的 Linux 文件系统内，PNG 序列读写更合适。安装 WSL 涉及系统设置，由电脑使用者完成。

PowerShell（只安装 WSL 时使用）：

```powershell
wsl --install -d Ubuntu-24.04
```

以下在 Ubuntu 终端执行。安装 Git、Python 3.11 或 3.12、Node.js 22、FFmpeg（必须同时有 `ffprobe`）、OpenSSH 客户端；Node 请通过你使用的官方安装方式安装。渲染中文需要字体，例如 `fonts-noto-cjk`。

```bash
sudo apt update
sudo apt install git python3 python3-venv ffmpeg openssh-client fonts-noto-cjk
# 确认 node --version 是 22.x；确保 npm 可用
node --version

git clone https://github.com/am345/jingchengenglish.git
cd jingchengenglish
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[media,portable,qa]'
npm install --global pnpm@11.25.0
pnpm --dir remotion install --frozen-lockfile
jc init-config
jc doctor
```

原生 Windows 也可以运行控制脚本和通用 ONNX/Remotion 后端：Python 3.11、Node 22、FFmpeg 和 OpenSSH 加入 PATH 后，创建 `.venv`，使用 `.venv\Scripts\python.exe -m pip install -e ".[media,portable,qa]"`，命令入口可用 `.venv\Scripts\python.exe -m jingcheng`。Grok Build 的安装/平台支持以你的账号安装入口为准；本仓库没有替你打包 Grok，也没有验证原生 Windows 的完整付费生成链路。降噪建议在 WSL/Linux 执行。

不要复制旧电脑的 `.venv`、`node_modules` 或 Mac 二进制；让依赖在新电脑重新安装。

## 2. 配置一次，不改脚本

编辑 `config/local.toml`，该文件已被 Git 忽略。

| 配置 | 含义 |
|---|---|
| `paths.assets` | 私有人脸、音色、音频、模型、PDF 的目录 |
| `paths.runs` | 每次生成、抠像、渲染和 QA 的输出目录 |
| `tools.*` | 命令名或本机可执行文件路径 |
| `servers.voice` / `runtime` | SSH 别名、端口、远端 Python、CosyVoice 与权重目录 |
| `servers.storage` | 成片服务器和存储根目录 |
| `grok.*` | 控制模型、并发、单批预算、子进程代理和就绪状态 |
| `matte.*` / `qa.*` | 抠像、人脸检测、模型哈希和校准记录 |
| `render.font` | 可选的本机授权字体；不把商业字体传进仓库 |

**本机配置中的相对路径统一相对 TOML 文件所在目录**，不依赖启动命令时的工作目录。支持 `${VARIABLE}`，变量未设置会报错。Windows TOML 建议使用正斜杠，或单引号字符串。远端路径是服务器自己的绝对 POSIX 路径。

课件 JSON 用 `asset:audio/master.wav`、`run:lesson04/matte/G01` 指向配置中的素材/输出根目录。移动项目或把大文件放到其他盘，只改配置即可。命令行 `--config` 放在子命令之前：

```bash
jc --config config/local.toml doctor
```

`doctor` 只检查本地依赖，不代表登录、服务器连通或成片质量已经验证。

## 3. 恢复交接包、连接服务器

[Windows 交接包](handoff/jingcheng-windows-setup.zip) 已随仓库提交，`git clone` / `git pull` 后即可取得，约 59 MB；[SHA-256 校验文件](handoff/jingcheng-windows-setup.zip.sha256) 与包放在一起。

在仓库根目录，用刚安装的 Python 解压：

```bash
python -m zipfile -e handoff/jingcheng-windows-setup.zip .cache/windows-setup
```

阅读 `.cache/windows-setup/README.md`，将其中的 `config/local.toml` 和 `assets/` 复制到仓库对应目录。首次配置可替换 `jc init-config` 的空模板；已有配置请合并本机路径和代理设置。包内的 `ssh_config` 只合并 Host 条目，不覆盖自己的整个 SSH 配置。

包内包含 R6/R2 WAV、音色 preset、SyncNet 与 DeepFilterNet3。CosyVoice 基模型和微调权重仍在 L40S。SSH 私钥、密码及 Grok 登录状态不在包里，需要在新电脑单独配置；人物参考、笔记 PDF 和具体课程时间线也需另行准备。

参照 [服务器与私有素材](docs/servers.md) 配置 SSH。两个角色分别是：

- `jingcheng-voice`：已有 L40S / CosyVoice3 环境，运行音色推理；不在 Windows 重训权重。
- `jingcheng-storage`：保存成片、私有素材及校验清单。

```bash
jc server-check voice
jc server-check storage
```

本流程使用 **R6 表演参考 + R2 声学参考 + refined LLM/flow 权重**，普通 CosyVoice 权重不能替代已确认的建国音色。交接包已提供两段参考和精简基准；若改从另一份旧基准迁移，可用下面工具剔除旧电脑路径：

```bash
python tools/import_voice_preset.py /path/to/existing/preset.json
```

把原 `preset.json` 和两段参考 WAV 放在同一目录后导入。模型权重继续留在 L40S，实际路径由 `servers.voice.runtime` 指定。也可根据真实服务器清单恢复素材：

```bash
jc assets pull private/assets-manifest.json
jc assets pull private/assets-manifest.json --execute
```

公开的 `examples/asset-manifest.json` 只是格式示例，必须换成真实字节数和 SHA-256。账号/私钥单独在新电脑配置，不放入素材包。

## 4. 先跑一个无需私人素材的课件示例

```bash
jc render examples/courseware-demo.json
jc render examples/courseware-demo.json --execute
```

输出 `runs/courseware-demo/render/film.mp4`，3 秒、72 帧，无声，用来检查中文字体和 Remotion 安装。重跑时换一个 `id`，脚本不会覆盖已有成品。

## 5. 正式生产顺序

### 配音 → 降噪 → 试听

先复制 `examples/voice-job.json` 到 `private/`，填完整教学文本和新的任务 ID。一个 case 生成一个完整讲解段；服务器按配置分组并发。

```bash
jc voice launch private/voice-job.json
jc voice launch private/voice-job.json --execute
jc voice status lesson04-audio-v1
jc voice fetch lesson04-audio-v1
jc voice fetch lesson04-audio-v1 --execute
```

`launch` 默认只检查素材和计划；`--execute` 才会上传并运行。结果下载到 `runs/<job_id>/voice-results/`，状态不等于音频已接受。失败后先查看状态/日志，不自动重复提交。

可选降噪需要 `python -m pip install -e '.[denoise]'`，并配置已有 DeepFilterNet3 模型目录：

```bash
jc clean-audio path/to/raw.wav path/to/clean.wav
```

保留 24kHz 单声道样本数；只有低电平开头适合时才做额外稳态降噪，补偿滤波器延迟，并检查整段/四分段相关延迟。听感仍须复核。拼成 90 秒母带时只安排真实停顿和课件间隔，**不拉伸语速、不改内部停顿去迁就画面**。正式视频生成前试听通过，并把母带哈希写入清单。

按 `examples/audio-timeline.json` 列出每个完整音频的源哈希和 `start_sample`（秒数 × 24000），用静默填充明确安排的段间间隔：

```bash
jc audio-master private/audio-timeline.json assets/audio/lesson04-master.wav
```

所有完整片段的 PCM 样本原样复制；重叠、超出 90 秒、采样率不一致都会拒绝。该命令不代表整课试听通过，模板也需要补齐所有段。

### Grok 生成完整连续镜头

安装并登录你账号提供的 Grok Build：`grok --help`、`grok login`。账号验证由使用者完成。检查当前 `reference_to_video` 参数和隐私状态，再把配置的 `schema_confirmed`、`privacy_ready` 设为 `true`；脚本不修改账号隐私开关。

复制 `examples/grok-batch.json` 到 `private/`，按**已经试听的母带**重写发声时间、指向坐标和参考图，填 `approved_audio`。示例中的时间不能直接当成实际配音时间。

```bash
jc grok private/grok-batch.json
jc grok private/grok-batch.json --execute
```

- 当前适配器：整数 1–15 秒、16:9、720p/480p、首尾帧、至多 4 个内部关键帧，关键帧在 1/3 秒网格。新机器必须核对当前 CLI schema。
- `grok-4.7` / `high` 是 CLI 控制模型参数，不能据此推断视频后端版本。
- 本路线仍不向 Grok 输入母带。提示词安排发声，最终使用已确认配音，必须检测实际导出。
- 同一个教学片段完整生成一次；连读多个单词时朝黑板读完整组再回头。不要按单词拼接多个候选。
- 并发和总生成秒数有上限；CLI 超时或结果不明时保留记录，不自动重试收费请求。

### 抠像 → Remotion → 90 秒成片

```bash
python tools/fetch_model.py rvm
python tools/fetch_model.py yunet
jc matte runs/lesson04-video-v1/grok/G01-c01/native.mp4 runs/lesson04-video-v1/matte/G01-c01
```

Windows/Linux 默认 RVM ONNX，仅使用 alpha，保留输入 RGB；模型逐帧传递循环状态。Mac 可配置 Vision，编译方式见 [技术说明](docs/workflow.md)。两者边缘效果需要对照预览。

复制并补全 `examples/lesson-template.json`，为全课添加镜头。它目前只有第一段示例，**不是可直接交付的完整课程**。整课人物位置/大小只有一个配置；每段只允许连续源帧，禁止变速、补帧、循环、嘴部重绘。课件占满画面只用于预先设计的段间停顿。指定授权字体后检查文字位置与手指位置。

```bash
jc render private/lesson.json
jc render private/lesson.json --execute
```

正式母带必须已经是 90 秒；渲染器不会自行裁语音、加速、填声音或延长人物。成片、源帧证据和时间线写入该次任务的 `render/` 目录。

### 冻结窗口 → 实际成片检测 → 归档

检测模型使用既有 `syncnet_v2.model`，放在配置的路径下；`doctor` 校验权重哈希。**在看候选分数之前**，按已确定的完整讲解段准备 `timeline.json` 并冻结窗口：

```bash
jc qa-freeze private/timeline.json private/quality-gate.json
# 把上一条返回的真实 SHA-256 代入下面命令
jc qa -- --timeline private/timeline.json --gate private/quality-gate.json --gate-sha256 ACTUAL_SHA256 --film runs/lesson04-film-v1/render/film.mp4 --mode final --fail-on-gate
```

每次都检测最终 AAC 和实际 PTS。要求：**所有冻结窗口可计算、每窗绝对估计偏移 ≤120ms、全片平均绝对估计偏移 <60ms**。边界/缺脸窗口不能丢弃；检测是 40ms 网格的估计，还需听首尾词、看手势和接缝。换人脸后端后，没有对应真人与已知位移校准记录就不会报告最终通过。详见 [口型验收与校准](docs/quality.md)。

```bash
jc archive runs/lesson04-film-v1/render/film.mp4
jc archive runs/lesson04-film-v1/render/film.mp4 --execute
```

上传后核对远端字节数和 SHA-256，保存本地 `.remote.json` 指针。**不自动删除本地文件**。

## 制作 Skill 与维护

项目内的 [.agents/skills/jingcheng-video-production/SKILL.md](.agents/skills/jingcheng-video-production/SKILL.md) 包含本工作流，可在支持仓库技能的客户端直接使用；明确调用：`$jingcheng-video-production`。其中记录了完整片段生成、指黑板、禁变速、音频先审、局部问题整段重生和最终复测。

```bash
python -m unittest discover -s tests -v
pnpm --dir remotion run typecheck
```

GitHub Actions 对 Linux / Windows 的控制代码运行测试；不登录账号，不花视频额度，不连接生产服务器。实际验证记录见 [验证范围](docs/validation.md)。第三方来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。
