"""Vérification du service RÉEL après un déploiement (aucun accès à la VM : tout est lu en HTTP).

    python deploy/verify_prod.py        # code de sortie 1 si une vérification échoue
    EXPECTED_COMMIT=<sha> python deploy/verify_prod.py   # + vérifie que CE commit est celui en service

Contrôle /health (latence, refresh, sources, alertes, villes), les fenêtres et le fuseau, les filtres,
les routes et deep links, les pages (canonique, JSON-LD, CSP, assets versionnés), un échantillon de
liens externes, et que les villes éteintes sont bien introuvables. Nécessite `zoneinfo` + tzdata."""
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

P = "https://eventmap.hamdy-tabsissi.com"
RES = []

def req(path, method="GET", timeout=25, full=False):
    t = time.time()
    r = urllib.request.Request(path if path.startswith("http") else P + path, method=method,
                               headers={"User-Agent": "eventmap-validate/1.0", "Accept-Encoding": "identity"})
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        resp = op.open(r, timeout=timeout)
        body = resp.read() if method != "HEAD" else b""
        return resp.status, round(time.time() - t, 2), body, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, round(time.time() - t, 2), e.read() if method != "HEAD" else b"", e.headers
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, round(time.time() - t, 2), b"", {}

def check(name, ok, detail=""):
    RES.append((name, ok, detail))
    print(("OK   " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))

# --- /health : disponibilité, latence, refresh, flags -------------------------------------------------
lat = []
for _ in range(12):
    st, dt, body, _h = req("/health")
    lat.append(dt)
    time.sleep(0.4)
check("/health répond 200 sur 12 sondes", all(x < 3 for x in lat), f"max {max(lat)} s, médiane {statistics.median(lat)} s")
h = json.loads(req("/health")[2])
c = h["cities"]["paris"]
now = datetime.now(timezone.utc)
last = datetime.fromisoformat(h["refresh"]["last_run"]) if h["refresh"]["last_run"] else None
check("status ok", h["status"] == "ok", h["status"])
expected = os.environ.get("EXPECTED_COMMIT", "").strip()      # fourni par la CI post-déploiement (SHA du tag prod)
rel = h.get("release") or {}
if expected:
    check("version déployée = commit attendu", bool(rel.get("commit")) and expected.startswith(rel["commit"]),
          f'en service {rel.get("commit")}, attendu {expected[:12]}')
else:
    print(f'INFO commit en service : {rel.get("commit")} · Python {rel.get("python")} (EXPECTED_COMMIT non fourni : pas de comparaison)')
check("refresh récent (< 7 h)", bool(last) and (now - last).total_seconds() < 7 * 3600, str(h["refresh"]["last_run"]))
check("ingestion Paris : sources fraîches", c["data"]["state"] == "ok", f'{c["data"]["state"]} {[(s["id"], s["data_age_h"]) for s in c["data"]["sources"]]}')
check("aucune anomalie Paris", c["anomalies"] == [], str(c["anomalies"]))
check("Paris : tous géolocalisés", c["events"].get("without_geo") == 0, str(c["events"]))
check("Jeddah éteinte", h["cities"]["jeddah"]["enabled"] is False)
nr = {x["id"]: x["reason"] for x in h["cities"]["jeddah"]["not_running"]}
check("sources Jeddah non lancées pour la bonne raison", len(nr) == 3 and all("autorisation" in v for v in nr.values()), str({k: v[:30] for k, v in nr.items()}))
check("dédup présente", set(h["dedup"]) >= {"forte", "faible", "traduction"}, str(h["dedup"]))

# --- API Paris : fenêtres, fuseau, filtres -------------------------------------------------------------
paris_tz = ZoneInfo("Europe/Paris")
counts = {}
for w in ("now", "today", "tomorrow", "weekend", "week"):
    st, dt, body, _ = req(f"/api/events?city=paris&radius=8&when={w}&limit=300")
    j = json.loads(body) if st == 200 else {}
    counts[w] = j.get("count")
    if w == "today":
        end = datetime.fromisoformat(j["window"]["to"]).astimezone(paris_tz)
        check("fuseau : « ce soir » finit à minuit heure de Paris", (end.hour, end.minute) == (0, 0), str(end))
    if w == "tomorrow":
        s0 = datetime.fromisoformat(j["window"]["from"]).astimezone(paris_tz)
        check("fuseau : « demain » commence à minuit heure de Paris", (s0.hour, s0.minute) == (0, 0), str(s0))
