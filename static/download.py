import urllib.request

# 要下载的CSS文件URL
url1 = 'https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/css/bootstrap.min.css'

# 保存文件的路径和文件名
filename1 = 'bootstrap.min.css'

print(f"正在从 {url1} 下载文件...")

try:
    # 使用urllib库下载文件
    urllib.request.urlretrieve(url1, filename1)
    print(f"✅ 下载成功！文件已保存为当前目录下的 '{filename1}'")
except Exception as e:
    print(f"❌ 下载失败，发生错误: {e}")



# 要下载的CSS文件URL
url2 = 'https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/js/bootstrap.bundle.min.js'

# 保存文件的路径和文件名
filename2 = 'bootstrap.bundle.min.js'

print(f"正在从 {url2} 下载文件...")

try:
    # 使用urllib库下载文件
    urllib.request.urlretrieve(url2, filename2)
    print(f"✅ 下载成功！文件已保存为当前目录下的 '{filename2}'")
except Exception as e:
    print(f"❌ 下载失败，发生错误: {e}")