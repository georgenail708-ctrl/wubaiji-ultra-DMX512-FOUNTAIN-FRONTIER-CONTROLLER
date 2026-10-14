# R215 本轮基本验收与未完成 CE 交接

本轮按用户最后确认的范围收口：达到可用、完成基本验收、本地提交并同步
GitHub；最终计算层的深度 CE 贯通由 dot 继续。此次不打包。

状态：`BASIC_ACCEPTANCE_PASS_CE_FINAL_PENDING`。此状态不能解释为全部计算进
CE、全部缓存问题彻底解决或完整工程渲染达到 10 秒。

## 已有实际结果

SNTC 菜单位于真实“启动网络”下面，名称为“开启SNTC同步”。打开后自动启动
接收，窗口提供明确开关并显示状态；隐藏窗口继续同步。接收采用 UDP8405，
使用真实 SNTC16 字节协议，时钟/播放/重复/过期策略在同一个安装的 CE DLL。
有效输入接管播放及指针并阻止本地覆盖；重复或停更保持位置和输出，过期后
用户的主动本地操作可接管。关闭、再开启与正常退出保留完整资源退场。

当前源码真实 Windows Main 基本用例通过：默认未开启时 controller/clock
资格 True；真实菜单、复选框、ruler 事件；真实 UDP 接收、外部 PLAY/推进、
fresh 本地拒绝、重复/停更、stale 主动接管、OFF/再次启用及 normal close。
读取六个完整512值/512掩码层检查点，收到五个真实回环 ArtDmx 包。
状态 `PASS_BASIC_REAL_MAIN_SNTC_NOT_FULL_CE`，保护器 raw0、退出树为空，
总运行9.7551176秒，任务峰值228241408字节。该时间是基本用例耗时。
原始日志在本地 `.research_cache/r215/p10-tc1-basic01/run09/result.json` 和
`basic-main-attempt09.log(.guard.jsonl)`；所有失败原始记录保留。

两种当前 CPU CE DLL 已实际重新编译，source build raw0/6.3347132秒；保留旧
DLL，未改旧成品。原始五个 controller 数值 body 的独立指纹均保持不变。
独立 Windows CI：PR13 head60efde18d7d88027dff1377b30f6dea51876235b，
run38489299887，SSE2/AVX2各71个协议/authority/transition测试通过，参考音频
待执行指令策略 C 控制通过。Main06/Main07原生控制和实际ABI也通过。

缓存批写/数据库回滚/真实写入线程连接关闭/私有目录清理以及 SNTC 失败资源
保留和重试的八个控制通过。批写保持原缓存格式与内存别名，不承诺跨原有
spill 前缀的整批原子性；调用者仍须拒绝不完整结果。该 API 尚未让新 R6
完整 producer 进入默认路径，也不构成所有缓存命中及增量语义的完整验收。

## 未完成项：dot 接手必须保留的边界

1. **整帧 R6 默认贯通未完成。** Main07 已进入私有 R6 源码的真实 ledger、
   bounded compile、phase8 grid采用、取消和部分退场；frame/step源码组成。
   context阶段仍是真正 Pending，`publishable=0`，没有默认安装、完整 ABI6
   binder 或完整 producer 的执行证据。禁止仅清除 pending 或强设 READY。
2. **FI/上下文/锚点与结果侧边数据未完成。** FI06有原始 causal map codec、
   树比较、getter/历史候选。FI07仅有 raw ABI、arena、scalar/count/allocate/
   sort基础，尚无 static/layout/filter/motor/runtime 的实际完整装配、固定
   `r6_fi_current_context` supplier、R6接线或独立控制。还需真实 source permit/
   token adoption、原始 chronology/prewarm、固定 transition sampler 和结果
   块拥有的 context/anchor/deps/旧payload。不能用空字典、常量true或前一帧
   代替真实来源与因果锚点。
3. **其它普通计算域仍有缺口。** shape/override/curve/ratio/manual phase/
   force/filter/audio供应链及全文件音频路径尚未完整贯通。A1/A2及Runner07
   的新源码候选没有默认接入或完整实际验收。Runner07创建候选覆盖init/v2/
   legacy/Panel/部分失败发布，但其50个控制全部未运行；鼻端、链长、其它
   materialize、运行参数、道岔和车队等仍未全部CE化。
4. **整工程速度没有本轮新成绩。** 之前11889帧记录为537.3838291秒，且只是
   timing-only、退出验收未全通过。没有新的等工作量冷暖AB/BA、完整原始工程
   等价、最终输出或“约10秒”证明。不要把安装/调用数、helper控制、此基本
   用例或原生构建时间写成完整渲染性能。

保存的候选与交接见 `docs/_drafts/README-r215-ce.md`。这些文件是未安装且
未验收的工作成果，不能自动纳入运行或构建。正常生产路径仍使用本轮实际
基本验收的产品源码。

## 接手顺序与资源

先核对 PR8、PR12、PR13 及当前分支，保留 dot 的后续修改，逐文件合并。
PR8 shortcut 修复仍独立；本轮不会替它强制合并或重置主分支。完整 CE 先
闭合真实 source/binder/context/FI/owning block，再接入已有 FrameStore 的
批写；Python承担模型/IO，不能逐帧递入已计算好的数值当作全CE。

所有资源作业使用已有 `tools/run_validation_guarded.py` 串行执行。此次采用
1.25GiB任务上限、物理/提交/磁盘各8GiB余量、数值线程1。用户此前已发生内存
耗尽；不能为测试并发整工程、原生构建或整套 pytest。保留 raw exit、失败、
取消与最终空进程树，不能把超时/杀进程当正常关闭。

两份原始show、旧包、既有dirty/staged/index以及所有独立期望保留。未来深度
验收可恢复完整原工程冷暖/缓存/实时及输出合同，但此次用户只要求基本验收。

参考：原有 FrameStore/DiskCache5.6.3 事务与关闭；CPython3.13.13 ctypes、
contextlib、threading；现有同CE时间码策略和 Windows Job 资源保护器。
