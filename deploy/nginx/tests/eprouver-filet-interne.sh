#!/usr/bin/env bash
# Corps execute DANS le namespace de montage ET de reseau.
#
# Ce qui est REEL ici : nginx (un vrai master, demarre dans le bac), `nginx -t`
# (sur la vraie configuration du bac), le rechargement (vrai SIGHUP via
# `nginx -s reload`), et curl (vraies requetes HTTPS sur 127.0.0.1:443).
#
# Ce qui reste simule, faute de pouvoir faire autrement :
#   - `systemctl` : il n'y a pas de systemd dans le bac. Le shim traduit
#     `systemctl reload nginx` en un VRAI `nginx -s reload`, et sert de point
#     d'accroche pour tuer le master quand le scenario l'exige.
#   - `nginx` : un enrobage MINCE, present uniquement pour le scenario de
#     verrouillage, qui doit poser un chattr entre l'installation et la
#     restauration. Il delegue au vrai binaire.
#
# Le namespace reseau est indispensable : sans lui, le nginx du bac se
# disputerait les ports 80 et 443 avec celui de la production.
#
# Variables attendues : NOM ETAT RACINE. Optionnelles : MASTER AMONT
# CASSER_CONF CASSER_CSP SHIM_RELOAD SHIM_VERROU NSYNC.
set -uo pipefail

SHIM="$RACINE/shim"
REPO="$RACINE/repo"
TRAVAIL="$RACINE/etc-nginx-run"
RES="$RACINE/resultats"
LOGS="$RACINE/var-log-nginx"
LIB="$RACINE/var-lib-nginx"
PIDF="$RACINE/nginx-bac.pid"

MASTER="${MASTER:-oui}"
AMONT="${AMONT:-non}"

ip link set lo up || { echo "lo indisponible" >&2; exit 90; }

mkdir -p "$LOGS" "$LIB"
# Le pid doit sortir du /run partage, sinon le bac ecraserait celui de la
# production. On le reecrit dans la copie plutot que de passer par -g, que
# nginx refuse quand nginx.conf declare deja la directive.
sed -i "s|^pid .*|pid $PIDF;|" "$TRAVAIL/nginx.conf"

mount --bind "$TRAVAIL" /etc/nginx  || { echo "bind /etc/nginx impossible" >&2; exit 90; }
mount --bind "$LOGS" /var/log/nginx || { echo "bind /var/log/nginx impossible" >&2; exit 90; }
mount --bind "$LIB"  /var/lib/nginx || { echo "bind /var/lib/nginx impossible" >&2; exit 90; }

# --- lecture du manifeste ---------------------------------------------------
# shellcheck source=/dev/null
source "$REPO/nginx-sync.conf"
LIEN="/etc/nginx/sites-enabled/$SITE"
DESTS=()
for p in "${FICHIERS[@]}"; do DESTS+=("${p#*|}"); done
export VERROU_CIBLE="$SITE_CONF" PIDF RACINE

empreinte() {
  local sortie=$1 f
  {
    for f in "${DESTS[@]}"; do
      if [[ -f "$f" ]]; then
        printf '%s FICHIER %s %s %s\n' "$f" "$(sha256sum "$f" | cut -c1-16)" \
          "$(stat -c '%U:%G' "$f")" "$(stat -c '%a' "$f")"
      else
        printf '%s ABSENT\n' "$f"
      fi
    done
    if [[ -L "$LIEN" ]]; then printf '%s LIEN -> %s\n' "$LIEN" "$(readlink "$LIEN")"
    elif [[ -e "$LIEN" ]]; then printf '%s FICHIER_ORDINAIRE\n' "$LIEN"
    else printf '%s ABSENT\n' "$LIEN"; fi
    printf 'INV_snippets %s\n' "$(ls -1 /etc/nginx/snippets 2>/dev/null | grep -v '\.bak-' | sort | tr '\n' ',')"
    printf 'INV_sites-available %s\n' "$(ls -1 /etc/nginx/sites-available 2>/dev/null | grep -v '\.bak-' | sort | tr '\n' ',')"
    printf 'INV_sites-enabled %s\n' "$(ls -1 /etc/nginx/sites-enabled 2>/dev/null | sort | tr '\n' ',')"
    printf 'RESIDUS_bak %s\n' "$(find /etc/nginx -name '*.bak-*-nginx-sync' 2>/dev/null | wc -l)"
  } > "$sortie"
}

# --- etat de depart ---------------------------------------------------------
ancienne_config() {
  local bak
  bak=$(ls -1 "$SITE_CONF".bak-*-nginx-sync 2>/dev/null | head -1)
  if [[ -n "$bak" ]]; then cp -a "$bak" "$SITE_CONF"
  else echo "# ancienne config $SITE (ersatz du banc)" > "$SITE_CONF"; fi
}
enlever_snippets() { local d; for d in "${DESTS[@]}"; do [[ "$d" != "$SITE_CONF" ]] && rm -f "$d"; done; }

case "$ETAT" in
  A) enlever_snippets; ancienne_config; ln -sfn "$SITE_CONF" "$LIEN" ;;
  B) enlever_snippets; rm -f "$SITE_CONF" "$LIEN" ;;
  C) enlever_snippets; ancienne_config
     echo "# config de secours" > "/etc/nginx/sites-available/$SITE-secours"
     ln -sfn "/etc/nginx/sites-available/$SITE-secours" "$LIEN" ;;
  D) ancienne_config; ln -sfn "$SITE_CONF" "$LIEN"
     rm -rf /etc/nginx/snippets; : > /etc/nginx/snippets ;;
  *) echo "ETAT inconnu : $ETAT" >&2; exit 91 ;;
esac

