"""
FastAPI REST API for TripoSR - Image to 3D Model Generation
Supports both synchronous and asynchronous processing with job queue management
"""

import asyncio
import io
import logging
import os
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import sys
import os
from io import StringIO

# Try to import rembg, suppressing stderr if it fails due to backend issues
REMBG_AVAILABLE = False
rembg = None

if os.getenv('SKIP_REMBG') == '1':
    print("⚠ Background removal disabled (SKIP_REMBG=1)")
else:
    try:
        # Temporarily redirect stderr to suppress backend errors
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        try:
            import rembg
            REMBG_AVAILABLE = True
            print("✓ Background removal (rembg) loaded successfully")
        finally:
            sys.stderr = old_stderr
    except (ImportError, Exception) as e:
        sys.stderr = old_stderr
        print(f"⚠ Background removal disabled: {e}")
import torch
import xatlas
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from tsr.system import TSR
from tsr.utils import remove_background, resize_foreground
from tsr.bake_texture import bake_texture

# ================== Configuration ==================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "10"))
RATE_LIMIT = os.getenv("RATE_LIMIT", "10/minute")
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "3600"))  # 1 hour
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
GPU_MEMORY_THRESHOLD = float(os.getenv("GPU_MEMORY_THRESHOLD", "0.9"))
ENABLE_METRICS = os.getenv("ENABLE_METRICS", "true").lower() == "true"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))  # seconds

# ================== Logging Setup ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - [%(correlation_id)s] - %(message)s",
    level=getattr(logging, LOG_LEVEL),
)
logger = logging.getLogger(__name__)

# ================== Models ==================
class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class OutputFormat(str, Enum):
    OBJ = "obj"
    GLB = "glb"

class JobInfo(BaseModel):
    job_id: str
    status: JobStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    progress: int = 0  # 0-100
    file_path: Optional[str] = None

class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str

class HealthResponse(BaseModel):
    status: str
    device: str
    gpu_memory_allocated_gb: Optional[float] = None
    gpu_memory_reserved_gb: Optional[float] = None
    gpu_memory_percent: Optional[float] = None
    queue_length: int
    model_loaded: bool

# ================== Global State ==================
class AppState:
    def __init__(self):
        self.model: Optional[TSR] = None
        self.device: str = "cpu"
        self.rembg_session = None
        self.jobs: Dict[str, JobInfo] = {}
        self.job_queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self.processing_semaphore = asyncio.Semaphore(1)  # Only 1 concurrent GPU job
        self.worker_task: Optional[asyncio.Task] = None
        self.metrics = {
            "requests_total": 0,
            "requests_success": 0,
            "requests_failed": 0,
            "processing_time_total": 0.0,
        }

app_state = AppState()

# ================== Rate Limiting ==================
limiter = Limiter(key_func=get_remote_address)

