"""Compare ENG-92 image transfer paths in an existing NumPy/PyTorch environment.

Windows CUDA example (use the Comfy environment's Python, no installation):
    python tests/benchmark_images.py --device cuda --batch 8 --height 512 --width 512

Local CPU-only harness smoke:
    python tests/benchmark_images.py --device numpy --batch 2 --height 16 --width 16 --repeats 2

The reference freezes the pre-ENG-92 count/materialization/selection sequence
from a466b6b. Both paths use the unchanged PNG encoder. Estimated logical CUDA
transfer bytes are reported separately from synchronized measured wall time;
they are not PCIe counters or a claimed proportional speedup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import PIL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation import images


def _baseline_numpy(tensor):
    value = tensor
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _baseline_count(tensor, include_batch):
    array = _baseline_numpy(tensor)
    return int(array.shape[0]) if array.ndim == 4 and include_batch else 1


def _baseline_encode(tensor, include_batch):
    array = _baseline_numpy(tensor)
    frames = array if array.ndim == 4 else array[np.newaxis, ...]
    if not include_batch:
        frames = frames[:1]
    return [images._encode_frame(frame) for frame in frames]


def _pipeline(tensor, include_batch, *, baseline):
    if baseline:
        return _baseline_count(tensor, include_batch), _baseline_encode(tensor, include_batch)
    return (
        images.image_tensor_frame_count(tensor, include_batch=include_batch),
        images.image_tensor_to_data_urls_bounded(tensor, include_batch=include_batch),
    )


def _measure_pair(baseline, candidate, synchronize, *, warmup, repeats):
    for _ in range(warmup):
        baseline()
        candidate()
    timings = {"baseline": [], "candidate": []}
    operations = (("baseline", baseline), ("candidate", candidate))
    for index in range(repeats):
        for name, operation in operations if index % 2 == 0 else reversed(operations):
            synchronize()
            started = time.perf_counter_ns()
            operation()
            synchronize()
            timings[name].append((time.perf_counter_ns() - started) / 1_000_000)
    return tuple(
        {
            "median_ms": statistics.median(samples),
            "min_ms": min(samples),
            "max_ms": max(samples),
            "samples_ms": samples,
        }
        for samples in timings.values()
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("numpy", "cpu", "cuda"), default="cuda")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--channels", type=int, choices=(1, 3, 4), default=3)
    parser.add_argument("--dtype", choices=("float16", "float32", "float64"), default="float32")
    parser.add_argument("--noncontiguous", action="store_true")
    parser.add_argument("--requires-grad", action="store_true")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.batch, args.height, args.width, args.repeats) <= 0 or args.warmup < 0:
        parser.error("dimensions/repeats must be positive and warmup must be non-negative")
    if args.device == "numpy" and args.requires_grad:
        parser.error("--requires-grad requires a PyTorch device")

    shape = (args.batch, args.height, args.width, args.channels)
    source_shape = (
        (args.batch, args.width, args.height, args.channels) if args.noncontiguous else shape
    )
    source = np.random.default_rng(args.seed).random(source_shape).astype(args.dtype)
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pillow": PIL.__version__,
    }

    def synchronize():
        pass

    if args.device == "numpy":
        tensor = source.transpose(0, 2, 1, 3) if args.noncontiguous else source
    else:
        try:
            import torch
        except ImportError:
            parser.error("use an existing PyTorch environment, or --device numpy for a CPU smoke")
        if args.device == "cuda" and not torch.cuda.is_available():
            parser.error("CUDA is not available in this interpreter")
        tensor = torch.from_numpy(source).to(args.device)
        if args.noncontiguous:
            tensor = tensor.transpose(1, 2)
        tensor.requires_grad_(args.requires_grad)
        environment["torch"] = str(torch.__version__)
        environment["torch_cuda"] = torch.version.cuda
        if args.device == "cuda":
            synchronize = torch.cuda.synchronize
            environment["gpu"] = torch.cuda.get_device_name(tensor.device)

    transfer_bytes = int(source.nbytes) if args.device == "cuda" else 0
    report = {
        "reference": "a466b6b count/materialization/selection, unchanged shared PNG encoder",
        "candidate_images_sha256": hashlib.sha256(Path(images.__file__).read_bytes()).hexdigest(),
        "environment": environment,
        "input": {
            "shape": shape,
            "dtype": args.dtype,
            "device": args.device,
            "noncontiguous": args.noncontiguous,
            "requires_grad": args.requires_grad,
            "seed": args.seed,
        },
        "repeats": args.repeats,
        "warmup_per_path": args.warmup,
        "measurement_order": "alternate baseline/candidate order each repeat",
        "timing": "synchronized perf_counter wall time; allocation and encoding included where applicable",
        "transfer_estimate": "logical CUDA-to-CPU bytes, not measured bus traffic; CPU inputs report zero",
        "policies": {},
    }
    for include_batch in (False, True):
        policy = "all_frames" if include_batch else "first_frame"
        reference = _pipeline(tensor, include_batch, baseline=True)
        candidate = _pipeline(tensor, include_batch, baseline=False)
        if reference != candidate:
            raise AssertionError(f"{policy}: count or encoded PNG bytes changed")
        selected_bytes = transfer_bytes if include_batch else transfer_bytes // args.batch
        stages = (
            (
                "count",
                lambda batch=include_batch: _baseline_count(tensor, batch),
                lambda batch=include_batch: images.image_tensor_frame_count(
                    tensor, include_batch=batch
                ),
                transfer_bytes,
                0,
            ),
            (
                "encode",
                lambda batch=include_batch: _baseline_encode(tensor, batch),
                lambda batch=include_batch: images.image_tensor_to_data_urls_bounded(
                    tensor, include_batch=batch
                ),
                transfer_bytes,
                selected_bytes,
            ),
            (
                "count_and_encode",
                lambda batch=include_batch: _pipeline(tensor, batch, baseline=True),
                lambda batch=include_batch: _pipeline(tensor, batch, baseline=False),
                2 * transfer_bytes,
                selected_bytes,
            ),
        )
        measured = {}
        for name, baseline, current, old_bytes, new_bytes in stages:
            old, new = _measure_pair(
                baseline, current, synchronize, warmup=args.warmup, repeats=args.repeats
            )
            measured[name] = {
                "baseline": old,
                "candidate": new,
                "measured_baseline_over_candidate_ratio": old["median_ms"] / new["median_ms"],
                "estimated_baseline_transfer_bytes": old_bytes,
                "estimated_candidate_transfer_bytes": new_bytes,
            }
        report["policies"][policy] = {
            "png_bytes_equal": True,
            "encoded_frames": len(candidate[1]),
            "stages": measured,
        }
    rendered = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