# --- sabotages du DEPOT : ce que le script s'apprete a installer -------------
# Un sabotage qui ne mord pas rend le scenario muet : le banc dirait « PASS »
# sans avoir rien declenche. Chacun verifie donc son propre effet et fait
# echouer le scenario avec un code distinct s'il n'a pas pris.
#
# On ne bouchonne pas nginx : on lui donne une configuration reellement
# invalide, et c'est le vrai `nginx -t` qui la refuse.
if [[ "${CASSER_CONF:-non}" == "oui" ]]; then
  cible="$REPO/${FICHIERS[0]%%|*}"      # chemin relatif au manifeste, comme le fait le script
  if [[ ! -f "$cible" ]]; then
    echo "SABOTAGE IMPOSSIBLE : $cible introuvable" >&2; exit 92
  fi
  echo "cette_directive_nexiste_pas on;" >> "$cible"
  grep -q "cette_directive_nexiste_pas" "$cible" || { echo "SABOTAGE SANS EFFET : $cible" >&2; exit 92; }
fi

# CSP retiree du snippet : le vrai nginx servira alors sans CSP, et c'est la
# vraie reponse HTTP qui fera echouer la verification.
if [[ "${CASSER_CSP:-non}" == "oui" ]]; then
  retirees=0
  while IFS= read -r f; do
    n=$(grep -c 'add_header Content-Security-Policy' "$f" 2>/dev/null)
    n="${n:-0}"
    if [[ "$n" -gt 0 ]]; then
      sed -i '/add_header Content-Security-Policy/d' "$f"
      retirees=$((retirees + n))
    fi
  done < <(find "$REPO" -type f)
  [[ "$retirees" -gt 0 ]] || { echo "SABOTAGE SANS EFFET : aucune CSP trouvee dans $REPO" >&2; exit 93; }
  echo "(banc : $retirees directive(s) CSP retiree(s) du depot teste)" >&2
fi

# --- amonts applicatifs ------------------------------------------------------
# Sans eux, tout proxy_pass renvoie un vrai 502. C'est un cas utile en soi :
# il verifie que le script ne defait pas une config correcte quand seule
# l'application est a l'arret.
AMONTS_PID=()
if [[ "$AMONT" == "oui" ]]; then
  for port in 3000 8000 8001; do
    (cd /tmp && nohup python3 -m http.server "$port" --bind 127.0.0.1 >/dev/null 2>&1 &)
  done
  sleep 2
fi

# --- shims -------------------------------------------------------------------
mkdir -p "$SHIM"
cat > "$SHIM/systemctl" <<'EOS'
#!/usr/bin/env bash
# Pas de systemd dans le bac : on traduit en signal reel au master du bac.
if [[ "${1:-}" == "reload" ]]; then
  case "${SHIM_RELOAD:-ok}" in
    tue_avant)
      # Le master disparait AVANT le rechargement : `nginx -s reload` echoue
      # alors pour de vrai, faute de processus a signaler.
      [[ -f "$PIDF" ]] && kill -TERM "$(cat "$PIDF")" 2>/dev/null
      sleep 1; rm -f "$PIDF"
      exec /usr/sbin/nginx -s reload ;;
    tue_apres)
      # Rechargement reel, puis le master s'arrete : la verification HTTP qui
      # suit tombe sur une vraie connexion refusee.
      /usr/sbin/nginx -s reload || exit 1
      [[ -f "$PIDF" ]] && kill -TERM "$(cat "$PIDF")" 2>/dev/null
      sleep 1; exit 0 ;;
    *)
      exec /usr/sbin/nginx -s reload ;;
  esac
fi
exit 0
EOS
chmod +x "$SHIM/systemctl"

if [[ "${SHIM_VERROU:-non}" == "oui" ]]; then
  cat > "$SHIM/nginx" <<'EON'
#!/usr/bin/env bash
# Enrobage MINCE : il ne simule rien, il delegue au vrai nginx. Sa seule
# raison d'etre est de poser un verrou immuable a l'instant precis ou le
# script appelle `nginx -t`, c'est-a-dire entre l'installation et la
# restauration — le seul moment ou il peut faire echouer la restauration.
if [[ "${1:-}" == "-t" && ! -f "$RACINE/.verrou-pose" ]]; then
  : > "$RACINE/.verrou-pose"
  chattr +i "$VERROU_CIBLE" 2>/dev/null || echo "chattr +i a echoue" >&2
fi
exec /usr/sbin/nginx "$@"
EON
  chmod +x "$SHIM/nginx"
fi
export PATH="$SHIM:$PATH"

# --- master reel -------------------------------------------------------------
if [[ "$MASTER" == "oui" ]]; then
  if /usr/sbin/nginx -t >/dev/null 2>&1; then
    /usr/sbin/nginx && sleep 1
  else
    echo "(master non demarre : la config de depart ne passe pas nginx -t)" >&2
  fi
fi

empreinte "$RES/$NOM.avant"
bash "${NSYNC:-$REPO/nginx-sync.sh}" apply > "$RES/$NOM.log" 2>&1
echo "$?" > "$RES/$NOM.code"
empreinte "$RES/$NOM.apres"

# Trace de ce qui a reellement ete servi, pour le rapport.
{
  echo "master vivant en fin de scenario : $([[ -f $PIDF ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null && echo oui || echo non)"
  curl -skI --max-time 5 --resolve "$VERIF_HOST:443:127.0.0.1" "https://$VERIF_HOST/" 2>&1 \
    | tr -d '\r' | grep -iE '^(HTTP/|content-security-policy)' || echo "(aucune reponse)"
} > "$RES/$NOM.servi"

{ [[ -f "$PIDF" ]] && kill -TERM "$(cat "$PIDF")"; pkill -f "http.server"; } >/dev/null 2>&1
wait 2>/dev/null
exit 0
