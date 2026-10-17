# 口型验收与校准

检测器使用既有 SyncNet v2 权重，SHA-256 固定为 `961e8696f888fce4f3f3a6c3d5b3267cf5b343100b238e79b2659bff2c605442`。从原生产私有模型文件恢复，不替换为名字相似但哈希不同的模型。

## 冻结规则

在查看候选分数之前准备 timeline，所有人物讲解段按顺序列出。示例：

```json
{"clips":[{"id":"G01","start_frame":0,"end_frame":240,"required_frames":240}]}
```

完整项目要列全，不只列通过的段。`qa-freeze` 生成并绑定 timeline 哈希、方法指纹和窗口清单。窗口长 1.6 秒、步长 1 秒；非整秒尾部在冻结时声明 40ms 网格上的尾窗，不根据测量结果挑窗。

分析网格 25fps、音频 16kHz、偏移步长 40ms、搜索范围 ±320ms。实际输入是最后的 24fps/1280×720 MP4 和其中 AAC，按实际音频块/视频帧 PTS 对齐，不能拿另一份 WAV 代替。取原视频最近原生帧只用于分析，不修改输出视频。

全片窗口平均绝对偏移严格 `<60ms`，每一个窗口 `≤120ms`。触及搜索边界、检测不到脸、音频 PTS 不完整或特征不足都保留为 unresolved，阻止通过。CSV 完整列出每窗。候选平均值只是描述值，全片平均必须重新测最终导出。

## 新后端必须校准

YuNet 和 Mac Vision 的框不一样，不能把两者的分数直接横向比较。先选真人教学原片，人工确认音画自然，保留原始文件；另外制作明确标注的诊断副本（例如已知 ±120ms、±240ms 常量偏移），只作检测校准，不作生产素材。检查：估计方向、相对位移是否恢复到一格 40ms 内、脸框是否稳定。若失败，调整检测/裁脸并重新冻结协议，不通过筛窗让它看起来通过。

`qa-freeze` 返回 `method_fingerprint`。在 `qa.calibration_record` 指定私有 JSON，只有实际复核后才填 accepted：

```json
{
  "method_fingerprint":"真实方法指纹",
  "accepted":false,
  "evidence":[
    {"file":"real-native/gate_report.json","sha256":"真实哈希"},
    {"file":"known-negative/gate_report.json","sha256":"真实哈希"},
    {"file":"known-positive/gate_report.json","sha256":"真实哈希"}
  ]
}
```

证据路径相对该校准 JSON。工具验证方法指纹、接受状态和至少三份证据哈希；人工评审负责确认真人基准、已知偏移和测量解释，不能只为解除门禁填 true。未校准仍可输出诊断数值，最终 `timing_gate_pass` 会为 false；`uncalibrated_timing_gate_pass` 仅描述数值门槛。

数值不是逐音素真值，也不是“人一定看不出”的保证。单独检查每段首尾词、英语长元音/辅音、手指到字的位置、转身过程和全部剪辑接缝。连续源证据来自渲染清单；导出的恒定帧率本身不能证明动作连续。
