import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/Library/Python/3.9/lib/python/site-packages"))

import uvicorn
uvicorn.run("main:app", host="0.0.0.0", port=8000, loop="asyncio", http="h11")
