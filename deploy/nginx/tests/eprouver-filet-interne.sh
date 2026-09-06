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

# Le master du bac partage l'espace de PID de l'hote : interrompre le banc le
# laisserait tourner, avec ses montages et son namespace reseau. On le tue donc
# sur toute sortie, y compris une interruption — et TOUJOURS par son PID, jamais
# par un motif : `pkill -f "nginx: master"` frapperait aussi la production.
nettoyer() {
  local rc=$?
  # Un scenario qui s'arrete AVANT d'avoir lance l'apply — sabotage impossible,
  # scenario non applicable — n'ecrivait pas son code de sortie : le verdict
  # lisait « ? » et comptait un echec la ou il n'y en avait pas.
  [[ -n "${RES:-}" && -n "${NOM:-}" && ! -f "$RES/$NOM.code" ]] && echo "$rc" > "$RES/$NOM.code"
  { [[ -f "$PIDF" ]] && kill -TERM "$(cat "$PIDF")"; pkill -f "http.server"; } >/dev/null 2>&1
  # Un scenario defait son propre sabotage. Le drapeau immuable porte sur
  # l'inode reel sous /tmp : il survit au namespace et, s'il reste, contamine
  # TOUS les scenarios suivants — un fichier qu'on ne peut plus reecrire fait
  # echouer leur restauration pour une raison qui n'a rien a voir avec eux.
  # C'est arrive : r8 echouait en serie et passait seul.
  { [[ -n "${VERROU_CIBLE:-}" ]] && chattr -i "$VERROU_CIBLE"; } >/dev/null 2>&1
  return 0
}
trap nettoyer EXIT INT TERM

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
# Etat « avant la migration » : on ne retire que les snippets que l'ancienne
# configuration n'incluait PAS encore. Les retirer tous serait faux et
# fabriquerait un etat impossible — observatory, par exemple, incluait deja
# entetes-observatory.conf dans ses 7 locations avant la migration ; le
# supprimer rendrait l'ancienne config invalide et empecherait tout master de
# demarrer, ce qui ferait echouer le scenario pour une raison artificielle.
enlever_snippets_non_utilises() {
  local d
  for d in "${DESTS[@]}"; do
    [[ "$d" == "$SITE_CONF" ]] && continue
    if [[ -f "$SITE_CONF" ]] && grep -q "$(basename "$d")" "$SITE_CONF"; then
      continue   # deja utilise par l'ancienne config : il existait donc deja
    fi
    rm -f "$d"
  done
}

case "$ETAT" in
  A) ancienne_config; enlever_snippets_non_utilises; ln -sfn "$SITE_CONF" "$LIEN" ;;
  B) # Site jamais installe : le vhost disparait, donc les snippets qui ne
     # servaient qu'a lui aussi. On les retire tous, ce qui est ici coherent.
     for d in "${DESTS[@]}"; do rm -f "$d"; done; rm -f "$LIEN" ;;
  C) ancienne_config; enlever_snippets_non_utilises
     echo "# config de secours" > "/etc/nginx/sites-available/$SITE-secours"
     ln -sfn "/etc/nginx/sites-available/$SITE-secours" "$LIEN" ;;
  D) ancienne_config; ln -sfn "$SITE_CONF" "$LIEN"
     rm -rf /etc/nginx/snippets; : > /etc/nginx/snippets ;;
  E) # Fichiers CONFORMES au depot, mais lien d'activation ABSENT : le site
     # n'est pas servi. Le piege est que rien ne differe cote fichiers.
     rm -f "$LIEN" ;;
  F) # Fichiers conformes, lien present mais DIRIGE AILLEURS.
     echo "# config de secours" > "/etc/nginx/sites-available/$SITE-secours"
     ln -sfn "/etc/nginx/sites-available/$SITE-secours" "$LIEN" ;;
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

# Detournement de server_name : on ajoute au vhost teste le domaine d'un VOISIN.
# `nginx -t` ne rend qu'un [warn] « conflicting server name » ; comme
# sites-enabled est charge par ordre alphabetique, le premier bloc declare
# capture le nom. Le voisin tombe sans qu'aucune erreur ne soit levee.
if [[ "${DETOURNER:-non}" == "oui" ]]; then
  # Le voisin doit etre charge APRES nous. `include sites-enabled/*` est trie,
  # et quand deux blocs declarent le meme server_name, c'est le PREMIER declare
  # qui gagne — le second recoit un simple [warn] « ignored ». Injecter le nom
  # d'un voisin qui nous precede ne vole donc rien : il n'y a pas de victime,
  # et `apply` a raison de reussir. Le scenario n'aurait mesure que sa propre
  # inefficacite. Verifie a la source : l'ordre est eventmap, observatory,
  # portfolio.
  voisin=""
  for l in /etc/nginx/sites-enabled/*; do
    [[ -e "$l" ]] || continue
    n="$(basename "$l")"
    [[ "$n" > "$SITE" ]] || continue
    voisin=$(awk '/^[[:space:]]*server_name[[:space:]]/ {
                    for (i=2;i<=NF;i++){t=$i; sub(/;$/,"",t)
                      if (t!="_" && t !~ /^\*/ && t!=""){print t; exit}}}' "$l")
    [[ -n "$voisin" ]] && break
  done
  if [[ -z "$voisin" ]]; then
    # Dernier site de l'ordre de chargement : il ne peut voler personne.
    # Non applicable n'est pas un echec — le banc doit le dire, pas le compter
    # comme un defaut.
    echo "SCENARIO NON APPLICABLE : $SITE est charge en dernier, il ne peut" >&2
    echo "capter le server_name d'aucun voisin." >&2
    exit 95
  fi
  src_site="$REPO/${FICHIERS[-1]%%|*}"
  ligne=$(grep -n '^[[:space:]]*server_name[[:space:]]' "$src_site" | tail -1 | cut -d: -f1)
  [[ -n "$ligne" ]] || { echo "SABOTAGE IMPOSSIBLE : pas de server_name dans $src_site" >&2; exit 94; }
  sed -i "${ligne}s/;/ $voisin;/" "$src_site"
  grep -q "$voisin" "$src_site" || { echo "SABOTAGE SANS EFFET : $src_site" >&2; exit 94; }
  echo "(banc : $voisin ajoute au server_name de $SITE, ligne $ligne)" >&2
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
# On repart d'un repertoire VIDE a chaque scenario. $SHIM vit dans $RACINE, qui
# survit d'un scenario a l'autre : sans ce nettoyage, l'enrobage `nginx` cree
# par le seul scenario de verrouillage restait en tete de PATH pour TOUS les
# suivants et se rearmait a chaque fois, posant un chattr +i sur le fichier de
# site au moment du `nginx -t`. Les scenarios qui doivent restaurer apres ce
# point echouaient alors sur un « Operation not permitted » sans rapport avec
# ce qu'ils testaient — et passaient parfaitement en isolement. Diagnostic
# couteux, cause triviale.
rm -rf "$SHIM"
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

nettoyer
exit 0
