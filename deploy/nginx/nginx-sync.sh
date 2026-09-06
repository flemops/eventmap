#!/usr/bin/env bash
# Synchronise la configuration nginx d'un site depuis son depot vers la VM.
#
#   bash <chemin>/nginx-sync.sh check        # compare, ne modifie rien, sans sudo
#   sudo bash <chemin>/nginx-sync.sh apply   # installe, teste, recharge
#
# CE FICHIER EST GENERIQUE ET IDENTIQUE DANS LES TROIS DEPOTS (portfolio,
# eventmap, observatory). Tout ce qui est propre a un site vit dans le
# manifeste nginx-sync.conf, place a cote de ce script. Ne jamais adapter ce
# script a un site : le modifier ici, puis le recopier tel quel dans les autres
# depots. Un `diff` entre les trois copies doit toujours etre vide.
#
# `check` sort en 0 si la VM est conforme au depot, en 1 s'il y a derive.
# `apply` ne laisse pas nginx casse : sauvegarde de chaque cible existante,
# `nginx -t` apres copie, rechargement surveille, verification du comportement
# reel, et restauration complete des que l'une de ces etapes echoue.
#
# Aucun appel reseau sortant. Le seul curl vise 127.0.0.1 : il verifie nginx
# lui-meme, sans passer par Cloudflare, dont le cache ou les regles de
# transformation pourraient masquer une regression.
#
# LIMITE DE CONFIANCE : `apply` tourne en root et lit son manifeste dans le
# repertoire du depot, qui appartient a l'utilisateur applicatif. Quiconque
# ecrit dans ce depot peut donc influencer ce que root installe — c'est deja
# vrai du script lui-meme. En revanche nginx, lui, ne lit JAMAIS depuis le
# depot : les fichiers sont copies dans /etc/nginx par cette etape explicite.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTE="$BASE/nginx-sync.conf"
[[ -f "$MANIFESTE" ]] || { echo "Manifeste introuvable : $MANIFESTE" >&2; exit 2; }

# Le manifeste doit definir :
#   SITE        nom du site (= nom du fichier dans sites-available et sites-enabled)
#   VERIF_HOST  hote a interroger en local pour verifier le resultat
#   SITE_CONF   destination absolue de la configuration du site (celle qu'on relie)
#   FICHIERS    tableau "source relative au manifeste|destination absolue",
#               DANS L'ORDRE D'INSTALLATION : les snippets AVANT le fichier de
#               site qui les inclut, sinon `nginx -t` echoue sur un include
#               manquant.
# shellcheck source=/dev/null
source "$MANIFESTE"

for v in SITE VERIF_HOST SITE_CONF; do
  [[ -n "${!v:-}" ]] || { echo "$v non defini dans $MANIFESTE" >&2; exit 2; }
done

# Sous `set -u`, ${#FICHIERS[@]} sur un nom non declare ou sur un scalaire leve
# « unbound variable » et sort en 1 — or 1 est aussi le code de « derive
# detectee ». Un appelant scripte confondrait un manifeste casse avec une
# derive normale. D'ou le controle explicite, qui sort en 2 dans les trois cas.
declare -p FICHIERS >/dev/null 2>&1 \
  || { echo "FICHIERS non defini dans $MANIFESTE" >&2; exit 2; }
[[ "$(declare -p FICHIERS)" == "declare -a"* ]] \
  || { echo "FICHIERS doit etre un tableau dans $MANIFESTE" >&2; exit 2; }
[[ "${#FICHIERS[@]}" -gt 0 ]] || { echo "FICHIERS vide dans $MANIFESTE" >&2; exit 2; }

LIEN="/etc/nginx/sites-enabled/$SITE"

