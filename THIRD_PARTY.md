# 第三方来源

- `vendor/syncnet/SyncNetModel.py` 来自 [joonson/syncnet_python](https://github.com/joonson/syncnet_python)，固定源提交 `907c0b579c2e2d83f0eae1b2ac9e720cde4e5623`；随附原 MIT 许可。既有检测权重随获准公开的 Windows 交接包提供，哈希记录在包内 manifest.json。
- RVM ONNX 权重按 [RobustVideoMatting v1.0.0](https://github.com/PeterL1n/RobustVideoMatting/releases/tag/v1.0.0) 下载，来源项目为 GPL-3.0；本仓库不复制其模型代码/权重。适配器使用原 RGB 与预测 alpha。[官方输入输出说明](https://github.com/PeterL1n/RobustVideoMatting/blob/master/documentation/inference.md)。
- YuNet 来自 [OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet)，模型固定提交、单独下载和校验；保留上游使用条款。
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) 与 [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet) 由本机/私有服务器安装。获准公开的交接包包含两段音色参考与 DeepFilterNet3 权重；CosyVoice 微调权重仍留在服务器。
- [Remotion](https://www.remotion.dev/docs) 通过包管理器安装，使用条件以其许可证为准；本仓库不附带商业字体。

公开代码不代表原人物、声音、课件 PDF 或素材的权利一并开放。
