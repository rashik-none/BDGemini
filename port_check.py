import socket, time

HOST = 'brd.superproxy.io'

for port in [22225, 33335, 24000]:
    start = time.time()
    try:
        s = socket.create_connection((HOST, port), timeout=5)
        elapsed = time.time() - start
        data = b''
        s.settimeout(2)
        try:
            data = s.recv(4)
        except Exception:
            pass
        s.close()
        hex_data = data.hex() if data else 'none'
        print(f'Port {port}: OPEN in {elapsed:.2f}s, first bytes: {hex_data}')
    except Exception as e:
        elapsed = time.time() - start
        print(f'Port {port}: FAILED in {elapsed:.2f}s => {e}')
