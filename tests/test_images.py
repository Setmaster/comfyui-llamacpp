from __future__ import annotations

import base64
import importlib
import io

import numpy as np
import pytest
from PIL import Image

import generation.images as images


class _Tensor:
    """A tensor protocol probe, with explicit CPU transfer accounting."""

    def __init__(self, array, events=None, *, on_cpu=False):
        self.array = array
        self.events = [] if events is None else events
        self.on_cpu = on_cpu

    @property
    def shape(self):
        return self.array.shape

    def detach(self):
        self.events.append(("detach", self.shape))
        return _Tensor(self.array, self.events, on_cpu=self.on_cpu)

    def __getitem__(self, index):
        self.events.append(("slice", index))
        return _Tensor(self.array[index], self.events, on_cpu=self.on_cpu)

    def cpu(self):
        self.events.append(("cpu", self.array.nbytes))
        return _Tensor(self.array, self.events, on_cpu=True)

    def numpy(self):
        assert self.on_cpu, "numpy must follow CPU transfer"
        self.events.append(("numpy", self.shape))
        return self.array


def _old_conversion(array, *, include_batch):
    # Freeze the pre-optimization frame-selection policy independently of the
    # new conversion/count helpers. The PNG encoder itself is unchanged.
    frames = array if array.ndim == 4 else array[np.newaxis, ...]
    if not include_batch:
        frames = frames[:1]
    return [images._encode_frame(frame) for frame in frames]


@pytest.mark.parametrize(
    ("shape", "include_batch", "expected"),
    [
        ((2, 3, 3), False, 1),
        ((2, 3, 3), True, 1),
        ((8, 2, 3, 3), False, 1),
        ((8, 2, 3, 3), True, 8),
        ((0, 2, 3, 3), False, 0),
        ((0, 2, 3, 3), True, 0),
    ],
)
def test_frame_count_reads_only_shape(shape, include_batch, expected):
    tensor = _Tensor(np.zeros(shape, dtype=np.float32))
    assert images.image_tensor_frame_count(tensor, include_batch=include_batch) == expected
    assert tensor.events == []


@pytest.mark.parametrize("include_batch", [False, True])
@pytest.mark.parametrize("bounded", [False, True])
def test_selected_frames_are_sliced_before_one_cpu_transfer(include_batch, bounded):
    array = np.arange(8 * 2 * 3 * 3, dtype=np.float32).reshape(8, 2, 3, 3) / 200
    tensor = _Tensor(array)
    encoder = (
        images.image_tensor_to_data_urls_bounded if bounded else images.image_tensor_to_data_urls
    )
    assert images.image_tensor_frame_count(tensor, include_batch=include_batch) == (
        8 if include_batch else 1
    )
    assert encoder(tensor, include_batch=include_batch) == _old_conversion(
        array, include_batch=include_batch
    )
    expected_bytes = array.nbytes if include_batch else array[:1].nbytes
    assert [event for event in tensor.events if event[0] == "cpu"] == [("cpu", expected_bytes)]
    assert [event[0] for event in tensor.events] == (
        ["detach", "cpu", "numpy"] if include_batch else ["detach", "slice", "cpu", "numpy"]
    )


@pytest.mark.parametrize("channels", [1, 3, 4])
@pytest.mark.parametrize(
    "dtype", [np.float16, np.float32, np.float64, np.int16, np.uint8, np.bool_]
)
@pytest.mark.parametrize("include_batch", [False, True])
def test_tensor_dtype_channels_and_noncontiguous_values_are_byte_exact(
    channels, dtype, include_batch
):
    values = np.arange(3 * 4 * 5 * channels).reshape(3, 4, 5, channels)
    if np.issubdtype(dtype, np.floating):
        values = (values / 100 - 0.25).astype(dtype)
        values.reshape(-1)[:3] = [np.nan, np.inf, -np.inf]
    else:
        values = (values - 50).astype(dtype)
    array = values[:, ::2, ::-1, :]
    assert not array.flags.c_contiguous
    expected = _old_conversion(array, include_batch=include_batch)
    assert images.image_tensor_to_data_urls(_Tensor(array), include_batch=include_batch) == expected
    assert images.image_tensor_to_data_urls(array, include_batch=include_batch) == expected


