#!/usr/bin/env bash
# Banc d'essai du filet de securite de nginx-sync.sh — avec un VRAI nginx.
#
#   sudo bash deploy/nginx/tests/eprouver-filet.sh
#   sudo DEPOT=/opt/observatory/nginx bash .../eprouver-filet.sh
#
# CE QU'IL PROUVE : que `apply`, quand il echoue, remet TOUT en place —
# fichiers, snippets, lien symbolique — quel que soit l'etat de depart et quel
# que soit le declencheur. Ce filet est le seul argument qui autorise a lancer
# `apply` sur le reverse proxy de production ; il merite d'etre verifie.
#
# CE QUI EST REEL : un vrai master nginx tourne dans le bac, `nginx -t` valide
# une configuration reellement invalide quand le scenario le demande, le
# rechargement est un vrai SIGHUP, et curl fait de vraies requetes HTTPS sur
# 127.0.0.1:443. Les echecs ne sont pas racontes, ils sont provoques.
#
# CE QUI RESTE SIMULE, et pourquoi :
#   - `systemctl` : il n'y a pas de systemd dans le bac. Le shim traduit
#     `systemctl reload nginx` en un VRAI `nginx -s reload` et sert de point
#     d'accroche pour arreter le master quand le scenario l'exige.
#   - `nginx` : un enrobage mince, present pour le seul scenario de
#     verrouillage, qui doit poser un chattr entre l'installation et la
#     restauration. Il delegue au vrai binaire.
#
# ISOLATION : namespace de MONTAGE (une copie jetable de /etc/nginx est
# bind-montee par-dessus, avec /var/log/nginx et /var/lib/nginx detournes) ET
# namespace de RESEAU (sans lui, le nginx du bac se disputerait les ports 80 et
# 443 avec la production). Le pid du bac est sorti de /run. Le script teste est
# celui QUI EST LIVRE, non modifie, chemins /etc/nginx en dur compris.
#
# Prerequis : unshare, ip (iproute2), python3, et le droit de monter — donc sudo.
set -uo pipefail

DEPOT="${DEPOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
RACINE=/tmp/eprouver-filet
REPO="$RACINE/repo"
REF="$RACINE/etc-nginx-ref"
TRAVAIL="$RACINE/etc-nginx-run"
RES="$RACINE/resultats"
export RACINE DEPOT

