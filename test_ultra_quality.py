"""Test with MAXIMUM resolution + smoothing for best quality"""
import requests

url = "http://localhost:8000/api/generate"

print("Generating ULTRA HIGH QUALITY model...")
print("Resolution: 512 (maximum)")
print("Smoothing: Enabled")
print("This may take 2-3 minutes...\n")

with open("examples/chair.png", "rb") as f:
    response = requests.post(
        url,
        files={'file': ('chair.png', f, 'image/png')},
        data={
            'output_format': 'glb',
            'mc_resolution': '512',  # MAXIMUM (was 320)
            'foreground_ratio': '0.85',
            'remove_background': 'true'
        },
        timeout=300  # 5 minutes for high-res processing
    )

if response.status_code == 200:
    with open("output_test/chair_ultra_highres.glb", "wb") as f:
        f.write(response.content)
    print("\n✓ ULTRA HIGH QUALITY model saved!")
    print("  File: output_test/chair_ultra_highres.glb")
    print("\nThis should be MUCH smoother with less pixelation!")
else:
    print(f"\n✗ Failed: {response.status_code}")
    print(f"  Error: {response.text}")
