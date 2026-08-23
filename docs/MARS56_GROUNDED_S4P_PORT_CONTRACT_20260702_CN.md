# MARS56 Grounded S4P Port Contract

## 端口连接方式

最终 EMX 输出仍然是四端口 `.s4p`，导出的 RF signal ports 只有：

- `P001`: M10/primary top horizontal terminal
- `P002`: M10/primary bottom horizontal terminal
- `P003`: M9/secondary top horizontal terminal
- `P004`: M9/secondary bottom horizontal terminal

每个 RF signal port 都有自己的局部 M5/shield ground reference：

- `P001_G`, `P002_G`, `P003_G`, `P004_G`

竖直 power-line / center-tab 的四个端点不是导出的 RF signal ports。它们是 M5 ground-only reference labels：

- `P005`, `P006`, `P007`, `P008`

因此版图里用于解释端口连接的物理 port/reference label 总数是 12：

- 4 个 RF signal labels: `P001-P004`
- 4 个 RF local ground labels: `P001_G-P004_G`
- 4 个 vertical M5 ground-only labels: `P005-P008`

## 为什么最终还是 `.s4p`

EMX manifest 只把 `P001-P004` 写成 Touchstone ports，所以输出是 `.s4p`。每个 RF port 只引用自己的局部地标签 `P00x_G`。`P005-P008` 不进入 Touchstone port list，也不重复加入四个 RF port 的 ground label set；它们通过独立的 via stack 物理连接到公共 M5 地。这避免同一标签被 EMX 分配给多个 ground group。

## 竖直导线如何接地

S4P 模式下，每个竖直端点都有一个可审计的 M5 ground-stitch stack：

- M10 侧端点从 metal10 通过 via9/via8/via7/via6/via5 接到 metal5。
- M9 侧端点从 metal9 通过 via8/via7/via6/via5 接到 metal5。
- 每个 stack 的中心坐标、footprint、metal/via layer 都写入 `power_line_8port_geometry.json` 的 `power_line_ground_stitches`。

## ADS/物理特征提取

后处理按四端口 transformer 读取 `.s4p`：

- Primary differential pair: `P001-P002`
- Secondary differential pair: `P003-P004`
- 物理特征提取：`Lp`, `Ls`, `Q`, `M/Kw`，频点为 5-60 GHz，0.5 GHz step，共 111 点。
