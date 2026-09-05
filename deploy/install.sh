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
[[ -d .venv ]] || python3 -m venv .venv
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
EVENTMAP_CONTACT=https://github.com/flemops/eventmap
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

echo "==> nginx (fichiers installés ; le site n'est PAS encore activé)"
# Les snippets AVANT le fichier de site qui les inclut : dans l'autre sens,
# `nginx -t` échoue sur un include manquant.
install -d -m 755 /etc/nginx/snippets
install -m 644 deploy/nginx/snippets/eventmap-entetes.conf /etc/nginx/snippets/eventmap-entetes.conf
install -m 644 deploy/nginx/snippets/eventmap-csp.conf     /etc/nginx/snippets/eventmap-csp.conf
install -m 644 deploy/nginx/eventmap.conf                  /etc/nginx/sites-available/eventmap
echo "    -> éditer server_name dans deploy/nginx/eventmap.conf (le dépôt, PAS /etc/nginx),"
echo "       puis : ln -s /etc/nginx/sites-available/eventmap /etc/nginx/sites-enabled/ && nginx -t && systemctl reload nginx"
echo "    -> ensuite, pour toute mise à jour de la config nginx :"
echo "         bash deploy/nginx/nginx-sync.sh check         (signale une dérive, ne modifie rien)"
echo "         sudo bash deploy/nginx/nginx-sync.sh apply    (installe, teste, recharge, restaure si échec)"
echo "       ATTENTION : 'apply' ACTIVE le site (lien dans sites-enabled) et recharge nginx."
echo "       Ne le lancer qu'une fois le sous-domaine résolu."

echo
sleep 3
systemctl --no-pager --lines=3 status eventmap || true
echo
echo "Santé locale :"
curl -s http://127.0.0.1:8000/health | head -c 300 || echo "(pas encore prêt)"
echo
