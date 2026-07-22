#!/usr/bin/env bash
# Installer for blueman-pios-assistant.
# Copies the Python package into Blueman's applet plugin directory, drops the
# systemd user unit + udev rule, and hooks the current user into the "input"
# group so the debouncer can open /dev/uinput without root.
set -euo pipefail

PACKAGE_NAME="blueman_pios_assistant"
PLUGIN_LINK_NAME="PiOSAssistant.py"

find_blueman_plugin_dir() {
    for candidate in \
        /usr/lib/python3/dist-packages/blueman/plugins/applet \
        /usr/local/lib/python3/dist-packages/blueman/plugins/applet
    do
        if [[ -d "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done
    echo "Could not locate Blueman applet plugin directory. Is blueman installed?" >&2
    return 1
}

do_install() {
    if [[ $EUID -ne 0 ]]; then
        echo "install.sh needs root to write into /usr/lib and /etc/udev. Re-run with sudo." >&2
        exit 1
    fi

    local plugin_dir
    plugin_dir=$(find_blueman_plugin_dir)
    local site_pkgs
    site_pkgs=$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

    echo "Installing package into ${site_pkgs}/${PACKAGE_NAME}"
    rm -rf "${site_pkgs:?}/${PACKAGE_NAME}"
    cp -r "${PACKAGE_NAME}" "${site_pkgs}/${PACKAGE_NAME}"

    echo "Linking Blueman plugin into ${plugin_dir}/${PLUGIN_LINK_NAME}"
    ln -sf "${site_pkgs}/${PACKAGE_NAME}/PiOSAssistant.py" "${plugin_dir}/${PLUGIN_LINK_NAME}"

    echo "Installing udev rule for /dev/uinput"
    install -m 0644 systemd/99-blueman-pios-uinput.rules /etc/udev/rules.d/
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=misc || true

    local target_user="${SUDO_USER:-$USER}"
    echo "Adding ${target_user} to the 'input' group"
    usermod -aG input "$target_user" || true

    echo "Installing systemd user unit"
    install -m 0644 systemd/blueman-pios-assistant.service /etc/systemd/user/
    sudo -u "$target_user" XDG_RUNTIME_DIR="/run/user/$(id -u "$target_user")" \
        systemctl --user daemon-reload || true
    sudo -u "$target_user" XDG_RUNTIME_DIR="/run/user/$(id -u "$target_user")" \
        systemctl --user enable blueman-pios-assistant.service || true

    echo
    echo "Done. Log out and back in (for the 'input' group), then enable"
    echo "'PiOSAssistant' from Blueman's plugin manager."
}

do_uninstall() {
    if [[ $EUID -ne 0 ]]; then
        echo "install.sh --uninstall needs root. Re-run with sudo." >&2
        exit 1
    fi
    local plugin_dir site_pkgs
    plugin_dir=$(find_blueman_plugin_dir || true)
    site_pkgs=$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

    [[ -n "$plugin_dir" ]] && rm -f "${plugin_dir}/${PLUGIN_LINK_NAME}"
    rm -rf "${site_pkgs:?}/${PACKAGE_NAME}"
    rm -f /etc/udev/rules.d/99-blueman-pios-uinput.rules
    rm -f /etc/systemd/user/blueman-pios-assistant.service
    udevadm control --reload-rules || true
    echo "Uninstalled. Group membership in 'input' left in place."
}

case "${1:-install}" in
    install) do_install ;;
    --uninstall|uninstall) do_uninstall ;;
    *) echo "usage: $0 [install|--uninstall]" >&2; exit 2 ;;
esac
