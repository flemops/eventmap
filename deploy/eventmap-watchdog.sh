#!/bin/bash
# 14.9 : watchdog EventMap indépendant de l'application. Sonde /health ; après 3 échecs de suite redémarre
# eventmap (3 redémarrages/h max) ; alerte refresh_stalled -> 1 redémarrage / 6 h ; trace disque >= 90 %.
# Essai à blanc : ST=/tmp/wdt URL=http://127.0.0.1:9/health DRY=1 ./eventmap-watchdog.sh (x4)
set -u
ST=${ST:-/run/eventmap-watchdog}; URL=${URL:-http://127.0.0.1:8000/health}; DRY=${DRY:-0}
mkdir -p "$ST"; now=$(date +%s)
log() { logger -t eventmap-watchdog "$*"; [ "$DRY" = 1 ] && echo "$*"; }
restart() { log "redemarrage eventmap ($1)"; [ "$DRY" = 1 ] || systemctl restart eventmap; }
if curl -sf --max-time 10 -o "$ST/h.json" "$URL"; then
  echo 0 > "$ST/fails"
  if grep -q '"kind":"refresh_stalled"' "$ST/h.json"; then
    last=$(cat "$ST/stalled" 2>/dev/null || echo 0)
    if [ $((now-last)) -gt 21600 ]; then echo "$now" > "$ST/stalled"; restart refresh_stalled; fi
  fi
else
  n=$(( $(cat "$ST/fails" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$ST/fails"; log "health KO ($n/3)"
  if [ "$n" -ge 3 ]; then
    recent=$(awk -v t=$((now-3600)) '$1>t' "$ST/restarts" 2>/dev/null | wc -l)
    if [ "$recent" -lt 3 ]; then echo "$now" >> "$ST/restarts"; restart health_KO; echo 0 > "$ST/fails"; else log "limite 3 redemarrages/h atteinte : aucune action"; fi
  fi
fi
use=$(df --output=pcent / | tail -1 | tr -dc 0-9); [ "$use" -ge 90 ] && log "disque a ${use} %"
exit 0
