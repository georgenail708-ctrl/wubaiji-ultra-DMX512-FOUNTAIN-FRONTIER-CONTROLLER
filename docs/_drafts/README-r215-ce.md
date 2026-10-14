# R215 未完成 CE 候选源码

用户将最终计算层深度贯通交给 dot。本目录保存此前未安装、未验收的工作
源码，避免只留一个“待完成”描述而丢失可接手的实现。这里不属于生产模块，
不能加入默认导入、直接解除 Pending 或作为全CE已完成证据。

- `p10-r1-owned-job01/candidate03/native`、`candidate04/native`、
  `candidate05/native`：R6依赖的原始/R4/R5源码，保持原字节。
- `p10-r1-owned-job01/candidate06_D/native` 与 `READ_READY_G07_R6.md`：
  实际Main07采用、job/compile/cancel/destroy/frame/step组成。context仍Pending，
  publishable=0；**整体未编译、未执行、无默认接入**。
- `p10-g0-main06`、`p10-g0-main07`：原始及bounded Main网格。其组件原生控制与
  ABI已验证，不能转移为完整R6验证。目录层级保留相对include关系。
- `p10-r1-fi06`：genuine原始causal payload/tree/history/getter候选；读HANDOFF。
- `p10-r1-fi07_context`：用户改为handoff时保存的部分raw/arena/scalar/count/
  allocate/sort源码。没有真实context装配、fixedsupplier、R6patch或controls。
- `p10-runner-complete-candidate07_create`：未应用的创建producer、消费者补丁、
  准入/布局/预算及50个未运行控制；详见CAPABILITY/HANDOFF。仍拒绝空来源表，
  不要默认安装。其旧02–06依赖可从原工作区的同名研究目录取得。

仅复制作者自己的文本实现及交接，不携带vendor DLL/反编译材料、真实show、
缓存、构建二进制或其它资产。原工作区路径为
`.research_cache/r215/<同名目录>`；完整状态正本仍在
`ai-memory/R211-K2-CLOSURE-2026-10-10.md`。本轮已验收功能和剩余要求以
`docs/r215-basic-closeout-and-ce-handoff.md`为准。

首次深度接入先核对实际当前产品和dot的后续提交，完成source/binder/context/
FI/owning结果块，然后恢复资源保护下的独立原版、真实工程等价和冷暖性能
验证。不要替换原始期望、修补原show或把foreign全原版一次计作全CE。
