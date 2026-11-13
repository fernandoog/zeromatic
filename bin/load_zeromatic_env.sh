# shellcheck shell=bash
# Carga /etc/zeromatic/*.env en el shell actual (claves alfanum + _).
load_zeromatic_env() {
  local f key val
  for f in /etc/zeromatic/email.env /etc/zeromatic/gprs.env; do
    [[ -f "$f" ]] || continue
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
      [[ "$line" != *=* ]] && continue
      key="${line%%=*}"
      key="${key%"${key##*[![:space:]]}"}"
      key="${key#"${key%%[![:space:]]*}"}"
      [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
      val="${line#*=}"
      val="${val%"${val##*[![:space:]]}"}"
      val="${val#"${val%%[![:space:]]*}"}"
      val="${val%\"}"; val="${val#\"}"
      val="${val%\'}"; val="${val#\'}"
      printf -v "$key" '%s' "$val"
      export "$key"
    done <"$f"
  done
}

# true si hay ruta hasta Internet (WiFi, eth o PPP ya levantado)
zeromatic_net_reachable() {
  local host="${NET_CHECK_HOST:-smtp.gmail.com}"
  local port="${NET_CHECK_PORT:-587}"
  if timeout 5 bash -c "echo >/dev/tcp/${host}/${port}" 2>/dev/null; then
    return 0
  fi
  ping -c1 -W3 8.8.8.8 >/dev/null 2>&1
}
