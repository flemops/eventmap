#!/usr/bin/env bash
# Banc d'essai du filet de securite de nginx-sync.sh.
#
#   sudo bash deploy/nginx/tests/eprouver-filet.sh
#   sudo DEPOT=/opt/observatory/nginx bash .../eprouver-filet.sh   (autre site)
#
# CE QU'IL PROUVE : que `apply`, quand il echoue, remet TOUT en place — fichiers,
# snippets, lien symbolique — quel que soit l'etat de depart et quel que soit le
# declencheur de l'echec. Ce filet est le seul argument qui autorise a lancer
# `apply` sur le reverse proxy de production ; il merite d'etre verifie, pas
# suppose.
#
# LE SCRIPT TESTE EST CELUI QUI EST LIVRE, non modifie, avec ses chemins
# /etc/nginx en dur. L'isolation vient d'un namespace de montage : on bind-monte
# une copie jetable par-dessus /etc/nginx, donc les ecritures n'existent que
# dans le namespace et meurent avec lui. La production est verifiee intacte en
# fin de course (sha256, inventaire, cible du lien).
#
# nginx, systemctl et curl sont remplaces par des bouchons en tete de PATH,
# pour provoquer chaque echec a volonte. Tout le reste (install, cp, ln,
# readlink, diff...) est le vrai binaire.
#
# Le script sous test etant identique octet pour octet dans les trois depots,
# l'eprouver via un manifeste les couvre tous les trois.
#
# Prerequis : unshare (util-linux) et le droit de monter, donc sudo.
set -uo pipefail

# Par defaut, le depot dont ce banc fait partie : ../ depuis tests/.
DEPOT="${DEPOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RACINE=/tmp/eprouver-filet
STUB="$RACINE/stub"
REPO="$RACINE/repo"
REF="$RACINE/etc-nginx-ref"
TRAVAIL="$RACINE/etc-nginx-run"
RES="$RACINE/resultats"
export RACINE DEPOT

vert()  { printf '\033[32m%s\033[0m\n' "$*"; }
rouge() { printf '\033[31m%s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "A lancer avec sudo (bind-mount)." >&2; exit 2; }
command -v unshare >/dev/null || { echo "unshare absent (paquet util-linux)." >&2; exit 2; }
[[ -f "$DEPOT/nginx-sync.sh"   ]] || { echo "Introuvable : $DEPOT/nginx-sync.sh" >&2; exit 2; }
[[ -f "$DEPOT/nginx-sync.conf" ]] || { echo "Introuvable : $DEPOT/nginx-sync.conf" >&2; exit 2; }

INNER="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/eprouver-filet-interne.sh"
[[ -f "$INNER" ]] || { echo "Introuvable : $INNER" >&2; exit 2; }

# shellcheck source=/dev/null
source "$DEPOT/nginx-sync.conf"

# ---------------------------------------------------------------- preparation
rm -rf "$RACINE"
mkdir -p "$STUB" "$REPO" "$RES"
cp -a /etc/nginx "$REF"
cp -a "$DEPOT/." "$REPO/"

cat > "$STUB/nginx" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "-t" ]]; then
  case "${STUB_NGINX_T:-ok}" in
    ko)
      echo "nginx: [emerg] bouchon : echec simule de nginx -t" >&2
      echo "nginx: configuration file test failed" >&2
      exit 1 ;;
    ko_verrou)
      # Point d'accroche : le script appelle `nginx -t` JUSTE APRES
      # l'installation et JUSTE AVANT la restauration. On profite de cet
      # instant pour rendre la cible non reinscriptible, puis on echoue.
      # La restauration bute alors sur un `cp` impossible — le seul chemin
      # ou le script doit CONSERVER sa sauvegarde au lieu de l'effacer.
      if [[ ! -f "$RACINE_BANC/.verrou-pose" ]]; then
        : > "$RACINE_BANC/.verrou-pose"
        chattr +i "$VERROU_CIBLE" 2>/dev/null \
          || echo "bouchon : chattr +i a echoue sur $VERROU_CIBLE" >&2
        echo "nginx: [emerg] bouchon : echec simule, cible verrouillee" >&2
        exit 1
      fi
      # Appels suivants (celui de la restauration) : on laisse passer.
      echo "nginx: configuration file /etc/nginx/nginx.conf test is successful"
      exit 0 ;;
  esac
  echo "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok"
  echo "nginx: configuration file /etc/nginx/nginx.conf test is successful"
  exit 0
