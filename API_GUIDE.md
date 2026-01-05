# TripoSR API Guide

Complete guide for using the TripoSR REST API to generate 3D models from images.

## Table of Contents
- [Quick Start](#quick-start)
- [API Endpoints](#api-endpoints)
- [Usage Modes](#usage-modes)
- [Client Examples](#client-examples)
- [Rate Limiting](#rate-limiting)
- [Error Handling](#error-handling)
- [Deployment](#deployment)
- [Scaling Recommendations](#scaling-recommendations)

## Quick Start

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Start the server:
```bash
# Development
uvicorn api:app --reload --host 0.0.0.0 --port 8000

# Production
python api.py
```

3. Access the API:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/api/health

## API Endpoints

### POST /api/generate

Generate a 3D model from an image.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file` | File | *required* | Image file (PNG, JPG, JPEG, max 10MB) |
| `remove_background` | Boolean | `true` | Automatically remove image background |
| `foreground_ratio` | Float | `0.85` | Object size ratio (0.5-1.0) |
| `mc_resolution` | Integer | `256` | Mesh resolution (32-320, higher = more detail) |
| `output_format` | String | `glb` | Output format: "obj" or "glb" |
| `bake_texture` | Boolean | `false` | Bake texture atlas instead of vertex colors |
| `texture_resolution` | Integer | `2048` | Texture size when baking (512-4096) |
| `async_mode` | Boolean | `false` | Return job ID immediately instead of waiting |

**Response (Sync Mode):**
- Status: 200
- Content-Type: `application/octet-stream`
- Body: 3D model file

**Response (Async Mode):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "message": "Job queued for processing. Use /api/status/{job_id} to check progress."
}
```

---

### GET /api/status/{job_id}

Check the status of an async job.

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "created_at": "2024-01-03T22:00:00",
  "completed_at": null,
  "error": null,
  "progress": 60,
  "file_path": null
}
```

**Status Values:**
- `queued` - Waiting in queue
- `processing` - Currently generating 3D model
- `completed` - Ready for download
- `failed` - Generation failed (see `error` field)

---

### GET /api/download/{job_id}

Download a completed 3D model.

**Response:**
- Status: 200 (if completed)
- Content-Type: `application/octet-stream`
- Body: 3D model file

**Notes:**
- Only works for completed jobs
- Files are deleted after 1 hour (configurable via `JOB_TTL_SECONDS`)

---

### GET /api/health

Check API status and GPU information.

**Response:**
```json
{
  "status": "healthy",
  "device": "cuda:0",
  "gpu_memory_allocated_gb": 5.2,
  "gpu_memory_reserved_gb": 6.1,
  "gpu_memory_percent": 76.25,
  "queue_length": 2,
  "model_loaded": true
}
```

---

### GET /api/metrics

Prometheus-compatible metrics endpoint.

**Response:**
```
# HELP triposr_requests_total Total number of requests
# TYPE triposr_requests_total counter
triposr_requests_total 150

# HELP triposr_requests_success Total number of successful requests
# TYPE triposr_requests_success counter
triposr_requests_success 145

# ...
```

## Usage Modes

### Synchronous Mode (Default)

**Best for:**
- Interactive applications
- Low latency requirements
- Single user scenarios

**Limitations:**
- Request waits for completion (0.5-30s depending on GPU)
- May timeout on slow GPUs
- Blocks the connection

**Example:**
```bash
curl -X POST "http://localhost:8000/api/generate" \
  -F "file=@examples/chair.png" \
  -F "output_format=glb" \
  -o output.glb
```

### Asynchronous Mode

**Best for:**
- High-traffic scenarios
- Slow GPU environments
- Batch processing
- Mobile/web apps

**Advantages:**
- Immediate response with job ID
- Client can poll for status
- No timeout issues
- Better for queue management

**Example:**
```bash
# Submit job
JOB_ID=$(curl -X POST "http://localhost:8000/api/generate" \
  -F "file=@examples/chair.png" \
  -F "async_mode=true" | jq -r '.job_id')

# Check status
curl "http://localhost:8000/api/status/$JOB_ID"

# Download when completed
curl "http://localhost:8000/api/download/$JOB_ID" -o output.glb
```

## Client Examples

### cURL

```bash
# Simple synchronous request
curl -X POST "http://localhost:8000/api/generate" \
  -F "file=@image.png" \
  -o model.glb

# With custom parameters
curl -X POST "http://localhost:8000/api/generate" \
  -F "file=@image.png" \
  -F "remove_background=false" \
  -F "mc_resolution=320" \
  -F "output_format=obj" \
  -F "bake_texture=true" \
  -o model.obj
```

### Python

```python
import requests
import time

# Synchronous mode
with open('image.png', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/generate',
        files={'file': f},
        data={'output_format': 'glb'}
    )

with open('output.glb', 'wb') as f:
    f.write(response.content)

# Asynchronous mode
with open('image.png', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/generate',
        files={'file': f},
        data={'async_mode': 'true', 'output_format': 'glb'}
    )

job_id = response.json()['job_id']

# Poll for completion
while True:
    status = requests.get(f'http://localhost:8000/api/status/{job_id}').json()
    print(f"Progress: {status['progress']}%")
    
    if status['status'] == 'completed':
        break
    elif status['status'] == 'failed':
        print(f"Error: {status['error']}")
        break
    
    time.sleep(2)

# Download result
response = requests.get(f'http://localhost:8000/api/download/{job_id}')
with open('output.glb', 'wb') as f:
    f.write(response.content)
```

### JavaScript (Node.js)

```javascript
const FormData = require('form-data');
const fs = require('fs');
const fetch = require('node-fetch');

async function generate3DModel(imagePath) {
  const formData = new FormData();
  formData.append('file', fs.createReadStream(imagePath));
  formData.append('output_format', 'glb');
  formData.append('async_mode', 'true');
  
  // Submit job
  const submitResponse = await fetch('http://localhost:8000/api/generate', {
    method: 'POST',
    body: formData
  });
  
  const { job_id } = await submitResponse.json();
  console.log(`Job ID: ${job_id}`);
  
  // Poll for completion
  while (true) {
    const statusResponse = await fetch(`http://localhost:8000/api/status/${job_id}`);
    const status = await statusResponse.json();
    
    console.log(`Progress: ${status.progress}%`);
    
    if (status.status === 'completed') {
      break;
    } else if (status.status === 'failed') {
      throw new Error(`Job failed: ${status.error}`);
    }
    
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  
  // Download result
  const downloadResponse = await fetch(`http://localhost:8000/api/download/${job_id}`);
  const buffer = await downloadResponse.buffer();
  fs.writeFileSync('output.glb', buffer);
  
  console.log('Model saved to output.glb');
}

generate3DModel('image.png');
```

### JavaScript (Browser)

```javascript
async function generate3DModel(imageFile) {
  const formData = new FormData();
  formData.append('file', imageFile);
  formData.append('async_mode', 'true');
  
  // Submit job
  const response = await fetch('http://localhost:8000/api/generate', {
    method: 'POST',
    body: formData
  });
  
  const { job_id } = await response.json();
  
  // Poll for completion
  while (true) {
    const statusResponse = await fetch(`http://localhost:8000/api/status/${job_id}`);
    const status = await statusResponse.json();
    
    updateProgressBar(status.progress);
    
    if (status.status === 'completed') {
      break;
    } else if (status.status === 'failed') {
      throw new Error(status.error);
    }
    
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
  
  // Download result
  const downloadUrl = `http://localhost:8000/api/download/${job_id}`;
  window.location.href = downloadUrl;
}
```

## Rate Limiting

Default rate limit: **10 requests per minute per IP**

### Rate Limit Headers

Response includes rate limit information:
```
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 7
X-RateLimit-Reset: 1704326400
```

### Handling Rate Limits

When rate limited, you'll receive:
```json
{
  "detail": "Rate limit exceeded: 10.0 per 1 minute"
}
```

**Retry Strategy:**
```python
import time

def generate_with_retry(image_path, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.post(...)
            if response.status_code == 200:
                return response
            elif response.status_code == 429:  # Rate limited
                wait_time = int(response.headers.get('Retry-After', 60))
                print(f"Rate limited. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                response.raise_for_status()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)  # Exponential backoff
```

## Error Handling

### Common Errors

| Status Code | Meaning | Solution |
|-------------|---------|----------|
| 400 | Bad Request | Check file format, size, parameters |
| 404 | Not Found | Job ID doesn't exist or expired |
| 413 | Payload Too Large | Reduce image size (<10MB) |
| 429 | Too Many Requests | Wait and retry (see rate limiting) |
| 503 | Service Unavailable | Queue full or GPU memory high, retry later |
| 504 | Gateway Timeout | Use async_mode=true for slow GPUs |

### Error Response Format

```json
{
  "detail": "File size exceeds 10MB limit"
}
```

## Deployment

### Development

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

### Production (Systemd)

Create `/etc/systemd/system/triposr-api.service`:

```ini
[Unit]
Description=TripoSR API
After=network.target

[Service]
Type=simple
User=triposr
WorkingDirectory=/opt/triposr
Environment="PATH=/opt/triposr/venv/bin"
ExecStart=/opt/triposr/venv/bin/python api.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable triposr-api
sudo systemctl start triposr-api
sudo systemctl status triposr-api
```

### Docker

```bash
# Build
docker build -t triposr-api .

# Run
docker run --gpus all -p 8000:8000 triposr-api

# Or use docker-compose
docker-compose up -d
```

### Environment Variables

See `.env.example` for all configuration options:

```bash
cp .env.example .env
# Edit .env with your settings
```

## Scaling Recommendations

### Traffic Profiles

#### Low Traffic (<100 requests/day)
- **Setup**: Single server with GPU
- **Mode**: Sync mode acceptable
- **Cost**: $0.50-2/hour (cloud GPU instance)

#### Medium Traffic (100-1000 requests/day)
- **Setup**: Single server, async mode
- **Configuration**: Increase `MAX_QUEUE_SIZE` to 20-50
- **Monitoring**: Enable metrics, monitor queue length
- **Cost**: $0.50-2/hour

#### High Traffic (>1000 requests/day)
- **Setup**: Phase 2 (Redis + Celery)
- **Architecture**: 
  - Multiple stateless API servers (no GPU needed)
  - Separate GPU worker instances with Celery
  - Redis for job queue
  - S3/MinIO for result storage
- **Scaling**: Auto-scale workers based on queue length
- **Cost**: $2-10/hour depending on worker count

### Performance Metrics

| GPU | Resolution 256 | Resolution 320 | Throughput/hour |
|-----|---------------|----------------|-----------------|
| CPU | ~30s | ~60s | ~120 |
| RTX 4090 | ~2s | ~4s | ~1800 |
| A100 | ~0.5s | ~1s | ~7200 |

### Optimization Tips

1. **Pre-download models**: Models download on first request. Use Docker to pre-download during build.

2. **Adjust chunk size**: Lower `chunk_size` reduces VRAM but increases time:
```python
model.renderer.set_chunk_size(4096)  # Lower for less VRAM
```

3. **Queue management**: Set appropriate `MAX_QUEUE_SIZE` based on your GPU and expected traffic.

4. **Caching**: Implement result caching for duplicate images using image hash.

5. **Load balancing**: Use nginx or similar for multiple API instances.

## Monitoring

### Health Checks

```bash
# Basic health
curl http://localhost:8000/api/health

# Metrics
curl http://localhost:8000/api/metrics
```

### Prometheus Configuration

`prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'triposr'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/api/metrics'
    scrape_interval: 15s
```

### Grafana Dashboard

Key metrics to monitor:
- Request rate and success rate
- Queue length over time
- GPU memory usage
- Average processing time
- Error rate by type

## Support & Contributing

- **Issues**: https://github.com/VAST-AI-Research/TripoSR/issues
- **Documentation**: https://github.com/VAST-AI-Research/TripoSR
- **Model**: https://huggingface.co/stabilityai/TripoSR

## License

This API wrapper is provided as-is. TripoSR model is released under MIT license by Stability AI and Tripo AI.
