"""Get Bright Data CA cert from proxy CONNECT chain using pyOpenSSL."""
import socket
import base64
import ssl


try:
    from OpenSSL import SSL, crypto
    HAS_OPENSSL = True
except ImportError:
    HAS_OPENSSL = False

proxy_host = 'brd.superproxy.io'
proxy_port = 33335
user = 'brd-customer-hl_fc20e5ef-zone-residential_proxy1'
passwd = 'diza2wz9o9bp'
auth = base64.b64encode(f'{user}:{passwd}'.encode()).decode()

# CONNECT via raw socket
s = socket.create_connection((proxy_host, proxy_port), timeout=15)
req = (
    f'CONNECT accounts.google.com:443 HTTP/1.1\r\n'
    f'Host: accounts.google.com:443\r\n'
    f'Proxy-Authorization: Basic {auth}\r\n\r\n'
)
s.sendall(req.encode())
resp = b''
while b'\r\n\r\n' not in resp:
    resp += s.recv(1024)
print('CONNECT:', resp[:40].decode())

if b'200' not in resp:
    print('CONNECT failed')
    exit(1)

if HAS_OPENSSL:
    print('Using pyOpenSSL to get full chain...')
    ctx = SSL.Context(SSL.TLS_CLIENT_METHOD)
    ctx.set_verify(SSL.VERIFY_NONE, lambda *a: True)
    conn = SSL.Connection(ctx, s)
    conn.set_tlsext_host_name(b'accounts.google.com')
    conn.set_connect_state()
    # Drive handshake manually for blocking socket
    while True:
        try:
            conn.do_handshake()
            break
        except SSL.WantReadError:
            import select
            select.select([s], [], [], 5)
        except SSL.Error as e:
            print(f'Handshake error: {e}')
            break

    chain = conn.get_peer_cert_chain()
    print(f'Chain length: {len(chain)}')
    for i, cert in enumerate(chain):
        subj = cert.get_subject()
        iss = cert.get_issuer()
        print(f'  [{i}] Subject CN={subj.CN}, O={subj.O}')
        print(f'       Issuer  CN={iss.CN}, O={iss.O}')

    # The CA cert is the LAST cert in the chain (or the one that is NOT google's leaf)
    # Bright Data replaces leaf cert but keeps real chain above it
    # Actually Bright Data issues its own cert so the root CA IS Bright Data's
    ca_cert = chain[-1]  # root / topmost
    pem = crypto.dump_certificate(crypto.FILETYPE_PEM, ca_cert).decode()
    with open('brd_ca.crt', 'w') as f:
        f.write(pem)
    print(f'\nSaved root CA cert -> brd_ca.crt')
    print(pem[:200])
    conn.close()
else:
    print('pyOpenSSL not installed. Install it: pip install pyopenssl')
    print('Trying fallback: pip install pyopenssl')

s.close()
