#!/usr/bin/env bash
# Installation d'EventMap sur la VM Oracle (Ubuntu 22.04 aarch64).
# À lancer en root depuis /opt/eventmap après un `git clone` :
#     sudo bash deploy/install.sh
#
# Idempotent : relançable après un `git pull` pour mettre à jour.
set -euo pipefail

APP_DIR=/opt/eventmap
APP_USER=eventmap
ENV_FILE=/etc/eventmap.env

[[ $EUID -eq 0 ]] || { echo "À lancer avec sudo." >&2; exit 1; }
[[ -f "$APP_DIR/main.py" ]] || { echo "Le dépôt doit être cloné dans $APP_DIR." >&2; exit 1; }

echo "==> Utilisateur système sans shell"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> Python et venv"
apt-get install -y -qq python3-venv >/dev/null
cd "$APP_DIR"
PY312="$(ls /opt/pybuild/python/cpython-3.12*/bin/python3.12 2>/dev/null | head -1)"   # Python autonome posé le 07/10/2026 (docs/python-runtime.md)
[[ -d .venv ]] || "${PY312:-python3}" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Le dépôt appartient à $APP_USER mais git tourne en root : sans cette ligne,
# tout `git fetch` ultérieur échoue sur "dubious ownership".
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

echo "==> Dossier de données (seul chemin inscriptible pour le service)"
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 750 "$APP_DIR/data"

echo "==> Fichier d'environnement"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
EVENTMAP_DB=${APP_DIR}/data/eventmap.db
EVENTMAP_FEEDS=${APP_DIR}/feeds.yaml
EVENTMAP_REFRESH_SECONDS=21600
EVENTMAP_CONTACT=https://github.com/flemops/eventmap.hamdy-tabsissi.com
EVENTMAP_LOG=INFO
# OPENAGENDA_KEY=
# ANTHROPIC_API_KEY=
EOF
  chmod 640 "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
  echo "    -> $ENV_FILE créé"
fi

echo "==> Quota disque : la base et le WAL ne doivent pas dépasser 200 Mo"
# SQLite n'a pas de quota natif ; on borne le dossier via un tmpfiles cleanup
# sur les vieux fichiers de sauvegarde, et on compte sur purge_past() pour la
# table. Le vrai garde-fou est MemoryMax + le refresh borné à 90 jours.
cat > /etc/tmpfiles.d/eventmap.conf <<EOF
e ${APP_DIR}/data - - - 30d
EOF

echo "==> Unité systemd"
install -m 644 deploy/eventmap.service /etc/systemd/system/eventmap.service
systemctl daemon-reload
systemctl enable --now eventmap
systemctl restart eventmap

echo "==> nginx (délégué à nginx-sync.sh)"
# Ce script n'écrit plus lui-même dans /etc/nginx. Une seule voie d'écriture
# subsiste, celle qui sait revenir en arrière : sauvegarde, `nginx -t`,
# rechargement surveillé, vérification du comportement réel, et restauration
# complète dès qu'une étape échoue. Comme install.sh est rejouable après un
# `git pull`, c'était la dernière voie capable de laisser le reverse proxy —
# partagé par trois sites — dans un état intermédiaire non testé.
# `apply` recharge le reverse proxy, PARTAGÉ par portfolio, eventmap et
# observatory. Un installeur applicatif qui recharge un proxy partagé, ça se
# dit : c'est le prix de l'auto-réparation (un lien d'activation manquant est
# reposé tout seul), pas un détail.
SYNC="$APP_DIR/deploy/nginx/nginx-sync.sh"
ECHEC_NGINX=0

if ! systemctl is-active --quiet nginx; then
  # Rien n'a été tenté : ce n'est pas un échec de l'installeur. L'application
  # est installée et fonctionnelle ; on avertit sans faire échouer le script.
  echo "    nginx n'est pas démarré (ou pas installé) : le reverse proxy n'a pas"
  echo "    été touché. Une fois nginx en service : sudo bash $SYNC apply"
elif bash "$SYNC" apply; then
  echo "    -> configuration nginx conforme au dépôt"
else
  rc=$?
  echo
  if [[ "$rc" -eq 2 || "$rc" -eq 127 ]]; then
    # Codes d'appel : manifeste ou script absent / malformé. nginx-sync.sh
    # s'arrête avant d'ouvrir /etc/nginx — dire « restauré » ici serait faux.
    echo "    ERREUR d'appel de nginx-sync.sh (code $rc) : script ou manifeste"
    echo "    absent ou malformé. RIEN n'a été modifié dans /etc/nginx."
    echo "    La cause exacte est dans le message juste au-dessus."
  else
    echo "    ATTENTION : l'application de la configuration nginx a échoué."
    echo "    L'état précédent a été rétabli (voir les ALERTES ci-dessus)."
    echo "    Cause la plus fréquente : certificat TLS absent, ou un vhost d'un"
    echo "    AUTRE site déjà cassé sur le disque — nginx -t teste l'ensemble."
    echo "    Lire le fichier et la ligne que nginx -t nomme ci-dessus."
    echo "    Le reste de l'installation est en place. Une fois corrigé :"
    echo "      sudo bash $SYNC apply"
  fi
  echo
  ECHEC_NGINX=1
fi

echo
sleep 3
systemctl --no-pager --lines=3 status eventmap || true
echo
# Sonde applicative qui compte réellement. L'ancienne (`curl ... || echo "(pas
# encore prêt)"`) ne pouvait jamais faire échouer le script : le script sortait
# en 0 avec un service mort. Le code de retour était donc sensible à la dérive
# nginx et aveugle au composant le plus critique. Aligné sur observatory.
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8000/health || echo 000)
echo "Sonde locale /health : $code"
ECHEC_APP=0
[[ "$code" == "200" ]] || { echo "    le service ne répond pas." >&2; ECHEC_APP=1; }

# Des `if` et non `[[ ... ]] && echo` : sous set -e, un test faux en dernière
# position ferait sortir en 1 un script pourtant réussi. Le piège s'est déjà
# présenté deux fois dans ce chantier.
if [[ "$ECHEC_APP" -eq 1 || "$ECHEC_NGINX" -eq 1 ]]; then
  echo
  if [[ "$ECHEC_APP" -eq 1 ]]; then
    echo "ÉCHEC : le service eventmap ne répond pas — voir plus haut." >&2
  fi
  if [[ "$ECHEC_NGINX" -eq 1 ]]; then
    echo "ÉCHEC : la configuration nginx n'a pas pu être appliquée — voir plus haut." >&2
  fi
  exit 1
fi
echo "eventmap installé et actif."
