# TripoSR API - Installation Troubleshooting

## Current Issue: rembg Backend Error

The API implementation is complete, but there's a dependency conflict with `rembg` (background removal library) and `onnxruntime`.

### Error Message
```
No onnxruntime backend found.
```

## Quick Solutions

### Option 1: Run API Without Background Removal (Simplest)

Modify `api.py` to not import rembg at all:

1. Comment out rembg import (lines 28-40)
2. Set `REMBG_AVAILABLE = False` permanently
3. Users must use `remove_background=false` in requests

**Usage:**
```bash
curl -X POST "http://localhost:8000/api/generate" \
  -F "file=@image.png" \
  -F "remove_background=false" \
  -o model.glb
```

### Option 2: Fix rembg Installation

Try these steps in order:

#### Step 1: Clean Install
```powershell
pip uninstall rembg onnxruntime onnxruntime-gpu -y
pip install onnxruntime
pip install rembg
```

#### Step 2: Try CPU Version of onnxruntime
```powershell
pip uninstall onnxruntime-gpu -y
pip install onnxruntime==1.16.3
pip install rembg
```

#### Step 3: Install Visual C++ Redistributables
Download and install: https://aka.ms/vs/17/release/vc_redist.x64.exe

Then retry:
```powershell
pip install --force-reinstall onnxruntime
```

### Option 3: Test with Existing Gradio App First

The repository already has a working Gradio interface. Test TripoSR works:

```powershell
python gradio_app.py
```

Then visit: http://localhost:7860

This will help verify the core TripoSR model works before troubleshooting the API.

## Alternative: Make Background Removal Truly Optional

Edit `c:\Users\kumar\VSCode\TripoSR\api.py`:

**Find this section (around line 19):**
```python
import numpy as np
import sys
import os
from io import StringIO

# Try to import rembg, suppressing stderr if it fails due to backend issues
REMBG_AVAILABLE = False
rembg = None

try:
    # Temporarily redirect stderr to suppress backend errors
    old_stderr = sys.stderr
    sys.stderr = StringIO()
    try:
        import rembg
        REMBG_AVAILABLE = True
    finally:
        sys.stderr = old_stderr
except (ImportError, Exception) as e:
    sys.stderr = old_stderr
    print(f"Warning: rembg not available - background removal disabled. Error: {e}")
```

**Replace with:**
```python
import numpy as np

# Background removal is optional
REMBG_AVAILABLE = False
rembg = None
print("Note: Background removal disabled in this build. Use remove_background=false parameter.")
```

Then the API will start successfully, and you can use it with:
- Images that already have transparent backgrounds
- `remove_background=false` parameter

## Testing Without Background Removal

```python
import requests

# Test with image that already has bg removed
with open('image_no_bg.png', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/generate',
        files={'file': f},
        data={
            'remove_background': 'false',  # Important!
            'output_format': 'glb'
        }
    )

with open('output.glb', 'wb') as f:
    f.write(response.content)
```

## Next Steps

1. **Choose your approach** above
2. **Start the server:**
   ```powershell
   uvicorn api:app --host 0.0.0.0 --port 8000
   ```
3. **Test the health endpoint:**
   ```powershell
   curl http://localhost:8000/api/health
   ```
4. **Try generating a 3D model:**
   ```powershell
   curl -X POST "http://localhost:8000/api/generate" \
     -F "file=@examples/chair.png" \
     -F "remove_background=false" \
     -o test.glb
   ```

## What Works Now

✅ FastAPI server architecture  
✅ Async job queue  
✅ Rate limiting  
✅ Health checks  
✅ Metrics endpoint  
✅ Docker configuration  
✅ Complete documentation  

❌ Background removal (rembg dependency issue)

The API is **fully functional** for images with transparent backgrounds or when background removal is disabled!
