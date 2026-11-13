#!/bin/bash
# Levanta o baja una sesión PPP por SIM800 (GPRS) para fallback sin WiFi.
# Requiere parar zeromatic: el mismo /dev/ttyUSB0 no admite AT + PPP a la vez.
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/zeromatic}"
# shellcheck source=load_zeromatic_env.sh
source "$INSTALL_ROOT/bin/load_zeromatic_env.sh"
load_zeromatic_env

LOG="${ZEROMATIC_LOG:-/var/log/zeromatic.log}"
RUN_DIR=/run/zeromatic
LOCK=/run/lock/zeromatic-gprs.lock
CHAT="$RUN_DIR/chat-gprs"
PEER_FILE="$RUN_DIR/peer-gprs"
DISCONNECT_CHAT="${INSTALL_ROOT}/etc/ppp/chat-sim800-disconnect"

mkdir -p "$RUN_DIR"
exec 9>>"$LOCK"
flock -n 9 || { echo "gprs_failover: otro proceso usa GPRS" >>"$LOG"; exit 1; }

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) [gprs] $*" | tee -a "$LOG"; }

ppp_active() {
  ip link show ppp0 >/dev/null 2>&1
}

write_chat_and_peer() {
  local apn="${GPRS_APN:?GPRS_APN vacío — edita /etc/zeromatic/gprs.env}"
  local dev="${GPRS_DEVICE:-/dev/ttyUSB0}"
  local baud="${GPRS_BAUD:-115200}"

  cat >"$CHAT" <<EOF
ABORT 'BUSY'
ABORT 'NO CARRIER'
ABORT 'NO DIALTONE'
ABORT 'ERROR'
ABORT 'NO ANSWER'
TIMEOUT 90
'' AT
OK AT+CPIN?
OK AT+CGDCONT=1,"IP","${apn}"
OK ATD*99***1#
CONNECT ''
EOF
  chmod 600 "$CHAT"

  cat >"$PEER_FILE" <<EOF
${dev}
${baud}
crtscts
modem
lock
noauth
local
defaultroute
usepeerdns
ipparam zeromatic-gprs
persist
maxfail 3
holdoff 20
connect "chat -v -f ${CHAT}"
disconnect "chat -v -f ${DISCONNECT_CHAT}"
EOF
}

stop_zeromatic() {
  if systemctl is-active --quiet zeromatic; then
    log "Parando zeromatic para usar ${GPRS_DEVICE:-/dev/ttyUSB0} con PPP…"
    systemctl stop zeromatic
  fi
  local dev="${GPRS_DEVICE:-/dev/ttyUSB0}"
  sleep 1
  fuser -k "$dev" 2>/dev/null || true
  sleep 1
}

start_zeromatic() {
  if ! systemctl is-active --quiet zeromatic; then
    log "Rearrancando zeromatic…"
    systemctl start zeromatic
  fi
}

cmd_up() {
  if zeromatic_net_reachable; then
    log "Red ya disponible (WiFi/Ethernet) — no hace falta GPRS"
    return 0
  fi
  if ppp_active && zeromatic_net_reachable; then
    log "PPP ya activo"
    return 0
  fi

  [[ "${GPRS_FALLBACK:-1}" == "1" ]] || {
    log "GPRS_FALLBACK desactivado"
    return 1
  }

  write_chat_and_peer
  stop_zeromatic

  log "Iniciando PPP (${GPRS_APN})…"
  poff 2>/dev/null || killall pppd 2>/dev/null || true
  sleep 1
  if ! pppd file "$PEER_FILE"; then
    log "ERROR: pppd no arrancó"
    start_zeromatic
    return 1
  fi

  local timeout="${GPRS_CONNECT_TIMEOUT:-90}"
  local ok=0
  for _ in $(seq 1 "$timeout"); do
    if ip link show ppp0 >/dev/null 2>&1 && zeromatic_net_reachable; then
      ok=1
      break
    fi
    sleep 1
  done

  if [[ "$ok" != 1 ]]; then
    log "ERROR: PPP no obtuvo Internet en ${timeout}s"
    poff 2>/dev/null || killall pppd 2>/dev/null || true
    sleep 2
    start_zeromatic
    return 1
  fi

  log "GPRS/PPP activo (ppp0)"
  return 0
}

cmd_down() {
  log "Cerrando PPP…"
  poff 2>/dev/null || killall pppd 2>/dev/null || true
  sleep 2
  start_zeromatic
  log "PPP cerrado, zeromatic en marcha"
}

cmd_with() {
  cmd_up || return 1
  trap 'cmd_down' EXIT
  "$@"
  local rc=$?
  cmd_down
  trap - EXIT
  return "$rc"
}

usage() {
  echo "Uso: $0 up | down | status | with-session <comando...>"
  exit 2
}

case "${1:-}" in
  up) cmd_up ;;
  down) cmd_down ;;
  status)
    if ppp_active; then
      ip -4 addr show dev ppp0 2>/dev/null || true
      exit 0
    fi
    zeromatic_net_reachable && exit 0
    exit 1
    ;;
  with-session)
    shift
    [[ $# -gt 0 ]] || usage
    cmd_with "$@"
    ;;
  *) usage ;;
esac
