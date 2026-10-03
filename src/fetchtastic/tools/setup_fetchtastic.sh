#!/usr/bin/env bash
set -euo pipefail

installer=${1:-auto}
case "${installer}" in
auto | uv | pip | pipx) ;;
-h | --help)
	echo "Usage: $0 [uv|pip|pipx] (default: preserve the existing manager, otherwise uv)"
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

legacy_pip_python=""
select_default_installer() {
	local existing uv_owns pipx_owns pipx_list uv_bin pipx_bin first_line interpreter
	existing=$(command -v fetchtastic 2>/dev/null || true)
	if [[ -z ${existing} ]]; then
		installer=uv
		return
	fi

	uv_owns=false
	uv_bin=""
	if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -Eq '^fetchtastic([[:space:]]|$)'; then
		uv_bin=$(uv tool dir --bin 2>/dev/null || true)
		if [[ -n ${uv_bin} && ${existing} == "${uv_bin%/}/fetchtastic" ]]; then
			uv_owns=true
		fi
	fi
	pipx_owns=false
	pipx_bin=""
	if command -v pipx >/dev/null 2>&1; then
		pipx_list=$(pipx list --short 2>/dev/null || pipx list 2>/dev/null || true)
		if printf '%s\n' "${pipx_list}" | grep -Eq '(^|[[:space:]])fetchtastic([[:space:]]|$)'; then
			pipx_bin=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
			if [[ -n ${pipx_bin} && ${existing} == "${pipx_bin%/}/fetchtastic" ]]; then
				pipx_owns=true
			fi
		fi
	fi

	if ${uv_owns} && ! ${pipx_owns}; then
		installer=uv
		return
	fi
	if ${pipx_owns} && ! ${uv_owns}; then
		installer=pipx
		return
	fi
	if ${uv_owns} && ${pipx_owns}; then
		echo "Existing Fetchtastic is registered with both uv and pipx; refusing to guess which installation owns '${existing}'." >&2
		echo "Upgrade with the current manager, or uninstall the old copy before explicitly selecting another installer." >&2
		exit 1
	fi

	local fetchtastic_venv fetchtastic_link
	fetchtastic_venv="${XDG_DATA_HOME:-${HOME}/.local/share}/fetchtastic/venv"
	fetchtastic_link="${XDG_BIN_HOME:-${HOME}/.local/bin}/fetchtastic"
	if [[ ${existing} == "${fetchtastic_venv}/bin/fetchtastic" ]]; then
		installer=pip
		return
	fi
	if [[ ${existing} == "${fetchtastic_link}" && -L ${fetchtastic_link} ]]; then
		local managed_target
		managed_target=$(readlink "${fetchtastic_link}" 2>/dev/null || true)
		if [[ ${managed_target} == "${fetchtastic_venv}/bin/fetchtastic" ]]; then
			installer=pip
			return
		fi
	fi

	first_line=$(head -n 1 "${existing}" 2>/dev/null || true)
	if [[ ${first_line} == '#!'* ]]; then
		interpreter=${first_line#\#!}
		interpreter=${interpreter%$'\r'}
		if [[ ${interpreter} != *' '* && -x ${interpreter} ]] && "${interpreter}" -m pip show fetchtastic >/dev/null 2>&1; then
			legacy_pip_python=${interpreter}
			installer=legacy-pip
			return
		fi
	fi

	echo "Existing Fetchtastic found at '${existing}', but its installer could not be identified safely." >&2
	echo "It was left untouched. Upgrade it with its current Python/package manager, or uninstall it before explicitly selecting uv, pip, or pipx." >&2
	exit 1
}

if [[ ${installer} == auto ]]; then
	select_default_installer
fi

display_installer=${installer}
if [[ ${installer} == legacy-pip ]]; then
	display_installer="existing pip"
fi
echo "Installing Fetchtastic with ${display_installer}..."
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
	if uv tool list 2>/dev/null | grep -Eq '^fetchtastic([[:space:]]|$)'; then
		uv tool upgrade fetchtastic
	elif ${termux}; then
		# Termux requires its native Python rather than a managed Linux build.
		uv tool install --python "${PREFIX}/bin/python" fetchtastic
	else
		uv tool install --python '>=3.10' fetchtastic
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
legacy-pip)
	"${legacy_pip_python}" -m pip install --upgrade fetchtastic
	fetchtastic_bin="$(command -v fetchtastic)"
	;;
pipx)
	if ! command -v pipx >/dev/null 2>&1; then
		echo "Install pipx with your platform's package manager, then rerun this script." >&2
		exit 1
	fi
	pipx_list=$(pipx list --short 2>/dev/null || pipx list 2>/dev/null || true)
	if printf '%s\n' "${pipx_list}" | grep -Eq '(^|[[:space:]])fetchtastic([[:space:]]|$)'; then
		pipx upgrade fetchtastic
	else
		pipx install --python python3 fetchtastic
	fi
	pipx ensurepath
	fetchtastic_bin="$(pipx environment --value PIPX_BIN_DIR)/fetchtastic"
	;;
*) exit 2 ;;
esac

"${fetchtastic_bin}" version
echo "Installation complete. Restart your terminal if PATH was updated."
echo "Run '${fetchtastic_bin} setup' to choose downloads."
