import os
import requests
import time
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()

# ================= 配置区域 =================
# 🔴 请修改这里的端口为你VPN的端口 (Clash通常是7890, v2rayN通常是10809)
PROXY_PORT = 7897 
PROXIES = {
    "http": f"http://127.0.0.1:{PROXY_PORT}",
    "https": f"http://127.0.0.1:{PROXY_PORT}",
}
# ===========================================

api_key = os.getenv('API_KEY')
api_secret = os.getenv('API_SECRET')
base_url = "https://api.binance.com"

print("-" * 30)
print(f"1. 测试网络连接 (通过代理 {PROXY_PORT})...")

try:
    # 1. 查询当前出口IP
    ip_resp = requests.get("https://api.ipify.org?format=json", proxies=PROXIES, timeout=10)
    current_ip = ip_resp.json()['ip']
    print(f"✅ 网络通畅！")
    print(f"🌍 你的当前出口 IP 是: {current_ip}")
    print(f"👉 请务必把这个 IP 添加到币安白名单！")
    
except Exception as e:
    print(f"❌ 网络连接失败: {e}")
    print("请检查：1. VPN是否开启 2. 脚本中的 PROXY_PORT 端口是否正确")
    exit()

print("-" * 30)
print("2. 测试币安 API 签名...")

# 2. 测试币安账户接口
timestamp = int(time.time() * 1000)
params = f"timestamp={timestamp}&recvWindow=10000"
signature = hmac.new(
    api_secret.encode('utf-8'),
    params.encode('utf-8'),
    hashlib.sha256
).hexdigest()

headers = {'X-MBX-APIKEY': api_key}
url = f"{base_url}/api/v3/account?{params}&signature={signature}"

try:
    resp = requests.get(url, headers=headers, proxies=PROXIES)
    if resp.status_code == 200:
        print("✅ 成功连通币安！账户权限验证通过。")
        print("你可以去运行机器人了 (记得把代理加到机器人代码里)。")
    else:
        print(f"❌ 币安拒绝访问: {resp.status_code}")
        print(resp.json())
        if resp.json().get('code') == -2015:
            print("👉 原因：IP未白名单。请把上面的 '当前出口 IP' 加到币安设置里。")

except Exception as e:
    print(f"❌ 请求出错: {e}")