# ================== Lifecycle Management ==================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown"""
    # Startup
    logger.info("Starting TripoSR API...")
    
    # Determine device
    if torch.cuda.is_available():
        app_state.device = "cuda:0"
        logger.info(f"CUDA available. Using GPU: {torch.cuda.get_device_name(0)}")
    else:
        app_state.device = "cpu"
        logger.warning("CUDA not available. Using CPU (will be slow!)")
    
    # Load model
    try:
        logger.info("Loading TripoSR model...")
        app_state.model = TSR.from_pretrained(
            "stabilityai/TripoSR",
            config_name="config.yaml",
            weight_name="model.ckpt",
        )
        app_state.model.renderer.set_chunk_size(8192)
        app_state.model.to(app_state.device)
        logger.info("Model loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise
    
    # Initialize rembg session
    if REMBG_AVAILABLE:
        try:
            logger.info("Initializing background removal session...")
            app_state.rembg_session = rembg.new_session()
            logger.info("Background removal initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize rembg: {e}")
    else:
        logger.warning("rembg not available - background removal will be disabled")
    
    # Start background worker
    app_state.worker_task = asyncio.create_task(process_queue())
    logger.info("Background worker started")
    
    # Start cleanup task
    cleanup_task = asyncio.create_task(cleanup_old_jobs())
    
    logger.info("TripoSR API ready!")
    
    yield
    
    # Shutdown
    logger.info("Shutting down TripoSR API...")
    if app_state.worker_task:
        app_state.worker_task.cancel()
        try:
            await app_state.worker_task
        except asyncio.CancelledError:
            pass
    
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    
    logger.info("Shutdown complete")

# ================== FastAPI App ==================
app = FastAPI(
    title="TripoSR API",
    description="Fast 3D reconstruction from a single image",
    version="1.0.0",
    lifespan=lifespan,
)

# Add middleware
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID middleware
@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    correlation_id = str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    
    # Create a logger adapter that includes the correlation ID
    old_factory = logging.getLogRecordFactory()
    
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.correlation_id = correlation_id
        return record
    
    logging.setLogRecordFactory(record_factory)
    
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
    finally:
        logging.setLogRecordFactory(old_factory)

# ================== Helper Functions ==================
def check_gpu_memory() -> bool:
    """Check if GPU memory usage is below threshold"""
    if not torch.cuda.is_available():
        return True
    
    allocated = torch.cuda.memory_allocated(0) / (1024**3)
    reserved = torch.cuda.memory_reserved(0) / (1024**3)
    total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    
    usage_percent = reserved / total if total > 0 else 0
    
    return usage_percent < GPU_MEMORY_THRESHOLD

def preprocess_image(
    image: Image.Image,
    do_remove_background: bool,
    foreground_ratio: float
) -> Image.Image:
    """Preprocess input image"""
    def fill_background(img):
        img_array = np.array(img).astype(np.float32) / 255.0
        img_array = img_array[:, :, :3] * img_array[:, :, 3:4] + (1 - img_array[:, :, 3:4]) * 0.5
        return Image.fromarray((img_array * 255.0).astype(np.uint8))
    
    if do_remove_background:
        if app_state.rembg_session is None:
            raise HTTPException(status_code=500, detail="Background removal not available")
        image = image.convert("RGB")
        image = remove_background(image, app_state.rembg_session)
        image = resize_foreground(image, foreground_ratio)
        image = fill_background(image)
    else:
        if image.mode == "RGBA":
            image = fill_background(image)
    
    return image

async def process_single_job(job_id: str):
    """Process a single 3D generation job"""
    job = app_state.jobs.get(job_id)
    if not job:
        return
    
    start_time = time.time()
    
    try:
        job.status = JobStatus.PROCESSING
        job.progress = 10
        logger.info(f"Starting job {job_id}")
        
        # Load job parameters from temporary file
        temp_dir = Path(tempfile.gettempdir()) / "triposr_jobs" / job_id
        params_file = temp_dir / "params.txt"
        
        if not params_file.exists():
            raise Exception("Job parameters not found")
        
        # Read parameters
        params = {}
        with open(params_file, 'r') as f:
            for line in f:
                key, value = line.strip().split('=', 1)
                params[key] = value
        
        # Load and preprocess image
        image_path = temp_dir / "input_image.png"
        image = Image.open(image_path)
        
        job.progress = 20
        logger.info(f"Job {job_id}: Preprocessing image")
        
        processed_image = preprocess_image(
            image,
            params['remove_background'] == 'True',
            float(params['foreground_ratio'])
        )
        
        job.progress = 40
        logger.info(f"Job {job_id}: Running model inference")
        
        # Run model
        with torch.no_grad():
            scene_codes = app_state.model([processed_image], device=app_state.device)
        
        job.progress = 60
        logger.info(f"Job {job_id}: Extracting mesh")
        
        # Extract mesh
        mc_resolution = int(params['mc_resolution'])
        bake = params['bake_texture'] == 'True'
        meshes = app_state.model.extract_mesh(scene_codes, not bake, resolution=mc_resolution)
        
        job.progress = 80
        logger.info(f"Job {job_id}: Exporting model")
        
        # Export mesh
        output_format = params['output_format']
        output_path = temp_dir / f"output.{output_format}"
        
        if bake:
            texture_resolution = int(params['texture_resolution'])
            texture_path = temp_dir / "texture.png"
            
            bake_output = bake_texture(meshes[0], app_state.model, scene_codes[0], texture_resolution)
            
            xatlas.export(
                str(output_path),
                meshes[0].vertices[bake_output["vmapping"]],
                bake_output["indices"],
                bake_output["uvs"],
                meshes[0].vertex_normals[bake_output["vmapping"]]
            )
            Image.fromarray((bake_output["colors"] * 255.0).astype(np.uint8)).transpose(
                Image.FLIP_TOP_BOTTOM
            ).save(texture_path)
        else:
            meshes[0].export(str(output_path))
        
        job.progress = 100
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now()
        job.file_path = str(output_path)
        
        processing_time = time.time() - start_time
        app_state.metrics["requests_success"] += 1
        app_state.metrics["processing_time_total"] += processing_time
        
        logger.info(f"Job {job_id} completed in {processing_time:.2f}s")
        
    except Exception as e:
        job.status = JobStatus.FAILED
        job.error = str(e)
        job.completed_at = datetime.now()
        app_state.metrics["requests_failed"] += 1
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)