fi
exit 0
EOF

cat > "$STUB/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "reload" ]]; then
  case "${STUB_RELOAD:-ok}" in
    ko) echo "Job for nginx.service failed (bouchon)." >&2; exit 1 ;;
    ko1)
      # Echoue seulement au premier appel (celui de l'apply) : celui de la
      # restauration doit reussir, sinon on ne teste pas le bon chemin.
      if [[ ! -f "$RACINE_BANC/.reload-vu" ]]; then
        : > "$RACINE_BANC/.reload-vu"
        echo "Job for nginx.service failed (bouchon, 1er appel)." >&2
        exit 1
      fi
      exit 0 ;;
    *) exit 0 ;;
  esac
fi
exit 0
EOF

cat > "$STUB/curl" <<'EOF'
#!/usr/bin/env bash
# Le script fait deux appels : l'un avec -w pour le code, l'autre avec -I pour
# les en-tetes. On les distingue sur les arguments.
if [[ "$*" == *"-w"* ]]; then
  echo "${STUB_CODE:-200}"
  exit 0
fi
echo "HTTP/1.1 ${STUB_CODE:-200} OK"
[[ "${STUB_CSP:-1}" != "0" ]] && echo "Content-Security-Policy: default-src 'self'"
exit 0
EOF

chmod +x "$STUB"/nginx "$STUB"/systemctl "$STUB"/curl
export RACINE_BANC="$RACINE"

# ------------------------------------------------------------------ scenario
scenario() { # $1 nom, $2 etat, $3.. VAR=val
  local nom=$1 etat=$2; shift 2
  # Le drapeau immuable pose par le scenario de verrouillage survit au
  # namespace : il porte sur l'inode reel, sous /tmp. A retirer avant de
  # pouvoir effacer la copie de travail.
  [[ -d "$TRAVAIL" ]] && chattr -R -i "$TRAVAIL" 2>/dev/null
  rm -rf "$TRAVAIL"; cp -a "$REF" "$TRAVAIL"
  rm -f "$RACINE/.reload-vu" "$RACINE/.verrou-pose"
  env NOM="$nom" ETAT="$etat" RACINE="$RACINE" RACINE_BANC="$RACINE" \
      "$@" unshare -m bash "$INNER"
}

verdict() { # $1 nom, $2 code attendu, $3 oui|non (doit restaurer)
  local nom=$1 attendu=$2 restaure=$3
  local code notes=() ok=1
  code=$(cat "$RES/$nom.code" 2>/dev/null || echo "?")

  [[ "$code" == "$attendu" ]] || { ok=0; notes+=("code $code au lieu de $attendu"); }

  if [[ "$restaure" == "oui" ]]; then
    if diff -q "$RES/$nom.avant" "$RES/$nom.apres" >/dev/null 2>&1; then
      notes+=("etat integralement restaure, zero residu")
    elif diff <(grep -v '^RESIDUS_bak' "$RES/$nom.avant") \
              <(grep -v '^RESIDUS_bak' "$RES/$nom.apres") >/dev/null 2>&1; then
      ok=0; notes+=("restaure mais $(awk '/^RESIDUS_bak/{print $2}' "$RES/$nom.apres") sauvegarde(s) .bak laissee(s)")
    else
      ok=0; notes+=("ETAT NON RESTAURE")
    fi
  else
    if diff -q "$RES/$nom.avant" "$RES/$nom.apres" >/dev/null 2>&1; then
      ok=0; notes+=("rien n'a change alors que l'apply devait reussir")
    else
      notes+=("etat modifie, comme attendu")
    fi
  fi

  if [[ $ok -eq 1 ]]; then
    vert "  PASS  $nom  — ${notes[*]}"
  else
    rouge "  FAIL  $nom  — ${notes[*]}"
    echo "  ---- diff avant/apres ----"
    diff "$RES/$nom.avant" "$RES/$nom.apres" 2>&1 | sed 's/^/    /'
    echo "  ---- log ----"
    tail -15 "$RES/$nom.log" 2>&1 | sed 's/^/    /'
  fi
  return $((1 - ok))
}

