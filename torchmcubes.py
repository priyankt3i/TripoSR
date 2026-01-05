"""
Compatibility shim: torchmcubes using pymcubes backend
This allows TripoSR to work without compiling torchmcubes
"""
import torch
import numpy as np
import mcubes  # pymcubes

def marching_cubes(volume, threshold=0.0):
    """
    Wrapper around pymcubes.marching_cubes to match torchmcubes API
    
    Args:
        volume: torch.Tensor of shape (resolution, resolution, resolution)
        threshold: float, isovalue for surface extraction
        
    Returns:
        vertices: torch.Tensor of shape (N, 3) on same device as input
        triangles: torch.LongTensor of shape (M, 3) on same device as input
    """
    # Remember original device
    original_device = volume.device if isinstance(volume, torch.Tensor) else 'cpu'
    
    # Convert to numpy (PyMCubes only works on CPU)
    if isinstance(volume, torch.Tensor):
        volume_np = volume.cpu().numpy()
    else:
        volume_np = np.array(volume)
    
    # Run marching cubes on CPU
    vertices, triangles = mcubes.marching_cubes(volume_np, threshold)
    
    # Convert back to torch tensors on the original device
    vertices = torch.from_numpy(vertices).float().to(original_device)
    triangles = torch.from_numpy(triangles).long().to(original_device)
    
    return vertices, triangles


__version__ = "0.1.0-pymcubes"