async def process_queue():
    """Background worker that processes jobs from the queue"""
    logger.info("Queue processor started")
    
    while True:
        try:
            # Get job from queue
            job_id = await app_state.job_queue.get()
            
            # Acquire semaphore to ensure only one GPU job runs at a time
            async with app_state.processing_semaphore:
                await process_single_job(job_id)
            
            app_state.job_queue.task_done()
            
        except asyncio.CancelledError:
            logger.info("Queue processor cancelled")
            break
        except Exception as e:
            logger.error(f"Error in queue processor: {e}", exc_info=True)
            await asyncio.sleep(1)

async def cleanup_old_jobs():
    """Periodically cleanup old completed jobs"""
    while True:
        try:
            await asyncio.sleep(300)  # Run every 5 minutes
            
            cutoff_time = datetime.now() - timedelta(seconds=JOB_TTL_SECONDS)
            jobs_to_delete = []
            
            for job_id, job in app_state.jobs.items():
                if job.completed_at and job.completed_at < cutoff_time:
                    jobs_to_delete.append(job_id)
            
            for job_id in jobs_to_delete:
                # Clean up files
                temp_dir = Path(tempfile.gettempdir()) / "triposr_jobs" / job_id
                if temp_dir.exists():
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
                
                # Remove from jobs dict
                del app_state.jobs[job_id]
                logger.info(f"Cleaned up job {job_id}")
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}", exc_info=True)

