import requests

urls = [
    ('https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/css/bootstrap.min.css', 'bootstrap.min.css'),
    ('https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/js/bootstrap.bundle.min.js', 'bootstrap.bundle.min.js'),
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

for url, filename in urls:
    r = requests.get(url, headers=headers)
    with open(filename, 'wb') as f:
        f.write(r.content)
    print(f"✅ {filename} 下载完成 ({len(r.content)} bytes)")