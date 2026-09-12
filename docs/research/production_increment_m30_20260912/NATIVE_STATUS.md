# M30：准入计时已测试并接入边界等待，尚未生效

## 实际变更

- 新隔离运行包只改变 `fixed48_runtime.py` 和 `run_fixed48_native.py`。`AdmissionTiming` 使用 monotonic，`Manager.acquire/_acquire` 累计总耗时、尝试次数、dispatch锁等待/持有秒数和实际分支拒因计数。正常许可只在已有 `FIXED48_TOOL_PERMIT_GRANTED` 加一个汇总；失败先仅存内存，由已有 `NEW_RESULT_CAPTURED` 或 owner终态事件输出一次。事件落盘失败仍抛原异常，未发布计时保留。
- 汇总的明确截点在已有事件写入之前；锁持有指标包含当时仍持有的区间，但不含尚未发生的该事件fsync和最终unlock。没有冒充未来完整区间，也没有将这些秒数当作原生EMX运行时长。
- 原门禁调用顺序、异常、2秒重试间隔、许可撤回、8/8/48容量、pipeline64、双CPU、5健康样本、256/12h/5GiB预算及物理代码不变。没有新增逐轮日志、资源探针、控制器或优先队列。
- 安装器复用既有 `install_at_boundary.py` 的全批终态等待。只补边界时复制晚到的冻结输入注册记录，冲突拒绝、完整当前profile/terminal先验证，再允许退役已无子进程的旧continuation。活动owner、求解器和metadata不被signal。该复制修补的是前次已证实的registry传输遗漏，未改registry消费者或物理QA。

## 验证和实际等待入口

远端新包：
`/volumes/research-localdata/ywang3652/eucap15_native_owner_20260909T062500Z/continuous_admission_timing_20260912T140817241525Z`

- 新runtime SHA `fdc74cbfeb62c2bd67198ccedbef542dbd3dba413dbea0d3cacb9461c97cb4bd`，18193 bytes；新owner SHA `08cbb052c6968f4387b3e4456c98cda0154e2c74f6ca4e7c7d1172c09b1d3ff8`，9981 bytes。其他runtime逐字节匹配父部署，continuation仍1584。
- MARS批准Python真实路径 `/usr/bin/python3.12`，8项专项测试PASS，0.057s；只使用临时合成输入。覆盖monotonic计算、正常准入、3轮无容量且无轮询日志、过期、撤回、终态仅输出一次、事件写失败、晚到registry复制与冲突拒绝。不是旧638项、没有原生工具或QA重复执行。
- `TARGETED_TESTS.json` SHA `f16a8d9f4bce4a83368b6cee33d44ddb79d37b638eb4cae5cfc5acecbe77b6ef`；`SOURCE_PREFLIGHT.json` SHA `f8ad2b302a95c7a32bbd58f4136a8329a6f2d49565ebd749865d90925cb7e8a1`。真实profile/Python/源码、现有owner/metadata/continuation身份、锁fdinfo及已有资源PASS已核。
- `STAGED.json` SHA `603863071d1698adee487ac0a411e696e868ca6e9ff62f72d5788dbecbb76f2d`；`BOUNDARY_INSTALLER_START.json` SHA `18ba5300fa155476e66a0a1c862b1633c025610f78192f2d9b17c26715b0e951`。唯一边界安装器PID3297923/start402602726于14:08:18启动，14:09:26实际PPID1存活；复用PERMIT_RELEASE_UPGRADE锁，最多等待5400秒。
- 当前 `INSTALL_RECEIPT` 和 `INSTALL_FAILURE` 均无；**DEPLOYMENT=WAITING_FOR_BATCH_TERMINAL_AND_OWNER_EXIT，不是INSTALLED**。没有冻结正在等待的旧continuation来抢边界；自然窗口未获得时保留生产。无新的逐SHA批准要求。
- 六份实际新收据已只读运输：父目录 `scout14_metadata_20260912T140949307091Z/TRANSPORT.json`，SHA `1aada68eba4c40bbaebf31297a24bc6c4525b9b85bbdbb4c3e1f55f51d316192`，37527 bytes。

## 同时自然新增的生产

仅一次14:09:26Z新截面，相对M29 13:44:51Z：

- 新89终态=79 fresh EMX（train42/validation17/test20）+10解析失败未派发；新core40，正式新增39（train23/validation8/test8），历史新增0。
- 新core的DOE156/test尚待正式提交，不提前计数。现行正式6884、15GHz记录6884、引用宽频记录385504。
- 正式头006884 SHA `2d0c9ce093b4a08164db2d24fa104c9bbdd2679539a5b0e80510361252ffc4d6`，186832 bytes。
- batch4已224/256终态，尚未闭合；原owner1052218/start402287917、metadata1052221/start402287917、continuation1041957/start402286569均活。活动runtime仍23ed，计时尚未进入活动owner。
- 原生EMX实际3，许可EMX3/Calibre2；等待EMX11/Calibre15/Cadence1。健康44/5、已有资源全PASS；诊断additional各6共享CPU，不是各自独立的6。磁盘实际1947389952+投影2412158976在原5368709120预算内。
- 观察：父目录 `increment_20260912T140925780080Z/OBSERVATION.json` SHA `64807427e2788a076197b7df37cbae7f31b4b61858cd998fbec5cb7894cf6618`，1184773 bytes。旧RESULT正文0、旧正式前缀QA0、新探针0、观察信号0。不是重复M29同截面或旧事件窗口。

下一合法入口：既有安装器在真实全批终态、owner退出且metadata/其他子进程自然收尾后完成交接；随后从下一正常许可事件读取第一份timing即止。若5400秒未取得边界，安装器写失败并保留旧链，不能把等待报告成部署成功。本次无AI周期监控、无NN、无历史64派发、无并发测速或公开Git推送。
