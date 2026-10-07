"""Lecture des flux de presse : repère les articles qui annoncent une levée.

Un flux RSS est une liste d'articles (titre, lien, date, catégories) qu'un
site publie pour être lue par des programmes. Ce fichier sait :
  1. lire un flux et en sortir les articles ;
  2. reconnaître ceux qui parlent d'une levée de fonds ;
  3. y trouver un montant, et dire si l'article concerne ta zone.
"""

import html
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

NAVIGATEUR = "Mozilla/5.0 (compatible; veille-levees/1.0; +https://github.com/HVIVIANI/veille-levees)"

# Mots qui signalent une levée dans un titre, en français et en anglais.
SIGNAL_LEVEE = re.compile(
    r"\bl[eè]ve(?:nt)?\b|\blev[ée]e\b|tour de table|\bboucle\b|\braises?\b|\bsecures?\b|\bcloses?\b|\bfunding\b|\blands?\b",
    re.IGNORECASE,
)
# Catégories que certains sites posent eux-mêmes sur leurs articles de levées.
CATEGORIES_LEVEE = {"les levees de fonds", "funding", "levee de fonds", "levees de fonds"}
# Récapitulatifs hebdomadaires : ils citent des dizaines de boîtes, on les écarte.
RECAPITULATIF = re.compile(
    r"lev[ée]es de fonds de la|lev[ée]s dans la french tech|cette semaine|de la semaine|weekly|this week|funding deals|recap",
    re.IGNORECASE,
)

NOMBRE = r"(\d+(?:[.,]\d+)?)"
MONTANT_FR = re.compile(
    NOMBRE + r"\s*(millions?|milliards?|Mds?|M)\s*(?:d['’]\s*|de\s+)?(euros?|€|dollars?|\$|francs?|CHF)", re.IGNORECASE
)
MONTANT_EN = re.compile(r"(€|\$|£|CHF|USD|EUR)\s*" + NOMBRE + r"\s*(million|billion|bn|m|k)?\b", re.IGNORECASE)
MULTIPLES = {"million": 1e6, "millions": 1e6, "m": 1e6, "milliard": 1e9, "milliards": 1e9,
             "md": 1e9, "mds": 1e9, "billion": 1e9, "bn": 1e9, "k": 1e3}
# Taux approximatifs, uniquement pour comparer au seuil. Le montant affiché reste celui de l'article.
TAUX_VERS_EURO = {"€": 1, "eur": 1, "euro": 1, "euros": 1, "$": 0.89, "usd": 0.89, "dollar": 0.89,
                  "dollars": 0.89, "chf": 1.05, "franc": 1.05, "francs": 1.05, "£": 1.15}


def normaliser(texte):
    """Met en minuscules et retire les accents, pour comparer des textes."""
    sans_accents = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in sans_accents if not unicodedata.combining(c)).casefold()


def contient_un_mot(texte, mots):
    """Vrai si le texte contient un mot qui commence par l'un des mots donnés."""
    propre = normaliser(texte)
    return any(re.search(r"\b" + re.escape(normaliser(mot)), propre) for mot in mots)


def trouver_montant(texte):
    """Cherche un montant dans un texte. Renvoie (texte trouvé, estimation en euros) ou (None, None)."""
    trouve = MONTANT_FR.search(texte or "")
    if trouve:
        nombre, multiple, devise = trouve.groups()
    else:
        trouve = MONTANT_EN.search(texte or "")
        if not trouve:
            return None, None
        devise, nombre, multiple = trouve.groups()
        if not multiple:  # "USD 42" : on ne sait pas si ce sont des millions
            return None, None
    valeur = float(nombre.replace(",", ".")) * MULTIPLES[multiple.lower()]
    return trouve.group(0).strip(), round(valeur * TAUX_VERS_EURO.get(devise.lower(), 1))


def telecharger(url):
    demande = urllib.request.Request(url, headers={"User-Agent": NAVIGATEUR})
    with urllib.request.urlopen(demande, timeout=30) as reponse:
        return reponse.read()


def _texte(noeud, nom):
    for enfant in noeud:
        if enfant.tag.split("}")[-1] == nom and enfant.text:
            return enfant.text.strip()
    return ""


def lire_flux(contenu, nom_source):
    """Transforme le contenu d'un flux RSS en liste d'articles."""
    racine = ET.fromstring(contenu)
    articles = []
    for item in racine.iter():
        if item.tag.split("}")[-1] != "item":
            continue
        try:
            date = parsedate_to_datetime(_texte(item, "pubDate")).astimezone(timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            date = datetime.now(timezone.utc).date().isoformat()
        resume = re.sub(r"<[^>]+>", " ", html.unescape(_texte(item, "description")))
        articles.append({
            "source": nom_source,
            "titre": html.unescape(_texte(item, "title")),
            "lien": _texte(item, "link"),
            "date": date,
            "categories": [e.text.strip() for e in item if e.tag.split("}")[-1] == "category" and e.text],
            "resume": re.sub(r"\s+", " ", resume).strip()[:400],
        })
    return [a for a in articles if a["titre"] and a["lien"]]


def analyser(article, portee, mots_zone, mots_pays):
    """Dit si un article annonce une levée. Renvoie l'article enrichi, ou None."""
    titre = article["titre"]
    if RECAPITULATIF.search(titre):
        return None
    texte_montant, estimation = trouver_montant(titre)
    categorie_levee = any(normaliser(c) in CATEGORIES_LEVEE for c in article["categories"])
    if not categorie_levee and not (SIGNAL_LEVEE.search(titre) and texte_montant):
        return None
    if not texte_montant:
        texte_montant, estimation = trouver_montant(article["resume"])
    tout = " ".join([titre, article["resume"], " ".join(article["categories"])])
    return {
        "source": article["source"],
        "titre": titre,
        "lien": article["lien"],
        "date": article["date"],
        "montant_texte": texte_montant,
        "montant_eur_estime": estimation,
        "zone": contient_un_mot(tout, mots_zone),
        # Un site français parle surtout de la France ; un site européen doit citer le pays.
        "pays_ok": portee == "france" or contient_un_mot(tout, mots_pays),
    }


def _cle(texte):
    """Comme normaliser, en traitant "IA" et "AI" comme le même mot (Edison IA = Edison AI)."""
    return re.sub(r"\bia\b", "ai", normaliser(texte))


def parle_de(article, nom_entreprise):
    """Vrai si le titre de l'article cite l'entreprise."""
    nom = _cle(nom_entreprise)
    return len(nom) >= 4 and re.search(r"\b" + re.escape(nom) + r"\b", _cle(article["titre"])) is not None


def ecart_en_jours(date_a, date_b):
    return abs((datetime.fromisoformat(date_a) - datetime.fromisoformat(date_b)).days)