vert()  { printf '\033[32m%s\033[0m\n' "$*"; }
rouge() { printf '\033[31m%s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "A lancer avec sudo (bind-mount, netns)." >&2; exit 2; }
for o in unshare ip python3; do
  command -v "$o" >/dev/null || { echo "$o absent." >&2; exit 2; }
done
[[ -f "$DEPOT/nginx-sync.sh"   ]] || { echo "Introuvable : $DEPOT/nginx-sync.sh" >&2; exit 2; }
[[ -f "$DEPOT/nginx-sync.conf" ]] || { echo "Introuvable : $DEPOT/nginx-sync.conf" >&2; exit 2; }
INNER="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/eprouver-filet-interne.sh"
[[ -f "$INNER" ]] || { echo "Introuvable : $INNER" >&2; exit 2; }

# shellcheck source=/dev/null
source "$DEPOT/nginx-sync.conf"

rm -rf "$RACINE"; mkdir -p "$RES"
cp -a /etc/nginx "$REF"

# ------------------------------------------------------------------ scenario
scenario() { # $1 nom, $2 etat, $3.. VAR=val
  local nom=$1 etat=$2; shift 2
  # Le drapeau immuable pose par le scenario de verrouillage porte sur l'inode
  # reel, sous /tmp : il survit au namespace et doit etre retire a la main.
  [[ -d "$TRAVAIL" ]] && chattr -R -i "$TRAVAIL" 2>/dev/null
  rm -rf "$TRAVAIL" "$REPO"; cp -a "$REF" "$TRAVAIL"; cp -a "$DEPOT" "$REPO"
  rm -f "$RACINE/.verrou-pose" "$RACINE/nginx-bac.pid"
  env NOM="$nom" ETAT="$etat" RACINE="$RACINE" "$@" \
      unshare -m -n bash "$INNER"
}

verdict() { # $1 nom, $2 code attendu, $3 oui|non (doit restaurer)
  local nom=$1 attendu=$2 restaure=$3 code notes=() ok=1
  code=$(cat "$RES/$nom.code" 2>/dev/null || echo "?")
  [[ "$code" == "$attendu" ]] || { ok=0; notes+=("code $code au lieu de $attendu"); }
  if [[ "$restaure" == "oui" ]]; then
    if diff -q "$RES/$nom.avant" "$RES/$nom.apres" >/dev/null 2>&1; then
      notes+=("etat integralement restaure, zero residu")
    elif diff <(grep -v '^RESIDUS_bak' "$RES/$nom.avant") \
              <(grep -v '^RESIDUS_bak' "$RES/$nom.apres") >/dev/null 2>&1; then
      ok=0; notes+=("restaure mais $(awk '/^RESIDUS_bak/{print $2}' "$RES/$nom.apres") .bak laissee(s)")
    else ok=0; notes+=("ETAT NON RESTAURE"); fi
  else
    if diff -q "$RES/$nom.avant" "$RES/$nom.apres" >/dev/null 2>&1; then
      ok=0; notes+=("rien n'a change alors que l'apply devait reussir")
    else notes+=("etat modifie, comme attendu"); fi
  fi
  if [[ $ok -eq 1 ]]; then vert "  PASS  $nom  — ${notes[*]}"
  else
    rouge "  FAIL  $nom  — ${notes[*]}"
    echo "  ---- diff avant/apres ----"; diff "$RES/$nom.avant" "$RES/$nom.apres" 2>&1 | sed 's/^/    /'
    echo "  ---- servi ----"; sed 's/^/    /' "$RES/$nom.servi" 2>/dev/null
    echo "  ---- log ----"; tail -18 "$RES/$nom.log" 2>&1 | sed 's/^/    /'
  fi
  return $((1 - ok))
}

verdict_alerte() { # $1 nom — la restauration elle-meme a echoue
  local nom=$1 ok=1 notes=() code
  code=$(cat "$RES/$nom.code" 2>/dev/null || echo "?")
  [[ "$code" == "1" ]] || { ok=0; notes+=("code $code au lieu de 1"); }
  grep -q "chattr +i a echoue" "$RES/$nom.log" 2>/dev/null \
    && { ok=0; notes+=("verrou non pose : scenario invalide"); }
  grep -q "restauration de .* IMPOSSIBLE" "$RES/$nom.log" 2>/dev/null \
    && notes+=("echec de copie signale") || { ok=0; notes+=("echec de copie NON signale"); }
  grep -q "ETAT D'ORIGINE NON RETABLI" "$RES/$nom.log" 2>/dev/null \
    && notes+=("verdict final explicite") || { ok=0; notes+=("pas de verdict final"); }
  if [[ "$(awk '/^RESIDUS_bak/{print $2}' "$RES/$nom.apres")" -gt \
        "$(awk '/^RESIDUS_bak/{print $2}' "$RES/$nom.avant")" ]]; then
    notes+=("sauvegarde conservee sur le disque")
  else ok=0; notes+=("SAUVEGARDE PERDUE"); fi
  if grep -qiE "etat d'origine retabli|rechargé dans son etat d'origine" "$RES/$nom.log" 2>/dev/null; then
    ok=0; notes+=("MESSAGE TROMPEUR")
  else notes+=("aucun faux message de retour a la normale"); fi
  if [[ $ok -eq 1 ]]; then vert "  PASS  $nom  — ${notes[*]}"
  else rouge "  FAIL  $nom  — ${notes[*]}"; echo "  ---- log ----"; tail -20 "$RES/$nom.log" | sed 's/^/    /'; fi
  return $((1 - ok))
}

# =========================================================================
PROD_SHA=$(sha256sum "$SITE_CONF" | cut -c1-16)
PROD_SNIP=$(ls -1 /etc/nginx/snippets | sort | tr '\n' ',')
PROD_LIEN=$(readlink "/etc/nginx/sites-enabled/$SITE" || echo "(aucun)")
PROD_PID=$(cat /run/nginx.pid 2>/dev/null || echo "(aucun)")

echo "=== Banc d'essai du filet de securite — vrai nginx en namespace ==="
echo "    depot teste : $DEPOT   (site : $SITE)"
echo "    A=snippets absents/lien correct  B=rien d'installe"
echo "    C=lien pointant ailleurs         D=install impossible"
echo
echec=0

echo "-- Declencheur 1 : la config installee est REELLEMENT invalide (vrai nginx -t) --"
scenario r1_conf_A A CASSER_CONF=oui AMONT=oui ; verdict r1_conf_A 1 oui || echec=1
scenario r1_conf_C C CASSER_CONF=oui AMONT=oui ; verdict r1_conf_C 1 oui || echec=1
scenario r1_conf_B B CASSER_CONF=oui AMONT=oui ; verdict r1_conf_B 1 oui || echec=1

echo "-- Declencheur 2 : plus de master, le vrai 'nginx -s reload' echoue --"
scenario r2_reload_A A SHIM_RELOAD=tue_avant AMONT=oui ; verdict r2_reload_A 1 oui || echec=1
scenario r2_reload_C C SHIM_RELOAD=tue_avant AMONT=oui ; verdict r2_reload_C 1 oui || echec=1

echo "-- Declencheur 3 : vraie requete HTTPS en echec --"
# (a) le master est arrete apres un rechargement reussi : vraie connexion refusee
scenario r3_refus_A A SHIM_RELOAD=tue_apres AMONT=oui ; verdict r3_refus_A 1 oui || echec=1
# (b) le vrai nginx sert reellement sans CSP
scenario r3_sanscsp_A A CASSER_CSP=oui AMONT=oui ; verdict r3_sanscsp_A 1 oui || echec=1

echo "-- Declencheur 4 : l'installation echoue en cours de route --"
scenario r4_install_D D MASTER=non ; verdict r4_install_D 1 oui || echec=1

echo "-- Declencheur 5 : la RESTAURATION elle-meme echoue (chattr +i reel) --"
scenario r5_verrou_A A CASSER_CONF=oui SHIM_VERROU=oui AMONT=oui ; verdict_alerte r5_verrou_A || echec=1

echo "-- Declencheur 6 : fichiers conformes mais site NON ACTIVE --"
# Le lien d'activation fait partie de l'etat compare. Sans cela, `check`
# repondait « aucune derive » et `apply` court-circuitait avant de reposer le
# lien : le site n'etait pas servi et l'outil annoncait que tout allait bien.
# C'est le defaut BLOQUANT remonte en relecture le 06/09.
verdict_lien() { # $1 nom
  local nom=$1 ok=1 notes=() code
  code=$(cat "$RES/$nom.code" 2>/dev/null || echo "?")
  [[ "$code" == "0" ]] || { ok=0; notes+=("code $code au lieu de 0"); }
  grep -qE "LIEN (MANQUANT|DEVIE|ANORMAL)" "$RES/$nom.log" 2>/dev/null \
    && notes+=("derive du lien detectee") || { ok=0; notes+=("derive du lien NON detectee"); }
  grep -q "Rien a faire" "$RES/$nom.log" 2>/dev/null \
    && { ok=0; notes+=("a court-circuite sur « Rien a faire »"); }
  if grep -q "^${LIEN_ATTENDU} LIEN -> ${SITE_CONF}$" "$RES/$nom.apres" 2>/dev/null; then
    notes+=("lien repose vers $SITE_CONF")
  else
    ok=0; notes+=("LIEN NON RETABLI : $(grep -a "sites-enabled" "$RES/$nom.apres" | head -1)")
  fi
  if [[ $ok -eq 1 ]]; then vert "  PASS  $nom  — ${notes[*]}"
  else rouge "  FAIL  $nom  — ${notes[*]}"; echo "  ---- log ----"; tail -14 "$RES/$nom.log" | sed 's/^/    /'; fi
  return $((1 - ok))
}
LIEN_ATTENDU="/etc/nginx/sites-enabled/$SITE"
scenario r7_lien_absent E AMONT=oui ; verdict_lien r7_lien_absent || echec=1
scenario r7_lien_devie  F AMONT=oui ; verdict_lien r7_lien_devie  || echec=1

echo "-- Cas limite : l'amont applicatif est a l'arret (vrai 502) --"
# Sans upstream, tout proxy_pass renvoie un vrai 502. Le critere du script
# accepte tout code sauf 000 : une config nginx correcte NE DOIT PAS etre
# defaite parce que l'application redemarre au meme moment. C'est la
# correction issue de la relecture ('^[1-5]' et non '^[1-4]'), ici prouvee.
scenario r6_amont_down_A A AMONT=non ; verdict r6_amont_down_A 0 non || echec=1

echo "-- Temoin : apply reussi, vrai 200 servi par un vrai nginx --"
scenario r0_succes_A A AMONT=oui ; verdict r0_succes_A 0 non || echec=1
if grep -q "HTTP/1.1 200" "$RES/r0_succes_A.servi" 2>/dev/null; then
  vert "        et la reponse servie est bien un 200 reel :"
  sed 's/^/          /' "$RES/r0_succes_A.servi"
else
  rouge "        mais la reponse servie n'est pas un 200 :"; sed 's/^/          /' "$RES/r0_succes_A.servi"; echec=1
fi

# ---------------------------------------------------------- contre-epreuve
echo "-- Contre-epreuve : le banc sait-il detecter un filet troue ? --"
cp -a "$DEPOT" "$REPO" 2>/dev/null || true
sed 's/^restaurer() {$/restaurer() { echo "(restauration neutralisee)" >\&2; return 0; }\nrestaurer_originale() {/' \
  "$DEPOT/nginx-sync.sh" > "$RACINE/casse-a.sh"
sed '/^  if \[\[ "\$LIEN_EXISTAIT" -eq 0 \]\]; then$/,/^  fi$/d' \
  "$DEPOT/nginx-sync.sh" > "$RACINE/casse-b.sh"
for c in a b; do
  diff -q "$DEPOT/nginx-sync.sh" "$RACINE/casse-$c.sh" >/dev/null \
    && { rouge "  casse-$c.sh identique a l'original : contre-epreuve invalide"; echec=1; }
done
attendu_fail() {
  if verdict "$1" "$2" "$3" >/dev/null 2>&1; then
    rouge "  ANOMALIE  $1  — le banc a valide un filet troue : il ne teste rien"; return 1
  fi
  vert "  OK  $1  — le banc recale bien ce filet troue"; return 0
}
scenario cx_a_restaure_vide A CASSER_CONF=oui AMONT=oui NSYNC="$RACINE/casse-a.sh"
attendu_fail cx_a_restaure_vide 1 oui || echec=1
scenario cx_b_lien_oublie   C CASSER_CONF=oui AMONT=oui NSYNC="$RACINE/casse-b.sh"
attendu_fail cx_b_lien_oublie 1 oui || echec=1

echo
echo "=== La production a-t-elle bouge ? ==="
if [[ "$PROD_SHA" == "$(sha256sum "$SITE_CONF" | cut -c1-16)" \
   && "$PROD_SNIP" == "$(ls -1 /etc/nginx/snippets | sort | tr '\n' ',')" \
   && "$PROD_LIEN" == "$(readlink "/etc/nginx/sites-enabled/$SITE" || echo "(aucun)")" \
   && "$PROD_PID"  == "$(cat /run/nginx.pid 2>/dev/null || echo "(aucun)")" ]]; then
  vert "  intacte : vhost, snippets, lien symbolique et PID du master inchanges"
else
  rouge "  FUITE : la production a ete modifiee !"; echec=1
fi
/usr/sbin/nginx -t 2>&1 | tail -1

chattr -R -i "$TRAVAIL" 2>/dev/null
rm -rf "$RACINE"
echo
[[ $echec -eq 0 ]] && vert "=== TOUS LES SCENARIOS PASSENT ===" || rouge "=== AU MOINS UN SCENARIO ECHOUE ==="
exit $echec
