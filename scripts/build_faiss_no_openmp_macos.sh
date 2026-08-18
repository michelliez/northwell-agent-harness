#!/usr/bin/env bash
set -euo pipefail

FAISS_VERSION="1.15.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
SOURCE_DIR="${PROJECT_ROOT}/.local/build/faiss-v${FAISS_VERSION}-no-openmp-src"
BUILD_DIR="${PROJECT_ROOT}/.local/build/faiss-v${FAISS_VERSION}-no-openmp-build"
WHEEL_DIR="${PROJECT_ROOT}/.local/build/wheelhouse"
PATCH_FILE="${SCRIPT_DIR}/patches/faiss-v${FAISS_VERSION}-no-openmp.patch"
UV_CACHE_DIR="${PROJECT_ROOT}/.uv-cache"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This workaround is only intended for macOS." >&2
  exit 2
fi

for command in cmake swig git uv otool; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Missing ${command}. On macOS, install build tools with: brew install cmake swig" >&2
    exit 2
  fi
done

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing project Python at ${PYTHON}. Run uv sync --group dense first." >&2
  exit 2
fi

mkdir -p "$(dirname "${SOURCE_DIR}")" "${WHEEL_DIR}" "${UV_CACHE_DIR}"

if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
  git clone --depth 1 --branch "v${FAISS_VERSION}" \
    https://github.com/facebookresearch/faiss.git "${SOURCE_DIR}"
fi

if git -C "${SOURCE_DIR}" apply --check "${PATCH_FILE}" >/dev/null 2>&1; then
  git -C "${SOURCE_DIR}" apply "${PATCH_FILE}"
elif ! git -C "${SOURCE_DIR}" apply --reverse --check "${PATCH_FILE}" >/dev/null 2>&1; then
  echo "The FAISS source has unexpected changes; refusing to apply the no-OpenMP patch." >&2
  exit 2
fi

cmake -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
  -DFAISS_ENABLE_GPU=OFF \
  -DFAISS_ENABLE_METAL=OFF \
  -DFAISS_ENABLE_PYTHON=ON \
  -DFAISS_ENABLE_C_API=OFF \
  -DFAISS_ENABLE_OPENMP=OFF \
  -DFAISS_ENABLE_MKL=OFF \
  -DFAISS_ENABLE_EXTRAS=OFF \
  -DFAISS_OPT_LEVEL=generic \
  -DBUILD_TESTING=OFF \
  -DBLA_VENDOR=Apple \
  -DPython_EXECUTABLE="${PYTHON}" \
  -DPython3_EXECUTABLE="${PYTHON}" \
  -DCMAKE_BUILD_TYPE=Release

BUILD_JOBS="$(sysctl -n hw.logicalcpu 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
cmake --build "${BUILD_DIR}" --target swigfaiss -j "${BUILD_JOBS}"

(
  cd "${BUILD_DIR}/faiss/python"
  "${PYTHON}" setup.py bdist_wheel --dist-dir "${WHEEL_DIR}"
)

WHEEL="${WHEEL_DIR}/faiss_cpu-${FAISS_VERSION}-py3-none-any.whl"
if [[ ! -f "${WHEEL}" ]]; then
  echo "Expected wheel was not created: ${WHEEL}" >&2
  exit 2
fi

if otool -L "${BUILD_DIR}/faiss/python/_swigfaiss.so" | grep -q libomp; then
  echo "Built FAISS extension still links libomp; refusing to install it." >&2
  exit 2
fi

env -u VIRTUAL_ENV UV_CACHE_DIR="${UV_CACHE_DIR}" uv pip install \
  --python "${PYTHON}" --force-reinstall --no-deps "${WHEEL}"

"${PYTHON}" - <<'PY'
import numpy as np
import torch

x = torch.randn(64, 64)
_ = x @ x.T

import faiss

vectors = np.random.default_rng(7).random((100, 8), dtype=np.float32)
index = faiss.IndexFlatIP(8)
index.add(vectors)
scores, ids = index.search(vectors[:2], 5)
assert index.ntotal == 100
assert scores.shape == (2, 5)
assert ids.shape == (2, 5)
print(f"PASS: torch and FAISS {faiss.__version__} ran together without OpenMP")
PY

echo "Installed serial FAISS ${FAISS_VERSION} into ${PROJECT_ROOT}/.venv."
echo "Re-run this script after uv sync replaces faiss-cpu with the PyPI wheel."
