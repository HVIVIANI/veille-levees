"""Vérifie la lecture des flux de presse et le croisement avec Tech.eu.

Lancement : python -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path

ICI = Path(__file__).parent
sys.path.insert(0, str(ICI.parent))
import presse
import veille

CONFIG = json.loads((ICI.parent / "config.json").read_text(encoding="utf-8"))
ZONE = CONFIG["zone_prioritaire"]["mots"]
PAYS = CONFIG["mots_pays"]
ECHANTILLON = json.loads((ICI / "echantillon_techeu.json").read_text(encoding="utf-8"))


def articles(fichier, source, portee):
    lus = presse.lire_flux((ICI / fichier).read_bytes(), source)
    return lus, [a for a in (presse.analyser(l, portee, ZONE, PAYS) for l in lus) if a]


class TestMontants(unittest.TestCase):
    def test_montants_en_francais(self):
        self.assertEqual(presse.trouver_montant("LUPIN DENTAL lève 15 millions d’euros")[1], 15_000_000)
        self.assertEqual(presse.trouver_montant("lève 7,5 millions d'euros")[1], 7_500_000)
        self.assertEqual(presse.trouver_montant("un tour de 2 M€")[1], 2_000_000)
        self.assertEqual(presse.trouver_montant("lève 19 millions de dollars")[1], 16_910_000)

    def test_montants_en_anglais(self):
        self.assertEqual(presse.trouver_montant("raises €15 million for")[1], 15_000_000)
        self.assertEqual(presse.trouver_montant("Raises $26M in Series B")[1], 23_140_000)
        self.assertEqual(presse.trouver_montant("raises $90m in Series B")[1], 80_100_000)

    def test_montant_absent_ou_ambigu(self):
        self.assertEqual(presse.trouver_montant("Mistral AI veut revenir dans la course"), (None, None))
        self.assertEqual(presse.trouver_montant("closes USD 42 Series B round"), (None, None))


class TestFluxFrancais(unittest.TestCase):
    def setUp(self):
        self.lus, self.gardes = articles("flux_francais.xml", "FrenchWeb", "france")
        self.par_titre = {a["titre"].split(" ")[0]: a for a in self.gardes}

    def test_lit_tous_les_articles(self):
        self.assertEqual(len(self.lus), 9)

    def test_ne_garde_que_les_levees(self):
        titres = " | ".join(a["titre"] for a in self.gardes)
        self.assertIn("LUPIN DENTAL", titres)
        self.assertIn("EDISON IA", titres)
        self.assertIn("BIOLEVATE", titres)      # pas de "lève" dans le titre : gardé grâce à sa catégorie
        self.assertNotIn("nominations", titres)  # pas une levée
        self.assertNotIn("ELEVENLABS", titres)   # une valorisation, pas une levée
        self.assertNotIn("cette semaine", titres)  # récapitulatif écarté

    def test_repere_la_zone(self):
        self.assertTrue(self.par_titre["Hackuity"]["zone"])   # "lyonnaise" dans le résumé
        self.assertTrue(self.par_titre["Sopht"]["zone"])      # "Lyon" dans le résumé
        self.assertFalse(self.par_titre["Robotiser"]["zone"])  # Montpellier

    def test_dates_et_montants(self):
        self.assertEqual(self.par_titre["Sopht"]["date"], "2026-09-15")
        self.assertEqual(self.par_titre["Sopht"]["montant_eur_estime"], 7_500_000)


class TestFluxEuropeen(unittest.TestCase):
    def test_garde_la_france_et_la_suisse_seulement(self):
        _, gardes = articles("flux_europeen.xml", "EU-Startups", "europe")
        pays_ok = {a["titre"].split(" ")[0]: a["pays_ok"] for a in gardes}
        self.assertTrue(pays_ok["Edonia"])
        self.assertTrue(pays_ok["WhiteLab"])
        self.assertFalse(pays_ok["Polygrade"])  # Allemagne


class TestCroisement(unittest.TestCase):
    def setUp(self):
        self.levees = [veille.simplifier(l, {}, "2026-10-07") for l in ECHANTILLON["data"]]
        hackuity = dict(self.levees[0], id="H", entreprise="Hackuity", ville="Paris", date="2026-09-16")
        self.levees.append(hackuity)
        _, fr = articles("flux_francais.xml", "FrenchWeb", "france")
        _, eu = articles("flux_europeen.xml", "EU-Startups", "europe")
        self.config = dict(CONFIG, corrections_entreprises={})
        self.presse_seule = veille.croiser(self.levees, fr + eu, self.config)
        self.par_nom = {l["entreprise"]: l for l in self.levees}

    def test_attache_les_articles_aux_levees_connues(self):
        self.assertEqual([p["source"] for p in self.par_nom["Edonia"]["presse"]], ["EU-Startups"])
        self.assertEqual(len(self.par_nom["Whitelab Genomics"]["presse"]), 1)  # malgré la casse différente
        self.assertEqual(self.par_nom["Vocca"]["presse"], [])

    def test_un_article_peut_corriger_la_zone(self):
        # Tech.eu range Hackuity à Paris, l'article dit "lyonnaise" : la levée passe dans ta zone.
        self.assertTrue(self.par_nom["Hackuity"]["zone"])
        self.assertFalse(self.par_nom["Vocca"]["zone"])

    def test_presse_seule(self):
        titres = " | ".join(a["titre"] for a in self.presse_seule)
        self.assertIn("Sopht", titres)           # absente de Tech.eu : rattrapée par la presse
        self.assertIn("LUPIN DENTAL", titres)
        self.assertNotIn("Hackuity", titres)     # déjà attachée à sa levée
        self.assertNotIn("Edonia", titres)
        self.assertNotIn("Polygrade", titres)    # hors France et Suisse
        self.assertNotIn("PetiteBoite", titres)  # sous le seuil de 1 M€
        self.assertTrue(next(a for a in self.presse_seule if "Sopht" in a["titre"])["zone"])

    def test_ia_et_ai_sont_le_meme_nom(self):
        article = {"titre": "EDISON IA lève 1 million d’euros pour les PME"}
        self.assertTrue(presse.parle_de(article, "EDISON AI"))
        self.assertFalse(presse.parle_de(article, "Edonia"))

    def test_correction_manuelle_de_ville(self):
        config = dict(CONFIG, corrections_entreprises={"Vocca": {"ville": "Lyon"}})
        veille.croiser(self.levees, [], config)
        self.assertEqual(self.par_nom["Vocca"]["ville"], "Lyon")
        self.assertTrue(self.par_nom["Vocca"]["zone"])


class TestPannes(unittest.TestCase):
    def test_un_flux_en_panne_n_arrete_pas_les_autres(self):
        def telecharger(url):
            if "frenchweb" in url:
                raise OSError("site injoignable")
            return (ICI / "flux_europeen.xml").read_bytes()

        config = dict(CONFIG, flux_presse=[
            {"nom": "FrenchWeb", "url": "https://www.frenchweb.fr/feed", "portee": "france"},
            {"nom": "EU-Startups", "url": "https://www.eu-startups.com/feed/", "portee": "europe"},
        ])
        gardes, etats = veille.collecter_presse(config, telecharger=telecharger)
        self.assertEqual([e["etat"] for e in etats], ["erreur", "ok"])
        self.assertTrue(len(gardes) >= 2)

    def test_la_memoire_oublie_les_vieux_articles(self):
        vieux = {"lien": "a", "date": "2026-07-01", "titre": "vieux"}
        recent = {"lien": "b", "date": "2026-10-01", "titre": "récent"}
        gardes = veille.fusionner_articles([vieux, recent], [dict(recent)], "2026-10-07")
        self.assertEqual([a["lien"] for a in gardes], ["b"])


if __name__ == "__main__":
    unittest.main()