srcs=(); dsts=()
for paire in "${FICHIERS[@]}"; do
  # Exactement un `|`. Sans ce controle, une entree a deux separateurs perd son
  # segment central en silence : %%|* prend jusqu'au PREMIER, ##*| apres le
  # DERNIER, et le fichier du milieu n'est jamais installe ni compare.
  [[ "$paire" == *"|"* && "$paire" != *"|"*"|"* ]] \
    || { echo "Entree FICHIERS invalide (il faut exactement un |) : [$paire]" >&2; exit 2; }
  src="$BASE/${paire%%|*}"
  dst="${paire#*|}"
  # Crochets dans les messages : sans eux, un espace parasite autour du `|`
  # donne une erreur de chemin parfaitement invisible a l'oeil.
  [[ "$dst" == /* ]] || { echo "Destination non absolue : [$dst]" >&2; exit 2; }
  [[ -f "$src" ]] || { echo "Introuvable dans le depot : [$src]" >&2; exit 2; }
  srcs+=("$src"); dsts+=("$dst")
done

# Le lien de sites-enabled pointera sur SITE_CONF : celui-ci doit donc etre
# l'un des fichiers reellement installes, sinon on relierait un fichier que ce
# script ne pose jamais.
trouve=0
for d in "${dsts[@]}"; do [[ "$d" == "$SITE_CONF" ]] && trouve=1; done
[[ "$trouve" -eq 1 ]] \
  || { echo "SITE_CONF [$SITE_CONF] ne figure pas dans FICHIERS" >&2; exit 2; }

ACTION="${1:-}"
if [[ "$ACTION" != "check" && "$ACTION" != "apply" ]]; then
  echo "Usage : bash $0 check   |   sudo bash $0 apply" >&2
  exit 2
fi

# ---------------------------------------------------------------- comparaison
# mktemp plutot qu'un chemin devinable dans /tmp : ce script tourne en root sur
# un repertoire ou tout le monde peut ecrire, un lien symbolique pose d'avance
# suffirait sinon a lui faire ecraser un fichier arbitraire.
TMP_DIFF="$(mktemp)"
trap 'rm -f "$TMP_DIFF"' EXIT

drift=0
echo "==> [$SITE] Comparaison depot -> VM"
for i in "${!srcs[@]}"; do
  src="${srcs[$i]}"; dst="${dsts[$i]}"
  if [[ ! -f "$dst" ]]; then
    echo "  MANQUANT sur la VM : $dst"
    drift=1
  elif diff -u "$dst" "$src" > "$TMP_DIFF" 2>/dev/null; then
    echo "  conforme : $dst"
  else
    echo "  DERIVE : $dst  (- VM / + depot)"
    sed 's/^/    /' "$TMP_DIFF"
    drift=1
  fi
done

# Le LIEN D'ACTIVATION fait partie de l'etat compare, au meme titre que les
# fichiers. Sans cela, un site dont les fichiers sont conformes mais dont le
# lien manque — ou pointe ailleurs — etait declare « aucune derive », et
# `apply` court-circuitait avant meme de reposer le lien : le site n'etait pas
# servi et l'outil annoncait que tout allait bien.
if [[ ! -L "$LIEN" ]]; then
  if [[ -e "$LIEN" ]]; then
    echo "  LIEN ANORMAL : $LIEN existe mais n'est pas un lien symbolique"
  else
    echo "  LIEN MANQUANT sur la VM : $LIEN  (le site n'est pas servi)"
  fi
  drift=1
elif [[ "$(readlink "$LIEN")" != "$SITE_CONF" ]]; then
  echo "  LIEN DEVIE : $LIEN -> $(readlink "$LIEN")  (attendu : $SITE_CONF)"
  drift=1
else
  echo "  conforme : $LIEN -> $SITE_CONF"
fi

if [[ "$ACTION" == "check" ]]; then
  if [[ "$drift" -eq 0 ]]; then
    echo "==> Aucune derive."
    exit 0
  fi
  echo "==> Derive detectee. Pour aligner la VM sur le depot : sudo bash $0 apply" >&2
  exit 1
fi

# -------------------------------------------------------------------- apply
[[ $EUID -eq 0 ]] || { echo "apply doit etre lance avec sudo." >&2; exit 2; }

if [[ "$drift" -eq 0 ]]; then
  echo "==> Rien a faire : la VM est deja conforme au depot."
  exit 0
fi

# --- photographie des VOISINS, avant toute modification ---------------------
# La verification post-rechargement ne regarde que $VERIF_HOST. Or une
# configuration syntaxiquement valide peut capter le server_name d'un voisin :
# `nginx -t` ne rend qu'un [warn] « conflicting server name », notre propre
# sonde repond 200, et le site voisin tombe sans que rien ne le signale. Ce
# reverse proxy sert trois domaines : le filet doit les couvrir tous.
#
# On compare la CSP servie, pas le code HTTP. Tous les add_header sont en
# `always`, donc un 502 du a un applicatif qui redemarre porte la MEME CSP :
# le critere est insensible a la sante des applications et sensible a un
# detournement de server_name. C'est ce qui evite les restaurations a tort.
#
# On compare aussi AVANT/APRES plutot qu'a un attendu absolu : un voisin deja
# casse le reste, sans nous faire echouer pour un degat qu'on n'a pas cause.
hote_representatif() { # $1 = fichier de vhost -> premier nom de serveur reel
  awk '/^[[:space:]]*server_name[[:space:]]/ {
         for (i = 2; i <= NF; i++) {
           t = $i; sub(/;$/, "", t)
           if (t != "_" && t !~ /^\*/ && t != "") { print t; exit }
         }
       }' "$1" 2>/dev/null || true
}

