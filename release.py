"""Version déployée : le commit Git du code qui tourne, lu sans lancer `git`.

La VM déploie par `git` (le tag `prod` avance après une CI verte) : `.git/HEAD` contient donc le commit
réellement en service, soit directement (HEAD détaché sur le tag), soit via une référence de branche.
Pas de sous-processus : `git` refuserait de lire un dépôt appartenant à un autre utilisateur
(« dubious ownership ») et le service tourne avec `ProtectSystem=strict`. Exposé par `/health` pour que
la vérification post-déploiement compare « ce qui devait partir » et « ce qui tourne »."""
from __future__ import annotations

import re
from pathlib import Path

_SHA = re.compile(r"^[0-9a-f]{40}$")


def read_commit(base: Path) -> str | None:
    """SHA complet du commit courant, ou None (pas de dépôt Git, archive, référence illisible)."""
    git = base / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if _SHA.match(head):
            return head
        if head.startswith("ref: "):
            ref = head[5:].strip()
            ref_file = git / ref
            if ref_file.is_file():
                sha = ref_file.read_text(encoding="utf-8").strip()
                return sha if _SHA.match(sha) else None
            packed = git / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref) and _SHA.match(line.split(" ", 1)[0]):
                        return line.split(" ", 1)[0]
    except OSError:
        return None
    return None
