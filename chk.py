import urllib.request
import ssl

proxy = 'http://brd-customer-hl_fc20e5ef-zone-residential_proxy1:diza2wz9o9bp@brd.superproxy.io:33335'
url = 'https://geo.brdtest.com/welcome.txt?product=resi&method=native'

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({'https': proxy, 'http': proxy}),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
)

try:
    print(opener.open(url).read().decode())
except Exception as e:
    print(f"Error: {e}")
