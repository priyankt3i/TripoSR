"""Test with higher resolution for better quality"""
import requests

url = "http://localhost:8000/api/generate"

with open("examples/hamburger.png", "rb") as f:
    response = requests.post(
        url,
        files={'file': ('hamburger.png', f, 'image/png')},
        data={
            'output_format': 'glb',
            'mc_resolution': '320',  # Higher resolution (max: 320)
            'foreground_ratio': '0.9',  # Larger object in frame
            'remove_background': 'true'
        },
        timeout=180
    )

if response.status_code == 200:
    with open("output_test/hamburger_highres.glb", "wb") as f:
        f.write(response.content)
    print("✓ Higher resolution model saved!")
    print("  File: output_test/hamburger_highres.glb")
else:
    print(f"✗ Failed: {response.status_code}")
    print(f"  Error: {response.text}")