# =========================================================================
PROD_SHA=$(sha256sum "$SITE_CONF" | cut -c1-16)
PROD_SNIP=$(ls -1 /etc/nginx/snippets | sort | tr '\n' ',')
PROD_LIEN=$(readlink "/etc/nginx/sites-enabled/$SITE" || echo "(aucun)")

echo "=== Banc d'essai du filet de securite de nginx-sync.sh ==="
echo "    depot teste : $DEPOT   (site : $SITE)"
echo "    etats de depart : A=snippets absents/lien correct  B=rien d'installe"
echo "                      C=lien pointant ailleurs         D=install impossible"
echo
echec=0

echo "-- Declencheur 1 : nginx -t echoue --"
scenario t1_nginxt_A A STUB_NGINX_T=ko ; verdict t1_nginxt_A 1 oui || echec=1
scenario t1_nginxt_B B STUB_NGINX_T=ko ; verdict t1_nginxt_B 1 oui || echec=1
scenario t1_nginxt_C C STUB_NGINX_T=ko ; verdict t1_nginxt_C 1 oui || echec=1

echo "-- Declencheur 2 : le rechargement echoue --"
scenario t2_reload_A A STUB_RELOAD=ko1 ; verdict t2_reload_A 1 oui || echec=1
scenario t2_reload_C C STUB_RELOAD=ko1 ; verdict t2_reload_C 1 oui || echec=1

echo "-- Declencheur 3 : la verification HTTP echoue --"
scenario t3_curl000_A A STUB_CODE=000 STUB_CSP=0 ; verdict t3_curl000_A 1 oui || echec=1
scenario t3_sanscsp_A A STUB_CODE=200 STUB_CSP=0 ; verdict t3_sanscsp_A 1 oui || echec=1
scenario t3_curl000_B B STUB_CODE=000 STUB_CSP=0 ; verdict t3_curl000_B 1 oui || echec=1

echo "-- Declencheur 4 : l'installation echoue en cours de route --"
scenario t4_install_D D STUB_NGINX_T=ok ; verdict t4_install_D 1 oui || echec=1

echo "-- Declencheur 5 : la RESTAURATION elle-meme echoue --"
# Le seul chemin ou le script doit CONSERVER sa sauvegarde. La cible est rendue
# immuable entre l'installation et la restauration, par le bouchon `nginx -t`.
verdict_alerte() { # $1 nom
  local nom=$1 ok=1 notes=()
  local code; code=$(cat "$RES/$nom.code" 2>/dev/null || echo "?")
  [[ "$code" == "1" ]] || { ok=0; notes+=("code $code au lieu de 1"); }

  grep -q "chattr +i a echoue" "$RES/$nom.log" 2>/dev/null \
    && { ok=0; notes+=("le verrou n'a pas pu etre pose : scenario invalide"); }
  grep -q "restauration de .* IMPOSSIBLE" "$RES/$nom.log" 2>/dev/null \
    && notes+=("echec de copie signale") \
    || { ok=0; notes+=("l'echec de copie n'est PAS signale"); }
  grep -q "CONSERVEES" "$RES/$nom.log" 2>/dev/null \
    && notes+=("sauvegardes annoncees conservees") \
    || { ok=0; notes+=("rien ne dit que les sauvegardes sont conservees"); }
  grep -q "ETAT D'ORIGINE NON RETABLI" "$RES/$nom.log" 2>/dev/null \
    && notes+=("verdict final explicite") \
    || { ok=0; notes+=("pas de verdict final disant que l'etat n'est pas retabli"); }

  # La sauvegarde doit exister encore : c'est le seul exemplaire restant.
  if [[ "$(awk '/^RESIDUS_bak/{print $2}' "$RES/$nom.apres")" -gt \
        "$(awk '/^RESIDUS_bak/{print $2}' "$RES/$nom.avant")" ]]; then
    notes+=("sauvegarde effectivement conservee sur le disque")
  else
    ok=0; notes+=("SAUVEGARDE PERDUE alors que la restauration a echoue")
  fi

  # Et surtout : ne pas annoncer un retour a la normale qui n'a pas eu lieu.
  # C'est la ligne que l'operateur lit ; elle doit dire la verite.
  if grep -qiE "etat d'origine retabli|rechargé dans son etat d'origine" \
       "$RES/$nom.log" 2>/dev/null; then
    ok=0; notes+=("MESSAGE TROMPEUR : annonce l'etat d'origine alors que la restauration a echoue")
  else
    notes+=("aucun faux message de retour a la normale")
  fi

  if [[ $ok -eq 1 ]]; then vert "  PASS  $nom  — ${notes[*]}"
  else
    rouge "  FAIL  $nom  — ${notes[*]}"
    echo "  ---- log ----"; sed 's/^/    /' "$RES/$nom.log" 2>&1 | tail -20
  fi
  return $((1 - ok))
}

