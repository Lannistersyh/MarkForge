#!/bin/bash
# Standard-library client stays small; the chosen worker keeps CV/UI imports warm.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${MARKFORGE_PYTHON:-}" ]]; then
  python_bin="$MARKFORGE_PYTHON"
elif [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
  python_bin="${CONDA_PREFIX}/bin/python"
elif [[ -x "${HOME}/anaconda3/bin/python" ]]; then
  python_bin="${HOME}/anaconda3/bin/python"
else
  python_bin="$(command -v python3)"
fi
exec "$python_bin" "$script_dir/control.py" "$@"