check("fenêtres Paris renvoient des événements", all((v or 0) > 0 for k, v in counts.items() if k != "now") , str(counts))
st, dt, body, _ = req("/api/events?city=paris&radius=8&when=week&price=free&limit=300")
free = json.loads(body)["events"]
check("filtre gratuit : tous gratuits", free and all(e["price_type"] == "free" for e in free), f"{len(free)} événements")
st, dt, body, _ = req("/api/events?city=paris&radius=8&when=week&category=music&limit=300")
mus = json.loads(body)["events"]
check("filtre catégorie : musique", mus and all(e["category"] == "music" for e in mus), f"{len(mus)} événements")
st, dt, body, _ = req("/api/events?city=paris&radius=8&when=week&culture=japon,suede&limit=300")
cu = json.loads(body)
check("filtre culture multi-clés", st == 200 and all(e.get("culture_cle") in ("japon", "suede") for e in cu["events"]), f'{cu["count"]} événements')
st, dt, body, _ = req("/api/events?city=paris&radius=8&when=week&culture=coree&limit=300")
co = json.loads(body)
check("culture Corée (PR #5) en production", st == 200 and co["count"] > 0, f'{co["count"]} événements cette semaine')
for bad, code in (("radius=99", 422), ("when=hier", 422), ("city=atlantide", 404), ("city=jeddah", 404), ("lat=91", 422)):
    st, *_ = req(f"/api/events?{bad}")
    check(f"entrée invalide /api/events?{bad} → {code}", st == code, str(st))
st, dt, body, _ = req("/api/events?radius=8&when=week&limit=1")
check("compatibilité : appel sans ville = Paris", st == 200 and json.loads(body)["city"] == "paris")

# --- isolation des villes -------------------------------------------------------------------------------
cities = json.loads(req("/api/cities")[2])["cities"]
check("/api/cities = Paris seule", [x["id"] for x in cities] == ["paris"])

# --- routes / deep links ----------------------------------------------------------------------------------
ev = json.loads(req("/api/events?city=paris&radius=8&when=week&limit=5")[2])["events"]
eid = ev[0]["id"]
for path, code in (("/", 200), ("/paris", 200), ("/paris/carte", 200), (f"/paris/e/{eid}", 200), ("/sitemap.xml", 200),
                   ("/jeddah", 404), ("/jeddah/carte", 404), ("/ar/jeddah", 404), ("/ar/paris", 404), ("/paris/e/999999999", 404),
                   ("/paris/e/abc", 404), ("/paris/inconnu", 404), ("/static/city.css", 200)):
    st, dt, body, hd = req(path)
    check(f"GET {path} → {code}", st == code, f"{st} {dt}s")
st, dt, body, hd = req("/carte")
check("/carte → 301 /paris/carte", st == 301 and hd.get("Location") == "/paris/carte", f'{st} {hd.get("Location")}')
for path in ("/", "/paris", "/paris/carte", f"/paris/e/{eid}"):
    st, *_ = req(path, "HEAD")
    check(f"HEAD {path}", st == 200, str(st))

# --- contenu des pages (SEO, partage, sécurité) --------------------------------------------------------------
st, dt, body, hd = req("/paris/carte")
html = body.decode("utf-8")
check("canonical /paris/carte", f'rel="canonical" href="{P}/paris/carte"' in html)
check("balises de partage", all(x in html for x in ('property="og:title"', 'name="twitter:card"', 'name="description"')))
check("pas de noindex", "noindex" not in html)
ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
check("JSON-LD valide", bool(ld) and bool(json.loads(ld[0])["@graph"]), f"{len(ld)} bloc(s)")
check("CSP présente", "Content-Security-Policy" in hd, (hd.get("Content-Security-Policy") or "")[:60])
vers = set(re.findall(r"\?v=([0-9a-f]{10})", html))
check("ressources versionnées (?v=)", len(vers) == 1, str(vers))
for a in re.findall(r'(?:src|href)="(/static/[^"]+)"', html):
    st, *_ = req(a, "HEAD")
    if st != 200:
        check(f"asset {a}", False, str(st))
check("tous les assets de la page répondent 200", not [r for r in RES if r[0].startswith("asset") and not r[1]])
st, dt, body, hd = req(f"/paris/e/{eid}")
page = body.decode("utf-8")
ldv = json.loads(re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)[0])
evt = [x for x in ldv["@graph"] if x["@type"] == "Event"]
check("fiche : JSON-LD Event avec fuseau Paris", bool(evt) and re.search(r"\+0[12]:00$", evt[0]["startDate"]) is not None, evt[0]["startDate"] if evt else "aucun")

# --- liens externes ----------------------------------------------------------------------------------------------
urls = []
for e in ev + free[:20]:
    if e.get("url") and e["url"] not in urls:
        urls.append(e["url"])
urls = urls[:12]
dead = []
for u in urls:
    st, *_ = req(u, "GET", timeout=20)
    if st not in (200, 301, 302, 403):
        dead.append((u, st))
    time.sleep(0.5)
check("liens externes d'événements (échantillon)", not dead, f"{len(urls)} testés, KO: {dead}")

# --- portfolio ------------------------------------------------------------------------------------------------------
st, dt, body, _ = req("https://hamdy-tabsissi.com/api/contenus.json")
check("portfolio /api/contenus.json (Mindmap) répond", st == 200, f"{st}")

bad = [r for r in RES if not r[1]]
print(f"\n{len(RES) - len(bad)}/{len(RES)} vérifications OK")
sys.exit(1 if bad else 0)
