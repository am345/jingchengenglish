# 服务器与私有素材

公开仓库只有角色别名，真实主机/端口/用户名和密钥位置写在 `config/local.toml` 或本机 SSH 配置。

## SSH

把 `config/ssh_config.example` 的条目合并到自己的 `~/.ssh/config`。原生 Windows 是用户目录下 `.ssh/config`，WSL 是 Linux 用户目录下 `.ssh/config`，二者不是同一份。每台电脑单独导入/授权密钥，私钥不进 Git。

先手动 `ssh jingcheng-voice`、`ssh jingcheng-storage`，核对可信来源提供的主机指纹并建立 known_hosts。自动脚本使用 `StrictHostKeyChecking=yes` 和 `BatchMode=yes`，不会绕过主机验证。传输要求 OpenSSH 9+ 的默认 SFTP `scp`。远端是 Linux，必须有 Python 3.10+（本机控制器需要 3.11+）。

## L40S 配音服务器

`servers.voice.python` 指向**已有可工作的 CosyVoice3 虚拟环境 Python**。`runtime` 的四个路径分别是 CosyVoice 源码、原始模型目录、微调 LLM、微调 flow。原始 hift、tokenizer 和 YAML 保留基准版本。worker 会对照私有 preset 校验记录的权重、原始模型和参考 WAV 的哈希。

并发 `2` 表示两个独立 worker 各加载一套模型；按显存余量降低为 `1`。每个 worker 默认最多 12 个完整 case，禁止用提高并发代替排查显存。当前工具复用现有推理环境，不会部署 CUDA、不训练、不改系统 Python。

`jc voice launch` 上传到 `<job_root>/<job_id>/`，目录已存在就失败。每组有 `qa/generation.json`、`qa/worker.log`、`raw/*.wav`。不要在另一个终端重复同一 ID；先 `jc voice status`。任务是远端后台进程，关闭本机窗口不等于停止任务。

## 语音私有数据格式

`voice.preset` 只需要：

```json
{
  "schema_version": 1,
  "id": "jianguo_v3_classroom_reference",
  "base_model": {"family":"CosyVoice3", "original_asset_sha256": {"llm.pt":"真实完整哈希","flow.pt":"真实完整哈希","hift.pt":"真实完整哈希","cosyvoice3.yaml":"真实完整哈希"}},
  "checkpoints": [{"module":"llm","sha256":"真实完整哈希"},{"module":"flow","sha256":"真实完整哈希"}]
}
```

保留原基准记录的其余 tokenizer 等哈希。`voice.references` 格式是 `{"references":[{"id":"R6","input_filename":"R6.wav","sha256":"…","prompt_text":"参考音频的准确原文"}, {"id":"R2", …}]}`。WAV 在该 JSON 所在目录。使用导入工具即可从旧基准产生这两份新文件，不需要手改旧机器路径。

## 成片服务器

`servers.storage.root` 是自己的媒体根目录。`jc archive` 写到 `final-videos/<sha256>/film.mp4`，先临时上传、流式核对，再归位，最后复验。相同内容可重复归档，不覆盖不同内容。把 `.remote.json` 与课程清单一起保留。

恢复清单每项包含：`path`（本机 assets 下相对路径）、`remote_relative_path`（远端 root 下相对路径）、`bytes`、`sha256`。`jc assets pull` 下载校验后归位，若本地同名文件不同则拒绝覆盖。

私有素材并不会因为发布仓库而自动上服务器。迁移时需要安全传递：精简音色 preset、R6/R2 WAV、人物参考/首尾帧、标准笔记 PDF、已确认音频、SyncNet/降噪权重、可用字体以及真实素材清单。L40S 上的模型权重不用下载到 Windows。

代理只配置在 `grok.http_proxy/https_proxy/all_proxy`，仅传给 Grok 子进程。不要为了新仓库改原生产项目或全局网络配置。
