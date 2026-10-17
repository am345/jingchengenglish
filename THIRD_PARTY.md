# 第三方来源

- `vendor/syncnet/SyncNetModel.py` 来自 [joonson/syncnet_python](https://github.com/joonson/syncnet_python)，固定源提交 `907c0b579c2e2d83f0eae1b2ac9e720cde4e5623`；随附原 MIT 许可。权重不放入仓库。
- RVM ONNX 权重按 [RobustVideoMatting v1.0.0](https://github.com/PeterL1n/RobustVideoMatting/releases/tag/v1.0.0) 下载，来源项目为 GPL-3.0；本仓库不复制其模型代码/权重。适配器使用原 RGB 与预测 alpha。[官方输入输出说明](https://github.com/PeterL1n/RobustVideoMatting/blob/master/documentation/inference.md)。
- YuNet 来自 [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)，模型固定提交、单独下载和校验；保留上游使用条款。
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) 与 [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) 由本机/私有服务器安装，参考音频和微调权重不公开。
- [Remotion](https://www.remotion.dev/docs) 通过包管理器安装，使用条件以其许可证为准；本仓库不附带商业字体。

公开代码不代表原人物、声音、课件 PDF 或素材的权利一并开放。
