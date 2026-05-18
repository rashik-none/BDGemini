import os
from dotenv import load_dotenv

with open("test.env", "w") as f:
    f.write('PROXY_LIST="a\\n  b\\n  c"')

load_dotenv("test.env")
print(repr(os.getenv("PROXY_LIST")))
