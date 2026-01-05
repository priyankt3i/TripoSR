"""
Example client for testing TripoSR API
Demonstrates both sync and async modes
"""

import argparse
import requests
import time
from pathlib import Path


def test_sync_mode(api_url: str, image_path: str, output_path: str):
    """Test synchronous mode - wait for result"""
    print(f"Testing sync mode with {image_path}...")
    
    with open(image_path, 'rb') as f:
        # Specify MIME type for image
        files = {'file': (Path(image_path).name, f, 'image/png')}
        response = requests.post(
            f"{api_url}/api/generate",
            files=files,
            data={
                'output_format': 'glb',
                'remove_background': 'true',
                'mc_resolution': '256'
            },
            timeout=120  # 2 minute timeout
        )
    
    if response.status_code == 200:
        with open(output_path, 'wb') as f:
            f.write(response.content)
        print(f"✓ Success! Model saved to {output_path}")
        return True
    else:
        print(f"✗ Failed: {response.status_code} - {response.text}")
        return False


def test_async_mode(api_url: str, image_path: str, output_path: str):
    """Test asynchronous mode - poll for status"""
    print(f"Testing async mode with {image_path}...")
    
    # Submit job
    with open(image_path, 'rb') as f:
        # Specify MIME type for image
        files = {'file': (Path(image_path).name, f, 'image/png')}
        response = requests.post(
            f"{api_url}/api/generate",
            files=files,
            data={
                'async_mode': 'true',
                'output_format': 'glb',
                'mc_resolution': '256'
            }
        )
    
    if response.status_code != 200:
        print(f"✗ Failed to submit job: {response.status_code} - {response.text}")
        return False
    
    job_data = response.json()
    job_id = job_data['job_id']
    print(f"Job submitted: {job_id}")
    
    # Poll for completion
    while True:
        status_response = requests.get(f"{api_url}/api/status/{job_id}")
        status = status_response.json()
        
        print(f"  Status: {status['status']} - Progress: {status['progress']}%")
        
        if status['status'] == 'completed':
            break
        elif status['status'] == 'failed':
            print(f"✗ Job failed: {status.get('error', 'Unknown error')}")
            return False
        
        time.sleep(2)
    
    # Download result
    download_response = requests.get(f"{api_url}/api/download/{job_id}")
    
    if download_response.status_code == 200:
        with open(output_path, 'wb') as f:
            f.write(download_response.content)
        print(f"✓ Success! Model saved to {output_path}")
        return True
    else:
        print(f"✗ Failed to download: {download_response.status_code}")
        return False


def test_health(api_url: str):
    """Test health endpoint"""
    print("Testing health endpoint...")
    
    response = requests.get(f"{api_url}/api/health")
    
    if response.status_code == 200:
        health = response.json()
        print("✓ Server is healthy:")
        print(f"  Device: {health['device']}")
        print(f"  Model loaded: {health['model_loaded']}")
        print(f"  Queue length: {health['queue_length']}")
        
        if health.get('gpu_memory_percent'):
            print(f"  GPU memory: {health['gpu_memory_percent']}%")
        
        return True
    else:
        print(f"✗ Health check failed: {response.status_code}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test TripoSR API")
    parser.add_argument(
        '--url',
        default='http://localhost:8000',
        help='API base URL (default: http://localhost:8000)'
    )
    parser.add_argument(
        '--image',
        default='examples/chair.png',
        help='Path to test image (default: examples/chair.png)'
    )
    parser.add_argument(
        '--mode',
        choices=['sync', 'async', 'both', 'health'],
        default='both',
        help='Test mode (default: both)'
    )
    parser.add_argument(
        '--output-dir',
        default='output_test',
        help='Output directory (default: output_test)'
    )
    
    args = parser.parse_args()
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    print(f"\n{'='*60}")
    print(f"TripoSR API Test Client")
    print(f"{'='*60}\n")
    
    # Test health first
    if not test_health(args.url):
        print("\n⚠ Server not healthy. Exiting.")
        return 1
    
    print()
    
    if args.mode == 'health':
        return 0
    
    # Check if image exists
    image_path = Path(args.image)
    if not image_path.exists():
        print(f"✗ Image not found: {image_path}")
        return 1
    
    results = []
    
    # Test sync mode
    if args.mode in ['sync', 'both']:
        print()
        output_sync = output_dir / 'model_sync.glb'
        success = test_sync_mode(args.url, str(image_path), str(output_sync))
        results.append(('Sync mode', success))
    
    # Test async mode
    if args.mode in ['async', 'both']:
        print()
        output_async = output_dir / 'model_async.glb'
        success = test_async_mode(args.url, str(image_path), str(output_async))
        results.append(('Async mode', success))
    
    # Summary
    print(f"\n{'='*60}")
    print("Test Summary:")
    print(f"{'='*60}")
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{name}: {status}")
    
    all_passed = all(success for _, success in results)
    return 0 if all_passed else 1


if __name__ == '__main__':
    exit(main())
