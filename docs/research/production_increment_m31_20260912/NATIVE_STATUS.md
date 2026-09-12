# M31：原生产自动接续第5批，计时仍等待安全边界

观察时间2026-09-12 14:22:35 UTC，相对M30 14:09:26 UTC。仅本次新窗口，不重读M30已闭合RESULT或重复8项测试。

## 本次六项产出

1. 现行正式合格唯一几何 **6904**，15GHz记录6904，引用宽频记录386624。正式头006904 SHA `cfe2f2bedaf6fde43fb7c6f84735458541e54faf695236d630e20281baf3a3d9`，189924 bytes。
2. 新终态53：第4批最后32=31 fresh EMX+1候选执行失败；第5批前21=15 fresh EMX+6解析失败未派发。合计46 fresh（train28/validation7/test11）、25 core15（train18/validation4/test3）。新正式20=19个本窗新core提交+M30待提交DOE156/test补登1；历史正式新增0。不把25core提前全部计为正式。
3. M30待提交DOE156/test已在14:09:29.404010Z写为006885，SHA `df84786650b1a1f3487791f5d9f000fa22fef54e1e86c36d3db79befaf90cd5a`，186728 bytes；它不是本次新EMX。当前第5批仍有6个core待提交，ID在OBS新RESULT与formal差集中，未虚报完成。
4. 实际原生EMX并发 **26**，EMX许可26、Cadence许可3；等待EMX8/Calibre8/Cadence4。申请与容量48，不等于实际48；当前资源只诊断允许另外3份共享双CPU工具许可，未强占资源。
5. 第4批14:14:20.955027Z闭合256请求，终态BATCH SHA `d118226b703b52dea569f121e85641cb473d6713c06ee7e012b4f54f0ffb83f2`，109277 bytes；旧owner1052218和metadata1052221已自然退出。原continuation1041957/start402286569仍活，自动启动第5批owner3581696/start402643948、metadata3581701/start402643949。第5批release SHA `575a2e097dbab5c64ee2d86ebe7160e1a29aa47ddff764fce9c6bcfa5ca006bc`，17600 bytes。没有重跑Golden或旧批次。
6. 计时安装器3297923/start402602726、PPID1仍活；INSTALL与FAIL均无，旧runtime23ed仍活动，首正常permit计时字段尚无。**TIMING_DEPLOYMENT=WAITING_FOR_SUPPORTED_BOUNDARY**。本次原continuation先接续了第5批，安装器未取得该次安全交接窗口；不能声称“已装”或保证下一窗口一定取得。既有入口不提供配置热加载；本次不冻结健康子链或为取计时试跑。

## 资源与失败边界

第5批资源14:22:20Z全部检查PASS，独立健康7/5；资源SHA `79c32bc8be797c7c3df270527b4c0db01ba946f89e9232be888f4f948af8569f`。可用磁盘457694666752 bytes，研究挂载完整配额UNKNOWN。当前批预算实际329867264+投影3865198592小于5368709120，未重置旧批预算或放宽保护。

本窗第4批 TRAIN_NEIGHBORHOOD-063 的EMX阶段失败保留：RESULT SHA `348e6124af9ceca8732ba8186db5f4f879fb6146f8bcbcc74e7c2593950291f4`，9481 bytes；emx_PROCESS pin `86dc757030f1e0430057b0015fee107f588a6905f6ed46a108ecef6e77a32ab1`，2493 bytes。此轮只读取新RESULT，未追读其log；具体失败原因UNKNOWN，不推断与DOE021同因，不换候选、不重试、不将单个失败升级为全队列停止。

本次观察 `increment_20260912T142234843746Z/OBSERVATION.json` SHA `adae9d180af1df250a5248ef86fc9bf4fa35f9fc823b4156d2caef7521ed9051`，1224452 bytes。观察脚本只在本来已读取的事件数组中返回第一条已有timing事件，不额外读取事件窗口、启动探针或记录生产事件。

## 历史strict112四文件交付

严格执行研究请求 SHA `a32c7fb268a5675e7be7cc4add6b56b5e0d1314ba548c2081b3eeff9c05a9898`：只读4个精确已知常规文件，总255309 bytes，4/4实际SHA匹配，读取前/句柄内/读取后stat稳定。未读取GDS/S4P、未扫描目录、未跟随其他artifact pin、未做特征提取、未正式写账。

交付目录 `strict112_four_20260912T142344723294Z`：
- `authority_csv.csv` SHA `391f4ece1b455de01d84c0f45c16e1bf2360773ce9613c383ac007019be2e9f8`，175292 bytes。
- `historical_generation_config.yaml` SHA `4bd1492ba9dc0e429de31c16e9be86ace5df706477dfeacd52f1dab19c4c04aa`，3285 bytes。
- `drc_index.csv` SHA `27d614de99d3ec6f14e96fb3de24b077d652a54c8924f7ef80cd492384bee3fd`，73068 bytes。
- `drc_summary.json` SHA `0b92ffda4e8da617e3df6d9c042954957dcfa95b166a1a3946abdf1e7596b187`，3664 bytes。
- `RECEIPT.json` SHA `1db6e971c7ecd8c2063d26d3965974c96af61da5ccbcebf111009ba36a05b686`，5279 bytes。

已将实际路径先交研究主任务。原benchmark_arm/pair_id/split和原字节保持，不宣称P215子集或“112已新合格”；selected_member=null，下一步由研究端基于真实索引限定单成员资格核验，生产不等待此审查。

## 精确下一入口

保留唯一当前第5批执行与提交；既有安装器在其5400秒有界等待内继续尝试 `BATCH_TERMINAL_AND_OWNER_EXIT` 且无metadata/其他子进程的真实边界，不新增installer、不原样重启、不重复AI轮询。下一次明确请求或边界事件只核该installer的INSTALL/FAIL和实际PID；若已安装，只读下一正常许可第一条timing。历史工作只等研究侧指定的已索引单成员，不扩展到112全量工件。任务仍非100K完成，不启动NN或旧广域200K。
