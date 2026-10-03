#!/usr/bin/env bash
set -euo pipefail

installer=${1:-uv}
case "${installer}" in
uv | pip | pipx) ;;
-h | --help)
	echo "Usage: $0 [uv|pip|pipx] (default: uv)"
	exit 0
	;;
*)
	echo "Unknown installer: ${installer}. Choose uv, pip, or pipx." >&2
	exit 2
	;;
esac

termux=false
if [[ -n ${TERMUX_VERSION:-} || ${PREFIX:-} == */com.termux/* ]]; then
	termux=true
fi

echo "Installing Fetchtastic with ${installer}..."
case "${installer}" in
uv)
	if ! command -v uv >/dev/null 2>&1; then
		if ${termux}; then
			pkg install -y python uv
		else
			curl -LsSf https://astral.sh/uv/install.sh | sh
			export PATH="${UV_INSTALL_DIR:-${XDG_BIN_HOME:-${HOME}/.local/bin}}:${PATH}"
		fi
	fi
	if ${termux}; then
		# Termux requires its native Python rather than a managed Linux build.
		uv tool install --upgrade --python "${PREFIX}/bin/python" fetchtastic
	else
		uv tool install --upgrade --python '>=3.10' fetchtastic
	fi
	uv tool update-shell
	fetchtastic_bin="$(uv tool dir --bin)/fetchtastic"
	;;
pip)
	if ${termux}; then
		pkg install -y python python-pip
	fi
	if ! command -v python3 >/dev/null 2>&1; then
		echo "Install Python 3.10 or later with venv support, then rerun this script." >&2
		exit 1
	fi
	python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or later is required")'
	fetchtastic_venv="${XDG_DATA_HOME:-${HOME}/.local/share}/fetchtastic/venv"
	fetchtastic_link="${XDG_BIN_HOME:-${HOME}/.local/bin}/fetchtastic"
	if [[ -e ${fetchtastic_link} || -L ${fetchtastic_link} ]]; then
		if [[ ! -L ${fetchtastic_link} ]]; then
			echo "${fetchtastic_link} belongs to another installation. Remove it before switching installers." >&2
			exit 1
		fi
		existing_target=$(readlink "${fetchtastic_link}")
		if [[ ${existing_target} != "${fetchtastic_venv}/bin/fetchtastic" ]]; then
			echo "${fetchtastic_link} belongs to another installation. Remove it before switching installers." >&2
			exit 1
		fi
	fi
	python3 -m venv "${fetchtastic_venv}"
	"${fetchtastic_venv}/bin/python" -m pip install --upgrade fetchtastic
	mkdir -p "${fetchtastic_link%/*}"
	ln -sfn "${fetchtastic_venv}/bin/fetchtastic" "${fetchtastic_link}"
	fetchtastic_bin="${fetchtastic_venv}/bin/fetchtastic"
	;;
pipx)
	if ! command -v pipx >/dev/null 2>&1; then
		echo "Install pipx with your platform's package manager, then rerun this script." >&2
		exit 1
	fi
	pipx install --python python3 fetchtastic
	pipx upgrade fetchtastic
	pipx ensurepath
	fetchtastic_bin="$(pipx environment --value PIPX_BIN_DIR)/fetchtastic"
	;;
*) exit 2 ;;
esac

"${fetchtastic_bin}" version
echo "Installation complete. Restart your terminal if PATH was updated."
echo "Run '${fetchtastic_bin} setup' to choose downloads."
