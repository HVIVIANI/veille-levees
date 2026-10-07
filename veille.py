"""Veille des levées de fonds.

Lancement : python veille.py
Réglages  : config.json
Résultat  : data/levees.json

Deux familles de sources :
  - Tech.eu Funding Explorer, une base de données interrogée par API (source principale) ;
  - des flux de presse (voir presse.py), qui recoupent Tech.eu et rattrapent ce qu'elle rate.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import presse

RACINE = Path(__file__).parent
CONFIG = RACINE / "config.json"
SORTIE = RACINE / "data" / "levees.json"
SITE = "https://funding.tech.eu"
API = SITE + "/api/v1/rounds"
JOURS_DE_MEMOIRE_PRESSE = 45
ECART_MAX_ARTICLE_LEVEE = 14


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
    ville = (entreprise.get("city") or "").strip()
    if ville.islower():  # "paris" écrit sans majuscule dans la base
        ville = ville.title()
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


def collecter_presse(config, telecharger=presse.telecharger):
    """Lit chaque flux de presse et garde les articles qui annoncent une levée.

    Un flux en panne n'arrête pas tout : on note l'erreur et on passe au suivant.
    """
    articles, etats = [], []
    zone = config.get("zone_prioritaire", {}).get("mots", [])
    for flux in config.get("flux_presse", []):
        try:
            contenu = telecharger(flux["url"])
            lus = presse.lire_flux(contenu, flux["nom"])
            if not lus:  # le site a répondu, mais pas avec un flux d'articles
                raise ValueError("aucun article trouvé ; début de la réponse : " + repr(contenu[:80]))
        except Exception as erreur:  # réseau, site en panne, réponse inattendue...
            etats.append({"nom": flux["nom"], "etat": "erreur", "detail": f"{type(erreur).__name__}: {erreur}"[:220]})
            print(f"{flux['nom']} : illisible ({erreur})")
            continue
        gardes = [a for a in (presse.analyser(l, flux["portee"], zone, config.get("mots_pays", []), config.get("mots_etranger", [])) for l in lus) if a]
        articles += gardes
        etats.append({"nom": flux["nom"], "etat": "ok", "articles_lus": len(lus), "articles_levees": len(gardes)})
        print(f"{flux['nom']} : {len(lus)} articles lus, {len(gardes)} annoncent une levée")
    return articles, etats


def fusionner_articles(anciens, nouveaux, aujourd_hui):
    """Garde en mémoire les articles des dernières semaines, un seul par lien."""
    limite = (date.fromisoformat(aujourd_hui) - timedelta(days=JOURS_DE_MEMOIRE_PRESSE)).isoformat()
    par_lien = {a["lien"]: a for a in anciens}
    par_lien.update({a["lien"]: a for a in nouveaux})
    recents = [a for a in par_lien.values() if a["date"] >= limite]
    return sorted(recents, key=lambda a: (a["date"], a["titre"]), reverse=True)


def croiser(levees, articles, config):
    """Rapproche les articles de presse des levées connues.

    - Un article qui cite une entreprise de la liste est attaché à sa levée.
    - Les autres forment la liste "vue dans la presse seulement".
    - Une levée est dans ta zone si sa ville y est, ou si un de ses articles en parle.
    """
    mots_zone = config.get("zone_prioritaire", {}).get("mots", [])
    corrections = config.get("corrections_entreprises", {})
    seuil = config["montant_min_eur"]
    attaches = set()
    for levee in levees:
        levee.update(corrections.get(levee["entreprise"], {}))
        siens = [a for a in articles
                 if presse.parle_de(a, levee["entreprise"])
                 and presse.ecart_en_jours(a["date"], levee["date"]) <= ECART_MAX_ARTICLE_LEVEE]
        attaches.update(a["lien"] for a in siens)
        levee["presse"] = [{"source": a["source"], "titre": a["titre"], "lien": a["lien"]} for a in siens]
        levee["zone"] = presse.contient_un_mot(levee["ville"], mots_zone) or any(a["zone"] for a in siens)
    return [
        {cle: a[cle] for cle in ("date", "titre", "source", "lien", "montant_texte", "montant_eur_estime", "zone")}
        for a in articles
        if a["lien"] not in attaches and a["pays_ok"]
        and (a["montant_eur_estime"] is None or a["montant_eur_estime"] >= seuil)
    ]


def main():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    aujourd_hui = date.today().isoformat()
    depuis = (date.today() - timedelta(days=config["jours_en_arriere"])).isoformat()
    ancien = json.loads(SORTIE.read_text(encoding="utf-8")) if SORTIE.exists() else {}

    nouvelles = []
    for pays in config["pays"]:
        brutes = recuperer_levees(pays, depuis, config["montant_min_eur"])
        print(f"Tech.eu, {pays} : {len(brutes)} levées depuis le {depuis}")
        nouvelles += [simplifier(l, config["corrections_villes"], aujourd_hui) for l in brutes]
    levees, ajoutees = fusionner(ancien.get("levees", []), nouvelles)

    articles_du_jour, etats = collecter_presse(config)
    articles = fusionner_articles(ancien.get("articles", []), articles_du_jour, aujourd_hui)
    presse_seule = croiser(levees, articles, config)

    resultat = {
        "mis_a_jour": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": "Données : Tech.eu Funding Explorer (CC BY 4.0), https://funding.tech.eu",
        "sources": [{"nom": "Tech.eu", "etat": "ok", "levees": len(nouvelles)}] + etats,
        "levees": levees,
        "presse_seule": presse_seule,
        "articles": articles,
    }
    SORTIE.parent.mkdir(exist_ok=True)
    SORTIE.write_text(json.dumps(resultat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{ajoutees} nouvelles levées, {len(levees)} au total ; "
          f"{len(presse_seule)} articles sans équivalent chez Tech.eu ; "
          f"{sum(1 for l in levees if l['zone'])} levées dans ta zone")


if __name__ == "__main__":
    main()
