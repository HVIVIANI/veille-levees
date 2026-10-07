"""Veille des levées de fonds : interroge l'API Tech.eu Funding Explorer.

Lancement : python veille.py
Réglages  : config.json
Résultat  : data/levees.json
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).parent
CONFIG = RACINE / "config.json"
SORTIE = RACINE / "data" / "levees.json"
SITE = "https://funding.tech.eu"
API = SITE + "/api/v1/rounds"


def lire_json(url):
    """Envoie une demande à l'API et renvoie sa réponse.

    Si l'API refuse (code 400, 429...) ou est injoignable, le script s'arrête
    avec un message clair au lieu de continuer avec zéro résultat.
    """
    demande = urllib.request.Request(
        url, headers={"User-Agent": "veille-levees (github.com/HVIVIANI/veille-levees)"}
    )
    try:
        with urllib.request.urlopen(demande, timeout=30) as reponse:
            return json.load(reponse)
    except urllib.error.HTTPError as erreur:
        raise SystemExit(f"L'API a refusé la demande (code {erreur.code}) : {url}")
    except urllib.error.URLError as erreur:
        raise SystemExit(f"API injoignable ({erreur.reason}) : {url}")


def recuperer_levees(pays, depuis, montant_min, lire=lire_json):
    """Récupère toutes les levées d'un pays depuis une date, page après page."""
    levees, curseur = [], None
    while True:
        parametres = {"country": pays, "from": depuis, "minAmount": montant_min, "limit": 100}
        if curseur:
            parametres["after"] = curseur
        reponse = lire(API + "?" + urllib.parse.urlencode(parametres))
        levees += reponse["data"]
        curseur = reponse.get("nextCursor")
        if not curseur or not reponse["data"]:
            return levees


def simplifier(levee, corrections_villes, aujourd_hui):
    """Ne garde que les champs utiles d'une levée, avec des noms simples."""
    entreprise = levee["company"]
    ville = entreprise.get("city") or ""
    return {
        "id": levee["id"],
        "date": levee["date"],
        "entreprise": entreprise["name"],
        "pays": entreprise.get("country"),
        "ville": corrections_villes.get(ville, ville),
        "secteurs": entreprise.get("sectors") or [],
        "montant_eur": levee.get("amountEur"),
        "stade": levee.get("stage"),
        "investisseurs": levee.get("investors") or [],
        "source": (levee.get("source") or {}).get("domain"),
        "confiance": levee.get("confidence"),
        "lien": SITE + levee["url"],
        "vue_le": aujourd_hui,
    }


def fusionner(anciennes, nouvelles):
    """Ajoute les nouvelles levées aux anciennes, sans doublon.

    Chaque levée a un identifiant unique : si on l'a déjà, on met ses infos à
    jour mais on garde la date à laquelle on l'a vue pour la première fois.
    """
    par_id = {levee["id"]: levee for levee in anciennes}
    ajoutees = 0
    for levee in nouvelles:
        if levee["id"] in par_id:
            levee["vue_le"] = par_id[levee["id"]]["vue_le"]
        else:
            ajoutees += 1
        par_id[levee["id"]] = levee
    triees = sorted(par_id.values(), key=lambda l: (l["date"], l["entreprise"]), reverse=True)
    return triees, ajoutees


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    aujourd_hui = date.today().isoformat()
    depuis = (date.today() - timedelta(days=config["jours_en_arriere"])).isoformat()

    nouvelles = []
    for pays in config["pays"]:
        brutes = recuperer_levees(pays, depuis, config["montant_min_eur"])
        print(f"{pays} : {len(brutes)} levées depuis le {depuis}")
        nouvelles += [simplifier(l, config["corrections_villes"], aujourd_hui) for l in brutes]

    anciennes = json.loads(SORTIE.read_text(encoding="utf-8"))["levees"] if SORTIE.exists() else []
    levees, ajoutees = fusionner(anciennes, nouvelles)

    resultat = {
        "mis_a_jour": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": "Données : Tech.eu Funding Explorer (CC BY 4.0), https://funding.tech.eu",
        "levees": levees,
    }
    SORTIE.parent.mkdir(exist_ok=True)
    SORTIE.write_text(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{ajoutees} nouvelles levées, {len(levees)} au total dans {SORTIE.name}")


if __name__ == "__main__":
    main()
