#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON:-python3}
VENV_DIR=${VENV:-"$PROJECT_ROOT/.venv"}

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_CLI="$VENV_DIR/bin/simple_hermes_codex"

install_local_checkout() {
  "$VENV_PYTHON" - "$PROJECT_ROOT" "$VENV_CLI" <<'PY'
import shlex
import sys
import sysconfig
from pathlib import Path

project_root = Path(sys.argv[1]).resolve()
cli_path = Path(sys.argv[2])
site_packages = Path(sysconfig.get_paths()["purelib"])
site_packages.mkdir(parents=True, exist_ok=True)
(site_packages / "simple_hermes_codex_local.pth").write_text(
    str(project_root) + "\n",
    encoding="utf-8",
)
cli_path.write_text(
    "#!/usr/bin/env sh\n"
    f"PYTHONPATH={shlex.quote(str(project_root))}${{PYTHONPATH:+:$PYTHONPATH}} "
    f"exec {shlex.quote(sys.executable)} -m simple_hermes.cli \"$@\"\n",
    encoding="utf-8",
)
cli_path.chmod(0o755)
PY
  INSTALL_MODE="local checkout"
}

if "$VENV_PYTHON" -c "import setuptools" >/dev/null 2>&1; then
  if "$VENV_PYTHON" -m pip install --no-build-isolation -e "$PROJECT_ROOT"; then
    INSTALL_MODE="editable package"
  else
    echo "pip editable install failed; falling back to local checkout install." >&2
    install_local_checkout
  fi
else
  echo "setuptools is not available in this virtualenv; using local checkout install." >&2
  install_local_checkout
fi

"$VENV_PYTHON" -c "import simple_hermes.cli; print('Simple Hermes Codex package import OK')"

if [ ! -x "$VENV_CLI" ]; then
  echo "Expected console script was not created: $VENV_CLI" >&2
  exit 1
fi

cat <<EOF
Setup complete: $INSTALL_MODE.

Run:
  $VENV_CLI

Or activate the virtualenv first:
  . "$VENV_DIR/bin/activate"
  simple_hermes_codex
EOF
