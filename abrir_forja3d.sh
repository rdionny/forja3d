#!/bin/sh
# Abre o Forja3D pelo código no Linux. Ainda não foi testado em Linux: veja "No Linux" no README.
cd "$(dirname "$0")" || exit 1
PYTHONPATH="$PWD/src" exec .venv/bin/python -m forja3d "$@"
