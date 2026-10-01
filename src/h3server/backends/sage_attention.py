"""Optional pinned SageAttention 1 (pure Triton), not sparse attention or Sage2/3."""
from importlib import metadata
from pathlib import Path
import sys

VERSION = '1.0.6'
WHEEL_SHA256 = 'fafc66569bed62a16839e820c2612141b5a20accf55b876d941bab9c0ac5d888'
RECEIPT = 'state/sageattention-receipt.json'


def receipt_path(paths, *, environment=None):
    from h3server.backends.environment_scope import receipt_path as scoped
    return scoped(paths, RECEIPT, environment)


def installed(paths, *, environment=None):
    from h3server.backends.environment_scope import receipt as read_receipt, prefix, torch_version
    try:
        target = prefix(paths, environment)
        receipt = read_receipt(paths, RECEIPT, environment)
        local = target == Path(sys.prefix).absolute()
        return ((not local or metadata.version('sageattention') == VERSION)
                and receipt == {'version': VERSION, 'wheel_sha256': WHEEL_SHA256,
                                'environment': str(target),
                                'torch': torch_version(paths, environment), 'gpu_probe': True})
    except (OSError, ValueError, metadata.PackageNotFoundError):
        return False


def require(paths, *, environment=None):
    if not installed(paths, environment=environment):
        raise ValueError('SageAttention was requested but is not installed and GPU-verified; install the optional component or disable it')


def probe():
    import torch
    from sageattention import sageattn
    if not torch.cuda.is_available() or torch.cuda.get_device_capability()[0] < 8:
        raise RuntimeError('SageAttention requires a supported NVIDIA Ampere-or-newer CUDA GPU')
    with torch.inference_mode():
        torch.manual_seed(42)
        for dtype in (torch.float16, torch.bfloat16):
            q, k, v = (torch.randn((1, 4, 128, 128), device='cuda', dtype=dtype) for _ in range(3))
            result = sageattn(q, k, v, tensor_layout='HND', is_causal=False)
            expected = torch.nn.functional.scaled_dot_product_attention(q, k, v)
            torch.cuda.synchronize()
            if (result.shape != q.shape or not torch.isfinite(result).all().item()
                    or not torch.allclose(result, expected, atol=.07, rtol=.07)):
                raise RuntimeError('SageAttention GPU output validation failed')
    print('SageAttention 1.0.6 FP16/BF16 GPU kernels verified', flush=True)


if __name__ == '__main__':
    probe()
