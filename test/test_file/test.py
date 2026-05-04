# 在 Python 交互环境或脚本里执行
import os
p = r"C:\Users\ASUS\Desktop\tmp\Secret\test\test_file\text.txt"
print(os.path.exists(p))      # True/False
print(os.path.isfile(p))      # True/False
print(os.listdir(os.path.dirname(p)))  # 列出所在目录所有文件