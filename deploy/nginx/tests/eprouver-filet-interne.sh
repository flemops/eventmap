#!/usr/bin/env bash
# Corps execute DANS le namespace de montage. Parametres par variables
# d'environnement : NOM, ETAT, DEPOT, RACINE, et les STUB_* que consomment les
# bouchons.
#
# Une fois le bind-mount fait, /etc/nginx est la copie jetable : tout ce que
# nginx-sync.sh y ecrit meurt avec le namespace.
#
# Rien n'est code en dur ici : les chemins surveilles sont deduits du manifeste
# du site teste, exactement comme le fait le script sous test.
set -uo pipefail

STUB="$RACINE/stub"
REPO="$RACINE/repo"
TRAVAIL="$RACINE/etc-nginx-run"
RES="$RACINE/resultats"

mount --bind "$TRAVAIL" /etc/nginx || { echo "bind-mount impossible" >&2; exit 90; }
export PATH="$STUB:$PATH"

# --- lecture du manifeste ---------------------------------------------------
# shellcheck source=/dev/null
source "$REPO/nginx-sync.conf"
LIEN="/etc/nginx/sites-enabled/$SITE"
DESTS=()
for p in "${FICHIERS[@]}"; do DESTS+=("${p#*|}"); done

empreinte() {
  local sortie=$1 f
  {
    for f in "${DESTS[@]}"; do
      if [[ -f "$f" ]]; then
        printf '%s FICHIER %s %s %s\n' "$f" \
          "$(sha256sum "$f" | cut -c1-16)" \
          "$(stat -c '%U:%G' "$f")" "$(stat -c '%a' "$f")"
      else
        printf '%s ABSENT\n' "$f"
      fi
    done
    if [[ -L "$LIEN" ]]; then
      printf '%s LIEN -> %s\n' "$LIEN" "$(readlink "$LIEN")"
    elif [[ -e "$LIEN" ]]; then
      printf '%s FICHIER_ORDINAIRE\n' "$LIEN"
    else
      printf '%s ABSENT\n' "$LIEN"
    fi
    printf 'INV_snippets %s\n' \
      "$(ls -1 /etc/nginx/snippets 2>/dev/null | grep -v '\.bak-' | sort | tr '\n' ',')"
    printf 'INV_sites-available %s\n' \
      "$(ls -1 /etc/nginx/sites-available 2>/dev/null | grep -v '\.bak-' | sort | tr '\n' ',')"
    printf 'INV_sites-enabled %s\n' \
      "$(ls -1 /etc/nginx/sites-enabled 2>/dev/null | sort | tr '\n' ',')"
    printf 'RESIDUS_bak %s\n' \
      "$(find /etc/nginx -name '*.bak-*-nginx-sync' 2>/dev/null | wc -l)"
  } > "$sortie"
}

# --- etat de depart ---------------------------------------------------------
# On part si possible de la sauvegarde reelle laissee par la bascule du
# 05/09 : c'est litteralement la config qui tournait avant, pas un ersatz.
ancienne_config() {
  local bak
  bak=$(ls -1 "$SITE_CONF".bak-*-nginx-sync 2>/dev/null | head -1)
  if [[ -n "$bak" ]]; then cp -a "$bak" "$SITE_CONF"
  else echo "# ancienne config $SITE (ersatz du banc d'essai)" > "$SITE_CONF"; fi
}

# Tous les fichiers du manifeste SAUF la config du site : les snippets.
enlever_snippets() {
  local d
  for d in "${DESTS[@]}"; do [[ "$d" != "$SITE_CONF" ]] && rm -f "$d"; done
}

case "$ETAT" in
  A)  # snippets absents, site present en ancienne version, lien correct
      enlever_snippets; ancienne_config; ln -sfn "$SITE_CONF" "$LIEN" ;;
  B)  # site jamais installe : ni fichiers, ni lien
      enlever_snippets; rm -f "$SITE_CONF" "$LIEN" ;;
  C)  # le lien pointe AILLEURS : site bascule sur une config de secours
      enlever_snippets; ancienne_config
      echo "# config de secours" > "/etc/nginx/sites-available/$SITE-secours"
      ln -sfn "/etc/nginx/sites-available/$SITE-secours" "$LIEN" ;;
  D)  # une destination devient impossible a creer : snippets/ remplace par un
      # fichier ordinaire, donc `install -d` echoue et le bloc d'installation
      # part en erreur au milieu.
      ancienne_config; ln -sfn "$SITE_CONF" "$LIEN"
      rm -rf /etc/nginx/snippets; : > /etc/nginx/snippets ;;
  *)  echo "ETAT inconnu : $ETAT" >&2; exit 91 ;;
esac

empreinte "$RES/$NOM.avant"
# NSYNC permet de substituer une version volontairement cassee du script, pour
# la contre-epreuve : un banc qui ne sait pas dire non ne prouve rien.
bash "${NSYNC:-$REPO/nginx-sync.sh}" apply > "$RES/$NOM.log" 2>&1
echo "$?" > "$RES/$NOM.code"
empreinte "$RES/$NOM.apres"
