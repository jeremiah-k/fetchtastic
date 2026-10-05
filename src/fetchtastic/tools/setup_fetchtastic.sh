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
legacy_pip_command=""
last_nonempty_line() {
	local line last=""
	while IFS= read -r line; do
		if [[ ${line} =~ [^[:space:]] ]]; then
			last=${line}
		fi
	done
	printf '%s\n' "${last}"
}

select_default_installer() {
	local existing uv_owns pipx_owns pipx_list uv_bin pipx_bin first_line interpreter manager_root manager_output
	existing=$(command -v fetchtastic 2>/dev/null || true)
	if [[ -z ${existing} ]]; then
		installer=uv
		return
	fi

	uv_owns=false
	uv_bin=""
	if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -Eq '^fetchtastic([[:space:]]|$)'; then
		uv_tool_dir=$(uv tool dir --bin 2>/dev/null || true)
		uv_bin=$(printf '%s\n' "${uv_tool_dir}" | last_nonempty_line)
		if [[ -n ${uv_bin} && ${existing} == "${uv_bin%/}/fetchtastic" ]]; then
			manager_output=$(uv tool dir 2>/dev/null || true)
			manager_root=$(printf '%s\n' "${manager_output}" | last_nonempty_line)
			if [[ -n ${manager_root} && ${existing} -ef "${manager_root%/}/fetchtastic/bin/fetchtastic" ]]; then
				uv_owns=true
			fi
		fi
	fi
	pipx_owns=false
	pipx_bin=""
	if command -v pipx >/dev/null 2>&1; then
		pipx_list=$(pipx list --short 2>/dev/null || pipx list 2>/dev/null || true)
		if printf '%s\n' "${pipx_list}" | grep -Eq '(^|[[:space:]])fetchtastic([[:space:]]|$)'; then
			pipx_bin_dir=$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)
			pipx_bin=$(printf '%s\n' "${pipx_bin_dir}" | last_nonempty_line)
			if [[ -n ${pipx_bin} && ${existing} == "${pipx_bin%/}/fetchtastic" ]]; then
				manager_output=$(pipx environment --value PIPX_LOCAL_VENVS 2>/dev/null || true)
				manager_root=$(printf '%s\n' "${manager_output}" | last_nonempty_line)
				if [[ -n ${manager_root} && ${existing} -ef "${manager_root%/}/fetchtastic/bin/fetchtastic" ]]; then
					pipx_owns=true
				fi
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
			legacy_pip_command=${existing}
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
			if [[ -n ${UV_INSTALL_DIR:-} ]]; then
				uv_install_bin=${UV_INSTALL_DIR}
			elif [[ -n ${XDG_BIN_HOME:-} ]]; then
				uv_install_bin=${XDG_BIN_HOME}
			elif [[ -n ${XDG_DATA_HOME:-} ]]; then
				uv_install_bin="${XDG_DATA_HOME}/../bin"
			else
				uv_install_bin="${HOME}/.local/bin"
			fi
			export PATH="${uv_install_bin}:${PATH}"
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
	fetchtastic_bin="$(uv tool dir --bin | last_nonempty_line)/fetchtastic"
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
	fetchtastic_bin="${legacy_pip_command}"
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
		if ! command -v python3 >/dev/null 2>&1; then
			echo "Install Python 3.10 or later, then rerun this script." >&2
			exit 1
		fi
		python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or later is required")'
		pipx install --python python3 fetchtastic
	fi
	pipx ensurepath
	fetchtastic_bin="$(pipx environment --value PIPX_BIN_DIR | last_nonempty_line)/fetchtastic"
	;;
*) exit 2 ;;
esac

"${fetchtastic_bin}" version
echo "Installation complete. Restart your terminal if PATH was updated."
echo "Run '${fetchtastic_bin} setup' to choose downloads."