scenario t5_verrou_A A STUB_NGINX_T=ko_verrou ; verdict_alerte t5_verrou_A || echec=1

echo "-- Temoin : un apply qui reussit (le banc doit savoir dire oui) --"
scenario t0_succes_A A STUB_NGINX_T=ok ; verdict t0_succes_A 0 non || echec=1

# ---------------------------------------------------------- contre-epreuve
# Un banc qui ne sait pas dire non ne prouve rien. On lui soumet deux versions
# volontairement cassees : il DOIT les recaler. Un PASS ici invaliderait les
# dix verdicts precedents.
echo "-- Contre-epreuve : le banc sait-il detecter un filet troue ? --"

# (a) restaurer() vide : ne defait rien du tout.
sed 's/^restaurer() {$/restaurer() { echo "(restauration neutralisee)" >\&2; return 0; }\nrestaurer_originale() {/' \
  "$REPO/nginx-sync.sh" > "$RACINE/casse-a.sh"
# (b) restaurer() rend les fichiers mais PAS le lien symbolique : exactement le
#     defaut trouve en relecture le 05/09 et corrige depuis.
sed '/^  if \[\[ "\$LIEN_EXISTAIT" -eq 0 \]\]; then$/,/^  fi$/d' \
  "$REPO/nginx-sync.sh" > "$RACINE/casse-b.sh"

for c in a b; do
  if diff -q "$REPO/nginx-sync.sh" "$RACINE/casse-$c.sh" >/dev/null; then
    rouge "  casse-$c.sh identique a l'original : le sed n'a rien coupe, contre-epreuve invalide"
    echec=1
  fi
done

attendu_fail() { # $1 nom, $2 code attendu, $3 oui|non
  if verdict "$1" "$2" "$3" >/dev/null 2>&1; then
    rouge "  ANOMALIE  $1  — le banc a valide un filet troue : il ne teste rien"
    return 1
  fi
  vert "  OK  $1  — le banc recale bien ce filet troue"
  return 0
}

scenario cx_a_restaure_vide A STUB_NGINX_T=ko NSYNC="$RACINE/casse-a.sh"
attendu_fail cx_a_restaure_vide 1 oui || echec=1
scenario cx_b_lien_oublie   C STUB_NGINX_T=ko NSYNC="$RACINE/casse-b.sh"
attendu_fail cx_b_lien_oublie 1 oui || echec=1

echo
echo "=== La production a-t-elle bouge ? ==="
if [[ "$PROD_SHA" == "$(sha256sum "$SITE_CONF" | cut -c1-16)" \
   && "$PROD_SNIP" == "$(ls -1 /etc/nginx/snippets | sort | tr '\n' ',')" \
   && "$PROD_LIEN" == "$(readlink "/etc/nginx/sites-enabled/$SITE" || echo "(aucun)")" ]]; then
  vert "  intacte : sha256 du vhost, inventaire des snippets et cible du lien inchanges"
else
  rouge "  FUITE : la production a ete modifiee !"; echec=1
fi
/usr/sbin/nginx -t 2>&1 | tail -1

chattr -R -i "$TRAVAIL" 2>/dev/null
rm -rf "$RACINE"
echo
[[ $echec -eq 0 ]] && vert "=== TOUS LES SCENARIOS PASSENT ===" || rouge "=== AU MOINS UN SCENARIO ECHOUE ==="
exit $echec
