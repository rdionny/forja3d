#!/bin/sh
# Abre o Forja3D pelo código no Linux. Veja "No Linux" no README para os pacotes do sistema.
cd "$(dirname "$0")" || exit 1
PYTHONPATH="$PWD/src" exec .venv/bin/python -m forja3d "$@"
