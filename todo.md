# TODO

仅工作日分享。

1. 手撕 OpenClaw 上下文管理工程源码
   - 明日优先推送
2. 大模型的共享概念空间
3. 大模型以后可能写二进制而不是 Python
4. AI 时代的焦虑如何化解
5. CC 进阶 ask-question 和 todo list 的设计

---

## 2026-03-16 今日分享

### 边缘计算：端侧小模型与云端大模型的算力调度

#### 核心问题
端侧模型（如 Gemini Nano、Llama Edge）如何在本地与云端大模型之间动态协作？"推理卸载"面临哪些工程挑战？

#### 大纲
1. **端侧小模型的技术现状与产品化**
   - Gemini Nano: Pixel设备部署，2-4B参数
   - Llama Edge: WebAssembly边缘方案
   - Apple Intelligence: 端侧+云端Private Compute Cloud
2. **三种主流混合推理架构拆解**
   - LoRA Routing
   - Speculative Decoding
   - Cascaded Inference
3. **"卸载决策"——下一个兵家必争之地**
   - Model-as-a-Judge新架构
4. **隐私与效率的终极博弈**
5. **我的判断：未来3年的演进路线**

建议时长：8-10分钟 | 难度：中高级