csp_servie() { # $1 = hote -> valeur de l'en-tete, vide si aucun
  curl -skI --max-time 5 --resolve "$1:443:127.0.0.1" "https://$1/" 2>/dev/null \
    | tr -d '\r' \
    | awk 'tolower($1) == "content-security-policy:" {
             sub(/^[^:]*:[[:space:]]*/, ""); print; exit }' || true
}

voisins=(); csp_avant=()
for lien in /etc/nginx/sites-enabled/*; do
  [[ -e "$lien" ]] || continue
  nom="$(basename "$lien")"
  [[ "$nom" == "$SITE" ]] && continue
  hote="$(hote_representatif "$lien")"
  [[ -n "$hote" ]] || continue
  avant="$(csp_servie "$hote")"
  # Sans reponse exploitable avant, il n'y a pas de reference : on ne juge pas.
  [[ -n "$avant" ]] || continue
  voisins+=("$hote"); csp_avant+=("$avant")
done
if [[ "${#voisins[@]}" -gt 0 ]]; then
  echo "==> Voisins sous surveillance : ${voisins[*]}"
fi

STAMP="$(date +%Y%m%d-%H%M%S)"

# On memorise TOUT ce qu'on s'apprete a modifier, y compris les absences : une
# restauration qui recree un fichier ou un lien qui n'existait pas avant n'est
# pas une restauration.
existait=(); baks=()
LIEN_EXISTAIT=0
LIEN_CIBLE=""
[[ -e "$LIEN" ]] && LIEN_EXISTAIT=1
# La cible et pas seulement l'existence : `ln -sfn` ecrase un lien qui pointait
# ailleurs, et le supprimer ne suffirait pas a revenir en arriere.
[[ -L "$LIEN" ]] && LIEN_CIBLE="$(readlink "$LIEN")"

echo "==> Sauvegarde"
for i in "${!dsts[@]}"; do
  dst="${dsts[$i]}"
  bak="${dst}.bak-${STAMP}-nginx-sync"
  if [[ -f "$dst" ]]; then
    cp -a "$dst" "$bak"
    existait+=(1); baks+=("$bak")
    echo "  $bak"
  else
    existait+=(0); baks+=("$bak")
    echo "  (absent, sera cree : $dst)"
  fi
done

# errexit est desactive dans toute la fonction : une restauration qui s'arrete
# a la premiere erreur laisserait nginx dans l'etat intermediaire qu'elle est
# precisement censee defaire, et sans le moindre message.
restaurer() {
  echo "==> RESTAURATION de l'etat precedent" >&2
  set +e
  local restauration_ok=1

  # Ordre inverse de l'installation : le fichier de site d'abord, les snippets
  # ensuite, pour ne jamais laisser un include pointer vers un fichier disparu.
  for ((i = ${#dsts[@]} - 1; i >= 0; i--)); do
    if [[ "${existait[$i]}" -eq 1 ]]; then
      cp -a "${baks[$i]}" "${dsts[$i]}" || {
        echo "==> ALERTE : restauration de ${dsts[$i]} IMPOSSIBLE (copie dans ${baks[$i]})" >&2
        restauration_ok=0
      }
    else
      rm -f "${dsts[$i]}" || restauration_ok=0
    fi
  done

  if [[ "$LIEN_EXISTAIT" -eq 0 ]]; then
    rm -f "$LIEN" || restauration_ok=0
  elif [[ -n "$LIEN_CIBLE" ]]; then
    ln -sfn "$LIEN_CIBLE" "$LIEN" || {
      echo "==> ALERTE : impossible de refaire pointer $LIEN vers $LIEN_CIBLE" >&2
      restauration_ok=0
    }
  fi

  local recharge_ok=0
  if nginx -t; then
    if systemctl reload nginx; then
      recharge_ok=1
    else
      echo "==> ALERTE : le rechargement de nginx a echoue." >&2
      echo "    nginx tourne encore sur sa config en memoire ; comprendre pourquoi" >&2
      echo "    avant tout redemarrage (systemctl status nginx)." >&2
    fi
  else
    echo "==> ALERTE : la configuration en place ne passe pas nginx -t." >&2
    echo "    nginx tourne encore sur sa config en memoire ; NE PAS le redemarrer." >&2
    echo "    $SITE_CONF vient d'etre restaure a l'identique : si nginx -t echoue" >&2
    echo "    encore, la cause est AILLEURS. Lire le fichier et la ligne que nginx -t" >&2
    echo "    nomme ci-dessus — ce reverse proxy est partage par plusieurs sites, et" >&2
    echo "    le vhost fautif peut tres bien ne pas etre celui qu'on deployait." >&2
  fi

  # Le mot de la fin doit dire la verite : c'est la ligne que l'operateur lit.
  # Annoncer un retour a la normale apres une restauration ratee est le pire
  # des deux mondes — l'echec est signale plus haut, mais noye.
  #
  # Les sauvegardes, elles, ne survivent qu'a une restauration incomplete :
  # quand tout est revenu en place, elles sont l'exact doublon des fichiers
  # restaures et un .bak s'accumulerait par echec. Si une copie a echoue, en
  # revanche, la sauvegarde est le SEUL exemplaire restant : on n'y touche pas.
  if [[ "$restauration_ok" -eq 1 ]]; then
    for i in "${!baks[@]}"; do
      [[ "${existait[$i]}" -eq 1 ]] && rm -f "${baks[$i]}"
    done
    if [[ "$recharge_ok" -eq 1 ]]; then
      echo "==> Etat d'origine retabli et nginx recharge." >&2
    else
      # Les fichiers sont bien revenus, mais nginx n'a pas pu etre recharge :
      # sans cette ligne, la derniere chose lue serait une ALERTE technique,
      # sans dire ce qui est vrai — l'etat sur le disque, lui, est bon.
      echo "==> Etat d'origine retabli sur le disque, mais nginx N'A PAS ete recharge." >&2
      echo "    Il continue de servir sa configuration en memoire." >&2
    fi
  else
    echo "==> ETAT D'ORIGINE NON RETABLI — voir les ALERTES ci-dessus." >&2
    echo "    Les sauvegardes .bak-${STAMP}-nginx-sync sont CONSERVEES : ce sont" >&2
    echo "    peut-etre les seuls exemplaires. Reprendre a la main avant tout" >&2
    echo "    redemarrage de nginx." >&2
  fi

  set -e
}

# L'installation est encapsulee pour la meme raison que le reste : sous set -e,
# un `install` qui echoue au milieu de la boucle sortirait sans restaurer et
# sans un mot, en laissant /etc/nginx a moitie a jour. nginx ne casserait pas
# tout de suite — il tourne sur sa config en memoire — mais le premier
# rechargement venu, declenche par n'importe quel autre chantier, revelerait
# l'etat intermediaire.
installer() {
  # Les repertoires de destination, deduits du manifeste et non codes en dur :
  # une destination hors /etc/nginx/snippets doit fonctionner aussi.
  local i
  for i in "${!dsts[@]}"; do
    install -d -m 755 -o root -g root "$(dirname "${dsts[$i]}")" || return 1
  done
  for i in "${!srcs[@]}"; do
    install -m 644 -o root -g root "${srcs[$i]}" "${dsts[$i]}" || return 1
    echo "  ${dsts[$i]}"
  done
  ln -sfn "$SITE_CONF" "$LIEN" || return 1
}

echo "==> Installation"
if ! installer; then
  echo "==> L'installation a echoue." >&2
  restaurer
  exit 1
fi

echo "==> nginx -t"
if ! nginx -t; then
  restaurer
  exit 1
fi

# Le PID du master, tel que nginx lui-meme le connait : la directive `pid` de
# la configuration, et non un chemin devine.
pid_master() {
  local f
  f=$(nginx -T 2>/dev/null | awk '/^[[:space:]]*pid[[:space:]]/{gsub(/;/,"",$2); print $2; exit}')
  [[ -n "$f" ]] || f=/run/nginx.pid
  [[ -r "$f" ]] && cat "$f"
}
# `|| true` obligatoire : pgrep sort en 1 quand il ne trouve aucun processus,
# et sous `pipefail` cela fait echouer tout le pipeline, donc l'affectation,
# donc — avec `set -e` — le script entier. Il mourrait ici, juste apres le
# rechargement et AVANT la restauration, en laissant /etc/nginx modifie.
# Trouve par le banc d'essai, scenario r3_refus_A.
MASTER_PID="$(pid_master || true)"
WORKERS_AVANT=""
if [[ -n "$MASTER_PID" ]]; then
  WORKERS_AVANT="$(pgrep -P "$MASTER_PID" 2>/dev/null | sort | tr '\n' ' ' || true)"
fi

echo "==> Rechargement"
if ! systemctl reload nginx; then
  echo "==> Le rechargement a echoue." >&2
  restaurer
  exit 1
fi

# ATTENDRE QUE LE RECHARGEMENT AIT REELLEMENT PRIS. `nginx -s reload` rend la
# main des l'envoi du SIGHUP : pendant un court instant, les anciens workers
# repondent encore, sous l'ANCIENNE configuration. Sonder tout de suite fait
# donc valider le deploiement sur le comportement d'avant — mesure sur cette
# VM : la premiere sonde renvoie l'ancienne CSP, la suivante la bonne.
# Un simple delai serait un pari ; on attend un fait : le renouvellement
# complet du jeu de workers du master.
if [[ -n "$MASTER_PID" && -n "$WORKERS_AVANT" ]]; then
  echo "==> Attente du renouvellement des workers"
  for _ in $(seq 1 20); do
    workers_apres="$(pgrep -P "$MASTER_PID" 2>/dev/null | sort | tr '\n' ' ' || true)"
    [[ -n "$workers_apres" && "$workers_apres" != "$WORKERS_AVANT" ]] && break
    sleep 0.5
  done
  if [[ "${workers_apres:-}" == "$WORKERS_AVANT" ]]; then
    echo "  AVERTISSEMENT : les workers n'ont pas change en 10 s. La verification" >&2
    echo "  qui suit porte peut-etre encore sur l'ancienne configuration." >&2
  fi
fi

# Verification du comportement reel, pas seulement de la syntaxe.
#
# `systemctl reload` rend la main des l'envoi du SIGHUP, pas quand la nouvelle
# config sert : sans attente, on peut interroger un worker encore sous l'ancienne
# config et valider un deploiement qui ne l'est pas. D'ou la boucle.
#
# Le critere reste volontairement large : on valide nginx, pas la sante de
# l'application. Un 502 parce que le service applicatif redemarre au meme moment
# ne doit pas defaire une modification nginx correcte : tout code HTTP est donc
# accepte, y compris les 5xx, qui portent de toute facon les en-tetes puisque
# tous les add_header sont en `always`. Seul le code 000 — curl n'a pas obtenu
# de reponse — est refuse : c'est le vrai signal « nginx ne repond plus ».
# Et "au moins une CSP" et non "exactement une", pour ne pas declencher de
# restauration le jour ou l'application en emettrait une de son cote.
echo "==> Verification HTTP en local ($VERIF_HOST)"
verif_ok=0
code=000
csp=0
for _ in $(seq 1 10); do
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 \
           --resolve "$VERIF_HOST:443:127.0.0.1" \
           "https://$VERIF_HOST/" || echo 000)
  csp=$(curl -skI --max-time 5 --resolve "$VERIF_HOST:443:127.0.0.1" \
          "https://$VERIF_HOST/" | tr -d '\r' \
          | grep -ci '^content-security-policy:' || true)
  csp="${csp:-0}"
  if [[ "$code" =~ ^[1-5][0-9][0-9]$ ]] && [[ "$csp" -ge 1 ]]; then
    verif_ok=1
    break
  fi
  sleep 1
done

echo "  HTTPS 127.0.0.1 -> HTTP $code, en-tetes CSP comptes : $csp"
if [[ "$verif_ok" -eq 0 ]]; then
  echo "==> La verification HTTP a echoue apres 10 tentatives." >&2
  restaurer
  exit 1
fi
if [[ "$csp" -gt 1 ]]; then
  echo "  AVERTISSEMENT : $csp en-tetes Content-Security-Policy sur la meme reponse."
  echo "  Le navigateur applique alors l'INTERSECTION des politiques. A verifier."
fi

# Les voisins servent-ils toujours la meme chose qu'avant ? Leur configuration
# n'a pas ete touchee : toute difference vient donc de ce qu'on vient
# d'installer.
for i in "${!voisins[@]}"; do
  hote="${voisins[$i]}"
  apres="$(csp_servie "$hote")"
  if [[ "$apres" != "${csp_avant[$i]}" ]]; then
    echo "==> $hote ne sert plus la meme politique qu'avant ce deploiement." >&2
    echo "    Sa configuration n'a pourtant pas ete modifiee : le vhost installe" >&2
    echo "    capte probablement son server_name (nginx ne rend qu'un [warn]" >&2
    echo "    « conflicting server name », pas une erreur). Restauration." >&2
    restaurer
    exit 1
  fi
done
if [[ "${#voisins[@]}" -gt 0 ]]; then
  echo "  Voisins inchanges : ${voisins[*]}"
fi

echo "==> OK. La VM est alignee sur le depot pour $SITE."
# Volontairement des if et non `[[ ... ]] && echo` : sous set -e, un test faux en
# derniere commande ferait sortir le script en 1 alors que tout s'est bien passe.
for i in "${!baks[@]}"; do
  if [[ "${existait[$i]}" -eq 1 ]]; then
    echo "    Sauvegarde conservee : ${baks[$i]}"
  fi
done
exit 0
