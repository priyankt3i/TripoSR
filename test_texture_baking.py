"""Quick test for texture baking"""
import requests

url = "http://localhost:8000/api/generate"

with open("examples/chair.png", "rb") as f:
    response = requests.post(
        url,
        files={'file': ('chair.png', f, 'image/png')},
        data={
            'output_format': 'obj',
            'bake_texture': 'true',  # Enable texture baking
            'texture_resolution': '1024',
            'remove_background': 'true'
        },
        timeout=120
    )

if response.status_code == 200:
    with open("output_test/model_with_texture.obj", "wb") as f:
        f.write(response.content)
    print("✓ Success! Model with baked texture saved!")
    print("  Files: output_test/model_with_texture.obj")
    print("         output_test/texture.png (should be in same directory)")
else:
    print(f"✗ Failed: {response.status_code}")
    print(f"  Error: {response.text}")
