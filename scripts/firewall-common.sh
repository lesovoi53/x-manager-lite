# Keep INPUT changes persistent when UFW owns the host firewall.
XM_FIREWALL_ADAPTER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/firewall-adapter.py"
iptables() {
    local rc=0
    python3 "$XM_FIREWALL_ADAPTER" "$@" || rc=$?
    # 1 is the normal absent-rule result. Operational errors must not be hidden
    # by legacy `|| true` cleanup or followed by a false success message.
    if [ "$rc" -gt 1 ]; then
        echo 'X-Manager: firewall operation failed; action stopped.' >&2
        exit "$rc"
    fi
    return "$rc"
}