def test_float_clamp_rounding_and_grayscale_png_bytes_are_preserved():
    frame = np.array([[[np.nan], [np.inf], [-np.inf], [-1.0], [0.5], [2.0]]], dtype=np.float32)
    expected = Image.fromarray(np.array([[0, 255, 0, 0, 128, 255]], dtype=np.uint8))
    buffer = io.BytesIO()
    expected.save(buffer, format="PNG", optimize=True)
    encoded = images.image_tensor_to_data_url(_Tensor(frame))
    assert encoded == "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.parametrize("include_batch", [False, True])
def test_lists_and_conversion_only_wrappers_remain_supported(include_batch):
    array = np.arange(2 * 2 * 3 * 3, dtype=np.uint8).reshape(2, 2, 3, 3)

    class ConversionOnly:
        def __init__(self, *, shape=None):
            if shape is not None:
                self.shape = shape

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return array

    for value in (
        array.tolist(),
        ConversionOnly(),
        ConversionOnly(shape=array.shape),
        ConversionOnly(shape=(None, 2, 3, 3)),
    ):
        assert images.image_tensor_frame_count(value, include_batch=include_batch) == (
            2 if include_batch else 1
        )
        assert images.image_tensor_to_data_urls(
            value, include_batch=include_batch
        ) == _old_conversion(array, include_batch=include_batch)


@pytest.mark.parametrize("shape", [(2,), (2, 3), (1, 2, 3, 4, 3)])
def test_invalid_rank_is_rejected_from_metadata_without_transfer(shape):
    tensor = _Tensor(np.zeros(shape, dtype=np.float32))
    with pytest.raises(ValueError, match="Expected IMAGE shape"):
        images.image_tensor_frame_count(tensor)
    assert tensor.events == []
    with pytest.raises(ValueError, match="Expected IMAGE shape"):
        images.image_tensor_to_data_urls(tensor)


@pytest.mark.parametrize("include_batch", [False, True])
def test_empty_batch_count_matches_empty_encoding(include_batch):
    tensor = _Tensor(np.empty((0, 2, 3, 3), dtype=np.float32))
    assert images.image_tensor_frame_count(tensor, include_batch=include_batch) == 0
    assert images.image_tensor_to_data_urls(tensor, include_batch=include_batch) == []


def test_interruption_before_transfer_does_no_tensor_or_png_work(monkeypatch):
    class Interrupt(BaseException):
        pass

    def cancelled():
        raise Interrupt()

    def unexpected_encoding(_frame):
        pytest.fail("interrupted input reached the encoder")

    tensor = _Tensor(np.zeros((8, 2, 3, 3), dtype=np.float32))
    monkeypatch.setattr(images, "_encode_frame", unexpected_encoding)
    with pytest.raises(Interrupt):
        images.image_tensor_to_data_urls_bounded(
            tensor, include_batch=True, interrupt_check=cancelled
        )
    assert tensor.events == []


def test_interruption_is_checked_between_full_batch_frames(monkeypatch):
    class Interrupt(BaseException):
        pass

    encoded = []

    def encode(frame):
        encoded.append(frame)
        return "encoded"

    def check():
        if encoded:
            raise Interrupt()

    tensor = _Tensor(np.zeros((8, 2, 3, 3), dtype=np.float32))
    monkeypatch.setattr(images, "_encode_frame", encode)
    with pytest.raises(Interrupt):
        images.image_tensor_to_data_urls_bounded(tensor, include_batch=True, interrupt_check=check)
    assert len(encoded) == 1
    assert [event for event in tensor.events if event[0] == "cpu"] == [("cpu", tensor.array.nbytes)]


def test_canonical_preflight_rejects_excess_frames_without_transfer(node_package):
    common = importlib.import_module(f"{node_package.__name__}.nodes.common")
    tensor = _Tensor(np.zeros((4097, 1, 1, 3), dtype=np.float32))
    with pytest.raises(ValueError, match="exceeds 4096 frames"):
        common.collect_images(1, {"image_1": tensor}, include_batch=True, maximum_images=4096)
    assert tensor.events == []


def test_canonical_preflight_and_encoding_transfer_each_batch_only_once(node_package):
    common = importlib.import_module(f"{node_package.__name__}.nodes.common")
    tensor = _Tensor(np.zeros((8, 2, 3, 3), dtype=np.float32))
    result = common.collect_images(1, {"image_1": tensor}, include_batch=True, maximum_images=4096)
    assert len(result) == 8
    assert [event for event in tensor.events if event[0] == "cpu"] == [("cpu", tensor.array.nbytes)]