# ================== Endpoints ==================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint with API documentation"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>TripoSR API</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
            h1 { color: #333; }
            code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
            pre { background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }
            .endpoint { margin: 20px 0; padding: 15px; border-left: 4px solid #007bff; background: #f8f9fa; }
        </style>
    </head>
    <body>
        <h1>TripoSR API</h1>
        <p>Fast 3D reconstruction from a single image</p>
        
        <h2>Endpoints</h2>
        
        <div class="endpoint">
            <h3>POST /api/generate</h3>
            <p>Generate a 3D model from an image</p>
            <p><strong>Parameters:</strong></p>
            <ul>
                <li><code>file</code>: Image file (PNG, JPG, JPEG, max 10MB)</li>
                <li><code>remove_background</code>: Boolean (default: true)</li>
                <li><code>foreground_ratio</code>: Float 0.5-1.0 (default: 0.85)</li>
                <li><code>mc_resolution</code>: Integer 32-320 (default: 256)</li>
                <li><code>output_format</code>: "obj" or "glb" (default: "glb")</li>
                <li><code>bake_texture</code>: Boolean (default: false)</li>
                <li><code>texture_resolution</code>: Integer (default: 2048)</li>
                <li><code>async_mode</code>: Boolean (default: false)</li>
            </ul>
        </div>
        
        <div class="endpoint">
            <h3>GET /api/status/{job_id}</h3>
            <p>Check the status of an async job</p>
        </div>
        
        <div class="endpoint">
            <h3>GET /api/download/{job_id}</h3>
            <p>Download a completed 3D model</p>
        </div>
        
        <div class="endpoint">
            <h3>GET /api/health</h3>
            <p>Check API health and GPU status</p>
        </div>
        
        <h2>Documentation</h2>
        <ul>
            <li><a href="/docs">Interactive API Documentation (Swagger UI)</a></li>
            <li><a href="/redoc">Alternative Documentation (ReDoc)</a></li>
        </ul>
        
        <h2>Example Usage</h2>
        <pre>
# Synchronous mode (returns file directly)
curl -X POST "http://localhost:8000/api/generate" \\
  -F "file=@image.png" \\
  -F "output_format=glb" \\
  -o output.glb

# Asynchronous mode (returns job ID)
curl -X POST "http://localhost:8000/api/generate" \\
  -F "file=@image.png" \\
  -F "async_mode=true"
        </pre>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/api/health", response_model=HealthResponse)
@limiter.limit("30/minute")
async def health(request: Request):
    """Health check endpoint"""
    gpu_memory_allocated = None
    gpu_memory_reserved = None
    gpu_memory_percent = None
    
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated(0) / (1024**3)
        reserved = torch.cuda.memory_reserved(0) / (1024**3)
        total = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        
        gpu_memory_allocated = round(allocated, 2)
        gpu_memory_reserved = round(reserved, 2)
        gpu_memory_percent = round((reserved / total * 100) if total > 0 else 0, 2)
    
    return HealthResponse(
        status="healthy" if app_state.model is not None else "unhealthy",
        device=app_state.device,
        gpu_memory_allocated_gb=gpu_memory_allocated,
        gpu_memory_reserved_gb=gpu_memory_reserved,
        gpu_memory_percent=gpu_memory_percent,
        queue_length=app_state.job_queue.qsize(),
        model_loaded=app_state.model is not None,
    )

@app.get("/api/metrics")
async def metrics():
    """Prometheus-compatible metrics endpoint"""
    if not ENABLE_METRICS:
        raise HTTPException(status_code=404, detail="Metrics disabled")
    
    avg_processing_time = (
        app_state.metrics["processing_time_total"] / app_state.metrics["requests_success"]
        if app_state.metrics["requests_success"] > 0
        else 0
    )
    
    metrics_output = f"""# HELP triposr_requests_total Total number of requests
# TYPE triposr_requests_total counter
triposr_requests_total {app_state.metrics["requests_total"]}

# HELP triposr_requests_success Total number of successful requests
# TYPE triposr_requests_success counter
triposr_requests_success {app_state.metrics["requests_success"]}

# HELP triposr_requests_failed Total number of failed requests
# TYPE triposr_requests_failed counter
triposr_requests_failed {app_state.metrics["requests_failed"]}

# HELP triposr_queue_length Current queue length
# TYPE triposr_queue_length gauge
triposr_queue_length {app_state.job_queue.qsize()}

# HELP triposr_processing_time_avg Average processing time in seconds
# TYPE triposr_processing_time_avg gauge
triposr_processing_time_avg {avg_processing_time:.2f}
"""
    
    return HTMLResponse(content=metrics_output, media_type="text/plain")

@app.post("/api/generate")
@limiter.limit(RATE_LIMIT)
async def generate(
    request: Request,
    file: UploadFile = File(...),
    remove_background: bool = Form(True),
    foreground_ratio: float = Form(0.85),
    mc_resolution: int = Form(256),
    output_format: OutputFormat = Form(OutputFormat.GLB),
    bake_texture: bool = Form(False),
    texture_resolution: int = Form(2048),
    async_mode: bool = Form(False),
):
    """Generate 3D model from image"""
    app_state.metrics["requests_total"] += 1
    
    # Validate model is loaded
    if app_state.model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    # Validate file type
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    
    # Validate file size
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"File size exceeds {MAX_FILE_SIZE_MB}MB limit"
        )
    
    # Check GPU memory
    if not check_gpu_memory():
        raise HTTPException(
            status_code=503,
            detail="GPU memory usage too high. Please try again later."
        )
    
    # Validate parameters
    if not 0.5 <= foreground_ratio <= 1.0:
        raise HTTPException(status_code=400, detail="foreground_ratio must be between 0.5 and 1.0")
    
    if not 32 <= mc_resolution <= 512:
        raise HTTPException(status_code=400, detail="mc_resolution must be between 32 and 512")
    
    # Create job
    job_id = str(uuid.uuid4())
    job = JobInfo(
        job_id=job_id,
        status=JobStatus.QUEUED,
        created_at=datetime.now(),
    )
    app_state.jobs[job_id] = job
    
    # Save job data to temp directory
    temp_dir = Path(tempfile.gettempdir()) / "triposr_jobs" / job_id
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Save image
    image_path = temp_dir / "input_image.png"
    try:
        image = Image.open(io.BytesIO(contents))
        image.save(image_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {str(e)}")
    
    # Save parameters
    params_file = temp_dir / "params.txt"
    with open(params_file, 'w') as f:
        f.write(f"remove_background={remove_background}\n")
        f.write(f"foreground_ratio={foreground_ratio}\n")
        f.write(f"mc_resolution={mc_resolution}\n")
        f.write(f"output_format={output_format.value}\n")
        f.write(f"bake_texture={bake_texture}\n")
        f.write(f"texture_resolution={texture_resolution}\n")
    
    if async_mode:
        # Add to queue for background processing
        try:
            app_state.job_queue.put_nowait(job_id)
            logger.info(f"Job {job_id} queued for async processing")
            return GenerateResponse(
                job_id=job_id,
                status="queued",
                message="Job queued for processing. Use /api/status/{job_id} to check progress."
            )
        except asyncio.QueueFull:
            del app_state.jobs[job_id]
            raise HTTPException(
                status_code=503,
                detail=f"Queue is full (max {MAX_QUEUE_SIZE} jobs). Please try again later."
            )
    else:
        # Process synchronously
        try:
            await asyncio.wait_for(
                process_single_job(job_id),
                timeout=REQUEST_TIMEOUT
            )
            
            if job.status == JobStatus.COMPLETED and job.file_path:
                return FileResponse(
                    job.file_path,
                    media_type="application/octet-stream",
                    filename=f"model.{output_format.value}"
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Job failed: {job.error or 'Unknown error'}"
                )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=f"Request timeout after {REQUEST_TIMEOUT}s. Try async_mode=true for long-running requests."
            )

@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Get job status"""
    job = app_state.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return job

@app.get("/api/download/{job_id}")
async def download(job_id: str):
    """Download completed job result"""
    job = app_state.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Status: {job.status}"
        )
    
    if not job.file_path or not Path(job.file_path).exists():
        raise HTTPException(status_code=404, detail="Result file not found")
    
    # Get file extension
    ext = Path(job.file_path).suffix.lstrip('.')
    
    return FileResponse(
        job.file_path,
        media_type="application/octet-stream",
        filename=f"model.{ext}"
    )

# ================== Main ==================
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    uvicorn.run(
        "api:app",
        host=host,
        port=port,
        reload=False,
        log_level=LOG_LEVEL.lower(),
    )
