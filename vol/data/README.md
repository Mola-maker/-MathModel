# 竞赛数据文件目录

将题目附带的数据文件（.xlsx / .csv）放在这里，AI 的 EDA 和建模代码会自动读取。

## 使用方法

1. 将 .xlsx 或 .csv 文件直接复制到本目录
2. 运行 main.py，EDA 阶段会自动扫描本目录下的数据文件
3. 代码中使用相对路径读取：
   pd.read_excel("vol/data/你的文件名.xlsx")

## 文件命名建议

- 保持原始文件名（和题目附件一致）
- 多个附件直接都放进来，AI 会逐个分析

## Docker 容器路径

容器内路径：/workspace/vol/data/
主机路径：E:/mathmodel/vol/data/
系统会在运行前自动 docker cp 同步。
