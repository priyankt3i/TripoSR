# api_no_rembg.py - Temporary API without background removal
# Use this until onnxruntime DLL issue is resolved

import os
os.environ['SKIP_REMBG'] = '1'  # Signal to skip rembg import

# Now import the regular API
from api import *

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    print("="*60)
    print("TripoSR API Starting (Background Removal Disabled)")
    print("="*60)
    print(f"Server: http://{host}:{port}")
    print(f"Docs: http://localhost:{port}/docs")
    print()
    print("Note: Use remove_background=false in API requests")
    print("="*60)
    
    uvicorn.run(
        "api_no_rembg:app",
        host=host,
        port=port,
        reload=False,
        log_level="INFO",
    )
